"""One definition of "the same period", used by claim-to-slot alignment (harness.claims) and by the binding check
(harness.scoring). Fiscal labels are read with the entity's calendar from step1/entities/companies.json."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Optional

COMPANIES = Path(__file__).resolve().parents[1] / "step1" / "entities" / "companies.json"

_DATE = r"\d{4}-\d{2}-\d{2}"
Period = tuple[Optional[date], date, bool]


def _fiscal(fy: str, part: str, cal: dict) -> Optional[tuple[date, date]]:
    """(start, end) of FY2024 / Q3 FY2024 / 9M FY2024 in the entity's calendar (companies.json fiscal_years and
    fiscal_quarters; a quarter starts the day after the previous one ends, Q4 ends with the year)."""
    year, quarters = cal.get("fiscal_years", {}).get(fy), cal.get("fiscal_quarters", {})
    start = date.fromisoformat(year["start"]) if year else \
        next((date.fromisoformat(q["ytd_start"]) for k, q in quarters.items() if k.startswith(fy)), None)

    def end(n: int) -> Optional[date]:
        if n == 4:
            return date.fromisoformat(year["end"]) if year else None
        q = quarters.get(f"{fy}Q{n}")
        return date.fromisoformat(q["end"]) if q else None
    if part == "FY":
        e = end(4)
        return (start, e) if start and e else None
    if part[0] == "Q":
        n = int(part[1])
        e, s = end(n), start if n == 1 else end(n - 1)
        return (s + timedelta(days=1) if n > 1 and s else s, e) if s and e else None
    e = end(int(part[0]) // 3)
    return (start, e) if start and e else None


def period_of(p: Optional[str], cal: Optional[dict] = None) -> Optional[Period]:
    """A period string as (start, end, is_label). An ISO date is an instant (None, d); "d1..d2" an interval. A label
    names an interval that also stands for its end date: a calendar quarter (2023Q1, Q1 2023) or, with the entity's
    fiscal calendar, FY2024 / Q3 FY2024 / 9M FY2024. None for anything else (free text, ranges of labels)."""
    p = (p or "").strip()
    if re.fullmatch(_DATE, p):
        return None, date.fromisoformat(p), False
    if m := re.fullmatch(rf"({_DATE})\.\.({_DATE})", p):
        return date.fromisoformat(m[1]), date.fromisoformat(m[2]), False
    if m := re.fullmatch(r"(\d{4})Q([1-4])|Q([1-4]) (\d{4})", p):
        y, q = int(m[1] or m[4]), int(m[2] or m[3])
        return date(y, 3 * q - 2, 1), date(y, 3 * q, 30 if q in (2, 3) else 31), True
    if cal and (m := re.fullmatch(r"(?:(Q[1-4]|[369]M) )?(FY\d{4})|(FY\d{4}) ?(Q[1-4])", p)):
        se = _fiscal(m[2] or m[3], m[1] or m[4] or "FY", cal)
        return (se[0], se[1], True) if se else None
    return None


@cache
def _calendars() -> dict[str, dict]:
    return {c["name"]: c for c in json.loads(COMPANIES.read_text())} if COMPANIES.exists() else {}


def calendar(entity: Optional[str]) -> Optional[dict]:
    """The entity's fiscal calendar (a companies.json entry), None for an entity not listed there (13F filers)."""
    return _calendars().get(entity) if entity else None


def _match(x: Period, y: Period) -> bool:
    if (x[0] is None) == (y[0] is None):
        return x[:2] == y[:2]
    instant, other = (x, y) if x[0] is None else (y, x)
    return other[2] and other[1] == instant[1]


# Free text ("FY2022 (December 31, 2022)", "Three Months Ended June 30, 2023 (Q2 FY2023)") is read as the periods it
# names: fiscal and quarter labels, "N months ended <date>", "as of <date>", date ranges and bare dates. Each one must
# be the gold period, so a label and a date that disagree keep the mismatch.
_MON = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|"
        r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b\.?")
_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                            "nov", "dec"))}
# A date to the day, or to the month ("June 2023"); a day without a year only opens a range ("Jan 1 - Sep 30, 2023").
_TEXT_DATE = (rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}}|{_MON}\s*\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?"
              rf"|\d{{1,2}}\s+{_MON},?\s*\d{{4}}|{_MON},?\s*\d{{4}})(?![\d/-])")
_NUMBERS = {"one": 1, "two": 2, "three": 3, "six": 6, "nine": 9, "twelve": 12, "thirteen": 13, "twenty-six": 26,
            "thirty-nine": 39, "fifty-two": 52, "fifty-three": 53}
_UNIT_MONTHS = {"month": 1, "quarter": 3, "week": 7 / 30.44}
_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_DOC_AFTER = re.compile(r"\s*(?:Form\s+)?(?:10-[KQ]|20-F|8-K|13F|DEF\s*14A|proxy|filing|annual report|quarterly report)\b",
                        re.I)
_IN_DOC_BEFORE = re.compile(r"\b(?:in|from|per|see)\s+(?:the\s+)?$", re.I)
_QUOTED_BEFORE = re.compile(r"\b(?:says|labels?\s+it|label(?:l)?ed(?:\s+it)?(?:\s+as)?|calls\s+it|tagged(?:\s+as)?)\s*$",
                            re.I)
_FREE = [
    # dates that are not the period: when a document was filed, a plan took effect ...
    ("skip", re.compile(rf"\b(?:filed|effective|restated|dated|amended|issued|signed)(?:\s+on)?\s+{_TEXT_DATE}", re.I)),
    ("range", re.compile(rf"(?P<a>{_TEXT_DATE})\s*(?:-|–|—|to|through)\s*(?P<b>{_TEXT_DATE})", re.I)),
    ("ended", re.compile(rf"\b(?:fiscal\s+)?(?P<n>{'|'.join(_NUMBERS)}|\d{{1,2}})[\s-]+(?:fiscal\s+)?"
                         rf"(?P<u>month|quarter|week)s?\s+(?:period\s+)?end(?:ed|ing)\s+(?P<d>{_TEXT_DATE})", re.I)),
    ("ended", re.compile(rf"\b(?P<n>[369]|12)M\s+end(?:ed|ing)\s+(?P<d>{_TEXT_DATE})", re.I)),
    ("ended", re.compile(rf"\b(?:fiscal\s+)?(?:(?:first|second|third|fourth)\s+)?(?:fiscal\s+)?(?P<u>quarter|year)"
                         rf"[\s-]+end(?:ed|ing)\s+(?P<d>{_TEXT_DATE})", re.I)),
    ("label", re.compile(r"\b(?:(?P<p>Q[1-4]|[369]M)\s+)?FY\s?(?P<fy>\d{4})(?:\s?(?P<q>Q[1-4]))?\b(?![-–/]\d)", re.I)),
    ("label", re.compile(r"\b(?P<ord>first|second|third|fourth)\s+(?P<fq>fiscal\s+)?quarter\s+(?:of\s+)?"
                         r"(?P<fy2>fiscal\s+(?:year\s+)?)?(?P<y>\d{4})\b", re.I)),
    ("label", re.compile(r"\bfiscal\s+(?:year\s+)?(?P<fy>\d{4})\b(?![-–/]\d)", re.I)),
    ("label", re.compile(r"\b(?:(?P<y>\d{4})\s?Q(?P<q>[1-4])|Q(?P<q2>[1-4])\s+(?P<y2>\d{4}))\b", re.I)),
    ("at", re.compile(_TEXT_DATE, re.I)),
]


def _text_date(s: str, year: Optional[int] = None):
    """A date, or (year, month) for a date stated to the month; a day without a year takes ``year``."""
    s = s.strip().rstrip(".")
    if re.fullmatch(_DATE, s):
        return date.fromisoformat(s)
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s):
        return date(int(m[3]), int(m[1]), int(m[2]))
    mon = _MONTHS[re.match(r"\d*\s*([A-Za-z]{3})", s)[1].lower()]
    nums = [int(n) for n in re.findall(r"\d+", s)]
    if len(nums) == 2:
        day, y = (nums[0], nums[1]) if nums[0] < 100 else (nums[1], nums[0])
        return date(y, mon, day)
    if nums[0] > 31:
        return nums[0], mon
    return date(year, mon, nums[0]) if year else None


def _end_ok(end, d: date) -> bool:
    return d == end if isinstance(end, date) else (d.year, d.month) == end


def _months(start: date, end: date) -> int:
    return round(((end - start).days + 1) / 30.44)


def _free_text(p: str, cal: Optional[dict]) -> Optional[list]:
    """The periods free text names, each as (names an interval, test on the gold period and its label); None when
    it names none, or a date it cannot read. A label the entity's calendar cannot place matches only the same label."""
    comps, text = [], p
    for kind, rx in _FREE:
        for m in list(rx.finditer(text)):
            if kind == "label" and ((_DOC_AFTER.match(text, m.end()) and _IN_DOC_BEFORE.search(text, 0, m.start()))
                                    or _QUOTED_BEFORE.search(text, 0, m.start())):
                continue    # "as presented in the Q1 FY2025 10-Q", "metadata labels it FY2023": not the claim's period
            g = m.groupdict()
            if kind == "range":
                b = _text_date(g["b"])
                a = _text_date(g["a"], b.year if isinstance(b, date) else None)
                if not (isinstance(a, date) and isinstance(b, date)):
                    return None
                if a > b:
                    a = a.replace(year=a.year - 1)
                comps.append((True, lambda y, gl, x=(a, b, False): _match(x, y)))
            elif kind == "ended":
                d = _text_date(g["d"])
                if d is None:
                    return None
                u = (g.get("u") or "month").lower()
                n = 12 if u == "year" else 3 if u == "quarter" and not g.get("n") else \
                    round((_NUMBERS.get((g["n"] or "").lower()) or int(g["n"])) * _UNIT_MONTHS[u])
                # a duration stands for its end date, like a label; against an interval its length must agree
                comps.append((True, lambda y, gl, n=n, d=d: _end_ok(d, y[1]) and (y[0] is None or _months(y[0], y[1]) == n)))
            elif kind == "label":
                if g.get("ord"):
                    q = _ORDINAL[g["ord"].lower()]
                    lab = f"Q{q} FY{g['y']}" if g.get("fq") or g.get("fy2") else f"{g['y']}Q{q}"
                elif g.get("fy"):
                    part = g.get("p") or g.get("q")
                    lab = f"{part.upper()} FY{g['fy']}" if part else f"FY{g['fy']}"
                else:
                    lab = f"{g['y'] or g['y2']}Q{g['q'] or g['q2']}"
                x = period_of(lab, cal)
                comps.append((True, lambda y, gl, x=x, lab=lab: (x is not None and _match(x, y)) or lab == gl))
            elif kind == "at":
                d = _text_date(m[0])
                if d is None:
                    return None
                # a date names an instant; beside a label or duration it is that period's end date
                comps.append((False, lambda y, gl, d=d: _end_ok(d, y[1])))
            text = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    return comps or None


def same_period(a: Optional[str], b: Optional[str], entity: Optional[str] = None,
                b_start: Optional[date] = None, b_end: Optional[date] = None) -> bool:
    """Period ``a`` (a claim's, a fact's) is period ``b`` (a slot's or gold fact's label; ``b_start``/``b_end`` its
    dates when known): the same string, or the same interval / instant once both are read as dates. A label matches
    an instant only at its own end date (2023Q1 = 2023-03-31, FY2024 = the fiscal year-end); an instant never
    matches an interval. Free text in ``a`` matches when every period it names is ``b``."""
    if a == b:
        return True
    cal = calendar(entity)
    x = period_of(a, cal)
    y = (b_start, b_end, False) if b_end else period_of(b, cal)
    if y is None:
        return False
    if x is not None:
        return _match(x, y)
    comps = _free_text(a or "", cal)
    if not comps:
        return False
    # against an interval, dates alone are instants: something must name the interval itself
    return all(test(y, b) for _, test in comps) and (y[0] is None or any(iv for iv, _ in comps))
