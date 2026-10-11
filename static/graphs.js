/* AuraFlow graphs: adds (1) an Evaluation tab with ROC / threshold / noise line plots
   and (2) a score line chart on the Monitor page. Plain JS, no libraries, uses the
   page's own CSS variables so dark and light themes both work. */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function sv(tag, attrs, text) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }

  // ------------------------------------------------------------ styles
  var css = [
    ".gr-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin:0 0 22px}",
    ".gr-svg{width:100%;height:auto;display:block}",
    ".gr-grid{stroke:var(--line);stroke-width:1;opacity:.55}",
    ".gr-tick{fill:var(--muted);font-size:11px}",
    ".gr-axl{fill:var(--muted);font-size:12px}",
    ".gr-ln{fill:none;stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}",
    ".gr-dash{stroke-dasharray:5 5;stroke-width:1.6}",
    ".gr-ref{stroke:var(--muted);stroke-dasharray:4 4;stroke-width:1;opacity:.8}",
    ".gr-pt{stroke:var(--panel);stroke-width:2;cursor:pointer}",
    ".gr-pt:focus{outline:none;stroke:var(--text)}",
    ".gr-leg{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:.85rem;color:var(--muted);margin-top:10px}",
    ".gr-sw{display:inline-block;width:16px;height:4px;border-radius:2px;margin-right:7px;vertical-align:middle}",
    ".gr-note{color:var(--muted);font-size:.88rem;margin:10px 0 0}",
    ".gr-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:16px;margin:26px 0}",
    ".gr-kpi{border-left:3px solid var(--accent);padding-left:14px}",
    ".gr-kpi b{display:block;font:700 1.9rem/1.1 var(--display)}",
    ".gr-kpi span{color:var(--muted);font-size:.88rem}",
    ".gr-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:22px}",
    ".gr-cms{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}",
    ".gr-cms h4{margin:0 0 8px;font-size:.95rem}",
    "table.gr-cm td,table.gr-cm th{text-align:center}",
    ".gr-g{background:color-mix(in srgb,var(--ok) 20%,transparent)}",
    ".gr-r{background:color-mix(in srgb,var(--bad) 20%,transparent)}",
    "#view-evaluation{padding:clamp(28px,5vw,56px) 0 70px}",
    "#view-evaluation h1{font-size:clamp(1.8rem,3.6vw,2.8rem);line-height:1.1;margin:0 0 10px;max-width:22ch}",
    "#view-evaluation h2{font-size:1.2rem;margin:34px 0 12px}",
    "#score-chart-sec{margin-bottom:40px}",
    "#score-chart-sec h2{font-size:1.15rem;margin-bottom:14px}"
  ].join("\n");
  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  // ------------------------------------------------------------ chart helper
  var SERIES_COLORS = ["var(--accent)", "var(--bad)", "var(--ok)", "var(--warn)", "var(--muted)"];
  function num(v, d) { return Number(v).toFixed(d); }

  function lineChart(o) {
    var W = 560, H = 320, L = 56, R = 16, T = 14, B = 46;
    var pw = W - L - R, ph = H - T - B;
    var xmax = o.xmax || 1, ymax = o.ymax || 1;
    var X = function (x) { return L + (x / xmax) * pw; };
    var Y = function (y) { return T + ph - (y / ymax) * ph; };
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.label, "class": "gr-svg" });

    (o.bands || []).forEach(function (b) {
      svg.appendChild(sv("rect", { x: L, width: pw, y: Y(b.to), height: Math.max(Y(b.from) - Y(b.to), 0),
        style: "fill:" + b.color + ";opacity:.10" }));
    });
    for (var i = 0; i <= 5; i++) {
      var g = i / 5;
      svg.appendChild(sv("line", { x1: L, x2: W - R, y1: Y(g * ymax), y2: Y(g * ymax), "class": "gr-grid" }));
      svg.appendChild(sv("text", { x: L - 8, y: Y(g * ymax) + 4, "text-anchor": "end", "class": "gr-tick" }, num(g * ymax, o.yd == null ? 1 : o.yd)));
      if (!o.xticks) {
        svg.appendChild(sv("text", { x: X(g * xmax), y: H - B + 17, "text-anchor": "middle", "class": "gr-tick" }, num(g * xmax, o.xd == null ? 1 : o.xd)));
      }
    }
    (o.xticks || []).forEach(function (t) {
      svg.appendChild(sv("text", { x: X(t.x), y: H - B + 17, "text-anchor": "middle", "class": "gr-tick" }, t.label));
    });
    (o.hlines || []).forEach(function (h) {
      svg.appendChild(sv("line", { x1: L, x2: W - R, y1: Y(h.y), y2: Y(h.y), "class": "gr-ref" }));
      svg.appendChild(sv("text", { x: W - R - 4, y: Y(h.y) - 5, "text-anchor": "end", "class": "gr-tick" }, h.label));
    });
    if (o.vline != null) {
      svg.appendChild(sv("line", { x1: X(o.vline), x2: X(o.vline), y1: T, y2: T + ph, "class": "gr-ref" }));
    }
    o.series.forEach(function (s, idx) {
      var color = s.color || SERIES_COLORS[idx % SERIES_COLORS.length];
      var d = s.pts.map(function (p, k) { return (k ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1); }).join(" ");
      var path = sv("path", { d: d, "class": "gr-ln" + (s.dash ? " gr-dash" : "") });
      path.style.stroke = color;
      svg.appendChild(path);
    });
    svg.appendChild(sv("text", { x: L + pw / 2, y: H - 6, "text-anchor": "middle", "class": "gr-axl" }, o.xLabel));
    svg.appendChild(sv("text", { x: 14, y: T + ph / 2, "text-anchor": "middle", "class": "gr-axl",
      transform: "rotate(-90 14 " + (T + ph / 2) + ")" }, o.yLabel));

    (o.points || []).forEach(function (p) {          // clickable markers (monitor chart)
      var c = sv("circle", { cx: X(p.x), cy: Y(p.y), r: 6, "class": "gr-pt", tabindex: "0", role: "button", "aria-label": p.label });
      c.style.fill = p.color;
      c.appendChild(sv("title", {}, p.label));
      if (p.onClick) {
        c.addEventListener("click", p.onClick);
        c.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); p.onClick(); } });
      }
      svg.appendChild(c);
    });

    var wrap = el("div", "gr-card");
    wrap.appendChild(svg);
    var leg = el("div", "gr-leg");
    o.series.forEach(function (s, idx) {
      var item = el("span");
      var sw = el("i", "gr-sw");
      sw.style.background = s.color || SERIES_COLORS[idx % SERIES_COLORS.length];
      item.appendChild(sw);
      item.appendChild(document.createTextNode(s.name));
      leg.appendChild(item);
    });
    if (o.series.length > 1 || o.legendAlways) wrap.appendChild(leg);
    if (o.note) wrap.appendChild(el("p", "gr-note", o.note));
    return wrap;
  }

  // ------------------------------------------------------------ monitor: score line chart
  var LEVEL_COLOR = { low: "var(--ok)", mid: "var(--warn)", high: "var(--bad)" };

  function readTable() {
    var out = [];
    var trs = document.querySelectorAll("#rows tr");
    for (var i = trs.length - 1; i >= 0; i--) {        // table is newest first; chart runs oldest to newest
      var tds = trs[i].children;
      if (tds.length < 7) continue;
      var dot = tds[6].querySelector(".dot");
      out.push({
        name: tds[0].textContent.trim(),
        score: parseFloat(tds[5].textContent),
        level: dot ? dot.dataset.level : "low",
        status: tds[6].textContent.trim(),
        btn: tds[0].querySelector("button")
      });
    }
    return out;
  }

  var chartSec = null;
  function ensureChartSection() {
    if (chartSec && document.body.contains(chartSec)) return chartSec;
    var h = $("h-readings");
    var anchor = h && h.closest("section");
    if (!anchor) return null;
    chartSec = el("section");
    chartSec.id = "score-chart-sec";
    chartSec.setAttribute("aria-labelledby", "h-scorechart");
    var h2 = el("h2", null, "Score across nodes");
    h2.id = "h-scorechart";
    chartSec.appendChild(h2);
    chartSec.appendChild(el("div")).id = "score-chart";
    anchor.parentNode.insertBefore(chartSec, anchor);
    return chartSec;
  }

  var drawQueued = false;
  function drawScoreChart() {
    drawQueued = false;
    if (!ensureChartSection()) return;
    var box = $("score-chart");
    box.textContent = "";
    var rows = readTable();
    if (!rows.length) {
      box.appendChild(el("p", "empty", "Add readings to see the score line chart."));
      return;
    }
    var n = rows.length;
    var xticks = rows.map(function (r, i) { return { x: i, label: String(i + 1) }; });
    if (n > 14) xticks = xticks.filter(function (t, i) { return i % Math.ceil(n / 14) === 0; });
    var chart = lineChart({
      label: "Line chart of risk score for " + n + " monitoring nodes, in the order they were added",
      series: [{ name: "Score (%)", pts: rows.map(function (r, i) { return [i, r.score]; }), color: "var(--accent)" }],
      legendAlways: false,
      xmax: Math.max(n - 1, 1), ymax: 100, yd: 0, xticks: xticks,
      xLabel: "Node (order added)", yLabel: "Risk score (%)",
      bands: [{ from: 0, to: 25, color: "var(--ok)" }, { from: 25, to: 50, color: "var(--warn)" }, { from: 50, to: 100, color: "var(--bad)" }],
      hlines: [{ y: 25, label: "Caution from 25" }, { y: 50, label: "High risk from 50" }],
      points: rows.map(function (r, i) {
        return { x: i, y: r.score, color: LEVEL_COLOR[r.level] || "var(--accent)",
          label: (i + 1) + ". " + r.name + ": " + r.score.toFixed(1) + "% (" + r.status + ")",
          onClick: function () { if (r.btn) r.btn.click(); } };
      }),
      note: "Each point is one node, in the order added. Select a point to inspect that node. Green, amber and red bands match the score bands."
    });
    box.appendChild(chart);
  }
  function queueDraw() {
    if (drawQueued) return;
    drawQueued = true;
    setTimeout(drawScoreChart, 30);
  }

  function watchRows() {
    var tb = $("rows");
    if (!tb) return false;
    new MutationObserver(queueDraw).observe(tb, { childList: true, subtree: true, characterData: true });
    queueDraw();
    return true;
  }

  // ------------------------------------------------------------ evaluation tab
  var view = null, loaded = false;

  function buildShell() {
    var main = $("main");
    if (!main) return;
    view = el("div");
    view.id = "view-evaluation";
    view.hidden = true;
    var wrap = el("div", "wrap");
    var h1 = el("h1", null, "How well does the score catch unsafe water?");
    h1.id = "h-evaluation";
    h1.tabIndex = -1;
    wrap.appendChild(h1);
    wrap.appendChild(el("p", "lede", "The score is tested as a screening tool on 1,000 simulated samples. Unsafe water is the positive class, so missing it (a false negative) is the costly mistake."));
    var body = el("div");
    body.id = "eval-body";
    wrap.appendChild(body);
    view.appendChild(wrap);
    main.appendChild(view);

    var nav = document.querySelector(".top nav");
    if (nav) {
      var a = el("a", null, "Evaluation");
      a.href = "#evaluation";
      a.id = "nav-evaluation";
      nav.appendChild(a);
    }
  }

  function kpi(value, label) {
    var d = el("div", "gr-kpi");
    d.appendChild(el("b", null, value));
    d.appendChild(el("span", null, label));
    return d;
  }
  function f3(v) { return Number(v).toFixed(3); }

  function metricsTable(rows) {
    var wrap = el("div", "tablewrap");
    var t = el("table");
    t.appendChild(el("caption", "sr", "Precision, recall, specificity and F1 for each model"));
    var thead = el("thead"), tr = el("tr");
    ["Model", "Precision", "Recall", "Specificity", "F1"].forEach(function (h, i) {
      var th = el("th", i ? "num" : null, h); th.scope = "col"; tr.appendChild(th);
    });
    thead.appendChild(tr); t.appendChild(thead);
    var tb = el("tbody");
    rows.forEach(function (r) {
      var row = el("tr");
      var th = el("th", null, r[0]); th.scope = "row"; row.appendChild(th);
      [r[1].precision, r[1].recall_sensitivity, r[1].specificity, r[1].f1].forEach(function (v) {
        row.appendChild(el("td", "num", f3(v)));
      });
      tb.appendChild(row);
    });
    t.appendChild(tb); wrap.appendChild(t);
    return wrap;
  }

  function confusion(title, m) {
    var box = el("div");
    box.appendChild(el("h4", null, title));
    var wrap = el("div", "tablewrap");
    var t = el("table", "gr-cm");
    t.appendChild(el("caption", "sr", "Confusion matrix: " + title));
    var thead = el("thead"), hr = el("tr");
    ["", "Predicted unsafe", "Predicted OK"].forEach(function (h) { var th = el("th", null, h); th.scope = "col"; hr.appendChild(th); });
    thead.appendChild(hr); t.appendChild(thead);
    var tb = el("tbody");
    [["Actually unsafe", "TP " + m.tp, "gr-g", "FN " + m.fn, "gr-r"], ["Actually OK", "FP " + m.fp, "gr-r", "TN " + m.tn, "gr-g"]].forEach(function (r) {
      var tr = el("tr");
      var th = el("th", null, r[0]); th.scope = "row"; tr.appendChild(th);
      tr.appendChild(el("td", r[2], r[1]));
      tr.appendChild(el("td", r[4], r[3]));
      tb.appendChild(tr);
    });
    t.appendChild(tb); wrap.appendChild(t); box.appendChild(wrap);
    return box;
  }

  function renderEval(d) {
    var body = $("eval-body");
    body.textContent = "";
    var v1 = d.models.wti_v1, v2 = d.models.wti_v2, ds = d.dataset;
    var sf = v2.safety_first;

    var k = el("div", "gr-kpis");
    k.appendChild(kpi(f3(v2.roc_auc), "ROC-AUC, calibrated score (95% CI " + v2.roc_auc_95ci[0] + " to " + v2.roc_auc_95ci[1] + ")"));
    k.appendChild(kpi(f3(v2.default_band.recall_sensitivity), "Recall (sensitivity), calibrated score"));
    k.appendChild(kpi(f3(v2.default_band.specificity), "Specificity, calibrated score"));
    k.appendChild(kpi(f3(v1.default_band.recall_sensitivity), "Recall, current score at the 25 band"));
    body.appendChild(k);

    var call = el("div", "callout");
    var p = el("p");
    p.appendChild(el("b", null, "Key finding. "));
    p.appendChild(document.createTextNode("The current score misses about 90% of unsafe samples at its low-risk band, because its limits are set for extreme pollution. The limit-calibrated version fixes that. The data is simulated and the calibrated score shares its limits with the ground truth, so this shows consistency, not independent validation. Bacteria are not tested, so a pass never means safe."));
    call.appendChild(p);
    body.appendChild(call);

    body.appendChild(el("h2", null, "ROC curve"));
    body.appendChild(lineChart({
      label: "ROC curve comparing the current and calibrated scores",
      series: [
        { name: "Current score, AUC " + f3(v1.roc_auc), pts: v1.roc_points, color: "var(--bad)" },
        { name: "Calibrated score, AUC " + f3(v2.roc_auc), pts: v2.roc_points, color: "var(--accent)" },
        { name: "Chance", pts: [[0, 0], [1, 1]], color: "var(--muted)", dash: true }
      ],
      xLabel: "False positive rate (1 - specificity)", yLabel: "Sensitivity (recall)",
      note: "A curve hugging the top-left corner means unsafe water is caught without many false alarms."
    }));

    var two = el("div", "gr-two");
    var left = el("div"), right = el("div");
    left.appendChild(el("h2", null, "Threshold trade-off"));
    var sw = function (key) { return v2.threshold_sweep.map(function (r) { return [r.threshold / 100, r[key]]; }); };
    left.appendChild(lineChart({
      label: "Recall, specificity, precision and F1 as the alert threshold changes",
      series: [
        { name: "Recall", pts: sw("recall_sensitivity"), color: "var(--accent)" },
        { name: "Specificity", pts: sw("specificity"), color: "var(--ok)" },
        { name: "Precision", pts: sw("precision"), color: "var(--warn)" },
        { name: "F1", pts: sw("f1"), color: "var(--bad)" }
      ],
      vline: sf.threshold / 100, xLabel: "Alert threshold (score / 100)", yLabel: "Metric",
      note: "Dashed line: safety-first threshold (" + sf.threshold + "), the highest alert level that keeps recall at or above " + sf.min_recall_target + "."
    }));
    two.appendChild(left);
    if (d.noise_robustness) {
      right.appendChild(el("h2", null, "Robustness to sensor noise"));
      right.appendChild(lineChart({
        label: "ROC-AUC as simulated sensor noise increases",
        series: [
          { name: "Current score", pts: d.noise_robustness.map(function (r) { return [r.sensor_noise, r.wti_v1]; }), color: "var(--bad)" },
          { name: "Calibrated score", pts: d.noise_robustness.map(function (r) { return [r.sensor_noise, r.wti_v2]; }), color: "var(--accent)" }
        ],
        xmax: 0.4, xd: 2, ymax: 1, xLabel: "Sensor noise (log-sd)", yLabel: "ROC-AUC",
        note: "The calibrated score degrades gradually as readings get noisier."
      }));
      two.appendChild(right);
    }
    body.appendChild(two);

    body.appendChild(el("h2", null, "Metrics"));
    body.appendChild(metricsTable([
      ["Current score, alert at 25 or more", v1.default_band],
      ["Calibrated score, alert at 25 or more", v2.default_band],
      ["Calibrated score, safety-first (" + sf.threshold + " or more)", sf],
      ["App drinking check (stricter, flags borderline)", d.rule_based_drinking_check]
    ]));

    body.appendChild(el("h2", null, "Confusion matrices"));
    var cms = el("div", "gr-cms");
    cms.appendChild(confusion("Current score, 25 or more", v1.default_band));
    cms.appendChild(confusion("Calibrated score, 25 or more", v2.default_band));
    cms.appendChild(confusion("App drinking check", d.rule_based_drinking_check));
    body.appendChild(cms);
    body.appendChild(el("p", "gr-note", "Based on " + ds.n + " simulated samples (" + ds.unsafe + " unsafe, " + ds.acceptable + " acceptable) with 5% sensor noise. Replace with lab-labelled samples before any real-world claim."));
  }

  function loadEval() {
    var body = $("eval-body");
    body.textContent = "";
    body.appendChild(el("p", "empty", "Running the evaluation..."));
    fetch("/api/evaluate").then(function (r) {
      if (!r.ok) throw new Error("Server returned " + r.status);
      return r.json();
    }).then(function (d) { loaded = true; renderEval(d); })
      .catch(function (e) {
        body.textContent = "";
        body.appendChild(el("p", "empty", "Couldn't load the evaluation: " + e.message + ". "));
        var b = el("button", "btn small", "Try again");
        b.type = "button";
        b.addEventListener("click", loadEval);
        body.appendChild(b);
      });
  }

  var OTHER = ["home", "app", "guide"];
  function onRoute() {
    if (!view) return;
    var on = (location.hash || "") === "#evaluation";
    view.hidden = !on;
    var link = $("nav-evaluation");
    if (on) {
      OTHER.forEach(function (n) {
        var v = $("view-" + n); if (v) v.hidden = true;
        var a = $("nav-" + n); if (a) a.removeAttribute("aria-current");
      });
      if (link) link.setAttribute("aria-current", "page");
      document.title = "Evaluation · AuraFlow";
      window.scrollTo(0, 0);
      if (!loaded) loadEval();
      var h = $("h-evaluation"); if (h) h.focus({ preventScroll: true });
    } else if (link) {
      link.removeAttribute("aria-current");
    }
  }

  function init() {
    buildShell();
    watchRows();
    window.addEventListener("hashchange", onRoute);
    onRoute();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
