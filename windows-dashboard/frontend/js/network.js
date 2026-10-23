/* ==========================================================================
   network.js — network card, adapters, ping, ports and speed test
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, el, fmt, icon } = App;

  const Network = (App.Network = {});
  let info = null;
  let portsLoaded = 0;
  let speedTesting = false;

  /* ------------------------------------------------------------------ */
  /* Overview card                                                      */
  /* ------------------------------------------------------------------ */
  function renderCard(stats) {
    const body = $("#network-body");
    if (!body) return;
    const net = stats.network || {};
    const wifi = net.wifi;
    const fragments = [];

    if (wifi) {
      const signal = Number(wifi.signal);
      const bars = el("div", { class: "signal-bars" },
        [1, 2, 3, 4].map((index) => el("i", {
          class: Number.isFinite(signal) && signal >= index * 22 ? "on" : "",
        })));
      fragments.push(el("div", { class: "wifi-row" }, [
        el("span", { html: icon("wifi") }),
        el("div", { class: "grow" }, [
          el("strong", { text: wifi.ssid || "Wi-Fi" }),
          el("small", { text: [wifi.radio, Number.isFinite(signal) ? `${signal}% signal` : null].filter(Boolean).join(" · ") || "connected" }),
        ]),
        bars,
      ]));
    } else if (net.primary_ip) {
      fragments.push(el("div", { class: "wifi-row" }, [
        el("span", { html: icon("globe") }),
        el("div", { class: "grow" }, [
          el("strong", { text: "Wired / internal network" }),
          el("small", { text: net.primary_ip }),
        ]),
      ]));
    }

    fragments.push(el("div", { class: "net-headline" }, [
      el("div", { class: "net-speed" }, [
        el("div", { class: "label" }, [el("span", { html: icon("download") }), "Download"]),
        el("strong", { text: fmt.speed(net.download_bps) }),
        el("small", { text: `${fmt.bytes(net.total_received)} total` }),
      ]),
      el("div", { class: "net-speed" }, [
        el("div", { class: "label" }, [el("span", { html: icon("upload") }), "Upload"]),
        el("strong", { text: fmt.speed(net.upload_bps) }),
        el("small", { text: `${fmt.bytes(net.total_sent)} total` }),
      ]),
    ]));

    fragments.push(el("dl", { class: "kv" }, [
      el("dt", { text: "Local IP" }), el("dd", { text: net.primary_ip || "—" }),
      el("dt", { text: "External IP" }), el("dd", { text: (info && info.external_ip) || "—" }),
      el("dt", { text: "Gateway" }), el("dd", { text: (info && info.gateway) || "—" }),
    ]));

    body.replaceChildren(...fragments);
  }

  /* ------------------------------------------------------------------ */
  /* Network view                                                       */
  /* ------------------------------------------------------------------ */
  function adapterRow(iface) {
    return el("div", { class: "iface" }, [
      el("div", { class: "iface-head" }, [
        el("span", { class: `status-dot`, dataset: { state: iface.up ? "online" : "offline" } }),
        el("strong", { text: iface.name }),
        el("span", { class: "ip", text: (iface.ipv4 && iface.ipv4[0]) || (iface.ipv6 && iface.ipv6[0]) || "no address" }),
      ]),
      el("small", {
        text: [
          iface.mac ? `MAC ${iface.mac}` : null,
          iface.speed_mbps ? `${iface.speed_mbps} Mbps` : null,
          iface.up ? "up" : "down",
          `${fmt.bytes(iface.bytes_recv)} in / ${fmt.bytes(iface.bytes_sent)} out`,
        ].filter(Boolean).join(" · "),
      }),
    ]);
  }

  function renderAdapters() {
    const body = $("#network-adapters");
    if (!body || !info) return;
    const hostname = $("#net-hostname");
    if (hostname) hostname.textContent = info.hostname_fqdn || info.hostname || "";
    body.replaceChildren(...(info.interfaces || []).map(adapterRow));
    if (!info.interfaces || !info.interfaces.length) {
      body.replaceChildren(el("p", { class: "muted small", text: "No network interfaces found" }));
    }
  }

  function renderConnectivity() {
    const body = $("#network-connectivity");
    if (!body || !info) return;
    const wifi = info.wifi;
    body.replaceChildren(
      el("dl", { class: "kv wide" }, [
        el("dt", { text: "Wi-Fi" }),
        el("dd", { text: wifi ? `${wifi.ssid}${wifi.signal != null ? ` · ${wifi.signal}%` : ""}` : "not connected" }),
        el("dt", { text: "Internal IP" }),
        el("dd", { text: (info.interfaces.find((i) => i.up && i.ipv4.length) || {}).ipv4?.[0] || "—" }),
        el("dt", { text: "External IP" }),
        el("dd", { text: info.external_ip || info.external_error || "—" }),
        el("dt", { text: "Default gateway" }),
        el("dd", { text: info.gateway || "—" }),
        el("dt", { text: "DNS servers" }),
        el("dd", { text: (info.dns || []).join(", ") || "—" }),
        el("dt", { text: "Hostname" }),
        el("dd", { text: info.hostname_fqdn || info.hostname }),
      ]),
    );
  }

  function renderPorts(data) {
    const tbody = $("#ports-rows");
    const count = $("#ports-count");
    const note = $("#ports-note");
    if (!tbody) return;
    tbody.replaceChildren();
    const rows = data.ports || [];
    rows.slice(0, 80).forEach((port) => {
      tbody.append(el("tr", {}, [
        el("td", { class: "mono", text: port.port }),
        el("td", { class: "muted small", text: `${port.proto} · ${port.family}` }),
        el("td", { class: "mono small", text: port.ip }),
        el("td", { text: port.process || "—" }),
        el("td", { class: "num", text: port.pid || "—" }),
      ]));
    });
    if (count) count.textContent = `${data.count} listening`;
    if (note) note.textContent = (data.warnings || []).join(" ") || `Showing ${Math.min(rows.length, 80)} of ${rows.length} sockets`;
    portsLoaded = Date.now();
  }

  async function loadPorts() {
    try {
      const data = await App.api.get("/api/network/ports");
      renderPorts(data);
    } catch (error) {
      App.showError("Could not list ports", error);
    }
  }

  async function loadInfo(forceExternal = false) {
    try {
      info = await App.api.get("/api/network/info");
      App.state.network = info;
      renderAdapters();
      renderConnectivity();
      if (App.state.stats) renderCard(App.state.stats);
      if (forceExternal || !info.external_ip) {
        try {
          const external = await App.api.get(App.api.withQuery("/api/network/external-ip", { force: forceExternal ? "true" : "false" }));
          if (external.ip) {
            info.external_ip = external.ip;
            App.state.network = info;
            renderConnectivity();
            if (App.state.stats) renderCard(App.state.stats);
          }
        } catch { /* offline */ }
      }
    } catch (error) {
      App.showError("Could not load network info", error);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Ping                                                               */
  /* ------------------------------------------------------------------ */
  async function runPing() {
    const custom = ($("#ping-custom")?.value || "").trim();
    const target = custom || $("#ping-target")?.value || "8.8.8.8";
    const results = $("#ping-results");
    if (results) {
      results.replaceChildren(el("div", { class: "row" }, [
        el("span", { class: "spin", html: icon("refresh") }),
        el("span", { text: `Pinging ${target}…` }),
      ]));
    }
    try {
      const data = await App.api.post("/api/network/ping", { host: target, count: 4 });
      renderPing(data);
    } catch (error) {
      if (results) results.replaceChildren(el("p", { class: "muted small", text: error.message }));
    }
  }

  function renderPing(data) {
    const results = $("#ping-results");
    if (!results) return;
    if (!data.ok) {
      results.replaceChildren(el("div", { class: "stack" }, [
        el("p", { class: "badge badge-danger", text: "No reply" }),
        el("pre", { class: "preview-pre", text: (data.raw || "").slice(0, 800) || "Host unreachable" }),
      ]));
      return;
    }
    const max = Math.max(...data.samples, 1);
    results.replaceChildren(
      el("div", { class: "ping-bars" },
        data.samples.map((sample) => el("i", {
          style: `height:${Math.max(6, (sample / max) * 100)}%`,
          title: `${sample} ms`,
        }))),
      el("div", { class: "ping-stats" }, [
        pingStat("avg", `${data.avg} ms`),
        pingStat("min", `${data.min} ms`),
        pingStat("max", `${data.max} ms`),
        pingStat("loss", `${data.loss}%`),
      ]),
      el("p", { class: "muted small mt-8", text: `${data.host} · ${data.received}/${data.sent} replies · jitter ${data.jitter} ms` }),
    );
  }

  function pingStat(label, value) {
    return el("div", { class: "ping-stat" }, [
      el("small", { text: label }),
      el("strong", { text: value }),
    ]);
  }

  /* ------------------------------------------------------------------ */
  /* Speed test + port scan                                             */
  /* ------------------------------------------------------------------ */
  Network.speedTest = async function speedTest() {
    if (speedTesting) return;
    speedTesting = true;
    const button = $("#speedtest-run");
    const results = $("#ping-results");
    if (button) { button.disabled = true; button.textContent = "Testing…"; }
    if (results) results.replaceChildren(el("div", { class: "row" }, [
      el("span", { class: "spin", html: icon("refresh") }),
      el("span", { text: "Downloading a 25 MB test payload…" }),
    ]));
    try {
      const data = await App.api.post("/api/network/speedtest");
      if (results) {
        results.replaceChildren(
          el("div", { class: "ping-stats" }, [
            pingStat("down", `${data.mbps} Mbps`),
            pingStat("MB/s", `${data.mbytes_per_s}`),
            pingStat("size", `${(data.bytes / 1e6).toFixed(1)} MB`),
            pingStat("time", `${data.seconds}s`),
          ]),
          el("p", { class: "muted small mt-8", text: `Measured against ${data.server}` }),
        );
      }
      App.toast.success("Speed test complete", `${data.mbps} Mbps download`);
    } catch (error) {
      if (results) results.replaceChildren(el("p", { class: "muted small", text: error.message }));
      App.showError("Speed test failed", error);
    } finally {
      speedTesting = false;
      if (button) { button.disabled = false; button.textContent = "Speed test"; }
    }
  };

  async function runPortScan() {
    const host = ($("#portscan-host")?.value || "127.0.0.1").trim();
    const out = $("#portscan-results");
    if (out) out.replaceChildren(el("span", { class: "muted small", text: "Scanning…" }));
    try {
      const data = await App.api.post("/api/network/portscan", { host, timeout: 0.3 });
      const openPorts = new Map((data.open || []).map((entry) => [entry.port, entry]));
      out.replaceChildren();
      [21, 22, 25, 53, 80, 443, 3000, 3306, 3389, 5000, 5432, 6379, 8000, 8080, 8420, 27017].forEach((port) => {
        const entry = openPorts.get(port);
        out.append(el("span", {
          class: `port-chip${entry ? " open" : ""}`,
          text: port,
          title: entry ? `open · ${entry.latency} ms` : "closed/filtered",
        }));
      });
    } catch (error) {
      if (out) out.replaceChildren(el("span", { class: "muted small", text: error.message }));
    }
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                             */
  /* ------------------------------------------------------------------ */
  App.on("stats", (stats) => {
    if (!info) {
      // Grab adapter details lazily on the first stats frame.
      loadInfo(false);
    }
    renderCard(stats);
  });

  Network.refresh = function refresh() {
    loadInfo(false);
    if (!portsLoaded || Date.now() - portsLoaded > 20000) loadPorts();
  };

  document.addEventListener("DOMContentLoaded", () => {
    $("#ping-run")?.addEventListener("click", runPing);
    $("#speedtest-run")?.addEventListener("click", () => Network.speedTest());
    $("#net-external-refresh")?.addEventListener("click", () => loadInfo(true));
    $("#ports-refresh")?.addEventListener("click", loadPorts);
    $("#portscan-run")?.addEventListener("click", runPortScan);
    $("#ping-custom")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") runPing();
    });

    setTimeout(() => loadInfo(false), 1200);
  });
})();
