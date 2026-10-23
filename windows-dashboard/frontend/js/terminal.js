/* ==========================================================================
   terminal.js — interactive terminal tabs backed by xterm.js + PTY websocket
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, $$, el, icon } = App;

  const Terminal = (App.Terminal = {});

  /** @type {Array<{id:string,title:string,term:any,fit:any,ws:WebSocket|null,el:HTMLElement,shell:string,mode:string,dead:boolean,disposers:Function[]}>} */
  const tabs = [];
  let activeId = null;
  let shells = [];
  let bootstrapped = false;
  let host = null;

  const THEME = {
    background: "#05070d",
    foreground: "#d7e3f4",
    cursor: "#22d3ee",
    cursorAccent: "#05070d",
    selectionBackground: "rgba(34,211,238,.30)",
    black: "#0b1220",
    red: "#f87171",
    green: "#34d399",
    yellow: "#fbbf24",
    blue: "#60a5fa",
    magenta: "#f472b6",
    cyan: "#22d3ee",
    white: "#e2e8f0",
    brightBlack: "#475569",
    brightRed: "#fca5a5",
    brightGreen: "#6ee7b7",
    brightYellow: "#fcd34d",
    brightBlue: "#93c5fd",
    brightMagenta: "#f9a8d4",
    brightCyan: "#67e8f9",
    brightWhite: "#f8fafc",
  };

  /* ------------------------------------------------------------------ */
  /* Tabs                                                               */
  /* ------------------------------------------------------------------ */
  function activeTab() {
    return tabs.find((tab) => tab.id === activeId) || null;
  }

  function renderTabs() {
    const strip = $("#terminal-tabs");
    if (!strip) return;
    strip.replaceChildren();
    tabs.forEach((tab) => {
      const node = el("div", {
        class: `term-tab${tab.id === activeId ? " is-active" : ""}`,
        title: tab.title,
        onclick: () => focusTab(tab.id),
      }, [
        el("span", { class: "dot", dataset: { dead: String(tab.dead) } }),
        el("span", { text: tab.title }),
        el("span", {
          class: "tab-close",
          title: "Close tab",
          onclick: (event) => {
            event.stopPropagation();
            closeTab(tab.id);
          },
        }, [el("span", { html: icon("x") })]),
      ]);
      strip.append(node);
    });
    if (!tabs.length) {
      strip.append(el("span", { class: "muted small", style: "padding:6px 4px", text: "No sessions" }));
    }
    updateStatus();
  }

  function focusTab(id) {
    const tab = tabs.find((item) => item.id === id);
    if (!tab) return;
    activeId = id;
    tabs.forEach((item) => {
      const visible = item.id === id;
      item.el.style.display = visible ? "block" : "none";
    });
    renderTabs();
    setTimeout(() => {
      try {
        tab.fit.fit();
        tab.term.focus();
      } catch { /* terminal may be hidden */ }
    }, 30);
  }

  function updateStatus() {
    const tab = activeTab();
    const status = $("#term-status");
    const mode = $("#term-mode");
    if (!tab) {
      if (status) status.textContent = "No session";
      if (mode) mode.textContent = "";
      return;
    }
    if (status) {
      status.textContent = `${tab.shell} · ${tab.dead ? "exited" : tab.ws && tab.ws.readyState === WebSocket.OPEN ? "connected" : "connecting…"}`;
    }
    if (mode) mode.textContent = tab.mode ? `${tab.mode} backend` : "";
  }

  /* ------------------------------------------------------------------ */
  /* Sessions                                                           */
  /* ------------------------------------------------------------------ */
  function createTerminalElement(tab) {
    const container = el("div", { class: "term-surface", style: "height:100%" });
    tab.el = container;
    host.append(container);

    const term = new window.Terminal({
      cursorBlink: true,
      cursorStyle: "bar",
      fontFamily: App.state.config?.terminal?.font_family
        || "'Cascadia Code', 'JetBrains Mono', Consolas, monospace",
      fontSize: Number(App.state.config?.terminal?.font_size) || 13,
      lineHeight: 1.15,
      scrollback: 8000,
      allowProposedApi: true,
      theme: THEME,
      macOptionIsMeta: true,
      rightClickSelectsWord: false,
    });
    const fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(container);

    // Keyboard conveniences: Ctrl+Shift+C / Ctrl+Shift+V, Ctrl+Shift+K clear.
    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown") return true;
      if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "c") {
        const selection = term.getSelection();
        if (selection) {
          navigator.clipboard.writeText(selection).catch(() => {});
          return false;
        }
      }
      if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "v") {
        navigator.clipboard.readText().then((text) => {
          if (text) send(tab, { type: "input", data: text });
        }).catch(() => {});
        return false;
      }
      return true;
    });

    // Right click pastes when there is no selection, otherwise copies.
    container.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      const selection = term.getSelection();
      if (selection) {
        navigator.clipboard.writeText(selection).catch(() => {});
        term.clearSelection();
      } else {
        navigator.clipboard.readText().then((text) => {
          if (text) send(tab, { type: "input", data: text });
        }).catch(() => {});
      }
    });

    tab.term = term;
    tab.fit = fit;

    term.onData((data) => send(tab, { type: "input", data }));

    const onResize = App.debounce(() => {
      if (tab.id !== activeId) return;
      try {
        fit.fit();
      } catch { /* ignore */ }
      send(tab, { type: "resize", cols: term.cols, rows: term.rows });
    }, 120);
    const observer = new ResizeObserver(onResize);
    observer.observe(container);
    tab.disposers.push(() => observer.disconnect());

    return { term, fit };
  }

  function send(tab, message) {
    if (tab.ws && tab.ws.readyState === WebSocket.OPEN) {
      tab.ws.send(JSON.stringify(message));
    }
  }

  function connectSocket(tab) {
    tab.dead = false;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${location.host}/api/terminal/ws/${encodeURIComponent(tab.id)}`);
    socket.binaryType = "arraybuffer";
    tab.ws = socket;

    socket.addEventListener("open", () => {
      updateStatus();
      send(tab, { type: "resize", cols: tab.term.cols, rows: tab.term.rows });
    });
    socket.addEventListener("message", (event) => {
      if (typeof event.data === "string") {
        try {
          const message = JSON.parse(event.data);
          if (message.type === "pong") return;
        } catch { /* plain text */ }
        tab.term.write(event.data);
      } else {
        tab.term.write(new Uint8Array(event.data));
      }
    });
    socket.addEventListener("close", () => {
      tab.dead = true;
      updateStatus();
      renderTabs();
    });
    socket.addEventListener("error", () => {
      tab.term.write("\r\n\x1b[31m[dashboard] connection error\x1b[0m\r\n");
    });
  }

  async function attachSession(session) {
    const tab = {
      id: session.id,
      title: session.title || session.shell || "shell",
      shell: session.shell || "shell",
      mode: session.mode || "",
      ws: null,
      dead: false,
      disposers: [],
      el: null,
      term: null,
      fit: null,
    };
    createTerminalElement(tab);
    tabs.push(tab);
    focusTab(tab.id);
    connectSocket(tab);
    renderTabs();
    try {
      tab.fit.fit();
    } catch { /* ignore */ }
    return tab;
  }

  Terminal.newTab = async function newTab() {
    if (!bootstrapped) return;
    const shell = $("#term-shell")?.value || undefined;
    try {
      const session = await App.api.post("/api/terminal/sessions", {
        shell,
        cols: 120,
        rows: 32,
      });
      await attachSession(session);
      App.emit("terminal-tab-created", session);
    } catch (error) {
      App.showError("Could not start a terminal", error);
    }
  };

  async function closeTab(id) {
    const index = tabs.findIndex((tab) => tab.id === id);
    if (index === -1) return;
    const tab = tabs[index];
    try {
      await App.api.del(`/api/terminal/sessions/${encodeURIComponent(id)}`);
    } catch { /* session may already be gone */ }
    try {
      tab.ws?.close();
    } catch { /* ignore */ }
    tab.disposers.forEach((dispose) => dispose());
    try {
      tab.term.dispose();
    } catch { /* ignore */ }
    tab.el.remove();
    tabs.splice(index, 1);
    if (activeId === id) {
      activeId = tabs.length ? tabs[Math.max(0, index - 1)].id : null;
      if (activeId) focusTab(activeId);
      else renderTabs();
    }
    renderTabs();
  }

  Terminal.closeTab = closeTab;
  Terminal.clear = function clear() {
    const tab = activeTab();
    if (tab) tab.term.clear();
  };
  Terminal.clearAndReset = function () {
    const tab = activeTab();
    if (tab) tab.term.reset();
  };

  Terminal.onShow = function onShow() {
    if (!bootstrapped) {
      bootstrap();
      return;
    }
    const tab = activeTab();
    if (tab) {
      setTimeout(() => {
        try {
          tab.fit.fit();
          tab.term.focus();
        } catch { /* ignore */ }
      }, 40);
    }
  };

  /* ------------------------------------------------------------------ */
  /* History                                                            */
  /* ------------------------------------------------------------------ */
  async function loadHistory() {
    const panel = $("#term-history-panel");
    const list = $("#term-history-list");
    if (!panel || !list) return;
    try {
      const data = await App.api.get("/api/terminal/history");
      list.replaceChildren();
      if (!data.history.length) {
        list.append(el("span", { class: "muted small", text: "No commands yet." }));
        return;
      }
      data.history.forEach((command) => {
        list.append(el("div", {
          class: "history-item",
          text: command,
          title: "Insert into the active terminal",
          onclick: () => {
            const tab = activeTab();
            if (!tab) {
              App.toast.warn("No active terminal", "Open a tab first");
              return;
            }
            send(tab, { type: "input", data: command });
            tab.term.focus();
          },
        }));
      });
    } catch (error) {
      list.replaceChildren(el("span", { class: "muted small", text: "History unavailable" }));
    }
  }

  /* ------------------------------------------------------------------ */
  /* Bootstrap                                                          */
  /* ------------------------------------------------------------------ */
  async function loadShells() {
    try {
      const data = await App.api.get("/api/terminal/shells");
      shells = data.shells || [];
      const select = $("#term-shell");
      if (select) {
        select.replaceChildren();
        shells.forEach((shell) => {
          select.append(el("option", { value: shell.id, text: shell.name }));
        });
        const preferred = App.state.config?.terminal?.default_shell;
        if (preferred && shells.some((shell) => shell.id === preferred)) select.value = preferred;
      }
      App.state.shellInfo = data;
    } catch (error) {
      console.warn("shell list unavailable", error);
    }
  }

  async function bootstrap() {
    if (bootstrapped) return;
    bootstrapped = true;
    host = $("#terminal-host");
    host.replaceChildren();

    await loadShells();

    // Reattach to sessions that are already running (e.g. after a reload).
    try {
      const data = await App.api.get("/api/terminal/sessions");
      const sessions = (data.sessions || []).filter((session) => session.alive !== false);
      for (const session of sessions.slice(0, 6)) {
        await attachSession(session);
      }
    } catch (error) {
      console.warn("could not list sessions", error);
    }

    if (!tabs.length) await Terminal.newTab();
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("#term-new")?.addEventListener("click", () => Terminal.newTab());
    $("#term-kill")?.addEventListener("click", () => {
      const tab = activeTab();
      if (tab) closeTab(tab.id);
    });
    $("#term-clear")?.addEventListener("click", () => Terminal.clear());
    $("#term-history")?.addEventListener("click", () => {
      const panel = $("#term-history-panel");
      if (!panel) return;
      panel.hidden = !panel.hidden;
      if (!panel.hidden) loadHistory();
    });
    $("#term-history-close")?.addEventListener("click", () => {
      $("#term-history-panel").hidden = true;
    });

    window.addEventListener("resize", () => {
      const tab = activeTab();
      if (tab && App.state.view === "terminal") {
        try { tab.fit.fit(); } catch { /* ignore */ }
      }
    });

    // Cmd/Ctrl+T inside the terminal view starts a new tab.
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === "t" && App.state.view === "terminal") {
        event.preventDefault();
        Terminal.newTab();
      }
    });
  });

  Terminal.loadShells = loadShells;
  Terminal.loadHistory = loadHistory;
})();
