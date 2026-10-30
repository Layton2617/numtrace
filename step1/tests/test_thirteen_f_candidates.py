"""13F candidate profiling, offline. Fixtures are cached Berkshire EDGAR files trimmed to a few KB: the submissions
JSON keeps six filings, other-included-manager lists keep their first 3 entries, information tables their first
6 rows. Kahn Brothers and Bull Street come from data_layer's real fixtures."""
import csv
import json
import re
from pathlib import Path

import pytest

from data_layer import edgar_fetch
from step1 import thirteen_f_candidates as m

FX = Path(__file__).parent / "fixtures"
REAL = Path(__file__).resolve().parents[2] / "data_layer" / "tests" / "fixtures" / "13f" / "real"


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """get() serves fixture files and raises NotCached for anything else; data/raw/ is replaced by an empty
    directory so the tests never read the real cache."""
    def get(url):
        if url == m.submissions_url(1067983):
            return (FX / "CIK0001067983.json").read_bytes()
        acc_dir, name = url.split("/")[-2:]
        p = FX / f"{acc_dir[:10]}-{acc_dir[10:12]}-{acc_dir[12:]}" / name
        if not p.exists():
            raise m.NotCached(url)
        return p.read_bytes()
    monkeypatch.setattr(m, "get", get)
    monkeypatch.setattr(edgar_fetch, "RAW", tmp_path)


def test_file_number_normalised():
    assert m.file_number("028-04545") == m.file_number("28-4545") == "28-4545"
    assert m.file_number(None) is None


def test_history_keeps_13f_in_window_and_skips_old_pages(offline):
    name, fs, note = m.history(1067983)
    assert name == "BERKSHIRE HATHAWAY INC" and note == ""      # its older page ends in 2017: never fetched
    assert [(f["form"], f["period"]) for f in fs] == [
        ("13F-HR", "2022-09-30"), ("13F-HR", "2022-12-31"), ("13F-HR/A", "2023-09-30"), ("13F-HR/A", "2023-09-30")]
    assert fs[0]["doc"] == "primary_doc.xml"        # raw XML, not the xslForm13F rendering


def test_history_skips_huge_older_pages_with_a_note(monkeypatch):
    sub = json.loads((FX / "CIK0001067983.json").read_text())
    n = m.MAX_OLDER + 1
    sub["filings"]["files"] = [{"name": "p2.json", "filingCount": n, "filingFrom": "2021-01-01",
                                "filingTo": "2025-01-01"}]
    monkeypatch.setattr(m, "get", lambda url: json.dumps(sub).encode() if "CIK" in url else pytest.fail(url))
    name, fs, note = m.history(1067983)
    assert note == f"older submission pages not read ({n} filings)" and len(fs) == 4


def test_cover_reads_amendment_and_confidential_treatment():
    c = m.cover((FX / "0000950123-24-005653" / "primary_doc.xml").read_bytes())
    assert c["amendment_type"] == "NEW HOLDINGS" and c["conf_denied_expired"] and not c["conf_omitted"]
    assert c["conf_reason"] == "Confidential Treatment Expired"
    assert [x["file_number"] for x in c["included"]] == ["28-554", "28-718"] and c["rows"] == 1
    c = m.cover((FX / "0000950123-22-012275" / "primary_doc.xml").read_bytes())
    assert c["report_type"] == "13F COMBINATION REPORT" and c["file_number"] == "28-4545"
    assert c["reporting"] == [{"name": "New England Asset Management Inc", "file_number": "28-5194"}]


def test_complete_rows_closes_a_cut_table_with_its_prefix():
    xml = (FX / "0000950123-22-012275" / "18337.xml").read_bytes()
    cut = m.complete_rows(xml[: xml.index(b"</infoTable>", 2000) + 5])
    assert cut.endswith(b"</infoTable></informationTable>") and cut.count(b"</infoTable>") == 3
    pref = re.sub(rb"<(/?)(infoTable|informationTable)\b", rb"<\1ns1:\2", xml).replace(b"xmlns=", b"xmlns:ns1=")
    assert m.complete_rows(pref[:2500]).endswith(b"</ns1:informationTable>")


@pytest.mark.parametrize("primary, info, filed, rule, evidence", [
    ("0001039565-22-000009.primary_doc.xml", "0001039565-22-000009.infotable.xml", "2022-05-02",
     "USD_thousands", "USD"),                        # Kahn Brothers: dollars before the change
    ("0001790837-23-000002.txt", None, "2023-02-28", "USD", "USD_thousands"),   # Bull Street: thousands after
])
def test_unit_check_flags_filers_who_break_the_filing_date_rule(monkeypatch, primary, info, filed, rule, evidence):
    if info:
        prim, tab = (REAL / primary).read_bytes(), (REAL / info).read_bytes()
    else:
        prim, tab = (b.encode() for b in re.findall(r"<XML>\s*(.*?)</XML>", (REAL / primary).read_text(), re.S))
    monkeypatch.setattr(m, "primary", lambda cik, f: prim)
    monkeypatch.setattr(m, "info_table", lambda cik, f: (tab, False))
    u = m.unit_check(0, {"acc": "x", "filed": filed})
    assert (u["rule_unit"], u["evidence_unit"], u["suspect"]) == (rule, evidence, True)


def test_profile_counts_amendments_cofiling_and_unit_boundary(offline):
    r = m.profile(1067983, "insurer", "Berkshire", "test")
    assert r["status"] == "complete" and r["covers_read"] == "4/4"
    assert (r["n_hr"], r["quarters"], r["n_hr_a"], r["n_restatement"], r["n_new_holdings"]) == (2, 2, 2, 1, 1)
    assert r["n_combination"] == 2 and r["reporting_for"] == "New England Asset Management Inc"
    assert r["n_conf_omitted"] == 1 and r["n_conf_denied_expired"] == 1      # restatement omits, NEW HOLDINGS discloses
    assert r["other_mgrs_max"] == 3 and r["file_number"] == "28-4545"
    assert (r["q3_rule_unit"], r["q3_evidence_unit"], r["q4_rule_unit"], r["q4_evidence_unit"]) == (
        "USD_thousands", "USD_thousands", "USD", "USD")
    assert r["q3_filed"] < m.UNIT_CHANGE <= r["q4_filed"] and not r["unit_flags"] and not r["errors"]
    assert r["q4_rows_checked"] == 6 and r["q4_cusips"] == 2 and r["q4_rows_total"] == r["latest_rows"] == 172


def _row(**kw):
    base = dict(cik=1, name="A", kind="activist", n_restatement=0, n_new_holdings=0, other_mgrs_max=0, n_nt=0,
                reporting_for="", listed_by="", amend_2022_after_change=0, n_unit_strong=0, n_unit_weak=0, quarters=16,
                latest_rows=50, q3_filed="2022-11-14", q4_filed="2023-02-14", q4_putcall=0, q4_rows_checked=50,
                q4_cusips=50)
    return {**base, **kw}


def test_score_components():
    s = m.score(_row(n_restatement=1, n_new_holdings=2, other_mgrs_max=14, q4_putcall=3, q4_cusips=20))
    assert (s["s_amend"], s["s_cofile"], s["s_unit"], s["s_shares"], s["s_coverage"], s["s_size"]) == (4, 3, 1, 2, 1, 1)
    s = m.score(_row(quarters=10, latest_rows=40_000, q3_filed=None, n_unit_strong=1, n_unit_weak=3))
    assert (s["s_unit"], s["s_coverage"], s["s_size"]) == (1, -1, -2)


def test_suggest_fills_quotas_keeps_pilot_and_skips_gappy_filers():
    rows = [_row(cik=c, kind=k, score=sc, quarters=q) for c, k, sc, q in [
        (1336528, "activist", 0, 16), (2, "activist", 5, 16), (3, "activist", 9, 8), (4, "bank", 1, 16),
        (5, "bank", 2, 16), (6, "hedge", 7, 16)]]
    assert m.suggest(rows, n=5) == {1336528, 2, 4, 5, 6}        # 3 has originals for only 8 quarters
    rows.append(_row(cik=7, kind="hedge", score=1, quarters=16))
    assert 7 in m.suggest(rows, n=6)


def test_cross_refs_by_file_number():
    rows = [{"name": "Parent", "file_number": "28-1", "_included": {"28-2", "28-9"}},
            {"name": "Sub", "file_number": "28-2", "_included": set()}]
    m.cross_refs(rows)
    assert rows[0]["lists_candidates"] == "Sub" and rows[1]["listed_by"] == "Parent" and rows[0]["listed_by"] == ""


def test_efts_query_is_ordered_so_the_cache_hits(monkeypatch):
    urls = []
    monkeypatch.setattr(m, "get", lambda url: urls.append(url) or b"{}")
    m.efts(q='"NEW HOLDINGS"', forms="13F-HR/A")
    m.efts(forms="13F-HR/A", q='"NEW HOLDINGS"')
    assert urls[0] == urls[1] == f"{m.EFTS}?forms=13F-HR%2FA&q=%22NEW+HOLDINGS%22"


def test_unit_flags_strong_when_both_signals_disagree(offline, monkeypatch):
    def fake(cik, f):
        q3 = f["period"] == "2022-09-30"
        return {"rule_unit": "USD_thousands" if q3 else "USD", "evidence_unit": "USD" if q3 else "USD_thousands",
                "suspect": q3, "reason": "r", "rows_usd": 1, "rows_thousands": 2}
    monkeypatch.setattr(m, "unit_check", fake)
    r = m.profile(1067983, "insurer", "Berkshire", "test")
    assert (r["n_unit_strong"], r["n_unit_weak"]) == (1, 1)
    assert r["unit_flags"].startswith("STRONG 2022-09-30") and " | weak 2022-12-31" in r["unit_flags"]


def test_boundary_pick_prefers_rule_breakers_then_straddlers_then_small_controls():
    rows = [_row(cik=c, n_unit_strong=st, amend_2022_after_change=a, q3_acc="x", q4_acc="y", q4_rows_total=n)
            for c, st, a, n in [(1, 0, 0, 10), (2, 1, 0, 900), (3, 0, 2, 50), (4, 0, 0, 5), (5, 1, 0, 30)]]
    rows.append(_row(cik=6, n_unit_strong=1, q3_acc="x", q4_acc=None))      # no 2022Q4 filing: not a pair
    assert m.boundary_pick(rows, exclude={5}, n=3) == {2, 3, 4}


def test_offline_profile_marks_what_was_not_read(offline, monkeypatch):
    served = m.get

    def get(url):
        if "000095012323011029" in url or url.endswith("index.json"):     # restatement cover, both table indexes
            raise m.NotCached(url)
        return served(url)
    monkeypatch.setattr(m, "get", get)
    r = m.profile(1067983, "insurer", "Berkshire", "test")
    assert (r["n_restatement"], r["n_new_holdings"], r["n_amend_unread"]) == (0, 1, 1)
    assert r["covers_read"] == "3/4" and r["status"].startswith("partial: 1 cover pages and 2 unit checks")
    assert r["amend_detail"].startswith("2023-09-30 ? filed 2023-11-16")


def test_cached_full_submission_is_used_for_primary_and_table(monkeypatch, tmp_path):
    """The pilot cached whole submissions (<acc>.txt); those need no further download."""
    monkeypatch.setattr(edgar_fetch, "RAW", tmp_path)
    monkeypatch.setattr(m, "OFFLINE", True)
    acc = "0001790837-23-000002"
    dest = edgar_fetch.cache_path(edgar_fetch.archive_url(1790837, acc, f"{acc}.txt"))
    dest.parent.mkdir(parents=True)
    dest.write_bytes((REAL / f"{acc}.txt").read_bytes())
    f = {"acc": acc, "filed": "2023-02-28", "doc": "primary_doc.xml"}
    assert m.cover(m.primary(1790837, f))["rows"] > 0 and m.info_table(1790837, f)[1] is False
    with pytest.raises(m.NotCached):
        m.get("https://www.sec.gov/Archives/edgar/data/1790837/x/primary_doc.xml")


def test_main_offline_lists_unfetched_seeds_and_suggests_nothing(offline, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "SEEDS", [(1067983, "insurer", "Berkshire Hathaway", "pilot"), (999, "bank", "X", "y")])
    monkeypatch.setattr(m, "OFFLINE", False)
    out = tmp_path / "c.csv"
    m.main(["--offline", "--out", str(out)])
    rows = list(csv.DictReader(out.open()))
    assert [r["cik"] for r in rows] == ["1067983", "999"] and not any(r["suggested"] for r in rows)
    assert rows[0]["name"] == "BERKSHIRE HATHAWAY INC" and rows[1]["status"].startswith("not fetched")
