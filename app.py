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
import random

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
    html = render_template("index.html")
    # Adds the graphs (Evaluation tab + score line chart) without editing index.html.
    if "graphs.js" not in html:
        tag = '<script src="/static/graphs.js?v=2" defer></script>'
        html = html.replace("</body>", tag + "\n</body>", 1) if "</body>" in html else html + tag
    return html


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




# ======================================================================
# Evaluation (added): screening metrics, WTI v2, line-plot report page
# ======================================================================
# Positive class = unsafe water. Ground truth for the synthetic benchmark comes
# from published limits, NOT from the score; the score sees a noisy sensor
# reading. Results show the pipeline works, they are not clinical validation.

PARAMS = ("ph", "turbidity", "metals", "bod")


# ------------------------------------------------------------ reference standard

def reference_label(r):
    """Ground truth for the synthetic benchmark: 1 = unsafe, 0 = acceptable.

    Defined independently of the WTI, directly from published limits
    (BIS IS 10500 acceptable limits, WHO guideline for metals, CPCB class
    thresholds for BOD). A sample is unsafe if ANY parameter breaches its limit.
    """
    return int(
        not (6.5 <= r["ph"] <= 8.5)
        or r["turbidity"] > 5.0
        or r["metals"] > 0.01
        or r["bod"] > 3.0
    )


# ------------------------------------------------------- limit-calibrated score

# (limit, cap_ratio): the permissible limit, and how many times over the limit counts
# as the worst case (cap = 100 NTU, 1 ppm, 150 mg/L, the same worst cases as WTI v1).
# Limits match reference_label(): BIS permissible turbidity 5 NTU, 0.01 ppm metals,
# CPCB BOD 3 mg/L. Note drinking_check() in app.py is stricter (flags 1-5 NTU and
# 2-3 mg/L as 'check'), so it raises more alerts by design.
_V2 = {"turbidity": (5.0, 20.0), "metals": (0.01, 100.0), "bod": (3.0, 50.0)}


def sub_indices_v2(r):
    """Per-parameter sub-index 0..100, anchored to the guideline limits.

    Within limit:   0..24 (24 * value/limit)   -> stays in the 'low' band
    At/over limit:  25..100, log-scaled by how many times over the limit
    (AQI-style: the final score is the WORST sub-index, so one bad parameter
    cannot be averaged away by clean ones.)
    """
    def sub(ratio, cap):
        if ratio <= 1:
            return 24.0 * max(ratio, 0.0)
        return 25.0 + 75.0 * min(1.0, math.log10(ratio) / math.log10(cap))

    out = {k: sub(r[k] / lim, cap) for k, (lim, cap) in _V2.items()}
    # pH: 7.5 is the centre of the 6.5-8.5 acceptable band
    dev = abs(r["ph"] - 7.5)
    out["ph"] = sub(dev / 1.0, 6.5)
    return {k: out[k] for k in PARAMS}


def wti_v2(r):
    return round(max(sub_indices_v2(r).values()), 1)


def _subindices_v1(r):
    return {k: v * 100 for k, v in penalties(r).items()}


MODELS = {
    "wti_v1": {"score": wti, "sub": _subindices_v1, "label": "WTI v1 (current app score)"},
    "wti_v2": {"score": wti_v2, "sub": sub_indices_v2, "label": "WTI v2 (limit-calibrated)"},
}


# ------------------------------------------------------------ data generation

def _lognorm(rng, median, sigma):
    return median * math.exp(rng.gauss(0, sigma))


def synthetic_dataset(n=1000, seed=42, noise=0.05, unsafe_share=0.45):
    """Simulate field samples.

    Each sample has a TRUE water state (used for the reference label) and a
    SENSOR reading (true value + multiplicative noise) which is what the WTI
    actually sees. That gap is what makes the evaluation non-trivial: it mimics
    a low-cost sensor being compared against a lab result.
    """
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        polluted = rng.random() < unsafe_share
        if polluted:
            # polluted source: one to three parameters pushed out of range
            true = {
                "ph": rng.gauss(7.2, 1.3),
                "turbidity": _lognorm(rng, 8.0, 1.1),
                "metals": _lognorm(rng, 0.05, 1.3),
                "bod": _lognorm(rng, 4.0, 0.9),
            }
            hit = rng.sample(PARAMS, rng.randint(1, 3))
            for k in PARAMS:
                if k not in hit:
                    true[k] = {"ph": rng.gauss(7.3, 0.3), "turbidity": _lognorm(rng, 0.6, 0.5),
                               "metals": _lognorm(rng, 0.004, 0.6), "bod": _lognorm(rng, 1.2, 0.4)}[k]
        else:
            true = {
                "ph": rng.gauss(7.3, 0.35),
                "turbidity": _lognorm(rng, 0.6, 0.6),
                "metals": _lognorm(rng, 0.004, 0.7),
                "bod": _lognorm(rng, 1.2, 0.45),
            }
        label = reference_label(true)
        sensed = {
            "ph": true["ph"] + rng.gauss(0, 0.15 * noise / 0.05),
            **{k: true[k] * math.exp(rng.gauss(0, noise)) for k in ("turbidity", "metals", "bod")},
        }
        sensed["location"] = f"Node-{i:04d}"
        r = clean_reading(sensed)
        if r:
            r["label"] = label
            rows.append(r)
    return rows


# ------------------------------------------------------------------- metrics

def confusion(y_true, y_pred):
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    return tp, fp, tn, fn


def _div(a, b):
    return a / b if b else 0.0


def metrics_at(y_true, y_pred):
    tp, fp, tn, fn = confusion(y_true, y_pred)
    precision = _div(tp, tp + fp)
    recall = _div(tp, tp + fn)
    specificity = _div(tn, tn + fp)
    denom = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision,
        "recall_sensitivity": recall,
        "specificity": specificity,
        "f1": _div(2 * precision * recall, precision + recall),
        "accuracy": _div(tp + tn, tp + tn + fp + fn),
        "balanced_accuracy": (recall + specificity) / 2,
        "mcc": _div(tp * tn - fp * fn, denom),
        "false_negative_rate": _div(fn, tp + fn),
        "false_positive_rate": _div(fp, fp + tn),
    }


def roc_auc(y_true, scores):
    """AUC via the rank-sum (Mann-Whitney U) identity, ties handled by mid-rank."""
    pairs = sorted(zip(scores, y_true))
    n_pos = sum(y_true)
    n_neg = len(y_true) - n_pos
    if not n_pos or not n_neg:
        return float("nan")
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        mid = (i + j) / 2 + 1
        rank_sum += mid * sum(1 for k in range(i, j + 1) if pairs[k][1] == 1)
        i = j + 1
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def roc_curve(y_true, scores):
    thresholds = sorted(set(scores), reverse=True)
    pts = [(0.0, 0.0)]
    n_pos = sum(y_true)
    n_neg = len(y_true) - n_pos
    for t in thresholds:
        pred = [1 if s >= t else 0 for s in scores]
        tp, fp, _, _ = confusion(y_true, pred)
        pts.append((_div(fp, n_neg), _div(tp, n_pos)))
    pts.append((1.0, 1.0))
    return pts


def bootstrap_ci(y_true, scores, stat, n_boot=400, seed=0):
    rng = random.Random(seed)
    n = len(y_true)
    vals = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        yt = [y_true[i] for i in idx]
        sc = [scores[i] for i in idx]
        v = stat(yt, sc)
        if not math.isnan(v):
            vals.append(v)
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def threshold_sweep(y_true, scores, grid=range(1, 100)):
    out = []
    for t in grid:
        m = metrics_at(y_true, [1 if s >= t else 0 for s in scores])
        out.append({"threshold": t, **{k: m[k] for k in
                    ("precision", "recall_sensitivity", "specificity", "f1", "tp", "fp", "tn", "fn")}})
    return out


def youden_threshold(sweep):
    return max(sweep, key=lambda r: r["recall_sensitivity"] + r["specificity"] - 1)["threshold"]


def safety_first_threshold(sweep, min_recall=0.95):
    """Highest threshold that still keeps Recall >= min_recall (fewest false alarms
    subject to missing at most 5% of unsafe samples)."""
    ok = [r for r in sweep if r["recall_sensitivity"] >= min_recall]
    if ok:
        return max(ok, key=lambda r: r["threshold"])["threshold"], True
    return sweep[0]["threshold"], False  # target unreachable at any threshold


# ----------------------------------------------------------- interpretability

def per_parameter_auc(rows, sub):
    """How well each measurement alone separates unsafe from acceptable water."""
    y = [r["label"] for r in rows]
    return {k: round(roc_auc(y, [sub(r)[k] for r in rows]), 4) for k in PARAMS}


def error_analysis(rows, scores, threshold, sub):
    """Which parameter was the main driver in each kind of error?"""
    out = {"false_negatives": {k: 0 for k in PARAMS}, "false_positives": {k: 0 for k in PARAMS}}
    for r, s in zip(rows, scores):
        if int(s >= threshold) == r["label"]:
            continue
        p = sub(r)
        out["false_negatives" if r["label"] == 1 else "false_positives"][max(p, key=p.get)] += 1
    return out


def noise_robustness(n, seed, levels=(0.0, 0.05, 0.10, 0.20, 0.40)):
    res = []
    for lv in levels:
        data = synthetic_dataset(n, seed, noise=max(lv, 1e-9))
        y = [r["label"] for r in data]
        row = {"sensor_noise": lv}
        for name, m in MODELS.items():
            row[name] = round(roc_auc(y, [m["score"](r) for r in data]), 4)
        res.append(row)
    return res


# -------------------------------------------------------------------- driver

def evaluate_model(rows, name, min_recall=0.95, n_boot=400):
    m = MODELS[name]
    y = [r["label"] for r in rows]
    scores = [m["score"](r) for r in rows]
    sweep = threshold_sweep(y, scores)
    default_t = 25  # score >= 25 leaves the 'low risk' band
    chosen, target_met = safety_first_threshold(sweep, min_recall)
    auc = roc_auc(y, scores)
    lo, hi = bootstrap_ci(y, scores, roc_auc, n_boot)
    rlo, rhi = bootstrap_ci(
        y, scores,
        lambda yt, sc: metrics_at(yt, [1 if s >= chosen else 0 for s in sc])["recall_sensitivity"], n_boot)
    return {
        "label": m["label"],
        "roc_auc": round(auc, 4),
        "roc_auc_95ci": [round(lo, 4), round(hi, 4)],
        "default_band": {"threshold": default_t,
                         **metrics_at(y, [1 if s >= default_t else 0 for s in scores])},
        "safety_first": {"threshold": chosen, "min_recall_target": min_recall, "target_met": target_met,
                         **metrics_at(y, [1 if s >= chosen else 0 for s in scores]),
                         "recall_95ci": [round(rlo, 4), round(rhi, 4)]},
        "youden_threshold": youden_threshold(sweep),
        "threshold_sweep": sweep,
        "interpretability": {
            "per_parameter_auc": per_parameter_auc(rows, m["sub"]),
            "error_drivers_at_safety_first": error_analysis(rows, scores, chosen, m["sub"]),
        },
        "roc_points": roc_curve(y, scores),
    }


def evaluate(rows, min_recall=0.95, n_boot=400, noise_n=1000, seed=42, include_robustness=True):
    y = [r["label"] for r in rows]
    drink_pred = [0 if drinking_check(r)["level"] == "low" else 1 for r in rows]
    rep = {
        "dataset": {"n": len(rows), "unsafe": sum(y), "acceptable": len(y) - sum(y),
                    "prevalence": round(sum(y) / len(y), 3)},
        "models": {name: evaluate_model(rows, name, min_recall, n_boot) for name in MODELS},
        "rule_based_drinking_check": metrics_at(y, drink_pred),
    }
    if include_robustness:
        rep["noise_robustness"] = noise_robustness(noise_n, seed)
    return rep



# --- report page ---
def _plot(series, xlab, ylab, xmax=1.0, vline=None):
    W, H, L, R, T, B = 560, 320, 52, 14, 14, 44
    pw, ph = W - L - R, H - T - B
    X = lambda x: L + x / xmax * pw
    Y = lambda y: T + ph - y * ph
    s = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{ylab} vs {xlab}">']
    for i in range(6):
        g = i / 5
        s.append(f'<line x1="{L}" x2="{W-R}" y1="{Y(g):.1f}" y2="{Y(g):.1f}" class="grid"/>')
        s.append(f'<text x="{L-8}" y="{Y(g)+4:.1f}" text-anchor="end" class="tick">{g:.1f}</text>')
        s.append(f'<text x="{X(g*xmax):.1f}" y="{H-B+16}" text-anchor="middle" class="tick">{g*xmax:g}</text>')
    if vline is not None:
        s.append(f'<line x1="{X(vline):.1f}" x2="{X(vline):.1f}" y1="{T}" y2="{T+ph}" class="vl"/>')
    for _, pts, cls in series:
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(pts))
        s.append(f'<path d="{d}" class="ln {cls}"/>')
    s.append(f'<text x="{L+pw/2}" y="{H-6}" text-anchor="middle" class="axl">{xlab}</text>')
    s.append(f'<text x="14" y="{T+ph/2}" text-anchor="middle" class="axl" transform="rotate(-90 14 {T+ph/2})">{ylab}</text></svg>')
    leg = "".join(f'<span><i class="sw {c}"></i>{n}</span>' for n, _, c in series)
    return "".join(s) + f'<div class="leg">{leg}</div>'


def _row_html(name, m):
    return f"<tr><td>{name}</td>" + "".join(f"<td>{m[k]:.3f}</td>" for k in
            ("precision", "recall_sensitivity", "specificity", "f1")) + "</tr>"


def _cm_html(title, m):
    return (f'<div><h4>{title}</h4><table class="cm"><tr><th></th><th>Pred unsafe</th><th>Pred OK</th></tr>'
            f'<tr><th>Unsafe</th><td class="g">TP {m["tp"]}</td><td class="r">FN {m["fn"]}</td></tr>'
            f'<tr><th>OK</th><td class="r">FP {m["fp"]}</td><td class="g">TN {m["tn"]}</td></tr></table></div>')


_REPORT_CSS = """:root{box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px);
--bg:#f7f8fa;--card:#fff;--tx:#1b2430;--mut:#5b6675;--bd:#dde2e9;--a:#d9480f;--b:#1971c2;--c:#868e96;--d:#2f9e44;--e:#9c36b5;--ok:#e6f4ea;--bad:#fdecea}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#12161c;--card:#1a2029;--tx:#e8ecf1;--mut:#9aa6b5;--bd:#2c3542;--a:#ff8c5a;--b:#5aa9ff;--c:#8a94a0;--d:#5fd07a;--e:#d08cf0;--ok:#1d3326;--bad:#3a2121}}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:880px;margin:0 auto;padding:20px 16px 48px}h1{font-size:1.7rem;margin:.2em 0}h2{font-size:1.15rem;margin:1.6em 0 .5em}h4{margin:.2em 0 .4em;font-size:.9rem}
.sub{color:var(--mut)}.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;margin:12px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.kpi b{display:block;font-size:1.6rem}.kpi span{color:var(--mut);font-size:.85rem}
.warn{border-left:4px solid var(--a)}svg{width:100%;height:auto}.grid{stroke:var(--bd)}.tick{fill:var(--mut);font-size:11px}.axl{fill:var(--mut);font-size:12px}
.vl{stroke:var(--c);stroke-dasharray:4 4}.ln{fill:none;stroke-width:2.5}.a{stroke:var(--a);background:var(--a)}.b{stroke:var(--b);background:var(--b)}
.c{stroke:var(--c);background:var(--c)}.d{stroke:var(--d);background:var(--d)}.e{stroke:var(--e);background:var(--e)}.c.ln{stroke-dasharray:5 5;stroke-width:1.5}
.leg{display:flex;flex-wrap:wrap;gap:14px;font-size:.85rem;color:var(--mut)}.sw{display:inline-block;width:14px;height:4px;border-radius:2px;margin-right:6px;vertical-align:middle}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.92rem}th,td{padding:7px 10px;border-bottom:1px solid var(--bd);text-align:left}
.cms{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.cm td,.cm th{text-align:center}.g{background:var(--ok)}.r{background:var(--bad)}"""


def _build_page(rep, source):
    M = rep["models"]; v1, v2 = M["wti_v1"], M["wti_v2"]; d = rep["dataset"]
    roc = _plot([(f"WTI v1 (current), AUC {v1['roc_auc']:.3f}", v1["roc_points"], "a"),
                (f"WTI v2 (calibrated), AUC {v2['roc_auc']:.3f}", v2["roc_points"], "b"),
                ("Chance", [(0, 0), (1, 1)], "c")], "False positive rate (1 - specificity)", "Sensitivity (recall)")
    sw = lambda k: [(r["threshold"] / 100, r[k]) for r in v2["threshold_sweep"]]
    tr = _plot([("Recall", sw("recall_sensitivity"), "a"), ("Specificity", sw("specificity"), "b"),
               ("Precision", sw("precision"), "d"), ("F1", sw("f1"), "e")],
              "Alert threshold (WTI / 100)", "Metric", vline=v2["safety_first"]["threshold"] / 100)
    noise = ""
    if "noise_robustness" in rep:
        nz = rep["noise_robustness"]
        noise = ("<h2>Robustness to sensor noise</h2><div class='card'>" + _plot(
            [("WTI v1", [(r["sensor_noise"], r["wti_v1"]) for r in nz], "a"),
             ("WTI v2", [(r["sensor_noise"], r["wti_v2"]) for r in nz], "b")],
            "Sensor noise (log-sd)", "ROC-AUC", xmax=0.4) + "</div>")
    sf = v2["safety_first"]
    table = "".join([_row_html("WTI v1, alert >= 25", v1["default_band"]), _row_html("WTI v2, alert >= 25", v2["default_band"]),
                     _row_html(f"WTI v2, safety-first >= {sf['threshold']}", sf),
                     _row_html("App drinking check (stricter)", rep["rule_based_drinking_check"])])
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>AuraFlow Evaluation Report</title>
<style>{_REPORT_CSS}</style></head><body><main>
<h1>AuraFlow evaluation report</h1>
<p class="sub">Water Toxicity Index as a screening classifier. Positive class = unsafe water. {d['n']} samples ({d['unsafe']} unsafe, {d['acceptable']} acceptable). Data: {source}.</p>
<div class="kpis"><div class="card kpi"><b>{v2['roc_auc']}</b><span>ROC-AUC, WTI v2 (95% CI {v2['roc_auc_95ci'][0]} to {v2['roc_auc_95ci'][1]})</span></div>
<div class="card kpi"><b>{v2['default_band']['recall_sensitivity']:.3f}</b><span>Recall, v2 at &gt;= 25</span></div>
<div class="card kpi"><b>{v2['default_band']['specificity']:.3f}</b><span>Specificity, v2 at &gt;= 25</span></div>
<div class="card kpi"><b>{v1['default_band']['recall_sensitivity']:.3f}</b><span>Recall, current v1 at &gt;= 25</span></div></div>
<div class="card warn"><b>Read with care.</b> Synthetic data unless a lab-labelled CSV was supplied; v2 is calibrated to the same limits as the ground truth, so a strong result shows consistency, not independent validation. No bacteria are tested, so a pass never means "safe".</div>
<h2>ROC curve</h2><div class="card">{roc}</div>
<h2>Threshold trade-off (WTI v2)</h2><div class="card">{tr}<p class="sub">Dashed line = safety-first threshold (highest alert level keeping recall at or above {sf['min_recall_target']}).</p></div>
{noise}<h2>Metrics</h2><div class="card scroll"><table><tr><th>Model</th><th>Precision</th><th>Recall</th><th>Specificity</th><th>F1</th></tr>{table}</table></div>
<h2>Confusion matrices</h2><div class="cms card">{_cm_html("WTI v1, >= 25", v1["default_band"])}{_cm_html("WTI v2, >= 25", v2["default_band"])}{_cm_html("App drinking check", rep["rule_based_drinking_check"])}</div>
</main></body></html>"""



_EVAL_CACHE = {}


def _eval_args():
    def num(name, default, lo, hi, cast):
        try:
            return min(max(cast(request.args.get(name, default)), lo), hi)
        except (TypeError, ValueError):
            return default
    return num("n", 1000, 100, 2000, int), num("seed", 42, 0, 10**6, int), num("noise", 0.05, 0.001, 0.5, float)


def _evaluation(n, seed, noise):
    key = (n, seed, noise)
    if key not in _EVAL_CACHE:
        if len(_EVAL_CACHE) >= 20:
            _EVAL_CACHE.clear()
        _EVAL_CACHE[key] = evaluate(synthetic_dataset(n, seed, noise), n_boot=100, noise_n=n, seed=seed)
    return _EVAL_CACHE[key]


def _thin(points, limit=200):
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[int(i * step)] for i in range(limit)] + [points[-1]]


def _slim(rep):
    out = dict(rep)
    out["models"] = {
        k: {**v, "roc_points": [[round(x, 4), round(y, 4)] for x, y in _thin(v["roc_points"])]}
        for k, v in rep["models"].items()
    }
    return out


@app.get("/evaluation")
def evaluation_page():
    n, seed, noise = _eval_args()
    return _build_page(_evaluation(n, seed, noise), f"synthetic, n={n}, seed={seed}, sensor noise={noise}")


@app.get("/api/evaluate")
def api_evaluate():
    return jsonify(_slim(_evaluation(*_eval_args())))


@app.post("/api/evaluate")
def api_evaluate_labelled():
    """Evaluate on real data: CSV with location, ph, turbidity, metals, bod, label (1 = lab says unsafe)."""
    f = request.files.get("file")
    if not f:
        return jsonify(error="Choose a CSV file first."), 400
    try:
        text = f.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify(error="That file isn't UTF-8 text."), 400
    rows = []
    for i, rec in enumerate(csv.DictReader(io.StringIO(text))):
        if i >= MAX_ROWS:
            break
        mapped = {ALIASES[str(c).strip().lower()]: v for c, v in rec.items() if str(c).strip().lower() in ALIASES}
        r = clean_reading(mapped)
        lab = {str(k).strip().lower(): v for k, v in rec.items()}.get("label", "")
        try:
            lab = int(float(lab))
        except (TypeError, ValueError):
            continue
        if r and lab in (0, 1):
            r["label"] = lab
            rows.append(r)
    ys = {r["label"] for r in rows}
    if len(rows) < 20 or ys != {0, 1}:
        return jsonify(error="Need at least 20 valid rows with both label 0 and label 1."), 400
    return jsonify(_slim(evaluate(rows, n_boot=100, include_robustness=False)))


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
    
