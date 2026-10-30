"""step1.corpus offline: filing selection, inline XBRL spans, 13F rendering, and snapshot + anchors on a tiny cache."""
import json
from argparse import Namespace

import pytest

from data_layer import edgar_fetch
from data_layer.filing_text import html_to_text, merge_cells
from data_layer.thirteen_f import POSITION_SHARES, POSITION_VALUE, parse_filing
from harness.corpus import Corpus
from step1 import corpus as c

CTX = '<xbrli:context id="{}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier>{}' \
      '</xbrli:entity><xbrli:period>{}</xbrli:period></xbrli:context>'
SEG = ('<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">acme:RetailMember'
       '</xbrldi:explicitMember></xbrli:segment>')
IX = '<ix:nonFraction name="us-gaap:{}" contextRef="{}" unitRef="usd" scale="6" decimals="-6">{}</ix:nonFraction>'
IXBRL = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>
<div style="display:none"><ix:header><ix:hidden>
<ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="c1">2025</ix:nonNumeric>
<ix:nonNumeric name="dei:DocumentFiscalPeriodFocus" contextRef="c1">FY</ix:nonNumeric>
{IX.format("Hidden", "c1", "9")}
</ix:hidden><ix:resources>
{CTX.format("c1", "", "<xbrli:instant>2026-01-31</xbrli:instant>")}
{CTX.format("c0", "", "<xbrli:instant>2025-02-01</xbrli:instant>")}
{CTX.format("d1", SEG, "<xbrli:instant>2026-01-31</xbrli:instant>")}
{CTX.format("y1", "", "<xbrli:startDate>2025-02-02</xbrli:startDate><xbrli:endDate>2026-01-31</xbrli:endDate>")}
</ix:resources></ix:header></div>
<p>Item 8. Financial Statements</p>
<table>
<tr><td>(millions)</td><td>January 31, 2026</td><td>February 1, 2025</td></tr>
<tr><td>Current portion of long-term debt</td><td>$</td><td>{IX.format("LongTermDebtCurrent", "c1", "2,130")}</td>
<td>$</td><td>{IX.format("LongTermDebtCurrent", "c0", "1,636")}</td><td>(a)</td></tr>
<tr><td>Retail segment debt</td><td>{IX.format("LongTermDebtCurrent", "d1", "500")}</td><td>&#8212;</td></tr>
<tr><td>Payments for property</td><td>({IX.format("PaymentsToAcquirePropertyPlantAndEquipment", "y1", "3,000")}</td><td>)</td>
<td>(1,200</td><td>)</td></tr>
</table>
<p>Total debt was ${IX.format("Debt", "c1", "16,456")} million.</p>
</body></html>"""

OM = "<otherManager2><sequenceNumber>{0}</sequenceNumber><otherManager><name>Sub {0} LLC</name></otherManager></otherManager2>"
PRIMARY = """<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler"><headerData><submissionType>{form}</submissionType>
<filerInfo><filer><credentials><cik>0000000009</cik></credentials></filer><periodOfReport>09-30-2022</periodOfReport>
</filerInfo></headerData><formData><coverPage><reportCalendarOrQuarter>09-30-2022</reportCalendarOrQuarter>
<isAmendment>false</isAmendment><filingManager><name>Fund LP</name></filingManager><reportType>{report}</reportType>
{reporting}</coverPage>{summary}</formData></edgarSubmission>"""
SUMMARY = (f"<summaryPage><tableEntryTotal>4</tableEntryTotal><tableValueTotal>1600</tableValueTotal>"
           f"<otherManagers2Info>{OM.format(1)}{OM.format(2)}</otherManagers2Info></summaryPage>")
ROW = ("<infoTable><nameOfIssuer>{0}</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>{1}</cusip><value>{2}</value>"
       "<shrsOrPrnAmt><sshPrnamt>{3}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>{4}"
       "<investmentDiscretion>DFND</investmentDiscretion><otherManager>{5}</otherManager>"
       "<votingAuthority><Sole>{3}</Sole><Shared>0</Shared><None>0</None></votingAuthority></infoTable>")
INFO = ('<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">'
        + ROW.format("ALPHA INC", "111111111", 1000, 50, "", 1) + ROW.format("BETA CORP", "222222222", 300, 30, "", 1)
        + ROW.format("BETA CORP", "222222222", 200, 20, "", 2)
        + ROW.format("ALPHA INC", "111111111", 100, 10, "<putCall>Put</putCall>", 1) + "</informationTable>")
HR = PRIMARY.format(form="13F-HR", report="13F HOLDINGS REPORT", reporting="", summary=SUMMARY)
NT = PRIMARY.format(form="13F-NT", report="13F NOTICE", summary="",
                    reporting="<otherManagersInfo><otherManager><name>Parent Holdings</name></otherManager></otherManagersInfo>")


pytestmark = pytest.mark.filterwarnings("ignore::bs4.XMLParsedAsHTMLWarning")


def sub(form, acc, filed, period, doc="d.htm"):
    return {"form": form, "accessionNumber": acc, "filingDate": filed, "reportDate": period, "size": 1,
            "acceptanceDateTime": f"{filed}T16:00:00.000Z", "primaryDocument": doc}


def test_filing_selection(monkeypatch):
    rows = [sub("10-Q", "a1", "2022-07-29", "2022-06-25"), sub("10-K", "a2", "2022-10-28", "2022-09-24"),
            sub("10-Q", "a3", "2026-07-31", "2026-06-27"), sub("10-Q", "a4", "2026-10-30", "2026-09-26"),
            sub("10-K/A", "a5", "2023-01-20", "2022-09-24"), sub("DEF 14A", "a6", "2023-01-12", ""),
            sub("DEF 14A", "a7", "2022-12-30", ""), sub("8-K", "a8", "2023-02-02", "2023-02-02")]
    monkeypatch.setattr(c, "history", lambda cik: ("ACME CORP", rows))
    fs = c.entity_filings({"kind": "company", "key": "ACME", "ciks": [1], "quarters": c.WINDOW})
    # 2022-09-24 is in calendar 2022Q3; its fiscal year's Q3 10-Q (2022-06-25) comes in for ST-Q4
    assert {f["accession"]: f["note"] for f in fs} == {
        "a1": "Q3 10-Q of the fiscal year ending 2022-09-24", "a2": "", "a3": "", "a5": "", "a6": ""}

    xsl = "xslForm13F_X02/primary_doc.xml"
    rows = [sub("13F-HR", "m1", "2022-11-14", "2022-09-30", xsl), sub("13F-HR", "m2", "2023-02-14", "2022-12-31", xsl),
            sub("13F-HR/A", "m3", "2023-05-15", "2022-12-31", xsl), sub("13F-NT", "m4", "2023-05-15", "2023-03-31", xsl),
            sub("13F-HR", "m0", "2022-08-12", "2022-06-30", xsl)]
    monkeypatch.setattr(c, "history", lambda cik: ("FUND LP", rows))
    monkeypatch.setattr(c, "info_table", lambda cik, acc, primary: "infotable.xml")
    full = c.entity_filings({"kind": "manager", "key": "FUND", "ciks": [9], "quarters": c.WINDOW})
    assert [f["accession"] for f in full] == ["m1", "m2", "m3", "m4"]
    assert full[0]["doc"] == "primary_doc.xml" and full[0]["info_table_url"].endswith("/m1/infotable.xml")
    assert "info_table" not in full[3]
    boundary = c.entity_filings({"kind": "manager", "key": "FUND", "ciks": [9], "quarters": c.BOUNDARY})
    assert [f["accession"] for f in boundary] == ["m1", "m2", "m3"]


def test_manager_lists_are_unioned(monkeypatch, tmp_path):
    sel, cand = tmp_path / "sel.csv", tmp_path / "cand.csv"
    sel.write_text("key,cik,name,short,kind,main,boundary\nAAA,1,A Co,A,hedge,Y,Y\nCCC,3,C Co,C,hedge,,Y\n")
    cand.write_text("cik,label,suggested,boundary\n1,A label,Y,\n2,B label,,Y\n4,D label,,\n")
    monkeypatch.setattr(c, "COMPANIES", {"AAPL": "Apple Inc."})
    monkeypatch.setattr(c, "fetch", lambda url: b'{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}')
    got = {e["key"]: (e["kind"], e["ciks"], len(e["quarters"])) for e in c.entities([sel, cand])}
    assert got == {"AAPL": ("company", [320193], 16), "AAA": ("manager", [1], 16), "CCC": ("manager", [3], 2),
                   "B label": ("manager", [2], 2)}


def test_merged_index_follows_merge_cells():
    cells = ["Total", "$", "1,234", ")", "", "(1)", "$", "(5", ")", "%"]
    assert len(merge_cells(cells)) == 3  # Total | $1,234) | ($5)%
    assert c.merged_index(cells) == [0, 1, 1, 1, None, None, 2, 2, 2, 2]


def test_ix_spans_locate_table_cells():
    text = html_to_text(IXBRL)
    got = {(s["concept"][8:], s["period_end"], s["dims"]): s for s in c.ix_spans(IXBRL.encode(), text)}
    assert set(got) == {("LongTermDebtCurrent", "2026-01-31", False), ("LongTermDebtCurrent", "2025-02-01", False),
                        ("LongTermDebtCurrent", "2026-01-31", True), ("Debt", "2026-01-31", False),
                        ("PaymentsToAcquirePropertyPlantAndEquipment", "2026-01-31", False)}  # not the hidden one
    s = got[("LongTermDebtCurrent", "2026-01-31", False)]
    assert (text[s["start"]:s["end"]], s["printed"], s["scale"], s["value"]) == ("$2,130", 2130.0, 1e6, 2.13e9)
    assert s["label"] == "Current portion of long-term debt, January 31, 2026"
    assert text[got[("LongTermDebtCurrent", "2025-02-01", False)]["start"]:].startswith("$1,636")
    s = got[("PaymentsToAcquirePropertyPlantAndEquipment", "2026-01-31", False)]
    assert (s["printed"], s["value"], s["period_start"]) == (-3000.0, 3e9, "2025-02-02")  # printed as an outflow
    s = got[("Debt", "2026-01-31", False)]
    assert (text[s["start"]:s["end"]], s["printed"], s["label"]) == ("16,456", 16456.0, "Total debt was $16,456 million.")
    assert c.dei(IXBRL) == {"DocumentFiscalYearFocus": "2025", "DocumentFiscalPeriodFocus": "FY"}


def test_render_13f_rows_carry_filing_date_and_value_as_filed():
    text = c.render_13f(parse_filing(HR, INFO, accession="0000000009-22-000001", filing_date="2022-11-14"))
    rows = [l for l in text.split("\n") if "(CUSIP " in l]
    assert rows[0] == ("ALPHA INC COM (CUSIP 111111111) | Value: 1,000 | Shares or principal (SH): 50 | Investment "
                       "discretion: DFND | Other managers: #1 | Filed: 2022-11-14")
    assert len(rows) == 4 and all(r.endswith("| Filed: 2022-11-14") for r in rows) and " PUT | " in rows[3]
    assert "Table value total | 1,600" in text and "Other included managers: 1. Sub 1 LLC; 2. Sub 2 LLC" in text
    nt = c.render_13f(parse_filing(NT, None, accession="0000000009-23-000002", filing_date="2023-05-15"))
    assert "Other managers reporting for this manager: Parent Holdings" in nt and "Table" not in nt


def test_snapshot_and_anchors(monkeypatch, tmp_path):
    for k, v in {"ROOT": tmp_path, "OUT": tmp_path / "step1", "SNAP": tmp_path / "step1" / "snap",
                 "ANCHORS": tmp_path / "anchors.jsonl", "SELECTED": tmp_path / "none.csv",
                 "COMPANIES": {"ACME": "Acme Corp"}}.items():
        monkeypatch.setattr(c, k, v)
    monkeypatch.setattr(edgar_fetch, "RAW", tmp_path / "raw")
    k_acc, hr_acc, nt_acc = "0000000001-26-000010", "0000000009-22-000001", "0000000009-23-000002"
    url = lambda cik, acc, doc: edgar_fetch.archive_url(cik, acc, doc)
    old_plan, new_plan = url(1, "0000000001-12-000003", "ex101.htm"), url(1, k_acc, "ex1012.htm")
    files = {url(1, k_acc, "acme-20260131.htm"): IXBRL, url(9, hr_acc, "primary_doc.xml"): HR,
             url(9, hr_acc, "infotable.xml"): INFO, url(9, nt_acc, "primary_doc.xml"): NT,
             old_plan: "<p>Deferral Plan, restated 2012: deferrals up to 50% of salary.</p>",
             new_plan: "<p>Deferral Plan, restated 2025: deferrals up to 80% of salary.</p>"}
    for u, body in files.items():
        edgar_fetch.cache_path(u).parent.mkdir(parents=True, exist_ok=True)
        edgar_fetch.cache_path(u).write_text(body)
    # the manager shares the company's key, as the 13F filer Goldman Sachs shares the company GS's
    base = {"kind": "manager", "key": "ACME", "cik": 9, "edgar_name": "FUND LP", "accepted": "", "size": 1, "note": "",
            "doc": "primary_doc.xml"}
    rows = [{**base, "kind": "company", "key": "ACME", "cik": 1, "form": "10-K", "accession": k_acc, "filed": "2026-03-01",
             "period": "2026-01-31", "quarter": "2026Q1", "doc": "acme-20260131.htm", "url": url(1, k_acc, "acme-20260131.htm")},
            {**base, "form": "13F-HR", "accession": hr_acc, "filed": "2022-11-14", "period": "2022-09-30",
             "quarter": "2022Q3", "url": url(9, hr_acc, "primary_doc.xml"), "info_table": "infotable.xml",
             "info_table_url": url(9, hr_acc, "infotable.xml")},
            {**base, "form": "13F-NT", "accession": nt_acc, "filed": "2023-05-15", "period": "2023-03-31",
             "quarter": "2023Q1", "url": url(9, nt_acc, "primary_doc.xml")}]
    plan = {**base, "kind": "company", "key": "ACME", "cik": 1, "period": None, "quarter": None, "carrier": "10-K"}
    rows += [{**plan, "form": "EX-10.1", "accession": "0000000001-12-000003", "filed": "2012-02-20", "doc": "ex101.htm",
              "url": old_plan, "plan": {"role": "superseded", "in_force_url": new_plan, "fy": "FY2025", "title": ""}},
             {**plan, "form": "EX-10.12", "accession": k_acc, "filed": "2026-03-01", "doc": "ex1012.htm",
              "url": new_plan, "plan": {"role": "in_force", "in_force_url": None, "fy": "FY2025", "title": ""}}]
    c.OUT.mkdir(parents=True)
    (c.OUT / "filings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    c.cmd_snapshot(Namespace(workers=1))

    corpus = Corpus.load(c.SNAP)  # verifies the content hash
    man = json.loads((c.SNAP / "manifest.json").read_text())
    assert man["snapshot_id"] == corpus.snapshot_id and man["docs_sha256"] == c.sha256(c.SNAP / "docs.jsonl")
    src = [json.loads(l) for l in (c.SNAP / "sources.jsonl").read_text().splitlines()]
    assert [(s["form"], s["label"], s["entity"]) for s in src] == [
        ("EX-10.1", None, "Acme Corp"), ("10-K", "FY2025", "Acme Corp"), ("EX-10.12", None, "Acme Corp"),
        ("13F-HR", "2022Q3", "Fund LP"), ("13F-NT", "2023Q1", "Fund LP")]
    stale = corpus.get("acme-ex101-000003-ex101-s001")  # the plan exhibit shares the 10-K's accession
    assert (stale.source_label, stale.period, stale.meta["superseded"], stale.meta["superseded_by"]) == (
        "SEC EDGAR 10-K exhibit 10.1", None, True, "acme-ex1012-000010-ex1012")
    assert "superseded" not in corpus.get("acme-ex1012-000010-ex1012-s001").meta
    assert src[3]["files"][1]["sha256"] == c.sha256(edgar_fetch.cache_path(url(9, hr_acc, "infotable.xml")))
    k_doc = corpus.get("acme-10k-fy2025-000010-s001")
    assert (k_doc.entity, k_doc.period, k_doc.form_type, k_doc.filing_date) == ("Acme Corp", "FY2025", "10-K", "2026-03-01")
    hr = corpus.get(f"acme-13f-2022q3-{hr_acc}-s001")
    assert hr.period == "2022Q3" and "BETA CORP COM (CUSIP 222222222) | Value: 300 |" in hr.text
    assert "Parent Holdings" in corpus.get(f"acme-13f-2023q1-{nt_acc}-s001").text
    spans = [json.loads(l) for l in (c.SNAP / "ix_spans.jsonl").read_text().splitlines()]
    assert {corpus.get(s["doc_id"]).text[s["start"]:s["end"]] for s in spans} == {"$2,130", "$1,636", "500", "( 3,000)",
                                                                                "16,456"}

    fact = lambda concept, value, **kw: {"entity": "0000000009", "concept": concept, "value": value, "unit": "USD",
                                         "period_end": "2022-09-30", "accession": hr_acc, "filed": "2022-11-14", **kw}
    lines = [{"entity": "0000000001", "concept": "us-gaap:LongTermDebtCurrent", "value": 2.13e9, "unit": "USD",
              "period_end": "2026-01-31", "entity_name": "Acme Corp", "period_label": "FY2025"},
             {"entity": "0000000001", "concept": "us-gaap:LongTermDebtCurrent", "value": 2.131e9, "unit": "USD",
              "period_end": "2026-01-31"},
             {"entity": "Fund", "period": "2022Q3", "fact": fact(POSITION_VALUE, 1e6, dimensions={"cusip": "111111111"})},
             fact(POSITION_SHARES, 50, unit="shares", dimensions={"cusip": "111111111"}),
             fact(POSITION_VALUE, 5e5, dimensions={"cusip": "222222222"}),
             fact("13f:TableValueTotal", 1.6e6),
             fact(POSITION_VALUE, 5e5, dimensions={"cusip": "222222222"}, accession=f"{hr_acc},{nt_acc}")]
    (tmp_path / "facts.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
    c.cmd_anchors(Namespace(facts=[tmp_path / "facts.jsonl"]))

    got = [json.loads(l) for l in c.ANCHORS.read_text().splitlines()]
    assert [(a["entity"], a["period"], a["scope"], a["printed_value"], a["scale"]) for a in got] == [
        ("Acme Corp", "FY2025", None, 2130.0, 1e6), ("Fund", "2022Q3", "filer", 1000.0, 1e3),
        ("Fund LP", "2022Q3", "filer", 50.0, 1.0), ("Fund LP", "2022Q3", "single filing", 1600.0, 1e3)]
    assert corpus.get(got[1]["doc_id"]).text[got[1]["start"]:got[1]["end"]] == "1,000"
    missing = [json.loads(l)["reason"] for l in (c.OUT / "anchors_missing.jsonl").read_text().splitlines()]
    assert missing == ["no table cell tagged with this concept, period and value", "not printed: sum of 2 rows",
                       "not printed: merged over 2 filings"]


def test_nqdc_anchors_with_and_without_column_headers():
    text = ("Name | Executive Contributions in Last FY ($) | Registrant Contributions in Last FY ($)\n"
            "Jane Roe | Executive Contributions in Last FY ($): 1,000 | Registrant Contributions in Last FY ($): 500\n"
            "John Doe\n"
            "Restoration Plan (2) | — | 2,500 | 3\n"
            "Mary Major\n"
            "Restoration Plan (2) | — | 2,500 | 3")
    d = {"doc_id": "x-def14a-s001", "entity": "X Corp", "period": "FY2025", "accession": "a", "text": text}
    neos = {"janeroe", "johndoe", "marymajor"}

    def run(concept, value, exe, plan=""):
        f = {"entity": "0000000001", "concept": concept, "value": value, "unit": "USD", "period_end": "2025-12-31",
             "dimensions": {"executive": exe, "plan": plan}, "accession": "a"}
        got, why = c.nqdc_anchors({"fact": c.Fact.model_validate(f)}, [d], neos)
        return [text[a.start:a.end] + f"@{text.count(chr(10), 0, a.start)}" for a in got], why

    assert run("nqdc:registrant_contributions", 500, "Jane Roe") == (["500@1"], "")  # column header
    assert run("nqdc:registrant_contributions", 2500, "Mary Major", "Restoration Plan") == (["2,500@5"], "")  # her row only
    assert run("nqdc:executive_contributions", 0, "John Doe", "Restoration Plan")[0] == []  # "—" without header: ambiguous


def test_nqdc_anchors_use_printed_header_and_column_letter():
    text = ("Pat Lee | Executive Contributions ($): 700 | Company Contributions ($): 700\n"
            "Sam Roe | — | — | 5 | — | 90")
    d = {"doc_id": "x-def14a-s002", "entity": "X Corp", "period": "FY2025", "accession": "a", "text": text}

    def run(concept, value, exe, **kw):
        f = {"entity": "0000000001", "concept": concept, "value": value, "unit": "USD", "period_end": "2025-12-31",
             "dimensions": {"executive": exe}, "accession": "a"}
        got, _ = c.nqdc_anchors({"fact": c.Fact.model_validate(f), **kw}, [d], {"patlee", "samroe"})
        return [(a.start, a.end) for a in got]

    # "Company Contributions" is column c only by the printed header the fact carries; 700 is in b too
    assert run("nqdc:registrant_contributions", 700, "Pat Lee", column_header="Company Contributions ($)") == [(72, 75)]
    # five headerless amount cells: the column letter picks the "—" of column c
    assert run("nqdc:registrant_contributions", 0, "Sam Roe", column="c") == [(90, 91)]


def test_statement_anchors_follow_the_filings_own_tag():
    span = {"doc_id": "d-10k-s001", "start": 5, "end": 8, "label": "COLI, 2025", "printed": 344.0, "scale": 1e6,
            "concept": "us-gaap:LifeInsuranceCorporateOrBankOwnedAmount", "period_start": None,
            "period_end": "2025-12-31", "dims": False}
    docs = {"d-10k-s001": {"doc_id": "d-10k-s001", "entity": "Dow Inc.", "period": "FY2025"}}
    f = c.Fact(entity="0001751788", concept="us-gaap:CashSurrenderValueOfLifeInsurance", value=344e6, unit="USD",
               period_end="2025-12-31")
    texts = {"d-10k-s001": "COLI 344 net"}
    assert c.statement_anchors({"fact": f}, [span], docs, None, texts)[0] == []
    got, _ = c.statement_anchors({"fact": f, "tag": span["concept"]}, [span], docs, None, texts)
    assert [(a.doc_id, a.printed_value, a.scale) for a in got] == [("d-10k-s001", 344.0, 1e6)]



def test_printed_number_matches_to_its_last_digit():
    # WMT Q1: basic EPS 0.62 (FY2024) and 0.21 (FY2025) differ by less than 0.5 and must not bind to each other
    assert c.prints("$0.62", 1.0, 0.62) and not c.prints("$0.62", 1.0, 0.21) and not c.prints("0.21", 1.0, 0.62)
    assert not c.prints("$1.51", 1.0, 1.57)  # TSLA Q3 FY2024
    assert not c.prints("$14.4", 1e9, 14_306_000_000)  # PG proxy, $B: 14.3 when rounded
    # LLY 3,268.4 millions: +-0.05 million around the print, or the fact rounded to its own million
    assert c.prints("3,268.4", 1e6, 3_268_400_000) and c.prints("3,268.4", 1e6, 3_268_000_000)
    assert not c.prints("3,268.4", 1e6, 3_268_500_000) and not c.prints("3,268.4", 1e6, 3_000_000_000)
    assert c.prints("(11,750)", 1e6, 11_750_000_000) and c.prints("—", 1.0, 0)
