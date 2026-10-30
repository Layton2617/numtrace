# Offline SEC fixtures (real filings, no sec.gov needed)

Source: `dgunning/edgartools` @ `e46440efe29c97ac4c70903e3382d143c5bd00b4` (MIT), cloned to `stack/vendor/edgartools/`.
Three places hold real filings: `data/`, `tests/fixtures/`, and `tests/cassettes/` (vcrpy recordings of real sec.gov responses, 213 files, 847 MB).
`stack/tools/extract_cassettes.py --write` pulls the 13F-HR, DEF 14A and companyfacts responses out of the cassettes into `stack/fixtures/sec/`. The full index of 88 recorded submissions is in `fixtures/sec/index.tsv`.
Paths below are relative to `stack/`. `V/` = `vendor/edgartools/`.

## Form 13F: the unit change (values in dollars for filings submitted on or after 3 Jan 2023)

"Filing-date rule" is what the proposal assumes. "Actual unit" comes from the implied price per share (value ÷ shares, median over plain equity rows) and the cover total. **The filing-date rule gives the wrong unit for 2 of the 5 real 13F-HRs that have an information table.** The data_layer must store the raw value, the rule's unit, and an evidence-based unit separately. Do not use edgartools' normalised value: `edgar/thirteenf/units.py` decides by implied price, then by schema `X0202`, then by report period ≤ 2022-09-30. It never uses the filing date.

| Path | Form | Manager (CIK) | Period | Filed | vs 3 Jan 2023 | Rows / cover total | Filing-date rule | Actual unit | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `fixtures/sec/13FHR/0001619124-21-000002.txt` (cassette `test_presplit_megacap_thousands…`) | 13F-HR | ACTIAM N.V. (1619124) | 2020-12-31 | 2021-02-12 | **before** | 5 / 732,158 | thousands | thousands (median px 1.75 → $1,750/sh, pre-split mega-caps) | full SGML submission |
| `V/tests/fixtures/13f/kahn-0001039565-22-000009/{primary,holdings}.xml` | 13F-HR | Kahn Brothers Group (1039565) | 2022-03-31 | 2022-05-02 | **before** | 46 / 787,553,693 | thousands | **dollars** (ALCON 254,000 / 3,175 sh = $80) | the rule is WRONG; the holdings sum is 787,553,692, one dollar off the cover total; no `schemaVersion` |
| `fixtures/sec/13FHR/0001790837-23-000002.txt` (cassette `test_post_cutover_thousands…`) | 13F-HR | Bull Street Advisors (1790837) | 2022-12-31 | 2023-02-28 | **after** | 90 / 192,767 | dollars | **thousands** (all 90 implied px < $1) | the rule is WRONG; schema X0202; a Q4-2022 boundary filing |
| `fixtures/sec/13FHR/0001140361-23-013281.txt` (cassette `test_pre_cutover_dollars…`) | 13F-HR (COMBINATION REPORT) | MetLife Inc (1099219) | 2021-12-31 | 2023-03-23 | **after** (late) | 6 / 11,019,796 | dollars | dollars (median px 49) | old period filed late: the filing-date rule is right and a period-based rule is wrong. The cover lists 3 other managers (co-filing group); primary-doc copy at `V/data/metlife.13F-HR.primarydoc.xml` |
| `V/data/xml/abacus.13F-HR.primarydoc.xml` | 13F-HR (primary doc only) | Abacus Wealth Partners (1978011) | 2020-12-31 | signed 2023-05-31 | **after** (late) | 92 / 114,663,960 | dollars | n/a (no info table) | cover only |
| `V/data/13F.0001894188-23-000007.txt` | 13F-HR | LTS One Management LP (1894188) | 2023-09-30 | 2023-11-14 | after | 14 / 454,926,000 | dollars | dollars (px 174) | full SGML |
| `V/data/13f/0001951757-25-000093.nc` | 13F-HR | Acorn Creek Capital (1994827) | 2024-12-31 | 2025-01-22 | after | 77 / 194,611,845 | dollars | dollars (px 152) | SGML (.nc) |
| `V/data/xml/13F-HR.infotable.xml` (+ HTML render `V/data/13F-HR.privateportfolio.infotable.html`) | info table only | unknown | unknown | unknown | after (render says "to the nearest dollar") | 255 rows, 3 PRN | — | dollars (px 103) | no metadata |
| `V/data/form.13FHR.xml` | HTML render of an info table | unknown | unknown | unknown | after | 375 rows | — | — | HTML only |
| `V/data/13f/EDGAR Form 13F XML Samples/…/Sample_13F-HR.xml`, `Sample_13F-HRA.xml` (RESTATEMENT, amendmentNo 45), `Sample_13F-NT(A).xml`, `Sample_13F-CTR(A).xml`, `information_table.xml` | SEC technical samples | **synthetic** ("BIG FUND TRUST inc", CIK 0123456789) | 2021-12-31 | — | — | 169 rows | — | thousands-like | parser tests only, **not real data** |
| `V/data/13f/EDGAR Form 13F Schema Files/…/eis_13F*.xsd` | XSD | — | — | — | — | — | — | — | schema |

**Missing offline:** no real 13F-HR/A (RESTATEMENT or NEW HOLDINGS); no real rows with `putCall` or `otherManager` populated (all empty); no manager with two consecutive quarters; no co-filing group with both member filings. The Q3→Q4 2022 boundary pair for one manager has to be downloaded on lab infra.

## Statements (10-K / 10-Q)

companyfacts JSON, extracted from cassettes to `fixtures/sec/companyfacts/` (facts filed up to 2026):

| File | Company | Facts | Latest `filed` |
|---|---|---|---|
| `CIK0000320193.json` | Apple | 24,579 | 2026-01-30 |
| `CIK0000789019.json` | Microsoft | 31,574 | 2026-01-28 |
| `CIK0000021344.json` | Coca-Cola | 32,819 | 2026-02-20 |
| `CIK0001045810.json` | NVIDIA | 26,903 | 2026-06-17 |
| `CIK0001018724.json` | Amazon | 29,222 | 2026-07-08 |
| `CIK0001652044.json` | Alphabet | 20,324 | 2026-06-04 |
| `CIK0001835632.json` | Marvell | 8,053 | 2026-05-28 |

The same companies also have XBRL instances and linkbases in `V/tests/fixtures/xbrl/<ticker>/` (e.g. `aapl/10k_2023`, `aapl/10q_2023`, `msft/10k_2024`, `ko/10k_2024`, `nvda/10k_2026`; 19 tickers in all) and full 10-K/10-Q HTML in `V/tests/fixtures/html/<ticker>/{10k,10q}/` (52 companies, 2024–25). **AAPL, MSFT, KO and NVDA each have all three (companyfacts, XBRL, 10-K and 10-Q HTML), so they are the obvious pilot companies.** Other real items: `V/data/Netflix.10K.xbrl.xml`, `V/data/AAPL-Xbrl.2013.xml`, `V/data/*_gaap.csv`, and full-submission 10-K/10-Q in cassettes (see `index.tsv`).

## NQDC (DEF 14A, Item 402(i))

| Path | Form | Company | Period | Filed | 402(i) table? |
|---|---|---|---|---|---|
| `V/data/cabot.DEF14A.ixbrl.html` | DEF 14A (inline XBRL, `AmendmentFlag=false`) | Cabot Corp (CIK 16040) | FY ended 2023-09-30 | ≈ Jan 2024 (meeting 2024-03; exact date not in file) | **yes.** "Nonqualified Deferred Compensation Table": exec/registrant contributions, earnings, withdrawals, FYE balance, two plans per NEO (Deferred Compensation Plan + Supplemental 401(k)) |
| `V/data/troweprice.DEF14A.html` | DEF 14A | T. Rowe Price fund | — | — | no NQDC |
| `fixtures/sec/DEF14A/0000893739-20-000019.txt` | DEF 14A | 180 Degree Capital (closed-end fund) | 2020-05-14 | 2020-03-25 | no NQDC |

10-K HTML with NQDC mentions (plan liability / rabbi trust / COLI notes): `V/tests/fixtures/html/{ibm,nke,wfc,unp,cat,axp,ma,orcl}/10k/*.html`, `V/data/html/Oracle.10-K.html`. **Only one real 402(i) table is available offline (Cabot), and none of the pilot statement companies has its proxy offline.**
