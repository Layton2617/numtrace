"""Step 1 frozen retrieval corpus: filing lists, downloads, snapshot, anchors.

    SEC_USER_AGENT='Name email' python -m step1.corpus filings      # data/step1/filings.jsonl
    SEC_USER_AGENT='Name email' python -m step1.corpus download     # documents into data/raw/ (resumable)
    python -m step1.corpus snapshot                                 # data/step1/corpus/step1-v2/
    python -m step1.corpus anchors --facts step1/data/*/facts.jsonl # step1/data/anchors.jsonl

Entities: the 30 companies (COMPANIES; CIKs from SEC company_tickers.json, plus predecessor CIKs) and
the managers of --managers (default: step1/entities/13f_selected.csv main=Y and 13f_candidates.csv suggested=Y for
the whole window, boundary=Y in either for 2022Q3-2022Q4 only). A filing is in the window when the calendar quarter
of its report date (step1.templates.quarter_of) is 2022Q3-2026Q2; nothing filed after FREEZE enters. All SEC
traffic goes through data_layer.edgar_fetch (cache under data/raw/, machine-wide rate limit). Layout, counts and
formats: step1/CORPUS.md.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gc
import hashlib
import json
import os
import re
import resource
import subprocess
import sys
import time
import warnings
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from data_layer import edgar_fetch
from data_layer.facts import Fact
from data_layer.filing_text import BLOCKS, _norm, html_to_text, merge_cells, merged_index, render_table
from data_layer.harness_adapter import Anchor
from data_layer.thirteen_f import POSITION_SHARES, USD_THOUSANDS, Filing13F, parse_filing, value_unit_for_filing_date
from harness.corpus import Corpus
from harness.schema import Doc, RetrievalConfig
from harness.tools import parse_printed_number, parse_table_lines
from pilot.build_corpus import COLS, _cells, sections
from step1.templates import quarter_of, quarters

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)  # inline XBRL files open with <?xml ...?>

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "step1"
SELECTED = ROOT / "step1" / "entities" / "13f_selected.csv"
# co-filing group members outside the 30 (s1-13f's 13F-GROUP family), whole window
MANAGERS = [SELECTED, ROOT / "step1" / "entities" / "13f_candidates.csv", OUT / "13f_group_members.csv"]
SNAP = OUT / "corpus" / "step1-v2"
ANCHORS = ROOT / "step1" / "data" / "anchors.jsonl"
PLANS = ROOT / "step1" / "data" / "nqdc" / "corpus_documents.jsonl"
WINDOW = quarters("2022Q3:2026Q2")
BOUNDARY = ["2022Q3", "2022Q4"]
START, FREEZE = "2022-07-01", "2026-10-02"
PROXY_FILED = ("2023-01-01", "2026-12-31")
# Ticker -> registrant name (the cover-page name; several companies tag it in capitals).
COMPANIES = {
    "AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation", "ORCL": "Oracle Corporation", "NVDA": "NVIDIA Corporation",
    "INTC": "Intel Corporation", "AVGO": "Broadcom Inc.", "GOOGL": "Alphabet Inc.", "META": "Meta Platforms, Inc.",
    "NFLX": "Netflix, Inc.", "JPM": "JPMorgan Chase & Co.", "GS": "The Goldman Sachs Group, Inc.", "BLK": "BlackRock, Inc.",
    "JNJ": "Johnson & Johnson", "LLY": "Eli Lilly and Company", "UNH": "UnitedHealth Group Incorporated",
    "AMZN": "Amazon.com, Inc.", "TSLA": "Tesla, Inc.", "HD": "The Home Depot, Inc.", "WMT": "Walmart Inc.",
    "PG": "The Procter & Gamble Company", "KO": "The Coca-Cola Company", "CAT": "Caterpillar Inc.",
    "BA": "The Boeing Company", "HON": "Honeywell International Inc.", "XOM": "Exxon Mobil Corporation",
    "CVX": "Chevron Corporation", "COP": "ConocoPhillips", "FCX": "Freeport-McMoRan Inc.", "DOW": "Dow Inc.",
    "NEM": "Newmont Corporation"}
# Holding-company reorganisations inside the window: the earlier filings sit under the old CIK.
PREDECESSORS = {"BLK": [1364742], "XOM": [34088]}
PERIODIC, PROXY = {"10-K", "10-K/A", "10-Q", "10-Q/A"}, {"DEF 14A"}
F13 = {"13F-HR", "13F-HR/A", "13F-NT", "13F-NT/A"}
NEW_BYTES = 0  # downloaded by this process


def fetch(url: str) -> bytes:
    global NEW_BYTES
    cached = edgar_fetch.cache_path(url).exists()
    body = edgar_fetch.get(url)
    NEW_BYTES += 0 if cached else len(body)
    return body


def quarter(period: str | None) -> str | None:
    return quarter_of(date.fromisoformat(period)) if period else None


# ---------------------------------------------------------------- filings
def entities(managers: list[Path] = MANAGERS) -> list[dict]:
    """Companies, then the union of the manager lists: main=Y (13f_selected.csv) or suggested=Y (13f_candidates.csv)
    for the whole window, boundary=Y for 2022Q3-2022Q4."""
    tickers = {v["ticker"]: v["cik_str"] for v in json.loads(fetch("https://www.sec.gov/files/company_tickers.json")).values()}
    out = [{"kind": "company", "key": t, "ciks": [tickers[t]] + PREDECESSORS.get(t, []), "quarters": WINDOW}
           for t in COMPANIES]
    mgr = {}
    for path in managers:
        for r in csv.DictReader(path.open()):
            full = r.get("main", r.get("suggested")) == "Y"
            if full or r.get("boundary") == "Y":
                e = mgr.setdefault(int(r["cik"]), {"kind": "manager", "key": r.get("key") or r["label"],
                                                   "ciks": [int(r["cik"])], "quarters": BOUNDARY})
                e["quarters"] = WINDOW if full else e["quarters"]
    return out + list(mgr.values())


def history(cik: int) -> tuple[str, list[dict]]:
    """EDGAR name and every filing made START..FREEZE (recent page + the older pages that reach START)."""
    sub = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    pages = [sub["filings"]["recent"]] + [json.loads(fetch(f"https://data.sec.gov/submissions/{f['name']}"))
                                          for f in sub["filings"].get("files", []) if f["filingTo"] >= START]
    rows = [{k: pg[k][i] for k in pg} for pg in pages for i in range(len(pg["accessionNumber"]))]
    return sub["name"], [r for r in rows if START <= r["filingDate"] <= FREEZE]


def wanted(e: dict, r: dict) -> bool:
    if e["kind"] == "manager":
        return r["form"] in F13 and quarter(r["reportDate"]) in e["quarters"]
    return (r["form"] in PERIODIC and quarter(r["reportDate"]) in e["quarters"]
            or r["form"] in PROXY and PROXY_FILED[0] <= r["filingDate"] <= PROXY_FILED[1])


def q3_10qs(rows: list[dict], picked: list[dict]) -> list[dict]:
    """For each 10-K in the window, the last 10-Q before it (the Q3 10-Q, whose nine-month figures ST-Q4 needs)
    when that 10-Q's own report period falls before the window."""
    have, out = {r["accessionNumber"] for r in picked}, []
    for k in (r for r in picked if r["form"] == "10-K"):
        end = date.fromisoformat(k["reportDate"])
        q3 = max((r for r in rows if r["form"] == "10-Q" and r["reportDate"]
                  and 0 < (end - date.fromisoformat(r["reportDate"])).days < 120), key=lambda r: r["reportDate"], default=None)
        if q3 and q3["accessionNumber"] not in have:
            out.append({**q3, "note": f"Q3 10-Q of the fiscal year ending {k['reportDate']}"})
    return out


def info_table(cik: int, acc: str, primary: str) -> str | None:
    """The information table: the largest XML document other than the primary document."""
    items = json.loads(fetch(edgar_fetch.archive_url(cik, acc, "index.json")))["directory"]["item"]
    xml = [i for i in items if i["name"].lower().endswith(".xml") and i["name"] != primary]
    return max(xml, key=lambda i: int(i["size"] or 0))["name"] if xml else None


def entity_filings(e: dict) -> list[dict]:
    out, seen = [], set()
    for cik in e["ciks"]:
        name, rows = history(cik)
        picked = [r for r in rows if wanted(e, r)]
        if e["kind"] == "company":
            picked += q3_10qs(rows, picked)
        for r in picked:
            acc = r["accessionNumber"]
            if acc in seen:  # co-registrant filings appear under both CIKs
                continue
            seen.add(acc)
            # 13F: primaryDocument is EDGAR's rendering (xslForm13F_X02/primary_doc.xml) of the XML we want
            doc = r["primaryDocument"].rsplit("/", 1)[-1] if e["kind"] == "manager" else r["primaryDocument"]
            f = {"kind": e["kind"], "key": e["key"], "cik": cik, "edgar_name": name, "form": r["form"], "accession": acc,
                 "filed": r["filingDate"], "accepted": r["acceptanceDateTime"], "period": r["reportDate"] or None,
                 "quarter": quarter(r["reportDate"]), "doc": doc, "url": edgar_fetch.archive_url(cik, acc, doc),
                 "size": r["size"], "note": r.get("note", "")}
            if r["form"].startswith("13F-HR"):
                it = info_table(cik, acc, doc)
                f["info_table"], f["info_table_url"] = it, it and edgar_fetch.archive_url(cik, acc, it)
            out.append(f)
    return sorted(out, key=lambda f: (f["filed"], f["accession"]))


def plan_documents(path: Path) -> list[dict]:
    """The NQDC plan documents s1-nqdc lists (versions in force, superseded and future, amendments; key, url, role,
    in_force_url, fy, title). The carrier filing's date and form and the exhibit type come from its EDGAR index page."""
    out = []
    for d in map(json.loads, path.read_text().splitlines()):
        cik, folder, doc = re.search(r"/data/(\d+)/(\d{18})/([^/]+)$", d["url"]).groups()
        acc = f"{folder[:10]}-{folder[10:12]}-{folder[12:]}"
        idx = fetch(edgar_fetch.archive_url(int(cik), acc, f"{acc}-index.htm")).decode("utf-8", "replace")
        ex = re.search(rf'{re.escape(doc)}</a>.*?</td>\s*<td scope="row">([^<]*)</td>', idx, re.S)
        out.append({"kind": "company", "key": d["key"], "cik": int(cik), "edgar_name": "", "form": ex.group(1).strip(),
                    "accession": acc, "filed": re.search(r'Filing Date</div>\s*<div class="info">([\d-]+)<', idx).group(1),
                    "accepted": "", "period": None, "quarter": None, "doc": doc, "url": d["url"], "size": 0,
                    "note": f"NQDC plan document ({d['role']}, {d['fy']})",
                    "carrier": re.search(r"<strong>Form ([^<]+)</strong>", idx).group(1).strip(),
                    "plan": {k: d.get(k) for k in ("role", "in_force_url", "fy", "title")}})
    return out


def cmd_filings(args) -> None:
    ents = entities(args.managers)
    rows = []
    for i, e in enumerate(ents, 1):
        fs = entity_filings(e)
        rows += fs
        print(f"[{i}/{len(ents)}] {e['key']}: {len(fs)} filings  {dict(Counter(f['form'] for f in fs))}  "
              f"(+{NEW_BYTES / 1e6:.1f} MB)", flush=True)
    if args.plans.exists():
        rows += plan_documents(args.plans)
        print(f"{sum('plan' in r for r in rows)} plan documents from {args.plans}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "filings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"{len(rows)} filings -> {OUT / 'filings.jsonl'}; downloaded {NEW_BYTES / 1e6:.1f} MB")


# ---------------------------------------------------------------- download
def load_filings() -> list[dict]:
    return [json.loads(l) for l in (OUT / "filings.jsonl").read_text().splitlines()]


def documents(rows: list[dict]) -> list[str]:
    """URLs to fetch, in the download priority: periodic reports, proxies, 13F."""
    rank = lambda f: 0 if f["form"] in PERIODIC else 1 if f["form"] in PROXY else 2
    rows = sorted(rows, key=rank)
    return [u for f in rows for u in (f["url"], f.get("info_table_url")) if u]


def cmd_download(args) -> None:
    urls = documents(load_filings())
    todo = [u for u in urls if not edgar_fetch.cache_path(u).exists()]
    print(f"{len(urls)} documents, {len(urls) - len(todo)} cached, {len(todo)} to fetch", flush=True)
    failed, t0, done, nbytes = [], time.time(), 0, 0
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(edgar_fetch.get, u): u for u in todo}
        for fut in as_completed(futs):
            done += 1
            if fut.exception():
                failed.append({"url": futs[fut], "error": repr(fut.exception())})
            else:
                nbytes += len(fut.result())
            if done % 50 == 0 or done == len(todo):
                print(f"[{done}/{len(todo)}] {nbytes / 1e6:.0f} MB in {time.time() - t0:.0f} s, {len(failed)} failed",
                      flush=True)
            if nbytes > args.budget_mb * 1e6:
                print(f"stopped: over the {args.budget_mb:.0f} MB budget for this run")
                ex.shutdown(cancel_futures=True)
                break
    (OUT / "download_failures.jsonl").write_text("".join(json.dumps(f) + "\n" for f in failed))
    print(f"downloaded {nbytes / 1e6:.1f} MB; {len(failed)} failures -> {OUT / 'download_failures.jsonl'}")


# ---------------------------------------------------------------- snapshot
DEI_RE = re.compile(r'<ix:nonNumeric[^>]*?name="dei:(\w+)"[^>]*>(.*?)</ix:nonNumeric>', re.S | re.I)


def dei(html: str) -> dict:
    out = {}
    for k, v in DEI_RE.findall(html):
        out.setdefault(k, re.sub(r"<[^>]+>", "", v).strip())
    return out


def ix_value(ix) -> float | None:
    t = ix.get_text(strip=True)
    if re.fullmatch(r"[—–-]*", t) or (ix.get("format") or "").endswith(("fixed-zero", "zerodash")):
        v = 0.0
    elif re.search(r"\d", t):
        v = float(re.sub(r"[^\d.]", "", t))
    else:
        return None
    v *= 10 ** int(ix.get("scale") or 0)
    return -v if ix.get("sign") == "-" else v


def ix_spans(html: bytes, text: str) -> list[dict]:
    """Inline XBRL numbers (ix:nonFraction) located in text = html_to_text(html), offsets into text. Tables: each
    top-level table is rendered again on its own (render_table) and found in text; its k-th non-empty row is the
    k-th line, and merge_cells' positions give the cell."""
    soup = BeautifulSoup(html, "lxml")
    ctx = {}
    for c in soup.find_all("xbrli:context"):
        val = lambda t: c.find(t).get_text(strip=True) if c.find(t) else None
        ctx[c.get("id")] = (val("xbrli:startdate"), val("xbrli:enddate") or val("xbrli:instant"),
                            c.find(["xbrldi:explicitmember", "xbrldi:typedmember"]) is not None)
    for t in soup.find_all(["ix:header", "script", "style"]):  # as html_to_text does
        t.decompose()
    for t in soup.find_all(style=re.compile(r"display:\s*none")):
        t.decompose()
    out, pos = [], 0
    for table in soup.find_all("table"):
        if table.find_parent("table") is not None:
            continue
        block = "\n".join(render_table(table))
        at = text.find(block, pos) if block else -1
        if at < 0:
            continue
        lines, pos, k, l0 = block.split("\n"), at + len(block), 0, at
        for tr in table.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            cells = [td.get_text(" ") for td in tds]
            if not merge_cells(cells):
                continue
            line, k, l0 = lines[k], k + 1, l0 + (len(lines[k - 1]) + 1 if k else 0)
            ixs = tr.find_all("ix:nonfraction")
            if not ixs:
                continue
            where, parts = merged_index(cells), line.split(" | ")
            tls = {tl.end: tl for tl in parse_table_lines(line)}
            for ix in ixs:
                td = ix.find_parent(["td", "th"])
                m = where[next(i for i, t in enumerate(tds) if t is td)] if td is not None else None
                if m is None or m >= len(parts):
                    continue
                tl = tls.get(sum(len(p) + 3 for p in parts[:m]) + len(parts[m].rstrip()))
                if tl is None or re.sub(r"\D", "", tl.number_text) != re.sub(r"\D", "", ix.get_text()):
                    continue
                out.append(span(ix, ctx, l0 + tl.end - len(tl.number_text), l0 + tl.end, tl.label, tl.value))
    # Running text: a paragraph (block element without nested blocks) is found in text by its own text, the number by
    # how often it occurs in the paragraph before the tag.
    pos = 0
    for ix in soup.find_all("ix:nonfraction"):
        block = ix.find_parent(BLOCKS)
        if ix.find_parent("table") is not None or block is None or block.find(BLOCKS) is not None:
            continue
        para, num = _norm(block.get_text("")), ix.get_text(strip=True)
        at = text.find(para, pos) if len(para) >= 20 and re.search(r"\d", num) else -1
        if at < 0:
            continue
        pos = at
        before = []
        for t in block.strings:
            if any(a is ix for a in t.parents):
                break
            before.append(t)
        k, i = "".join(before).count(num), -1
        for _ in range(k + 1):
            i = para.find(num, i + 1)
        if i >= 0:
            out.append(span(ix, ctx, at + i, at + i + len(num), para[:80], parse_printed_number(num)))
    return out


def span(ix, ctx: dict, start: int, end: int, label: str, printed: float) -> dict:
    s, e, dims = ctx.get(ix.get("contextref"), (None, None, True))
    return {"start": start, "end": end, "label": label, "printed": printed, "concept": ix.get("name"),
            "period_start": s, "period_end": e, "dims": dims, "value": ix_value(ix),
            "scale": 10.0 ** int(ix.get("scale") or 0)}


def render_13f(f: Filing13F) -> str:
    """The pilot's rendering (pilot.build_corpus.render_13f) with the filing date on every row; values as filed.
    A 13F-NT has no table and lists the managers that report for this one."""
    m = f.meta
    lines = [f"Form {m.form_type} ({m.report_type}) filed by {m.filer_name} (CIK {m.filer_cik})",
             f"Period of report: {m.period_of_report}. Filing date: {m.filing_date}. Accession: {m.accession}."]
    if m.is_amendment:
        lines.append(f"Amendment no. {m.amendment_no}, amendment type: {m.amendment_type}.")
    lines.append("Other included managers: "
                 + ("; ".join(f"{o.sequence_number}. {o.name}" for o in m.other_included_managers) or "none"))
    if m.other_managers_reporting:
        lines.append("Other managers reporting for this manager: " + "; ".join(o.name for o in m.other_managers_reporting))
    if m.table_entry_total is not None:
        lines.append(f"Table entry total | {m.table_entry_total}")
    if m.table_value_total is not None:
        lines.append(f"Table value total | {m.table_value_total:,}")
    if f.rows:
        lines.append("Information table")
    for r in f.rows:
        pc = f" {r.put_call.upper()}" if r.put_call else ""
        oms = " ".join(f"#{x}" for x in r.other_managers) or "none"
        lines.append(f"{r.name_of_issuer} {r.title_of_class} (CUSIP {r.cusip}){pc} | Value: {r.original_value:,} | "
                     f"Shares or principal ({r.ssh_prnamt_type}): {r.ssh_prnamt:,} | Investment discretion: "
                     f"{r.investment_discretion} | Other managers: {oms} | Filed: {m.filing_date}")
    return "\n".join(lines)


def read_filing(f: dict) -> dict:
    """Worker: one filing's rendered text (+ dei fields and inline XBRL spans, or the 13F cover)."""
    try:
        if f["kind"] == "manager":
            info = edgar_fetch.cache_path(f["info_table_url"]).read_bytes() if f.get("info_table_url") else None
            filing = parse_filing(edgar_fetch.cache_path(f["url"]).read_bytes(), info, accession=f["accession"],
                                  filing_date=f["filed"])
            m = filing.meta
            return {"text": render_13f(filing), "manager": m.filer_name, "amendment_type": m.amendment_type,
                    "report_type": m.report_type, "rows": len(filing.rows), "period_of_report": str(m.period_of_report)}
        html = edgar_fetch.cache_path(f["url"]).read_bytes()
        text = html_to_text(html)
        return {"text": text, "dei": dei(html.decode("utf-8", "replace")), "ix": ix_spans(html, text)}
    except Exception as e:  # reported in the stats, the filing stays out of the snapshot
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def labels(fs: list[dict], read: dict) -> dict:
    """Period label per document (url): FY2025 / Q3 FY2025 from the filing's own dei tags (the company's fiscal-year
    naming), a proxy the fiscal year of the last 10-K filed before it, a 13F its quarter, a plan document none."""
    out = {}
    for f in fs:
        if f["kind"] == "manager":
            out[f["url"]] = f["quarter"]
        elif f["form"] in PERIODIC:
            d = read[f["url"]].get("dei", {})
            fy, fp = d.get("DocumentFiscalYearFocus") or f["period"][:4], d.get("DocumentFiscalPeriodFocus") or "FY"
            out[f["url"]] = f"FY{fy}" if fp == "FY" or f["form"].startswith("10-K") else f"{fp} FY{fy}"
    for f in fs:
        if f["form"] in PROXY:
            ks = [k for k in fs if k["key"] == f["key"] and k["form"] == "10-K" and k["filed"] < f["filed"]]
            out[f["url"]] = out[max(ks, key=lambda k: k["filed"])["url"]] if ks else f"FY{int(f['filed'][:4]) - 1}"
    return out


def manager_names(rows: list[dict], read: dict) -> dict:
    """Harness entity name and doc-id prefix per manager CIK: 13f_selected.csv when it lists the manager, else
    the name on its latest filing."""
    sel = {int(r["cik"]): r for r in csv.DictReader(SELECTED.open())} if SELECTED.exists() else {}
    out = {}
    for f in sorted(rows, key=lambda f: f["filed"]):
        if f["kind"] == "manager" and "manager" in read[f["url"]]:
            s = sel.get(f["cik"])
            out[f["cik"]] = (s["name"], s["key"].lower()) if s else (read[f["url"]]["manager"], slug(f["key"]))
    return out


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def doc_base(f: dict, prefix: str, label: str | None) -> str:
    if "plan" in f:
        return f"{prefix}-{slug(f['form'])}-{f['accession'][-6:]}-{slug(Path(f['doc']).stem)[:24]}"
    return f"{prefix}-{slug(f['form'])}-{slug(label)}-{f['accession'][-6:]}"


def filing_docs(f: dict, r: dict, name: str, prefix: str, label: str | None, base: dict) -> list[Doc]:
    common = dict(source_label=f"SEC EDGAR {f['form']}", accession=f["accession"], entity=name, form_type=f["form"],
                  period=label, filing_date=f["filed"], is_primary_filing=True)
    secs = sections(r["text"])
    if "plan" in f:  # as the pilot's plan exhibits; the flags are what harness scoring reads, never shown to the model
        pl, head = f["plan"], f"{name} {f['form']} (plan document)"
        flags = ({"superseded": True, "superseded_by": base[pl["in_force_url"]]} if pl["role"] == "superseded" else
                 {"future": True, "in_force": base[pl["in_force_url"]]} if pl["role"] == "future" else {})
        common["source_label"] = f"SEC EDGAR {f['carrier']} exhibit {f['form'][3:]}"
        return [Doc(doc_id=f"{base[f['url']]}-s{i:03d}", title=f"{head}: {t}" if t else head, text=body,
                    meta={"document": f["doc"], "section": i, "cik": f["cik"], "plan_role": pl["role"],
                          "plan_fy": pl["fy"], **flags}, **common)
                for i, (t, body) in enumerate(secs, 1)]
    if f["kind"] == "company":
        head = f"{name} {f['form']} ({label})"
        return [Doc(doc_id=f"{base[f['url']]}-s{i:03d}", title=f"{head}: {t}" if t else head, text=body,
                    meta={"document": f["doc"], "section": i, "cik": f["cik"], "report_date": f["period"]}, **common)
                for i, (t, body) in enumerate(secs, 1)]
    form = f["form"] + (f" {r['amendment_type']}" if r["amendment_type"] else "")
    head = f"{name} {form} {'information table' if r['rows'] else 'cover page'}, period {r['period_of_report']}"
    return [Doc(doc_id=f"{prefix}-13f-{label.lower()}-{f['accession']}-s{i:03d}", title=head + (f" (part {i})" if i > 1 else ""),
                text=body, meta={"amendment_type": r["amendment_type"], "cik": f["cik"], "section": i}, **common)
            for i, (_, body) in enumerate(secs, 1)]


def cmd_snapshot(args) -> None:
    rows = load_filings()
    t0 = time.time()
    big_first = sorted(rows, key=lambda f: -f["size"])
    if args.workers > 1:
        with ProcessPoolExecutor(args.workers) as ex:
            read = dict(zip((f["url"] for f in big_first), ex.map(read_filing, big_first)))
    else:
        read = {f["url"]: read_filing(f) for f in big_first}
    print(f"read {len(rows)} filings in {time.time() - t0:.0f} s", flush=True)
    names, label = manager_names(rows, read), labels(rows, read)
    base = {f["url"]: doc_base(f, f["key"].lower(), label.get(f["url"])) for f in rows if f["kind"] == "company"}
    # (kind, key): the 13F manager Goldman Sachs and the company GS share a key
    order = dict.fromkeys([("company", k) for k in COMPANIES] + [("manager", f["key"]) for f in rows if f["kind"] == "manager"])
    rank = {k: i for i, k in enumerate(order)}
    docs, ix, sources, failed = [], [], [], []
    for f in sorted(rows, key=lambda f: (rank[(f["kind"], f["key"])], f["filed"], f["accession"], f["url"])):
        r = read[f["url"]]
        paths = [edgar_fetch.cache_path(u) for u in (f["url"], f.get("info_table_url")) if u]
        src = {k: f.get(k) for k in ("kind", "key", "cik", "form", "accession", "filed", "period", "quarter", "plan")}
        src["files"] = [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in paths]
        if "error" in r:
            failed.append({**src, "error": r["error"]})
            continue
        name, prefix = (COMPANIES[f["key"]], f["key"].lower()) if f["kind"] == "company" else names[f["cik"]]
        fd = filing_docs(f, r, name, prefix, label.get(f["url"]), base)
        starts = [0]
        for d in fd[:-1]:
            starts.append(starts[-1] + len(d.text) + 1)
        for s in r.get("ix", []):
            i = bisect.bisect_right(starts, s["start"]) - 1
            ix.append({"doc_id": fd[i].doc_id, **s, "start": s["start"] - starts[i], "end": s["end"] - starts[i],
                       "accession": f["accession"], "cik": f["cik"]})
        docs += fd
        sources.append({**src, "entity": name, "label": label.get(f["url"]), "chunks": len(fd),
                        "chars": sum(len(d.text) for d in fd)})
    corpus = Corpus(docs)
    old = json.loads((SNAP / "manifest.json").read_text()) if (SNAP / "manifest.json").exists() else {}
    if old.get("snapshot_id") != corpus.snapshot_id:  # same id, same docs.jsonl: leave it for readers
        corpus.save(SNAP)
    man = json.loads((SNAP / "manifest.json").read_text())
    man.update({"name": SNAP.name, "freeze_date": FREEZE, "window": f"{WINDOW[0]}:{WINDOW[-1]}", "n_filings": len(sources),
                "docs_sha256": sha256(SNAP / "docs.jsonl"), "sources": "sources.jsonl", "ix_spans": "ix_spans.jsonl"})
    (SNAP / "manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True))
    (SNAP / "sources.jsonl").write_text("".join(json.dumps(s) + "\n" for s in sources))
    (SNAP / "ix_spans.jsonl").write_text("".join(json.dumps(s) + "\n" for s in ix))
    (OUT / "snapshot_failures.jsonl").write_text("".join(json.dumps(s) + "\n" for s in failed))
    stats = Counter()
    for s in sources:
        form = "EX-10 plan" if s["plan"] else s["form"]
        stats[(s["kind"], form, "filings")] += 1
        stats[(s["kind"], form, "chunks")] += s["chunks"]
        stats[(s["kind"], form, "chars")] += s["chars"]
    for k, form in sorted({(k, f) for k, f, _ in stats}):
        print(f"{k:8} {form:9} {stats[(k, form, 'filings')]:5} filings {stats[(k, form, 'chunks')]:7} chunks "
              f"{stats[(k, form, 'chars')] / 1e6:8.1f} M chars")
    print(f"snapshot {corpus.snapshot_id}: {len(sources)} filings, {len(docs)} chunks, "
          f"{(SNAP / 'docs.jsonl').stat().st_size / 1e6:.0f} MB docs.jsonl, {len(ix)} ix spans, {len(failed)} failed "
          f"-> {SNAP} ({time.time() - t0:.0f} s)")


# ---------------------------------------------------------------- anchors
TOTALS = {"13f:TableValueTotal": "Table value total", "13f:TableEntryTotal": "Table entry total"}
ROW_RE = re.compile(r"\(CUSIP (\w+)\)(?: (PUT|CALL))?$")


def load_fact_lines(paths: list[Path]) -> list[dict]:
    """facts.jsonl lines: a data_layer Fact, optionally with the harness binding as "entity_name", "period_label" (or
    "period"), "scope"; or {"entity", "period", "scope", "fact"}. Optional "pattern" (one group: the number) and "scale"
    anchor a number printed in running text. Missing binding fields default to the doc's entity and period."""
    out = []
    for p in paths:
        for l in Path(p).read_text().splitlines():
            if not l.strip():
                continue
            d = json.loads(l)
            wrapped = "fact" in d
            line = {"fact": Fact.model_validate(d["fact"] if wrapped else d), "file": str(p),
                    "entity": d.get("entity" if wrapped else "entity_name"),
                    "period": d.get("period") if wrapped else d.get("period_label") or d.get("period")}
            out.append({**line, **{k: d[k] for k in ("scope", "pattern", "scale", "column", "column_header", "tag") if k in d}})
    return out


def kind_of(f: Fact) -> str:
    return "13f" if f.concept.startswith("13f:") else "nqdc" if f.concept.startswith("nqdc:") else "statement"


def anchor(line: dict, d: dict, start: int, end: int, label: str, printed: float, scale: float, scope=None, period=None):
    return Anchor(doc_id=d["doc_id"], start=start, end=end, label=label, printed_value=printed, scale=scale,
                  entity=line.get("entity") or d["entity"], period=line.get("period") or period or d["period"],
                  scope=line.get("scope", scope), fact=line["fact"])


def prints(number_text: str, scale: float, value: float) -> bool:
    """The printed number x scale is the fact's value: equal within half a unit of the last printed digit
    (3,268.4 millions: +-0.05 million), or the fact is the printed amount rounded to the fact's own precision. Facts
    carry no decimals, so that precision is read from the value's trailing zeros, capped at the print scale
    (companyfacts gives 3,268,000,000 for a printed 3,268.4 millions; a fact of 3,000,000,000 is not 3,268.4 millions)."""
    m = re.search(r"\.(\d+)", number_text)
    unit = 10.0 ** -(len(m.group(1)) if m else 0) * scale
    printed, v = abs(parse_printed_number(number_text)) * scale, abs(value)
    if abs(printed - v) <= 0.5 * unit * (1 + 1e-9):
        return True
    digits = str(int(v)) if v == int(v) and v else ""
    p = min(10.0 ** (len(digits) - len(digits.rstrip("0"))), scale) if digits else 0.0
    return p > unit and round(printed / p) * p == v


def statement_anchors(line: dict, spans: list[dict], docs: dict, period: str | None, texts: dict) -> tuple[list, str]:
    """Every place whose inline XBRL tag has the fact's concept (or the line's "tag", the filing's own concept) and
    period, no dimensions, and its value."""
    f, out = line["fact"], []
    concept = line.get("tag") or f.concept
    ps = str(f.period_start) if f.period_start else None
    for s in spans:
        if (s["concept"] == concept and not s["dims"] and s["period_end"] == str(f.period_end) and s["period_start"] == ps
                and prints(texts[s["doc_id"]][s["start"]:s["end"]], s["scale"], f.value)):
            out.append(anchor(line, docs[s["doc_id"]], s["start"], s["end"], s["label"], s["printed"], s["scale"],
                              period=period))
    return out, "" if out else "no table cell tagged with this concept, period and value"


def f13_cells(docs: list[dict]) -> dict:
    """One 13F filing's cells (pilot.build_corpus._cells) by (CUSIP, PUT/CALL, Value|Shares), and its summary totals
    by row label."""
    out = {}
    for d in docs:
        for row, hdr, tl in _cells(SimpleNamespace(text=d["text"])):
            r = ROW_RE.search(row)
            out.setdefault((r.group(1), r.group(2), "Shares" if hdr.startswith("Shares") else hdr) if r else row,
                           []).append((d, row, hdr, tl))
    return out


def f13_anchors(line: dict, cells: dict) -> tuple[list, str]:
    """The cell of the fact's row (CUSIP, PUT/CALL) or summary total in its filing; scale by the filing date unless
    the fact carries its as-filed unit. A filer-level position spread over several rows is not printed anywhere."""
    f = line["fact"]
    m = re.fullmatch(r"13f:(Value|Shares)\[(\w+)\]", f.concept)
    shares = f.concept == POSITION_SHARES or bool(m and m.group(1) == "Shares")
    pc = (f.dimensions.get("put_call") or "").upper() or None
    found = cells.get(TOTALS.get(f.concept) or (f.dimensions.get("cusip") or (m and m.group(2)), pc,
                                                  "Shares" if shares else "Value"), [])
    out, s = [], 1.0
    for d, row, hdr, tl in found:
        unit = f.original_unit or value_unit_for_filing_date(d["filing_date"])
        s = 1e3 if unit == USD_THOUSANDS and f.unit == "USD" else 1.0
        if not prints(tl.number_text, s, f.value) or (tl.value < 0) != (f.value < 0):
            continue
        om = re.search(r"Other managers: ([^|\n]*)", d["text"][tl.start:])
        scope = ("single filing" if f.concept in TOTALS else f"option ({pc.title()})" if pc else "filer" if len(found) == 1
                 else f"sub-position (other managers {om.group(1).replace('#', '').strip() if om else 'none'})")
        out.append(anchor(line, d, tl.end - len(tl.number_text), tl.end, f"{row}, {hdr}".strip(", "), tl.value, s, scope))
    if out or not found:
        return out, "" if out else "no such row or total in the filing"
    total = sum(tl.value for *_, tl in found) * s
    return [], (f"not printed: sum of {len(found)} rows" if abs(total - f.value) <= 0.5 * s * len(found)
                else f"value differs from all {len(found)} cells of this row")


def nqdc_anchors(line: dict, docs: list[dict], neos: set[str]) -> tuple[list, str]:
    """The 402(i) cell in the fact's column, in the executive's row or in a plan row under it (the last NEO name above
    the row is the executive's; neos: the proxy's NEOs, normalised); dollars. The column is the one whose header is
    the fact's printed column_header or reads like it (COLS); failing that, in a row of exactly the five 402(i)
    amount columns, the fact's column letter (b-f) by position; else the row's only cell printing the (non-zero)
    value."""
    f = line["fact"]
    col = COLS.get(re.sub(r"(?<!^)(?=[A-Z])", "_", f.concept.split(":", 1)[1]).lower())
    exe, plan = norm_name(f.dimensions.get("executive", "")), norm_name(f.dimensions.get("plan", ""))
    out = []
    for d in docs:
        pos, current = 0, None
        for ln in d["text"].split("\n"):
            label = norm_name(next((p for p in ln.split(" | ") if re.search(r"[A-Za-z]", p)), ""))
            current = max((n for n in neos if label.startswith(n)), key=len, default=current)
            if " | " in ln and col and current == exe and (label.startswith(exe) or plan and label.startswith(plan)):
                tls = parse_table_lines(ln)
                hdr = lambda tl: tl.text.split(" | ")[-1].split(": ")[0] if ": " in tl.text.split(" | ")[-1] else ""
                want = norm_name(line.get("column_header", "")) or None
                hit = [tl for tl in tls if hdr(tl) and (want and norm_name(hdr(tl)) == want or re.search(col, hdr(tl), re.I))]
                if not hit and len(tls) == 5 and line.get("column") in tuple("bcdef"):
                    hit = [tls["bcdef".index(line["column"])]]
                if not hit and f.value:
                    hit = [tl for tl in tls if prints(tl.number_text, 1.0, f.value)]
                    hit = hit if len(hit) == 1 else []
                for tl in hit:
                    if prints(tl.number_text, 1.0, f.value):
                        out.append(anchor(line, d, pos + tl.end - len(tl.number_text), pos + tl.end, tl.label, tl.value,
                                          1.0, f"NEO:{f.dimensions.get('executive', '')}"))
            pos += len(ln) + 1
    return out, "" if out else "no 402(i) cell with this executive, column and value"


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def prose_anchors(line: dict, docs: list[dict]) -> tuple[list, str]:
    """A printed number in running text: the line's "pattern" (one group: the number) and "scale"."""
    f, scale, out = line["fact"], float(line.get("scale", 1)), []
    for d in docs:
        for m in re.finditer(line["pattern"], d["text"]):
            v = parse_printed_number(m.group(1))
            if prints(m.group(1), scale, f.value):
                out.append(anchor(line, d, m.start(1), m.end(1), m.group(0)[:80], v, scale))
    return out, "" if out else "pattern not found with this value"


def cmd_anchors(args) -> None:
    lines = load_fact_lines(args.facts)
    sources = [json.loads(l) for l in (SNAP / "sources.jsonl").read_text().splitlines()]
    key_of = {s["accession"]: (s["kind"], s["key"]) for s in sources}
    # JPMorgan and Goldman Sachs are both a company and a 13F manager under one CIK
    key_of_cik = {(s["kind"], s["cik"]): (s["kind"], s["key"]) for s in sources}
    key_of_cik.update({("company", c): ("company", k) for k, cs in PREDECESSORS.items() for c in cs})
    # 13F facts of a co-filing group carry a group id ("group:MIM") as entity; they anchor by accession alone
    owner = lambda f: key_of_cik.get(("manager" if kind_of(f) == "13f" else "company", int(f.entity)
                                      if f.entity.isdigit() else f.entity))
    period_of = {(("company", s["key"]), s["period"]): s["label"] for s in sources if s["form"] in PERIODIC}
    text_accs = {l["fact"].accession for l in lines if kind_of(l["fact"]) != "statement" or "pattern" in l}
    text_keys = {owner(l["fact"]) for l in lines if "pattern" in l and not l["fact"].accession}
    concepts = {(owner(l["fact"]), l.get("tag") or l["fact"].concept) for l in lines}
    spans = {}
    with (SNAP / "ix_spans.jsonl").open() as fh:
        for raw in fh:
            s = json.loads(raw)
            k = (key_of[s["accession"]], s["concept"])
            if k in concepts:
                spans.setdefault(k, []).append(s)
    span_docs = {s["doc_id"] for ss in spans.values() for s in ss}
    docs, by_acc, by_key, texts = {}, {}, {}, {}
    with (SNAP / "docs.jsonl").open() as fh:
        for raw in fh:
            d = json.loads(raw)
            docs[d["doc_id"]] = {k: d[k] for k in ("doc_id", "entity", "period", "filing_date", "accession")}
            if d["doc_id"] in span_docs:  # the printed text decides the matching precision
                texts[d["doc_id"]] = d["text"]
            if d["accession"] in text_accs or key_of.get(d["accession"]) in text_keys:
                docs[d["doc_id"]] = d
                by_acc.setdefault(d["accession"], []).append(d)
                by_key.setdefault(key_of[d["accession"]], []).append(d)
    neos = {}
    for l in lines:
        if kind_of(l["fact"]) == "nqdc" and l["fact"].dimensions.get("executive"):
            neos.setdefault(l["fact"].accession, set()).add(norm_name(l["fact"].dimensions["executive"]))
    anchors, missing, cells = [], [], {}
    for l in lines:
        f, kind = l["fact"], kind_of(l["fact"])
        key = owner(f)
        if kind == "13f" and "," not in (f.accession or "") and f.accession not in cells:
            cells[f.accession] = f13_cells(by_acc.get(f.accession, []))
        if kind == "13f" and "," in (f.accession or ""):
            got, why = [], f"not printed: merged over {f.accession.count(',') + 1} filings"
        elif "pattern" in l:
            got, why = prose_anchors(l, by_acc.get(f.accession) or by_key.get(key, []))
        elif kind == "13f":
            got, why = f13_anchors(l, cells[f.accession])
        elif kind == "nqdc":
            got, why = nqdc_anchors(l, by_acc.get(f.accession, []), neos.get(f.accession, set()))
        else:
            got, why = statement_anchors(l, spans.get((key, l.get("tag") or f.concept), []), docs,
                                         period_of.get((key, str(f.period_end))), texts)
        anchors += got
        if not got:
            missing.append({"file": l["file"], "kind": kind, "reason": why, "fact": f.model_dump(mode="json")})
    ANCHORS.parent.mkdir(parents=True, exist_ok=True)
    ANCHORS.write_text("".join(a.model_dump_json() + "\n" for a in anchors))
    (OUT / "anchors_missing.jsonl").write_text("".join(json.dumps(m) + "\n" for m in missing))
    n = Counter(kind_of(l["fact"]) for l in lines)
    miss = Counter((m["kind"], m["reason"]) for m in missing)
    print(f"{len(lines)} facts {dict(n)} -> {len(anchors)} anchors in {ANCHORS}; {len(missing)} facts without an anchor "
          f"-> {OUT / 'anchors_missing.jsonl'}")
    for (k, why), c in miss.most_common():
        print(f"  {k:9} {c:5}  {why}")


def cmd_bm25(args) -> None:
    """Load the snapshot and build its BM25 index as run_pilot does (Corpus.load verifies the hash); time, resident
    and peak memory after each step, for planning parallel runs."""
    peak = lambda: round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1e6 if sys.platform == "darwin" else 1e3))
    rss = lambda: round(int(subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True,
                                           text=True).stdout) / 1e3)
    t0 = time.time()
    corpus = Corpus.load(SNAP)
    gc.collect()
    load = {"s": round(time.time() - t0, 1), "rss_mb": rss(), "peak_mb": peak()}
    cfg, t0 = RetrievalConfig(), time.time()
    corpus._index(cfg)
    gc.collect()
    index = {"s": round(time.time() - t0, 1), "rss_mb": rss(), "peak_mb": peak()}
    qs = ["Apple total long-term debt current portion fiscal 2025", "Berkshire Hathaway 13F Chubb shares 2023Q4",
          "Goldman Sachs nonqualified deferred compensation aggregate balance", "Pershing Square Hilton value 2022Q3"]
    t0 = time.time()
    for q in qs:
        corpus.retrieve(q, cfg)
    out = {"snapshot_id": corpus.snapshot_id, "docs": len(corpus), "load": load, "index": index,
           "query_s": round((time.time() - t0) / len(qs), 2)}
    (OUT / "bm25_stats.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("filings")
    p.add_argument("--managers", type=Path, nargs="+", default=MANAGERS)
    p.add_argument("--plans", type=Path, default=PLANS)
    p = sub.add_parser("download")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--budget-mb", type=float, default=3000)
    p = sub.add_parser("snapshot")
    p.add_argument("--workers", type=int, default=6)
    sub.add_parser("bm25")
    p = sub.add_parser("anchors")
    p.add_argument("--facts", type=Path, nargs="+", required=True)
    args = ap.parse_args(argv)
    {"filings": cmd_filings, "download": cmd_download, "snapshot": cmd_snapshot, "bm25": cmd_bm25,
     "anchors": cmd_anchors}[args.cmd](args)


if __name__ == "__main__":
    main()
