"""Score a filled spot-check CSV (step1.spot_check): gold accuracy with Wilson 95% intervals and Cohen's kappa.

    python -m step1.spot_check_score ~/Desktop/NumTrace_raw_dataset_spot_check_v1.csv [--json out.json]

Labels: 1 / 0 (also y/n, yes/no, the Chinese right/wrong characters, ok/wrong); na or empty is not judged. A slot is correct for a reviewer when
both its value and its binding are 1. "AB" counts a slot correct only when both reviewers say so (judged by both).
A question is correct when every judged slot is correct; it is skipped while any of its slots is unjudged.
Only sampled=yes rows count: the appendix-only rows are not a random sample.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

# \u5bf9 / \u9519 are the Chinese "right" / "wrong" labels, escaped to keep the source ASCII
YES = {"1", "y", "yes", "\u5bf9", "ok", "correct", "true"}
NO = {"0", "n", "no", "\u9519", "wrong", "incorrect", "false"}
RATERS = ("A", "B", "AB")


def label(s: str | None) -> int | None:
    t = (s or "").strip().lower()
    if t in YES:
        return 1
    if t in NO:
        return 0
    if t in ("", "na", "n/a", "-"):
        return None
    raise ValueError(f"unknown label {s!r}")


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def kappa(pairs: list[tuple[int, int]]) -> float:
    """Cohen's kappa for two raters on binary labels; nan when chance agreement is 1 (both constant and equal)."""
    n = len(pairs)
    if n == 0:
        return math.nan
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return math.nan if pe == 1 else (po - pe) / (1 - pe)


def slot_correct(row: dict, r: str) -> int | None:
    if r == "AB":
        a, b = slot_correct(row, "A"), slot_correct(row, "B")
        return None if a is None or b is None else a & b
    v, b = label(row[f"reviewer_{r}_value"]), label(row[f"reviewer_{r}_binding"])
    if v is None and b is None:
        return None
    if v == 0 or b == 0:
        return 0
    return 1 if v == 1 and b == 1 else None


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.DictReader(fh) if r["sampled"] == "yes"]


def score(rows: list[dict]) -> dict:
    groups = {"all": rows}
    for key in ("batch", "family"):
        for r in rows:
            groups.setdefault(f"{key}:{r[key]}", []).append(r)
    out = {"accuracy": {}, "kappa": {}, "disagreements": []}
    for g, rs in groups.items():
        res = {}
        for rater in RATERS:
            sl = [slot_correct(r, rater) for r in rs]
            judged = [c for c in sl if c is not None]
            byq = defaultdict(list)
            for r, c in zip(rs, sl):
                byq[r["question_id"]].append(c)
            qs = [all(cs) for cs in byq.values() if None not in cs]
            for unit, xs in (("slot", judged), ("question", qs)):
                k, n = sum(xs), len(xs)
                lo, hi = wilson(k, n)
                res[f"{rater}_{unit}"] = {"k": k, "n": n, "acc": k / n if n else math.nan, "lo": lo, "hi": hi}
        out["accuracy"][g] = res
    for what in ("value", "binding", "slot"):
        pairs = []
        for r in rows:
            if what == "slot":
                a, b = slot_correct(r, "A"), slot_correct(r, "B")
            else:
                a, b = label(r[f"reviewer_A_{what}"]), label(r[f"reviewer_B_{what}"])
            if a is not None and b is not None:
                pairs.append((a, b))
        out["kappa"][what] = {"n": len(pairs), "kappa": kappa(pairs),
                              "agree": sum(a == b for a, b in pairs) / len(pairs) if pairs else math.nan}
    for r in rows:
        a, b = slot_correct(r, "A"), slot_correct(r, "B")
        if a is not None and b is not None and a != b:
            out["disagreements"].append(f"{r['question_id']} {r['slot']}")
    return out


def report(res: dict) -> str:
    lines = [f"{'group':<26}{'rater':<6}{'slot acc [95% CI] (k/n)':<34}{'question acc [95% CI] (k/n)'}"]
    for g, res_g in res["accuracy"].items():
        for rater in RATERS:
            cells = []
            for unit in ("slot", "question"):
                x = res_g[f"{rater}_{unit}"]
                cells.append(f"{x['acc']:.3f} [{x['lo']:.3f}, {x['hi']:.3f}] ({x['k']}/{x['n']})" if x["n"] else "-")
            lines.append(f"{g:<26}{rater:<6}{cells[0]:<34}{cells[1]}")
    lines.append("")
    for what, k in res["kappa"].items():
        lines.append(f"kappa A vs B, {what:<8} {k['kappa']:.3f} (agreement {k['agree']:.3f}, n={k['n']})")
    if res["disagreements"]:
        lines.append(f"\n{len(res['disagreements'])} slots to adjudicate:")
        lines += [f"  {d}" for d in res["disagreements"]]
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    res = score(read(args.csv))
    print(report(res))
    if args.json:
        args.json.write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    main()
