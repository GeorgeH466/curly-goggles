/* ==========================================================================
   system.js — system monitoring cards, gauges, sparklines, processes,
   maintenance and power controls.
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, $$, el, fmt, icon } = App;
  const GAUGE_LENGTH = 327; // 2πr with r = 52

  const System = (App.System = {});
  let lastStats = null;
  let lastInfo = null;
  let procTimer = null;

  /* ------------------------------------------------------------------ */
  /* Gauges & sparkline                                                 */
  /* ------------------------------------------------------------------ */
  function setGauge(id, percent, caption) {
    const node = $(id);
    if (!node) return;
    const value = Math.max(0, Math.min(Number(percent) || 0, 100));
    const ring = node.querySelector(".gauge-value");
    const label = node.querySelector(".gauge-center strong");
    const cap = node.querySelector(".gauge-center span");
    if (ring) {
      ring.style.strokeDashoffset = String(GAUGE_LENGTH * (1 - value / 100));
      node.dataset.level = fmt.level(value);
    }
    if (label) label.textContent = `${value.toFixed(0)}%`;
    if (cap && caption) cap.textContent = caption;
  }

  function drawSparkline(canvas, series, history) {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || canvas.parentElement?.clientWidth || 300;
    const height = 72;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.height = `${height}px`;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    // grid
    ctx.strokeStyle = "rgba(120,160,200,.12)";
    ctx.lineWidth = 1;
    for (let i = 1; i < 4; i += 1) {
      const y = (height / 4) * i;
      ctx.beginPath();
      ctx.moveTo(0, y + 0.5);
      ctx.lineTo(width, y + 0.5);
      ctx.stroke();
    }

    const points = history.slice(-60);
    if (points.length < 2) {
      ctx.fillStyle = "rgba(160,180,205,.5)";
      ctx.font = "11px system-ui";
      ctx.fillText("collecting data…", 10, height / 2);
      return;
    }

    const step = width / Math.max(points.length - 1, 1);
    for (const item of series) {
      ctx.beginPath();
      points.forEach((point, index) => {
        const value = App.clamp(Number(point[item.key]) || 0, 0, 100);
        const x = index * step;
        const y = height - (value / 100) * (height - 6) - 3;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = item.color;
      ctx.lineWidth = 1.8;
      ctx.lineJoin = "round";
      ctx.stroke();

      // subtle fill
      const gradient = ctx.createLinearGradient(0, 0, 0, height);
      gradient.addColorStop(0, item.fill);
      gradient.addColorStop(1, "rgba(0,0,0,0)");
      ctx.lineTo(width, height);
      ctx.lineTo(0, height);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();
    }
  }

  function renderSystem(stats) {
    const cpu = stats.cpu || {};
    const memory = stats.memory || {};
    const disks = stats.disks || [];
    const primary = disks.reduce((best, disk) => (!best || disk.total > best.total ? disk : best), null);

    setGauge("#gauge-cpu", cpu.percent, "CPU");
    setGauge("#gauge-mem", memory.percent, "RAM");
    setGauge("#gauge-disk", primary ? primary.percent : 0, "Disk");

    const staticInfo = (App.state.info && App.state.info.static) || stats.static || {};
    const os = staticInfo.os || {};
    const network = stats.network || {};

    setText("#fact-uptime", fmt.uptime(stats.uptime));
    setText("#fact-processes", stats.process_count != null ? `${stats.process_count}` : "—");
    setText("#fact-host", staticInfo.hostname || "—");
    setText("#fact-os", os.display || os.system || "—");
    setText("#fact-cpu", cpu.freq_mhz ? `${(cpu.freq_mhz / 1000).toFixed(2)} GHz` : (staticInfo.cpu?.model || "—"));
    setText("#fact-ip", network.primary_ip || "—");

    // Per-core bars in the card footer.
    const cores = $("#system-cores");
    if (cores && cpu.per_core) {
      const perCore = cpu.per_core;
      if (cores.childElementCount !== perCore.length) {
        cores.replaceChildren();
        perCore.forEach((_, index) => {
          cores.append(el("div", { class: "core-bar", title: `Core ${index}` }));
        });
      }
      perCore.forEach((value, index) => {
        const bar = cores.children[index];
        if (!bar) return;
        bar.style.height = `${Math.max(4, value)}%`;
        bar.dataset.level = fmt.level(value);
        bar.title = `Core ${index} · ${value.toFixed(0)}%`;
      });
    }

    const history = stats.history || App.state.history || [];
    drawSparkline($("#spark-main"), [
      { key: "cpu", color: "#22d3ee", fill: "rgba(34,211,238,.28)" },
      { key: "mem", color: "#a78bfa", fill: "rgba(167,139,250,.22)" },
    ], history);

    const legend = $("#spark-legend");
    if (legend) {
      legend.innerHTML = `<span><i style="background:#22d3ee"></i>CPU</span><span><i style="background:#a78bfa"></i>RAM</span>`;
    }
  }

  function setText(selector, value) {
    const node = $(selector);
    if (node) node.textContent = value == null ? "—" : String(value);
  }

  /* ------------------------------------------------------------------ */
  /* Hardware card                                                      */
  /* ------------------------------------------------------------------ */
  function renderHardware(stats) {
    const body = $("#hardware-body");
    if (!body) return;
    const gpus = stats.gpus || [];
    const temps = stats.temperatures || {};
    const battery = stats.battery;
    const fragments = [];

    if (!gpus.length) {
      fragments.push(el("div", { class: "empty-state small", style: "padding:14px 4px" }, [
        el("span", { html: icon("gpu") }),
        el("p", { text: "No GPU detected" }),
      ]));
    }

    gpus.forEach((gpu) => {
      const hasUtil = gpu.utilization != null;
      fragments.push(el("div", { class: "gpu-block" }, [
        el("div", { class: "gpu-head" }, [
          el("strong", { text: gpu.name }),
          gpu.temperature != null ? el("span", { class: "badge", text: `${Math.round(gpu.temperature)}°C` }) : null,
        ]),
        el("div", { class: "gpu-stats" }, [
          hasUtil ? el("span", { html: `Load <b>${gpu.utilization}%</b>` }) : el("span", { class: "muted", text: gpu.vendor || "" }),
          gpu.memory_total
            ? el("span", { html: `VRAM <b>${fmt.bytes(gpu.memory_used || 0)} / ${fmt.bytes(gpu.memory_total)}</b>` })
            : null,
          gpu.driver ? el("span", { class: "muted", text: `driver ${gpu.driver}` }) : null,
        ]),
        hasUtil ? el("div", { class: "progress mt-8" }, [
          el("i", { style: `width:${App.clamp(gpu.utilization, 0, 100)}%` }),
        ]) : null,
      ]));
    });

    if (temps.other && temps.other.length) {
      const hottest = temps.other.slice().sort((a, b) => b.current - a.current)[0];
      fragments.push(el("div", { class: "hw-row" }, [
        el("span", { class: "label", text: "CPU temperature" }),
        el("span", { class: "value", text: temps.cpu != null ? `${temps.cpu} °C` : hottest ? `${hottest.label}: ${hottest.current} °C` : "n/a" }),
      ]));
    } else {
      fragments.push(el("div", { class: "hw-row" }, [
        el("span", { class: "label", text: "CPU temperature" }),
        el("span", { class: "value", text: temps.cpu != null ? `${temps.cpu} °C` : "not exposed" }),
      ]));
    }

    if (battery) {
      const level = fmt.level(100 - battery.percent);
      fragments.push(el("div", { class: "hw-row" }, [
        el("span", { class: "label" }, [
          el("span", { text: battery.plugged ? "On AC power" : "On battery" }),
        ]),
        el("span", { class: "value" }, [
          el("span", { html: `${Math.round(battery.percent)}%` }),
          battery.seconds_left ? el("span", { class: "muted small", text: ` · ${fmt.duration(battery.seconds_left)}` }) : null,
        ]),
      ]));
      fragments.push(el("div", { class: "progress", "data-level": battery.percent < 15 ? "crit" : level }, [
        el("i", { style: `width:${battery.percent}%` }),
      ]));
    }

    const swap = stats.swap || {};
    if (swap.total) {
      fragments.push(el("div", { class: "hw-row" }, [
        el("span", { class: "label", text: "Swap / page file" }),
        el("span", { class: "value", text: `${fmt.bytes(swap.used)} / ${fmt.bytes(swap.total)}` }),
      ]));
    }

    body.replaceChildren(...fragments);
  }

  /* ------------------------------------------------------------------ */
  /* Storage card                                                       */
  /* ------------------------------------------------------------------ */
  function renderStorage(stats) {
    const body = $("#storage-body");
    if (!body) return;
    const disks = stats.disks || [];
    if (!disks.length) {
      body.replaceChildren(el("p", { class: "muted small", text: "No disks detected" }));
      return;
    }
    const rows = disks.map((disk) =>
      el("div", { class: "progress-row" }, [
        el("div", { class: "progress-top" }, [
          el("strong", { text: `${disk.mount} ${disk.fstype ? `· ${disk.fstype}` : ""}` }),
          el("span", { text: `${fmt.bytes(disk.free)} free of ${fmt.bytes(disk.total)}` }),
        ]),
        el("div", { class: "progress", "data-level": fmt.level(disk.percent) }, [
          el("i", { style: `width:${disk.percent}%` }),
        ]),
      ]));
    const io = stats.disk_io || {};
    rows.push(el("div", { class: "row between mt-8" }, [
      el("span", { class: "muted small", text: "Disk activity" }),
      el("span", { class: "small", html: `${icon("download")} ${fmt.speed(io.read_bps)} &nbsp; ${icon("upload")} ${fmt.speed(io.write_bps)}` }),
    ]));
    body.replaceChildren(...rows);
  }

  /* ------------------------------------------------------------------ */
  /* System view                                                        */
  /* ------------------------------------------------------------------ */
  function renderDetails(info) {
    const list = $("#sys-details");
    if (!list) return;
    const staticInfo = (info && info.static) || {};
    const os = staticInfo.os || {};
    const cpu = staticInfo.cpu || {};
    const features = (info && info.features) || {};
    const gpus = (info && info.gpus) || [];
    const rows = [
      ["Hostname", staticInfo.hostname],
      ["User", staticInfo.user],
      ["Operating system", os.display || os.system],
      ["Version", os.version],
      ["Architecture", `${os.machine || "—"} · ${os.arch || ""}`],
      ["Processor", cpu.model],
      ["Cores", cpu.physical_cores ? `${cpu.physical_cores} physical · ${cpu.logical_cores} logical` : null],
      ["GPU", gpus.length ? gpus.map((gpu) => gpu.name).join(", ") : "not detected"],
      ["Python", staticInfo.python],
      ["Dashboard version", info && info.version],
      ["Host name (FQDN)", (App.state.network && App.state.network.hostname_fqdn) || null],
    ];
    list.replaceChildren(...rows.filter(([, value]) => value).map(([label, value]) =>
      el("div", {}, [
        el("dt", { text: label }),
        el("dd", { text: value }),
      ])));
    const badge = $("#sys-admin-badge");
    if (badge) {
      const admin = App.state.meta && App.state.meta.admin;
      badge.textContent = admin ? "administrator" : "standard user";
      badge.className = `badge ${admin ? "" : "badge-muted"}`;
    }
    const adminFeature = features;
    if (adminFeature && adminFeature.battery === false) {
      // Battery isn't shown anywhere in this view; nothing to do.
    }
  }

  function renderCores(stats) {
    const grid = $("#core-grid");
    if (!grid) return;
    const perCore = (stats.cpu && stats.cpu.per_core) || [];
    if (!perCore.length) return;
    if (grid.childElementCount !== perCore.length) {
      grid.replaceChildren();
      perCore.forEach((_, index) => {
        grid.append(el("div", { class: "core-cell" }, [
          el("div", { class: "core-track" }, [el("i")]),
          el("small", { text: `CPU ${index}` }),
        ]));
      });
    }
    perCore.forEach((value, index) => {
      const cell = grid.children[index];
      const bar = cell && cell.querySelector(".core-track i");
      if (!bar) return;
      bar.style.height = `${Math.max(2, value)}%`;
      bar.dataset.level = fmt.level(value);
      cell.title = `${value.toFixed(0)}%`;
    });
    setText("#load-avg", (stats.cpu.load_avg || []).map((v) => v.toFixed(2)).join(" · ") || "—");
    setText("#cpu-freq", stats.cpu.freq_mhz ? `${(stats.cpu.freq_mhz / 1000).toFixed(2)} GHz` : "—");
  }

  function renderDisks(stats) {
    const body = $("#sys-disks");
    if (!body) return;
    body.replaceChildren(...(stats.disks || []).map((disk) =>
      el("div", { class: "progress-row" }, [
        el("div", { class: "progress-top" }, [
          el("strong", { text: `${disk.mount} · ${disk.device}` }),
          el("span", { text: `${disk.percent}% · ${fmt.bytes(disk.free)} free` }),
        ]),
        el("div", { class: "progress", "data-level": fmt.level(disk.percent) }, [
          el("i", { style: `width:${disk.percent}%` }),
        ]),
      ])));
  }

  /* ------------------------------------------------------------------ */
  /* Processes                                                          */
  /* ------------------------------------------------------------------ */
  async function loadProcesses() {
    const tbody = $("#proc-rows");
    if (!tbody) return;
    const sort = $("#proc-sort")?.value || "cpu";
    try {
      const data = await App.api.get(App.api.withQuery("/api/system/processes", { sort, limit: 25 }));
      tbody.replaceChildren(...data.processes.map((proc) =>
        el("tr", {}, [
          el("td", {}, [el("div", { class: "file-name" }, [
            el("span", { html: icon("app") }),
            el("span", { text: proc.name }),
          ])]),
          el("td", { class: "num", text: proc.pid }),
          el("td", { class: "num", text: `${proc.cpu.toFixed(1)}%` }),
          el("td", { class: "num", text: `${proc.memory.toFixed(1)}%` }),
          el("td", { class: "muted small", text: proc.status }),
        ])));
    } catch (error) {
      console.warn("process list failed", error);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Controls                                                           */
  /* ------------------------------------------------------------------ */
  const POWER_META = {
    lock: { title: "Lock this PC?", message: "The workstation will lock immediately. You'll need your password or PIN to return.", confirm: "Lock now", danger: false },
    sleep: { title: "Put this PC to sleep?", message: "The PC will suspend. Some downloads and background jobs will pause.", confirm: "Sleep now", danger: false },
    restart: { title: "Restart this PC?", message: "Windows will restart in 30 seconds. Save your work — you can still abort from the dashboard or with `shutdown /a`.", confirm: "Restart", danger: true },
    shutdown: { title: "Shut down this PC?", message: "Windows will shut down in 30 seconds. Save your work — this dashboard will disappear.", confirm: "Shut down", danger: true },
  };

  async function confirmAndCountdown(options) {
    return App.confirm({
      title: options.title,
      message: options.message,
      confirmText: options.confirm,
      danger: options.danger,
      subtitle: "This action is logged",
    });
  }

  System.power = async function power(action) {
    const meta = POWER_META[action];
    if (!meta) return;
    const ok = await confirmAndCountdown(meta);
    if (!ok) return;
    try {
      const result = await App.api.post("/api/controls/action", { action, confirm: true, delay: 30 });
      App.toast.success("Request sent", result.message || `Action "${action}" requested`);
      refreshControlStatus();
    } catch (error) {
      App.showError(`Could not ${action} the PC`, error);
    }
  };

  System.maintenance = async function maintenance(action) {
    const meta = {
      "flush-dns": {
        title: "Flush the DNS cache?",
        message: "Clears the resolver cache. Useful when websites resolve to stale addresses.",
        success: "DNS cache flushed",
      },
      "clear-temp": {
        title: "Clear temporary files?",
        message: "Deletes files in your temp folders that are older than 24 hours. Files in use are skipped.",
        success: "Temporary files cleared",
        danger: true,
      },
      "empty-recycle-bin": {
        title: "Empty the Recycle Bin?",
        message: "Everything in the Recycle Bin will be permanently deleted. This cannot be undone.",
        success: "Recycle Bin emptied",
        danger: true,
      },
      "restart-explorer": {
        title: "Restart Windows Explorer?",
        message: "The taskbar and desktop will briefly disappear while Explorer restarts.",
        success: "Explorer restarted",
      },
    }[action];
    if (!meta) return;
    const ok = await App.confirm({
      title: meta.title,
      message: meta.message,
      confirmText: "Continue",
      danger: meta.danger,
    });
    if (!ok) return;
    try {
      const result = await App.api.post(App.api.withQuery(`/api/controls/${action}`, { confirm: true }), {});
      App.toast.success(meta.success, result.message || "");
      refreshControlStatus();
    } catch (error) {
      App.showError("Action failed", error);
    }
  };

  async function refreshControlStatus() {
    try {
      const status = await App.api.get("/api/controls/status");
      App.state.controls = status;
      const list = $("#action-log");
      if (list) {
        list.replaceChildren(...(status.actions || []).slice(0, 8).map((entry) =>
          el("li", {}, [
            el("span", { text: `${entry.action}${entry.ok === false ? " (failed)" : ""}` }),
            el("time", { text: entry.at ? entry.at.split(" ")[1] || entry.at : "" }),
          ])));
        if (!status.actions || !status.actions.length) {
          list.append(el("li", { class: "muted", text: "No actions yet" }));
        }
      }
      const pending = $("#pending-power");
      if (pending) {
        pending.hidden = !status.pending_shutdown;
        if (status.pending) {
          setText("#pending-text", `${status.pending.action === "restart" ? "Restart" : "Shutdown"} scheduled`);
        }
      }
      $$("[data-maintenance]").forEach((button) => {
        const feature = { "flush-dns": "flush_dns", "clear-temp": "clear_temp", "empty-recycle-bin": "recycle_bin", "restart-explorer": "restart_explorer" }[button.dataset.maintenance];
        if (feature && status.features && status.features[feature] === false) {
          button.disabled = true;
          button.title = "Not available on this system";
        }
      });
    } catch (error) {
      console.warn("control status failed", error);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                             */
  /* ------------------------------------------------------------------ */
  App.on("stats", (stats) => {
    lastStats = stats;
    renderSystem(stats);
    renderHardware(stats);
    renderStorage(stats);
    renderCores(stats);
    renderDisks(stats);
  });

  App.on("info", (info) => {
    lastInfo = info;
    renderDetails(info);
    renderHardware(lastStats || { gpus: info.gpus || [], temperatures: info.temperatures || {} });
  });

  App.on("view", (view) => {
    if (view === "system") {
      loadProcesses();
      refreshControlStatus();
      clearInterval(procTimer);
      procTimer = setInterval(() => {
        if (App.state.view === "system") loadProcesses();
      }, 8000);
    } else {
      clearInterval(procTimer);
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    // Quick controls on the overview card.
    const controls = $("#controls-body");
    if (controls) {
      controls.replaceChildren(
        el("div", { class: "button-grid" }, [
          el("button", { class: "btn ghost", onclick: () => System.power("lock") }, [spanIcon("lock"), "Lock"]),
          el("button", { class: "btn ghost", onclick: () => System.power("sleep") }, [spanIcon("moon"), "Sleep"]),
          el("button", { class: "btn ghost", onclick: () => System.maintenance("flush-dns") }, [spanIcon("wifi"), "Flush DNS"]),
          el("button", { class: "btn ghost", onclick: () => System.maintenance("clear-temp") }, [spanIcon("trash"), "Clear temp"]),
          el("button", { class: "btn ghost", onclick: () => System.maintenance("empty-recycle-bin") }, [spanIcon("recycle"), "Recycle Bin"]),
          el("button", { class: "btn danger", onclick: () => System.power("shutdown") }, [spanIcon("power"), "Shut down"]),
        ]),
      );
    }

    $$("[data-power]").forEach((button) => {
      button.addEventListener("click", () => System.power(button.dataset.power));
    });
    $$("[data-maintenance]").forEach((button) => {
      button.addEventListener("click", () => System.maintenance(button.dataset.maintenance));
    });
    $("#btn-abort-power")?.addEventListener("click", async () => {
      try {
        const result = await App.api.post("/api/controls/action", { action: "abort" });
        App.toast.success("Cancelled", result.message);
        refreshControlStatus();
      } catch (error) {
        App.showError("Could not abort", error);
      }
    });
    $("#proc-refresh")?.addEventListener("click", loadProcesses);
    $("#proc-sort")?.addEventListener("change", loadProcesses);

    setTimeout(refreshControlStatus, 900);
    setInterval(() => {
      if (App.state.view === "system") refreshControlStatus();
    }, 15000);
  });

  function spanIcon(name) {
    return el("span", { html: icon(name), style: "display:inline-flex" });
  }

  System.refreshPanels = function refreshPanels() {
    if (lastStats) {
      renderCores(lastStats);
      renderDisks(lastStats);
    }
    loadProcesses();
    refreshControlStatus();
  };

  System.refreshControlStatus = refreshControlStatus;
})();
