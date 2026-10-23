/* ==========================================================================
   files.js — "File Explorer Lite": browse, open, renaming, deletion
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, el, fmt, icon } = App;

  const Files = (App.Files = {});
  let currentPath = null;
  let roots = null;
  let entries = [];
  let selected = null;
  let loading = false;
  let lastData = null;
  let initialised = false;

  /* ------------------------------------------------------------------ */
  /* Sidebar                                                            */
  /* ------------------------------------------------------------------ */
  function renderRoots() {
    const quick = $("#file-quick");
    const drives = $("#file-drives");
    if (!roots) return;

    if (quick) {
      quick.replaceChildren();
      const labels = {
        Home: "home", Desktop: "folder", Documents: "file", Downloads: "download",
        Pictures: "camera", Music: "music", Videos: "video",
      };
      Object.entries(roots.common || {}).forEach(([name, path]) => {
        quick.append(el("button", {
          class: "side-link",
          title: path,
          onclick: () => load(path),
        }, [el("span", { html: icon(labels[name] || "folder") }), el("span", { text: name })]));
      });
    }

    if (drives) {
      drives.replaceChildren();
      (roots.drives || []).forEach((drive) => {
        drives.append(el("button", {
          class: "side-link",
          title: drive.path,
          onclick: () => load(drive.path),
        }, [el("span", { html: icon("drive") }), el("span", { text: drive.name })]));
      });
    }
  }

  async function loadRoots() {
    try {
      roots = await App.api.get("/api/files/roots");
      renderRoots();
      return roots;
    } catch (error) {
      App.showError("Could not load folders", error);
      return null;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Listing                                                            */
  /* ------------------------------------------------------------------ */
  function updateBreadcrumbs(breadcrumbs) {
    const bar = $("#file-breadcrumbs");
    if (!bar) return;
    bar.replaceChildren();
    (breadcrumbs || []).forEach((crumb, index, list) => {
      if (index) bar.append(el("span", { class: "crumb-sep", text: "›" }));
      bar.append(el("button", {
        class: `crumb${index === list.length - 1 ? " is-last" : ""}`,
        title: crumb.path,
        text: crumb.name,
        onclick: () => load(crumb.path),
      }));
    });
  }

  function renderRows(data) {
    const tbody = $("#file-rows");
    const empty = $("#file-empty");
    if (!tbody) return;
    tbody.replaceChildren();
    lastData = data;
    entries = data.entries || [];
    const query = ($("#file-search")?.value || "").trim().toLowerCase();
    const visible = query ? entries.filter((entry) => entry.name.toLowerCase().includes(query)) : entries;

    if (empty) empty.hidden = visible.length > 0;

    visible.forEach((entry) => {
      const row = el("tr", { class: "file-row", dataset: { path: entry.path } }, [
        el("td", {}, [
          el("div", { class: `file-name${entry.is_dir ? " is-dir" : ""}` }, [
            el("span", { html: icon(App.iconForFile(entry)) }),
            el("span", { text: entry.name }),
          ]),
        ]),
        el("td", { class: "num", text: entry.is_dir
          ? (entry.items != null ? `${entry.items} items` : "—")
          : fmt.bytes(entry.size) }),
        el("td", { class: "muted small", text: entry.modified ? fmt.date(entry.modified) : "—" }),
        el("td", { class: "actions-col" }, [
          el("div", { class: "row-actions" }, [
            el("button", { class: "icon-btn tiny", title: entry.is_dir ? "Open folder" : "Open", onclick: () => open(entry) }, [
              el("span", { html: icon(entry.is_dir ? "folder" : "external") }),
            ]),
            entry.is_dir ? null : el("button", { class: "icon-btn tiny", title: "Preview", onclick: () => preview(entry) }, [
              el("span", { html: icon("file") }),
            ]),
            el("button", { class: "icon-btn tiny", title: "Reveal in Explorer", onclick: () => reveal(entry) }, [
              el("span", { html: icon("search") }),
            ]),
            el("button", { class: "icon-btn tiny", title: "Rename", onclick: () => rename(entry) }, [
              el("span", { html: icon("edit") }),
            ]),
            el("button", { class: "icon-btn tiny", title: "Delete", onclick: () => remove(entry) }, [
              el("span", { html: icon("trash") }),
            ]),
          ]),
        ]),
      ]);
      row.addEventListener("click", () => selectRow(entry, row));
      row.addEventListener("dblclick", () => (entry.is_dir ? load(entry.path) : open(entry)));
      tbody.append(row);
    });

    const status = $("#file-status");
    if (status) {
      status.textContent = `${visible.length} item${visible.length === 1 ? "" : "s"}`
        + (query ? ` matching “${query}”` : "")
        + (data.truncated ? " · truncated" : "");
    }
    selected = null;
  }

  function selectRow(entry, row) {
    selected = entry;
    App.$$("#file-rows .file-row").forEach((tr) => tr.classList.remove("is-selected"));
    row.classList.add("is-selected");
  }

  async function load(path) {
    if (loading) return;
    loading = true;
    const tbody = $("#file-rows");
    if (tbody && !entries.length) {
      tbody.replaceChildren(el("tr", {}, [
        el("td", { colspan: "4" }, [el("div", { class: "skeleton-line" })]),
      ]));
    }
    try {
      const hidden = $("#file-hidden")?.checked ? "true" : "false";
      const data = await App.api.get(App.api.withQuery("/api/files/list", { path, hidden }));
      currentPath = data.path;
      App.state.filesPath = currentPath;
      updateBreadcrumbs(data.breadcrumbs);
      renderRows(data);
      try { localStorage.setItem("cc.lastPath", currentPath); } catch { /* ignore */ }
    } catch (error) {
      App.showError("Could not open folder", error);
      // Fall back to the home folder if the saved path is gone.
      if (path !== roots?.start) {
        try {
          const fallback = await App.api.get(App.api.withQuery("/api/files/list", { path: roots?.start }));
          currentPath = fallback.path;
          App.state.filesPath = currentPath;
          updateBreadcrumbs(fallback.breadcrumbs);
          renderRows(fallback);
        } catch { /* give up */ }
      }
    } finally {
      loading = false;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Actions                                                            */
  /* ------------------------------------------------------------------ */
  async function open(entry) {
    if (entry.is_dir) return load(entry.path);
    try {
      await App.api.post("/api/files/open", { path: entry.path });
      App.toast.success("Opening", entry.name);
    } catch (error) {
      App.showError("Could not open file", error);
    }
  }

  async function reveal(entry) {
    try {
      await App.api.post("/api/files/reveal", { path: entry.path });
    } catch (error) {
      App.showError("Could not reveal item", error);
    }
  }

  async function rename(entry) {
    const name = await App.prompt({
      title: "Rename",
      message: entry.path,
      value: entry.name,
      confirmText: "Rename",
    });
    if (!name || name === entry.name) return;
    try {
      await App.api.post("/api/files/rename", { path: entry.path, new_name: name });
      App.toast.success("Renamed", `${entry.name} → ${name}`);
      load(currentPath);
    } catch (error) {
      App.showError("Could not rename", error);
    }
  }

  async function remove(entry) {
    const ok = await App.confirm({
      title: `Delete “${entry.name}”?`,
      message: entry.is_dir
        ? "The folder and everything inside it will be removed."
        : "The file will be removed.",
      detail: "By default items go to the Recycle Bin, so you can restore them.",
      confirmText: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      const result = await App.api.del("/api/files/delete", { path: entry.path });
      App.toast.success("Deleted", result.recycled ? `${entry.name} moved to the Recycle Bin` : entry.name);
      load(currentPath);
    } catch (error) {
      App.showError("Could not delete", error);
    }
  }

  async function preview(entry) {
    try {
      const data = await App.api.get(App.api.withQuery("/api/files/preview", { path: entry.path }));
      if (!data.ok) {
        const reasons = {
          "too-large": "This file is too large to preview (over 256 KB).",
          binary: "This looks like a binary file, so it can't be shown as text.",
        };
        App.toast.warn("No preview", reasons[data.reason] || "Preview unavailable");
        return;
      }
      const body = el("div", { class: "stack" }, [
        el("div", { class: "row between" }, [
          el("span", { class: "muted small", text: `${fmt.bytes(data.size)} · ${data.encoding} · modified ${fmt.date(data.modified)}` }),
          el("button", { class: "btn ghost sm", text: "Open with default app", onclick: () => open(entry) }),
        ]),
        el("pre", {
          class: "preview-pre",
          text: data.content,
        }),
      ]);
      App.modal({ title: entry.name, body, wide: "xl" });
    } catch (error) {
      App.showError("Could not preview file", error);
    }
  }

  async function newFolder() {
    const name = await App.prompt({
      title: "New folder",
      message: `Creating in ${currentPath}`,
      placeholder: "New folder",
      confirmText: "Create",
    });
    if (!name) return;
    try {
      await App.api.post("/api/files/mkdir", { path: currentPath, name });
      App.toast.success("Folder created", name);
      load(currentPath);
    } catch (error) {
      App.showError("Could not create folder", error);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                             */
  /* ------------------------------------------------------------------ */
  async function init() {
    if (initialised) return;
    initialised = true;
    const data = await loadRoots();
    if (!data) return;
    if ($("#file-hidden") && typeof data.show_hidden === "boolean") {
      $("#file-hidden").checked = data.show_hidden;
    }
    let start = data.start;
    try {
      const saved = localStorage.getItem("cc.lastPath");
      if (saved) start = saved;
    } catch { /* ignore */ }
    load(start);
  }

  Files.ensureLoaded = function ensureLoaded() {
    if (!initialised) init();
  };

  document.addEventListener("DOMContentLoaded", () => {
    $("#file-up")?.addEventListener("click", async () => {
      try {
        const data = await App.api.get(App.api.withQuery("/api/files/list", { path: currentPath }));
        if (data.parent) load(data.parent);
      } catch (error) {
        App.showError("Could not go up", error);
      }
    });
    $("#file-home")?.addEventListener("click", () => load(roots?.home || roots?.start));
    $("#file-refresh")?.addEventListener("click", async () => {
      await loadRoots();
      load(currentPath);
    });
    $("#file-new-folder")?.addEventListener("click", newFolder);
    $("#file-hidden")?.addEventListener("change", () => load(currentPath));
    $("#file-search")?.addEventListener("input", App.debounce(() => {
      if (lastData) renderRows(lastData);
    }, 180));
    $(".files-panel")?.addEventListener("keydown", (event) => {
      if (event.key === "F5") {
        event.preventDefault();
        load(currentPath);
      }
    });
  });

  Files.load = load;
  Files.open = open;
})();
