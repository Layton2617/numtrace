"""step1.spot_check sampling on the repo's step1/data (no snapshot needed)."""
from collections import Counter

import pytest

from step1 import spot_check as sc


@pytest.fixture(scope="module")
def data():
    return sc.load()


@pytest.fixture(scope="module")
def picks(data):
    return sc.sample(data)


@pytest.fixture(scope="module")
def forced(data):
    return sc.sample(data, forced=True)


def test_deterministic(data, picks, forced):
    assert sc.sample(data) == picks
    assert sc.sample(sc.load()) == picks
    assert sc.sample(data, seed=sc.SEED + 1) != picks
    assert sc.sample(data, forced=True) == forced


def test_allocation(data):
    alloc = sc.allocation(data)
    assert sum(a["n"] for a in alloc.values()) == sc.N_TOTAL
    assert {b: a["n"] for b, a in alloc.items()} == {"statements": 44, "13f": 82, "nqdc": 74}
    for b, a in alloc.items():
        fams = a["families"]
        assert sum(f["n"] for f in fams.values()) == a["n"]
        assert all(f["n"] >= sc.FAMILY_FLOOR for f in fams.values())
        # proportional apart from the floor: within one of the share (a family trimmed to pay for a raised one), or raised
        assert all(abs(f["n"] - f["target"]) <= 1 or f["n"] == sc.FAMILY_FLOOR for f in fams.values()), b


def test_random_sample_matches_allocation(data, picks):
    alloc = sc.allocation(data)
    for b in sc.BATCHES:
        qs = data[b]["questions"]
        assert len(picks[b]) == len(set(picks[b])) == alloc[b]["n"]
        assert Counter(qs[i]["family"] for i in picks[b]) == {f: v["n"] for f, v in alloc[b]["families"].items()}


def test_random_sample_has_no_forced_items(data, picks):
    """Within a family the draw is rng.sample over the sorted ids, nothing else."""
    import random
    alloc = sc.allocation(data)
    for b in sc.BATCHES:
        qs = data[b]["questions"]
        rng = random.Random(sc.SEED)
        want = []
        for f in sorted(alloc[b]["families"]):
            want += rng.sample(sorted(i for i in qs if qs[i]["family"] == f), alloc[b]["families"][f]["n"])
        assert sorted(want) == sorted(picks[b])


def test_forced_sizes_and_strata(data, forced):
    for b in sc.BATCHES:
        assert len(forced[b]) == len(set(forced[b])) == sc.N_FORCED[b]
        fams = {data[b]["questions"][i]["family"] for i in forced[b]}
        assert fams == {q["family"] for q in data[b]["questions"].values()}


def test_forced_rules_met(data, forced):
    for b in sc.BATCHES:
        for name, pool, k in sc.rules(b, data[b]["questions"], data[b]["gold"]):
            assert sum(i in pool for i in forced[b]) >= k, (b, name)


def test_forced_inclusions(data, forced):
    st = set(forced["statements"])
    assert {"S1-ST-EPS-WMT-FY2025Q1", "S1-ST-Q4-META-CAPEX-FY2023"} <= st
    assert len(st & {"S1-ST-LTD-JNJ-FY2024", "S1-ST-LTD-PG-FY2026", "S1-ST-DEBT-INTC-FY2024",
                     "S1-ST-DEBT-XOM-FY2024"}) >= 2

    gold13 = data["13f"]["gold"]
    multi = [i for i in forced["13f"] if any(g["status"] == "derived" and (len(g.get("rows") or []) > 1
                                                                            or len(g.get("merge") or []) > 1)
                                             for g in gold13[i])]
    assert len(multi) >= 6
    assert any(i.startswith("S1-13F-GROUP-") for i in forced["13f"])

    nq, gnq = forced["nqdc"], data["nqdc"]["gold"]
    roll = [i for i in nq if i.startswith("S1-NQDC-ROLL-")]
    assert len(roll) >= 4
    assert sum(sc.ticker(i) in sc.SUMMED_PLANS and any(g.get("formula") == "sum over plans" for g in gnq[i])
               for i in roll) >= 2
    assert any(g["slot"] == "reconciliation_difference" and g["value"] for i in roll for g in gnq[i])
    limit = [i for i in nq if i.startswith("S1-NQDC-LIMIT-")]
    assert len(limit) >= 3 and any(sc.ticker(i) == "LLY" for i in limit)
    assert sum(i.startswith("S1-NQDC-FUND-") for i in nq) >= 2
    assert all(i.startswith("S1-NQDC-402I-") for i in nq if i not in roll + limit and "-FUND-" not in i)


def test_quotas_respect_floor():
    q = sc.quotas({"a": 60, "b": 39, "c": 12, "d": 9}, 12, {"c": 3})
    assert sum(q.values()) == 12 and q["c"] == 3 and all(v >= 1 for v in q.values())


def test_value_text_shows_dates_and_text():
    assert sc.value_text({"value": "2026-06-30"}) == "2026-06-30"
    assert sc.value_text({"value": 1234567.0}) == "1234567"
    assert sc.value_text({"value": None, "gold_text": "unfunded"}) == "unfunded"


def test_matches_at_precision():
    assert sc.matches_at_precision("5,190", 1e6, 5190000000.0)
    assert sc.matches_at_precision("(0.21)", 1.0, -0.21)
    assert sc.matches_at_precision("—", 1.0, 0.0)
    assert not sc.matches_at_precision("0.62", 1.0, 0.21)
    assert not sc.matches_at_precision("5,191", 1e6, 5190000000.0)
