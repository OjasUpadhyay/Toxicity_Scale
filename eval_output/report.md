# AuraFlow evaluation report

Samples: **1000** (454 unsafe, 546 acceptable, prevalence 0.454).  
Positive class = unsafe water. A false negative (unsafe water called fine) is the costly error, so Recall (sensitivity) is the headline metric.

## ROC-AUC

| Model | ROC-AUC | 95% CI |
|---|---|---|
| WTI v1 (current app score) | 0.6902 | 0.6569 to 0.7228 |
| WTI v2 (limit-calibrated) | 0.9979 | 0.9965 to 0.9991 |

## Operating points

| Model / threshold | Precision | Recall | Specificity | F1 | Accuracy | MCC |
|---|---|---|---|---|---|---|
| WTI v1 (current app score), default band (>= 25) | 1.0 | 0.099 | 1.0 | 0.18 | 0.591 | 0.238 |
| WTI v1 (current app score), safety-first (>= 1) [recall target 0.95 NOT reachable] | 0.544 | 0.694 | 0.516 | 0.61 | 0.597 | 0.212 |
| WTI v2 (limit-calibrated), default band (>= 25) | 0.97 | 0.985 | 0.974 | 0.977 | 0.979 | 0.958 |
| WTI v2 (limit-calibrated), safety-first (>= 26) | 0.982 | 0.96 | 0.985 | 0.971 | 0.974 | 0.948 |
| App drinking check (stricter: flags borderline too) | 0.71 | 0.991 | 0.663 | 0.827 | 0.812 | 0.676 |

## Confusion matrices (rows = truth, columns = predicted)

**WTI v1 (current app score), default band (>= 25)**

| | Pred unsafe | Pred acceptable |
|---|---|---|
| **True unsafe** | TP = 45 | FN = 409 |
| **True acceptable** | FP = 0 | TN = 546 |

**WTI v1 (current app score), safety-first (>= 1)**

| | Pred unsafe | Pred acceptable |
|---|---|---|
| **True unsafe** | TP = 315 | FN = 139 |
| **True acceptable** | FP = 264 | TN = 282 |

**WTI v2 (limit-calibrated), default band (>= 25)**

| | Pred unsafe | Pred acceptable |
|---|---|---|
| **True unsafe** | TP = 447 | FN = 7 |
| **True acceptable** | FP = 14 | TN = 532 |

**WTI v2 (limit-calibrated), safety-first (>= 26)**

| | Pred unsafe | Pred acceptable |
|---|---|---|
| **True unsafe** | TP = 436 | FN = 18 |
| **True acceptable** | FP = 8 | TN = 538 |

**App drinking check (stricter: flags borderline too)**

| | Pred unsafe | Pred acceptable |
|---|---|---|
| **True unsafe** | TP = 450 | FN = 4 |
| **True acceptable** | FP = 184 | TN = 362 |

## Interpretability

**WTI v1 (current app score)** - single-parameter ROC-AUC: ph 0.5969, turbidity 0.7049, metals 0.7975, bod 0.6777
- false negatives by main driver: ph 86, turbidity 24, metals 19, bod 10
- false positives by main driver: ph 264, turbidity 0, metals 0, bod 0

**WTI v2 (limit-calibrated)** - single-parameter ROC-AUC: ph 0.6519, turbidity 0.7049, metals 0.7975, bod 0.6777
- false negatives by main driver: ph 3, turbidity 2, metals 8, bod 5
- false positives by main driver: ph 6, turbidity 1, metals 0, bod 1

## Robustness to sensor noise (ROC-AUC)

| Sensor noise (log-sd) | WTI v1 (current app score) | WTI v2 (limit-calibrated) |
|---|---|---|
| 0.0 | 0.707 | 1.0 |
| 0.05 | 0.6902 | 0.9979 |
| 0.1 | 0.6691 | 0.9897 |
| 0.2 | 0.6294 | 0.9474 |
| 0.4 | 0.5704 | 0.8338 |

## Limits of this evidence

- Synthetic data. Ground truth comes from published limits and sensor error is simulated. WTI v2 is calibrated to those same limits, so its strong result shows internal consistency, not independent validation. WTI v1 was not calibrated to them, which is why it scores poorly here.
- Only pH, turbidity, metals and BOD are measured. Microbial contamination is not covered, so a 'pass' is never a safety guarantee.
- Replace with lab-labelled field samples (`--csv`) before making any real-world claim.
