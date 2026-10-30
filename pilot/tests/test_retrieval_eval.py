"""Offline retrieval evaluation: evidence resolution and coverage on a toy corpus."""
from harness.corpus import Corpus
from harness.schema import AnswerSlot, Doc, Question, RetrievalConfig
from pilot.retrieval_eval import covered, evidence_groups, evaluate, queries

ACC_K, ACC_Q = "0000000001-26-000001", "0000000001-25-000002"


def _docs():
    return [
        Doc(doc_id="k-bs", source_label="10-K", accession=ACC_K, text="balance sheet current portion of long-term debt 2,130 long-term debt 14,326"),
        Doc(doc_id="k-cf", source_label="10-K", accession=ACC_K, text="cash flows operating activities 6,562"),
        Doc(doc_id="q-cf", source_label="10-Q", accession=ACC_Q, text="nine months cash flows operating activities 3,485"),
        Doc(doc_id="other", source_label="10-K", accession="x", text="unrelated 2,130 marketing text"),
        Doc(doc_id="f13", source_label="13F", accession=ACC_K, text="APPLE row 100 APPLE row 200"),
    ]


def _anchor(doc_id, concept, value, unit="USD", scale=1e6, cusip=None):
    return {"doc_id": doc_id, "scale": scale, "fact": {"concept": concept, "value": value, "unit": unit,
                                                       "dimensions": {"cusip": cusip} if cusip else {}}}


ANCHORS = [
    _anchor("k-bs", "cur", 2130e6), _anchor("k-bs", "nc", 14326e6),
    _anchor("f13", "13f:PositionShares", 100, "shares", 1.0, "037833100"),
    _anchor("f13", "13f:PositionShares", 200, "shares", 1.0, "037833100"),
]


def _slot(sid, concept, derivation="reported", inputs=None, aliases=("generic", "label")):
    return AnswerSlot(slot_id=sid, concept=concept, entity="Acme", period="FY2025", derivation=derivation,
                      inputs=inputs or {}, aliases=list(aliases))


QUESTIONS = [
    Question(id="T1", filing_type="statement", entity="Acme", period="FY2025", text="long-term debt?", target_concepts=[],
             slots=[_slot("cur", "cur"), _slot("nc", "nc"), _slot("total", "tot", "computed", {"a": "cur", "b": "nc"})]),
    Question(id="B1", filing_type="13f", entity="Acme", period="2022Q4", text="apple shares?", target_concepts=[],
             slots=[_slot("sh", "13f:Shares[037833100]")]),
]

GOLD = [
    {"question_id": "T1", "slot": "cur", "status": "reported", "unit": "USD", "value": 2130e6, "source": {"accession": ACC_K}},
    {"question_id": "T1", "slot": "nc", "status": "reported", "unit": "USD", "value": 14326e6, "source": {"accession": ACC_K}},
    {"question_id": "T1", "slot": "total", "status": "derived", "unit": "USD", "value": 16456e6, "source": {"accession": ACC_K}},
    {"question_id": "T1", "slot": "q4", "status": "derived", "unit": "USD", "value": 3077e6, "source": {"accession": ACC_K},
     "inputs": [{"accession": ACC_K, "value": 6562e6}, {"accession": ACC_Q, "value": 3485e6}]},
    {"question_id": "T1", "slot": "note", "status": "text", "unit": "text", "value": None, "source": None},
    {"question_id": "B1", "slot": "sh", "status": "reported", "unit": "shares", "value": 300, "source": {"accession": ACC_K}},
]


def test_evidence_resolution():
    ev = evidence_groups(GOLD, Corpus(_docs()), ANCHORS, QUESTIONS)
    assert ev[("T1", "cur")] == [{"k-bs"}]                    # anchor match; 'other' prints 2,130 but is another filing
    assert ev[("T1", "total")] == [{"k-bs"}, {"k-bs"}]         # not printed -> evidence of its input slots
    assert ev[("T1", "q4")] == [{"k-cf"}, {"q-cf"}]            # inputs found by printed number in their accessions
    assert ev[("T1", "note")] is None                          # text slot: unresolved
    assert ev[("B1", "sh")] == [{"f13"}]                       # filer-level 13F sum -> docs of the component rows


def test_coverage_needs_every_group():
    assert covered([{"a"}, {"b", "c"}], ["a", "c"])
    assert not covered([{"a"}, {"b"}], ["a"])


def test_queries_and_evaluate():
    q = QUESTIONS[0]
    assert len(queries(q, "Q")) == 1
    assert queries(q, "Q+slot")[1].endswith("generic FY2025") and queries(q, "Q+label")[1].endswith("label FY2025")
    rows, ctx = evaluate(Corpus(_docs()), QUESTIONS, GOLD, ANCHORS, ks=(1, 5), strategies=("Q",), cfg=RetrievalConfig())
    by = {(r["question_id"], r["slot"]): r for r in rows}
    assert by[("T1", "cur")]["hit@5"] == 1 and by[("T1", "q4")]["hit@5"] == 1   # k=5 covers the 5-doc corpus
    assert by[("T1", "note")]["resolved"] is False
    assert all(c["n_docs"] <= 5 for c in ctx)
