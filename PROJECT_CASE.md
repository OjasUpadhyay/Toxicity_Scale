# AuraFlow: evaluation, SDG 3 alignment and deployment case

## 0. Adding this to your repo

Copy these into the repo root (do **not** replace your `app.py`):

```
evaluation.py
tests/test_evaluation.py
.github/workflows/ci.yml
PROJECT_CASE.md          (this file)
eval_output/             (generated report, metrics.json, plots)
```

Run it:

```bash
python evaluation.py                       # synthetic benchmark -> eval_output/
python evaluation.py --csv lab_data.csv    # real lab-labelled data (best for the viva)
pip install matplotlib                     # only needed for the PNG plots; keep it out of production requirements
```

Optional live endpoint. Add this to `app.py` (the import is inside the function on purpose: `evaluation.py` imports from `app.py`, so a top-level import would be circular):

```python
_EVAL_CACHE = {}

@app.get("/api/evaluate")
def api_evaluate():
    if "rep" not in _EVAL_CACHE:
        from evaluation import synthetic_dataset, evaluate
        rep = evaluate(synthetic_dataset(500, seed=42), n_boot=100, noise_n=500)
        for m in rep["models"].values():
            m.pop("roc_points", None)
        _EVAL_CACHE["rep"] = rep
    return jsonify(_EVAL_CACHE["rep"])
```

It computes once (about 0.4 s) and serves from memory after that. If you want a page for it, render `rep["models"]["wti_v2"]` as a table and the confusion matrix on the front end.

## 1. Quantitative evaluation

### Framing

The WTI is evaluated as a **screening classifier**. Positive class = unsafe water. A false negative (unsafe water called fine) is the costly error, so Recall/Sensitivity is the headline metric and the operating threshold is chosen to keep Recall at or above 0.95.

Ground truth (synthetic benchmark) is defined independently of the score, from published limits: pH outside 6.5 to 8.5, turbidity above 5 NTU, metals above 0.01 ppm, or BOD above 3 mg/L. The score sees a **noisy sensor reading**, not the true value, to mimic a low-cost sensor compared against a lab result.

### Results (1000 samples, seed 42, 5% sensor noise)

| Model | ROC-AUC | Precision | Recall | Specificity | F1 |
|---|---|---|---|---|---|
| WTI v1 (current app score), alert at >= 25 | 0.690 | 1.000 | **0.099** | 1.000 | 0.180 |
| WTI v2 (limit-calibrated), alert at >= 25 | **0.998** | 0.970 | 0.985 | 0.974 | 0.977 |
| WTI v2, safety-first (>= 26) | 0.998 | 0.982 | 0.960 | 0.985 | 0.971 |
| App `drinking_check` (stricter, flags borderline) | n/a (3-level) | 0.710 | 0.991 | 0.663 | 0.827 |

Confusion matrix, WTI v2 at >= 25: TP 447, FN 7, FP 14, TN 532.
Full tables, bootstrap 95% CIs, all confusion matrices and the noise-robustness table are in `eval_output/report.md`.

### The key finding (own this in the viva)

The evaluation exposed a real design flaw in v1. Its caps are set for extreme pollution (100 NTU, 1 ppm, 150 mg/L) and its penalties are squared, so water that is clearly over a drinking limit scores near zero. At the published "low risk" band (< 25) it **misses about 90% of unsafe samples**. v1 is a pollution-severity index, not a drinking-water screen.

v2 anchors each parameter to its guideline limit, scales the excess on a log axis, and takes the worst sub-index (the AQI approach), so one bad parameter cannot be averaged away. Recall rises from 0.10 to 0.985 without sacrificing specificity.

Your `app.py` is unchanged. v2 lives in `evaluation.py`. If you adopt it, replace `wti()` and keep `classify()` as is, because v2 is built to the same 25/50 bands.

### Interpretability

- Score structure is transparent: sub-index per parameter, final score = worst one, and the app already reports the `driver`.
- Single-parameter ROC-AUC shows which measurement carries the signal (metals highest at 0.80, pH lowest at 0.65 for v2 and 0.60 for v1, because pH has a two-sided band).
- Error analysis shows what drives mistakes. At the safety-first threshold, v2's few misses come mostly from metals (8 of 18) and BOD (5), where a reading just under the limit was pushed lower by sensor noise; its false alarms are almost all pH (6 of 8). v1's false alarms at its lowest usable threshold are all pH (264), because any small pH deviation scores above zero while real exceedances in other parameters score too low. That contrast is the diagnostic evidence for why v1 needed recalibrating.
- Noise robustness (ROC-AUC, v2): 1.000 with no noise, 0.998 at 5%, 0.990 at 10%, 0.947 at 20%, 0.834 at 40% multiplicative sensor error. It degrades gradually rather than collapsing, and v1 is below v2 at every level (0.71 down to 0.57).

### What these numbers do NOT show

- The data is synthetic and v2 is calibrated to the same limits as the reference label. That demonstrates internal consistency and a working pipeline, **not independent validation**. Say this before the examiner does.
- Microbial contamination (coliforms, E. coli), the main cause of waterborne illness, is not measured. A pass means "passes four chemical/physical checks", never "safe".
- To make a real claim: collect 100+ field samples, have an accredited lab label each one, run `--csv`, and report the CI.

## 2. Alignment with SDG 3 (Good Health and Well-being)

| Link | How AuraFlow contributes |
|---|---|
| **Target 3.9**: reduce illness and death from hazardous chemicals and water pollution | Flags heavy-metal, turbidity and organic-pollution exceedances before people drink the water |
| Supports **SDG 6** (6.1 safe drinking water, 6.3 water quality) | Same measurements, same limits (BIS IS 10500, WHO, CPCB) |
| Prevention, not treatment | Intervenes upstream of the clinic: treatment steps are suggested per failed parameter (settle/filter, pH correction, lab test for metals, disinfect) |

**Measurable indicators you can report** (define them now, measure them in a pilot):
- Recall of unsafe samples against lab results (target >= 0.95)
- Share of flagged sources that received a confirmatory lab test
- Time from reading to alert
- Number of households or sources screened per week

Avoid claiming a health outcome (cases averted) without field data. State the measurable indicator and the mechanism instead.

## 3. Clinical and real-world feasibility

- **Positioning:** an environmental screening and triage aid, not a medical device and not a replacement for laboratory testing. The app already says this ("passes these checks", never "safe"). Keep that wording everywhere.
- **Pathway to validation:** (1) lab-label a small field set, (2) compare v1/v2 with confidence intervals, (3) pilot with a water-testing programme or a college/NGO partner, (4) refine thresholds per region.
- **Hardware reality:** pH, turbidity and TDS probes are cheap; low-cost metal sensing is weak. That is why the app tells users to confirm metals with a lab. Treat the metals input as lab-entered or test-kit-entered until sensors are validated.
- **Failure modes to discuss:** sensor drift and calibration, missing bacteria data, out-of-range inputs (the app clamps and validates), over-reliance by users.

## 4. Healthcare accessibility

Already in the design: runs in a browser on any phone, no account, no install, CSV import for batch results.

Gaps and cheap fixes:
- Language: add Tamil, Hindi and other regional-language labels for the status, summary and steps.
- Low connectivity: add a service worker so the page loads offline (scoring is simple enough to port to client-side JS as a fallback).
- Literacy: colour plus icon plus one-line action ("Don't drink; boil and test"), already close with the low/mid/high levels.
- Cost: free hosting tier plus no database keeps running cost near zero.

## 5. Ethics and patient privacy

**What the design already gets right** (point this out):
- Stateless server. Readings live in the user's own browser (`localStorage`) and are sent only to be scored.
- No accounts, no database, so there is nothing to breach centrally.
- Security headers (`nosniff`, `same-origin` referrer), 1 MB upload cap, 500-row cap, input validation and clamping.

**What to address:**
- The `location` field is free text. Users may type a home address, which is personal data. Add a hint ("use a node ID, not an address") and keep it out of logs.
- If you ever add storage or sharing, India's **Digital Personal Data Protection Act, 2023** applies to personal data: minimise collection, get consent, state the purpose.
- **Fairness and harm:** a false negative can lead someone to drink unsafe water; a false positive can cause needless alarm or cost. That asymmetry is why the threshold is safety-first, and why the UI must carry the "not tested for germs" caveat.
- **Transparency:** show the formula, the limits used and their source. Label scores as a project-defined index, not an official BIS formula (the code already does).
- **Reproducibility:** the SHA-256 of the analysed readings in `analyse()` gives an audit trail without storing data.

## 6. Deployment readiness

| Area | Status | To do |
|---|---|---|
| Hosting | `Procfile`, `render.yaml`, `/healthz` present | Confirm HTTPS-only and a pinned Python version |
| Testing | Added `tests/test_evaluation.py` (metrics checked against hand-computed values) | Add tests for `wti`, `classify`, `drinking_check`, `/api/analyze`, `/api/import` |
| CI | Added `.github/workflows/ci.yml` | Add a lint step if you like |
| Dependencies | `requirements.txt` | Pin exact versions; keep `matplotlib` out of the production file |
| Abuse limits | Upload and row caps present | Add rate limiting (e.g. Flask-Limiter) on the public API |
| Observability | `/healthz` | Log request counts and errors, never readings or locations |
| Validation | Synthetic only | Lab-labelled pilot data (section 1) |

## 7. Viva preparation

**Q: Why these metrics instead of just accuracy?**
Prevalence of unsafe water varies by place, and the errors are not equal. Recall tells me how much unsafe water I miss, Specificity how many safe sources I wrongly condemn, Precision how trustworthy an alert is, F1 balances precision and recall, and ROC-AUC measures ranking quality independent of any threshold.

**Q: Your v1 score recalls only 10%. Why ship it?**
That is exactly what the evaluation was for. v1 measures pollution severity on a wide scale; at the published band it under-flags drinking-water exceedances. I diagnosed the cause (caps and squared penalties), built a limit-calibrated v2, and showed recall go from 0.10 to 0.985 at the same band.

**Q: Isn't v2 circular, since it uses the same limits as your ground truth?**
Partly, yes, and I say so. On synthetic data it demonstrates consistency and robustness to sensor noise. Independent validation needs lab-labelled field samples, which is the next step and is supported by `--csv`.

**Q: How did you choose the threshold?**
Safety-first: the highest threshold that still keeps Recall >= 0.95, because missing unsafe water is the costly error. The sweep plot shows the trade-off and Youden's J is reported for comparison.

**Q: What is the confusion matrix telling you?**
Of 454 unsafe samples v2 caught 447 and missed 7; of 546 acceptable it wrongly flagged 14. Both kinds of error come from samples sitting close to a limit, where 5% sensor noise can tip a reading to the wrong side.

**Q: Does it detect bacteria?**
No, and that is the biggest limitation. Every output says "passes these checks", never "safe", and the steps always include a lab test with coliform testing.

**Q: How does it support SDG 3?**
Target 3.9 (illness from water pollution), via early screening and per-parameter treatment guidance, with indicators defined (recall vs lab, flagged-to-tested ratio, time to alert).

**Q: Privacy?**
Stateless server, data stays in the browser, no accounts. The remaining risk is free-text locations; mitigations are in section 5.

**Q: What would you do with another month?**
Collect lab-labelled samples, add a bacteria test-kit input (H2S strip), multilingual UI, rate limiting, and a drift-check against calibration standards.

**Q: How do you know your metric code is right?**
Unit tests compare it to hand-computed confusion matrices and known AUC cases (perfect, inverted, all ties, the classic 0.75 example).
