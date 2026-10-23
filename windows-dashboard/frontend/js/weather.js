/* ==========================================================================
   weather.js — optional weather widget using wttr.in
   ========================================================================== */
(function () {
  "use strict";

  const App = window.App;
  const { $, el, icon } = App;

  const Weather = (App.Weather = {});
  let loading = false;
  let lastFetch = 0;

  // Map wttr.in weather codes to a compact glyph + tint.
  function iconForCode(code, isDay = true) {
    const numeric = Number(code);
    if ([113].includes(numeric)) return isDay ? "☀️" : "🌙";
    if ([116].includes(numeric)) return "⛅";
    if ([119, 122].includes(numeric)) return "☁️";
    if ([143, 248, 260].includes(numeric)) return "🌫️";
    if ([200, 386, 389, 392, 395].includes(numeric)) return "⛈️";
    if ([179, 182, 185, 227, 230, 320, 323, 326, 329, 332, 335, 338, 350, 362, 365, 368, 371, 374, 377].includes(numeric)) return "❄️";
    if ([176, 263, 266, 281, 284, 293, 296, 299, 302, 305, 308, 311, 314, 353, 356, 359].includes(numeric)) return "🌧️";
    return "🌤️";
  }

  function unitSymbol(units) {
    return units === "imperial" ? "°F" : "°C";
  }

  function render(data) {
    const body = $("#weather-body");
    if (!body) return;
    const units = unitSymbol(data.units);
    const fragments = [
      el("div", { class: "weather-now" }, [
        el("div", { class: "weather-icon", text: iconForCode(data.code) }),
        el("div", {}, [
          el("div", { class: "weather-temp" }, [
            el("span", { text: data.temp != null ? Math.round(data.temp) : "--" }),
            el("sup", { text: units }),
          ]),
          el("div", { class: "weather-desc", text: data.description || "" }),
          el("div", { class: "weather-loc", text: data.location || "" }),
        ]),
      ]),
      el("div", { class: "weather-grid" }, [
        weatherStat("Feels", data.feels_like != null ? `${Math.round(data.feels_like)}${units}` : "—"),
        weatherStat("Humidity", data.humidity != null ? `${Math.round(data.humidity)}%` : "—"),
        weatherStat("Wind", data.wind_kmph != null ? `${Math.round(data.wind_kmph)} km/h` : "—"),
      ]),
    ];

    if (data.forecast && data.forecast.length) {
      fragments.push(el("div", { class: "weather-forecast" }, data.forecast.map((day) => {
        const date = day.date ? new Date(`${day.date}T12:00:00`) : null;
        return el("div", { class: "forecast-day", title: day.description || "" }, [
          el("small", { text: date ? date.toLocaleDateString(undefined, { weekday: "short" }) : "" }),
          el("div", { text: iconForCode(day.code), style: "font-size:18px;line-height:1.4" }),
          el("strong", { text: day.max != null ? `${Math.round(day.max)}°` : "—" }),
          el("small", { text: day.min != null ? `${Math.round(day.min)}°` : "" }),
        ]);
      })));
    }

    if (data.observation_time) {
      fragments.push(el("p", { class: "muted small mt-8", text: `Updated ${data.observation_time} · wttr.in` }));
    }

    body.replaceChildren(...fragments);
  }

  function weatherStat(label, value) {
    return el("div", { style: "text-align:center" }, [
      el("small", { class: "muted", style: "display:block;font-size:10px;text-transform:uppercase;letter-spacing:.08em", text: label }),
      el("span", { style: "font-size:12.5px", text: value }),
    ]);
  }

  function renderError(message) {
    const body = $("#weather-body");
    if (!body) return;
    body.replaceChildren(el("div", { class: "empty-state", style: "padding:22px 10px" }, [
      el("span", { html: icon("cloud") }),
      el("p", { text: message }),
      el("button", { class: "btn ghost sm", text: "Retry", onclick: () => Weather.refresh(true) }),
    ]));
  }

  Weather.refresh = async function refresh(force = false) {
    if (loading) return;
    const enabled = App.state.features?.weather !== false;
    if (!enabled) {
      const card = document.querySelector('[data-card="weather"]');
      if (card) card.hidden = true;
      return;
    }
    loading = true;
    lastFetch = Date.now();
    try {
      const data = await App.api.get(App.api.withQuery("/api/weather", { force: force ? "true" : "false" }));
      if (data.disabled || data.ok === false) {
        renderError(data.error || "Weather is disabled");
        return;
      }
      render(data);
      App.state.weather = data;
    } catch (error) {
      renderError(error.message || "Weather unavailable (offline?)");
    } finally {
      loading = false;
    }
  };

  document.addEventListener("DOMContentLoaded", () => {
    $("#weather-refresh")?.addEventListener("click", () => Weather.refresh(true));
    setTimeout(() => Weather.refresh(false), 600);
    setInterval(() => {
      if (!document.hidden && Date.now() - lastFetch > 15 * 60 * 1000) Weather.refresh(false);
    }, 60000);
    App.on("config-saved", () => Weather.refresh(true));
  });
})();
