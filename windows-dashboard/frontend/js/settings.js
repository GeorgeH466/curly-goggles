/* ==========================================================================
   settings.js — full configuration modal
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, $$, el, icon } = App;

  const Settings = (App.Settings = {});
  const CARD_LABELS = {
    system: "System", gpu: "Hardware", network: "Network", weather: "Weather",
    storage: "Storage", repos: "Repositories", launcher: "Quick launch",
    notes: "Scratchpad", docker: "Docker", controls: "System controls",
  };
  const ACCENTS = ["cyan", "violet", "emerald", "amber", "rose", "blue"];

  let modalRef = null;

  /* ------------------------------------------------------------------ */
  /* Helpers                                                            */
  /* ------------------------------------------------------------------ */
  function applyHiddenCards(hidden) {
    const list = Array.isArray(hidden) ? hidden : [];
    $$("[data-card]").forEach((card) => {
      card.hidden = list.includes(card.dataset.card);
    });
  }
  App.applyHiddenCards = applyHiddenCards;

  function field(label, control, hint = "") {
    return el("div", { class: "field" }, [
      el("label", { text: label }),
      control,
      hint ? el("span", { class: "hint", text: hint }) : null,
    ]);
  }

  function row(label, control, desc = "") {
    return el("div", { class: "settings-row" }, [
      el("div", {}, [
        el("div", { text: label }),
        desc ? el("div", { class: "desc", text: desc }) : null,
      ]),
      control,
    ]);
  }

  function toggle(checked, onchange) {
    const input = el("input", { type: "checkbox", checked: checked ? "checked" : null });
    if (onchange) input.addEventListener("change", onchange);
    const wrapper = el("label", { class: "switch" }, [input, el("span")]);
    wrapper.input = input;
    return wrapper;
  }

  /* ------------------------------------------------------------------ */
  /* Modal                                                              */
  /* ------------------------------------------------------------------ */
  Settings.open = async function open() {
    let data;
    try {
      data = await App.api.get("/api/config");
    } catch (error) {
      App.showError("Could not load settings", error);
      return;
    }
    const cfg = data.config;
    const meta = data.meta;
    App.state.config = cfg;
    App.state.meta = meta;

    /* ---------------------------- panes ---------------------------- */
    const appearance = el("div", { class: "settings-pane is-active" });
    const monitoring = el("div", { class: "settings-pane" });
    const reposPane = el("div", { class: "settings-pane" });
    const terminalPane = el("div", { class: "settings-pane" });
    const filesPane = el("div", { class: "settings-pane" });
    const advanced = el("div", { class: "settings-pane" });

    // Appearance
    const themeSelect = el("select", { class: "select" }, [
      ["dark", "Dark"], ["light", "Light"], ["system", "Follow system"],
    ].map(([value, label]) => el("option", { value, text: label, selected: cfg.ui.theme === value })));

    const accentRow = el("div", { class: "row wrap" }, ACCENTS.map((name) =>
      el("button", {
        type: "button",
        class: `accent-swatch${cfg.ui.accent === name ? " is-selected" : ""}`,
        style: `--swatch:${(App.ACCENTS[name] || {}).accent || "#22d3ee"}`,
        title: name,
        dataset: { accent: name },
        onclick: (event) => {
          Array.from(accentRow.children).forEach((node) => node.classList.remove("is-selected"));
          event.currentTarget.classList.add("is-selected");
        },
      })));

    const compactToggle = toggle(cfg.ui.compact);

    const cardToggles = el("div", { class: "stack" }, Object.entries(CARD_LABELS).map(([id, label]) =>
      row(label, toggle(!(cfg.ui.hidden_cards || []).includes(id), null), "")));

    appearance.append(
      el("div", { class: "settings-section" }, [
        el("h3", { text: "Theme" }),
        field("Colour scheme", themeSelect),
        el("div", { class: "field mt-12" }, [el("label", { text: "Accent" }), accentRow]),
        row("Compact mode", compactToggle, "Tighter spacing for smaller screens"),
      ]),
      el("div", { class: "settings-section" }, [
        el("h3", { text: "Visible cards" }),
        el("p", { class: "muted small", text: "Hide cards you don't use. Cards can also be drag-reordered and resized on the overview." }),
        cardToggles,
      ]),
    );

    // Monitoring
    const refreshSelect = el("select", { class: "select" }, [1, 2, 3, 5, 10].map((seconds) =>
      el("option", { value: seconds, text: `every ${seconds}s`, selected: Number(cfg.app.refresh_seconds) === seconds })));
    const openBrowser = toggle(cfg.app.open_browser);
    monitoring.append(
      el("div", { class: "settings-section" }, [
        el("h3", { text: "Refresh" }),
        field("Statistics update interval", refreshSelect, "Applies to all connected dashboard tabs after you save."),
        row("Open the browser on start", openBrowser, "When launching via start.bat"),
      ]),
      el("div", { class: "settings-section" }, [
        el("h3", { text: "Server" }),
        row("Address", el("span", { class: "mono small", text: `${cfg.app.host}:${cfg.app.port}` }), "Change port in config.json and restart"),
        row("Config file", el("span", { class: "mono small", text: meta.config_path }), ""),
      ]),
    );

    // Repositories
    const scanPaths = [...(cfg.repos.scan_paths || [])];
    const pathList = el("div", { class: "scan-path-list" });
    const renderPaths = () => {
      pathList.replaceChildren();
      if (!scanPaths.length) {
        pathList.append(el("span", { class: "muted small", text: "No folders configured" }));
      }
      scanPaths.forEach((path, index) => {
        pathList.append(el("div", { class: "scan-path" }, [
          el("span", { text: path, title: path }),
          el("button", {
            class: "icon-btn tiny",
            title: "Remove",
            onclick: () => {
              scanPaths.splice(index, 1);
              renderPaths();
            },
          }, [el("span", { html: icon("x") })]),
        ]));
      });
    };
    renderPaths();

    const newPath = el("input", { class: "input", placeholder: "C:\\Users\\you\\projects" });
    const depthInput = el("input", { class: "input", type: "number", min: "1", max: "6", value: cfg.repos.max_depth });
    const cacheInput = el("input", { class: "input", type: "number", min: "0", max: "600", value: cfg.repos.cache_seconds });
    const notifToggle = toggle(cfg.repos.notifications.enabled);
    const tokenInput = el("input", {
      class: "input",
      type: "password",
      placeholder: cfg.repos.notifications.has_token ? "•••••••• (token saved)" : "ghp_… (optional)",
      autocomplete: "off",
    });
    const vscodeInput = el("input", { class: "input", value: cfg.paths.vscode || "", placeholder: "code" });
    const desktopInput = el("input", { class: "input", value: cfg.paths.github_desktop || "", placeholder: "auto-detect" });

    reposPane.append(
      el("div", { class: "settings-section" }, [
        el("h3", { text: "Scan folders" }),
        pathList,
        el("div", { class: "input-row" }, [
          newPath,
          el("button", {
            class: "btn ghost sm",
            text: "Add",
            onclick: () => {
              const value = newPath.value.trim();
              if (!value) return;
              scanPaths.push(value);
              newPath.value = "";
              renderPaths();
            },
          }),
        ]),
      ]),
      el("div", { class: "settings-section" }, [
        el("h3", { text: "Scanning" }),
        el("div", { class: "form-grid" }, [
          field("Max depth", depthInput, "How many folders deep to look"),
          field("Cache (seconds)", cacheInput, "Avoids rescanning on every refresh"),
        ]),
      ]),
      el("div", { class: "settings-section" }, [
        el("h3", { text: "GitHub notifications (optional)" }),
        row("Enable notifications", notifToggle, "Requires a personal access token with the notifications scope"),
        el("div", { class: "field mt-12" }, [
          el("label", { text: "Personal access token" }),
          tokenInput,
          el("span", { class: "hint", text: "Stored locally in config.json. Leave blank to keep the current token." }),
        ]),
      ]),
      el("div", { class: "settings-section" }, [
        el("h3", { text: "Applications" }),
        field("VS Code command", vscodeInput, "Executable name or full path"),
        el("div", { class: "mt-12" }, [field("GitHub Desktop path", desktopInput, "Leave blank to auto-detect")]),
      ]),
    );

    // Terminal
    const shellSelect = el("select", { class: "select" });
    const fontSize = el("input", { class: "input", type: "number", min: "9", max: "24", value: cfg.terminal.font_size });
    try {
      const shells = await App.api.get("/api/terminal/shells");
      shellSelect.append(el("option", { value: "auto", text: "Automatic" }));
      (shells.shells || []).forEach((shell) => {
        shellSelect.append(el("option", {
          value: shell.id, text: shell.name, selected: cfg.terminal.default_shell === shell.id,
        }));
      });
      if (cfg.terminal.default_shell === "auto") shellSelect.value = "auto";
      terminalPane.append(el("div", { class: "settings-section" }, [
        el("h3", { text: "Shell" }),
        field("Default shell", shellSelect, `Backend: ${shells.pty}`),
        row("Font size", fontSize),
        row("ConPTY", el("span", { class: `badge ${shells.conpty ? "" : "badge-warn"}`, text: shells.conpty ? "available" : "not installed" }),
          shells.conpty ? "Full interactive terminal support" : "Install pywinpty for colours and TUI apps"),
      ]));
    } catch {
      terminalPane.append(el("p", { class: "muted small", text: "Could not load shell list." }));
    }

    // Files
    const hiddenToggle = toggle(cfg.files.show_hidden);
    const recycleToggle = toggle(cfg.files.delete_to_recycle_bin);
    const startPath = el("input", { class: "input", value: cfg.files.start_path || meta.home });
    filesPane.append(el("div", { class: "settings-section" }, [
      el("h3", { text: "File explorer" }),
      row("Show hidden files", hiddenToggle),
      row("Delete to Recycle Bin", recycleToggle, "Turn off to delete permanently (not recommended)"),
      el("div", { class: "mt-12" }, [field("Starting folder", startPath)]),
    ]));

    // Weather / Docker / Advanced
    const weatherToggle = toggle(cfg.weather.enabled);
    const weatherLocation = el("input", { class: "input", value: cfg.weather.location, placeholder: "auto-detect from IP" });
    const weatherUnits = el("select", { class: "select" }, [
      ["metric", "Celsius"], ["imperial", "Fahrenheit"],
    ].map(([value, label]) => el("option", { value, text: label, selected: cfg.weather.units === value })));
    const dockerToggle = toggle(cfg.docker.enabled);
    const speedtestToggle = toggle(cfg.network.speedtest);

    reposPane.append(el("div", { class: "settings-section" }, [
      el("h3", { text: "Optional integrations" }),
      row("Weather widget", weatherToggle, "Uses wttr.in (internet required)"),
      el("div", { class: "form-grid mt-12" }, [
        field("Location", weatherLocation, "City name, or blank to auto-detect"),
        field("Units", weatherUnits),
      ]),
      row("Docker panel", dockerToggle, "Automatically hidden when Docker isn't running"),
      row("Allow speed test", speedtestToggle, "Downloads ~25 MB from Cloudflare on demand"),
    ]));

    const killDash = el("button", {
      class: "btn danger sm",
      text: "Stop dashboard",
      onclick: async () => {
        const ok = await App.confirm({
          title: "Stop the dashboard?",
          message: "The server process will exit. Terminals will be closed. Restart it with start.bat.",
          confirmText: "Stop server",
          danger: true,
        });
        if (!ok) return;
        try {
          await App.api.post("/api/app/shutdown");
        } catch { /* the server may close the connection before replying */ }
        const veil = $("#boot-veil");
        if (veil) {
          veil.classList.remove("is-hidden");
          veil.querySelector(".boot-text").textContent = "Dashboard stopped — you can close this tab";
        }
        modalRef?.close();
      },
    });

    const resetConfig = el("button", {
      class: "btn danger sm",
      text: "Reset configuration",
      onclick: async () => {
        const ok = await App.confirm({
          title: "Reset all settings?",
          message: "config.json will be recreated with the defaults, including shortcuts and scan folders. Notes are not affected.",
          confirmText: "Reset",
          danger: true,
        });
        if (!ok) return;
        try {
          await App.api.post("/api/config/reset?confirm=true");
          App.toast.success("Configuration reset", "Reloading…");
          setTimeout(() => location.reload(), 900);
        } catch (error) {
          App.showError("Could not reset configuration", error);
        }
      },
    });

    advanced.append(
      el("div", { class: "settings-section" }, [
        el("h3", { text: "About" }),
        row("Version", el("span", { class: "mono small", text: meta.version })),
        row("Python", el("span", { class: "mono small", text: meta.python })),
        row("Executable", el("span", { class: "mono small", text: meta.executable, title: meta.executable })),
        row("Privileges", el("span", { class: `badge ${meta.admin ? "" : "badge-muted"}`, text: meta.admin ? "Administrator" : "Standard user" })),
        row("Platform", el("span", { class: "mono small", text: meta.platform })),
        row("Home folder", el("span", { class: "mono small", text: meta.home, title: meta.home })),
      ]),
      el("div", { class: "settings-section" }, [
        el("h3", { text: "Danger zone" }),
        el("div", { class: "row wrap" }, [killDash, resetConfig]),
        el("p", { class: "muted small mt-8", text: "Note: power actions (shutdown, restart, sleep) live in the System view." }),
      ]),
    );

    /* --------------------------- structure -------------------------- */
    const tabs = [
      { id: "appearance", label: "Appearance", pane: appearance },
      { id: "monitoring", label: "Monitoring", pane: monitoring },
      { id: "repos", label: "Repositories", pane: reposPane },
      { id: "terminal", label: "Terminal", pane: terminalPane },
      { id: "files", label: "Files", pane: filesPane },
      { id: "advanced", label: "Advanced", pane: advanced },
    ];

    const tabStrip = el("div", { class: "settings-tabs" });
    const panes = el("div", {});
    tabs.forEach((tab, index) => {
      tabStrip.append(el("button", {
        class: `settings-tab${index === 0 ? " is-active" : ""}`,
        text: tab.label,
        onclick: (event) => {
          Array.from(tabStrip.children).forEach((node) => node.classList.remove("is-active"));
          Array.from(panes.children).forEach((node) => node.classList.remove("is-active"));
          event.currentTarget.classList.add("is-active");
          tab.pane.classList.add("is-active");
        },
      }));
      panes.append(tab.pane);
    });

    // The modal already provides padding; cancel it so the tabs sit flush
    // against the header and the panes keep a comfortable gutter.
    const container = el("div", { class: "settings-wrap" }, [
      tabStrip,
      el("div", { class: "settings-scroll" }, [panes]),
    ]);

    const save = async () => {
      const hidden = [];
      // Card toggles are laid out in the same order as CARD_LABELS.
      const switches = Array.from(cardToggles.children).map((node) => node.querySelector("input"));
      Object.keys(CARD_LABELS).forEach((id, index) => {
        if (switches[index] && !switches[index].checked) hidden.push(id);
      });

      const patch = {
        app: {
          refresh_seconds: Number(refreshSelect.value),
          open_browser: openBrowser.input.checked,
        },
        ui: {
          theme: themeSelect.value,
          accent: (accentRow.querySelector(".is-selected") || accentRow.children[0]).dataset.accent,
          compact: compactToggle.input.checked,
          hidden_cards: hidden,
        },
        repos: {
          scan_paths: scanPaths,
          max_depth: App.clamp(Number(depthInput.value) || 3, 1, 6),
          cache_seconds: App.clamp(Number(cacheInput.value) || 20, 0, 600),
          notifications: { enabled: notifToggle.input.checked },
        },
        paths: {
          vscode: vscodeInput.value.trim() || "code",
          github_desktop: desktopInput.value.trim(),
        },
        terminal: {
          default_shell: shellSelect.value || "auto",
          font_size: App.clamp(Number(fontSize.value) || 13, 9, 24),
        },
        files: {
          show_hidden: hiddenToggle.input.checked,
          delete_to_recycle_bin: recycleToggle.input.checked,
          start_path: startPath.value.trim(),
        },
        weather: {
          enabled: weatherToggle.input.checked,
          location: weatherLocation.value.trim(),
          units: weatherUnits.value,
        },
        docker: { enabled: dockerToggle.input.checked },
        network: { speedtest: speedtestToggle.input.checked },
      };
      const token = tokenInput.value.trim();
      if (token) patch.repos.notifications.token = token;

      const button = saveBtn;
      button.disabled = true;
      try {
        const result = await App.api.patch("/api/config", { patch });
        App.state.config = result.config;
        App.applyTheme(result.config.ui.theme);
        App.applyAccent(result.config.ui.accent);
        applyHiddenCards(result.config.ui.hidden_cards);
        App.state.features = App.state.features || {};
        App.state.features.weather = result.config.weather.enabled;
        App.emit("config-saved", result.config);
        App.toast.success("Settings saved", "Some changes apply on the next refresh");
        modalRef?.close();
      } catch (error) {
        App.showError("Could not save settings", error);
      } finally {
        button.disabled = false;
      }
    };

    const saveBtn = el("button", { class: "btn", text: "Save settings", onclick: save });

    modalRef = App.modal({
      title: "Settings",
      subtitle: `config.json · ${meta.config_path}`,
      body: container,
      wide: "xl",
      footer: [
        el("button", { class: "btn ghost", text: "Close", onclick: () => modalRef?.close() }),
        saveBtn,
      ],
      onClose: () => { modalRef = null; },
    });

    // Focus management for the first tab.
    setTimeout(() => modalRef?.body.querySelector(".settings-tabs button")?.focus(), 60);
  };

  document.addEventListener("DOMContentLoaded", () => {
    $("#btn-settings")?.addEventListener("click", () => Settings.open());
    App.on("config-loaded", (cfg) => applyHiddenCards(cfg.ui.hidden_cards));
  });
})();
