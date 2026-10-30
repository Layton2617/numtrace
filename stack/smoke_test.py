"""NumTrace stack smoke test: every chosen component imports and works with the network OFF.

Run:  python smoke_test.py        (from stack/; needs install.sh first)
All outbound sockets are blocked below, so a pass proves the checks are offline.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import socket
import sys
import tempfile
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
V = HERE / "vendor"
ET = V / "edgartools"
FX = HERE / "fixtures" / "sec"
os.chdir(HERE)
os.environ.setdefault("EDGAR_IDENTITY", "NumTrace smoke test numtrace@example.com")
os.environ.setdefault("INSPECT_LOG_DIR", tempfile.mkdtemp(prefix="inspect_logs_"))
warnings.filterwarnings("ignore")

_real_connect = socket.socket.connect


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and (host.startswith("127.") or host in ("localhost", "::1") or self.family == socket.AF_UNIX):
        return _real_connect(self, address)
    raise OSError(f"network disabled in smoke test: {address}")


socket.socket.connect = _guarded_connect

RESULTS: list[tuple[str, bool, str]] = []


def check(name):
    def deco(fn):
        try:
            msg = fn()
            RESULTS.append((name, True, msg or ""))
        except Exception as e:  # noqa: BLE001
            RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
        return fn
    return deco


CUTOVER = "2023-01-03"


@check("1a edgartools: 13F-HR full submissions (SGML) -> metadata + info table")
def _():
    from edgar.sgml import FilingSGML
    from edgar.thirteenf import ThirteenF
    lines = []
    for acc in ["0001619124-21-000002", "0001790837-23-000002", "0001140361-23-013281"]:
        s = FilingSGML.from_source(str(FX / "13FHR" / f"{acc}.txt"))
        prim = ThirteenF.parse_primary_document_xml(s.xml())
        it = next(a for a in s.attachments if a.document_type == "INFORMATION TABLE")
        df = ThirteenF.parse_infotable_xml(it.content)
        assert {"Value", "SharesPrnAmount", "Type", "PutCall", "OtherManager"} <= set(df.columns)
        raw_sum = int(df["Value"].sum())
        total = int(prim.summary_page.total_value)
        eq = df[(df.Type == "Shares") & (df.SharesPrnAmount > 0) & (df.PutCall == "")]
        px = float((eq.Value / eq.SharesPrnAmount).median())
        rule_unit = "dollars" if str(s.filing_date) >= CUTOVER else "thousands"
        lines.append(f"{acc} {s.form} filed={s.filing_date} period={s.period_of_report} "
                     f"{prim.cover_page.filing_manager.name!r} type={prim.cover_page.report_type!r} "
                     f"rows={len(df)} sum(value)={raw_sum} cover_total={total} median_px={px:,.2f} "
                     f"filing-date-rule={rule_unit}")
        assert raw_sum == total or abs(raw_sum - total) <= len(df)
    return "\n      " + "\n      ".join(lines)


@check("1b edgartools: Kahn Q1-2022 primary+holdings XML (pre-cutover filing, values already in $)")
def _():
    from edgar.thirteenf import ThirteenF
    d = ET / "tests/fixtures/13f/kahn-0001039565-22-000009"
    prim = ThirteenF.parse_primary_document_xml((d / "primary.xml").read_text())
    df = ThirteenF.parse_infotable_xml((d / "holdings.xml").read_text())
    alcon = df[df.Issuer.str.contains("ALCON")].iloc[0]
    assert int(df.Value.sum()) == 787553692 and int(prim.summary_page.total_value) == 787553693
    return (f"rows={len(df)} sum={int(df.Value.sum())} cover={int(prim.summary_page.total_value)} "
            f"ALCON value={alcon.Value} shares={alcon.SharesPrnAmount} -> ${alcon.Value / alcon.SharesPrnAmount:.0f}/sh if dollars")


@check("1c edgartools: 13F-HR/A amendment type (SEC sample XML)")
def _():
    from edgar.thirteenf import ThirteenF
    p = ET / "data/13f/EDGAR Form 13F XML Samples/EDGAR Form 13F XML Samples/Sample_13F-HRA.xml"
    prim = ThirteenF.parse_primary_document_xml(p.read_text())
    cp = prim.cover_page
    assert cp.is_amendment and cp.amendment_info.amendment_type == "RESTATEMENT"
    return f"is_amendment={cp.is_amendment} amendment_no={cp.amendment_number} type={cp.amendment_info.amendment_type} (synthetic SEC sample, not a real filer)"


@check("1d edgartools: XBRL instance (AAPL 10-K FY2023) -> balance-sheet fact")
def _():
    from edgar.xbrl import XBRL
    x = XBRL.from_directory(ET / "tests/fixtures/xbrl/aapl/10k_2023")
    facts = x.facts.query().by_concept("us-gaap:LongTermDebtNoncurrent").to_dataframe()
    row = facts[facts["period_instant"].astype(str).str.startswith("2023-09-30")].iloc[0]
    return f"{len(x._facts) if hasattr(x, '_facts') else ''} facts; LongTermDebtNoncurrent@2023-09-30 = {row['value']}"


@check("1e edgartools: companyfacts JSON (AAPL) -> EntityFacts")
def _():
    from edgar.entity.parser import EntityFactsParser
    j = json.loads((FX / "companyfacts/CIK0000320193.json").read_text())
    ef = EntityFactsParser.parse_company_facts(j)
    raw = j["facts"]["us-gaap"]["LongTermDebtNoncurrent"]["units"]["USD"]
    fy23 = [f for f in raw if f.get("end") == "2023-09-30" and f.get("form") == "10-K"][0]
    return f"{ef.name}: {len(ef.get_all_facts())} facts; raw LongTermDebtNoncurrent FY23 = {fy23['val']} (accn {fy23['accn']}, filed {fy23['filed']})"


@check("2 inspect_ai: mockllm + tool call, eval log records every event (offline)")
def _():
    from inspect_ai import Task, eval as ieval
    from inspect_ai.dataset import Sample
    from inspect_ai.log import read_eval_log
    from inspect_ai.model import ModelOutput, ModelUsage, get_model
    from inspect_ai.scorer import includes
    from inspect_ai.solver import generate, use_tools
    from inspect_ai.tool import tool

    @tool
    def lookup():
        async def execute(concept: str):
            """Look up a reported figure in the frozen fact store.

            Args:
              concept: taxonomy concept name
            """
            return {"LongTermDebtNoncurrent": "95,281"}.get(concept, "not found")
        return execute

    usage = ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    outs = [ModelOutput.for_tool_call("mockllm/model", "lookup", {"concept": "LongTermDebtNoncurrent"}),
            ModelOutput.from_content("mockllm/model", "Long-term debt, noncurrent: $95,281 million")]
    for o in outs:
        o.usage = usage
    task = Task(dataset=[Sample(input="Apple FY2023 noncurrent LTD?", target="95,281")],
                solver=[use_tools(lookup()), generate()], scorer=includes())
    log = ieval(task, model=get_model("mockllm/model", custom_outputs=outs), display="none")[0]
    s = read_eval_log(log.location).samples[0]
    kinds = sorted({e.event for e in s.events})
    tool_ev = [e for e in s.events if e.event == "tool"][0]
    assert log.status == "success" and "tool" in kinds and "model" in kinds
    return f"status={log.status} score={log.results.scores[0].metrics['accuracy'].value} events={kinds} tool_result={tool_ev.result!r}"


@check("3 bm25s: BM25 over 3 toy docs, numeric-preserving tokenizer, save/load snapshot")
def _():
    import bm25s
    docs = ["Long-term debt, net of current portion 4,120",
            "Total long-term debt 4,870 of which current portion 750",
            "Corporate-owned life insurance cash surrender value 74.9 million rabbi trust"]
    tok = lambda t: re.findall(r"\d[\d,.]*\d|\d|[a-z]+", t.lower())
    r = bm25s.BM25()
    r.index([tok(d) for d in docs], show_progress=False)
    with tempfile.TemporaryDirectory() as d:
        r.save(d)
        r2 = bm25s.BM25.load(d)
    res, sc = r2.retrieve([tok("total long-term debt including current portion")], k=3, show_progress=False)
    top = int(res[0, 0])
    assert top == 1
    return f"top doc={top} scores={[round(float(x), 3) for x in sc[0]]}"


@check("4a semantic entropy (port of jlko/semantic_uncertainty) + self-consistency, mock NLI")
def _():
    sys.path.insert(0, str(HERE))
    from collections import Counter
    from ports.semantic_entropy import cluster_assignment_entropy, get_semantic_ids, logsumexp_by_id, predictive_entropy_rao
    num = lambda s: re.findall(r"\d[\d,.]*", s)
    mock_nli = lambda a, b: 2 if num(a) == num(b) else 0
    samples = ["Total LTD was $4.12 billion", "LTD totalled 4.12 billion", "Total LTD was $4.87 billion", "It was 4.12 billion"]
    ids = get_semantic_ids(samples, mock_nli)
    se_discrete = cluster_assignment_entropy(ids)
    se_rao = predictive_entropy_rao(logsumexp_by_id(ids, [-1.0, -1.2, -2.5, -1.1]))
    majority, votes = Counter(ids).most_common(1)[0]
    return f"ids={ids} discrete_SE={se_discrete:.3f} rao_SE={se_rao:.3f} self-consistency={votes}/{len(ids)}"


@check("4b SummaC: repo + conv checkpoint vendored (NLI backbones need huggingface -> lab infra)")
def _():
    src = (V / "summac/summac/model_summac.py").read_text()
    assert "tals/albert-xlarge-vitaminc-mnli" in src and (V / "summac/summac_conv_vitc_sent_perc_e.bin").stat().st_size > 1000
    try:
        import torch  # noqa: F401
        have = "torch present"
    except ImportError:
        have = "torch NOT installed here (expected)"
    return f"model_map ok, summac_conv_vitc_sent_perc_e.bin present; {have}"


@check("5 agreement: Cohen (sklearn), Fleiss (statsmodels), bootstrap CI (scipy) + cluster bootstrap")
def _():
    import numpy as np
    from scipy.stats import bootstrap
    from sklearn.metrics import cohen_kappa_score
    from statsmodels.stats.inter_rater import aggregate_raters, fleiss_kappa
    a = np.array([1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0])
    b = np.array([1, 1, 0, 0, 0, 0, 1, 1, 0, 1, 0, 0])
    c = np.array([1, 0, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0])
    ck = cohen_kappa_score(a, b)
    fk = fleiss_kappa(aggregate_raters(np.stack([a, b, c], axis=1))[0])
    rng = np.random.default_rng(0)
    ci = bootstrap((np.arange(len(a)),), lambda i, axis=-1: np.apply_along_axis(lambda k: cohen_kappa_score(a[k], b[k]), axis, i),
                   n_resamples=500, method="percentile", random_state=rng).confidence_interval
    company = np.array([0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3])
    boots = []
    for _ in range(500):
        pick = rng.choice(np.unique(company), size=4, replace=True)
        k = np.concatenate([np.where(company == g)[0] for g in pick])
        if len(set(a[k])) > 1 or len(set(b[k])) > 1:
            boots.append(cohen_kappa_score(a[k], b[k]))
    lo, hi = np.nanpercentile(boots, [2.5, 97.5])
    return f"cohen={ck:.3f} fleiss={fk:.3f} iid95%=[{ci.low:.2f},{ci.high:.2f}] cluster95%=[{lo:.2f},{hi:.2f}]"


@check("6 pandas.read_html+lxml+bs4: Cabot DEF 14A Item 402(i) NQDC table")
def _():
    import pandas as pd
    from bs4 import BeautifulSoup
    html = (ET / "data/cabot.DEF14A.ixbrl.html").read_text()
    i = html.find("Nonqualified Deferred Compensation Table")
    j = html.find("<table", i)
    k = html.find("</table>", j) + len("</table>")
    df = pd.read_html(io.StringIO(html[j:k]), flavor="lxml")[0].dropna(how="all").dropna(axis=1, how="all")
    text = BeautifulSoup(html[j:k], "lxml").get_text(" ", strip=True)
    assert "Aggregate Balance at Last FYE" in text and "Keohane" in text
    keo = " ".join(str(v) for v in df[df.iloc[:, 0].astype(str).str.contains("Keohane")].iloc[0].dropna().unique())
    return f"raw table {df.shape}; Keohane row: {keo}"


if __name__ == "__main__":
    import importlib.metadata as md
    print("NumTrace stack smoke test  (network: BLOCKED except loopback)")
    print("python", sys.version.split()[0], "|", ", ".join(f"{p}={md.version(p)}" for p in
          ["edgartools", "inspect_ai", "bm25s", "statsmodels", "scikit-learn", "scipy", "pandas", "lxml", "beautifulsoup4"]))
    for name, ok, msg in RESULTS:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {msg}")
    n_fail = sum(not ok for _, ok, _ in RESULTS)
    print(f"{len(RESULTS) - n_fail}/{len(RESULTS)} passed")
    sys.exit(1 if n_fail else 0)
