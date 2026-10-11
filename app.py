"""AuraFlow: a small public web app for scoring simulated water-quality readings.

The server is stateless. Each visitor's readings live in their own browser
(localStorage) and are sent here only to be scored, so one visitor never sees
another visitor's data.
"""
import csv
import hashlib
import io
import json
import math
import os

from flask import Flask, jsonify, render_template, request

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
