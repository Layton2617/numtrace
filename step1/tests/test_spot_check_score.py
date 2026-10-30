"""step1.spot_check_score on a hand-scored fixture."""
import math
from pathlib import Path

import pytest

from step1 import spot_check_score as ss

FIXTURE = Path(__file__).parent / "fixtures" / "spot_check_filled.csv"


@pytest.fixture(scope="module")
def res():
    return ss.score(ss.read(FIXTURE))


def acc(res, group, key):
    x = res["accuracy"][group][key]
    return x["k"], x["n"]


def test_wilson():
    lo, hi = ss.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-4) and hi == pytest.approx(0.9433, abs=1e-4)
    assert ss.wilson(10, 10)[1] == pytest.approx(1.0)
    assert all(math.isnan(x) for x in ss.wilson(0, 0))


def test_kappa():
    assert ss.kappa([(1, 1), (0, 0)]) == 1.0
    assert ss.kappa([(1, 0), (0, 1)]) == -1.0
    assert math.isnan(ss.kappa([(1, 1), (1, 1)]))


def test_label():
    assert ss.label(" Yes ") == 1 and ss.label("\u9519") == 0 and ss.label("na") is None and ss.label("") is None
    with pytest.raises(ValueError):
        ss.label("maybe")


def test_appendix_only_rows_ignored(res):
    assert "batch:nqdc" not in res["accuracy"]


def test_accuracy(res):
    assert acc(res, "all", "A_slot") == (5, 6)       # Q3 s2 unjudged by A
    assert acc(res, "all", "A_question") == (2, 3)   # Q3 skipped
    assert acc(res, "all", "B_slot") == (5, 7)
    assert acc(res, "all", "B_question") == (2, 4)
    assert acc(res, "all", "AB_slot") == (4, 6)
    assert acc(res, "batch:13f", "A_slot") == (2, 2)
    assert acc(res, "family:ST-EPS", "B_slot") == (2, 4)
    x = res["accuracy"]["all"]["B_slot"]
    assert (x["lo"], x["hi"]) == pytest.approx(ss.wilson(5, 7))


def test_kappa_between_reviewers(res):
    k = res["kappa"]
    assert k["value"]["n"] == 6 and k["value"]["kappa"] == pytest.approx(1.0)
    assert k["binding"]["kappa"] == pytest.approx(0.0)
    assert k["slot"]["kappa"] == pytest.approx(8 / 14)
    assert res["disagreements"] == ["Q1 s2"]
