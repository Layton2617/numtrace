"""Offline retrieval-budget study for Step 1 (no model calls, no network, $0).

    python -m step1.retrieval_budget [--out docs/research/2026-10-04_retrieval_budget_rows.csv]

Measures, for every Step 1 question, whether BM25 over the frozen snapshot surfaces the chunks that hold the
gold evidence under a grid of budgets (k documents per query x Q queries), using deterministic templated queries
in the manner of pilot/retrieval_eval.py, and how many characters the union of retrieved chunks adds to the
extraction prompt. It reads the snapshot, the cached BM25 index (step1.run.build_index), the questions, gold and
anchors; it writes one CSV row per (strategy, k, Q, question) and prints the summary tables.

Evidence ("groups") per question; a group is a set of chunks, any one of which suffices, and a question's recall
is the share of its groups that have a retrieved chunk:
  * reported numeric slot with an anchor (step1/data/anchors.jsonl: same entity, concept, period, value and
    scope, as in step1.verify_dataset.anchored) -> one group: every chunk holding such an anchor;
  * 13F slot without an anchor (multi-row sums, merged amendments, co-filing groups) -> one group per chunk
    that holds a row of the slot's CUSIP in an accession listed in the gold ``rows`` (all needed);
  * NQDC sum over plans -> one group per plan value with an anchor, else any anchor of the same cell;
  * other reported slots without an anchor -> chunks of the source accession printing the number
    (pilot.retrieval_eval._num_forms), or the chunk named in the gold locator (NQDC-LIMIT);
  * computed slots whose inputs are other slots add nothing (their inputs carry the evidence);
  * text / not_reported / not_applicable / not_found_by_rule slots are unresolved and not scored.
13F-TOP additionally needs every chunk of both quarters' information tables ("needed"), because ranking the
positions needs the whole table; its "minimal" set is only the chunks holding the top-3 rows. For the other
families needed == minimal.

Query strategies (up to Q queries, in this order, duplicates dropped):
  Q+slot     question text + entity + period; per reported slot "entity + aliases[0] + period" (as
             retrieval_eval's Q+slot; for 13F-TOP the aliases name the top-3 issuers, which the model is not
             told, so this is an upper bound there); then the Q+visible extras
  Q+visible  question text + entity + period; per shown slot "entity + display id + period" (what the model
             sees: harness.agent.shown_slots); per slot period "entity + filing phrase + period"
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from data_layer.harness_adapter import harness_concept
from data_layer.facts import Fact
from harness.agent import shown_slots
from harness.corpus import tokenize
from harness.schema import Question, RetrievalConfig
from pilot.retrieval_eval import _num_forms, _text_hits

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO / "data/step1/corpus/step1-v2"
INDEX_ROOT = REPO / "harness/runs/step1/bm25"
BATCHES = ("statements", "13f", "nqdc")
KS = (5, 10, 15, 20)
QS = (6, 10)
STRATEGIES = ("Q+slot", "Q+visible")
FORM_PHRASE = {"statement": "10-K 10-Q financial statements", "13f": "13F-HR information table",
               "nqdc": "proxy statement nonqualified deferred compensation"}
UNSCORED = {"text", "not_reported", "not_applicable", "not_found_by_rule"}
CUSIP_RE = re.compile(r"\(CUSIP ([0-9A-Z]{9})\)")


# ---------------------------------------------------------------- snapshot
class DocMeta:
    """Per chunk: id, accession, length; 13F chunks also the CUSIPs whose rows they print."""

    def __init__(self, snapshot: Path) -> None:
        self.ids, self.acc, self.chars, self.cusips = [], [], [], {}
        self.by_acc: dict[str, list[int]] = defaultdict(list)
        self.offsets = [0]
        with open(snapshot / "docs.jsonl", "rb") as f:
            for i, raw in enumerate(f):
                d = json.loads(raw)
                self.offsets.append(self.offsets[-1] + len(raw))
                self.ids.append(d["doc_id"])
                self.acc.append(d.get("accession"))
                self.chars.append(len(d["text"]))
                self.by_acc[d.get("accession")].append(i)
                if (d.get("form_type") or "").startswith("13F"):
                    self.cusips[i] = set(CUSIP_RE.findall(d["text"]))
        self.path = snapshot / "docs.jsonl"
        self.index = {d: i for i, d in enumerate(self.ids)}

    def text(self, i: int) -> str:
        with open(self.path, "rb") as f:
            f.seek(self.offsets[i])
            return json.loads(f.read(self.offsets[i + 1] - self.offsets[i]))["text"]


class Retriever:
    """Top-k exactly as harness.corpus.Corpus.retrieve (ties by corpus position), over step1.run's index."""

    def __init__(self, index_dir: Path, cfg: RetrievalConfig | None = None, kmax: int = max(KS)) -> None:
        from step1.run import MappedBM25
        self.cfg = cfg or RetrievalConfig()
        self.bm = MappedBM25(index_dir, json.loads((index_dir / "meta.json").read_text()))
        self.kmax, self.cache = kmax, {}

    def top(self, query: str) -> list[int]:
        if query not in self.cache:
            s = self.bm.get_scores(tokenize(query, self.cfg.tokenizer))
            if len(s) > self.kmax:
                cut = np.partition(s, len(s) - self.kmax)[len(s) - self.kmax]
                cand = np.flatnonzero(s >= cut - 1e-6)
            else:
                cand = np.arange(len(s))
            self.cache[query] = sorted(cand.tolist(), key=lambda i: (-round(float(s[i]), 9), i))[: self.kmax]
        return self.cache[query]

    def union(self, qs: list[str], k: int) -> list[int]:
        seen: dict[int, None] = {}
        for q in qs:
            for i in self.top(q)[:k]:
                seen.setdefault(i)
        return list(seen)


# ---------------------------------------------------------------- evidence
def anchor_index(lines) -> dict:
    idx = defaultdict(list)
    for l in lines:
        a = json.loads(l)
        row = (a["fact"]["value"], a.get("scope"), a["doc_id"])
        idx[(a["entity"], harness_concept(Fact.model_validate(a["fact"])), a["period"])].append(row)
        idx[(a["entity"], "*", a["period"])].append(row)       # any concept: the NEO's row of a 402(i) table
    return idx


def _tol(v: float) -> float:
    # within half a unit of the gold value's last digit (step1.verify_dataset.anchored)
    return 0.5 * 10.0 ** -len(repr(float(v)).split(".")[1].rstrip("0")) if v != int(v) else 0.5


def anchor_docs(aidx: dict, entity: str, concept: str, period: str, value, scope) -> set[str]:
    out = set()
    for v, sc, doc in aidx.get((entity, concept, period), []):
        if value is not None and abs(v - value) > _tol(value):
            continue
        if scope is None or sc is None or scope == sc or (scope.startswith("group:") and sc == "filer"):
            out.add(doc)
    return out


def row_groups(meta: DocMeta, rows: list[dict], cusip: str) -> list[frozenset]:
    """Every chunk printing a row of this CUSIP in the accessions the gold rows come from (all needed)."""
    out = []
    for acc in dict.fromkeys(r["accession"] for r in rows):
        out += [frozenset({meta.ids[i]}) for i in meta.by_acc.get(acc, []) if cusip in meta.cusips.get(i, ())]
    return out


def table_accessions(merge: list[dict]) -> list[str]:
    """The filings whose rows make up a quarter's table: a restatement replaces what came before it."""
    accs: list[str] = []
    for m in merge:
        accs = [m["accession"]] if m["action"] in ("original", "restatement_replaced") else accs + [m["accession"]]
    return accs


def _source_acc(g: dict) -> str | None:
    src = g.get("source")
    if isinstance(src, list):
        src = src[0] if src else None
    return (src or {}).get("accession") or g.get("accession")


def slot_groups(g: dict, q: Question, aidx: dict, meta: DocMeta) -> list[frozenset] | None:
    """Evidence groups of one gold slot; [] = covered by other slots; None = unresolved."""
    slot = next((s for s in q.slots if s.slot_id == g["slot"]), None)
    if g["status"] in UNSCORED or g.get("value") is None:
        return None
    inputs = g.get("inputs") or []
    if (slot is not None and slot.inputs) or (inputs and all(isinstance(i, dict) and "slot" in i for i in inputs)):
        return []
    if g["status"] == "reported":
        docs = anchor_docs(aidx, g["entity"], g["concept"], g["period"], g["value"], g.get("scope"))
        if docs:
            return [frozenset(docs)]
    if g.get("rows") and g.get("cusip"):
        return row_groups(meta, g["rows"], g["cusip"]) or None
    if inputs and all(isinstance(i, dict) and "plan" in i for i in inputs):         # NQDC: sum over plans
        acc = _source_acc(g)
        per = [anchor_docs(aidx, g["entity"], g["concept"], g["period"], i["value"], g.get("scope")) for i in inputs]
        if all(per):
            return [frozenset(p) for p in per]
        cell = {d for d in anchor_docs(aidx, g["entity"], g["concept"], g["period"], None, g.get("scope"))
                if meta.acc[meta.index[d]] == acc}
        if cell:
            return [frozenset(cell)]
    if (g.get("scope") or "").startswith("NEO:"):
        # a 0 printed as a dash has no anchor: the chunks holding the NEO's other cells in the source proxy
        acc = _source_acc(g)
        row = {d for d in anchor_docs(aidx, g["entity"], "*", g["period"], None, g["scope"]) if meta.acc[meta.index[d]] == acc}
        if row:
            return [frozenset(row)]
    loc = (g.get("source") or {}).get("locator") if isinstance(g.get("source"), dict) else None
    if loc in meta.index:
        return [frozenset({loc})]
    acc = _source_acc(g)
    if acc and g["unit"] in ("USD", "shares"):
        docs = [type("D", (), {"doc_id": meta.ids[i], "text": meta.text(i)}) for i in meta.by_acc.get(acc, [])]
        hits = _text_hits(docs, _num_forms(float(g["value"]), g.get("scale"), g.get("printed"), None))
        if hits:
            return [frozenset(hits)]
    return None


def evidence(q: Question, gold: list[dict], aidx: dict, meta: DocMeta) -> dict:
    """needed / minimal groups of one question, and the slots that could not be tied to chunks."""
    minimal, unresolved = [], []
    asked = {s.slot_id for s in q.slots}
    for g in gold:
        if g["slot"] not in asked:
            continue
        grp = slot_groups(g, q, aidx, meta)
        if grp is None:
            if g["status"] not in UNSCORED:
                unresolved.append(g["slot"])
            continue
        minimal += [x for x in grp if x not in minimal]
    needed = list(minimal)
    if q.family == "13F-TOP":
        for period, merge in {g["period"]: g["merge"] for g in gold if g.get("merge")}.items():
            for acc in table_accessions(merge):
                needed += [x for x in (frozenset({meta.ids[i]}) for i in meta.by_acc.get(acc, [])) if x not in needed]
    return {"needed": needed, "minimal": minimal, "unresolved": unresolved}


# ---------------------------------------------------------------- queries
def queries(q: Question, strategy: str, n: int = max(QS)) -> list[str]:
    out = [f"{q.text} {q.entity} {q.period}"]
    if strategy == "Q+slot":
        out += [f"{s.entity} {s.aliases[0]} {s.period}" for s in q.slots if s.derivation.value == "reported" and s.aliases]
    period = {s.slot_id: s.period for s in q.slots}
    out += [f"{q.entity} {d.replace('_', ' ')} {period[sid]}" for d, sid in shown_slots(q).items()]
    out += [f"{q.entity} {FORM_PHRASE[q.filing_type.value]} {p}" for p in dict.fromkeys(s.period for s in q.slots)]
    return list(dict.fromkeys(out))[:n]


def score(groups: list[frozenset], got: set[str]) -> tuple[float | None, int | None]:
    if not groups:
        return None, None
    hit = sum(bool(g & got) for g in groups)
    return hit / len(groups), int(hit == len(groups))


# ---------------------------------------------------------------- run
def load_questions(root: Path = REPO) -> tuple[list[Question], dict[str, list[dict]]]:
    qs, gold = [], defaultdict(list)
    for b in BATCHES:
        d = root / "step1/data" / b
        qs += [Question.model_validate_json(l) for l in (d / "questions.jsonl").read_text().splitlines() if l.strip()]
        for l in (d / "gold.jsonl").read_text().splitlines():
            if l.strip():
                g = json.loads(l)
                gold[g["question_id"]].append(g)
    return qs, gold


def evaluate(qs: list[Question], ev: dict, meta: DocMeta, ret: Retriever) -> list[dict]:
    rows = []
    for strat in STRATEGIES:
        for q in qs:
            allq = queries(q, strat)
            for Q in QS:
                for k in KS:
                    got_i = ret.union(allq[:Q], k)
                    got = {meta.ids[i] for i in got_i}
                    e = ev[q.id]
                    nr, nf = score(e["needed"], got)
                    mr, mf = score(e["minimal"], got)
                    rows.append({"strategy": strat, "k": k, "Q": Q, "question_id": q.id, "family": q.family,
                                 "n_queries": len(allq[:Q]), "n_docs": len(got_i),
                                 "chars": sum(meta.chars[i] for i in got_i),
                                 "n_needed": len(e["needed"]), "needed_recall": nr, "needed_full": nf,
                                 "n_minimal": len(e["minimal"]), "minimal_recall": mr, "minimal_full": mf})
    return rows


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def summary(rows: list[dict], strategy: str) -> str:
    fams = list(dict.fromkeys(r["family"] for r in rows))
    out = []
    for fam in fams + ["ALL"]:
        out += [f"#### {fam} ({strategy})", "",
                "| k | Q | needed recall | needed full | minimal recall | minimal full | docs (mean) | tokens mean | tokens p90 | tokens max |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for Q in QS:
            for k in KS:
                rs = [r for r in rows if r["strategy"] == strategy and r["k"] == k and r["Q"] == Q
                      and (fam == "ALL" or r["family"] == fam)]
                toks = sorted(r["chars"] / 4 for r in rs)
                out.append(f"| {k} | {Q} | {_mean(r['needed_recall'] for r in rs):.3f} | {_mean(r['needed_full'] for r in rs):.3f} "
                           f"| {_mean(r['minimal_recall'] for r in rs):.3f} | {_mean(r['minimal_full'] for r in rs):.3f} "
                           f"| {_mean(r['n_docs'] for r in rs):.1f} | {_mean(toks) / 1e3:.0f}k | {toks[int(0.9 * (len(toks) - 1))] / 1e3:.0f}k "
                           f"| {toks[-1] / 1e3:.0f}k |")
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- option C: a header in every 13F chunk
def header_variant(snapshot: Path, out: Path) -> Path:
    """A scratch copy of the snapshot in which every 13F continuation chunk starts with the first two lines of
    its filing's first chunk (filer, CIK, period of report, filing date, accession). Only for this study: the
    doc ids are unchanged, so the same evidence applies; the snapshot id is recomputed so step1.run.build_index
    accepts it. Nothing under data/ is touched."""
    import hashlib
    from harness.schema import Doc, canonical_json
    out.mkdir(parents=True, exist_ok=True)
    head: dict[str, str] = {}
    h = hashlib.sha256(b"[")
    with open(snapshot / "docs.jsonl", "rb") as f, open(out / "docs.jsonl", "wb") as g:
        for n, raw in enumerate(f):
            d = json.loads(raw)
            if (d.get("form_type") or "").startswith("13F"):
                if d["meta"].get("section") == 1:
                    head[d["accession"]] = "\n".join(d["text"].split("\n")[:2])
                elif d["accession"] in head:
                    d["text"] = head[d["accession"]] + "\n" + d["text"]
            line = json.dumps(d, ensure_ascii=False).encode() + b"\n"
            h.update((b"," if n else b"") + canonical_json(Doc.model_validate_json(line)).encode())
            g.write(line)
    h.update(b"]")
    manifest = json.loads((snapshot / "manifest.json").read_text())
    manifest.update(snapshot_id="snap-" + h.hexdigest()[:16], name=manifest["name"] + "-13fheader-scratch")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return out


# ---------------------------------------------------------------- calibration and cost
def calibrate(traj_path: Path, qd: dict, ev: dict, meta: DocMeta, ret: Retriever) -> list[dict]:
    """Real model queries (logged retrieval stage) vs the templates, on the same evidence and the same index."""
    out = []
    for l in traj_path.read_text().splitlines():
        t = json.loads(l)
        st = next(s for s in t["steps"] if s["stage"] == "retrieval")
        q, e = qd[t["question_id"]], ev[t["question_id"]]
        logged = {d["doc_id"] for d in st["output"]["docs"]}
        ext = next((s.get("usage") or {}) for s in t["steps"] if s["stage"] == "extraction")
        logged_chars = sum(meta.chars[meta.index[d]] for d in logged)
        out.append({"question_id": q.id, "source": "logged run (k=5)", "k": 5, "n_queries": len(st["input"]["queries"]),
                    "n_docs": len(logged), "chars": logged_chars,
                    "needed": score(e["needed"], logged), "minimal": score(e["minimal"], logged),
                    "extraction_prompt_tokens": ext.get("prompt_tokens")})
        srcs = [("real queries", st["input"]["queries"])] + [(s, queries(q, s)[:6]) for s in STRATEGIES]
        for name, qs in srcs:
            for k in KS:
                ids = ret.union(qs, k)
                got = {meta.ids[i] for i in ids}
                out.append({"question_id": q.id, "source": name, "k": k, "n_queries": len(qs), "n_docs": len(ids),
                            "chars": sum(meta.chars[i] for i in ids),
                            "needed": score(e["needed"], got), "minimal": score(e["minimal"], got)})
    return out


# Baseline full-run estimate at k=5 / Q=6 (540 questions), and input prices per million tokens (step1_cli.toml).
BASELINE_MTOK = {"opus-cli": 38.0, "gpt-cli": 64.0, "deepseek": 26.0}
DEEPSEEK_BASELINE_USD = {"peak": 12.0, "off-peak": 6.0}


def cost_rows(rows: list[dict], strategy: str, prices: dict[str, float], chars_per_token: float = 4.0,
              chars_per_token_13f: float | None = None) -> list[dict]:
    """Extra retrieved-context tokens per question vs k=5/Q=6, and the full-run totals they imply (input price only).
    Q=10 'all 10 used' scales the Q=6 union by 10/6: the templates write fewer than 10 queries, a model may write 10.
    chars_per_token_13f: 13F tables tokenize denser (the 13F trial: about 1.85 characters per Opus token)."""
    def mean_tok(k, Q):
        rs = [r for r in rows if r["strategy"] == strategy and r["k"] == k and r["Q"] == Q]
        cpt = lambda r: chars_per_token_13f if chars_per_token_13f and r["family"].startswith("13F") else chars_per_token
        return sum(r["chars"] / cpt(r) for r in rs) / len(rs), len(rs)
    base, n = mean_tok(5, 6)
    out = []
    for Q in QS:
        for k in KS:
            tok, _ = mean_tok(k, Q)
            for label, t in ([("templated", tok)] + ([("all 10 used", mean_tok(k, 6)[0] * 10 / 6)] if Q == 10 else [])):
                d = t - base
                row = {"k": k, "Q": Q, "variant": label, "ctx_tokens_per_q": t, "delta_per_q": d, "delta_run_mtok": d * n / 1e6}
                for m, b in BASELINE_MTOK.items():
                    row[f"{m}_mtok"] = b + d * n / 1e6
                    row[f"{m}_delta_usd"] = d * n / 1e6 * prices[m]
                row["deepseek_usd_peak"] = DEEPSEEK_BASELINE_USD["peak"] + row["deepseek_delta_usd"]
                row["deepseek_usd_offpeak"] = DEEPSEEK_BASELINE_USD["off-peak"] + row["deepseek_delta_usd"] / 2
                out.append(row)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "docs/research/2026-10-04_retrieval_budget_rows.csv"))
    ap.add_argument("--header-variant", metavar="DIR", help="option C: build the 13F-header copy of the snapshot "
                    "and its BM25 index under DIR (a scratch directory) and measure on it instead")
    a = ap.parse_args(argv)
    snap, index_root = SNAPSHOT, INDEX_ROOT
    if a.header_variant:
        from step1.run import build_index
        snap = header_variant(SNAPSHOT, Path(a.header_variant) / "snapshot")
        index_root = build_index(snap, RetrievalConfig(), Path(a.header_variant) / "bm25").parent
    meta = DocMeta(snap)
    ret = Retriever(next(index_root.glob(json.loads((snap / "manifest.json").read_text())["snapshot_id"] + "-*")))
    qs, gold = load_questions()
    aidx = anchor_index((REPO / "step1/data/anchors.jsonl").read_text().splitlines())
    ev = {q.id: evidence(q, gold[q.id], aidx, meta) for q in qs}
    rows = evaluate(qs, ev, meta, ret)
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    unres = [(q.id, s) for q in qs for s in ev[q.id]["unresolved"]]
    print(f"questions {len(qs)}; with no scorable evidence {sum(not ev[q.id]['needed'] for q in qs)}; "
          f"unresolved numeric slots {len(unres)}")
    for x in unres:
        print("  unresolved", *x)
    for s in STRATEGIES:
        print(summary(rows, s))
    import tomllib
    cfg = tomllib.loads((REPO / "step1/configs/step1_cli.toml").read_text())
    prices = {m: cfg["models"][m]["pricing"]["input_per_mtok"] for m in BASELINE_MTOK}
    for s in STRATEGIES:
        for c13 in (None, 1.85):
            print(f"#### cost ({s}; 4 chars/token{'' if c13 is None else f', 13F {c13}'}; input price only)")
            for c in cost_rows(rows, s, prices, chars_per_token_13f=c13):
                print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in c.items()})
    traj = REPO / "harness/runs/step1/opus-cli/13f/trajectories.jsonl"
    if traj.exists():
        print("#### calibration")
        for c in calibrate(traj, {q.id: q for q in qs}, ev, meta, ret):
            print(c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
