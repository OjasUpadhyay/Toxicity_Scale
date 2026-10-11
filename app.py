"""AuraFlow: a small public web app for scoring simulated water-quality readings.

The server is stateless. Each visitor's readings live in their own browser
(localStorage) and are sent here only to be scored, so one visitor never sees
another visitor's data.
"""
import csv
import datetime
import hashlib
import html
import io
import json
import math
import os
from flask import Flask, Response, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Works whether index.html sits in a templates/ folder or next to app.py.
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
if not os.path.isfile(os.path.join(TEMPLATE_DIR, "index.html")):
    TEMPLATE_DIR = BASE_DIR

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MB upload limit

MAX_ROWS = 500
LIMITS = {"ph": (0.0, 14.0), "turbidity": (0.0, 10000.0), "metals": (0.0, 1000.0), "bod": (0.0, 100000.0)}

# ---------------------------------------------------------------- scoring

def penalties(r):
    """Each parameter mapped to 0..1 (0 = fine, 1 = at or beyond the cap)."""
    return {
        "ph": min(abs(r["ph"] - 7.0) / 3.0, 1.0),
        "turbidity": min(r["turbidity"] / 100.0, 1.0),
        "metals": min(r["metals"] / 1.0, 1.0),
        "bod": min(r["bod"] / 150.0, 1.0),
    }


def wti(r):
    """Project-defined demonstration score (0-100). Not an official BIS formula."""
    p = penalties(r)
    score = 100 * (
        0.20 * p["ph"] ** 2
        + 0.25 * p["turbidity"] ** 2
        + 0.35 * p["metals"] ** 2
        + 0.20 * p["bod"] ** 2
    )
    # Worst-parameter floor: one badly out-of-range reading must not be
    # averaged away by clean readings elsewhere.
    score = max(score, 60 * max(p.values()) ** 2)
    return round(min(score, 100.0), 1)


def classify(score):
    if score < 25:
        return "low", "Low simulated risk", "Monitor"
    if score <= 50:
        return "mid", "Caution", "Inspect / hold"
    return "high", "High risk", "Interlock simulated"


def clean_reading(obj):
    """Validate one reading; return a clean dict or None."""
    if not isinstance(obj, dict):
        return None
    out = {}
    for key, (lo, hi) in LIMITS.items():
        try:
            v = float(obj.get(key))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v):
            return None
        out[key] = min(max(v, lo), hi)
    name = str(obj.get("location", "")).strip()[:60]
    out["location"] = name or "Unnamed node"
    return out

# ------------------------------------------------- drinking-water check
# Compares readings with drinking-water guidance (BIS IS 10500 / WHO / CPCB source classes).
# It covers only four measurements. Germs (coliforms, E. coli) and most chemicals are NOT tested,
# so the best possible result is "passes these checks", never "safe".

def _chk(key, name, value, status, note):
    return {"key": key, "name": name, "value": value, "status": status, "note": note}


def drinking_check(r):
    ph, ntu, metals, bod = r["ph"], r["turbidity"], r["metals"], r["bod"]
    checks = []

    if 6.5 <= ph <= 8.5:
        checks.append(_chk("ph", "pH", f"{ph:.2f}", "pass", "Inside the 6.5 to 8.5 range BIS accepts for drinking water."))
    else:
        side = "acidic" if ph < 7 else "alkaline"
        if 6.0 <= ph <= 9.0:
            checks.append(_chk("ph", "pH", f"{ph:.2f}", "check", f"Slightly {side}. Outside the 6.5 to 8.5 range, so it needs adjusting."))
        else:
            checks.append(_chk("ph", "pH", f"{ph:.2f}", "fail", f"Too {side} to drink. It can irritate and, if acidic, corrode pipes and release metals."))

    if ntu <= 1:
        checks.append(_chk("turbidity", "Turbidity", f"{ntu:.2f} NTU", "pass", "At or below 1 NTU, the BIS acceptable limit."))
    elif ntu <= 5:
        checks.append(_chk("turbidity", "Turbidity", f"{ntu:.2f} NTU", "check", "Above the 1 NTU acceptable limit. BIS allows up to 5 NTU only when no better source exists."))
    else:
        checks.append(_chk("turbidity", "Turbidity", f"{ntu:.2f} NTU", "fail", "Too cloudy. Particles can shield germs from disinfectant."))

    if metals <= 0.01:
        checks.append(_chk("metals", "Metals", f"{metals:.3f} ppm", "pass", "At or below 0.01 ppm, the guideline for lead and arsenic. A lab test would confirm each metal separately."))
    elif metals <= 0.1:
        checks.append(_chk("metals", "Metals", f"{metals:.3f} ppm", "check", "Above 0.01 ppm, so lead or arsenic could be over their guideline. Test to find out which metals are present."))
    else:
        checks.append(_chk("metals", "Metals", f"{metals:.3f} ppm", "fail", "Well above the guideline values for toxic metals such as lead, arsenic and chromium."))

    if bod <= 2:
        checks.append(_chk("bod", "BOD", f"{bod:.1f} mg/L", "pass", "At or below 2 mg/L, the CPCB class for a source that needs only disinfection."))
    elif bod <= 3:
        checks.append(_chk("bod", "BOD", f"{bod:.1f} mg/L", "check", "Between 2 and 3 mg/L. Usable as a source only with conventional treatment: settling, filtering, then disinfecting."))
    else:
        checks.append(_chk("bod", "BOD", f"{bod:.1f} mg/L", "fail", "Above 3 mg/L means heavy organic pollution, often sewage, which brings germs. Not suitable as a drinking source."))

    status = {c["key"]: c["status"] for c in checks}
    worst = "fail" if "fail" in status.values() else ("check" if "check" in status.values() else "pass")

    steps = []
    if status["turbidity"] != "pass":
        steps.append("Clear the water first, because cloudy water defeats disinfection. Let it settle in a clean, covered container for several hours, then pour the clear water through a clean cloth or a sand or ceramic filter. Treatment plants add a coagulant such as alum, let the particles settle, then filter.")
    if status["ph"] != "pass":
        if ph < 7:
            steps.append("Raise the pH. A calcite (neutralising) filter or controlled soda-ash dosing does this, and it also stops acidic water corroding pipes.")
        else:
            steps.append("Lower the pH. This needs controlled acid or CO2 dosing, usually done by a water-treatment professional. Blending with a neutral source can also help.")
    if status["metals"] != "pass":
        steps.append("Find out which metals are present with a lab test, because removal depends on the metal. Reverse osmosis removes many dissolved metals. Arsenic usually needs iron-based or activated-alumina media. For lead, use a filter certified to remove lead. Boiling removes none of them.")
    if status["bod"] == "fail":
        steps.append("Don't use this as a drinking source if you have any alternative. Organic pollution usually means sewage or industrial waste, bringing germs and chemicals. If it is the only source, it needs full treatment (settling, filtering, disinfecting) and regular lab testing.")
    elif status["bod"] == "check":
        steps.append("Treat the organic load conventionally: settle, filter, then disinfect. Re-test afterwards.")
    steps.append("Disinfect clear water: bring it to a rolling boil for at least one minute, or use chlorine tablets as directed. UV works only on clear water. Disinfection kills germs but does not remove metals or chemicals.")
    steps.append("Before relying on it for drinking, have an accredited lab test it, including coliform bacteria. This tool cannot test for germs.")

    if worst == "fail":
        level, label = "high", "Not drinkable as it is"
        summary = "At least one reading is outside drinking-water limits. Don't drink it untreated. The steps below show what to fix."
    elif worst == "check":
        level, label = "mid", "Not confirmed: treat and test first"
        summary = "No reading is far out of range, but at least one is borderline or needs a closer look. Treat it and have it tested before drinking."
    else:
        level, label = "low", "Passes these four checks"
        summary = "That is not a safety guarantee. Germs and most chemicals were not tested. Disinfect it, and confirm with a lab before relying on it."

    return {"level": level, "label": label, "summary": summary, "checks": checks, "steps": steps}


def analyse(rows):
    enriched = []
    for r in rows:
        score = wti(r)
        level, label, response = classify(score)
        p = penalties(r)
        enriched.append({
            **r,
            "wti": score,
            "level": level,
            "status": label,
            "response": response,
            "penalties": {k: round(v, 3) for k, v in p.items()},
            "driver": max(p, key=p.get),
            "drink": drinking_check(r),
        })
    n = len(enriched)
    stats = {
        "total": n,
        "average": round(sum(e["wti"] for e in enriched) / n, 1) if n else 0.0,
        "caution": sum(1 for e in enriched if e["level"] == "mid"),
        "high": sum(1 for e in enriched if e["level"] == "high"),
    }
    canonical = json.dumps(
        [{k: e[k] for k in ("location", "ph", "turbidity", "metals", "bod", "wti")} for e in enriched],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {"rows": enriched, "stats": stats, "sha256": digest}

# ------------------------------------------------------- report document
# Builds one self-contained HTML file (graph, evaluation and details) that
# the browser downloads. No extra Python packages are needed.

PARAMS = ("ph", "turbidity", "metals", "bod")
PARAM_NAMES = {"ph": "pH", "turbidity": "Turbidity", "metals": "Metals", "bod": "BOD"}
PARAM_UNITS = {"ph": "", "turbidity": " NTU", "metals": " ppm", "bod": " mg/L"}
PARAM_LIMITS = {"ph": "3 units from neutral", "turbidity": "100 NTU", "metals": "1 ppm", "bod": "150 mg/L"}
PARAM_DIGITS = {"ph": 2, "turbidity": 2, "metals": 3, "bod": 1}
LEVEL_COLORS = {"low": "#0a7a58", "mid": "#b87a00", "high": "#c62d47"}


def _e(x):
    return html.escape(str(x), quote=True)


def _val(key, v):
    return "%.*f" % (PARAM_DIGITS[key], v)


def _clip(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def _chart_svg(rows):
    W, left, right, top, row_h, bar_h = 760, 220, 50, 34, 34, 20
    plot_w = W - left - right
    plot_h = len(rows) * row_h
    H = top + plot_h + 36

    def x(v):
        return left + plot_w * v / 100.0

    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="100%%" role="img" '
         'aria-label="Bar chart of risk scores by monitoring node" font-family="Segoe UI, Arial, sans-serif">' % (W, H)]
    for lo, hi, fill, label in ((0, 25, "#e6f5ef", "Low"), (25, 50, "#fdf3dc", "Caution"), (50, 100, "#fbe5e9", "High risk")):
        o.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s"/>' % (x(lo), top, x(hi) - x(lo), plot_h, fill))
        o.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="12" fill="#3d5a71">%s</text>' % ((x(lo) + x(hi)) / 2, top - 12, label))
    for v in (0, 25, 50, 75, 100):
        o.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="#a3bccf" stroke-width="1"/>' % (x(v), x(v), top, top + plot_h))
        o.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="11" fill="#3d5a71">%d</text>' % (x(v), top + plot_h + 16, v))
    for i, r in enumerate(rows):
        y = top + i * row_h + (row_h - bar_h) / 2.0
        w = max(x(r["wti"]) - left, 2)
        o.append('<text x="%d" y="%.1f" text-anchor="end" font-size="12" fill="#0a2236">%s</text>' % (left - 10, y + bar_h / 2.0 + 4, _e(_clip(r["location"], 30))))
        o.append('<rect x="%d" y="%.1f" width="%.1f" height="%d" rx="3" fill="%s"/>' % (left, y, w, bar_h, LEVEL_COLORS[r["level"]]))
        o.append('<text x="%.1f" y="%.1f" font-size="12" font-weight="600" fill="#0a2236">%.1f%%</text>' % (x(r["wti"]) + 6, y + bar_h / 2.0 + 4, r["wti"]))
    o.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="11" fill="#3d5a71">Risk score (0 to 100, higher is worse)</text>' % (left + plot_w / 2.0, H - 4))
    o.append("</svg>")
    return "".join(o)


def _node_evaluation(r):
    d = r["driver"]
    if r["wti"] >= 25:
        s = "Scores %.1f%% (%s). %s is the main driver at %s%s, against a score limit of %s." % (
            r["wti"], r["status"], PARAM_NAMES[d], _val(d, r[d]), PARAM_UNITS[d], PARAM_LIMITS[d])
    else:
        s = "Scores %.1f%% (%s). All four measurements are in a comfortable range." % (r["wti"], r["status"])
    return "%s Drinking check: %s. Suggested response: %s." % (s, r["drink"]["label"], r["response"])


def evaluate(result):
    """Plain-language evaluation of the whole data set."""
    rows = sorted(result["rows"], key=lambda r: r["wti"], reverse=True)
    st = result["stats"]
    n = st["total"]
    if st["high"]:
        level = "high"
        headline = "Action needed: %d of %d node(s) are in the high-risk band." % (st["high"], n)
    elif st["caution"]:
        level = "mid"
        headline = "Review recommended: %d of %d node(s) are in the caution band and none are high risk." % (st["caution"], n)
    else:
        level = "low"
        headline = "No node is above the caution band in these readings."

    findings = []
    worst = rows[0]
    if worst["wti"] >= 25:
        d = worst["driver"]
        findings.append("Highest score: %s at %.1f%% (%s). %s is the main driver at %s%s." % (
            worst["location"], worst["wti"], worst["status"], PARAM_NAMES[d], _val(d, worst[d]), PARAM_UNITS[d]))
    else:
        findings.append("Highest score: %s at %.1f%%, which is still in the low band." % (worst["location"], worst["wti"]))
    findings.append("Average score across all nodes: %.1f%%." % st["average"])

    flagged = [r for r in rows if r["wti"] >= 25]
    counts = {}
    for r in flagged:
        counts[r["driver"]] = counts.get(r["driver"], 0) + 1
    if counts:
        top = max(counts, key=counts.get)
        findings.append("%s is the main driver in %d of %d flagged node(s)." % (PARAM_NAMES[top], counts[top], len(flagged)))

    not_drinkable = sum(1 for r in rows if r["drink"]["level"] == "high")
    unconfirmed = sum(1 for r in rows if r["drink"]["level"] == "mid")
    passing = sum(1 for r in rows if r["drink"]["level"] == "low")
    findings.append("Drinking check: %d node(s) are not drinkable as they are, %d need treatment and testing first, and %d pass the four checks (which is not a safety guarantee)." % (
        not_drinkable, unconfirmed, passing))

    recs = []
    if flagged:
        names = ", ".join(r["location"] for r in flagged[:5])
        recs.append("Deal with the flagged nodes first, in this order: %s%s." % (names, " and others" if len(flagged) > 5 else ""))
    seen = {}
    for r in rows:
        for c in r["drink"]["checks"]:
            if c["status"] != "pass":
                seen[c["key"]] = True
    if "turbidity" in seen:
        recs.append("Clear cloudy water before disinfecting: let it settle, filter it, then disinfect.")
    if "ph" in seen:
        recs.append("Correct the pH of out-of-range nodes. Acidic water can corrode pipes and release metals.")
    if "metals" in seen:
        recs.append("Send samples for a lab test to find out which metals are present. Boiling does not remove metals.")
    if "bod" in seen:
        recs.append("High BOD points to organic or sewage pollution. Avoid those nodes as a drinking source and look for the upstream cause.")
    recs.append("Confirm any drinking-water use with an accredited laboratory, including coliform bacteria, which this tool cannot test.")
    recs.append("Re-test after any treatment or repair and compare the new scores with this report.")
    return {"level": level, "headline": headline, "findings": findings, "recs": recs}


REPORT_CSS = """
@page{margin:14mm}*{box-sizing:border-box}
body{margin:0;font:14px/1.55 'Segoe UI',Arial,sans-serif;color:#0a2236;background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{max-width:860px;margin:0 auto;padding:28px 24px 48px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:18px;margin:34px 0 10px;border-bottom:2px solid #0b7f9a;padding-bottom:4px}h3{font-size:16px;margin:0 0 4px}
.meta{color:#3d5a71;margin:0 0 18px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}
.stat{border:1px solid #a3bccf;border-radius:10px;padding:10px 14px}.stat b{display:block;font-size:24px}.stat span{color:#3d5a71;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0}
th,td{border:1px solid #c5d6e3;padding:6px 8px;text-align:left;vertical-align:top}th{background:#e4eff6}td.n{text-align:right}
.chip{display:inline-block;padding:0 9px;border-radius:99px;border:1.5px solid currentColor;font-size:11.5px;font-weight:600;white-space:nowrap}
.eval{border-left:5px solid #0b7f9a;background:#f2f7fb;border-radius:0 10px 10px 0;padding:12px 18px;margin:10px 0}
.eval .head{font-size:17px;font-weight:700;margin:0 0 6px}
.node{border:1px solid #a3bccf;border-radius:12px;padding:16px 18px;margin:16px 0;page-break-inside:avoid;break-inside:avoid}
.bars{display:grid;gap:7px;margin:10px 0 14px}
.bar{display:grid;grid-template-columns:90px 1fr 110px;gap:10px;align-items:center;font-size:12.5px}
.track{height:9px;background:#e4eff6;border-radius:99px;overflow:hidden}.track i{display:block;height:100%;background:#0b7f9a;border-radius:99px}
.bar.drv .track i{background:#b87a00}.bar.drv span:first-child{font-weight:600;color:#8a5a00}.bar span:last-child{text-align:right;color:#3d5a71}
.hash{font:12px/1.6 Consolas,Menlo,monospace;word-break:break-all}
.note{border-left:3px solid #b87a00;padding:4px 0 4px 14px;color:#3d5a71;font-size:12.5px;margin:18px 0}
ul,ol{padding-left:1.3em;margin:6px 0}li{margin-bottom:6px}
@media print{h2{page-break-after:avoid}}@media(max-width:640px){.stats{grid-template-columns:repeat(2,1fr)}}
"""


def build_report_html(result):
    rows = sorted(result["rows"], key=lambda r: r["wti"], reverse=True)
    st = result["stats"]
    ev = evaluate(result)
    chart_rows = rows[:25]
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    o = ['<!doctype html><html lang="en"><head><meta charset="utf-8">'
         '<meta name="viewport" content="width=device-width, initial-scale=1">'
         "<title>AuraFlow water quality report</title><style>%s</style></head><body><div class=\"page\">" % REPORT_CSS]
    o.append("<h1>AuraFlow water quality report</h1>")
    o.append('<p class="meta">Generated %s. %d monitoring node(s). Demonstration scores, not an official safety rating.</p>' % (_e(when), st["total"]))

    o.append('<div class="stats">')
    for value, label in ((st["total"], "Nodes"), ("%.1f%%" % st["average"], "Average score"), (st["caution"], "Need review"), (st["high"], "High risk")):
        o.append('<div class="stat"><b>%s</b><span>%s</span></div>' % (_e(value), label))
    o.append("</div>")

    # ---- evaluation
    o.append("<h2>Evaluation</h2>")
    o.append('<div class="eval" style="border-left-color:%s"><p class="head">%s</p><ul>' % (LEVEL_COLORS[ev["level"]], _e(ev["headline"])))
    for f in ev["findings"]:
        o.append("<li>%s</li>" % _e(f))
    o.append("</ul></div>")

    o.append("<h3 style=\"margin-top:18px\">Priority ranking</h3>")
    o.append("<table><thead><tr><th>#</th><th>Node</th><th>Score</th><th>Status</th><th>Main driver</th><th>Drinking check</th><th>Suggested response</th></tr></thead><tbody>")
    for i, r in enumerate(rows, 1):
        o.append('<tr><td class="n">%d</td><td>%s</td><td class="n"><b>%.1f%%</b></td><td style="color:%s;font-weight:600">%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            i, _e(r["location"]), r["wti"], LEVEL_COLORS[r["level"]], _e(r["status"]),
            _e(PARAM_NAMES[r["driver"]] if r["wti"] >= 25 else "None (all comfortable)"), _e(r["drink"]["label"]), _e(r["response"])))
    o.append("</tbody></table>")

    o.append("<h3 style=\"margin-top:18px\">Recommendations</h3><ol>")
    for rec in ev["recs"]:
        o.append("<li>%s</li>" % _e(rec))
    o.append("</ol>")

    # ---- graph
    o.append("<h2>Risk score by node</h2>")
    o.append(_chart_svg(chart_rows))
    if len(rows) > len(chart_rows):
        o.append('<p class="meta">The chart shows the %d highest-scoring nodes of %d. The tables list all of them.</p>' % (len(chart_rows), len(rows)))

    # ---- readings
    o.append("<h2>All readings</h2><table><thead><tr><th>Location</th><th>pH</th><th>Turbidity (NTU)</th><th>Metals (ppm)</th><th>BOD (mg/L)</th><th>Score</th><th>Status</th></tr></thead><tbody>")
    for r in rows:
        o.append('<tr><td>%s</td><td class="n">%s</td><td class="n">%s</td><td class="n">%s</td><td class="n">%s</td><td class="n"><b>%.1f%%</b></td><td style="color:%s;font-weight:600">%s</td></tr>' % (
            _e(r["location"]), _val("ph", r["ph"]), _val("turbidity", r["turbidity"]), _val("metals", r["metals"]), _val("bod", r["bod"]),
            r["wti"], LEVEL_COLORS[r["level"]], _e(r["status"])))
    o.append("</tbody></table>")

    # ---- per-node details
    o.append("<h2>Node details</h2>")
    for r in rows:
        d = r["drink"]
        o.append('<div class="node"><h3>%s</h3><p style="margin:0 0 6px"><span class="chip" style="color:%s">%s</span> %s</p><div class="bars">' % (
            _e(r["location"]), LEVEL_COLORS[r["level"]], _e(r["status"]), _e(_node_evaluation(r))))
        for k in PARAMS:
            drv = r["driver"] == k and r["wti"] >= 25
            o.append('<div class="bar%s"><span>%s</span><div class="track"><i style="width:%d%%"></i></div><span>%s%s</span></div>' % (
                " drv" if drv else "", PARAM_NAMES[k], round(r["penalties"][k] * 100), _val(k, r[k]), PARAM_UNITS[k]))
        o.append("</div>")
        o.append('<p style="margin:0 0 4px"><b>Drinking check: %s.</b> %s</p>' % (_e(d["label"]), _e(d["summary"])))
        o.append("<table><thead><tr><th>Measurement</th><th>Reading</th><th>Result</th><th>What it means</th></tr></thead><tbody>")
        for c in d["checks"]:
            col = LEVEL_COLORS["low"] if c["status"] == "pass" else (LEVEL_COLORS["mid"] if c["status"] == "check" else LEVEL_COLORS["high"])
            o.append('<tr><td><b>%s</b></td><td>%s</td><td><span class="chip" style="color:%s">%s</span></td><td>%s</td></tr>' % (
                _e(c["name"]), _e(c["value"]), col, _e(c["status"].capitalize()), _e(c["note"])))
        o.append("</tbody></table>")
        o.append('<p style="margin:8px 0 0"><b>%s</b></p><ol>' % ("Before you drink it" if d["level"] == "low" else "How to make it safer"))
        for t in d["steps"]:
            o.append("<li>%s</li>" % _e(t))
        o.append("</ol></div>")

    # ---- method + fingerprint
    o.append("<h2>How the score is calculated</h2>")
    o.append("<table><thead><tr><th>Measurement</th><th>Share of score</th><th>Reaches its limit at</th></tr></thead><tbody>"
             "<tr><td>pH</td><td>20%</td><td>3 units away from 7 (pH 4 or pH 10)</td></tr>"
             "<tr><td>Turbidity</td><td>25%</td><td>100 NTU</td></tr>"
             "<tr><td>Metals</td><td>35%</td><td>1 ppm</td></tr>"
             "<tr><td>BOD</td><td>20%</td><td>150 mg/L</td></tr></tbody></table>")
    o.append("<p>Each measurement is scaled from 0 to 1 against its limit, squared, weighted and summed. If any single measurement reaches its limit, the score is at least 60. Bands: under 25 low, 25 to 50 caution, over 50 high risk.</p>")
    o.append("<h2>Data fingerprint</h2>")
    o.append('<p class="hash">SHA-256: %s</p>' % _e(result["sha256"]))
    o.append('<p class="meta">A hash of the readings in this report. Change any value and it changes. It is not a tamper-proof ledger.</p>')
    o.append('<p class="note"><b>Demonstration only.</b> The score is a project-defined illustration, not an official BIS formula or a drinking-water safety certification. '
             "The drinking check covers four measurements and cannot detect germs or most chemicals. Confirm with an accredited laboratory before relying on any water for drinking.</p>")
    o.append("</div></body></html>")
    return "".join(o)

# ----------------------------------------------------------------- routes

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/analyze")
def api_analyze():
    data = request.get_json(silent=True) or {}
    raw = data.get("readings")
    if not isinstance(raw, list):
        return jsonify(error="Send a JSON body like {\"readings\": [...]}."), 400
    rows = [r for r in (clean_reading(x) for x in raw[:MAX_ROWS]) if r]
    return jsonify(analyse(rows))


@app.post("/api/report")
def api_report():
    """Receives the readings from a normal form post and returns the report as a file download."""
    try:
        raw = json.loads(request.form.get("readings", ""))
    except ValueError:
        raw = None
    if not isinstance(raw, list):
        return jsonify(error="No readings were sent."), 400
    rows = [r for r in (clean_reading(x) for x in raw[:MAX_ROWS]) if r]
    if not rows:
        return jsonify(error="Add at least one reading before creating a report."), 400
    doc = build_report_html(analyse(rows))
    return Response(
        doc,
        mimetype="text/html",
        headers={"Content-Disposition": 'attachment; filename="auraflow-report.html"'},
    )


ALIASES = {
    "location": "location", "node": "location", "name": "location",
    "ph": "ph",
    "turbidity": "turbidity", "turbidity (ntu)": "turbidity", "ntu": "turbidity",
    "metals": "metals", "metals (ppm)": "metals",
    "bod": "bod", "bod (mg/l)": "bod",
}


@app.post("/api/import")
def api_import():
    f = request.files.get("file")
    if not f:
        return jsonify(error="Choose a CSV file first."), 400
    try:
        text = f.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify(error="That file isn't UTF-8 text. Save it as CSV (UTF-8) and try again."), 400
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return jsonify(error="The CSV is empty."), 400
    mapping = {}
    for col in reader.fieldnames:
        key = ALIASES.get(str(col).strip().lower())
        if key:
            mapping[col] = key
    missing = [k for k in ("location", "ph", "turbidity", "metals", "bod") if k not in mapping.values()]
    if missing:
        return jsonify(error="Missing CSV columns: " + ", ".join(missing) + "."), 400
    rows, skipped = [], 0
    for i, rec in enumerate(reader):
        if i >= MAX_ROWS:
            break
        mapped = {mapping[c]: v for c, v in rec.items() if c in mapping}
        r = clean_reading(mapped)
        if r:
            rows.append(r)
        else:
            skipped += 1
    if not rows:
        return jsonify(error="No valid rows found. Check that pH, turbidity, metals and BOD are numbers."), 400
    return jsonify(readings=rows, skipped=skipped)


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="That file is over 1 MB."), 413


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
