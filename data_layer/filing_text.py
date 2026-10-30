"""Filing HTML -> plain text for the frozen corpus.

Paragraphs become lines. Each table row becomes one pipe-separated line whose numeric cells carry
their column header, e.g. ``Current portion of debt | 2025: 5,958 | 2024: 4,907``, so a number keeps
its column (period, 402(i) column) in the text a model sees, and ``harness.tools.parse_table_lines``
can bind it. EDGAR's split cells ("$" | "1,234" | ")") are merged and footnote markers dropped.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString

BLOCKS = ["p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]
NUM_RE = re.compile(r"^\(?-?\$?\s?\d[\d,]*(?:\.\d+)?\s?\)?%?$|^\$?[—–-]$")
FOOTNOTE_RE = re.compile(r"^\(\d{1,2}\)$|^\([a-z]\)$|^\*+$")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub("[\u200b-\u200d\ufeff]", "", s.replace("\xa0", " "))).strip()


def merge_cells(cells: list[str]) -> list[str]:
    """Join '$' with the next cell, ')' and '%' with the previous one; drop empties and footnote marks."""
    out: list[str] = []
    carry = ""
    for c in cells:
        c = _norm(c)
        if not c or FOOTNOTE_RE.match(c):
            continue
        if c == "$":
            carry = "$"
            continue
        if c in (")", "%", ")%") and out:
            out[-1] += c
            continue
        # "$" in its own cell before "(1,234)" must render as "($ 1,234 )", which the number parsers read as negative
        out.append("(" + carry + c[1:] if carry and c.startswith("(") else carry + c)
        carry = ""
    return out


def _is_num(c: str) -> bool:
    return bool(NUM_RE.match(c))


YEAR_RE = re.compile(r"^(?:19|20)\d\d$")
UNIT_RE = re.compile(r"^\(?[$#%]\)?$")
_AMT = r"\(?\$?\s?-?\d[\d,.]*\s?\)?\s?%?"
RANGE_RE = re.compile(rf"^{_AMT}\s*(?:to|/)\s*{_AMT}$")
RAISED_RE = re.compile(r"vertical-align:\s*super|(?<![\w-])top:\s*-")


def merged_index(cells: list[str]) -> list[int | None]:
    """Position in merge_cells(cells) of each input cell (None: dropped)."""
    out, n = [], 0
    for c in map(_norm, cells):
        if not c or FOOTNOTE_RE.match(c):
            out.append(None)
        elif c == "$":
            out.append(n)
        elif c in (")", "%", ")%") and n:
            out.append(n - 1)
        else:
            out.append(n)
            n += 1
    return out


def _grid(tr, above: dict[int, int]) -> tuple[list[tuple[int, int, str]], int]:
    """(first column, end column, text) of each cell, colspan-aware, and the row's width in columns. Columns still
    held by a rowspan cell above are skipped; ``above`` (column -> rows left) is updated for the next row."""
    out, col, held = [], 0, {c for c, n in above.items() if n > 0}
    for td in tr.find_all(["td", "th"]):
        while col in held:
            col += 1
        try:
            span = max(1, int(td.get("colspan") or 1))
            rows = max(1, int(td.get("rowspan") or 1))
        except ValueError:
            span, rows = 1, 1
        out.append((col, col + span, td.get_text(" ")))
        for c in range(col, col + span):
            if rows > 1:
                above[c] = rows  # decremented below, so it holds the next rows-1 rows
        col += span
    width = max([col] + [c + 1 for c in held])
    for c in list(above):
        above[c] -= 1
        if above[c] <= 0:
            del above[c]
    return out, width


def _grid_headers(grid: list, width: int, block: list) -> list[str | None] | None:
    """Column header of each merged cell of a data row, read from the header rows above by grid column; None when
    the header rows are not as wide as the row (the grid cannot be trusted)."""
    if not block or any(w != width for _, w in block):
        return None
    where = merged_index([t for *_, t in grid])
    first = {}
    for (s, _, t), m in zip(grid, where):
        if m is not None and re.search(r"[\d—–-]", t) and m not in first:
            first[m] = s
    out = [None] * (1 + max((m for m in where if m is not None), default=-1))
    for m, c in first.items():
        # a header row with a single cell right of the label column is a title ("(in millions)"), not a column name
        names = [_norm(t) for g, _ in block for s, e, t in g
                 if s <= c < e and _norm(t) and sum(1 for s2, _, t2 in g if s2 > 0 and _norm(t2)) > 1]
        out[m] = " ".join(names) or None
    return out


def render_table(table) -> list[str]:
    # footnote marks set as superscripts (or raised spans) in their own cells would read as numbers
    for t in table.find_all("sup") + table.find_all(style=RAISED_RE):
        t.decompose()
    out, headers, prev_header, block, grid_hdr, above = [], [], False, [], [], {}
    for tr in table.find_all("tr"):
        grid, width = _grid(tr, above)
        cells = merge_cells([t for *_, t in grid])
        if not cells:
            continue
        label, vals = (cells[0], cells[1:]) if not _is_num(cells[0]) else ("", cells)
        nums = [v for v in vals if _is_num(v)]
        # a row of ranges ("25 % to 81 %", "64 %/ 63 %") is data, though no cell is a plain number
        if not nums and "N/A" not in vals and not any(RANGE_RE.match(v) for v in vals) or nums and all(
                YEAR_RE.match(v) for v in nums):
            # The whole row, not just cells[1:]: a header row often has no cell over the label column. A header
            # stacked over several rows ("Executive" / "Contributions" / "($)") is joined column by column when the
            # rows line up: same number of cells, or a units row ("($)") under it with label cells on its left.
            h = headers[-1] if prev_header and headers else None
            if h and len(h) >= 2 and len(cells) >= len(h) and (
                    len(cells) == len(h) or all(UNIT_RE.match(c) for c in cells[-len(h):])):
                headers[-1] = cells[:-len(h)] + [f"{a} {b}" for a, b in zip(h, cells[-len(h):])]
            else:
                headers.append(cells)
            block = block + [(grid, width)] if prev_header else [(grid, width)]
            if any(s > 0 and _norm(t) for s, _, t in grid):  # a lone title over the label column keeps the last one
                grid_hdr = block
            prev_header = True
            out.append(" | ".join(cells))
            continue
        prev_header = False
        # Header by grid column where the header rows are as wide as this row. Otherwise by position from the right,
        # but only when the row has as many values as the header has columns (or one fewer, the header's label
        # cell): a shorter row has lost empty cells, and a wrong column name is worse than none.
        names = _grid_headers(grid, width, grid_hdr)
        if names is not None:
            off = 1 if label else 0
            vals = [f"{names[i + off]}: {v}" if _is_num(v) and i + off < len(names) and names[i + off] else v
                    for i, v in enumerate(vals)]
        else:
            header = next((h for h in reversed(headers) if len(h) >= len(vals)), None)
            if header and len(header) - len(vals) in (0, 1):
                vals = [f"{h}: {v}" if _is_num(v) else v for h, v in zip(header[-len(vals):], vals)]
        out.append(" | ".join([label] + vals) if label else " | ".join(vals))
    return out


def html_to_text(html: str | bytes) -> str:
    soup = BeautifulSoup(html, "lxml")
    for t in soup.find_all(["ix:header", "script", "style"]):
        t.decompose()
    for t in soup.find_all(style=re.compile(r"display:\s*none")):
        t.decompose()
    for table in soup.find_all("table"):
        if table.find_parent("table") is None:
            table.replace_with(NavigableString("\n" + "\n".join(render_table(table)) + "\n"))
    for t in soup.find_all(BLOCKS):
        t.append("\n")
    lines = (_norm(l) for l in soup.get_text("").split("\n"))
    return "\n".join(l for l in lines if l)
