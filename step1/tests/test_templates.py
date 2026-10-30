"""Step 1 templates: the 16 pilot questions come back from the families with every slot binding intact."""
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from harness.schema import Question
from step1 import templates as T

ROOT = Path(__file__).resolve().parents[2]
WINDOW = "2022Q3:2026Q2"

# The pilot entities as template inputs. Issuer quarters, amendments and executives are only those the pilot
# snapshot covers (pilot/gold/13f_rows.csv, pilot/entities.md).
PILOT_ENTITIES = [
    {"key": "TGT", "kind": "company", "name": "Target Corporation", "short": "Target", "cik": 27419,
     "fiscal_years": {"FY2021": "2022-01-29", "FY2022": "2023-01-28", "FY2023": "2024-02-03", "FY2024": "2025-02-01",
                      "FY2025": "2026-01-31"},
     "concepts": {"us-gaap:LongTermDebtCurrent": "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent",
                  "us-gaap:LongTermDebtNoncurrent": "us-gaap:LongTermDebtAndCapitalLeaseObligations",
                  "us-gaap:LongTermDebt": "us-gaap:LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"},
     "neos": {"FY2025": ["Brian C. Cornell", "Michael J. Fiddelke"]},
     # NQDC-ROLL (step1.nqdc_gold.roll_entry): an NEO whose balance this year's and the prior year's proxies print
     "rolls": {"FY2025": [{"key": "CORNELL", "neo": "Brian C. Cornell", "neo_prior": "Brian C. Cornell", "fy0": "2024"}]},
     "plan_terms": [{"key": "INVOPT", "slot": "current_investment_options",
                     "plan": "Target's officer deferred compensation plan",
                     "ask": "what investment options do participants have"}]},
    {"key": "CMCSA", "kind": "company", "name": "Comcast Corporation", "short": "Comcast", "cik": 1166691,
     "concepts": {"us-gaap:LongTermDebtNoncurrent": "us-gaap:LongTermDebtAndCapitalLeaseObligations"},
     "neos": {"FY2025": ["Brian L. Roberts", "Michael J. Cavanagh", "Jason S. Armstrong", "Thomas J. Reid",
                         "Jennifer Khoury"]},
     "plan_terms": [{"key": "SALDEF", "slot": "base_salary_deferral_limit",
                     "plan": "the Comcast Corporation 2005 Deferred Compensation Plan",
                     "ask": "how much of their base salary can participants defer"}],
     # NQDC-LIMIT (step1.nqdc_gold.limit_ask): the plan and the components its proxy states
     "plan_limits": {"FY2025": [{"plan": "the Comcast Corporation 2005 Deferred Compensation Plan",
                                 "plan_name": "Comcast Corporation 2005 Deferred Compensation Plan",
                                 "ask": "up to what percentage of base salary"}]}},
    {"key": "PSCM", "kind": "manager", "name": "Pershing Square Capital Management, L.P.", "short": "Pershing Square",
     "cik": 1336528, "amendments": {},
     "issuers": [{"key": "CMG", "name": "Chipotle", "cusip": "169656105", "periods": ["2022Q3", "2022Q4"]},
                 {"key": "HLT", "name": "Hilton", "cusip": "43300A203", "periods": ["2022Q3", "2022Q4"]}]},
    {"key": "BRK", "kind": "manager", "name": "Berkshire Hathaway Inc", "short": "Berkshire Hathaway", "cik": 1067983,
     "amendments": {"2023Q3": ["RESTATEMENT", "NEW HOLDINGS"], "2023Q4": ["NEW HOLDINGS"]},
     "issuers": [{"key": "AAPL", "name": "Apple", "cusip": "037833100", "periods": ["2022Q3", "2022Q4", "2023Q3", "2023Q4"]},
                 {"key": "CVX", "name": "Chevron", "cusip": "166764100", "periods": ["2022Q3", "2022Q4", "2023Q3", "2023Q4"]},
                 {"key": "CB", "name": "Chubb", "cusip": "H1467J104", "periods": ["2023Q3", "2023Q4"],
                  "new_holdings": ["2023Q3", "2023Q4"]}],
     # 13F-TOP / 13F-GROUP (not pilot questions): the largest positions of a quarter pair; a group's issuers
     "tops": [{"periods": ["2023Q3", "2023Q4"], "issuers": [{"name": "Apple", "cusip": "037833100"},
                                                           {"name": "Bank of America", "cusip": "060505104"},
                                                           {"name": "American Express", "cusip": "025816109"}]}],
     "group_issuers": [{"key": "AAPL", "name": "Apple", "cusip": "037833100", "periods": ["2022Q3"],
                        "params": {"group": "BRK", "members": "New England Asset Management"}}]},
]

PILOT = {
    "TGT-1": "S1-ST-LTD-TGT-FY2025", "TGT-2": "S1-ST-Q4-TGT-OCF-FY2025",
    "TGT-3": "S1-NQDC-402I-TGT-CORNELL-FY2025", "TGT-4": "S1-NQDC-FUND-TGT-INVOPT-FY2025",
    "CMCSA-1": "S1-ST-DEBT-CMCSA-FY2025", "CMCSA-2": "S1-ST-Q4-CMCSA-CAPEX-FY2025",
    "CMCSA-3": "S1-NQDC-402I-CMCSA-ROBERTS-FY2025", "CMCSA-4": "S1-NQDC-FUND-CMCSA-SALDEF-FY2025",
    "PSCM-1": "S1-13F-POS-PSCM-CMG-2022Q4", "PSCM-2": "S1-13F-UNIT-PSCM-HLT-2022Q3-2022Q4",
    "PSCM-3": "S1-13F-TOTAL-PSCM-2022Q3", "PSCM-4": "S1-13F-NOAMEND-PSCM-2022Q4",
    "BRK-1": "S1-13F-POS-BRK-AAPL-2022Q4", "BRK-2": "S1-13F-UNIT-BRK-CVX-2022Q3-2022Q4",
    "BRK-3": "S1-13F-AMEND-BRK-2023Q3", "BRK-4": "S1-13F-FIRST-BRK-CB-2023Q4",
}


def _jsonl(rel):
    return [json.loads(l) for l in (ROOT / rel).read_text().splitlines() if l.strip()]


@pytest.fixture(scope="module")
def items():
    out = T.select(T.load_families(), PILOT_ENTITIES, T.quarters(WINDOW))
    ids = [T.question_id(*it) for it in out]
    assert len(ids) == len(set(ids))
    return dict(zip(ids, out))


def test_window_quarters_fiscal_years_and_unit_boundary():
    qs = T.quarters(WINDOW)
    assert len(qs) == 16 and qs[:3] == ["2022Q3", "2022Q4", "2023Q1"] and qs[-1] == "2026Q2"
    tgt, cmcsa = PILOT_ENTITIES[:2]
    assert T.fiscal_years(tgt, qs) == T.fiscal_years(cmcsa, qs) == ["FY2022", "FY2023", "FY2024", "FY2025"]
    # fiscal 2025 ends in January 2026 for Target and in December 2025 for Comcast
    assert T.fiscal_years(tgt, T.quarters("2022Q3:2025Q4"))[-1] == "FY2024"
    assert T.fiscal_years(cmcsa, T.quarters("2022Q3:2025Q4"))[-1] == "FY2025"
    assert [(q, T.next_quarter(q)) for q in qs[:-1] if T.crosses_unit_change(q, T.next_quarter(q))] == [("2022Q3", "2022Q4")]


def test_every_family_has_candidates_and_complete_wording(items):
    assert {it[0]["id"] for it in items.values()} == {f["id"] for f in T.load_families()}
    for it in items.values():
        q = T.make_question(*it)
        assert not re.search(r"\{\w+\}", q.model_dump_json() + json.dumps(T.annotations(*it))), q.id
        assert all(s.expected_value is None for s in q.slots)
        assert Question.model_validate_json(q.model_dump_json()) == q


def test_generator_covers_the_16_pilot_questions(items):
    assert set(PILOT.values()) <= set(items)


def test_every_pilot_slot_binding_is_reproduced(items):
    pilot = {q["id"]: Question.model_validate(q) for q in _jsonl("pilot/harness_inputs/questions.jsonl")}
    gold = {(g["question_id"], g["concept"], g["period"]) for g in _jsonl("pilot/gold/pilot_gold.jsonl")}
    renamed, extra = {}, set()
    for pid, qid in PILOT.items():
        q, p = T.make_question(*items[qid]), pilot[pid]
        # the pilot labelled the nine-month YTD slot "Q3 FY2025"; Step 1 says "9M FY2025" (TEMPLATES.md section 3)
        p.slots = [s.model_copy(update={"period": "9M " + s.period[3:]}) if s.slot_id.endswith("_9m") else s
                   for s in p.slots]
        mine = {(s.concept, s.period): s for s in q.slots}
        ids = {s.slot_id: mine[(s.concept, s.period)].slot_id for s in p.slots}
        for s in p.slots:
            g = mine[(s.concept, s.period)]
            assert (g.entity, g.unit, g.scope, g.derivation, g.formula) == (s.entity, s.unit, s.scope, s.derivation,
                                                                            s.formula), (pid, s.slot_id)
            assert g.inputs == {k: ids[v] for k, v in s.inputs.items()}, (pid, s.slot_id)
            if g.slot_id != s.slot_id:
                renamed[(pid, s.slot_id)] = g.slot_id
        for g in q.slots:
            if (g.concept, g.period) not in {(s.concept, s.period) for s in p.slots}:
                assert (pid, g.concept, g.period) in gold, (pid, g.slot_id)
                extra.add((pid, g.slot_id))
    # the slot names and extra slots listed in TEMPLATES.md
    assert renamed == {("TGT-2", "q4_operating_cash_flow"): "q4_ocf", ("CMCSA-4", "benefit_obligation"): "plan_liability",
                       ("CMCSA-4", "coli_cash_surrender_value"): "coli_value", ("BRK-1", "shares_total_filer_level"): "shares"}
    assert extra == {("CMCSA-3", "registrant_contributions"), ("PSCM-3", "number_of_positions"),
                     ("BRK-3", "number_of_positions")}


def test_pilot_gold_slots_are_covered_except_one(items):
    missing = []
    for g in _jsonl("pilot/gold/pilot_gold.jsonl"):
        it = items[PILOT[g["question_id"]]]
        if g["unit"] in ("text", "date"):
            notes = {a["slot_id"]: (a["concept"], a["unit"]) for a in T.annotations(*it)}
            ok = notes.get(g["slot"]) == (g["concept"], g["unit"])
        else:
            ok = any((s.concept, s.period) == (g["concept"], g["period"]) for s in T.make_question(*it).slots)
        if not ok:
            missing.append((g["question_id"], g["slot"]))
    # PSCM-2 listed a share change its question text does not ask for; BRK-2, the same family, has none.
    # NQDC-FUND no longer asks a plan term (2026-10-02: no rule picks a meaningful term), so the pilot's
    # TGT-4 / CMCSA-4 term slots are not covered.
    assert sorted(missing) == [("CMCSA-4", "base_salary_deferral_limit"), ("PSCM-2", "share_change"),
                               ("TGT-4", "current_investment_options")]


def test_per_family_sample_is_stratified_and_seeded():
    fams, qs = {f["id"]: f for f in T.load_families()}, T.quarters(WINDOW)
    a = T.select([fams["ST-LTD"]], PILOT_ENTITIES, qs, per_family=4, seed=1)
    assert Counter(it[1]["key"] for it in a) == {"TGT": 2, "CMCSA": 2}
    assert sorted(T.year(it[2]) for it in a) == [2022, 2023, 2024, 2025]
    assert a == T.select([fams["ST-LTD"]], PILOT_ENTITIES, qs, per_family=4, seed=1)
    # 16 quarters: 2022 and 2026 have two each in this window, so half the share of a full year
    b = T.select([fams["13F-NOAMEND"]], PILOT_ENTITIES, qs, per_family=16, seed=3)
    assert Counter(T.year(it[2]) for it in b) == {2022: 2, 2023: 4, 2024: 4, 2025: 4, 2026: 2}
    assert Counter(it[1]["key"] for it in b) == {"PSCM": 8, "BRK": 8}


def test_cli_writes_harness_questions_annotations_and_counts(tmp_path, capsys):
    ents, out = tmp_path / "entities.json", tmp_path / "q.jsonl"
    ents.write_text(json.dumps(PILOT_ENTITIES))
    T.main(["--entities", str(ents), "--window", WINDOW, "--per-family", "3", "--seed", "0", "--out", str(out)])
    qs = [Question.model_validate_json(l) for l in out.read_text().splitlines()]
    # Berkshire amended 2023Q3 and 2023Q4, the only amended quarters among the inputs
    assert Counter(q.family for q in qs)["13F-AMEND"] == 2 and max(Counter(q.family for q in qs).values()) == 3
    notes = [json.loads(l) for l in (tmp_path / "q.annotate.jsonl").read_text().splitlines()]
    assert {n["question_id"] for n in notes} <= {q.id for q in qs}
    printed = capsys.readouterr().out
    assert "13F-NOAMEND" in printed and f"wrote {len(qs)} questions" in printed
