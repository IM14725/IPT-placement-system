/* Admin Metrics: draws every chart described by the server (apps/core/metrics.py) with Chart.js.
   One renderer handles three shapes: "series" (bar/line over time), "doughnut" and "bars" (categories,
   optionally filtered by region). Colours come from the theme variables, so light and dark mode both work
   and the charts redraw when the theme changes. A chart with nothing to show gets a message, not a blank box. */
(function () {
  "use strict";
  if (typeof Chart === "undefined") return;

  var dataEl = document.getElementById("charts-data");
  if (!dataEl) return;
  var specs = JSON.parse(dataEl.textContent);
  var charts = {};

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function rgb(varName, alpha) {
    var parts = cssVar(varName).split(/\s+/);
    return parts.length === 3 ? "rgb(" + parts.join(" ") + (alpha != null ? " / " + alpha : "") + ")" : "#3525cd";
  }
  var COLOR_VARS = {
    primary: "--c-primary", secondary: "--c-secondary-container", tertiary: "--c-tertiary-container",
    ok: "--c-ok-fg", error: "--c-error", outline: "--c-outline"
  };
  function color(name, alpha) {
    if (name === "warn") return alpha != null ? "rgb(249 168 37 / " + alpha + ")" : "rgb(249 168 37)";
    return rgb(COLOR_VARS[name] || COLOR_VARS.primary, alpha);
  }

  function theme() {
    Chart.defaults.font.family = "'Inter', sans-serif";
    Chart.defaults.color = rgb("--c-on-surface-variant");
    Chart.defaults.borderColor = rgb("--c-outline-variant", 0.5);
  }

  function allZero(values) {
    return !values.some(function (v) { return v; });
  }

  function card(id) { return document.querySelector('[data-chart="' + id + '"]'); }

  function showEmpty(spec, empty) {
    var el = card(spec.id);
    if (!el) return;
    el.querySelector("[data-canvas-wrap]").classList.toggle("hidden", empty);
    el.querySelector("[data-empty]").classList.toggle("hidden", !empty);
  }

  function moneyTicks(spec) {
    return spec.money ? { callback: function (v) { return v >= 1000 ? (v / 1000) + "k" : v; } } : { precision: 0 };
  }

  function seriesChart(spec, canvas) {
    var isLine = spec.kind === "line";
    var datasets = spec.datasets.map(function (d) {
      return {
        label: d.label, data: d.data,
        backgroundColor: isLine ? color(d.color, 0.15) : color(d.color, 0.85),
        borderColor: color(d.color), borderWidth: isLine ? 2 : 0, borderRadius: isLine ? 0 : 4,
        fill: isLine, tension: 0.35, pointRadius: isLine ? 2 : 0
      };
    });
    showEmpty(spec, allZero([].concat.apply([], spec.datasets.map(function (d) { return d.data; }))));
    return new Chart(canvas, {
      type: isLine ? "line" : "bar",
      data: { labels: spec.labels, datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: datasets.length > 1, position: "bottom" } },
        scales: {
          x: { stacked: !!spec.stacked, grid: { display: false }, ticks: { maxTicksLimit: 12, autoSkip: true } },
          y: { stacked: !!spec.stacked, beginAtZero: true, ticks: moneyTicks(spec) }
        }
      }
    });
  }

  function doughnutChart(spec, canvas) {
    showEmpty(spec, allZero(spec.data));
    return new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: spec.labels,
        datasets: [{ data: spec.data, backgroundColor: spec.colors.map(function (c) { return color(c, 0.85); }),
                     borderColor: rgb("--c-surface-container-lowest"), borderWidth: 2 }]
      },
      options: { responsive: true, maintainAspectRatio: false, cutout: "60%", plugins: { legend: { position: "bottom" } } }
    });
  }

  function barsChart(spec, canvas) {
    var select = card(spec.id).querySelector("[data-region-filter]");
    var regionId = select ? select.value : "";
    var items = spec.items.filter(function (i) { return !regionId || i.region === regionId; });
    showEmpty(spec, items.length === 0);
    var datasets = spec.datasets.map(function (d, index) {
      return {
        label: d.label, data: items.map(function (i) { return i.values[index]; }),
        backgroundColor: color(d.color, 0.85), borderColor: color(d.color), borderWidth: 0, borderRadius: 4
      };
    });
    var horizontal = !!spec.horizontal;
    return new Chart(canvas, {
      type: "bar",
      data: { labels: items.map(function (i) { return i.label; }), datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false, indexAxis: horizontal ? "y" : "x",
        plugins: { legend: { display: datasets.length > 1, position: "bottom" } },
        scales: {
          x: { stacked: !!spec.stacked, beginAtZero: true, grid: { display: horizontal }, ticks: horizontal ? { precision: 0 } : {} },
          y: { stacked: !!spec.stacked, beginAtZero: true, grid: { display: !horizontal }, ticks: horizontal ? {} : { precision: 0 } }
        }
      }
    });
  }

  var BUILDERS = { series: seriesChart, doughnut: doughnutChart, bars: barsChart };

  function build(spec) {
    var el = card(spec.id);
    if (!el) return;
    if (charts[spec.id]) { charts[spec.id].destroy(); }
    charts[spec.id] = BUILDERS[spec.shape](spec, el.querySelector("canvas"));
  }

  function buildAll() {
    theme();
    specs.forEach(build);
  }

  specs.forEach(function (spec) {
    var select = card(spec.id) && card(spec.id).querySelector("[data-region-filter]");
    if (select) {
      spec.regions.forEach(function (r) {
        var o = document.createElement("option");
        o.value = r.id; o.textContent = r.name;
        select.appendChild(o);
      });
      select.addEventListener("change", function () { build(spec); });
    }
  });

  buildAll();
  // Redraw with the right colours when the user switches Light / Dark / System.
  new MutationObserver(buildAll).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
})();
