"""step1.run end to end with the mock model on the pilot corpus and questions (NUMTRACE_OFFLINE=1): sharding, resume,
merge, re-score; DeepSeek keys only reach the shard processes' environment; the on-disk index retrieves exactly what
the harness Corpus does; step1.fact_checker over all batches."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from harness.corpus import Corpus, SnapshotError, tokenize
from harness.injection import flagged_docs
from harness.schema import RetrievalConfig
from step1 import fact_checker as fc
from step1 import run

ROOT = Path(__file__).resolve().parents[2]
PILOT_Q = ROOT / "pilot" / "harness_inputs" / "questions.jsonl"
IDS = [json.loads(l)["id"] for l in PILOT_Q.read_text().splitlines()]
FAKE = "sk-test-not-a-real-key"


@pytest.fixture
def batch(tmp_path, monkeypatch):
    monkeypatch.setenv("NUMTRACE_OFFLINE", "1")
    (tmp_path / "data" / "statements").mkdir(parents=True)
    (tmp_path / "data" / "statements" / "questions.jsonl").write_text(PILOT_Q.read_text())
    cfg = tmp_path / "mock.toml"
    cfg.write_text(f'[run]\nsnapshot = "pilot/corpus/pilot-v1"\n'
                   f'questions = "{tmp_path}/data/{{batch}}/questions.jsonl"\nfact_check = "pilot.fact_checker:make"\n'
                   '[agent]\nmax_queries = 6\n[retrieval]\nk = 5\n'
                   '[models.deepseek]\nprovider = "mock"\nquery_per_slot = true\n')
    out = tmp_path / "out"
    return (lambda *extra: run.main(["run", "--model", "deepseek", "--batch", "statements", "--out", str(out),
                                     "--config", str(cfg), *extra])), out / "deepseek" / "statements"


def lines(p: Path, key=None):
    return [json.loads(l) if key is None else key(json.loads(l)) for l in p.read_text().splitlines()]


def asked(d: Path):
    return sorted(q for f in d.glob("runs/*/questions.jsonl") for q in lines(f, lambda o: o["id"]))


def test_shards_resume_merge_rescore(batch, monkeypatch):
    go, d = batch
    monkeypatch.setenv("DS_A", FAKE + "-a")
    monkeypatch.setenv("DS_B", FAKE + "-b")
    assert go("--shards", "3", "--limit", "5", "--key-env", "DS_A,DS_B") == 0
    assert len(list(d.glob("runs/*/*/run.json"))) == 3                     # 5 questions over 3 shards
    m = json.loads((d / "merge.json").read_text())
    assert (m["reports"], m["questions"], m["missing"]) == (5, 16, IDS[5:])

    assert go("--shards", "3", "--key-env", "DS_A,DS_B") == 0                # the other 11; the first 5 are not rerun
    assert asked(d) == sorted(IDS)
    assert lines(d / "trajectories.jsonl", lambda o: o["question_id"]) == IDS
    tids = set(lines(d / "trajectories.jsonl", lambda o: o["trajectory_id"]))
    assert lines(d / "reports.jsonl", lambda o: o["question_id"]) == IDS
    assert set(lines(d / "claims.jsonl", lambda o: o["claim"]["trajectory_id"])) <= tids
    assert set(lines(d / "slots.jsonl", lambda o: o["trajectory_id"])) == tids
    assert set(lines(d / "rescored" / "reports.jsonl", lambda o: o["trajectory_id"])) == tids

    before, n_runs = (d / "trajectories.jsonl").read_text(), len(list(d.glob("runs/*")))
    assert go("--shards", "3") == 0                                          # nothing left: no run, same merge
    assert len(list(d.glob("runs/*"))) == n_runs and (d / "trajectories.jsonl").read_text() == before
    assert not any(FAKE.encode() in f.read_bytes() for f in d.rglob("*") if f.is_file())


def test_failed_and_changed_questions_are_rerun(batch, capsys):
    go, d = batch
    assert go("--shards", "2") == 0
    # BRK-2 failed in its run (e.g. a CLI error): no trajectory, an entry under "failed"
    traj = next(f for f in d.glob("runs/*/*/trajectories.jsonl") if '"question_id":"BRK-2"' in f.read_text())
    traj.write_text("".join(l + "\n" for l in traj.read_text().splitlines() if '"question_id":"BRK-2"' not in l))
    meta = json.loads((traj.parent / "run.json").read_text())
    meta["failed"] = [{"question_id": "BRK-2", "model": "deepseek", "condition": "clean", "error": "CLIError: quota"}]
    (traj.parent / "run.json").write_text(json.dumps(meta))
    # TGT-3's text changed after it was run: its report is stale
    qf = d.parents[2] / "data" / "statements" / "questions.jsonl"
    qs = lines(qf)
    qs[2]["text"] += " Answer in dollars."
    qf.write_text("".join(json.dumps(q) + "\n" for q in qs))
    capsys.readouterr()
    run.main(["status", "--out", str(d.parents[1])])
    assert "  14/16 reports  1 failed" in capsys.readouterr().out
    assert go("--shards", "2") == 0
    new = sorted(d.glob("runs/*"))[-2:]
    assert sorted(q for r in new for q in lines(r / "questions.jsonl", lambda o: o["id"])) == ["BRK-2", "TGT-3"]
    assert lines(d / "trajectories.jsonl", lambda o: o["question_id"]) == IDS
    run.main(["status", "--out", str(d.parents[1])])
    assert "  16/16 reports  0 failed" in capsys.readouterr().out


def test_harness_index_gives_the_same_runs(batch, tmp_path):
    go, d = batch
    assert go("--shards", "2", "--limit", "8") == 0
    assert go("--shards", "2", "--limit", "8", "--index", "harness", "--out", str(tmp_path / "plain")) == 0

    def view(p: Path):
        out = {}
        for t in lines(p / "trajectories.jsonl"):
            ret = next(s for s in t["steps"] if s["stage"] == "retrieval")["output"]
            out[t["question_id"]] = ([[(h["doc_id"], h["score"]) for h in q["hits"]] for q in ret["per_query"]],
                                     [x["text"] for x in ret["docs"]], t["report"])
        return out
    assert view(d) == view(tmp_path / "plain" / "deepseek" / "statements")
    assert len(view(d)) == 8


def test_mapped_index_is_bit_identical_to_rank_bm25(tmp_path):
    snap = ROOT / "pilot" / "corpus" / "pilot-v1"
    qs = [json.loads(l) for l in PILOT_Q.read_text().splitlines()]
    queries = [f"{q['text']} {q['entity']} {q['period']}" for q in qs] + \
              [f"{s['entity']} {a} {s['period']}" for q in qs for s in q["slots"] for a in s.get("aliases", [])] + \
              ["", "the the the", "zzzunknown 2130 14326", "Target Target total debt debt 2026"]
    cfg = RetrievalConfig()
    c, m = Corpus.load(snap), run.MappedCorpus(snap, run.build_index(snap, cfg, tmp_path))
    bm, mb = c._index(cfg), m._bm25_cache[(cfg.tokenizer, cfg.k1, cfg.b)]
    assert all(np.array_equal(bm.get_scores(tokenize(q)), mb.get_scores(tokenize(q))) for q in queries)
    for k in (5, 30):
        r = RetrievalConfig(k=k)
        assert all(c.retrieve(q, r) == m.retrieve(q, r) for q in queries)
    assert (m.snapshot_id, len(m)) == (c.snapshot_id, len(c))

    bad = tmp_path / "edited"                     # content no longer matches the manifest's snapshot id
    bad.mkdir()
    shutil.copy(snap / "manifest.json", bad)
    (bad / "docs.jsonl").write_text((snap / "docs.jsonl").read_text().replace("Target", "Tarjet", 1))
    with pytest.raises(SnapshotError):
        run.build_index(bad, cfg, tmp_path)


def test_api_and_cli_configs_share_run_agent_retrieval():
    import tomllib
    cli, api = (tomllib.loads((ROOT / "step1" / "configs" / f).read_text()) for f in ("step1_cli.toml", "step1_api.toml"))
    assert all(cli[k] == api[k] for k in ("run", "agent", "retrieval"))
    assert {m["provider"] for m in api["models"].values()} == {"anthropic", "openai", "deepseek"}
    assert len({m["max_output_tokens"] for m in api["models"].values()}) == 1
    assert not any(m["send_sampling"] for m in api["models"].values())
    effort = {m["model"]: m["effort"] for m in cli["models"].values()}
    assert all(m["effort"] == effort[m["model"]] for m in api["models"].values())
    with pytest.raises(SystemExit, match="not in"):
        run.main(["run", "--model", "opus-cli", "--batch", "13f", "--out", "unused",
                  "--config", str(ROOT / "step1" / "configs" / "step1_api.toml")])


def test_key_env(monkeypatch):
    monkeypatch.setenv("DS_A", "a")
    monkeypatch.setenv("DS_B", "b")
    monkeypatch.delenv("DS_C", raising=False)
    assert [run.shard_env(["DS_A", "DS_B"], i)["DEEPSEEK_API_KEY"] for i in range(3)] == ["a", "b", "a"]
    assert run.shard_env([], 0) is None
    with pytest.raises(SystemExit, match="DS_C not set"):
        run.main(["run", "--model", "deepseek", "--batch", "13f", "--out", "unused", "--key-env", "DS_A,DS_C"])
    with pytest.raises(SystemExit, match="only for --model deepseek"):
        run.main(["run", "--model", "opus-cli", "--batch", "13f", "--out", "unused", "--key-env", "DS_A"])


def test_fact_checker_takes_every_built_batch(tmp_path, monkeypatch):
    inputs = ROOT / "pilot" / "harness_inputs"
    facts = (inputs / "store_facts.jsonl").read_text().splitlines()
    for b, part in (("statements", facts[:1000]), ("nqdc", facts[1000:])):     # 13f not built yet
        (tmp_path / b).mkdir()
        (tmp_path / b / "facts.jsonl").write_text("\n".join(part) + "\n")
    (tmp_path / "anchors.jsonl").write_text((inputs / "anchors.jsonl").read_text())
    docs = [json.loads(l) for l in (ROOT / "pilot" / "corpus" / "pilot-v1" / "docs.jsonl").read_text().splitlines()]
    docs[0]["meta"]["superseded"] = True
    (tmp_path / "snap").mkdir()
    (tmp_path / "snap" / "docs.jsonl").write_text("".join(json.dumps(d) + "\n" for d in docs))
    monkeypatch.setattr(fc, "DATA", tmp_path)
    monkeypatch.setattr(fc, "SNAPSHOT", tmp_path / "snap")
    fc.make.cache_clear()
    try:
        c = fc.make()
        assert c is fc.make()
        assert len(c.facts) == len(facts)
        assert len(c.anchors) == len((inputs / "anchors.jsonl").read_text().splitlines())
        assert c._stub.flagged_docs == flagged_docs(Corpus.load(tmp_path / "snap"))
        assert c._stub.flagged_docs[docs[0]["doc_id"]] == "superseded"
    finally:
        fc.make.cache_clear()
