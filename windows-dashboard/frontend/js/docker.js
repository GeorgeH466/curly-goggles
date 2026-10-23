/* ==========================================================================
   docker.js — optional Docker panel (hidden automatically when unavailable)
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, el, icon } = App;

  const Docker = (App.Docker = {});
  let busy = false;
  let timer = null;

  const ACTION_LABEL = { start: "Start", stop: "Stop", restart: "Restart", remove: "Remove" };

  function hideCard() {
    const card = document.querySelector('[data-card="docker"]');
    if (card) card.hidden = true;
  }

  function showCard() {
    const card = document.querySelector('[data-card="docker"]');
    if (card) card.hidden = false;
  }

  function containerRow(container) {
    const running = container.state === "running";
    const stats = container.stats || {};
    return el("div", { class: "docker-row" }, [
      el("span", { class: `state ${container.state}`, title: container.status }),
      el("div", { class: "docker-name" }, [
        el("strong", { text: container.name }),
        el("small", { text: `${container.image}${container.ports ? ` · ${container.ports}` : ""}` }),
      ]),
      stats.cpu ? el("div", { class: "docker-stats" }, [
        el("div", { text: stats.cpu }),
        el("div", { text: stats.memory }),
      ]) : el("div", { class: "docker-stats muted", text: running ? "no stats" : container.status }),
      el("div", { class: "row", style: "gap:2px" }, [
        el("button", {
          class: "icon-btn tiny",
          title: "Logs",
          onclick: () => showLogs(container),
        }, [el("span", { html: icon("book") })]),
        el("button", {
          class: "icon-btn tiny",
          title: running ? "Stop container" : "Start container",
          onclick: () => action(container, running ? "stop" : "start"),
        }, [el("span", { html: icon(running ? "stop" : "play") })]),
        el("button", {
          class: "icon-btn tiny",
          title: "Restart container",
          onclick: () => action(container, "restart"),
        }, [el("span", { html: icon("refresh") })]),
      ]),
    ]);
  }

  async function render() {
    const body = $("#docker-body");
    const badge = $("#docker-badge");
    if (!body) return;
    if (busy) return;
    busy = true;
    try {
      const data = await App.api.get("/api/docker");
      if (!data.available) {
        hideCard();
        App.state.features.docker = false;
        return;
      }
      showCard();
      App.state.features.docker = true;
      if (badge) {
        badge.textContent = `${data.running}/${data.total} running`;
        badge.className = "badge";
      }
      const containers = (data.containers || []).slice().sort((a, b) => {
        if (a.state === b.state) return a.name.localeCompare(b.name);
        return a.state === "running" ? -1 : 1;
      });
      if (!containers.length) {
        body.replaceChildren(el("div", { class: "empty-state" }, [
          el("span", { html: icon("box") }),
          el("p", { text: "No containers yet" }),
          el("small", { class: "muted", text: data.version ? `Docker ${data.version}` : "" }),
        ]));
        return;
      }
      const list = el("div", { class: "docker-list" }, containers.map(containerRow));
      body.replaceChildren(
        list,
        el("div", { class: "row between mt-12 muted small" }, [
          el("span", { text: `${data.images || 0} images · Docker ${data.version || "?"}` }),
          el("span", { text: `${data.running} running` }),
        ]),
      );
    } catch (error) {
      console.warn("docker unavailable", error);
      hideCard();
    } finally {
      busy = false;
    }
  }

  async function action(container, verb) {
    if (verb === "remove") {
      const ok = await App.confirm({
        title: "Remove container?",
        message: `Force-remove "${container.name}"? The image stays on disk.`,
        confirmText: "Remove",
        danger: true,
      });
      if (!ok) return;
    }
    try {
      const result = await App.api.post("/api/docker/action", { name: container.name, action: verb });
      if (result.ok) App.toast.success(`Container ${verb}`, container.name);
      else App.toast.error(`Could not ${verb} container`, result.output || "");
      setTimeout(render, 900);
    } catch (error) {
      App.showError(`Could not ${verb} container`, error);
    }
  }

  async function showLogs(container) {
    const body = el("div", { class: "stack" }, [
      el("pre", { class: "preview-pre", text: "Loading logs…" }),
    ]);
    const modal = App.modal({
      title: `${container.name} · logs`,
      subtitle: container.image,
      body,
      wide: "xl",
      footer: [
        el("button", { class: "btn ghost", text: "Close", onclick: () => modal.close() }),
        el("button", { class: "btn ghost", text: "Refresh", onclick: async () => {
          const pre = body.querySelector("pre");
          if (pre) pre.textContent = "Refreshing…";
          try {
            const data = await App.api.get(App.api.withQuery("/api/docker/logs", { name: container.name, lines: 200 }));
            if (pre) pre.textContent = data.logs || "(no output)";
          } catch (error) {
            if (pre) pre.textContent = error.message;
          }
        } }),
      ],
    });
    try {
      const data = await App.api.get(App.api.withQuery("/api/docker/logs", { name: container.name, lines: 200 }));
      const pre = body.querySelector("pre");
      if (pre) pre.textContent = data.logs || "(no output)";
    } catch (error) {
      const pre = body.querySelector("pre");
      if (pre) pre.textContent = error.message;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("#docker-refresh")?.addEventListener("click", () => {
      const iconEl = $("#docker-refresh svg");
      iconEl?.classList.add("spin");
      render().finally(() => iconEl?.classList.remove("spin"));
    });
    setTimeout(render, 2200);
    timer = setInterval(() => {
      if (!document.hidden && App.state.view === "overview") render();
    }, 15000);
    App.on("config-saved", render);
  });

  Docker.refresh = render;
})();
