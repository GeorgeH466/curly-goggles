/* ==========================================================================
   api.js — tiny helper library shared by every panel
   Exposes the global `App` object: HTTP helpers, toasts, modals, formatting,
   an event bus and DOM utilities.
   ========================================================================== */
(function () {
  "use strict";

  const App = (window.App = window.App || {});

  /* ------------------------------- DOM ---------------------------------- */
  App.$ = (selector, root = document) => root.querySelector(selector);
  App.$$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  App.el = function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "html") node.innerHTML = value;
      else if (key === "text") node.textContent = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of [].concat(children)) {
      if (child === null || child === undefined) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  };

  App.icon = (name) => `<svg aria-hidden="true"><use href="#icon-${name}"></use></svg>`;

  App.escape = function escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  };

  /* ------------------------------- HTTP --------------------------------- */
  class ApiError extends Error {
    constructor(message, status, detail) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.detail = detail;
    }
  }
  App.ApiError = ApiError;

  async function request(method, url, body, options = {}) {
    const init = {
      method,
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    if (options.signal) init.signal = options.signal;

    let response;
    try {
      response = await fetch(url, init);
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new ApiError("Cannot reach the dashboard server", 0, String(error));
    }
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
    if (!response.ok) {
      const detail = (data && (data.detail || data.message)) || response.statusText || "Request failed";
      throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), response.status, data);
    }
    return data;
  }

  App.api = {
    get: (url, options) => request("GET", url, undefined, options),
    post: (url, body, options) => request("POST", url, body ?? {}, options),
    put: (url, body, options) => request("PUT", url, body ?? {}, options),
    patch: (url, body, options) => request("PATCH", url, body ?? {}, options),
    del: (url, body, options) => request("DELETE", url, body, options),
    request,
    withQuery(url, params = {}) {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) query.set(key, value);
      }
      const qs = query.toString();
      return qs ? `${url}?${qs}` : url;
    },
  };

  /* ------------------------------ event bus ----------------------------- */
  const listeners = new Map();
  App.on = function on(event, handler) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(handler);
    return () => listeners.get(event).delete(handler);
  };
  App.emit = function emit(event, payload) {
    for (const handler of listeners.get(event) || []) {
      try {
        handler(payload);
      } catch (error) {
        console.error(`[App] listener for ${event} failed`, error);
      }
    }
  };

  App.state = {
    stats: null,
    history: [],
    info: null,
    features: {},
    config: null,
    meta: null,
    connected: false,
    view: "overview",
  };

  /* ------------------------------ formatting ---------------------------- */
  App.fmt = {
    bytes(value, digits = 1) {
      const n = Number(value) || 0;
      if (n < 1) return "0 B";
      const units = ["B", "KB", "MB", "GB", "TB", "PB"];
      const index = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
      const scaled = n / 1024 ** index;
      return `${scaled.toFixed(index === 0 ? 0 : scaled >= 100 ? 0 : digits)} ${units[index]}`;
    },
    speed(bytesPerSecond) {
      const value = Number(bytesPerSecond) || 0;
      if (value < 1) return "0 B/s";
      return `${App.fmt.bytes(value)}/s`;
    },
    percent(value, digits = 0) {
      return `${(Number(value) || 0).toFixed(digits)}%`;
    },
    uptime(seconds) {
      let total = Math.max(0, Math.floor(Number(seconds) || 0));
      const days = Math.floor(total / 86400);
      total %= 86400;
      const hours = Math.floor(total / 3600);
      total %= 3600;
      const minutes = Math.floor(total / 60);
      const parts = [];
      if (days) parts.push(`${days}d`);
      if (days || hours) parts.push(`${hours}h`);
      parts.push(`${minutes}m`);
      return parts.join(" ");
    },
    duration(seconds) {
      const value = Math.max(0, Number(seconds) || 0);
      const hours = Math.floor(value / 3600);
      const minutes = Math.floor((value % 3600) / 60);
      if (hours) return `${hours}h ${minutes}m left`;
      return `${minutes}m left`;
    },
    date(value, withTime = true) {
      if (!value) return "—";
      const date = typeof value === "number" ? new Date(value * (value > 1e12 ? 1 : 1000)) : new Date(value);
      if (Number.isNaN(date.getTime())) return "—";
      return date.toLocaleString(undefined, withTime
        ? { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }
        : { year: "numeric", month: "short", day: "2-digit" });
    },
    ago(value) {
      if (!value) return "never";
      const ts = typeof value === "number" ? value * 1000 : new Date(value).getTime();
      const diff = Date.now() - ts;
      if (Number.isNaN(diff)) return "never";
      const seconds = Math.round(diff / 1000);
      if (seconds < 45) return "just now";
      const minutes = Math.round(seconds / 60);
      if (minutes < 60) return `${minutes}m ago`;
      const hours = Math.round(minutes / 60);
      if (hours < 24) return `${hours}h ago`;
      const days = Math.round(hours / 24);
      if (days < 30) return `${days}d ago`;
      return App.fmt.date(value, false);
    },
    shortPath(path, max = 46) {
      const text = String(path || "");
      if (text.length <= max) return text;
      const parts = text.split(/[\\/]/);
      const tail = parts.slice(-2).join("/");
      return tail.length > max ? `…${text.slice(-max)}` : `…/${tail}`;
    },
    level(percent) {
      const value = Number(percent) || 0;
      if (value >= 90) return "crit";
      if (value >= 70) return "warn";
      return "ok";
    },
  };

  /* ------------------------------- toasts ------------------------------- */
  const toastRoot = () => document.getElementById("toasts");

  App.toast = function toast(type, title, message = "", timeout = 4200) {
    const root = toastRoot();
    if (!root) return () => {};
    const iconName = { success: "check", error: "alert", warn: "alert", info: "info" }[type] || "info";
    const node = App.el("div", { class: `toast ${type}`, role: "status" }, [
      App.el("div", { html: App.icon(iconName) }),
      App.el("div", { class: "toast-body" }, [
        App.el("strong", { text: title }),
        message ? App.el("p", { text: message }) : null,
      ]),
    ]);
    root.append(node);

    let removed = false;
    const remove = () => {
      if (removed) return;
      removed = true;
      node.classList.add("is-leaving");
      setTimeout(() => node.remove(), 220);
    };
    node.addEventListener("click", remove);
    const timer = setTimeout(remove, timeout);
    node.addEventListener("mouseenter", () => clearTimeout(timer));
    return remove;
  };

  App.toast.error = (title, message) => App.toast("error", title, message, 6500);
  App.toast.success = (title, message) => App.toast("success", title, message);
  App.toast.warn = (title, message) => App.toast("warn", title, message, 5500);

  /** Show an error from a caught exception (ApiError aware). */
  App.showError = function showError(title, error) {
    const message = error && error.message ? error.message : String(error || "Unknown error");
    console.error(title, error);
    App.toast.error(title, message);
  };

  /* ------------------------------- modals ------------------------------- */
  let openModals = 0;

  /**
   * Open a modal dialog.
   * @param {{title:string, subtitle?:string, body:Node|string, footer?:Node[],
   *          wide?:boolean|string, onClose?:Function}} options
   */
  App.modal = function modal(options) {
    const root = document.getElementById("modal-root");
    const backdrop = App.el("div", { class: "modal-backdrop" });
    const wideClass = options.wide === true ? " wide" : options.wide === "xl" ? " xwide" : options.wide || "";
    const dialog = App.el("div", { class: `modal${wideClass}`, role: "dialog", "aria-modal": "true" });

    const close = (result) => {
      if (!backdrop.isConnected) return;
      backdrop.remove();
      openModals = Math.max(0, openModals - 1);
      document.removeEventListener("keydown", onKey);
      if (options.onClose) options.onClose(result);
    };

    const head = App.el("div", { class: "modal-head" }, [
      App.el("div", {}, [
        App.el("h2", { text: options.title || "" }),
        options.subtitle ? App.el("div", { class: "sub", text: options.subtitle }) : null,
      ]),
      App.el("button", { class: "icon-btn", title: "Close", onclick: () => close(null) },
        [App.el("span", { html: App.icon("x") })]),
    ]);

    const body = App.el("div", { class: "modal-body" });
    if (typeof options.body === "string") body.innerHTML = options.body;
    else if (options.body) body.append(options.body);

    dialog.append(head, body);
    if (options.footer && options.footer.length) {
      const foot = App.el("div", { class: "modal-foot" });
      options.footer.forEach((item) => foot.append(item));
      dialog.append(foot);
    }
    backdrop.append(dialog);

    const onKey = (event) => {
      if (event.key === "Escape") close(null);
    };
    document.addEventListener("keydown", onKey);
    backdrop.addEventListener("mousedown", (event) => {
      if (event.target === backdrop) close(null);
    });

    root.append(backdrop);
    openModals += 1;
    const firstInput = body.querySelector("input, select, textarea, button");
    if (firstInput) setTimeout(() => firstInput.focus(), 40);
    return { close, body, dialog, backdrop };
  };
  App.modalCount = () => openModals;

  /** Promise-based confirmation dialog. */
  App.confirm = function confirm(options) {
    return new Promise((resolve) => {
      const confirmLabel = options.confirmText || "Confirm";
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        resolve(value);
      };
      const body = App.el("div", { class: "stack" }, [
        App.el("p", { text: options.message || "" }),
        options.detail ? App.el("p", { class: "muted small", text: options.detail }) : null,
        options.extra || null,
      ]);
      const cancelBtn = App.el("button", { class: "btn ghost", text: "Cancel", onclick: () => { finish(false); modal.close(); } });
      const okBtn = App.el("button", {
        class: `btn ${options.danger ? "danger" : ""}`,
        text: confirmLabel,
        onclick: () => { finish(true); modal.close(); },
      });
      const modal = App.modal({
        title: options.title || "Are you sure?",
        subtitle: options.subtitle,
        body,
        footer: [cancelBtn, okBtn],
        onClose: () => finish(false),
      });
      setTimeout(() => okBtn.focus(), 60);
    });
  };

  /** Prompt for a single line of text. Resolves to the string or null. */
  App.prompt = function prompt(options) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        resolve(value);
      };
      const input = App.el("input", {
        class: "input",
        value: options.value || "",
        placeholder: options.placeholder || "",
        spellcheck: "false",
      });
      const submit = () => {
        const value = input.value.trim();
        if (!value) {
          input.focus();
          return;
        }
        finish(value);
        modal.close();
      };
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") submit();
      });
      const modal = App.modal({
        title: options.title || "Enter a value",
        subtitle: options.subtitle,
        body: App.el("div", { class: "stack" }, [
          options.message ? App.el("p", { class: "muted small", text: options.message }) : null,
          input,
        ]),
        footer: [
          App.el("button", { class: "btn ghost", text: "Cancel", onclick: () => { finish(null); modal.close(); } }),
          App.el("button", { class: `btn ${options.danger ? "danger" : ""}`, text: options.confirmText || "Save", onclick: submit }),
        ],
        onClose: () => finish(null),
      });
      setTimeout(() => { input.focus(); input.select(); }, 50);
    });
  };

  /* --------------------------- misc helpers ----------------------------- */
  App.copy = async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      App.toast.success("Copied", "Copied to the clipboard");
    } catch {
      App.toast.error("Copy failed", "Your browser blocked clipboard access");
    }
  };

  App.debounce = function debounce(fn, wait = 300) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  };

  App.clamp = (value, min, max) => Math.min(Math.max(value, min), max);

  App.iconForFile = function iconForFile(entry) {
    if (entry.is_dir) return "folder";
    const ext = (entry.ext || "").toLowerCase();
    if (["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"].includes(ext)) return "camera";
    if (["mp4", "mkv", "avi", "mov", "webm"].includes(ext)) return "video";
    if (["mp3", "wav", "flac", "ogg", "m4a"].includes(ext)) return "music";
    if (["zip", "7z", "rar", "tar", "gz", "iso"].includes(ext)) return "box";
    if (["js", "ts", "py", "java", "cs", "cpp", "c", "h", "json", "yml", "yaml", "html", "css", "sh", "bat", "ps1"].includes(ext)) return "code";
    return "file";
  };
})();
