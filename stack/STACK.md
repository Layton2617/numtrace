# NumTrace: open-source stack

Install: `./install.sh` (idempotent: pinned pip requirements, vendor clones checked out at pinned commits, fixture extraction). Verify: `python3 smoke_test.py`. It blocks all non-loopback sockets and must print `11/11 passed`.

| Role | Chosen | Version / commit | Licence | Why (vs alternatives) | Offline here? |
|---|---|---|---|---|---|
| EDGAR parsing: 13F primary doc + info table, SGML submissions, XBRL, companyfacts | **edgartools** | 5.58.0; repo `e46440ef` | MIT | Handles everything in one package: `ThirteenF.parse_primary_document_xml` (form, period, isAmendment, amendmentNo, amendmentType, reportType, summary totals), `parse_infotable_xml` (Value, SharesPrnAmount, SH/PRN, PutCall, OtherManager, voting), `FilingSGML.from_source` (form, filing date, period, accession), `XBRL.from_directory`, `EntityFactsParser`. Its repo also ships real SEC fixtures and VCR cassettes. *sec-edgar-downloader* only downloads (no parsing). *python-xbrl* is unmaintained and has no 13F support. | yes, all 5 checks run from local files |
| Agent / eval harness | **thin custom stage loop** (harness/ owns it) + **inspect_ai** as the model layer | inspect_ai 0.3.268 | MIT | The pipeline is a fixed program (retrieve → extract → compute → synthesise). Substituting a stage means reloading a logged trajectory, replacing stage *k* and re-running *k+1…*. That is ~20 lines of plain Python with JSONL/pydantic records, and awkward in any framework. inspect_ai is used only through `get_model()`: one interface for OpenAI, Anthropic and DeepSeek (OpenAI-compatible), a `mockllm` for offline runs, per-call token usage for cost per report, and a response cache for replay. Its `eval()` logs (every model and tool event) are available if we want its log viewer. *langgraph*: `update_state`/time-travel supports substitution, but it brings in the langchain stack and logs are checkpoints, not a flat trace. *smolagents*: a CodeAgent runs model-written code, and there is no stage substitution or deterministic replay. | yes (mockllm). Mock outputs must set `usage`, otherwise inspect downloads a tiktoken file (blocked) |
| Frozen retrieval corpus | **bm25s** | 0.3.11 | MIT | BM25 with `save`/`load`, so the index snapshot is a hashable artefact for frozen-corpus replay. It is sparse and fast, and accepts pre-tokenised input, so we keep numbers like `4,120` or `74.9` as single tokens (the default token pattern splits them). *rank_bm25* 0.2.2 (Apache-2.0, already pulled in by edgartools) is the fallback: unmaintained, no persistence. **No dense retrieval here**: embedding models come from huggingface.co (blocked); FAISS and a small encoder can be added on lab infra later as an ablation. | yes |
| Monitor: NLI | **SummaC** (ZS and Conv) | repo `tingofurro/summac` `c1f3da93`; PyPI `summac==0.0.4` | Apache-2.0 | This is the method cited in the proposal [6]. The Conv checkpoint `summac_conv_vitc_sent_perc_e.bin` is in the repo (vendored). The NLI backbones (`tals/albert-xlarge-vitaminc-mnli`, `roberta-large-mnli`, `ynie/roberta-large-snli_mnli_fever_anli…`) come from HF. | **no**: lab infra (torch + HF). Code and checkpoint are vendored |
| Monitor: semantic entropy | **port of jlko/semantic_uncertainty** → `ports/semantic_entropy.py` | repo `a8d9aa8c` | Clear BSD (notice kept) | This is the reference code of [8]. Upstream imports torch, wandb and transformers at import time, so the four core functions are ported unchanged with an injectable `entails(a,b)`. Use the discrete estimator (`cluster_assignment_entropy`) for Claude and any API without log-probs. The entailment model is DeBERTa-v2-xlarge-MNLI (HF) or an LLM judge. | the port runs with a mock NLI; the real NLI runs on lab infra |
| Monitor: self-consistency | built-in (`collections.Counter` over normalised numeric answers) | — | — | Trivial [7]. | yes |
| Monitor: LLM judge, concept check | inspect_ai model layer; concept check = harness code over the fact store | — | — | No extra package. | judge = lab infra (API keys) |
| Annotation agreement | **scikit-learn** `cohen_kappa_score`, **statsmodels** `fleiss_kappa`, **scipy** `stats.bootstrap` | 1.8.0 / 0.15.0 / 1.17.1 | BSD-3 | These are the standard implementations. **Bootstrap clustered by company is not in scipy**: use the 10-line resampler in `smoke_test.py` §5. | yes |
| Proxy (DEF 14A) NQDC tables | **pandas.read_html** (lxml flavour) + **beautifulsoup4** for locating sections | pandas 3.0.2, lxml 6.1.0, bs4 4.14.3, html5lib 1.1 | BSD-3 / BSD-3 / MIT / MIT | Works on the real Cabot 402(i) table. EDGAR tables need post-processing: colspan columns are duplicated ×4, `$`/`—` cells are split, and there are several plans per NEO in one cell (e.g. `2,967,182 2,700,426`). edgartools' own `edgar.documents` table parser is an alternative for data_layer to try. | yes |

Other pins: pydantic 2.13.3, numpy 2.4.4, PyYAML 6.0.3 (cassette extractor). Full list in `requirements.txt`. Lab-only packages are in `requirements-lab.txt`.

## What runs where

**Offline:** all parsing of local 13F / XBRL / companyfacts / proxy files; BM25 corpus build and replay; the full harness loop with `mockllm`; semantic entropy and self-consistency with a mock NLI; kappa and bootstrap; the fixture extractor (`tools/extract_cassettes.py`).

**Lab infra only:**
1. **SEC downloads** (need network access): the pilot's 2 managers × Q3/Q4 2022 boundary pair plus later quarters; any real 13F-HR/A; co-filing-group member filings; the Form 13F Data Sets zips [20]; DEF 14A proxies for the pilot companies (none are offline); 10-K notes with the NQDC liability. edgartools does this with `set_identity("Name email")`. Freeze everything into the corpus snapshot and hash it.
2. **HF models**: SummaC backbones (ZS, and Conv using the vendored checkpoint), DeBERTa-v2-xlarge-MNLI for semantic-entropy clustering, and any dense retriever. `pip install -r requirements-lab.txt` in a separate venv (SummaC pins `huggingface-hub<=0.28`).
3. **LLM APIs**: GPT, Claude and DeepSeek agents plus the LLM judge, through inspect_ai providers (`openai/…`, `anthropic/…`, `openai-api/deepseek/…`). Enable `tiktoken` on lab infra or always pass explicit `usage`.

## Notes on third-party packages

- **data_layer (13F unit):** do not use edgartools' normalised values or its `resolve_value_unit`. It decides by implied price, then schema `X0202`, then report period ≤ 2022-09-30, never by filing date. Use `parse_infotable_xml` (raw `Value`), store the raw value, the filing-date-rule unit and an evidence unit (implied price, cover total) separately, and flag disagreements. The real fixtures already show both failure directions (Kahn: filed before the change, values in dollars; Bull Street: filed after the change, values in thousands). See FIXTURES.md.
- **data_layer (co-filing):** edgartools reads other managers from the *summary page* (`otherManagers2Info`, the "other included managers"). The *cover page* `otherManagersInfo` list (managers reporting for this filer, e.g. MetLife's 3) is not exposed, so parse it with lxml directly.
- `FilingSGML.get_all_document_types()` raises AttributeError in 5.58.0. Use `.attachments` instead.
- `parse_infotable_xml` truncates values with `int(float(x))`, which is harmless for 13F integers.

## Smoke test output (offline, 2026-09-24)

```
NumTrace stack smoke test  (network: BLOCKED except loopback)
python 3.11.15 | edgartools=5.58.0, inspect_ai=0.3.268, bm25s=0.3.11, statsmodels=0.15.0, scikit-learn=1.8.0, scipy=1.17.1, pandas=3.0.2, lxml=6.1.0, beautifulsoup4=4.14.3
[PASS] 1a edgartools: 13F-HR full submissions (SGML) -> metadata + info table: 
      0001619124-21-000002 13F-HR filed=2021-02-12 period=2020-12-31 'ACTIAM N.V.' type='13F HOLDINGS REPORT' rows=5 sum(value)=732158 cover_total=732158 median_px=1.75 filing-date-rule=thousands
      0001790837-23-000002 13F-HR filed=2023-02-28 period=2022-12-31 'Bull Street Advisors, LLC' type='13F HOLDINGS REPORT' rows=90 sum(value)=192767 cover_total=192767 median_px=0.11 filing-date-rule=dollars
      0001140361-23-013281 13F-HR filed=2023-03-23 period=2021-12-31 'METLIFE INC' type='13F COMBINATION REPORT' rows=6 sum(value)=11019796 cover_total=11019796 median_px=49.32 filing-date-rule=dollars
[PASS] 1b edgartools: Kahn Q1-2022 primary+holdings XML (pre-cutover filing, values already in $): rows=46 sum=787553692 cover=787553693 ALCON value=254000 shares=3175 -> $80/sh if dollars
[PASS] 1c edgartools: 13F-HR/A amendment type (SEC sample XML): is_amendment=True amendment_no=45 type=RESTATEMENT (synthetic SEC sample, not a real filer)
[PASS] 1d edgartools: XBRL instance (AAPL 10-K FY2023) -> balance-sheet fact: 1164 facts; LongTermDebtNoncurrent@2023-09-30 = 95281000000
[PASS] 1e edgartools: companyfacts JSON (AAPL) -> EntityFacts: Apple Inc.: 24579 facts; raw LongTermDebtNoncurrent FY23 = 95281000000 (accn 0000320193-23-000106, filed 2023-11-03)
[PASS] 2 inspect_ai: mockllm + tool call, eval log records every event (offline): status=success score=1.0 events=['model', 'sample_init', 'score', 'span_begin', 'span_end', 'state', 'tool'] tool_result='95,281'
[PASS] 3 bm25s: BM25 over 3 toy docs, numeric-preserving tokenizer, save/load snapshot: top doc=1 scores=[1.312, 1.019, 0.0]
[PASS] 4a semantic entropy (port of jlko/semantic_uncertainty) + self-consistency, mock NLI: ids=[0, 0, 1, 0] discrete_SE=0.562 rao_SE=0.268 self-consistency=3/4
[PASS] 4b SummaC: repo + conv checkpoint vendored (NLI backbones need huggingface -> lab infra): model_map ok, summac_conv_vitc_sent_perc_e.bin present; torch NOT installed here (expected)
[PASS] 5 agreement: Cohen (sklearn), Fleiss (statsmodels), bootstrap CI (scipy) + cluster bootstrap: cohen=0.676 fleiss=0.667 iid95%=[0.25,1.00] cluster95%=[0.33,1.00]
[PASS] 6 pandas.read_html+lxml+bs4: Cabot DEF 14A Item 402(i) NQDC table: raw table (6, 21); Keohane row: Sean D. Keohane  Deferred Compensation Plan  Supplemental 401(k) Plan 522,852  — 52,285  127,535 134,265  247,413 —  — 2,967,182 2,700,426
11/11 passed
```
