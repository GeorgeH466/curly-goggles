/* ==========================================================================
   app.js — application shell: routing, boot sequence, live stats socket,
   theme handling, draggable/resizable cards and the command palette.
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, $$, el, fmt, icon } = App;

  const VIEW_META = {
    overview: { title: "Overview", subtitle: "Everything about your machine, at a glance" },
    terminal: { title: "Terminal", subtitle: "Interactive PowerShell / CMD sessions" },
    files: { title: "Files", subtitle: "Browse, open and manage your files" },
    network: { title: "Network", subtitle: "Adapters, latency, ports and throughput" },
    system: { title: "System", subtitle: "Hardware, processes, maintenance and power" },
    notes: { title: "Notes", subtitle: "Markdown scratchpad, saved to disk" },
  };

  const ACCENTS = {
    cyan: { accent: "#22d3ee", accent2: "#a78bfa", soft: "rgba(34,211,238,.14)" },
    violet: { accent: "#a78bfa", accent2: "#f472b6", soft: "rgba(167,139,250,.15)" },
    emerald: { accent: "#34d399", accent2: "#22d3ee", soft: "rgba(52,211,153,.14)" },
    amber: { accent: "#fbbf24", accent2: "#fb923c", soft: "rgba(251,191,36,.14)" },
    rose: { accent: "#f472b6", accent2: "#a78bfa", soft: "rgba(244,114,182,.14)" },
    blue: { accent: "#60a5fa", accent2: "#22d3ee", soft: "rgba(96,165,250,.14)" },
  };

  /* ------------------------------------------------------------------ */
  /* Theme                                                              */
  /* ------------------------------------------------------------------ */
  function applyTheme(theme) {
    const resolved = theme === "system"
      ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
      : theme;
    document.documentElement.dataset.theme = resolved === "light" ? "light" : "dark";
    const button = $("#btn-theme");
    if (button) {
      button.innerHTML = icon(resolved === "light" ? "sun" : "moon");
      button.title = resolved === "light" ? "Switch to dark mode" : "Switch to light mode";
    }
    App.state.theme = resolved;
  }

  function applyAccent(name) {
    const accent = ACCENTS[name] || ACCENTS.cyan;
    const root = document.documentElement.style;
    root.setProperty("--accent", accent.accent);
    root.setProperty("--accent-2", accent.accent2);
    root.setProperty("--accent-soft", accent.soft);
  }

  function applyCompact(compact) {
    document.body.classList.toggle("is-compact", !!compact);
  }

  async function toggleTheme() {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    applyTheme(next);
    App.state.config = App.state.config || {};
    App.state.config.ui = App.state.config.ui || {};
    App.state.config.ui.theme = next;
    try {
      await App.api.post("/api/config/theme", { theme: next });
    } catch {
      /* non-fatal: the UI already switched */
    }
    App.emit("theme", next);
  }

  /* ------------------------------------------------------------------ */
  /* Navigation                                                         */
  /* ------------------------------------------------------------------ */
  function showView(name, { persist = true } = {}) {
    if (!VIEW_META[name]) name = "overview";
    App.state.view = name;
    $$(".nav-item").forEach((btn) => btn.classList.toggle("is-active", btn.dataset.view === name));
    $$(".view").forEach((view) => view.classList.toggle("is-active", view.dataset.view === name));
    const meta = VIEW_META[name];
    $("#page-title").textContent = meta.title;
    $("#page-subtitle").textContent = meta.subtitle;
    document.title = `${meta.title} · Command Center`;
    $$(".card.is-maximized").forEach((card) => card.classList.remove("is-maximized"));
    App.emit("view", name);

    if (persist) {
      try {
        App.api.patch("/api/config", { patch: { ui: { active_view: name } } }).catch(() => {});
      } catch { /* ignore */ }
    }
    // Views that need a post-show refresh.
    if (name === "files" && App.Files && App.Files.ensureLoaded) App.Files.ensureLoaded();
    if (name === "network" && App.Network && App.Network.refresh) App.Network.refresh();
    if (name === "system" && App.System && App.System.refreshPanels) App.System.refreshPanels();
    if (name === "notes" && App.Notes && App.Notes.focus) App.Notes.focus();
    if (name === "terminal" && App.Terminal && App.Terminal.onShow) App.Terminal.onShow();
  }

  /* ------------------------------------------------------------------ */
  /* Live stats socket                                                  */
  /* ------------------------------------------------------------------ */
  const stats = {
    ws: null,
    attempts: 0,
    timer: null,
    manuallyClosed: false,
  };

  function setConnection(state, detail) {
    App.state.connected = state === "online";
    const dot = $("#link-dot");
    const label = $("#link-label");
    const detailEl = $("#link-detail");
    if (dot) dot.dataset.state = state;
    if (label) label.textContent = { online: "Live", connecting: "Connecting", offline: "Offline" }[state] || state;
    if (detailEl && detail) detailEl.textContent = detail;
  }

  function connect() {
    if (stats.ws && (stats.ws.readyState === WebSocket.OPEN || stats.ws.readyState === WebSocket.CONNECTING)) return;
    clearTimeout(stats.timer);
    setConnection("connecting", "opening stats stream…");
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    let socket;
    try {
      socket = new WebSocket(`${protocol}://${location.host}/api/system/ws`);
    } catch (error) {
      scheduleReconnect();
      return;
    }
    stats.ws = socket;

    socket.addEventListener("open", () => {
      stats.attempts = 0;
      setConnection("online", "streaming every couple of seconds");
    });

    socket.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.type === "hello" || message.type === "stats") {
        if (message.payload) ingestStats(message.payload);
      }
    });

    socket.addEventListener("close", () => {
      stats.ws = null;
      if (stats.manuallyClosed) return;
      setConnection("offline", "reconnecting…");
      scheduleReconnect();
    });

    socket.addEventListener("error", () => {
      try { socket.close(); } catch { /* ignore */ }
    });
  }

  function scheduleReconnect() {
    stats.attempts += 1;
    const delay = Math.min(1000 * 2 ** Math.min(stats.attempts, 4), 15000);
    clearTimeout(stats.timer);
    stats.timer = setTimeout(connect, delay);
  }

  function ingestStats(payload) {
    App.state.stats = payload;
    if (payload.history) App.state.history = payload.history;
    if (payload.static) App.state.info = { ...(App.state.info || {}), static: payload.static };

    const cpu = payload.cpu ? payload.cpu.percent : 0;
    const mem = payload.memory ? payload.memory.percent : 0;
    const net = payload.network || {};
    const topCpu = $("#top-cpu");
    const topMem = $("#top-mem");
    const topNet = $("#top-net");
    if (topCpu) topCpu.textContent = `${cpu.toFixed(0)}%`;
    if (topMem) topMem.textContent = `${mem.toFixed(0)}%`;
    if (topNet) topNet.textContent = fmt.speed((net.download_bps || 0) + (net.upload_bps || 0));
    App.emit("stats", payload);
  }

  function requestRefresh() {
    if (stats.ws && stats.ws.readyState === WebSocket.OPEN) {
      stats.ws.send("refresh");
    } else {
      connect();
    }
  }

  /* ------------------------------------------------------------------ */
  /* Clock                                                              */
  /* ------------------------------------------------------------------ */
  function tickClock() {
    const now = new Date();
    const time = $("#clock-time");
    const date = $("#clock-date");
    if (time) time.textContent = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    if (date) {
      date.textContent = now.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short" });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Cards: drag to reorder, resize spans, maximise                     */
  /* ------------------------------------------------------------------ */
  function cardId(card) {
    return card.dataset.card;
  }

  function applyCardOrder(order) {
    const grid = $("#card-grid");
    if (!grid || !Array.isArray(order) || !order.length) return;
    const cards = new Map($$(".card", grid).map((card) => [cardId(card), card]));
    for (const id of order) {
      const card = cards.get(id);
      if (card) grid.append(card);
    }
    // Append any cards that were not in the saved order at the end.
    cards.forEach((card) => {
      if (!order.includes(cardId(card))) grid.append(card);
    });
  }

  function saveCardOrder() {
    const grid = $("#card-grid");
    if (!grid) return;
    const order = $$(".card", grid).map(cardId);
    const patch = { patch: { ui: { card_order: order } } };
    if (App.state.config) App.state.config.ui = { ...(App.state.config.ui || {}), card_order: order };
    App.api.patch("/api/config", patch).catch(() => {});
  }

  function saveCardSpan(id, span) {
    const spans = { ...((App.state.config && App.state.config.ui.card_spans) || {}) };
    spans[id] = span;
    if (App.state.config) {
      App.state.config.ui = { ...(App.state.config.ui || {}), card_spans: spans };
    }
    App.api.patch("/api/config", { patch: { ui: { card_spans: spans } } }).catch(() => {});
  }

  function applyCardSpans(spans) {
    if (!spans || typeof spans !== "object") return;
    $$(".card").forEach((card) => {
      const span = Number(spans[cardId(card)]);
      if (!span) return;
      card.classList.remove("card-wide-2", "card-wide-3", "card-wide-4");
      if (span > 1) card.classList.add(`card-wide-${Math.min(span, 4)}`);
    });
  }

  /**
   * Pointer-based reordering: dragging a card's header live-sorts it into the
   * grid. HTML5 drag & drop was avoided because it hijacks text selection in
   * the notes/terminal cards.
   */
  function initCardDrag() {
    const grid = $("#card-grid");
    if (!grid) return;

    $$(".card", grid).forEach((card) => {
      card.setAttribute("draggable", "false");
      const head = card.querySelector(".card-head");
      if (!head) return;

      head.addEventListener("mousedown", (event) => {
        if (event.button !== 0) return;
        if (event.target.closest("button, a, input, select, textarea, .card-tools")) return;
        event.preventDefault();

        const startX = event.clientX;
        const startY = event.clientY;
        let active = false;
        let hoverTarget = null;

        const onMove = (moveEvent) => {
          if (!active) {
            if (Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) < 6) return;
            active = true;
            card.classList.add("is-dragging");
            document.body.style.userSelect = "none";
            document.body.style.cursor = "grabbing";
          }
          card.style.pointerEvents = "none";
          const under = document.elementFromPoint(moveEvent.clientX, moveEvent.clientY);
          card.style.pointerEvents = "";
          const target = under && under.closest ? under.closest(".card") : null;
          if (!target || target === card || !grid.contains(target)) return;

          const rect = target.getBoundingClientRect();
          const sameRow = moveEvent.clientY > rect.top && moveEvent.clientY < rect.bottom;
          const before = sameRow
            ? moveEvent.clientX < rect.left + rect.width / 2
            : moveEvent.clientY < rect.top + rect.height / 2;

          if (hoverTarget && hoverTarget !== target) hoverTarget.classList.remove("drop-target");
          hoverTarget = target;
          target.classList.add("drop-target");
          if (before) target.before(card);
          else target.after(card);
        };

        const onUp = () => {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          document.body.style.userSelect = "";
          document.body.style.cursor = "";
          card.classList.remove("is-dragging");
          if (hoverTarget) hoverTarget.classList.remove("drop-target");
          if (active) saveCardOrder();
        };

        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
    });

    // Resize handles adjust the column span (1-4) and persist it.
    $$(".resize-handle").forEach((handle) => {
      handle.addEventListener("mousedown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const card = handle.closest(".card");
        if (!card) return;
        const gridRect = grid.getBoundingClientRect();
        const columns = getComputedStyle(grid).gridTemplateColumns.split(" ").length || 1;
        const columnWidth = gridRect.width / columns;
        const startSpan = Math.max(1, Math.round(card.getBoundingClientRect().width / columnWidth));
        const startX = event.clientX;
        document.body.style.cursor = "nwse-resize";
        document.body.style.userSelect = "none";

        const onMove = (moveEvent) => {
          const delta = moveEvent.clientX - startX;
          const span = App.clamp(Math.round(startSpan + delta / columnWidth), 1, Math.min(columns, 4));
          card.classList.remove("card-wide-2", "card-wide-3", "card-wide-4");
          if (span > 1) card.classList.add(`card-wide-${span}`);
        };
        const onUp = () => {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          document.body.style.cursor = "";
          document.body.style.userSelect = "";
          const cols = getComputedStyle(grid).gridTemplateColumns.split(" ").length || 1;
          const ratio = card.getBoundingClientRect().width / (grid.getBoundingClientRect().width / cols);
          saveCardSpan(cardId(card), App.clamp(Math.round(ratio), 1, Math.min(cols, 4)));
        };
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
    });

    // Maximise buttons
    $$("[data-card-action='maximize']").forEach((button) => {
      button.addEventListener("click", () => {
        const card = button.closest(".card");
        const maximized = card.classList.toggle("is-maximized");
        button.title = maximized ? "Restore" : "Expand";
        if (maximized) {
          const closeOnEscape = (event) => {
            if (event.key === "Escape") {
              card.classList.remove("is-maximized");
              document.removeEventListener("keydown", closeOnEscape);
            }
          };
          document.addEventListener("keydown", closeOnEscape);
        }
        App.emit("card-maximized", { card, maximized });
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Command palette                                                    */
  /* ------------------------------------------------------------------ */
  const palette = {
    open: false,
    items: [],
    selected: 0,
  };

  function paletteItems() {
    const views = Object.entries(VIEW_META).map(([id, meta]) => ({
      label: meta.title,
      hint: "View",
      icon: {
        overview: "grid", terminal: "terminal", files: "folder",
        network: "wifi", system: "activity", notes: "edit",
      }[id],
      run: () => showView(id),
    }));
    const actions = [
      { label: "Toggle theme", hint: "Appearance", icon: "moon", run: toggleTheme },
      { label: "Open settings", hint: "App", icon: "settings", run: () => App.Settings && App.Settings.open() },
      { label: "Refresh statistics", hint: "System", icon: "refresh", run: requestRefresh },
      { label: "New terminal tab", hint: "Terminal", icon: "plus", run: () => { showView("terminal"); App.Terminal && App.Terminal.newTab(); } },
      { label: "Rescan repositories", hint: "Git", icon: "git", run: () => App.GitHub && App.GitHub.scan(true) },
      { label: "Open GitHub Desktop", hint: "Git", icon: "external", run: () => App.GitHub && App.GitHub.openDesktop() },
      { label: "New note", hint: "Notes", icon: "edit", run: () => { showView("notes"); App.Notes && App.Notes.create(); } },
      { label: "Run speed test", hint: "Network", icon: "activity", run: () => { showView("network"); App.Network && App.Network.speedTest(); } },
      { label: "Flush DNS cache", hint: "Maintenance", icon: "wifi", run: () => App.System && App.System.maintenance("flush-dns") },
      { label: "Lock PC", hint: "Power", icon: "lock", run: () => App.System && App.System.power("lock") },
      { label: "Open API documentation", hint: "Developer", icon: "book", run: () => window.open("/api/docs", "_blank") },
    ];
    return [...views, ...actions];
  }

  function renderPalette(filter = "") {
    const list = $("#palette-list");
    if (!list) return;
    const needle = filter.trim().toLowerCase();
    const items = paletteItems().filter((item) =>
      !needle || item.label.toLowerCase().includes(needle) || item.hint.toLowerCase().includes(needle));
    palette.items = items;
    palette.selected = 0;
    list.replaceChildren();
    if (!items.length) {
      list.append(el("div", { class: "palette-item", text: "No matching commands" }));
      return;
    }
    items.forEach((item, index) => {
      const node = el("div", {
        class: `palette-item${index === 0 ? " is-selected" : ""}`,
        onclick: () => { closePalette(); item.run(); },
      }, [
        el("span", { html: icon(item.icon || "play") }),
        el("span", { text: item.label, class: "grow" }),
        el("small", { text: item.hint }),
      ]);
      list.append(node);
    });
  }

  function openPalette() {
    const overlay = $("#palette");
    if (!overlay) return;
    palette.open = true;
    overlay.hidden = false;
    const input = $("#palette-input");
    input.value = "";
    renderPalette("");
    setTimeout(() => input.focus(), 30);
  }

  function closePalette() {
    palette.open = false;
    const overlay = $("#palette");
    if (overlay) overlay.hidden = true;
  }

  function movePalette(delta) {
    if (!palette.items.length) return;
    palette.selected = (palette.selected + delta + palette.items.length) % palette.items.length;
    $$("#palette-list .palette-item").forEach((node, index) => {
      node.classList.toggle("is-selected", index === palette.selected);
      if (index === palette.selected) node.scrollIntoView({ block: "nearest" });
    });
  }

  function initPalette() {
    const input = $("#palette-input");
    if (!input) return;
    input.addEventListener("input", () => renderPalette(input.value));
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") { event.preventDefault(); movePalette(1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); movePalette(-1); }
      else if (event.key === "Enter") {
        event.preventDefault();
        const item = palette.items[palette.selected];
        if (item) { closePalette(); item.run(); }
      } else if (event.key === "Escape") {
        closePalette();
      }
    });
    $("#palette").addEventListener("mousedown", (event) => {
      if (event.target.id === "palette") closePalette();
    });
    $("#btn-palette").addEventListener("click", openPalette);
  }

  /* ------------------------------------------------------------------ */
  /* Boot                                                               */
  /* ------------------------------------------------------------------ */
  async function loadConfig() {
    const data = await App.api.get("/api/config");
    App.state.config = data.config;
    App.state.meta = data.meta;
    const version = $("#app-version");
    if (version) version.textContent = `v${data.meta.version}`;
    applyTheme(data.config.ui.theme || "dark");
    applyAccent(data.config.ui.accent || "cyan");
    applyCompact(data.config.ui.compact);
    applyCardOrder(data.config.ui.card_order);
    applyCardSpans(data.config.ui.card_spans);
    if (App.applyHiddenCards) App.applyHiddenCards(data.config.ui.hidden_cards);
    App.emit("config-loaded", data.config);
    return data;
  }

  async function loadFeatures() {
    try {
      const info = await App.api.get("/api/system/info");
      App.state.info = info;
      App.state.features = info.features || {};
      App.emit("info", info);
      return info;
    } catch (error) {
      console.warn("Could not load system info", error);
      return null;
    }
  }

  function initNav() {
    $$(".nav-item").forEach((button) => {
      button.addEventListener("click", () => showView(button.dataset.view));
    });
    $$("[data-goto]").forEach((node) => {
      node.addEventListener("click", () => showView(node.dataset.goto));
    });
    $("#mobile-nav")?.addEventListener("click", () => {
      $(".app-shell")?.classList.toggle("sidebar-collapsed");
    });
    $("#sidebar-collapse")?.addEventListener("click", () => {
      const shell = $(".app-shell");
      const collapsed = shell.classList.toggle("sidebar-collapsed");
      localStorage.setItem("cc.sidebar", collapsed ? "collapsed" : "open");
    });
    if (localStorage.getItem("cc.sidebar") === "collapsed") {
      $(".app-shell")?.classList.add("sidebar-collapsed");
    }
  }

  function initShortcuts() {
    document.addEventListener("keydown", (event) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        palette.open ? closePalette() : openPalette();
        return;
      }
      if (event.key === "Escape" && palette.open) {
        closePalette();
        return;
      }
      if (typing) return;
      if (event.altKey && /^[1-6]$/.test(event.key)) {
        event.preventDefault();
        const views = ["overview", "terminal", "files", "network", "system", "notes"];
        showView(views[Number(event.key) - 1]);
      }
      if (event.key === "F5") {
        event.preventDefault();
        requestRefresh();
      }
    });
  }

  function initButtons() {
    $("#btn-theme")?.addEventListener("click", toggleTheme);
    $("#btn-settings")?.addEventListener("click", () => App.Settings && App.Settings.open());
    $("#btn-docs")?.addEventListener("click", () => window.open("/api/docs", "_blank"));
    $("#btn-refresh")?.addEventListener("click", () => {
      requestRefresh();
      App.emit("refresh-requested");
      const button = $("#btn-refresh");
      button.querySelector("svg")?.classList.add("spin");
      setTimeout(() => button.querySelector("svg")?.classList.remove("spin"), 700);
    });

    // Prefer reduced-motion users still get the clock.
    setInterval(tickClock, 1000);
    tickClock();
  }

  async function boot() {
    initNav();
    initShortcuts();
    initButtons();
    initPalette();
    initCardDrag();

    try {
      await loadConfig();
    } catch (error) {
      console.error("config load failed", error);
      App.toast.error("Configuration", "Could not load config.json — using defaults");
    }

    const activeView = (App.state.config && App.state.config.ui.active_view) || "overview";
    showView(activeView, { persist: false });

    connect();
    loadFeatures();

    // Give the panels a moment to render their first frame.
    setTimeout(() => {
      const veil = $("#boot-veil");
      if (veil) veil.classList.add("is-hidden");
    }, 320);

    window.addEventListener("online", connect);
    window.addEventListener("focus", () => {
      if (!App.state.connected) connect();
    });

    // System theme changes should be reflected when "system" is selected.
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
      const theme = App.state.config?.ui?.theme;
      if (theme === "system") applyTheme("system");
    });

    window.addEventListener("unhandledrejection", (event) => {
      console.error("Unhandled rejection", event.reason);
    });
  }

  App.applyTheme = applyTheme;
  App.applyAccent = applyAccent;
  App.showView = showView;
  App.requestRefresh = requestRefresh;
  App.applyCardOrder = applyCardOrder;
  App.applyCardSpans = applyCardSpans;
  App.ACCENTS = ACCENTS;
  App.connectStats = connect;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
