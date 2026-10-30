"""Step 1: candidate Form 13F filers, as information for choosing ~30 (the final choice was made by hand).

For every seed filer and report periods 2022Q3-2026Q2 (WINDOW):
  * EDGAR submissions history: 13F-HR originals, 13F-HR/A amendments, 13F-NT notices;
  * each filing's primary document (cover and summary page): amendment type (RESTATEMENT replaces, NEW HOLDINGS
    adds; Form 13F Special Instruction 3, FAQ 58b/58c), confidential-treatment flags, report type, other included managers, table entry total;
  * the 2022Q3 / 2022Q4 information tables (originals and amendments): the unit by filing date (Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03) next to
    the unit implied by price per share (data_layer.thirteen_f); disagreements are flagged, never corrected.
Each filer is scored with the rubric in score(); suggest() marks 30 by kind quota and score.

    SEC_USER_AGENT='Name email' python -m step1.thirteen_f_candidates --discover  # full-text search for more seeds
    SEC_USER_AGENT='Name email' python -m step1.thirteen_f_candidates             # writes entities/13f_candidates.csv
    python -m step1.thirteen_f_candidates --offline                              # cache only, marked partial

Downloads are cached under data/raw/; a re-run reads the cache and makes no request.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import threading
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

from lxml import etree

from data_layer import edgar_fetch
from data_layer.thirteen_f import (Filing13F, parse_information_table, parse_primary_doc, row_unit_evidence,
                                   unit_diagnostics)

HERE = Path(__file__).resolve().parent
OUT = HERE / "entities" / "13f_candidates.csv"
WINDOW = ("2022-09-30", "2026-06-30")
QUARTERS = [f"{y}-{md}" for y in range(2022, 2027) for md in ("03-31", "06-30", "09-30", "12-31")
            if WINDOW[0] <= f"{y}-{md}" <= WINDOW[1]]
BOUNDARY = {"2022-09-30": "q3", "2022-12-31": "q4"}
UNIT_CHANGE = "2023-01-03"
HEAD_BYTES = 500_000  # larger information tables are unit-checked on their first rows only (HTTP Range)
MAX_OLDER = 100_000  # filings in older submission pages read per filer (~170 bytes each; JPMorgan needs 60k)
EFTS = "https://efts.sec.gov/LATEST/search-index"

# (CIK, kind, label, why it is on the long list). CIKs are checked against the EDGAR name on the first download;
# None = still to look up. Kinds: activist, hedge, multi_quant, insurer, bank, asset_mgr, endow_sov, small_adviser.
SEEDS: list[tuple[int | None, str, str, str]] = [
    (1336528, "activist", "Pershing Square", "pilot M1: six positions, clean unit-boundary control"),
    (921669, "activist", "Carl Icahn", "concentrated; files as an individual"),
    (1040273, "activist", "Third Point", "activist; options"),
    (1345471, "activist", "Trian", "few large positions"),
    (1418814, "activist", "ValueAct", "concentrated"),
    (1517137, "activist", "Starboard Value", "activist, mid-cap"),
    (1791786, "activist", "Elliott Investment Management", "activist; options"),
    (1159159, "activist", "Jana Partners", "activist"),
    (1649339, "activist", "Scion Asset Management", "tiny table, puts/calls, positions churn"),
    (1079114, "activist", "Greenlight Capital", "concentrated; options"),
    (1061768, "activist", "Baupost", "value, concentrated"),
    (1656456, "activist", "Appaloosa", "concentrated; options"),
    (1365341, "activist", "Cevian Capital", "European activist, few US positions"),
    (1582090, "activist", "Sachem Head", "activist"),
    (1535472, "activist", "Corvex", "activist"),
    (1559771, "activist", "Engaged Capital", "activist, small"),
    (1510281, "activist", "Saba Capital", "closed-end-fund activist, many rows"),
    (1709323, "activist", "Himalaya Capital", "very concentrated"),
    (1167483, "hedge", "Tiger Global", "growth; positions churn"),
    (1135730, "hedge", "Coatue", "growth"),
    (1061165, "hedge", "Lone Pine", "long/short"),
    (1103804, "hedge", "Viking Global", "long/short"),
    (1029160, "hedge", "Soros Fund Management", "family office; options"),
    (1536411, "hedge", "Duquesne Family Office", "family office"),
    (1035674, "hedge", "Paulson & Co", "event-driven"),
    (1112520, "hedge", "Akre Capital", "concentrated long-only"),
    (1056831, "hedge", "Fairholme", "very concentrated"),
    (949509, "hedge", "Oaktree", "credit; convertibles (PRN rows)"),
    (1549575, "hedge", "Dalal Street (Pabrai)", "tiny table"),
    (1423053, "multi_quant", "Citadel Advisors", "multi-manager; huge table, options"),
    (1273087, "multi_quant", "Millennium", "multi-manager; options"),
    (1037389, "multi_quant", "Renaissance Technologies", "quant; large table"),
    (1179392, "multi_quant", "Two Sigma Investments", "quant"),
    (1009207, "multi_quant", "D. E. Shaw", "quant / multi-strategy; options"),
    (1350694, "multi_quant", "Bridgewater", "macro; ETF-heavy"),
    (1167557, "multi_quant", "AQR", "quant"),
    (1603466, "multi_quant", "Point72", "multi-manager"),
    (1218710, "multi_quant", "Balyasny", "multi-manager"),
    (1446194, "multi_quant", "Susquehanna", "market maker; options-heavy"),
    (1595888, "multi_quant", "Jane Street", "market maker; options-heavy"),
    (1637460, "multi_quant", "Man Group", "quant, UK"),
    (1067983, "insurer", "Berkshire Hathaway",
     "pilot M2: 14 other included managers, both amendment types, confidential treatment"),
    (1096343, "insurer", "Markel", "insurer with an equity portfolio"),
    (60086, "insurer", "Loews", "conglomerate"),
    (915191, "insurer", "Fairfax Financial", "insurer, Canada"),
    (5272, "insurer", "AIG", "insurer"),
    (1099219, "insurer", "MetLife", "insurer; combination reports"),
    (20286, "insurer", "Cincinnati Financial", "insurer; long-held equities"),
    (11544, "insurer", "W. R. Berkley", "insurer"),
    (775368, "insurer", "Alleghany", "acquired by Berkshire in Oct 2022"),
    (1137774, "insurer", "Prudential Financial", "insurer, many subsidiaries"),
    (899051, "insurer", "Allstate", "insurer"),
    (886982, "bank", "Goldman Sachs Group", "many other included managers"),
    (895421, "bank", "Morgan Stanley", "many other included managers"),
    (19617, "bank", "JPMorgan Chase", "many other included managers"),
    (70858, "bank", "Bank of America", "bank"),
    (72971, "bank", "Wells Fargo", "bank"),
    (831001, "bank", "Citigroup", "bank"),
    (1610520, "bank", "UBS Group", "bank"),
    (1000275, "bank", "Royal Bank of Canada", "bank"),
    (312069, "bank", "Barclays", "bank"),
    (1163653, "bank", "Nomura", "bank"),
    (948046, "bank", "Deutsche Bank", "bank"),
    (720005, "bank", "Raymond James Financial", "broker-dealer"),
    (1390777, "bank", "Bank of New York Mellon", "custodian bank"),
    (96223, "bank", "Jefferies Financial Group", "investment-bank holding company"),
    (1364742, "asset_mgr", "BlackRock (old holding company)", "13F filer before the Oct 2024 reorganisation"),
    (2012383, "asset_mgr", "BlackRock (new holding company)", "13F filer after the Oct 2024 reorganisation"),
    (102909, "asset_mgr", "Vanguard Group", "largest index manager"),
    (315066, "asset_mgr", "FMR (Fidelity)", "active manager"),
    (80255, "asset_mgr", "T. Rowe Price Associates", "active manager"),
    (93751, "asset_mgr", "State Street", "index manager"),
    (73124, "asset_mgr", "Northern Trust", "custodian / manager"),
    (914208, "asset_mgr", "Invesco", "ETF manager"),
    (38777, "asset_mgr", "Franklin Resources", "active manager"),
    (902219, "asset_mgr", "Wellington Management Group", "active manager"),
    (1422848, "asset_mgr", "Capital Research Global Investors", "active manager"),
    (1214717, "asset_mgr", "Geode Capital Management", "index sub-adviser"),
    (354204, "asset_mgr", "Dimensional Fund Advisors", "factor manager"),
    (200217, "asset_mgr", "Dodge & Cox", "concentrated long-only"),
    (1697748, "asset_mgr", "ARK Investment Management", "ETF manager; positions churn"),
    (820027, "asset_mgr", "Ameriprise Financial", "manager / broker"),
    (1374170, "endow_sov", "Norges Bank", "sovereign"),
    (1166559, "endow_sov", "Gates Foundation Trust", "foundation, concentrated"),
    (919079, "endow_sov", "CalPERS", "public pension"),
    (1081019, "endow_sov", "CalSTRS", "public pension"),
    (1767640, "endow_sov", "Public Investment Fund", "sovereign, concentrated"),
    (1082621, "endow_sov", "Harvard Management Co", "endowment"),
    (1021944, "endow_sov", "Temasek Holdings", "sovereign"),
    (1283718, "endow_sov", "Canada Pension Plan Investment Board", "public pension"),
    (937567, "endow_sov", "Ontario Teachers' Pension Plan Board", "public pension"),
    (810265, "endow_sov", "New York State Common Retirement Fund", "public pension"),
    # found by full-text search (--discover): in-window 13F-HR/A amendments with NEW HOLDINGS / confidential treatment
    (1595082, "hedge", "Davidson Kempner", "full-text search: 92 NEW HOLDINGS amendments in window"),
    (1390113, "hedge", "Taconic Capital", "full-text search: 58 NEW HOLDINGS amendments in window"),
    (909661, "hedge", "Farallon", "full-text search: 48 NEW HOLDINGS amendments in window"),
    (1357550, "hedge", "Weiss Asset Management", "full-text search: 14 NEW HOLDINGS amendments in window"),
    (1257391, "hedge", "Whitebox Advisors", "full-text search: 13 NEW HOLDINGS amendments in window"),
    (1132716, "multi_quant", "O'Connor (UBS Asset Management)", "full-text search: 58 NEW HOLDINGS amendments"),
    (1438284, "multi_quant", "Oxford Asset Management", "full-text search: 32 NEW HOLDINGS amendments in window"),
    (1011443, "multi_quant", "HBK Investments", "full-text search: 24 NEW HOLDINGS amendments in window"),
    (1831577, "multi_quant", "Jump Financial", "full-text search: 10 NEW HOLDINGS amendments in window"),
    (1998597, "activist", "JANA Partners Management", "full-text search: confidential treatment; newer Jana entity"),
    (1334429, "activist", "Biglari Capital", "full-text search: confidential treatment"),
    (1001085, "insurer", "Brookfield Corp", "full-text search: 9 NEW HOLDINGS, 6 confidential-treatment amendments"),
    (1529735, "insurer", "MetLife Investment Management", "full-text search: 8 NEW HOLDINGS amendments; MetLife group"),
    (1393818, "asset_mgr", "Blackstone", "full-text search: 5 confidential-treatment amendments"),
    (1601407, "asset_mgr", "Troy Asset Management", "full-text search: 21 NEW HOLDINGS amendments in window"),
    (938582, "endow_sov", "Yale University", "full-text search: 6 NEW HOLDINGS amendments in window"),
    (931588, "endow_sov", "Provida Pension Fund Administrator", "full-text search: 19 NEW HOLDINGS amendments"),
    (1039565, "small_adviser", "Kahn Brothers", "pilot fixture: reported dollars in 2022"),
    (1790837, "small_adviser", "Bull Street Advisors", "pilot fixture: still reported thousands in Feb 2023"),
]


class NotCached(Exception):
    """Raised under --offline for a URL that is not in data/raw/ yet."""


class BudgetExceeded(RuntimeError):
    pass


OFFLINE = False
BUDGET = 290_000_000  # bytes one run may download; the task budget is ~300 MB
WORKERS = 4  # concurrent requests; _throttle still spaces their starts 0.2 s apart
_lock = threading.Lock()
_last = 0.0
_downloaded = 0


def _throttle():
    global _last
    with _lock:
        time.sleep(max(0.0, 0.2 - (time.monotonic() - _last)))
        _last = time.monotonic()


def _account(body: bytes) -> bytes:
    global _downloaded
    with _lock:
        _downloaded += len(body)
        if _downloaded > BUDGET:
            raise BudgetExceeded(f"stopped: {_downloaded / 1e6:.0f} MB downloaded in this run, over the budget")
    return body


def _pmap(fn, items) -> list:
    with ThreadPoolExecutor(WORKERS) as ex:
        return list(ex.map(fn, items))


def get(url: str) -> bytes:
    """edgar_fetch.get, spaced so this script never exceeds 5 requests per second."""
    if edgar_fetch.cache_path(url).exists():
        return edgar_fetch.get(url)
    if OFFLINE:
        raise NotCached(url)
    _throttle()
    return _account(edgar_fetch.get(url))


def _request(url: str, headers: dict, limit: int | None = None) -> bytes:
    if OFFLINE:
        raise NotCached(url)
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise RuntimeError("set SEC_USER_AGENT to 'Name email' before downloading from sec.gov")
    _throttle()
    edgar_fetch.throttle()  # machine-wide spacing shared with other downloaders
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity", **headers})
    with urllib.request.urlopen(req, timeout=60) as r:
        return _account(r.read(limit))


def head(url: str, nbytes: int) -> bytes:
    """First nbytes of a document (HTTP Range), cached beside the path the full file would use."""
    p = edgar_fetch.cache_path(url)
    p = p.with_name(f"{p.name}.head{nbytes}")
    if not p.exists():
        body = _request(url, {"Range": f"bytes=0-{nbytes - 1}"}, nbytes)  # limit too, in case Range is ignored
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    return p.read_bytes()


def efts(**params) -> dict:
    """EDGAR full-text search (cached by query)."""
    return json.loads(get(f"{EFTS}?{urlencode(sorted(params.items()))}"))


def index_url(cik: int, acc: str) -> str:
    return edgar_fetch.archive_url(cik, acc, f"{acc}-index.html")


def submissions_url(cik: int) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik:010d}.json"


def history(cik: int) -> tuple[str, list[dict], str]:
    """Filer name, its 13F-family filings for report periods in WINDOW, and a note when older submission pages
    were skipped. Older pages are read only when they reach into the window and hold at most MAX_OLDER filings."""
    sub = json.loads(get(submissions_url(cik)))
    older = [f for f in sub["filings"].get("files", []) if f["filingTo"] >= WINDOW[0]]
    n_older = sum(f["filingCount"] for f in older)
    skip = n_older > MAX_OLDER
    pages = [sub["filings"]["recent"]] + ([] if skip else _pmap(
        lambda f: json.loads(get(f"https://data.sec.gov/submissions/{f['name']}")), older))
    seen, out = set(), []
    for pg in pages:
        for i, form in enumerate(pg["form"]):
            acc = pg["accessionNumber"][i]
            if form.startswith("13F") and WINDOW[0] <= pg["reportDate"][i] <= WINDOW[1] and acc not in seen:
                seen.add(acc)
                out.append({"form": form, "acc": acc, "filed": pg["filingDate"][i], "period": pg["reportDate"][i],
                            "doc": pg["primaryDocument"][i].rsplit("/", 1)[-1], "size": pg["size"][i]})
    note = f"older submission pages not read ({n_older} filings)" if skip else ""
    return sub["name"], sorted(out, key=lambda f: (f["period"], f["filed"], f["acc"])), note


def file_number(s: str | None) -> str | None:
    """'028-04545' and '28-4545' are the same 13F file number."""
    parts = re.findall(r"\d+", s or "")
    return "-".join(str(int(p)) for p in parts) or None


def _t(el, path: str) -> str | None:
    e = el.find(path)
    return e.text.strip() if e is not None and e.text and e.text.strip() else None


def _yes(v: str | None) -> bool:
    return (v or "").lower() in ("true", "y", "yes", "1")


def _manager(el) -> dict:
    return {"name": _t(el, ".//{*}name"), "file_number": file_number(_t(el, ".//{*}form13FFileNumber"))}


def cover(xml: bytes) -> dict:
    """Cover- and summary-page fields of a 13F primary document."""
    r = etree.fromstring(xml.strip())
    rows, total = _t(r, ".//{*}tableEntryTotal"), _t(r, ".//{*}tableValueTotal")
    return {"report_type": _t(r, ".//{*}reportType"),
            "amendment_type": _t(r, ".//{*}amendmentType"),
            "conf_omitted": _yes(_t(r, ".//{*}isConfidentialOmitted")),
            "conf_denied_expired": _yes(_t(r, ".//{*}confDeniedExpired")),
            "conf_reason": _t(r, ".//{*}reasonForNonConfidentiality"),
            "file_number": file_number(_t(r, ".//{*}coverPage/{*}form13FFileNumber")),
            "included": [_manager(e) for e in r.findall(".//{*}otherManagers2Info/{*}otherManager2")],
            "reporting": [_manager(e) for e in r.findall(".//{*}otherManagersInfo/{*}otherManager")],
            "rows": int(rows) if rows else None,
            "value_total": int(total) if total else None}


def _submission_docs(cik: int, acc: str, root: bytes) -> list[bytes]:
    """XML documents with this root element from the full submission (<acc>.txt), when that is already cached
    (the pilot fetched its filings that way)."""
    p = edgar_fetch.cache_path(edgar_fetch.archive_url(cik, acc, f"{acc}.txt"))
    if not p.exists():
        return []
    return [d for d in re.findall(rb"<XML>\s*(.*?)</XML>", p.read_bytes(), re.S) if root in d[:300]]


def primary(cik: int, f: dict) -> bytes:
    docs = _submission_docs(cik, f["acc"], b"edgarSubmission")
    return docs[0] if docs else get(edgar_fetch.archive_url(cik, f["acc"], f["doc"]))


_ROW_END = re.compile(rb"</(?:\w+:)?infoTable>")
_ROOT = re.compile(rb"<((?:\w+:)?)informationTable\b")


def complete_rows(xml: bytes) -> bytes:
    """Close a truncated information table after its last complete row."""
    end = list(_ROW_END.finditer(xml))[-1].end()
    return xml[:end] + b"</" + _ROOT.search(xml).group(1) + b"informationTable>"


def info_table(cik: int, f: dict) -> tuple[bytes, bool]:
    """Information table of one filing, and whether it was cut to its first HEAD_BYTES."""
    docs = _submission_docs(cik, f["acc"], b"informationTable")
    if docs:
        return docs[0], False
    items = json.loads(get(edgar_fetch.archive_url(cik, f["acc"], "index.json")))["directory"]["item"]
    it = max((i for i in items if i["name"].lower().endswith(".xml") and i["name"] != f["doc"]),
             key=lambda i: int(i.get("size") or 0))
    url = edgar_fetch.archive_url(cik, f["acc"], it["name"])
    if int(it.get("size") or 0) <= HEAD_BYTES:
        return get(url), False
    return complete_rows(head(url, HEAD_BYTES)), True


def unit_check(cik: int, f: dict) -> dict:
    """Filing-date unit (Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03) next to the price-per-share evidence, for one filing."""
    meta = parse_primary_doc(primary(cik, f), f["acc"], f["filed"])
    xml, partial = info_table(cik, f)
    rows = parse_information_table(xml, meta)
    d = unit_diagnostics(Filing13F(meta=meta, rows=rows))
    ev = Counter(row_unit_evidence(r).evidence_unit for r in rows)
    usd, k = ev["USD"], ev["USD_thousands"]
    return {"rule_unit": meta.value_unit,
            "evidence_unit": "USD" if usd > k else "USD_thousands" if k > usd else "unclear",
            "rows_usd": usd, "rows_thousands": k, "rows_ambiguous": ev["ambiguous"],
            "median_price": round(d.median_implied_price, 2) if d.median_implied_price else None,
            "suspect": d.suspect, "reason": d.reason, "rows_checked": len(rows), "partial": partial,
            "putcall": sum(r.put_call is not None for r in rows), "cusips": len({r.cusip for r in rows})}


def profile(cik: int, kind: str, label: str, why: str) -> dict:
    """One CSV row: counts, co-filing structure, confidential treatment, size and the 2022Q3/Q4 unit check.
    Cover-page fields describe the cover pages read (covers_read); None means none was read."""
    name, fs, note = history(cik)
    errors = []

    def read(f):
        try:
            f.update(cover(primary(cik, f)), read=True)
        except NotCached:
            pass
        except BudgetExceeded:
            raise
        except Exception as e:  # one unreadable cover page should not drop the filer
            errors.append(f"{f['acc']}: {type(e).__name__}: {e}"[:200])
    _pmap(read, fs)
    orig = [f for f in fs if f["form"] == "13F-HR"]
    amend = [f for f in fs if f["form"] == "13F-HR/A"]
    notice = [f for f in fs if f["form"].startswith("13F-NT")]
    latest = max(orig, key=lambda f: (f["period"], f["filed"]), default={})
    n_mgrs = [len(f["included"]) for f in orig if f.get("read")]
    row = {"cik": cik, "name": name, "label": label, "kind": kind, "why": why,
           "covers_read": f"{sum(bool(f.get('read')) for f in fs)}/{len(fs)}",
           "file_number": next((f["file_number"] for f in fs if f.get("file_number")), None),
           "n_hr": len(orig), "quarters": len({f["period"] for f in orig} & set(QUARTERS)),
           "first_period": min((f["period"] for f in orig), default=None),
           "last_period": latest.get("period"),
           "n_hr_a": len(amend),
           "n_restatement": sum(f.get("amendment_type") == "RESTATEMENT" for f in amend),
           "n_new_holdings": sum(f.get("amendment_type") == "NEW HOLDINGS" for f in amend),
           "n_amend_unread": sum(not f.get("read") for f in amend),
           "n_nt": len(notice),
           "nt_detail": "; ".join(f"{f['period']} filed {f['filed']}" for f in notice),
           "n_combination": sum("COMBINATION" in (f.get("report_type") or "") for f in orig),
           "n_conf_omitted": sum(f.get("conf_omitted", False) for f in fs),
           "n_conf_denied_expired": sum(f.get("conf_denied_expired", False) for f in amend),
           "conf_reasons": "; ".join(sorted({f["conf_reason"] for f in amend if f.get("conf_reason")})),
           "amend_detail": "; ".join(f"{f['period']} {f.get('amendment_type') or '?'} filed {f['filed']}"
                                     for f in amend),
           "other_mgrs_latest": len(latest["included"]) if latest.get("read") else None,
           "other_mgrs_min": min(n_mgrs, default=None), "other_mgrs_max": max(n_mgrs, default=None),
           "reporting_for": "; ".join(sorted({m["name"] for f in fs for m in f.get("reporting", []) if m["name"]})),
           "latest_acc": latest.get("acc"), "latest_filed": latest.get("filed"),
           "latest_rows": latest.get("rows"), "latest_value": latest.get("value_total"),
           "latest_size": latest.get("size"),
           "_included": {m["file_number"] for f in fs for m in f.get("included", []) if m["file_number"]}}
    flags, amend22, unchecked = {"strong": [], "weak": []}, [], 0
    for f in fs:
        if f["period"] not in BOUNDARY or f["form"] not in ("13F-HR", "13F-HR/A"):
            continue
        try:
            u = unit_check(cik, f)
        except NotCached:
            unchecked += 1
            continue
        except BudgetExceeded:
            raise
        except Exception as e:
            errors.append(f"{f['acc']} unit check: {type(e).__name__}: {e}"[:200])
            continue
        tag = BOUNDARY[f["period"]]
        if f["form"] == "13F-HR" and f"{tag}_acc" not in row:
            row.update({f"{tag}_acc": f["acc"], f"{tag}_filed": f["filed"], f"{tag}_rows_total": f.get("rows"),
                        **{f"{tag}_{k}": v for k, v in u.items()}})
        else:
            amend22.append(f"{f['acc']} {f['period']} {f.get('amendment_type') or f['form']} filed {f['filed']}: "
                           f"rule {u['rule_unit']}, evidence {u['evidence_unit']}")
        # strong: both the R3 diagnostic and the row majority disagree with the rule; weak: only one of them does
        # (warrants under $1 or SPAC units at $10 can sway either one)
        off = u["evidence_unit"] not in (u["rule_unit"], "unclear")
        if u["suspect"] or off:
            flags["strong" if u["suspect"] and off else "weak"].append(
                f"{f['period']} {f['form']} {f['acc']} filed {f['filed']}: rule {u['rule_unit']}, evidence "
                f"{u['evidence_unit']} ({u['rows_usd']} rows read as dollars, {u['rows_thousands']} as thousands; "
                f"{u['reason']})")
    unread = len(fs) - sum(bool(f.get("read")) for f in fs)
    row["status"] = "; ".join(filter(None, [
        f"partial: {unread} cover pages and {unchecked} unit checks not downloaded yet" if unread or unchecked
        else "complete", note]))
    row["amend_2022"] = "; ".join(amend22)
    row["amend_2022_after_change"] = sum(f["form"] == "13F-HR/A" and f["period"] in BOUNDARY
                                         and f["filed"] >= UNIT_CHANGE for f in fs)
    row["n_unit_strong"], row["n_unit_weak"] = len(flags["strong"]), len(flags["weak"])
    row["unit_flags"] = " | ".join([f"STRONG {x}" for x in flags["strong"]] + [f"weak {x}" for x in flags["weak"]])
    row["errors"] = " | ".join(errors)
    row["submissions_url"] = submissions_url(cik)
    row["latest_url"] = index_url(cik, latest["acc"]) if latest else None
    for tag in ("q3", "q4"):
        row[f"{tag}_url"] = index_url(cik, row[f"{tag}_acc"]) if row.get(f"{tag}_acc") else None
    row["amend_urls"] = " ".join(index_url(cik, f["acc"]) for f in amend[:6])
    row["nt_urls"] = " ".join(index_url(cik, f["acc"]) for f in notice[:6])
    return row


def cross_refs(rows: list[dict]) -> None:
    """Which other candidates a filer lists as other included managers, and which candidates list it."""
    by_fn = {r["file_number"]: r for r in rows if r["file_number"]}
    for r in rows:
        r.setdefault("listed_by", "")
    for r in rows:
        hits = sorted({by_fn[fn]["name"] for fn in r.pop("_included") if fn in by_fn and by_fn[fn] is not r})
        r["lists_candidates"] = "; ".join(hits)
        for h in (x for x in rows if x["name"] in hits):
            h["listed_by"] = "; ".join(filter(None, [h["listed_by"], r["name"]]))


def _bucket(n: int, edges: tuple[int, ...]) -> int:
    return sum(n >= e for e in edges)


def score(r: dict) -> dict:
    """Rubric: points per question family (13f_candidates.md explains each component)."""
    rows = r["latest_rows"] or 0
    pair = bool(r.get("q3_filed")) and r["q3_filed"] < UNIT_CHANGE <= (r.get("q4_filed") or "")
    split = (r.get("q4_rows_checked") or 0) > 1.2 * (r.get("q4_cusips") or 0) > 0
    s = {"s_amend": 2 * (r["n_restatement"] > 0) + 2 * (r["n_new_holdings"] > 0),
         "s_cofile": min(3, _bucket(r["other_mgrs_max"] or 0, (1, 3, 10))
                         + bool(r["n_nt"] or r["reporting_for"] or r["listed_by"])),
         "s_unit": pair + (r["amend_2022_after_change"] > 0) + (r["n_unit_strong"] > 0),
         "s_shares": ((r.get("q4_putcall") or 0) > 0) + split,
         "s_coverage": 1 if r["quarters"] == len(QUARTERS) else 0 if r["quarters"] >= 12 else -1,
         "s_size": 1 if 10 <= rows <= 2000 else 0 if rows <= 10_000 else -1 if rows <= 30_000 else -2}
    s["score"] = sum(s.values())
    return s


QUOTA = {"activist": 7, "hedge": 3, "multi_quant": 4, "insurer": 4, "bank": 4, "asset_mgr": 4, "endow_sov": 3,
         "small_adviser": 1}
PINNED = {1336528, 1067983}  # pilot M1 Pershing Square, M2 Berkshire Hathaway


def suggest(rows: list[dict], n: int = 30) -> set[int]:
    """Kind quotas first (higher score, then smaller table), pilot managers always in, the rest by score. Filers
    with originals for fewer than 12 of the 16 quarters are not suggested."""
    ok = [r for r in rows if r["quarters"] >= 12]
    key = lambda r: (-r["score"], r["latest_rows"] or 0)
    pick = {r["cik"] for r in ok if r["cik"] in PINNED}
    for kind, q in QUOTA.items():
        have = sum(r["kind"] == kind and r["cik"] in pick for r in ok)
        pick |= {r["cik"] for r in sorted((r for r in ok if r["kind"] == kind and r["cik"] not in pick), key=key)
                 [:max(0, q - have)]}
    for r in sorted(ok, key=key):
        if len(pick) >= n:
            break
        pick.add(r["cik"])
    return pick


def boundary_pick(rows: list[dict], exclude: set[int], n: int = 20) -> set[int]:
    """Filers for the 2022Q3/Q4 unit questions only, outside the suggested set: strong unit-rule breakers first,
    then filers whose 2022 filings straddle the change in another way (2022 amendments filed on or after
    3 Jan 2023, 2022Q3 filed late, weak flags), then clean controls; smaller 2022Q4 tables first in each group
    (only these two quarters' tables are needed for them)."""
    ok = [r for r in rows if r["cik"] not in exclude and r.get("q3_acc") and r.get("q4_acc")]
    group = lambda r: (0 if r["n_unit_strong"] else 1 if r["amend_2022_after_change"] or r["n_unit_weak"]
                       or r["q3_filed"] >= UNIT_CHANGE else 2)
    return {r["cik"] for r in sorted(ok, key=lambda r: (group(r), r.get("q4_rows_total") or 0))[:n]}


COLUMNS = ["cik", "name", "label", "kind", "why", "status", "suggested", "boundary", "score", "s_amend", "s_cofile", "s_unit",
           "s_shares", "s_coverage", "s_size", "covers_read", "file_number", "n_hr", "quarters", "first_period",
           "last_period", "n_hr_a", "n_restatement", "n_new_holdings", "n_amend_unread", "amend_detail", "n_nt",
           "nt_detail", "n_combination", "n_conf_omitted", "n_conf_denied_expired", "conf_reasons",
           "amend_2022_after_change", "amend_2022", "n_unit_strong", "n_unit_weak", "other_mgrs_latest", "other_mgrs_min", "other_mgrs_max",
           "lists_candidates", "listed_by", "reporting_for", "latest_acc", "latest_filed", "latest_rows",
           "latest_value", "latest_size"] + [
    f"{q}_{k}" for q in ("q3", "q4") for k in ("acc", "filed", "rule_unit", "evidence_unit", "median_price",
                                                 "rows_usd", "rows_thousands", "rows_ambiguous", "suspect",
                                                 "reason", "rows_total", "rows_checked", "partial", "putcall",
                                                 "cusips")] + [
    "unit_flags", "errors", "submissions_url", "latest_url", "q3_url", "q4_url", "amend_urls", "nt_urls"]


def discover(max_pages: int = 25) -> None:
    """Print the filers with most 13F amendments that add holdings (NEW HOLDINGS) or mention confidential
    treatment, counting only report periods in the window (full-text search, filed Oct 2022 - Sep 2026)."""
    for q in ('"NEW HOLDINGS"', '"confidential treatment"'):
        hits, names, total = Counter(), {}, None
        for page in range(max_pages):
            res = efts(q=q, forms="13F-HR/A", dateRange="custom", startdt="2022-10-01", enddt="2026-09-30",
                       **({"from": page * 100} if page else {}))
            total = res["hits"]["total"]["value"]
            for h in res["hits"]["hits"]:
                s = h["_source"]
                if WINDOW[0] <= (s.get("period_ending") or "") <= WINDOW[1]:
                    for cik, nm in zip(s["ciks"], s["display_names"]):
                        hits[int(cik)] += 1
                        names[int(cik)] = nm
            if (page + 1) * 100 >= total:
                break
        print(f"\n{q} in 13F-HR/A: {total} documents; filers by in-window amendments:")
        for cik, n in hits.most_common(40):
            print(f"  {n:3d}  {cik:>8d}  {names[cik]}{'  (seed)' if cik in {c for c, *_ in SEEDS} else ''}")


def main(argv=None) -> None:
    global OFFLINE
    ap = argparse.ArgumentParser(description="Candidate 13F filers for Step 1")
    ap.add_argument("--discover", action="store_true", help="full-text search for more seeds, then stop")
    ap.add_argument("--offline", action="store_true", help="use data/raw/ only; rows are marked partial")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    OFFLINE = a.offline
    if a.discover:
        return discover()
    rows = []
    for cik, kind, label, why in SEEDS:
        seed = {"cik": cik, "label": label, "kind": kind, "why": why}
        if cik is None:
            rows.append({**seed, "status": "CIK still to look up on EDGAR"})
            continue
        try:
            rows.append(profile(cik, kind, label, why))
        except NotCached:
            rows.append({**seed, "status": "not fetched yet; CIK not yet checked against EDGAR"})
        print(f"{cik:>8d} {label[:32]:32s} {rows[-1]['status']}  [{_downloaded / 1e6:.1f} MB]", flush=True)
    done = [r for r in rows if "n_hr" in r]
    cross_refs(done)
    for r in done:
        r.update(score(r))
    # a suggestion over a partial list would only repeat the pinned pilot managers
    pick = suggest(done) if len(done) == len(rows) else set()
    edge = boundary_pick(done, pick) if pick else set()
    for r in rows:
        r["suggested"] = "Y" if r["cik"] in pick else ""
        r["boundary"] = "Y" if r["cik"] in edge else ""
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: ("n_hr" not in r, r["kind"], -r.get("score", 0), r["label"])))
    print(f"{len(rows)} seeds, {len(done)} profiled, {len(pick)} suggested, {len(edge)} boundary -> {a.out}")


if __name__ == "__main__":
    main()
