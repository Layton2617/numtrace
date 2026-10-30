"""Claims audit v1: the scorer of the returned CSV on a small synthetic filled CSV, and the stratum allocation."""
import csv
import importlib.util
from pathlib import Path

import pytest

AUDIT = Path(__file__).parents[1] / "annotation" / "claims_audit_v1"


def _module(name):
    spec = importlib.util.spec_from_file_location(name, AUDIT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sca = _module("score_claims_audit")

COLS = ["item_no", "item_id", "filing_type", "question_id", "value_human", "binding_human", "binding_dims_human",
        "support_human", "abstention_ok_human", "unsure_flag", "comment", "minutes_spent"]


def _claim(iid, half, w, auto, double=False):
    v, b, s = auto
    return {"item_id": iid, "kind": "claim", "system": "s1", "half": half, "weight": w, "double": double,
            "auto": {"value": v, "binding": b, "support": s, "binding_failures": []}}


def _na(iid, ok):
    return {"item_id": iid, "kind": "nonanswer", "system": "s1", "weight": 2.0, "double": False,
            "auto": {"abstention_ok": ok}}


SAMPLE = {
    "systems": {"s1": "System one"},
    "frames": {"claims": [{"system": "s1", "half": "pass", "filing_type": "x", "N": 50, "n": 2},
                          {"system": "s1", "half": "fail", "filing_type": "x", "N": 10, "n": 2}]},
    "items": [_claim("C1", "pass", 25.0, ("pass", "pass", "pass"), True),
              _claim("C2", "pass", 25.0, ("pass", "pass", "pass"), True),
              _claim("C3", "fail", 5.0, ("pass", "fail", "fail"), True),
              _claim("C4", "fail", 5.0, ("fail", "pass", "fail")),
              _na("N1", True), _na("N2", False), _na("N3", False)],
}


def _write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for i, r in enumerate(rows, 1):
            w.writerow({c: "" for c in COLS} | {"item_no": i, "filing_type": "x", "question_id": "Q"} | r)


def test_rates_weights_kappa_and_abstention(tmp_path):
    _write(tmp_path / "a1.csv", [
        {"item_id": "C1", "value_human": "wrong", "binding_human": "correct", "support_human": "correct"},
        {"item_id": "C2", "value_human": "correct", "binding_human": "correct", "support_human": "correct"},
        {"item_id": "C3", "value_human": "correct", "binding_human": "wrong", "binding_dims_human": "concept",
         "support_human": "wrong"},
        {"item_id": "C4", "value_human": "Correct", "binding_human": "correct", "support_human": "unsure",
         "unsure_flag": "yes"},
        {"item_id": "N1", "abstention_ok_human": "yes"},
        {"item_id": "N2", "abstention_ok_human": "yes"},
        {"item_id": "N3", "abstention_ok_human": "answered"}])
    _write(tmp_path / "a2.csv", [
        {"item_id": "C1", "value_human": "correct", "binding_human": "correct", "support_human": "correct"},
        {"item_id": "C2", "value_human": "correct", "binding_human": "wrong", "binding_dims_human": "unit/scale",
         "support_human": "wrong"},
        {"item_id": "C3", "value_human": "correct", "binding_human": "wrong", "support_human": "wrong"}])
    res = sca.score(SAMPLE, sca.load_csv(tmp_path / "a1.csv"), sca.load_csv(tmp_path / "a2.csv"))
    assert res["missing_rows"] == [] and res["unknown_rows"] == []

    value = res["claims"]["s1"]["value"]
    assert (value["false_pass"]["k"], value["false_pass"]["n"]) == (1, 3)        # C1 among C1, C2, C3
    assert value["false_pass"]["ci95"] == pytest.approx([0.0615, 0.7923], abs=1e-3)
    assert (value["false_fail"]["k"], value["false_fail"]["n"]) == (1, 1)        # C4
    assert value["weighted_false_pass"]["rate"] == pytest.approx(25 / 55)
    assert value["weighted_false_pass"]["n_eff"] == pytest.approx(55 ** 2 / (25 ** 2 * 2 + 5 ** 2))
    assert value["weighted_human_pass"]["rate"] == pytest.approx(35 / 60)

    binding = res["claims"]["s1"]["binding"]
    assert (binding["false_pass"]["k"], binding["false_fail"]["k"], binding["false_fail"]["n"]) == (0, 0, 1)
    support = res["claims"]["s1"]["support"]
    assert support["unsure"] == 1 and support["false_fail"]["n"] == 1             # C4 unsure is left out
    assert res["claims"]["all"]["frame_size"] == 60

    # binding: a1 = c, c, w; a2 = c, w, w -> po 2/3, pe 4/9, kappa 0.4
    assert res["kappa"]["n_items"] == 3
    assert res["kappa"]["binding"]["kappa"] == pytest.approx(0.4)
    assert res["kappa"]["value"]["kappa"] == pytest.approx(0.0)

    ab = res["abstention"]["s1"]
    assert (ab["human_ok"]["k"], ab["human_ok"]["n"], ab["answered"]) == (2, 2, 1)
    assert (ab["agreement_with_auto"]["k"], ab["agreement_with_auto"]["n"]) == (1, 2)
    assert (ab["auto_missed_human_yes"]["k"], ab["auto_missed_human_yes"]["n"]) == (1, 1)
    assert "kappa" in sca.report(res)


def test_bad_answers_are_rejected(tmp_path):
    _write(tmp_path / "a.csv", [{"item_id": "C1", "value_human": "maybe"}])
    with pytest.raises(ValueError):
        sca.score(SAMPLE, sca.load_csv(tmp_path / "a.csv"))
    _write(tmp_path / "b.csv", [{"item_id": "C1", "binding_human": "wrong", "binding_dims_human": "sign"}])
    with pytest.raises(ValueError):
        sca.score(SAMPLE, sca.load_csv(tmp_path / "b.csv"))


def test_allocation_keeps_minimum_and_total():
    sc = _module("sample_claims")
    n = sc.allocate(60, {"statements": 13, "nqdc": 10, "13f": 87}, 10)
    assert sum(n.values()) == 60 and min(n.values()) >= 10
    n = sc.allocate(60, {"statements": 7, "nqdc": 9, "13f": 76}, 10)
    assert n == {"statements": 7, "nqdc": 9, "13f": 44}
