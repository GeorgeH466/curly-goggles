/* ==========================================================================
   launcher.js — quick launch grid with editable shortcuts
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, el, icon } = App;

  const Launcher = (App.Launcher = {});
  let shortcuts = [];

  const ICON_CHOICES = [
    "app", "code", "folder", "activity", "edit", "globe", "message", "music",
    "terminal", "game", "spotify", "chrome", "firefox", "steam", "discord",
    "settings", "camera", "video", "file",
  ];
  const COLOR_CHOICES = ["#22d3ee", "#a78bfa", "#34d399", "#fbbf24", "#f472b6", "#60a5fa", "#fb923c", "#e5e7eb"];

  /* ------------------------------------------------------------------ */
  /* Rendering                                                          */
  /* ------------------------------------------------------------------ */
  function render() {
    const body = $("#launcher-body");
    if (!body) return;
    if (!shortcuts.length) {
      body.replaceChildren(el("div", { class: "empty-state" }, [
        el("span", { html: icon("rocket") }),
        el("p", { text: "No shortcuts yet" }),
        el("button", { class: "btn sm mt-8", text: "Add your first shortcut", onclick: () => edit(null) }),
      ]));
      return;
    }

    const grid = el("div", { class: "launcher-grid" });
    shortcuts.forEach((shortcut) => {
      const tile = el("div", {
        class: "launch-tile",
        title: `${shortcut.name}\n${shortcut.path}${shortcut.args ? ` ${shortcut.args}` : ""}`,
        style: `--tile-color:${shortcut.color || "#22d3ee"}`,
        onclick: (event) => {
          if (event.target.closest(".tile-edit")) return;
          launch(shortcut, tile);
        },
        ondblclick: (event) => {
          if (event.target.closest(".tile-edit")) return;
          edit(shortcut);
        },
      }, [
        el("button", { class: "icon-btn tiny tile-edit", title: "Edit shortcut", onclick: (event) => { event.stopPropagation(); edit(shortcut); } }, [
          el("span", { html: icon("settings") }),
        ]),
        el("div", { class: "tile-icon" }, [el("span", { html: icon(shortcut.icon || "app") })]),
        el("span", { class: "tile-name", text: shortcut.name }),
      ]);
      grid.append(tile);
    });
    grid.append(el("div", {
      class: "launch-tile",
      style: "--tile-color: var(--text-mute); border-style: dashed",
      title: "Add a shortcut",
      onclick: () => edit(null),
    }, [
      el("div", { class: "tile-icon" }, [el("span", { html: icon("plus") })]),
      el("span", { class: "tile-name", text: "Add" }),
    ]));
    body.replaceChildren(grid);
  }

  async function launch(shortcut, tile) {
    tile.classList.add("is-busy");
    try {
      const result = await App.api.post("/api/launcher/launch", { id: shortcut.id });
      App.toast.success(`Launching ${shortcut.name}`, result.launched || "");
      tile.classList.remove("is-missing");
    } catch (error) {
      App.toast.error(`Could not launch ${shortcut.name}`, error.message || "Unknown error");
      if (error.status === 400) tile.classList.add("is-missing");
    } finally {
      setTimeout(() => tile.classList.remove("is-busy"), 500);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Editor modal                                                       */
  /* ------------------------------------------------------------------ */
  function edit(shortcut) {
    const isNew = !shortcut;
    const data = shortcut || {
      name: "", path: "", args: "", icon: "app", color: "#22d3ee",
    };

    const nameInput = el("input", { class: "input", value: data.name, placeholder: "Spotify", maxlength: "60" });
    const pathInput = el("input", { class: "input", value: data.path, placeholder: "C:\\Program Files\\…\\app.exe, a folder, or https://…" });
    const argsInput = el("input", { class: "input", value: data.args || "", placeholder: "--flag value (optional)" });
    const iconSelect = el("select", { class: "select" },
      ICON_CHOICES.map((name) => el("option", { value: name, text: name, selected: data.icon === name })));
    const colorRow = el("div", { class: "row wrap" },
      COLOR_CHOICES.map((color) => el("button", {
        type: "button",
        class: "color-dot",
        style: `background:${color}`,
        title: color,
        dataset: { color },
        onclick: () => {
          colorRow.dataset.selected = color;
          Array.from(colorRow.children).forEach((dot) => dot.classList.toggle("is-selected", dot.dataset.color === color));
        },
      })));
    colorRow.dataset.selected = data.color || "#22d3ee";
    Array.from(colorRow.children).forEach((dot) => dot.classList.toggle("is-selected", dot.dataset.color === (data.color || "#22d3ee")));

    const detected = el("div", { class: "row wrap mt-8" });
    const body = el("form", { class: "form-grid", onsubmit: (event) => event.preventDefault() }, [
      field("Name", nameInput, "full"),
      field("Path or command", pathInput, "full", "A full path, an executable name on PATH, a folder, or a URL."),
      field("Arguments", argsInput, "full"),
      field("Icon", iconSelect),
      field("Colour", colorRow),
      el("div", { class: "field full" }, [
        el("label", { text: "Detected applications" }),
        detected,
        el("span", { class: "hint", text: "Click one to fill the path field." }),
      ]),
    ]);

    const saveBtn = el("button", { class: "btn", text: isNew ? "Create shortcut" : "Save changes", onclick: save });
    const footer = [el("button", { class: "btn ghost", text: "Cancel", onclick: () => modal.close() })];
    if (!isNew) {
      footer.push(el("button", {
        class: "btn danger",
        text: "Delete",
        onclick: async () => {
          const ok = await App.confirm({
            title: "Delete shortcut?",
            message: `"${data.name}" will be removed from the dashboard. The application itself is not affected.`,
            confirmText: "Delete",
            danger: true,
          });
          if (!ok) return;
          try {
            await App.api.del(`/api/launcher/shortcuts/${encodeURIComponent(data.id)}`);
            App.toast.success("Shortcut deleted", data.name);
            modal.close();
            load();
          } catch (error) {
            App.showError("Could not delete shortcut", error);
          }
        },
      }));
    }
    footer.push(saveBtn);

    const modal = App.modal({
      title: isNew ? "New shortcut" : "Edit shortcut",
      subtitle: "Launch any program, folder or URL from the dashboard",
      body,
      footer,
      wide: true,
    });

    async function save() {
      const payload = {
        name: nameInput.value.trim(),
        path: pathInput.value.trim(),
        args: argsInput.value.trim(),
        icon: iconSelect.value,
        color: colorRow.dataset.selected || "#22d3ee",
      };
      if (!payload.name || !payload.path) {
        App.toast.warn("Missing details", "Name and path are required");
        return;
      }
      try {
        if (isNew) {
          const created = await App.api.post("/api/launcher/shortcuts", payload);
          shortcuts.push(created);
          App.toast.success("Shortcut created", created.name);
        } else {
          const updated = await App.api.put(`/api/launcher/shortcuts/${encodeURIComponent(data.id)}`, payload);
          shortcuts = shortcuts.map((item) => (item.id === data.id ? updated : item));
          App.toast.success("Shortcut updated", updated.name);
        }
        modal.close();
        render();
      } catch (error) {
        App.showError("Could not save shortcut", error);
      }
    }

    App.api.get("/api/launcher/detect").then(({ apps }) => {
      if (!apps.length) {
        detected.append(el("span", { class: "muted small", text: "No known apps detected on PATH." }));
        return;
      }
      apps.forEach((app) => {
        detected.append(el("button", {
          type: "button",
          class: "pill",
          title: app.resolved,
          onclick: () => {
            pathInput.value = app.path;
            if (!nameInput.value) nameInput.value = app.name;
            iconSelect.value = app.icon;
          },
        }, [el("span", { html: icon(app.icon) }), " ", app.name]));
      });
    }).catch(() => {});
  }

  function field(label, control, extraClass = "", hint = "") {
    return el("div", { class: `field ${extraClass}` }, [
      el("label", { text: label }),
      control,
      hint ? el("span", { class: "hint", text: hint }) : null,
    ]);
  }

  async function load() {
    try {
      const data = await App.api.get("/api/launcher/shortcuts");
      shortcuts = data.shortcuts || [];
      App.state.shortcuts = shortcuts;
      render();
    } catch (error) {
      App.showError("Could not load shortcuts", error);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("#btn-add-shortcut")?.addEventListener("click", () => edit(null));
    load();
    App.on("config-saved", load);
  });

  Launcher.load = load;
  Launcher.edit = edit;
})();
