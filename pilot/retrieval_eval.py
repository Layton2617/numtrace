"""Offline retrieval evaluation on the frozen pilot corpus (no model calls, no cost).

    python -m pilot.retrieval_eval            # writes pilot/retrieval_eval.md and pilot/gold/retrieval_eval.csv

Why: retrieval is evaluated on its own, before paid runs, so that (a) k is chosen from evidence and
(b) slots whose evidence BM25 cannot surface are known in advance and are not later blamed on a model
(cf. CMCSA-2 in the mock run).

Evidence ("relevant documents") per gold slot, from pilot/gold/pilot_gold.jsonl:
  * a slot needs one or more evidence groups; a group is a set of snapshot docs, any one of which suffices;
    the slot is covered at k only if every group has a doc in the retrieved set;
  * reported numeric slot -> one group: docs of the source accession holding an anchor whose fact value
    equals the gold value (same unit); if no anchor matches, docs of the source accession whose text prints
    the number (printed form, scaled form, 13F original value) or, for dates, a common date spelling;
  * derived slot with ``inputs`` -> one group per input (value + accession);
  * a number that is not printed anywhere (a filer-level 13F sum over other-manager rows) -> one group per
    doc holding a component row of the slot's concept (all needed);
  * a derived slot that is not printed (total = current + noncurrent, change = q4 - q3) -> the groups of its
    input slots, as defined in pilot/harness_inputs/questions.jsonl;
  * text slots, not_reported slots and anything else that cannot be tied to a doc -> "unresolved"
    (listed, not scored).

Query strategies (the agent unions the top-k of each query, as harness.agent._retrieve does):
  Q        one query: question text + entity + period (a naive agent)
  Q+slot   Q plus, per reported slot, "entity + generic alias + period" (aliases[0]; a plausible LLM query)
  Q+label  Q plus, per reported slot, "entity + printed row label + period" (aliases[-1]; what the mock
           sends with query_per_slot; optimistic, because the label is copied from the filing)
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date
from pathlib import Path
from typing import Iterable

from harness.corpus import Corpus
from harness.schema import Question, RetrievalConfig

HERE = Path(__file__).parent
KS = (1, 3, 5, 8, 10, 15, 20)
STRATEGIES = ("Q", "Q+slot", "Q+label")


# ---------------------------------------------------------------- evidence
def _close(a: float, b: float, scale: float | None = None) -> bool:
    tol = max(0.5 * (scale or 1.0), 0.5)
    return abs(float(a) - float(b)) <= tol


def _num_forms(value: float, scale: float | None, printed: str | None, original: float | None) -> set[str]:
    forms: set[str] = set()
    if printed:
        forms.add(printed.strip())
    # filings print USD in millions or thousands; without a known scale try both
    scaled = [value / scale] if scale else [value / 1e6, value / 1e3]
    for v in (value, original, *scaled):
        if v is None:
            continue
        if float(v).is_integer():
            forms.add(f"{int(v):,}")
            forms.add(str(int(v)))
    return {f for f in forms if len(f.replace(",", "")) >= 3}  # 1-2 digit numbers match everywhere


def _date_forms(iso: str) -> set[str]:
    d = date.fromisoformat(iso)
    return {iso, d.strftime("%Y%m%d"), d.strftime("%m/%d/%Y"), d.strftime("%m-%d-%Y"),
            f"{d.strftime('%B')} {d.day}, {d.year}"}


def _text_hits(docs: Iterable, forms: set[str]) -> set[str]:
    pats = [re.compile(r"(?<![\d,.])" + re.escape(f) + r"(?![\d]|,\d)") for f in forms]
    return {d.doc_id for d in docs if any(p.search(d.text) for p in pats)}


def _group(corpus: Corpus, anchors: list[dict], accessions: set[str], value, unit: str,
           scale=None, printed=None, original=None) -> set[str]:
    in_acc = [d for d in corpus.docs if d.accession in accessions]
    ids_in_acc = {d.doc_id for d in in_acc}
    if unit == "date":
        return _text_hits(in_acc, _date_forms(str(value)))
    hits = {a["doc_id"] for a in anchors
            if a["doc_id"] in ids_in_acc and a["fact"]["unit"] == unit and _close(a["fact"]["value"], value, a.get("scale"))}
    if hits:
        return hits
    return _text_hits(in_acc, _num_forms(float(value), scale, printed, original))


_F13_ROW = {"13f:Shares": "13f:PositionShares", "13f:Value": "13f:PositionValue"}


def _concept_match(slot_concept: str, fact: dict) -> bool:
    """Slot concepts name 13F positions as '13f:Value[<cusip>]'; anchors store rows as 13f:PositionValue + cusip."""
    if fact["concept"] == slot_concept:
        return True
    base, _, rest = slot_concept.partition("[")
    cusip = rest.rstrip("]")
    return bool(cusip) and _F13_ROW.get(base) == fact["concept"] and fact.get("dimensions", {}).get("cusip") == cusip


def evidence_groups(gold: list[dict], corpus: Corpus, anchors: list[dict],
                    questions: list[Question] | None = None) -> dict[tuple[str, str], list[set[str]] | None]:
    """(question_id, slot) -> list of evidence groups, or None if the slot cannot be tied to documents."""
    slot_def = {(q.id, s.slot_id): s for q in (questions or []) for s in q.slots}
    acc_of = {d.doc_id: d.accession for d in corpus.docs}
    out: dict[tuple[str, str], list[set[str]] | None] = {}
    for g in gold:
        key = (g["question_id"], g["slot"])
        src = g.get("source") or {}
        acc = {src["accession"]} if src.get("accession") else set()
        groups: list[set[str]] | None
        if g["status"] == "not_reported" or g["unit"] == "text" or g.get("value") is None:
            groups = None
        elif g.get("inputs"):
            groups = [_group(corpus, anchors, {i["accession"]}, i["value"], g["unit"]) for i in g["inputs"]]
        elif acc:
            groups = [_group(corpus, anchors, acc, g["value"], g["unit"], g.get("scale"), g.get("printed"),
                             g.get("original_value"))]
            sd = slot_def.get(key)
            if not groups[0] and sd is not None and not sd.inputs:
                # an aggregate that is not printed (e.g. a filer-level 13F total over other-manager rows):
                # every doc holding a component row of the same concept is needed
                docs = sorted({a["doc_id"] for a in anchors
                               if acc_of.get(a["doc_id"]) in acc and _concept_match(sd.concept, a["fact"])})
                groups = [{d} for d in docs]
        else:
            groups = []
        out[key] = groups if groups and all(groups) else (None if groups is None else [])
    # derived slots whose number is not printed: the evidence of their inputs (harness slot definition)
    for _ in range(2):
        for key, groups in list(out.items()):
            sd = slot_def.get(key)
            if groups == [] and sd is not None and sd.inputs:
                deps = [out.get((key[0], sid)) for sid in sd.inputs.values()]
                if deps and all(deps):
                    out[key] = [grp for d in deps for grp in d]
    return {k: (v or None) for k, v in out.items()}


# ---------------------------------------------------------------- queries and retrieval
def queries(q: Question, strategy: str) -> list[str]:
    base = [f"{q.text} {q.entity} {q.period}"]
    if strategy == "Q":
        return base
    pick = {"Q+slot": 0, "Q+label": -1}[strategy]
    return base + [f"{s.entity} {s.aliases[pick]} {s.period}" for s in q.slots
                   if s.derivation.value == "reported" and s.aliases]


def retrieved(corpus: Corpus, qs: list[str], k: int, cfg: RetrievalConfig) -> list[str]:
    cfg = cfg.model_copy(update={"k": k})
    seen: list[str] = []
    for query in qs:
        for d in corpus.retrieve(query, cfg):
            if d.doc_id not in seen:
                seen.append(d.doc_id)
    return seen


def covered(groups: list[set[str]], ids: Iterable[str]) -> bool:
    ids = set(ids)
    return all(g & ids for g in groups)


def first_rank(corpus: Corpus, qs: list[str], groups: list[set[str]], cfg: RetrievalConfig, kmax: int = 50) -> int | None:
    """Smallest per-query k at which the slot is covered (None if not within kmax)."""
    for k in range(1, kmax + 1):
        if covered(groups, retrieved(corpus, qs, k, cfg)):
            return k
    return None


# ---------------------------------------------------------------- run
def evaluate(corpus: Corpus, questions: list[Question], gold: list[dict], anchors: list[dict],
             ks=KS, strategies=STRATEGIES, cfg: RetrievalConfig | None = None) -> tuple[list[dict], list[dict]]:
    cfg = cfg or RetrievalConfig()
    ev = evidence_groups(gold, corpus, anchors, questions)
    by_id = {q.id: q for q in questions}
    ftype = {q.id: q.filing_type.value for q in questions}
    rows, ctx = [], []
    chars = {d.doc_id: len(d.text) for d in corpus.docs}
    for strat in strategies:
        for q in questions:
            qs = queries(q, strat)
            for k in ks:
                ids = retrieved(corpus, qs, k, cfg)
                ctx.append({"strategy": strat, "question_id": q.id, "k": k, "n_queries": len(qs),
                            "n_docs": len(ids), "approx_tokens": sum(chars[i] for i in ids) // 4})
        for (qid, slot), groups in ev.items():
            q = by_id[qid]
            row = {"strategy": strat, "question_id": qid, "filing_type": ftype[qid], "slot": slot,
                   "resolved": groups is not None}
            if groups is not None:
                qs = queries(q, strat)
                for k in ks:
                    row[f"hit@{k}"] = int(covered(groups, retrieved(corpus, qs, k, cfg)))
                row["first_k"] = first_rank(corpus, qs, groups, cfg)
            rows.append(row)
    return rows, ctx


def summarise(rows: list[dict], ctx: list[dict], ks=KS) -> str:
    res = [r for r in rows if r["resolved"]]
    strategies = list(dict.fromkeys(r["strategy"] for r in rows))
    n_res = len({(r["question_id"], r["slot"]) for r in res})
    n_all = len({(r["question_id"], r["slot"]) for r in rows})
    lines = [f"Slots tied to evidence documents: {n_res} of {n_all} (the rest are listed at the end).", ""]
    lines += ["### Slot recall@k (all evidence groups retrieved), by query strategy", "",
              "| strategy | " + " | ".join(f"k={k}" for k in ks) + " |", "|---|" + "---:|" * len(ks)]
    for s in strategies:
        rs = [r for r in res if r["strategy"] == s]
        lines.append(f"| {s} | " + " | ".join(f"{sum(r[f'hit@{k}'] for r in rs) / len(rs):.2f}" for k in ks) + " |")
    lines += ["", "### Slot recall@k by filing type (strategy Q+slot)", "",
              "| filing type | n | " + " | ".join(f"k={k}" for k in ks) + " |", "|---|---:|" + "---:|" * len(ks)]
    for ft in sorted({r["filing_type"] for r in res}):
        rs = [r for r in res if r["strategy"] == "Q+slot" and r["filing_type"] == ft]
        lines.append(f"| {ft} | {len(rs)} | " + " | ".join(f"{sum(r[f'hit@{k}'] for r in rs) / len(rs):.2f}" for k in ks) + " |")
    lines += ["", "### Context size per report (median over the 16 questions)", "",
              "| strategy | " + " | ".join(f"k={k}" for k in ks) + " |", "|---|" + "---:|" * len(ks)]
    for s in strategies:
        cells = []
        for k in ks:
            docs = sorted(x["n_docs"] for x in ctx if x["strategy"] == s and x["k"] == k)
            toks = sorted(x["approx_tokens"] for x in ctx if x["strategy"] == s and x["k"] == k)
            cells.append(f"{docs[len(docs) // 2]} docs / {toks[len(toks) // 2] / 1000:.0f}k tok")
        lines.append(f"| {s} | " + " | ".join(cells) + " |")
    lines += ["", "### Slots not covered at k=10 (strategy Q+slot)", "", "| question | slot | first k that covers it |", "|---|---|---:|"]
    for r in res:
        if r["strategy"] == "Q+slot" and not r["hit@10"]:
            lines.append(f"| {r['question_id']} | {r['slot']} | {r['first_k'] or '> 50'} |")
    lines += ["", "### Unresolved slots (not scored)", ""]
    lines += [f"- {r['question_id']} / {r['slot']}" for r in rows if r["strategy"] == strategies[0] and not r["resolved"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    corpus = Corpus.load(HERE / "corpus" / "pilot-v1")
    questions = [Question.model_validate_json(l) for l in (HERE / "harness_inputs" / "questions.jsonl").read_text().splitlines() if l.strip()]
    gold = [json.loads(l) for l in (HERE / "gold" / "pilot_gold.jsonl").read_text().splitlines() if l.strip()]
    anchors = [json.loads(l) for l in (HERE / "harness_inputs" / "anchors.jsonl").read_text().splitlines() if l.strip()]
    rows, ctx = evaluate(corpus, questions, gold, anchors)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(HERE / "gold" / "retrieval_eval.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    body = summarise(rows, ctx)
    head = (f"# Offline retrieval evaluation (snapshot {corpus.snapshot_id}, BM25 k1=1.5 b=0.75)\n\n"
            "Generated by `python -m pilot.retrieval_eval`; per-slot rows in `gold/retrieval_eval.csv`. "
            "No model calls. k is per query; the agent unions the top-k of each query. "
            "Definitions are in the module docstring.\n\n")
    (HERE / "retrieval_eval.md").write_text(head + body)
    print(head + body)


if __name__ == "__main__":
    main()
