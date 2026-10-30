"""Step 1 dataset verifier: one command, no model calls, no network. Per batch (statements, 13f, nqdc):

  1 sources      the snapshot manifest has an id and docs.jsonl matches its docs_sha256; every accession cited by
                 gold.jsonl or facts.jsonl is a filing in sources.jsonl
  2 time         every question's as-of date (fiscal period end from companies.json; 13F: quarter end) is in the
                 manifest window; every cited filing was filed on or before the freeze date
  3 counts       questions per family / calendar year / entity, gold slots by status; gold and questions agree
  4 anchors      reported numeric gold slots with no anchor in step1/data/anchors.jsonl (listed)
  5 determinism  with --rebuild: each builder into a temp dir, md5 of every output against the repo copy; anchors too
  6 leakage      no question text contains a gold value or an accession

    python -m step1.verify_dataset             # checks 1-4, 6; writes step1/DATASET.md
    python -m step1.verify_dataset --rebuild   # also 5 (~2 minutes)

Prints the md5 of every file under step1/data and step1/entities; step1/DATASET.md carries the same list.
Families, years and entities are read off the data, nothing is hard-coded per batch.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BATCHES = ("statements", "13f", "nqdc")
SNAP = Path("data/step1/corpus/step1-v2")
ACCESSION = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
QUARTER = re.compile(r"^\d{4}Q[1-4](-\d{4}Q[1-4])?$")
# builder outputs that live outside step1/data/<batch>/
ELSEWHERE = {"companies.json": "step1/entities/companies.json",
             "13f_cofiling_groups.csv": "step1/entities/13f_cofiling_groups.csv",
             "anchors.jsonl": "step1/data/anchors.jsonl",
             "anchors_missing.jsonl": "data/step1/anchors_missing.jsonl"}
CHECKS = ("sources", "time", "counts", "anchors", "determinism", "leakage")


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def quarter_end(q: str) -> date:
    n = int(q[5])
    return date(int(q[:4]), 3 * n, 31 if n in (1, 4) else 30)


def quarter_start(q: str) -> date:
    return date(int(q[:4]), 3 * int(q[5]) - 2, 1)


def as_of(q: dict, companies: dict) -> list[date]:
    """13F periods are quarters ("2024Q4-2025Q1" -> both ends); statements / NQDC periods are fiscal labels."""
    if QUARTER.match(q["period"]):
        return [quarter_end(p) for p in q["period"].split("-")]
    c = companies.get(q["entity"], {})
    p = c.get("fiscal_years", {}).get(q["period"]) or c.get("fiscal_quarters", {}).get(q["period"])
    return [date.fromisoformat(p["end"])] if p else []


def walk(obj, key: str):
    """Every value stored under `key` anywhere in a nested record."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v, key)


def accessions(records: list[dict]) -> set[str]:
    # 13F-GROUP facts join the members' accessions with commas
    return {a.strip() for r in records for v in walk(r, "accession") if isinstance(v, str) and v
            for a in v.split(",")}


def is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def value_strings(g: dict) -> set[str]:
    """How a gold value could be written in a question: plain and comma-grouped integers (>= 1,000 only; small
    integers like 0 or 3 are everywhere), decimals as written, and the printed figure."""
    out, v = set(), g.get("value")
    if is_number(v) and v:
        if float(v).is_integer():
            n = abs(int(v))
            if n >= 1000:
                out |= {str(n), f"{n:,}"}
        else:
            out.add(f"{abs(v):g}")
    elif isinstance(v, str) and len(v) >= 6:
        out.add(v)
    if isinstance(g.get("gold_text"), str) and len(g["gold_text"]) >= 6:
        out.add(g["gold_text"])
    p = str(g.get("printed") or "").lstrip("-(").rstrip(")")
    if sum(ch.isdigit() for ch in p) >= 3:
        out.add(p)
    return out


def leaks(text: str, s: str) -> bool:
    if not s[0].isdigit():
        return s.lower() in text.lower()
    return re.search(rf"(?<![\d.,]){re.escape(s)}(?![\d]|[.,]\d)", text) is not None


def anchor_index(path: Path) -> dict:
    idx = defaultdict(list)
    from data_layer.harness_adapter import Anchor
    for l in path.read_text().splitlines():
        a = Anchor.model_validate_json(l)
        idx[(a.entity, a.concept, a.period)].append((a.fact.value, a.scope))
    return idx


def anchored(g: dict, idx: dict) -> bool:
    """Same entity, concept, period and value; same scope, except that a co-filing group total (scope group:X) is
    anchored by a single filer row printing the same number (one member holds the position)."""
    v = g["value"]
    # within half a unit of the gold value's last digit (0.62 vs 0.21 EPS are not the same number)
    tol = 0.5 * 10.0 ** -len(repr(float(v)).split(".")[1].rstrip("0")) if v != int(v) else 0.5
    for value, scope in idx.get((g["entity"], g["concept"], g["period"]), []):
        if abs(value - v) > tol:
            continue
        s = g.get("scope")
        if s is None or scope is None or s == scope or (s.startswith("group:") and scope == "filer"):
            return True
    return False


def check_batch(root: Path, b: str, ctx: dict) -> dict:
    d = root / "step1" / "data" / b
    qs, gold = read_jsonl(d / "questions.jsonl"), read_jsonl(d / "gold.jsonl")
    facts = read_jsonl(d / "facts.jsonl") if (d / "facts.jsonl").exists() else []
    res = {"n_questions": len(qs), "n_gold": len(gold)}

    cited = accessions(gold) | accessions(facts)
    missing = sorted(cited - ctx["filed"].keys())
    res["sources"] = (ctx["snap_ok"] and not missing,
                      [f"{len(cited) - len(missing)}/{len(cited)} cited accessions in sources"]
                      + [f"  {n}" for n in ctx["snap_notes"]] + [f"  missing {a}" for a in missing])

    lo, hi, freeze = ctx["window"][0], ctx["window"][1], ctx["freeze"]
    dates, bad = {}, []
    for q in qs:
        ds = as_of(q, ctx["companies"])
        if not ds:
            bad.append(f"  {q['id']}: no as-of date for {q['entity']} {q['period']}")
        elif not all(lo <= x <= hi for x in ds):
            bad.append(f"  {q['id']}: as-of {', '.join(map(str, ds))} outside {lo}..{hi}")
        dates[q["id"]] = max(ds) if ds else None
    late = sorted(a for a in cited if a in ctx["filed"] and ctx["filed"][a] > freeze)
    late_gold = sorted({v for g in gold for k in ("filed", "filing_date") for v in walk(g, k)
                        if isinstance(v, str) and v[:10] > freeze.isoformat()})
    res["time"] = (not bad and not late and not late_gold,
                   [f"{len(qs) - len(bad)}/{len(qs)} as-of in window, {len(late) + len(late_gold)} filed late",
                    f"  as-of dates {min(filter(None, dates.values()), default='-')}.."
                    f"{max(filter(None, dates.values()), default='-')}, window {lo}..{hi}, freeze {freeze}"]
                   + bad + [f"  filed after freeze: {a} {ctx['filed'][a]}" for a in late]
                   + [f"  gold filed date after freeze: {v}" for v in late_gold])

    ids = [q["id"] for q in qs]
    gids = {g["question_id"] for g in gold}
    dup = [i for i, n in Counter(ids).items() if n > 1]
    no_gold, orphan = sorted(set(ids) - gids), sorted(gids - set(ids))
    res["family"] = Counter(q.get("family") for q in qs)
    res["year"] = Counter(dates[i].year for i in ids if dates[i])
    res["family_year"] = Counter((q.get("family"), dates[q["id"]].year) for q in qs if dates[q["id"]])
    res["entity"] = Counter(q["entity"] for q in qs)
    res["status"] = Counter(g["status"] for g in gold)
    res["counts"] = (not dup and not no_gold and not orphan,
                     [f"{len(qs)} q, {len(res['entity'])} entities, {len(gold)} slots",
                      f"  slots by status: {', '.join(f'{k} {v}' for k, v in sorted(res['status'].items()))}"]
                     + [f"  duplicate question id {i}" for i in dup]
                     + [f"  question without gold {i}" for i in no_gold]
                     + [f"  gold for unknown question {i}" for i in orphan])

    numeric = [g for g in gold if g["status"] == "reported" and is_number(g.get("value"))]
    res["unanchored"] = [g for g in numeric if not anchored(g, ctx["anchors"])]
    res["anchors"] = (None if res["unanchored"] else True,
                      [f"{len(res['unanchored'])}/{len(numeric)} unanchored"]
                      + [f"  {g['question_id']} {g['slot']} = {g['value']:,}" for g in res["unanchored"]])

    text = {q["id"]: q["text"] for q in qs}
    hits = []
    for q in qs:
        for a in ACCESSION.findall(q["text"]):
            hits.append(f"  {q['id']}: accession {a}")
    for g in gold:
        for s in value_strings(g):
            if g["question_id"] in text and leaks(text[g["question_id"]], s):
                hits.append(f"  {g['question_id']} {g['slot']}: {s!r}")
    res["leakage"] = (not hits, [f"{len(hits)} hits"] + hits)
    res["determinism"] = (None, ["not run (use --rebuild)"])
    return res


def context(root: Path) -> dict:
    snap = root / SNAP
    man = json.loads((snap / "manifest.json").read_text())
    notes, ok = [], bool(man.get("snapshot_id"))
    notes.append(f"snapshot {man.get('snapshot_id') or 'MISSING'}")
    if man.get("docs_sha256"):
        h = hashlib.sha256()
        with open(snap / "docs.jsonl", "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                h.update(chunk)
        same = h.hexdigest() == man["docs_sha256"]
        ok &= same
        notes.append("docs.jsonl matches docs_sha256" if same else "docs.jsonl does NOT match docs_sha256")
    first, last = man["window"].split(":")
    companies = {c["name"]: c for c in json.loads((root / "step1/entities/companies.json").read_text())}
    return {"manifest": man, "snap_ok": ok, "snap_notes": notes, "window": (quarter_start(first), quarter_end(last)),
            "freeze": date.fromisoformat(man["freeze_date"]), "companies": companies,
            "filed": {s["accession"]: date.fromisoformat(s["filed"]) for s in read_jsonl(snap / "sources.jsonl")},
            "anchors": anchor_index(root / "step1/data/anchors.jsonl")}


def builders(root: Path, tmp: Path) -> dict[str, list[str]]:
    """thirteen_f_gold and corpus anchors have no --out: their module paths are pointed at the temp dir instead."""
    py = [sys.executable, "-B"]
    # anchors.jsonl follows the order of --facts; the repo copy was built statements, 13f, nqdc
    facts = [f"step1/data/{b}/facts.jsonl" for b in BATCHES if (root / "step1/data" / b / "facts.jsonl").exists()]
    return {
        "statements": py + ["-m", "step1.statements", "--out", str(tmp / "statements"),
                            "--entities", str(tmp / "statements" / "companies.json")],
        "13f": py + ["-c", "import sys; from pathlib import Path; import step1.thirteen_f_gold as m; "
                           "m.OUT = Path(sys.argv[1]); m.GROUPS = m.OUT / '13f_cofiling_groups.csv'; m.main([])",
                     str(tmp / "13f")],
        "nqdc": py + ["-m", "step1.nqdc_gold", "--out", str(tmp / "nqdc")],
        "anchors": py + ["-c", "import sys; from pathlib import Path; import step1.corpus as m; "
                               "m.OUT = Path(sys.argv[1]); m.ANCHORS = m.OUT / 'anchors.jsonl'; "
                               "m.main(['anchors', '--facts'] + sys.argv[2:])", str(tmp / "anchors")] + facts,
    }


def rebuild(root: Path, tmp: Path, commands: dict[str, list[str]] | None = None) -> dict:
    """Run every builder at once (offline: SEC_USER_AGENT unset) and compare each file it wrote with the repo's."""
    commands = commands or builders(root, tmp)
    env = {k: v for k, v in os.environ.items() if k != "SEC_USER_AGENT"}
    procs = {}
    for name, cmd in commands.items():
        (tmp / name).mkdir(parents=True, exist_ok=True)
        log = open(tmp / f"{name}.log", "w")
        procs[name] = (subprocess.Popen(cmd, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT), log)
    out = {}
    for name, (p, log) in procs.items():
        rc = p.wait()
        log.close()
        if rc:
            tail = (tmp / f"{name}.log").read_text().splitlines()[-5:]
            out[name] = (False, [f"builder exited {rc}"] + [f"  {l}" for l in tail])
            continue
        lines, ok = [], True
        produced = sorted(p for p in (tmp / name).rglob("*") if p.is_file())
        for f in produced:
            rel = ELSEWHERE.get(f.name, f"step1/data/{name}/{f.name}")
            repo = root / rel
            same = repo.exists() and md5(repo) == md5(f)
            ok &= same
            how = "same" if same else "NOT IN REPO" if not repo.exists() else \
                "DIFF (same lines, other order)" if sorted(repo.read_bytes().splitlines()) == \
                sorted(f.read_bytes().splitlines()) else "DIFF"
            lines.append(f"  {how}  {rel}  {md5(f)}")
        if not produced:
            ok, lines = False, ["builder wrote nothing"]
        out[name] = (ok, [f"{sum(l.startswith('  same') for l in lines)}/{len(produced)} files md5-identical"] + lines)
    return out


def md5_manifest(root: Path) -> dict[str, str]:
    files = sorted(p for d in ("step1/data", "step1/entities") for p in (root / d).rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts and ".cache" not in p.parts)
    return {str(p.relative_to(root)): md5(p) for p in files}


def git_version(root: Path) -> str:
    def git(*a):
        r = subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    head = git("rev-parse", "--short", "HEAD") or "unknown"
    dirty = [l for l in git("status", "--porcelain", "--", "step1/data", "step1/entities").splitlines() if l]
    return head + (f" ({len(dirty)} uncommitted changes in step1/data, step1/entities)" if dirty else "")


def mark(ok) -> str:
    return {True: "PASS", False: "FAIL", None: "WARN"}[ok]


def table(results: dict, rebuilt: bool) -> str:
    rows = ["| check | " + " | ".join(BATCHES) + " |", "|---|" + "---|" * len(BATCHES)]
    for c in CHECKS:
        cells = []
        for b in BATCHES:
            ok, notes = results[b][c]
            cells.append("SKIP" if c == "determinism" and not rebuilt else f"{mark(ok)} {notes[0]}")
        rows.append(f"| {c} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def readme_rules(root: Path) -> list[str]:
    """The README headings that hold a batch's rules and caveats."""
    out = []
    for b in BATCHES:
        p = root / "step1" / "data" / b / "README.md"
        if p.exists():
            hs = [l.lstrip("# ").strip() for l in p.read_text().splitlines() if l.startswith("## ")
                  and re.search("rule|famil|gold|check list|unit|strat|observ", l, re.I)]
            out.append(f"- **{b}**: `step1/data/{b}/README.md` — " + "; ".join(f"\"{h}\"" for h in hs))
    return out


def caveats(root: Path, results: dict) -> list[str]:
    out = []
    for b in BATCHES:
        gold = read_jsonl(root / "step1" / "data" / b / "gold.jsonl")
        na = [g for g in gold if g.get("needs_annotation") or g["status"] == "needs_annotation"]
        if na:
            out.append(f"- {b}: {len(na)} slots needs_annotation (cannot be scored before human annotation): "
                       + ", ".join(sorted({g['question_id'] for g in na})))
        conflict = [g for g in gold if g.get("unit_conflict")]
        if conflict:
            out.append(f"- {b}: {len(conflict)} slots unit_conflict = true (rule unit and evidence unit disagree; flagged only, value not changed)")
        if results[b]["unanchored"]:
            out.append(f"- {b}: {len(results[b]['unanchored'])} reported numeric slots have no anchor (see the batch's \"anchors details\")")
    sel = root / "step1/entities/13f_selected.csv"
    if sel.exists():
        prov = [r for r in csv.DictReader(sel.open()) if "provisional" in (r.get("status") or "")]
        if prov:
            out.append(f"- 13f: the manager list is still provisional ({len(prov)} rows of 13f_selected.csv have a status containing provisional); "
                       "questions and gold are all recomputed when the list changes")
    out.append("- Each batch README's \"Manual check list\" lists what the rules do not cover and a person needs to look at")
    return out


def render(root: Path, ctx: dict, results: dict, rebuilt: bool, manifest: dict) -> str:
    m = ctx["manifest"]
    lines = ["# Step 1 dataset", "",
             f"Generated by `python -m step1.verify_dataset{' --rebuild' if rebuilt else ''}`; do not edit by hand.", "",
             "## Version", "",
             f"- git HEAD: `{git_version(root)}`",
             f"- corpus snapshot: `{m.get('snapshot_id')}` (`{SNAP}`, {m.get('n_docs')} chunks, {m.get('n_filings')} filings, "
             f"freeze date {m.get('freeze_date')}, window {m.get('window')})", "",
             "## Check results", "", table(results, rebuilt), "",
             "- sources: the manifest has a snapshot id, docs.jsonl matches docs_sha256, and every accession cited by gold/facts is in sources.jsonl",
             "- time: each question's as-of date (statements / NQDC: fiscal period end from companies.json; 13F: quarter end) is in the window; every cited filing was filed on or before the freeze date",
             "- counts: question ids are unique, every question has gold, and no gold points to a nonexistent question",
             "- anchors: reported numeric slots have an anchor in anchors.jsonl with the same entity, concept, period, value (and scope); missing ones are a WARN",
             "- determinism: with `--rebuild` each builder writes to a temp dir and the md5 of every file is compared",
             "- leakage: question text contains no gold value (integers ≥ 1,000, decimals, printed, text of ≥ 6 characters) and no accession", ""]
    for b in BATCHES:
        r = results[b]
        years = sorted({y for _, y in r["family_year"]})
        lines += [f"## {b}", "",
                  f"{r['n_questions']} questions, {len(r['entity'])} entities, {r['n_gold']} gold slots.", "",
                  "| family | " + " | ".join(map(str, years)) + " | total |", "|---|" + "---|" * (len(years) + 1)]
        for fam in sorted(r["family"]):
            lines.append(f"| {fam} | " + " | ".join(str(r["family_year"].get((fam, y), 0)) for y in years)
                         + f" | {r['family'][fam]} |")
        lines.append("| total | " + " | ".join(str(r["year"].get(y, 0)) for y in years) + f" | {r['n_questions']} |")
        lines += ["", "The year is the calendar year of the as-of date (13F questions spanning two quarters take the later quarter).", "",
                  "gold slots by status: " + ", ".join(f"{k} {v}" for k, v in sorted(r["status"].items())), "",
                  "questions per entity: " + "; ".join(f"{e} {n}" for e, n in sorted(r["entity"].items())), ""]
        for c in ("sources", "time", "counts", "anchors", "leakage", "determinism"):
            extra = r[c][1][1:]
            if extra and not (c == "determinism" and not rebuilt):
                lines += [f"{c} details:", "", "```", *[l.strip() for l in extra], "```", ""]
    if rebuilt:
        ok, notes = results["anchors_rebuild"]
        lines += ["## anchors rebuild", "", f"{mark(ok)} {notes[0]}", "", "```", *[l.strip() for l in notes[1:]], "```", ""]
    lines += ["## Rules list (in each batch README)", "", *readme_rules(root),
              "- corpus and anchors: `step1/CORPUS.md` sections 1, 3, 5", "",
              "## Known issues", "", *caveats(root, results), "",
              "## How to reproduce", "", "```bash", "conda activate numtrace",
              "env -u SEC_USER_AGENT python -B -m step1.statements --out /tmp/s1/statements --entities /tmp/s1/statements/companies.json",
              "python -B -m step1.thirteen_f_gold          # no --out; writes back to step1/data/13f/ and step1/entities/13f_cofiling_groups.csv",
              "python -B -m step1.nqdc_gold --out /tmp/s1/nqdc",
              "python -m step1.corpus anchors --facts step1/data/{statements,13f,nqdc}/facts.jsonl   # writes back to step1/data/anchors.jsonl; the order of facts sets the line order",
              "python -m step1.verify_dataset --rebuild    # runs the steps above into a temp dir and compares md5; does not change the repo",
              "```", "",
              "Rebuilding the corpus snapshot itself: see `step1/CORPUS.md` section 7 (needs network downloads). Builders that only read the cache do not go online; "
              "`--rebuild` unsets SEC_USER_AGENT.", "",
              "## File md5", "", "| file | md5 |", "|---|---|",
              *[f"| `{k}` | `{v}` |" for k, v in manifest.items()], ""]
    return "\n".join(lines)


def verify(root: Path = ROOT, rebuild_dir: Path | None = None, commands: dict | None = None) -> tuple[dict, dict]:
    ctx = context(root)
    results = {b: check_batch(root, b, ctx) for b in BATCHES}
    if rebuild_dir is not None:
        rb = rebuild(root, rebuild_dir, commands)
        for b in BATCHES:
            results[b]["determinism"] = rb.get(b, (False, ["no builder"]))
        results["anchors_rebuild"] = rb.get("anchors", (False, ["no builder"]))
    return ctx, results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify the Step 1 dataset and write step1/DATASET.md")
    ap.add_argument("--rebuild", action="store_true", help="rerun every builder into a temp dir and compare md5")
    ap.add_argument("--out", type=Path, default=ROOT / "step1" / "DATASET.md")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="s1-verify-") as tmp:
        ctx, results = verify(ROOT, Path(tmp) if args.rebuild else None)
    manifest = md5_manifest(ROOT)
    print(table(results, args.rebuild))
    if args.rebuild:
        ok, notes = results["anchors_rebuild"]
        print(f"\nanchors rebuild: {mark(ok)} {notes[0]}")
    for b in BATCHES:
        for c in CHECKS:
            extra = results[b][c][1][1:]
            if extra and not (c == "determinism" and not args.rebuild):
                print(f"\n[{b}] {c}")
                print("\n".join(extra))
    if args.rebuild:
        print("\n[anchors] determinism\n" + "\n".join(results["anchors_rebuild"][1][1:]))
    print("\nMANIFEST (md5):")
    print(json.dumps(manifest, indent=1))
    args.out.write_text(render(ROOT, ctx, results, args.rebuild, manifest))
    print(f"\nwrote {args.out}")
    failed = any(results[b][c][0] is False for b in BATCHES for c in CHECKS) or \
        (args.rebuild and results["anchors_rebuild"][0] is False)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
