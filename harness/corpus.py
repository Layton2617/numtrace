"""Frozen corpus snapshots and deterministic BM25 retrieval.

A snapshot is a directory holding

    docs.jsonl      one ``Doc`` per line, in a fixed order (order is part of the content)
    manifest.json   {snapshot_id, parent_snapshot_id, n_docs, intervention, presentation_order}

``snapshot_id`` is the SHA-256 of the canonical JSON of the ordered doc list, so the same
documents in the same order always give the same id. Replay = same snapshot id + same
``RetrievalConfig`` -> identical retrieved set (ties broken by corpus position).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

from rank_bm25 import BM25Okapi

from .schema import Doc, Intervention, RetrievalConfig, RetrievedDoc, canonical_json, sha256_of

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str, tokenizer: str = "lower_alnum_v1") -> list[str]:
    if tokenizer != "lower_alnum_v1":
        raise ValueError(f"unknown tokenizer {tokenizer!r}")
    return _TOKEN_RE.findall(text.lower())


def compute_snapshot_id(docs: Iterable[Doc]) -> str:
    return "snap-" + sha256_of([d.model_dump(mode="json") for d in docs])[:16]


class SnapshotError(RuntimeError):
    pass


class Corpus:
    """An immutable, content-addressed set of documents."""

    def __init__(
        self,
        docs: list[Doc],
        *,
        parent_snapshot_id: Optional[str] = None,
        intervention: Optional[Intervention] = None,
        presentation_order: Optional[list[str]] = None,
        bm25_reference: Optional[dict] = None,
    ) -> None:
        ids = [d.doc_id for d in docs]
        if len(ids) != len(set(ids)):
            raise SnapshotError("duplicate doc_id in corpus")
        self.docs: list[Doc] = list(docs)
        self.parent_snapshot_id = parent_snapshot_id
        self.intervention = intervention
        self.presentation_order = list(presentation_order or [])
        self.snapshot_id = compute_snapshot_id(self.docs)
        self._by_id = {d.doc_id: d for d in self.docs}
        self._bm25_cache: dict[tuple, BM25Okapi] = {}
        self.bm25_reference: Optional[dict] = bm25_reference

    def __len__(self) -> int:
        return len(self.docs)

    def __contains__(self, doc_id: str) -> bool:
        return doc_id in self._by_id

    def get(self, doc_id: str) -> Doc:
        return self._by_id[doc_id]

    def save(self, directory: str | Path) -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "docs.jsonl", "w", encoding="utf-8") as f:
            for doc in self.docs:
                f.write(canonical_json(doc) + "\n")
        if self.bm25_reference is not None:
            (d / "bm25_reference.json").write_text(json.dumps(self.bm25_reference, sort_keys=True))
        manifest = {
            "snapshot_id": self.snapshot_id,
            "parent_snapshot_id": self.parent_snapshot_id,
            "n_docs": len(self.docs),
            "intervention": self.intervention.model_dump(mode="json") if self.intervention else None,
            "presentation_order": self.presentation_order,
        }
        (d / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
        return d

    @classmethod
    def load(cls, directory: str | Path, *, verify: bool = True) -> "Corpus":
        d = Path(directory)
        docs = [Doc.model_validate_json(line) for line in (d / "docs.jsonl").read_text().splitlines() if line.strip()]
        manifest = {}
        if (d / "manifest.json").exists():
            manifest = json.loads((d / "manifest.json").read_text())
        iv = manifest.get("intervention")
        corpus = cls(
            docs,
            parent_snapshot_id=manifest.get("parent_snapshot_id"),
            intervention=Intervention.model_validate(iv) if iv else None,
            presentation_order=manifest.get("presentation_order") or [],
            bm25_reference=json.loads((d / "bm25_reference.json").read_text())
            if (d / "bm25_reference.json").exists() else None,
        )
        if verify and manifest.get("snapshot_id") and manifest["snapshot_id"] != corpus.snapshot_id:
            raise SnapshotError(
                f"snapshot hash mismatch in {d}: manifest {manifest['snapshot_id']} != content {corpus.snapshot_id}"
            )
        return corpus

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "Corpus":
        docs = [Doc.model_validate_json(line) for line in Path(path).read_text().splitlines() if line.strip()]
        return cls(docs)

    def _index(self, cfg: RetrievalConfig) -> BM25Okapi:
        key = (cfg.tokenizer, cfg.k1, cfg.b)
        if key not in self._bm25_cache:
            toks = [tokenize(f"{d.title} {d.source_label} {d.text}", cfg.tokenizer) for d in self.docs]
            bm = BM25Okapi(toks, k1=cfg.k1, b=cfg.b)
            ref = (self.bm25_reference or {}).get(cfg.tokenizer)
            if ref is not None:
                bm.idf = dict(ref["idf"])
                bm.avgdl = float(ref["avgdl"])
            self._bm25_cache[key] = bm
        return self._bm25_cache[key]

    def bm25_stats(self, tokenizer: str = "lower_alnum_v1") -> dict:
        """The statistics this corpus scores with (its own, or the inherited reference)."""
        if self.bm25_reference and tokenizer in self.bm25_reference:
            return self.bm25_reference
        bm = self._index(RetrievalConfig(tokenizer=tokenizer))
        return {tokenizer: {"idf": {k: float(v) for k, v in sorted(bm.idf.items())}, "avgdl": float(bm.avgdl),
                            "source_snapshot_id": self.snapshot_id}}

    def retrieve(self, query: str, cfg: RetrievalConfig) -> list[RetrievedDoc]:
        """Top-k by BM25; ties broken by corpus position; then any forced presentation order."""
        if not self.docs:
            return []
        scores = self._index(cfg).get_scores(tokenize(query, cfg.tokenizer))
        order = sorted(range(len(self.docs)), key=lambda i: (-round(float(scores[i]), 9), i))[: cfg.k]
        picked = self._apply_presentation_order([self.docs[i] for i in order])
        score_of = {self.docs[i].doc_id: float(scores[i]) for i in order}
        return [
            RetrievedDoc(**d.model_dump(), rank=r, score=round(score_of[d.doc_id], 6))
            for r, d in enumerate(picked, start=1)
        ]

    def _apply_presentation_order(self, picked: list[Doc]) -> list[Doc]:
        """If the snapshot forces an order among some docs (stale vs current plan document),
        reorder those docs among the positions they already occupy."""
        if not self.presentation_order:
            return picked
        ids = [d.doc_id for d in picked]
        present = [x for x in self.presentation_order if x in ids]
        if len(present) < 2:
            return picked
        slots = sorted(ids.index(x) for x in present)
        out = list(picked)
        for pos, doc_id in zip(slots, present):
            out[pos] = self._by_id[doc_id]
        return out


def diff_snapshots(a: Corpus, b: Corpus) -> dict[str, list[str]]:
    """Doc-level diff: which doc_ids were added, removed or modified going from a to b."""
    ida, idb = set(a._by_id), set(b._by_id)
    modified = sorted(
        x for x in ida & idb if canonical_json(a.get(x)) != canonical_json(b.get(x))
    )
    return {"added": sorted(idb - ida), "removed": sorted(ida - idb), "modified": modified}
