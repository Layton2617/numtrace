"""Cached SEC EDGAR downloads under data/raw/ (git-ignored), per the SEC fair-access policy.

User-Agent comes from $SEC_USER_AGENT. SEC counts requests per IP across all processes (limit 10 per second),
so every process downloading through here shares one lock file in data/raw/ that holds the last request time,
and request starts are spaced 1/RATE s apart machine-wide.
Every URL is cached once at data/raw/<host>/<path> (a query string adds a file named by its hash under
<path>.q/); re-runs read the cache and make no request.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
RATE = 6.0  # requests per second, all processes together


def cache_path(url: str) -> Path:
    u = urlparse(url)
    path = u.path.lstrip("/") or "index"
    if path.endswith("/"):
        path += "index"
    if u.query:
        path += f".q/{hashlib.sha1(u.query.encode()).hexdigest()[:16]}"
    return RAW / u.netloc / path


def throttle() -> float:
    """Wait for this process's turn and return its request start time; starts are at least 1/RATE s apart
    across every process using RAW."""
    RAW.mkdir(parents=True, exist_ok=True)
    with open(RAW / ".rate_lock", "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)  # released when the file closes
        f.seek(0)
        last = float(f.read() or 0)
        time.sleep(max(0.0, last + 1 / RATE - time.time()))
        now = time.time()
        f.truncate(0)
        f.write(repr(now))
    return now


def get(url: str) -> bytes:
    p = cache_path(url)
    if p.exists():
        return p.read_bytes()
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise RuntimeError("set SEC_USER_AGENT to 'Name email' before downloading from sec.gov")
    throttle()
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    tmp.write_bytes(body)
    tmp.replace(p)  # a killed run never leaves a partial file that looks cached
    return body


def get_json(url: str):
    return json.loads(get(url))


def archive_url(cik: int, accession: str, name: str = "") -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{name}"


def filing_index(cik: int, accession: str) -> dict:
    """Directory listing of one filing (document names, sizes)."""
    return get_json(archive_url(cik, accession, "index.json"))


def full_submission(cik: int, accession: str) -> bytes:
    """The complete SGML submission (<accession>.txt): header with FILED AS OF DATE plus all documents."""
    return get(archive_url(cik, accession, f"{accession}.txt"))


def submissions(cik: int) -> dict:
    """Filing history (recent filings inline; older pages listed under filings.files)."""
    return get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")


def companyfacts(cik: int) -> dict:
    return get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")


def recent_filings(cik: int, forms: tuple[str, ...] = ()) -> list[dict]:
    """All filings (recent + older pages) as dicts with form, accessionNumber, filingDate, acceptanceDateTime,
    reportDate, primaryDocument."""
    sub = submissions(cik)
    pages = [sub["filings"]["recent"]] + [
        get_json(f"https://data.sec.gov/submissions/{f['name']}") for f in sub["filings"].get("files", [])]
    out = []
    for pg in pages:
        keys = list(pg)
        for i in range(len(pg["accessionNumber"])):
            row = {k: pg[k][i] for k in keys}
            if not forms or row["form"] in forms:
                out.append(row)
    return out
