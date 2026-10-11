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

app = Flask(__name__)
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
