"""Deterministic tools used by the agent and the scorers.

* ``safe_eval`` - the arithmetic tool (AST whitelist, no names beyond the given variables).
* ``CodeTool`` - optional Python tool (off by default), sandboxed in a subprocess with a timeout.
* number parsing / formatting, table-line parsing and the 13F unit rule.
"""
from __future__ import annotations

import ast
import math
import operator
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from typing import Optional

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUN = {"abs": abs, "round": lambda x, n=0: round(x, int(n)), "min": min, "max": max, "sum": lambda *a: sum(a)}


class ArithmeticError_(ValueError):
    pass


def safe_eval(expr: str, variables: Optional[dict[str, float]] = None) -> float:
    """Evaluate +,-,*,/,**,% over numbers and named variables. Anything else is rejected."""
    variables = variables or {}

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ArithmeticError_(f"unknown variable {node.id!r}")
            return float(variables[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise ArithmeticError_("exponent too large")
            return float(_BIN[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
            return float(_UN[type(node.op)](ev(node.operand)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUN and not node.keywords:
            return float(_FUN[node.func.id](*[ev(a) for a in node.args]))
        raise ArithmeticError_(f"disallowed expression element: {ast.dump(node)[:60]}")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ArithmeticError_(str(e)) from e
    try:
        out = ev(tree)
    except (TypeError, ZeroDivisionError, OverflowError) as e:
        raise ArithmeticError_(str(e)) from e
    if math.isnan(out) or math.isinf(out):
        raise ArithmeticError_("non-finite result")
    return out


class CodeTool:
    """Optional code tool: runs a Python snippet in an isolated subprocess and returns stdout.
    Disabled unless ``AgentConfig.code_tool`` is True."""

    def __init__(self, timeout_s: float = 5.0) -> None:
        self.timeout_s = timeout_s

    def run(self, code: str) -> str:
        proc = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                              timeout=self.timeout_s)
        if proc.returncode != 0:
            raise RuntimeError(f"code tool failed: {proc.stderr.strip()[:400]}")
        return proc.stdout.strip()


_NUM_RE = r"\(?-?\$?\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?-?\$?\s?\d+(?:\.\d+)?\)?"


def parse_printed_number(s: str) -> float:
    s = s.strip().replace("$", "").replace(" ", "")
    if s in ("—", "–", "-"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    digits = re.sub(r"[^\d.]", "", s)
    v = float(digits)
    return -v if neg else v


@dataclass
class TableLine:
    label: str
    number_text: str
    value: float
    start: int
    end: int
    line_no: int
    text: str


_LINE_RE = re.compile(
    r"^(?P<label>[A-Za-z][^\n|]*?)\s*(?:\.{2,}|…+|:|\||\t|\s{2,})\s*\$?\s*(?P<num>\(?-?\d[\d,]*(?:\.\d+)?\)?)\s*$"
)


_CELL_RE = re.compile(r"^(?:(?P<hdr>.*\S):\s+)?(?P<num>\(?\s?-?\$?\s?\d[\d,]*(?:\.\d+)?\s?\)?|\$?[—–])$")


def _pipe_cells(text: str, line: str, pos: int, line_no: int) -> list[TableLine]:
    """One TableLine per numeric cell of 'Row label | Header: 1,234 | Header: 5,678'. Each spans from the
    line start to the end of its cell, so it quotes uniquely and the number is the last one in it."""
    parts, offs, k = line.split(" | "), [], 0
    for p in parts:
        offs.append(k)
        k += len(p) + 3
    row = parts[0] if not _CELL_RE.match(parts[0].strip()) else ""
    nums = [p.strip() for p in (parts[1:] if row else parts) if _CELL_RE.match(p.strip())]
    if nums and all(re.fullmatch(r"(?:19|20)\d\d", n) for n in nums):
        return []
    out = []
    for p, o in zip(parts[1:] if row else parts, offs[1:] if row else offs):
        m = _CELL_RE.match(p.strip())
        if not m:
            continue
        label = ", ".join(x for x in (row, m.group("hdr")) if x)
        if not label:
            continue
        end = pos + o + len(p.rstrip())
        out.append(TableLine(label=label, number_text=m.group("num"), value=parse_printed_number(m.group("num")),
                             start=pos, end=end, line_no=line_no, text=text[pos:end]))
    return out


def parse_table_lines(text: str) -> list[TableLine]:
    """Every 'label ... number' line in a document, with char offsets. Rendered filing-table rows
    ('Label | Header: 1,234 | ...') give one entry per numeric cell."""
    out: list[TableLine] = []
    pos = 0
    for i, line in enumerate(text.split("\n")):
        m = _LINE_RE.match(line.rstrip())
        if m:
            label = m.group("label").strip().rstrip(".").strip()
            out.append(TableLine(label=label, number_text=m.group("num"), value=parse_printed_number(m.group("num")),
                                 start=pos, end=pos + len(line), line_no=i, text=line))
        elif " | " in line:
            out += _pipe_cells(text, line, pos, i)
        pos += len(line) + 1
    return out


_UNIT_TEXT_RE = re.compile(r"\((?:\$ |dollars |amounts )?(?:in )?(thousands|millions|billions)(?:[, ][^()]*)?\)|\(x\s?\$\s?1,?000\)"
                           r"|in (thousands|millions|billions) of dollars", re.I)
SCALE_WORDS = {"thousands": 1e3, "millions": 1e6, "billions": 1e9, "thousand": 1e3, "million": 1e6, "billion": 1e9}


def detect_unit_text(text: str) -> Optional[str]:
    m = _UNIT_TEXT_RE.search(text)
    return m.group(0) if m else None


def unit_text_at(text: str, pos: int) -> Optional[str]:
    """Unit statement governing the number at ``pos``. For a rendered table row ('a | b: 1 | ...') it is the
    nearest one above it, looking only across table rows and short label lines (a paragraph of prose ends
    the search), else none (dollars): a proxy must not lend '(in millions)' from another table to a 402(i)
    row. Other lines use the document's first unit statement."""
    lines = text[:pos].split("\n")
    line_end = text.find("\n", pos)
    cur = lines[-1] + text[pos:line_end if line_end >= 0 else len(text)]
    if " | " not in cur:
        return detect_unit_text(text)
    for ln in [cur] + lines[-2::-1][:80]:
        found = _UNIT_TEXT_RE.findall(ln) and detect_unit_text(ln)
        if found:
            return found
        if " | " not in ln and len(ln) >= 150:
            return None
    return None


def scale_from_unit_text(unit_text: Optional[str]) -> float:
    if not unit_text:
        return 1.0
    t = unit_text.lower()
    if "x$1,000" in t.replace(" ", "") or "x$1000" in t.replace(" ", ""):
        return 1e3
    for w, s in SCALE_WORDS.items():
        if w in t:
            return s
    return 1.0


F13_UNIT_CHANGE = date(2023, 1, 3)


def f13_scale(filing_date: Optional[str]) -> float:
    """Form 13F: values in thousands for filings submitted before 3 Jan 2023, dollars from then (Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03)."""
    if not filing_date:
        raise ValueError("13F unit rule needs the filing date")
    return 1e3 if date.fromisoformat(filing_date[:10]) < F13_UNIT_CHANGE else 1.0


def fmt_usd(value: float, sig: int = 3) -> str:
    """4.12e9 -> '$4.12 billion'; -3.66e7 -> '-$36.6 million'; 950 -> '$950'."""
    sign = "-" if value < 0 else ""
    v = abs(value)
    for word, s in (("trillion", 1e12), ("billion", 1e9), ("million", 1e6)):
        if v >= s:
            x = v / s
            digits = max(sig - len(str(int(x))), 0)
            return f"{sign}${x:.{digits}f} {word}"
    if v >= 1000:
        return f"{sign}${v:,.0f}"
    return f"{sign}${v:.{max(sig - len(str(int(v))), 0)}f}"


_MONEY_RE = re.compile(
    r"(?:(?P<neg>[-−])(?=\$))?\$\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s?(?P<word>thousand|million|billion|trillion|[kKmMbB]n?)\b)?"
)
_WORD_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12, "k": 1e3, "m": 1e6, "mn": 1e6,
               "b": 1e9, "bn": 1e9}


@dataclass
class MoneyMention:
    text: str
    value: float
    rel_tol: float
    start: int
    end: int


def find_money(text: str) -> list[MoneyMention]:
    out = []
    for m in _MONEY_RE.finditer(text):
        num = m.group("num")
        word = (m.group("word") or "").lower()
        scale = _WORD_SCALE.get(word, 1.0)
        v = float(num.replace(",", "")) * scale
        decimals = len(num.split(".")[1]) if "." in num else 0
        half_ulp = 0.5 * (10 ** -decimals) * scale
        if scale == 1.0 and decimals == 0:
            half_ulp = 0.5
        if m.group("neg"):
            v = -v
        rel = half_ulp / abs(v) if v else 0.0
        out.append(MoneyMention(text=m.group(0).strip(), value=v, rel_tol=rel, start=m.start(), end=m.end()))
    return out


_COUNT_RE = re.compile(r"(?<![\w$.,])(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?![\d,.])\**\s+(?:[A-Za-z-]+\s+){0,3}?"
                       r"(?:shares|positions|holdings|securities|issuers)\b", re.I)
# The noun in a label before the number: "number_of_positions: 6", "Shares held: 1,105,208".
_COUNT_LABEL_RE = re.compile(r"(?:shares|positions|holdings|securities|issuers)\b[^\d\n|]{0,40}?[:=]\s*\**\s*"
                             r"(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?![\d,.%Q-])", re.I)


def find_counts(text: str) -> list[MoneyMention]:
    """Share counts and position counts ("895,136,175 shares", "6 positions", "positions: 6"): no "$", so
    find_money misses them."""
    out: dict[int, MoneyMention] = {}
    for rx in (_COUNT_RE, _COUNT_LABEL_RE):
        for m in rx.finditer(text):
            n = m.group("num")
            out.setdefault(m.start("num"), MoneyMention(text=n, value=float(n.replace(",", "")), rel_tol=0.0,
                                                        start=m.start("num"), end=m.end("num")))
    return sorted(out.values(), key=lambda m: m.start)


def values_match(a: float, b: float, rel_tol: float = 0.0, abs_tol: float = 0.5) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)) + 1e-9 * max(abs(a), abs(b)))


_ANY_NUM_RE = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?|[—–]")


def printed_in(text: str, value: float) -> bool:
    """``value`` is one of the numbers printed in ``text`` (either sign, at any scale scale_invariant_match tries)."""
    return any(scale_invariant_match(value, parse_printed_number(m.group(0)), 0.0) is not None
               for m in _ANY_NUM_RE.finditer(text))


def decimals_of(x: float) -> int:
    s = repr(float(x))
    if "e" in s:
        return 0
    frac = s.split(".")[1].rstrip("0")
    return len(frac)


def scale_invariant_match(claimed: float, printed: float, rel_tol: float,
                          scales=(1.0, 1e3, 1e6, 1e9, 1e-3), printed_decimals: Optional[int] = None) -> Optional[float]:
    """Return the scale s with claimed ≈ printed*s (at the claim's and the source's rounding), else None.
    The value verdict is scale-invariant by design: reading thousands as dollars is a *unit binding*
    failure (walk-through 2), not a value failure. Signs are ignored: filings print outflows negative and
    reports state them as positive amounts ("capital expenditures of $11.75 billion")."""
    claimed, printed = abs(claimed), abs(printed)
    if printed == 0:
        return 1.0 if abs(claimed) < 0.5 else None
    if printed_decimals is None:
        printed_decimals = decimals_of(printed)
    for s in scales:
        target = printed * s
        src_tol = min(0.5 * (10 ** -printed_decimals) * s, 0.005 * abs(target))
        if abs(claimed - target) <= rel_tol * abs(claimed) * 1.0001 + src_tol + 1e-9 * abs(target):
            return s
    return None
