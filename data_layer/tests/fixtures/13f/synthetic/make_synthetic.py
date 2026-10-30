"""Generate the SYNTHETIC 13F fixtures in this folder.

Every file written here is hand-made test data. Managers, CIKs, CUSIPs and amounts are
invented. The XML follows the official EDGAR Form 13F schemas (../xsd, validated in
tests/test_thirteen_f.py), and each file is wrapped in an EDGAR-style full-submission
SGML header so that accession number and filing date are read the same way as for the
real fixtures in ../real.

Scenario (mirrors proposal walk-through 2):
  Manager A  SYNTHETIC ALPHA CAPITAL LP   CIK 0009000001
  Manager B  SYNTHETIC ALPHA ADVISERS LLC CIK 0009000002  (Other Included Manager no. 1 of A,
                                                           also files its own 13F-HR)
  Manager C  SYNTHETIC ALPHA SUB FUND LP  CIK 0009000003  (files a 13F-NT naming A)
  A Q3-2022 13F-HR    filed 2022-11-14  -> thousands
  A Q3-2022 13F-HR/A  NEW HOLDINGS, filed 2023-02-20 -> dollars (unit follows filing date)
  A Q4-2022 13F-HR    filed 2023-02-14  -> dollars
  A Q4-2022 13F-HR/A  RESTATEMENT, filed 2023-03-01 -> dollars, replaces the Q4 original
  B Q4-2022 13F-HR    filed 2023-02-10  -> dollars, repeats A's shared (DFND) position
  C Q4-2022 13F-NT    filed 2023-02-13

Run:  python make_synthetic.py   (rewrites the *.txt files next to this script)
"""
from pathlib import Path

HERE = Path(__file__).parent

MANAGERS = {
    "0009000001": ("SYNTHETIC ALPHA CAPITAL LP", "028-90001"),
    "0009000002": ("SYNTHETIC ALPHA ADVISERS LLC", "028-90002"),
    "0009000003": ("SYNTHETIC ALPHA SUB FUND LP", "028-90003"),
}


def header(accession, form, period_yyyymmdd, filed_yyyymmdd, cik):
    name = MANAGERS[cik][0]
    return f"""<SEC-DOCUMENT>{accession}.txt : {filed_yyyymmdd}
<SEC-HEADER>{accession}.hdr.sgml : {filed_yyyymmdd}
ACCESSION NUMBER:\t\t{accession}
CONFORMED SUBMISSION TYPE:\t{form}
PUBLIC DOCUMENT COUNT:\t\t2
CONFORMED PERIOD OF REPORT:\t{period_yyyymmdd}
FILED AS OF DATE:\t\t{filed_yyyymmdd}

FILER:
\tCOMPANY DATA:
\t\tCOMPANY CONFORMED NAME:\t\t\t{name}
\t\tCENTRAL INDEX KEY:\t\t\t{cik}
</SEC-HEADER>
"""


def other_mgr(cik, seq=None):
    name, fileno = MANAGERS[cik]
    inner = f"<cik>{cik}</cik><form13FFileNumber>{fileno}</form13FFileNumber><name>{name}</name>"
    if seq is None:
        return f"<otherManager>{inner}</otherManager>"
    return f"<otherManager2><sequenceNumber>{seq}</sequenceNumber><otherManager>{inner}</otherManager></otherManager2>"


def primary(form, cik, period_mmddyyyy, sign_date, report_type, schema_version=None,
            amendment=None, included=(), reporting_for_me=(), n_rows=0, total=0):
    name, fileno = MANAGERS[cik]
    sv = f"<schemaVersion>{schema_version}</schemaVersion>" if schema_version else ""
    amend = "<isAmendment>false</isAmendment>"
    if amendment:
        no, typ = amendment
        amend = (f"<isAmendment>true</isAmendment><amendmentNo>{no}</amendmentNo>"
                 f"<amendmentInfo><amendmentType>{typ}</amendmentType></amendmentInfo>")
    others = ""
    if reporting_for_me:
        others = "<otherManagersInfo>" + "".join(other_mgr(c) for c in reporting_for_me) + "</otherManagersInfo>"
    summary = ""
    if report_type != "13F NOTICE":
        inc = ""
        if included:
            inc = "<otherManagers2Info>" + "".join(other_mgr(c, i + 1) for i, c in enumerate(included)) + "</otherManagers2Info>"
        summary = (f"<summaryPage><otherIncludedManagersCount>{len(included)}</otherIncludedManagersCount>"
                   f"<tableEntryTotal>{n_rows}</tableEntryTotal><tableValueTotal>{total}</tableValueTotal>"
                   f"<isConfidentialOmitted>false</isConfidentialOmitted>{inc}</summaryPage>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler" xmlns:com="http://www.sec.gov/edgar/common">
  {sv}
  <headerData>
    <submissionType>{form}</submissionType>
    <filerInfo>
      <liveTestFlag>LIVE</liveTestFlag>
      <filer><credentials><cik>{cik}</cik><ccc>XXXXXXXX</ccc></credentials></filer>
      <periodOfReport>{period_mmddyyyy}</periodOfReport>
    </filerInfo>
  </headerData>
  <formData>
    <coverPage>
      <reportCalendarOrQuarter>{period_mmddyyyy}</reportCalendarOrQuarter>
      {amend}
      <filingManager>
        <name>{name}</name>
        <address><com:street1>1 EXAMPLE PLAZA</com:street1><com:city>NEW YORK</com:city>
        <com:stateOrCountry>NY</com:stateOrCountry><com:zipCode>10001</com:zipCode></address>
      </filingManager>
      <reportType>{report_type}</reportType>
      <form13FFileNumber>{fileno}</form13FFileNumber>
      {others}
      <provideInfoForInstruction5>N</provideInfoForInstruction5>
    </coverPage>
    <signatureBlock>
      <name>JANE DOE</name><title>CCO</title><phone>212-555-0100</phone>
      <signature>/s/ Jane Doe</signature><city>NEW YORK</city><stateOrCountry>NY</stateOrCountry>
      <signatureDate>{sign_date}</signatureDate>
    </signatureBlock>
    {summary}
  </formData>
</edgarSubmission>"""


def row(issuer, cusip, value, shares, discretion="SOLE", other="", put_call=None, kind="SH"):
    pc = f"<putCall>{put_call}</putCall>" if put_call else ""
    om = f"<otherManager>{other}</otherManager>" if other else ""
    sole, shared = (shares, 0) if discretion == "SOLE" else (0, shares)
    return f"""  <infoTable>
    <nameOfIssuer>{issuer}</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>{cusip}</cusip>
    <value>{value}</value>
    <shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>{kind}</sshPrnamtType></shrsOrPrnAmt>
    {pc}<investmentDiscretion>{discretion}</investmentDiscretion>{om}
    <votingAuthority><Sole>{sole}</Sole><Shared>{shared}</Shared><None>0</None></votingAuthority>
  </infoTable>"""


def infotable(rows):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">\n'
            + "\n".join(rows) + "\n</informationTable>")


def submission(fname, accession, form, period, filed, cik, report_type, rows, **kw):
    mm, dd, yyyy = period.split("-")
    f_yyyy, f_mm, f_dd = filed[:4], filed[4:6], filed[6:]
    values = [int(r.split("<value>")[1].split("</value>")[0]) for r in rows]
    prim = primary(form, cik, period, f"{f_mm}-{f_dd}-{f_yyyy}", report_type,
                   n_rows=len(rows), total=sum(values), **kw)
    parts = [header(accession, form, f"{yyyy}{mm}{dd}", filed, cik),
             f"<DOCUMENT>\n<TYPE>{form}\n<SEQUENCE>1\n<FILENAME>primary_doc.xml\n<TEXT>\n<XML>\n{prim}\n</XML>\n</TEXT>\n</DOCUMENT>\n"]
    if rows:
        parts.append("<DOCUMENT>\n<TYPE>INFORMATION TABLE\n<SEQUENCE>2\n<FILENAME>infotable.xml\n<TEXT>\n<XML>\n"
                     f"{infotable(rows)}\n</XML>\n</TEXT>\n</DOCUMENT>\n")
    parts.append("</SEC-DOCUMENT>\n")
    (HERE / fname).write_text("".join(parts))


A, B, C = "0009000001", "0009000002", "0009000003"
EXAMPLE, OTHER, SHARED, NEWCO, BONLY = "99999A101", "99999B102", "99999C103", "99999D104", "99999E105"

submission("A_2022Q3_13F-HR.txt", "0009000001-22-000005", "13F-HR", "09-30-2022", "20221114", A,
           "13F HOLDINGS REPORT", [
               row("EXAMPLE CORP", EXAMPLE, 1234567, 10000000),
               row("OTHER CO", OTHER, 5000, 100000),
               row("SHARED CO", SHARED, 2000, 50000, discretion="DFND", other="1"),
               row("EXAMPLE CORP", EXAMPLE, 150, 1000, put_call="Put"),
           ], included=(B,))
submission("A_2022Q3_13F-HRA_newholdings.txt", "0009000001-23-000002", "13F-HR/A", "09-30-2022", "20230220", A,
           "13F HOLDINGS REPORT", [row("NEWCO INC", NEWCO, 3000000, 20000)],
           schema_version="X0202", amendment=(1, "NEW HOLDINGS"))
submission("A_2022Q4_13F-HR.txt", "0009000001-23-000001", "13F-HR", "12-31-2022", "20230214", A,
           "13F HOLDINGS REPORT", [
               row("EXAMPLE CORP", EXAMPLE, 1198000000, 10000000),
               row("OTHER CO", OTHER, 4500000, 90000),
               row("SHARED CO", SHARED, 2100000, 50000, discretion="DFND", other="1"),
           ], schema_version="X0202", included=(B,))
submission("A_2022Q4_13F-HRA_restatement.txt", "0009000001-23-000003", "13F-HR/A", "12-31-2022", "20230301", A,
           "13F HOLDINGS REPORT", [
               row("EXAMPLE CORP", EXAMPLE, 1199000000, 10000000),
               row("OTHER CO", OTHER, 4500000, 90000),
               row("SHARED CO", SHARED, 2100000, 50000, discretion="DFND", other="1"),
           ], schema_version="X0202", amendment=(2, "RESTATEMENT"), included=(B,))
submission("B_2022Q4_13F-HR.txt", "0009000002-23-000001", "13F-HR", "12-31-2022", "20230210", B,
           "13F HOLDINGS REPORT", [
               row("SHARED CO", SHARED, 2100000, 50000, discretion="DFND"),
               row("B ONLY CO", BONLY, 700000, 7000),
           ], schema_version="X0202")
submission("C_2022Q4_13F-NT.txt", "0009000003-23-000001", "13F-NT", "12-31-2022", "20230213", C,
           "13F NOTICE", [], schema_version="X0202", reporting_for_me=(A,))

if __name__ == "__main__":
    print("written:", sorted(p.name for p in HERE.glob("*.txt")))
