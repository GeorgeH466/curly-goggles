/* ==========================================================================
   github.js — local repository discovery + GitHub Desktop integration
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, el, fmt, icon } = App;

  const GitHub = (App.GitHub = {});
  let repos = [];
  let scanning = false;
  let notifications = null;
  let pollTimer = null;

  const MAX_VISIBLE = 6;

  /* ------------------------------------------------------------------ */
  /* Rendering                                                          */
  /* ------------------------------------------------------------------ */
  function remoteLabel(url) {
    if (!url) return "no remote";
    return url
      .replace(/^git@([^:]+):/, "$1/")
      .replace(/^https?:\/\/(www\.)?/, "")
      .replace(/\.git$/, "");
  }

  function repoRow(repo) {
    const ahead = repo.ahead ? `↑${repo.ahead}` : "";
    const behind = repo.behind ? `↓${repo.behind}` : "";
    const dirtyText = repo.dirty
      ? `${repo.modified + repo.staged + repo.untracked} change${repo.modified + repo.staged + repo.untracked === 1 ? "" : "s"}`
      : "clean";
    const syncLabel = [ahead, behind].filter(Boolean).join(" ");

    return el("div", { class: "repo-row", title: repo.path }, [
      el("div", { class: "repo-main" }, [
        el("div", { class: "repo-name" }, [
          el("span", { html: icon("git"), style: "display:inline-flex;color:var(--accent)" }),
          el("strong", { text: repo.name }),
          repo.error ? el("span", { class: "badge badge-danger", text: "error" }) : null,
        ]),
        el("div", { class: "repo-meta" }, [
          el("span", { class: "repo-branch" }, [
            el("span", { html: icon("git") }),
            el("span", { text: repo.branch || "no branch" }),
          ]),
          el("span", { class: repo.dirty ? "repo-dirty" : "repo-clean", text: `● ${dirtyText}` }),
          syncLabel ? el("span", { text: syncLabel }) : null,
          el("span", { class: "repo-msg", text: repo.commit_message || "no commits" }),
          el("span", { class: "muted", text: remoteLabel(repo.remote) }),
        ]),
      ]),
      el("div", { class: "repo-actions" }, [
        iconButton("refresh", "Pull latest", () => pull(repo)),
        iconButton("code", "Open in VS Code", () => openCode(repo)),
        iconButton("folder", "Open folder", () => openFolder(repo)),
        repo.remote ? iconButton("external", "Open on the web", () => openRemote(repo)) : null,
      ]),
    ]);
  }

  function iconButton(name, title, handler) {
    const button = el("button", { class: "icon-btn tiny", title, onclick: handler }, [
      el("span", { html: icon(name) }),
    ]);
    return button;
  }

  function render() {
    const body = $("#repos-body");
    if (!body) return;
    const count = $("#repo-count");
    if (count) count.textContent = repos.length ? `${repos.length} found` : "none";

    if (scanning && !repos.length) {
      body.replaceChildren(
        el("div", { class: "stack", style: "padding:8px 2px" }, [
          el("div", { class: "skeleton-line" }),
          el("div", { class: "skeleton-line" }),
          el("div", { class: "skeleton-line short" }),
        ]));
      return;
    }

    if (!repos.length) {
      body.replaceChildren(el("div", { class: "empty-state" }, [
        el("span", { html: icon("git") }),
        el("p", { text: "No repositories found" }),
        el("small", { class: "muted", text: "Add a scan folder in Settings → Repositories" }),
        el("button", { class: "btn ghost sm mt-8", text: "Add repository…", onclick: addRepo },
          []),
      ]));
      return;
    }

    const visible = repos.slice(0, MAX_VISIBLE);
    const list = el("div", { class: "repo-list" }, visible.map(repoRow));
    const fragments = [list];

    if (repos.length > MAX_VISIBLE) {
      fragments.push(el("div", { class: "row between mt-8" }, [
        el("span", { class: "muted small", text: `Showing ${MAX_VISIBLE} of ${repos.length}` }),
        el("button", { class: "text-btn", text: "Show all", onclick: showAllRepos }),
      ]));
    }

    if (notifications && notifications.enabled) {
      fragments.push(notificationsStrip());
    }

    body.replaceChildren(...fragments);
  }

  function notificationsStrip() {
    const items = notifications.items || [];
    const unread = items.filter((item) => item.unread).length;
    return el("div", { class: "row between mt-12", style: "padding-top:10px;border-top:1px solid var(--border)" }, [
      el("span", { class: "small muted" }, [
        el("span", { html: icon("message"), style: "display:inline-flex;vertical-align:-3px;margin-right:4px" }),
        el("span", {
          text: notifications.error
            ? notifications.error
            : `${unread} unread notification${unread === 1 ? "" : "s"}${notifications.login ? ` · @${notifications.login}` : ""}`,
        }),
      ]),
      el("button", { class: "text-btn", text: "View", onclick: showNotifications }),
    ]);
  }

  function showNotifications() {
    if (!notifications) return;
    const items = notifications.items || [];
    const body = el("div", { class: "stack" });
    if (notifications.error) {
      body.append(el("p", { class: "muted small", text: notifications.error }));
    }
    if (!items.length) {
      body.append(el("div", { class: "empty-state" }, [el("p", { text: "You're all caught up." })]));
    }
    items.forEach((item) => {
      body.append(el("div", { class: "repo-row" }, [
        el("div", { class: "repo-main" }, [
          el("div", { class: "repo-name" }, [
            el("strong", { text: item.title }),
            item.unread ? el("span", { class: "badge", text: "new" }) : null,
          ]),
          el("div", { class: "repo-meta" }, [
            el("span", { text: item.repo || "" }),
            el("span", { text: item.reason || "" }),
            el("span", { text: item.type || "" }),
            el("span", { text: fmt.ago(item.updated_at) }),
          ]),
        ]),
        item.html_url
          ? el("a", { class: "icon-btn tiny", href: item.html_url, target: "_blank", rel: "noreferrer", title: "Open" }, [
            el("span", { html: icon("external") }),
          ])
          : null,
      ]));
    });
    App.modal({
      title: "GitHub notifications",
      subtitle: notifications.login ? `Signed in as @${notifications.login}` : "Personal access token required",
      body,
      wide: true,
    });
  }

  function showAllRepos() {
    const body = el("div", { class: "repo-list" }, repos.map(repoRow));
    App.modal({
      title: "All repositories",
      subtitle: `${repos.length} repositories in your scan folders`,
      body,
      wide: true,
      footer: [
        el("button", { class: "btn ghost", text: "Add repository…", onclick: addRepo }),
        el("button", { class: "btn ghost", text: "Rescan", onclick: () => GitHub.scan(true) }),
      ],
    });
  }

  /* ------------------------------------------------------------------ */
  /* Actions                                                            */
  /* ------------------------------------------------------------------ */
  async function pull(repo) {
    const remove = App.toast("info", `Pulling ${repo.name}…`, "git pull --ff-only");
    try {
      const result = await App.api.post("/api/github/pull", { path: repo.path });
      remove();
      if (result.ok) App.toast.success(`${repo.name} updated`, (result.output || "").split("\n").slice(-3).join(" "));
      else App.toast.error(`${repo.name}: pull failed`, result.output || "Unknown error");
      GitHub.scan(false);
    } catch (error) {
      remove();
      App.showError(`Could not pull ${repo.name}`, error);
    }
  }

  async function openCode(repo) {
    try {
      await App.api.post("/api/github/open-code", { path: repo.path });
      App.toast.success("VS Code", `Opening ${repo.name}`);
    } catch (error) {
      App.showError("Could not open VS Code", error);
    }
  }

  async function openFolder(repo) {
    try {
      await App.api.post("/api/github/open-folder", { path: repo.path });
      App.toast.success("Explorer", `Opening ${repo.name}`);
    } catch (error) {
      App.showError("Could not open the folder", error);
    }
  }

  async function openRemote(repo) {
    try {
      await App.api.post("/api/github/open-remote", { path: repo.path });
    } catch (error) {
      App.showError("Could not open the remote", error);
    }
  }

  GitHub.openDesktop = async function openDesktop() {
    try {
      const result = await App.api.post("/api/github/open-desktop");
      App.toast.success("GitHub Desktop", `Launched ${result.launched || ""}`);
    } catch (error) {
      App.showError("Could not launch GitHub Desktop", error);
    }
  };

  async function addRepo() {
    const path = await App.prompt({
      title: "Add repository",
      message: "Enter the full path to a local Git repository. It will be added to the scan list.",
      placeholder: "C:\\Users\\you\\projects\\my-repo",
      confirmText: "Add",
    });
    if (!path) return;
    try {
      await App.api.post("/api/github/repos", { path });
      App.toast.success("Repository added", path);
      GitHub.scan(true);
    } catch (error) {
      App.showError("Could not add repository", error);
    }
  }

  GitHub.scan = async function scan(force = false) {
    if (scanning) return;
    scanning = true;
    render();
    const button = $("#btn-rescan-repos");
    button?.querySelector("svg")?.classList.add("spin");
    try {
      const data = await App.api.get(App.api.withQuery("/api/github/repos", { refresh: force ? "true" : "false" }));
      repos = data.repos || [];
      App.state.repos = repos;
      App.state.githubEnv = data.environment;
      App.emit("repos", repos);
    } catch (error) {
      App.showError("Repository scan failed", error);
    } finally {
      scanning = false;
      button?.querySelector("svg")?.classList.remove("spin");
      render();
    }
  };

  async function loadNotifications(force = false) {
    try {
      const data = await App.api.get(App.api.withQuery("/api/github/notifications", { force: force ? "true" : "false" }));
      notifications = data;
      if (data.enabled) render();
    } catch (error) {
      console.warn("notifications unavailable", error);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                             */
  /* ------------------------------------------------------------------ */
  document.addEventListener("DOMContentLoaded", () => {
    $("#btn-github-desktop")?.addEventListener("click", GitHub.openDesktop);
    $("#btn-rescan-repos")?.addEventListener("click", () => GitHub.scan(true));

    GitHub.scan(false);
    loadNotifications(false);

    // Refresh repository state periodically (dirty flags change often).
    pollTimer = setInterval(() => {
      if (!document.hidden) GitHub.scan(false);
    }, 60000);
    setInterval(() => {
      if (!document.hidden) loadNotifications(false);
    }, 180000);

    // Reload when the user changes settings.
    App.on("config-saved", () => {
      GitHub.scan(true);
      loadNotifications(true);
    });
  });

  App.on("refresh-requested", () => GitHub.scan(false));
})();
