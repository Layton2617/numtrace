import json

import pytest

from harness.corpus import Corpus, SnapshotError, compute_snapshot_id, diff_snapshots
from harness.fixtures import toy_corpus, write_toy
from harness.schema import RetrievalConfig


def test_snapshot_id_is_content_hash(corpus):
    assert corpus.snapshot_id == toy_corpus().snapshot_id == compute_snapshot_id(corpus.docs)
    reordered = Corpus(list(reversed(corpus.docs)))
    assert reordered.snapshot_id != corpus.snapshot_id
    edited = Corpus([corpus.docs[0].model_copy(update={"text": corpus.docs[0].text + " "})] + corpus.docs[1:])
    assert edited.snapshot_id != corpus.snapshot_id


def test_save_load_roundtrip_and_tamper_detection(tmp_path, corpus):
    d = corpus.save(tmp_path / "snap")
    again = Corpus.load(d)
    assert again.snapshot_id == corpus.snapshot_id
    lines = (d / "docs.jsonl").read_text().splitlines()
    rec = json.loads(lines[0])
    rec["text"] = rec["text"].replace("1,350", "1,351")
    lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    (d / "docs.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(SnapshotError):
        Corpus.load(d)


def test_replay_gives_identical_retrieved_set(tmp_path, corpus):
    cfg = RetrievalConfig(k=4)
    q = "Gamma Capital position in Nvidia Corp Q3 2022 Q4 2022"
    first = corpus.retrieve(q, cfg)
    replay = Corpus.load(corpus.save(tmp_path / "s")).retrieve(q, cfg)
    assert [(d.doc_id, d.score, d.rank) for d in first] == [(d.doc_id, d.score, d.rank) for d in replay]
    assert len(first) == 4
    assert corpus.retrieve(q, RetrievalConfig(k=2)) != first


def test_write_toy(tmp_path):
    paths = write_toy(tmp_path)
    assert Corpus.load(paths["snapshot"]).snapshot_id == toy_corpus().snapshot_id
    assert paths["questions"].read_text().count("\n") >= 6


def test_diff_snapshots(corpus):
    other = Corpus(corpus.docs[1:])
    assert diff_snapshots(corpus, other) == {"added": [], "removed": [corpus.docs[0].doc_id], "modified": []}
