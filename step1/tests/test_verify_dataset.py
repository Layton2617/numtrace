"""step1.verify_dataset on a tiny repo in tmp_path: one company, one 13F manager, one question per batch."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

from data_layer.facts import Fact
from data_layer.harness_adapter import Anchor
from step1 import verify_dataset as v

ACC_K, ACC_13F, ACC_P = "0000000001-23-000001", "0000000002-25-000001", "0000000001-24-000002"


def jl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def anchor(entity, concept, period, value, scope=None, unit="USD") -> dict:
    f = Fact(entity="0000000001", concept=concept, value=value, unit=unit, period_end="2022-12-31")
    return json.loads(Anchor(doc_id="d", start=0, end=1, label="x", printed_value=value, scale=1.0, entity=entity,
                             period=period, scope=scope, fact=f).model_dump_json())


@pytest.fixture
def repo(tmp_path):
    snap = tmp_path / v.SNAP
    snap.mkdir(parents=True)
    (snap / "docs.jsonl").write_text('{"doc_id": "d"}\n')
    (snap / "manifest.json").write_text(json.dumps({
        "snapshot_id": "snap-test", "docs_sha256": hashlib.sha256(b'{"doc_id": "d"}\n').hexdigest(),
        "freeze_date": "2026-10-02", "window": "2022Q3:2026Q2", "n_docs": 1, "n_filings": 3}))
    jl(snap / "sources.jsonl", [{"accession": ACC_K, "filed": "2023-02-01"},
                                {"accession": ACC_13F, "filed": "2025-05-15"},
                                {"accession": ACC_P, "filed": "2024-03-01"}])
    (tmp_path / "step1/entities").mkdir(parents=True)
    (tmp_path / "step1/entities/companies.json").write_text(json.dumps([{
        "name": "Acme Inc.", "fiscal_years": {"FY2022": {"end": "2022-12-31"}, "FY2021": {"end": "2021-12-31"}},
        "fiscal_quarters": {"FY2023Q1": {"end": "2023-03-31"}}}]))
    d = tmp_path / "step1/data"
    jl(d / "statements/questions.jsonl", [
        {"id": "S-1", "entity": "Acme Inc.", "period": "FY2022", "family": "ST-DEBT",
         "text": "How much debt did Acme have at the end of fiscal 2022?"},
        {"id": "S-2", "entity": "Acme Inc.", "period": "FY2023Q1", "family": "ST-EPS", "text": "Acme EPS in Q1?"}])
    jl(d / "statements/gold.jsonl", [
        {"question_id": "S-1", "slot": "debt", "entity": "Acme Inc.", "concept": "us-gaap:Debt", "period": "FY2022",
         "value": 5190000000.0, "status": "reported", "printed": "5,190", "source": {"accession": ACC_K}},
        {"question_id": "S-2", "slot": "eps", "entity": "Acme Inc.", "concept": "us-gaap:EPS", "period": "FY2023Q1",
         "value": 1.53, "status": "reported", "source": {"accession": ACC_K}}])
    jl(d / "statements/facts.jsonl", [{"accession": ACC_K}])
    jl(d / "13f/questions.jsonl", [
        {"id": "F-1", "entity": "Fund LP", "period": "2025Q1", "family": "13F-GROUP",
         "text": "How many shares of Visa did the Fund group hold on March 31, 2025?"}])
    jl(d / "13f/gold.jsonl", [
        {"question_id": "F-1", "slot": "group_shares", "entity": "Fund LP", "concept": "13f:Shares[X]",
         "period": "2025Q1", "value": 3270470, "status": "reported", "scope": "group:FUND",
         "source": [{"accession": ACC_13F}], "rows": [{"accession": ACC_13F, "filed": "2025-05-15"}]}])
    jl(d / "13f/facts.jsonl", [{"accession": f"{ACC_13F},{ACC_13F}"}])
    jl(d / "nqdc/questions.jsonl", [
        {"id": "N-1", "entity": "Acme Inc.", "period": "FY2023Q1", "family": "NQDC-NEWFAMILY",
         "text": "What did Jane Roe's account earn?"}])
    jl(d / "nqdc/gold.jsonl", [
        {"question_id": "N-1", "slot": "earnings", "entity": "Acme Inc.", "concept": "nqdc:AggregateEarnings",
         "period": "FY2023Q1", "value": 63366.0, "status": "reported", "scope": "NEO:Jane Roe",
         "source": {"accession": ACC_P}},
        {"question_id": "N-1", "slot": "plan", "entity": "Acme Inc.", "concept": "nqdc:PlanType", "period": "FY2023Q1",
         "value": None, "status": "text", "gold_text": "excess 401(k) plan", "source": None}])
    jl(d / "anchors.jsonl", [anchor("Acme Inc.", "us-gaap:Debt", "FY2022", 5190000000.0),
                             anchor("Acme Inc.", "us-gaap:EPS", "FY2023Q1", 1.53, unit="USD/shares"),
                             anchor("Fund LP", "13f:Shares[X]", "2025Q1", 3270470, "filer", unit="shares"),
                             anchor("Acme Inc.", "nqdc:AggregateEarnings", "FY2023Q1", 63366.0, "NEO:Jane Roe")])
    return tmp_path


def edit(path: Path, i: int, **kw) -> None:
    rows = v.read_jsonl(path)
    rows[i].update(kw)
    jl(path, rows)


def test_clean_repo_passes_and_counts(repo):
    _, r = v.verify(repo)
    for b in v.BATCHES:
        assert all(r[b][c][0] for c in ("sources", "time", "counts", "anchors", "leakage")), (b, r[b])
        assert r[b]["determinism"][0] is None
    assert r["statements"]["family"] == {"ST-DEBT": 1, "ST-EPS": 1}
    assert r["statements"]["year"] == {2022: 1, 2023: 1}
    assert r["nqdc"]["family"] == {"NQDC-NEWFAMILY": 1}        # families come from the data
    assert r["nqdc"]["status"] == {"reported": 1, "text": 1}
    assert r["13f"]["entity"] == {"Fund LP": 1}


def test_missing_accession_and_changed_docs_fail_sources(repo):
    edit(repo / "step1/data/nqdc/gold.jsonl", 0, source={"accession": "0000000009-24-000009"})
    _, r = v.verify(repo)
    ok, notes = r["nqdc"]["sources"]
    assert ok is False and "  missing 0000000009-24-000009" in notes
    (repo / v.SNAP / "docs.jsonl").write_text('{"doc_id": "e"}\n')
    _, r = v.verify(repo)
    assert r["statements"]["sources"][0] is False


def test_time_window_and_freeze(repo):
    edit(repo / "step1/data/statements/questions.jsonl", 0, period="FY2021")
    edit(repo / "step1/data/13f/questions.jsonl", 0, period="2026Q2-2026Q3")
    edit(repo / "step1/data/13f/gold.jsonl", 0, rows=[{"accession": ACC_13F, "filed": "2026-10-05"}])
    _, r = v.verify(repo)
    assert r["statements"]["time"][0] is False and any("S-1" in n for n in r["statements"]["time"][1])
    ok, notes = r["13f"]["time"]
    assert ok is False and any("2026-09-30" in n for n in notes) and any("2026-10-05" in n for n in notes)
    assert r["nqdc"]["time"][0] is True


def test_counts_catch_orphans(repo):
    edit(repo / "step1/data/nqdc/gold.jsonl", 1, question_id="N-9")
    _, r = v.verify(repo)
    assert r["nqdc"]["counts"][0] is False and "  gold for unknown question N-9" in r["nqdc"]["counts"][1]


def test_anchors_scope(repo):
    # a group total is anchored by the one filer row printing it; an NQDC slot needs the same executive
    edit(repo / "step1/data/nqdc/gold.jsonl", 0, scope="NEO:John Doe")
    _, r = v.verify(repo)
    assert r["13f"]["anchors"][0] is True
    ok, notes = r["nqdc"]["anchors"]
    assert ok is None and notes[0] == "1/1 unanchored" and "N-1 earnings" in notes[1]


def test_leakage(repo):
    edit(repo / "step1/data/statements/questions.jsonl", 0, text=f"Acme debt was 5,190 (see {ACC_K}) in 2022?")
    edit(repo / "step1/data/nqdc/questions.jsonl", 0, text="Is the Excess 401(k) Plan funded?")
    _, r = v.verify(repo)
    hits = r["statements"]["leakage"][1][1:]
    assert any(ACC_K in h for h in hits) and any("'5,190'" in h for h in hits)
    assert r["nqdc"]["leakage"][0] is False
    assert not v.leaks("Q1 2023 EPS was 11.53", "1.53") and v.leaks("EPS of 1.53.", "1.53")
    assert v.value_strings({"value": 3.0}) == set() and v.value_strings({"value": 5190.0}) == {"5190", "5,190"}


def test_rebuild_compares_md5(repo, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rb")
    gold = repo / "step1/data/statements/gold.jsonl"
    lines = gold.read_text().splitlines()
    write = lambda dest, text: [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write(sys.argv[2])",
                                str(dest), text]
    cmds = {"statements": write(tmp / "statements" / "gold.jsonl", gold.read_text()),
            "13f": write(tmp / "13f" / "gold.jsonl", "changed\n"),
            "nqdc": [sys.executable, "-c", "raise SystemExit('boom')"],
            "anchors": write(tmp / "anchors" / "gold.jsonl", "\n".join(lines[::-1]) + "\n")}
    r = v.rebuild(repo, tmp, cmds)
    assert r["statements"][0] is True and r["statements"][1][0] == "1/1 files md5-identical"
    assert r["13f"][0] is False and r["13f"][1][1].startswith("  DIFF  step1/data/13f/gold.jsonl")
    assert r["nqdc"][0] is False and "boom" in "\n".join(r["nqdc"][1])
    assert r["anchors"][1][1].startswith("  NOT IN REPO  step1/data/anchors/gold.jsonl")


def test_rebuild_reports_reordered_lines(repo, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rb")
    anchors = (repo / "step1/data/anchors.jsonl").read_text().splitlines()
    cmd = [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write(sys.argv[2])",
           str(tmp / "anchors" / "anchors.jsonl"), "\n".join(anchors[::-1]) + "\n"]
    ok, notes = v.rebuild(repo, tmp, {"anchors": cmd})["anchors"]
    assert ok is False and notes[1].startswith("  DIFF (same lines, other order)  step1/data/anchors.jsonl")


def test_render(repo, tmp_path_factory):
    ctx, r = v.verify(repo)
    md = v.render(repo, ctx, r, False, v.md5_manifest(repo))
    for s in ("## Version", "snap-test", "## Check results", "| anchors | PASS", "## How to reproduce", "## Known issues",
              "NQDC-NEWFAMILY", "`step1/data/anchors.jsonl`"):
        assert s in md, s
    assert "SKIP" in md
