"""AuraFlow evaluation harness.

Treats the Water Toxicity Index (WTI) as a screening classifier and measures it
with the metrics a health-screening tool is judged on: Confusion Matrix,
Precision, Recall (Sensitivity), Specificity, F1, ROC-AUC.

Run:
    python evaluation.py                      # synthetic benchmark
    python evaluation.py --csv my_labels.csv  # real lab-labelled data
    python evaluation.py --noise 0.10 --n 2000 --seed 7

CSV format for real data: location, ph, turbidity, metals, bod, label
where label = 1 if an accredited lab judged the sample NON-POTABLE, else 0.

Positive class = "unsafe / needs intervention". A false negative (unsafe water
called fine) is the costly error in public health, so Recall is the headline
metric and the operating threshold is chosen with that in mind.

Pure standard library for the maths; matplotlib is optional (plots only).
"""

import argparse
import csv
import json
import math
import os
import random

from app import clean_reading, drinking_check, penalties, wti

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


def load_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for rec in csv.DictReader(f):
            rec = {k.strip().lower(): v for k, v in rec.items()}
            r = clean_reading(rec)
            try:
                lab = int(float(rec.get("label", "")))
            except ValueError:
                continue
            if r and lab in (0, 1):
                r["label"] = lab
                rows.append(r)
    if not rows:
        raise SystemExit("No usable rows. Need location, ph, turbidity, metals, bod, label (0/1).")
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


def _r(x):
    return round(x, 3) if isinstance(x, float) else x


def _row(name, m):
    return (f"| {name} | {_r(m['precision'])} | {_r(m['recall_sensitivity'])} | {_r(m['specificity'])} | "
            f"{_r(m['f1'])} | {_r(m['accuracy'])} | {_r(m['mcc'])} |")


def _cm(title, m):
    return [f"**{title}**\n", "| | Pred unsafe | Pred acceptable |", "|---|---|---|",
            f"| **True unsafe** | TP = {m['tp']} | FN = {m['fn']} |",
            f"| **True acceptable** | FP = {m['fp']} | TN = {m['tn']} |\n"]


def to_markdown(rep):
    d = rep["dataset"]
    L = ["# AuraFlow evaluation report\n",
         f"Samples: **{d['n']}** ({d['unsafe']} unsafe, {d['acceptable']} acceptable, prevalence {d['prevalence']}).  ",
         "Positive class = unsafe water. A false negative (unsafe water called fine) is the costly error, "
         "so Recall (sensitivity) is the headline metric.\n",
         "## ROC-AUC\n", "| Model | ROC-AUC | 95% CI |", "|---|---|---|"]
    for m in rep["models"].values():
        L.append(f"| {m['label']} | {m['roc_auc']} | {m['roc_auc_95ci'][0]} to {m['roc_auc_95ci'][1]} |")
    L += ["", "## Operating points\n",
          "| Model / threshold | Precision | Recall | Specificity | F1 | Accuracy | MCC |",
          "|---|---|---|---|---|---|---|"]
    for m in rep["models"].values():
        L.append(_row(f"{m['label']}, default band (>= 25)", m["default_band"]))
        sf = m["safety_first"]
        note = "" if sf["target_met"] else f" [recall target {sf['min_recall_target']} NOT reachable]"
        L.append(_row(f"{m['label']}, safety-first (>= {sf['threshold']}){note}", sf))
    L.append(_row("App drinking check (stricter: flags borderline too)", rep["rule_based_drinking_check"]))
    L += ["", "## Confusion matrices (rows = truth, columns = predicted)\n"]
    for m in rep["models"].values():
        L += _cm(f"{m['label']}, default band (>= 25)", m["default_band"])
        L += _cm(f"{m['label']}, safety-first (>= {m['safety_first']['threshold']})", m["safety_first"])
    L += _cm("App drinking check (stricter: flags borderline too)", rep["rule_based_drinking_check"])
    L.append("## Interpretability\n")
    for m in rep["models"].values():
        L.append(f"**{m['label']}** - single-parameter ROC-AUC: " +
                 ", ".join(f"{k} {v}" for k, v in m["interpretability"]["per_parameter_auc"].items()))
        for kind, c in m["interpretability"]["error_drivers_at_safety_first"].items():
            L.append(f"- {kind.replace('_', ' ')} by main driver: " + ", ".join(f"{k} {v}" for k, v in c.items()))
        L.append("")
    if "noise_robustness" in rep:
        L += ["## Robustness to sensor noise (ROC-AUC)\n",
              "| Sensor noise (log-sd) | " + " | ".join(m["label"] for m in rep["models"].values()) + " |",
              "|---|" + "---|" * len(rep["models"])]
        for r in rep["noise_robustness"]:
            L.append(f"| {r['sensor_noise']} | " + " | ".join(str(r[n]) for n in rep["models"]) + " |")
    L += ["", "## Limits of this evidence\n",
          "- Synthetic data. Ground truth comes from published limits and sensor error is simulated. "
          "WTI v2 is calibrated to those same limits, so its strong result shows internal consistency, "
          "not independent validation. WTI v1 was not calibrated to them, which is why it scores poorly here.",
          "- Only pH, turbidity, metals and BOD are measured. Microbial contamination is not covered, "
          "so a 'pass' is never a safety guarantee.",
          "- Replace with lab-labelled field samples (`--csv`) before making any real-world claim.\n"]
    return "\n".join(L)


def make_plots(rep, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    os.makedirs(outdir, exist_ok=True)
    paths = []

    fig, ax = plt.subplots(figsize=(5, 4.6))
    for m in rep["models"].values():
        pts = m["roc_points"]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], lw=2, label=f"{m['label']} (AUC {m['roc_auc']})")
    ax.plot([0, 1], [0, 1], "--", color="grey", lw=1, label="Chance")
    ax.set(xlabel="1 - Specificity (false positive rate)", ylabel="Sensitivity (recall)",
           title="ROC curve: unsafe-water detection", xlim=(0, 1), ylim=(0, 1.02))
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    p = os.path.join(outdir, "roc_curve.png"); fig.savefig(p, dpi=160); plt.close(fig); paths.append(p)

    for name, m in rep["models"].items():
        op = m["safety_first"]
        grid = [[op["tn"], op["fp"]], [op["fn"], op["tp"]]]
        fig, ax = plt.subplots(figsize=(4.2, 3.8))
        ax.imshow(grid, cmap="Blues")
        top = max(map(max, grid))
        for i in range(2):
            for j in range(2):
                ax.text(j, i, grid[i][j], ha="center", va="center", fontsize=16,
                        color="white" if grid[i][j] > top / 2 else "black")
        ax.set_xticks([0, 1], ["Acceptable", "Unsafe"]); ax.set_yticks([0, 1], ["Acceptable", "Unsafe"])
        ax.set(xlabel="Predicted", ylabel="Actual", title=f"{name}: confusion matrix (>= {op['threshold']})")
        fig.tight_layout()
        p = os.path.join(outdir, f"confusion_matrix_{name}.png"); fig.savefig(p, dpi=160); plt.close(fig); paths.append(p)

    fig, axes = plt.subplots(1, len(rep["models"]), figsize=(5.2 * len(rep["models"]), 3.8), sharey=True)
    axes = axes if hasattr(axes, "__len__") else [axes]
    for ax, (name, m) in zip(axes, rep["models"].items()):
        sw = m["threshold_sweep"]
        for key, lab in (("recall_sensitivity", "Recall"), ("specificity", "Specificity"),
                         ("precision", "Precision"), ("f1", "F1")):
            ax.plot([r["threshold"] for r in sw], [r[key] for r in sw], label=lab)
        ax.axvline(m["safety_first"]["threshold"], color="grey", ls=":", lw=1)
        ax.set(xlabel="Alert threshold", title=m["label"], ylim=(0, 1.02))
    axes[0].set_ylabel("Metric"); axes[0].legend(fontsize=8)
    fig.tight_layout()
    p = os.path.join(outdir, "threshold_tradeoff.png"); fig.savefig(p, dpi=160); plt.close(fig); paths.append(p)
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="lab-labelled CSV (location, ph, turbidity, metals, bod, label)")
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--noise", type=float, default=0.05, help="simulated sensor noise (log-sd)")
    ap.add_argument("--min-recall", type=float, default=0.95)
    ap.add_argument("--out", default="eval_output")
    args = ap.parse_args()

    rows = load_csv(args.csv) if args.csv else synthetic_dataset(args.n, args.seed, args.noise)
    rep = evaluate(rows, min_recall=args.min_recall, noise_n=args.n, seed=args.seed,
                   include_robustness=not args.csv)
    os.makedirs(args.out, exist_ok=True)
    slim = {**rep, "models": {k: {kk: vv for kk, vv in v.items() if kk != "roc_points"}
                              for k, v in rep["models"].items()}}
    with open(os.path.join(args.out, "metrics.json"), "w") as f:
        json.dump(slim, f, indent=2)
    with open(os.path.join(args.out, "report.md"), "w") as f:
        f.write(to_markdown(rep))
    plots = make_plots(rep, args.out)
    print(to_markdown(rep))
    print("Saved to", args.out, ":", "metrics.json, report.md,", ", ".join(os.path.basename(p) for p in plots))


if __name__ == "__main__":
    main()
