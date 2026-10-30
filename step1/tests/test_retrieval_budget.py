"""Retrieval-budget study: evidence for a split 13F table, exact top-k, queries, option C copy (toy snapshot)."""
import json

from harness.corpus import Corpus, compute_snapshot_id
from harness.schema import AnswerSlot, Doc, Question, RetrievalConfig
from step1.retrieval_budget import (DocMeta, Retriever, evidence, header_variant, queries, score,
                                    table_accessions)
from step1.run import build_index

A4, A1 = "0000000001-25-000001", "0000000001-25-000002"      # 2024Q4 table (2 chunks), 2025Q1 table (3 chunks)


def _row(name, cusip, value):
    return f"{name} COM (CUSIP {cusip}) | Value: {value:,} | Shares or principal (SH): 10 | Filed: 2025-05-15"


def _f13(acc, period, sec, rows, head=True):
    text = (f"Form 13F-HR filed by Acme Capital LP (CIK 0000000001)\nPeriod of report: {period}. Accession: {acc}.\n"
            "Information table\n" if head else "") + "\n".join(rows)
    return Doc(doc_id=f"acme-{acc}-s{sec:03d}", source_label="SEC EDGAR 13F-HR", accession=acc, form_type="13F-HR",
               entity="Acme Capital LP", period=period, text=text, meta={"section": sec})


DOCS = [
    _f13(A4, "2024-12-31", 1, [_row("ALPHA", "AAAAAAAA1", 900)]),
    _f13(A4, "2024-12-31", 2, [_row("BETA", "BBBBBBBB2", 50)], head=False),
    _f13(A1, "2025-03-31", 1, [_row("GAMMA", "CCCCCCCC3", 10)]),
    _f13(A1, "2025-03-31", 2, [_row("ALPHA", "AAAAAAAA1", 800)], head=False),
    _f13(A1, "2025-03-31", 3, [_row("BETA", "BBBBBBBB2", 70)], head=False),
    Doc(doc_id="other-s001", source_label="SEC EDGAR 10-K", accession="x", text="Acme annual report alpha beta"),
]


def _snapshot(tmp_path, docs=DOCS):
    d = tmp_path / "snap"
    d.mkdir()
    (d / "docs.jsonl").write_text("".join(x.model_dump_json() + "\n" for x in docs))
    (d / "manifest.json").write_text(json.dumps({"snapshot_id": compute_snapshot_id(docs), "name": "toy"}))
    return d


def _question():
    slot = lambda sid, per, cusip: AnswerSlot(slot_id=sid, concept=f"13f:Value[{cusip}]", entity="Acme Capital LP",
                                             period=per, aliases=["Alpha position"])
    return Question(id="T", filing_type="13f", entity="Acme Capital LP", period="2024Q4-2025Q1", family="13F-TOP",
                    text="Acme's largest position?", target_concepts=[],
                    slots=[slot("top1_q4_value", "2024Q4", "AAAAAAAA1"), slot("top1_q1_value", "2025Q1", "AAAAAAAA1")])


def _gold():
    merge = lambda acc: [{"accession": "0000000001-25-000009", "action": "original"},
                         {"accession": acc, "action": "restatement_replaced"}]
    return [{"question_id": "T", "slot": "top1_q4_value", "concept": "13f:Value[AAAAAAAA1]", "entity": "Acme Capital LP",
             "period": "2024Q4", "value": 900.0, "unit": "USD", "status": "derived", "cusip": "AAAAAAAA1",
             "rows": [{"accession": A4}], "merge": merge(A4)},
            {"question_id": "T", "slot": "top1_q1_value", "concept": "13f:Value[AAAAAAAA1]", "entity": "Acme Capital LP",
             "period": "2025Q1", "value": 800.0, "unit": "USD", "status": "derived", "cusip": "AAAAAAAA1",
             "rows": [{"accession": A1}], "merge": merge(A1)},
            {"question_id": "T", "slot": "top_positions", "status": "text", "unit": "text", "value": None}]


def test_table_accessions_follow_restatements():
    m = [{"accession": "a", "action": "original"}, {"accession": "b", "action": "new_holdings_added"},
         {"accession": "c", "action": "restatement_replaced"}, {"accession": "d", "action": "new_holdings_added"}]
    assert table_accessions(m) == ["c", "d"]


def test_top_needs_whole_tables_minimal_only_the_rows(tmp_path):
    meta = DocMeta(_snapshot(tmp_path))
    e = evidence(_question(), _gold(), {}, meta)
    assert {next(iter(g)) for g in e["minimal"]} == {f"acme-{A4}-s001", f"acme-{A1}-s002"}
    assert {next(iter(g)) for g in e["needed"]} == {d.doc_id for d in DOCS[:5]}
    assert e["unresolved"] == []
    got = {f"acme-{A4}-s001", f"acme-{A1}-s001", f"acme-{A1}-s002"}
    assert score(e["minimal"], got) == (1.0, 1)
    assert score(e["needed"], got) == (0.6, 0)


def test_retriever_matches_corpus_retrieve(tmp_path):
    snap = _snapshot(tmp_path)
    idx = build_index(snap, RetrievalConfig(), tmp_path / "bm25")
    r, c = Retriever(idx, kmax=4), Corpus(DOCS)
    for q in ("Acme Capital alpha", "BETA CUSIP Value", "nothing matches xyz"):
        assert [DOCS[i].doc_id for i in r.top(q)] == \
               [d.doc_id for d in c.retrieve(q, RetrievalConfig(k=4))]
    assert r.union(["Acme Capital alpha", "BETA CUSIP Value"], 2) == list(dict.fromkeys(r.top("Acme Capital alpha")[:2] + r.top("BETA CUSIP Value")[:2]))


def test_queries_slot_vs_visible():
    q = _question()
    slot, vis = queries(q, "Q+slot"), queries(q, "Q+visible")
    assert slot[0] == vis[0] == "Acme's largest position? Acme Capital LP 2024Q4-2025Q1"
    assert "Acme Capital LP Alpha position 2024Q4" in slot and not any("Alpha" in x for x in vis)
    assert len(set(slot)) == len(slot) and len(queries(q, "Q+slot", 2)) == 2


def test_header_variant_puts_the_filer_in_continuation_chunks(tmp_path):
    out = header_variant(_snapshot(tmp_path), tmp_path / "var")
    docs = [Doc.model_validate_json(l) for l in (out / "docs.jsonl").read_text().splitlines()]
    assert docs[1].text.startswith("Form 13F-HR filed by Acme Capital LP") and "BETA" in docs[1].text
    assert docs[0].text == DOCS[0].text and docs[5].text == DOCS[5].text
    assert json.loads((out / "manifest.json").read_text())["snapshot_id"] == compute_snapshot_id(docs)
    build_index(out, RetrievalConfig(), tmp_path / "bm25v")       # the copy is a valid snapshot for the index


def test_cost_rows_are_relative_to_k5_q6():
    from step1.retrieval_budget import KS, QS, cost_rows
    rows = [{"strategy": "s", "k": k, "Q": Q, "family": fam, "chars": 4000 * k * (Q // 6) * (2 if fam == "13F-POS" else 1)}
            for k in KS for Q in QS for fam in ("ST-EPS", "13F-POS")]
    prices = {"opus-cli": 4.0, "gpt-cli": 10.0, "deepseek": 0.3}
    base = next(c for c in cost_rows(rows, "s", prices) if c["k"] == 5 and c["Q"] == 6)
    assert base["delta_per_q"] == 0 and base["opus-cli_mtok"] == 38.0 and base["deepseek_usd_peak"] == 12.0
    c10 = next(c for c in cost_rows(rows, "s", prices) if c["k"] == 10 and c["Q"] == 6)
    assert c10["delta_per_q"] == (10 + 20) / 2 * 1000 - (5 + 10) / 2 * 1000
    dense = next(c for c in cost_rows(rows, "s", prices, chars_per_token_13f=2.0) if c["k"] == 5 and c["Q"] == 6)
    assert dense["ctx_tokens_per_q"] == (5000 + 20000) / 2
