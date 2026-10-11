"""Checks the metric maths against hand-computed values, then sanity-checks the benchmark."""
import math
import evaluation as ev


def test_confusion_and_metrics():
    y = [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]
    p = [1, 1, 1, 0, 1, 0, 0, 0, 0, 0]
    assert ev.confusion(y, p) == (3, 1, 5, 1)          # tp, fp, tn, fn
    m = ev.metrics_at(y, p)
    assert math.isclose(m["precision"], 3 / 4)
    assert math.isclose(m["recall_sensitivity"], 3 / 4)
    assert math.isclose(m["specificity"], 5 / 6)
    assert math.isclose(m["f1"], 0.75)
    assert math.isclose(m["accuracy"], 0.8)


def test_auc_known_cases():
    assert ev.roc_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0      # perfect
    assert ev.roc_auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == 0.0      # inverted
    assert ev.roc_auc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) == 0.5      # all ties
    assert math.isclose(ev.roc_auc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]), 0.75)  # classic textbook case


def test_zero_division_is_safe():
    m = ev.metrics_at([0, 0, 0], [0, 0, 0])   # no positives at all
    assert m["precision"] == 0.0 and m["recall_sensitivity"] == 0.0


def test_dataset_is_reproducible_and_mixed():
    a = ev.synthetic_dataset(300, seed=1)
    b = ev.synthetic_dataset(300, seed=1)
    assert a == b
    assert 0.2 < sum(r["label"] for r in a) / len(a) < 0.7


def test_calibrated_score_beats_chance_and_v1():
    rows = ev.synthetic_dataset(800, seed=3)
    y = [r["label"] for r in rows]
    v1 = ev.roc_auc(y, [ev.MODELS["wti_v1"]["score"](r) for r in rows])
    v2 = ev.roc_auc(y, [ev.MODELS["wti_v2"]["score"](r) for r in rows])
    assert v2 > 0.9 and v2 > v1
