# data_layer: ground truth for NumTrace

Pure-Python package (pydantic models, pure functions, pytest). It depends on
`pydantic>=2`, `lxml`, `pandas` and `pytest`, and does not use `edgartools` (see Open issues).
All parsing works offline.

```
python -m pytest data_layer/tests -q      # run from the repo root
```

| Module | Purpose |
|---|---|
| `facts.py` | `Fact`: the record shared by the three fact stores (entity, concept, canonical value and unit, period, dimensions, original value and unit, form, accession, filing date, derived flag) |
| `thirteen_f.py` | Form 13F: parsing, the unit rule, amendment merge, co-filing groups, positions |
| `statements.py` | 10-K/10-Q fact store from companyfacts JSON, the concept binding table, derivations |
| `nqdc.py` | Item 402(i) table schema and parser, roll-forward check, company-level NQDC record |
| `binding_check.py` | Checks a claim's concept against the fact store (value / binding / "right number, wrong account") |

---

## Rules and their sources

| # | Rule | Where implemented | Source |
|---|---|---|---|
| R1 | **Units follow the filing date.** In the information table, `<value>` is in **thousands of dollars** for filings made **before 3 January 2023**. It is in **whole dollars** for filings made **on or after 3 January 2023**. The filing date decides the unit, not the report period, so an amendment filed in 2023 for a 2022 quarter is in dollars. The filing date is the EDGAR *FILED AS OF DATE*. | `value_unit_for_filing_date`, `normalize_value`, `parse_primary_doc` | SEC Form 13F FAQ **Q36** (proposal ref. [18]) |
| R2 | **Every row keeps `original_value` and `original_unit`**, plus `value_usd`. Each filing also records `unit_basis`, which is either the rule or an explicit override. | `HoldingRow`, `FilingMeta.unit_basis` | Proposal p.2: as-filed data "with … original unit" |
| R3 | **Filer deviations are flagged, never fixed silently.** `unit_diagnostics` flags a filing when at least 50 % of its SH rows (options excluded) imply a price below $1, or when the median implied price is above $10,000. A verified exception is then entered as a `UnitOverride(accession, unit, reason)`. | `unit_diagnostics`, `UnitOverride` | Our rule. The real counter-examples are in the fixtures (Bull Street, Kahn Brothers). |
| R4 | **Amendment merge.** Amendments are applied in filing order. A `RESTATEMENT` **replaces** all holdings for the period. A `NEW HOLDINGS` amendment **adds** rows. Each row keeps the accession it came from. Each amendment's rows use the unit of that amendment's own filing date (R1). | `merge_filings`, `merge_all` | SEC Form 13F FAQ **Q58**; Form 13F cover-page instructions (amendment type) |
| R5 | **Two manager lists.** (a) The summary page's *List of Other Included Managers* (`otherManagers2Info`, with sequence numbers). The information-table column *Other Manager* refers to these numbers. (b) The cover page's *List of Other Managers Reporting for this Manager* (`otherManagersInfo`), used on 13F-NT and Combination Reports. | `FilingMeta.other_included_managers`, `FilingMeta.other_managers_reporting` | SEC Form 13F FAQ **Q37**; Form 13F instructions; EDGAR 13F XML technical specification (`eis_13F_Filer.xsd`) |
| R6 | **Co-filing groups are defined by us.** The input is an explicit `CoFilingGroup(group_id, name, members[priority order])` table. `suggest_groups` proposes connected components from R5 only as candidates. | `CoFilingGroup`, `suggest_groups` | Proposal p.1 table: "co-filing groups defined by us" |
| R7 | **De-duplication (our rule).** Within a group, a later member's row is dropped when an earlier member already reported a row with the same CUSIP, class, put/call, SH/PRN, amount and dollar value, and either row is DFND or names other managers. Rows within one filing are never dropped, because they are separate sub-positions and are summed. Dropped rows are returned with the reason. | `group_rows` | Ours; follows Form 13F instructions on shared investment discretion |
| R8 | **Positions are in normalised dollars.** Put/call rows are excluded by default, because their value is the underlying's. Shares count SH rows and principal counts PRN rows. | `position`, `qoq_change` | Form 13F instructions, columns 4–6 |
| R9 | **Statements: bind every metric in advance.** Each pilot metric maps to its us-gaap concept(s). Neighbouring concepts that plain English conflates are listed alongside. | `CONCEPT_BINDINGS` | US GAAP taxonomy; proposal p.1 and p.2 |
| R10 | **Derivations are marked `derived`, never presented as reported.** Total LTD = noncurrent + current. Discrete Q4 = FY − 9M YTD, for additive durations only. EPS is not additive, so a Q4 EPS is `not_available`. | `get_metric`, `derive_q4` | Proposal p.2: "unreported concepts marked and flagged as derivable or not" |
| R11 | **companyfacts repeats facts** across filings. `select(policy="first")` returns the first-filed value (as originally reported). `"latest"` returns the most recent re-presentation. | `select` | SEC XBRL companyfacts API format |
| R12 | **402(i) columns:** executive contributions (b), registrant contributions (c), aggregate earnings (d), aggregate withdrawals/distributions (e) and aggregate balance at last FYE (f), per NEO and per plan. | `NQDCTableRow`, `parse_nqdc_table` | **17 CFR 229.402(i)(2)** (Regulation S-K Item 402(i)) |
| R13 | **Roll-forward check:** balance(t) ≈ balance(t−1) + (b) + (c) + (d) − (e). The tolerance is max($1,000, 1 %). A mismatch is flagged, not corrected. | `check_roll_forward`, `check_table_roll_forward` | Arithmetic implied by 402(i)(2). The identity can legitimately fail (after-FYE credits, forfeitures, a change in the NEO population). |
| R14 | **Company-level NQDC is a separate population.** The plan liability (note), the funding assets (cash / funds / COLI cash surrender value / company stock, with an `in_rabbi_trust` flag) and dated plan-document versions are kept apart from NEO balances. | `CompanyNQDCRecord`, `FundingAsset`, `PlanDocumentVersion` | Proposal p.1; IRS NQDC Audit Technique Guide (Pub. 5528) |
| R15 | **Binding check.** `value_ok` means the claimed number is a correct copy or rounding of **some** fact. The fact can be read in its canonical unit, its as-filed unit, or with a ×1,000 or ×1,000,000 scale slip. `binding_ok` means that fact has the target concept, period, unit, entity and dimensions. `value_ok ∧ ¬binding_ok` is **right number, wrong account**. | `check_claim` | Proposal p.1 ("value passes, binding fails") |
| R16 | **Rounding tolerance** is half a unit in the claim's last significant digit, counting at least 2 significant digits (4.12e9 → ±5e6; 6.13 → ±0.005). An optional `rel_tol` can widen it. | `half_unit_of_last_digit`, `values_match` | Ours |

---

## Functions

### `thirteen_f.py`

* `value_unit_for_filing_date(filing_date) -> "USD" | "USD_thousands"`: rule R1.
* `normalize_value(value, filing_date) -> float`: converts a value to whole dollars under R1.
* `parse_primary_doc(xml, accession, filing_date, unit_override=None) -> FilingMeta` reads:
  * form type, period, filer, report type
  * `is_amendment`, `amendment_no`, `amendment_type`
  * schema version
  * both manager lists (R5)
  * summary totals
  * value unit and unit basis
* `parse_information_table(xml, meta) -> list[HoldingRow]` reads, for each row:
  * issuer, class, CUSIP, FIGI
  * value (original and USD)
  * `sshPrnamt` and its type
  * put/call and investment discretion
  * other-manager sequence numbers
  * voting authority (sole, shared, none)
  * provenance
* `parse_filing(primary_xml, infotable_xml=None, *, accession, filing_date, unit_overrides=None) -> Filing13F` combines the two parsers above. If no information table is passed, it decodes a base64 one embedded in `<documents>`, as in the SEC samples.
* `parse_sgml_header(text)` and `parse_full_submission(text, unit_overrides=None)` read an EDGAR full-submission `.txt` file. They take the accession and *FILED AS OF DATE* from its header. `load_full_submission(path)` does the same from a file path.
* `validate_xml(xml, xsd_dir) -> list[str]` checks a document against the official EDGAR 13F XSDs.
* `unit_diagnostics(filing) -> UnitDiagnostics`: rule R3.
* `summary_total_difference(filing)` returns the sum of the rows minus `tableValueTotal`, in original units. The difference is reported, not reconciled.
* `merge_filings(original, amendments) -> AsFiledHoldings` returns the rows, a merge `history` and the filing metadata (R4). `merge_all(filings)` does the same for every (filer, period). It skips 13F-NT filings.
* `CoFilingGroup`, `suggest_groups(filings)` and `group_rows(group, holdings) -> GroupHoldings(rows, dropped)`: rules R6 and R7.
* `HoldingsStore(holdings, groups)` looks up entities by CIK or group id. It also has `.from_filings(...)` and `.rows(entity, period)`.
* `position(store, entity, cusip, period, include_options=False) -> Position` and `qoq_change(store, entity, cusip, p_from, p_to) -> PositionChange` compute positions in dollars (R8).
* `position_facts(store, entity, period) -> list[Fact]` returns `13f:PositionValue` and `13f:PositionShares` per CUSIP. The original value and unit are kept whenever all rows share a unit.

### `statements.py`

* `load_companyfacts(path | dict) -> list[StatementFact]` returns one row per reported fact. Each row carries:
  * concept (`us-gaap:X`) and unit
  * value
  * start and end dates
  * fy, fp and frame
  * form, filed date and accession

  `facts_to_frame(facts)` turns the list into a pandas table.
* `CONCEPT_BINDINGS` holds one `ConceptBinding` per metric, with these fields:
  * `metric`, `description` and `concepts`
  * `unit`, `period_type` and `additive`
  * `derivation` and `not_to_confuse_with`

  The pilot metrics are:
  * `total_long_term_debt` (LongTermDebt; derivable as Noncurrent + Current)
  * `long_term_debt_noncurrent` and `long_term_debt_current`
  * `cash_and_cash_equivalents`
  * `eps_diluted` (not derivable)
  * `net_income` (Q4 derivable)
  * `deferred_comp_liability_total` and `deferred_comp_liability_noncurrent`
  * `coli_cash_surrender_value`

  `binding_for(metric_or_concept)` looks up a binding by metric or concept.
* `select(facts, entity, concept, period_end, period_start=None, policy="first")`: rule R11.
* `get_metric(...) -> MetricValue(status=reported|derived|not_available, fact, inputs)` and `derive_q4(facts, entity, metric, fy_end)`: rule R10.

### `nqdc.py`

* `NQDCTableRow`: rule R12. A blank cell is `None` and a dash is `0`.
* `normalize_column(header)` maps a header to its field. `parse_amount(cell)` handles `$`, commas, `(neg)`, dashes and footnote marks.
* `parse_nqdc_table(html, fiscal_year_end=None, source=None, table_index=None)` uses `pandas.read_html`. It first marks `<br>` and `</p>` breaks, so it can split cells where several plans are stacked in one row. It also:
  * finds the header row inside the table body
  * collapses colspan duplicates
  * drops `$` cells and Total rows
* `totals_by_executive(rows)`, `check_roll_forward(current, prior)` and `check_table_roll_forward(cur, prior)`: rule R13.
* `CompanyNQDCRecord` stores `plan_liability`, `funding_assets` and `plan_documents`. Its `.document_in_force(as_of)` method returns the plan document in force on a date, and `.to_facts()` converts the record into facts (R14). `table_rows_to_facts(rows, entity)` converts 402(i) table rows into facts.

### `binding_check.py`

* `Claim(entity, concept_as_stated, period_end, period_start=None, unit="USD", value, dimensions={}, target=None)`. `concept_as_stated` can be a metric key, a concept, or a phrase from `PHRASES`, for example "total long-term debt" or "deferred compensation obligation".
* `check_claim(claim, facts, rel_tol=0.0) -> BindingResult` returns:
  * `value_ok` and `binding_ok`
  * `verdict`: `correct`, `right_number_wrong_account`, `wrong_value` or `no_ground_truth`
  * `failed_dimensions`: any of concept, period, unit, entity, dimensions
  * `expected_fact` and `nearest_fact`, each with its concept
  * `nearest_reading` and all `matches`
* `values_match` and `half_unit_of_last_digit`: rule R16.

The three proposal walk-throughs are tested in `tests/test_binding_check.py`:
* **(a) Neighbouring line.** LongTermDebtNoncurrent of 4,120 is claimed as total LTD, which is 4,870. The value is found, and the binding fails on concept. The same check is run on real Apple FY2023 data: noncurrent is 95,281m and the total is 105,103m.
* **(b) Unit follows the filing date.** The Q3-2022 value of 1,234,567 was filed in Nov 2022, in thousands. It is read as dollars next to the Q4-2022 value of 1,198,000,000, filed in Feb 2023, in dollars. The value is found, and the binding fails on unit. The quarter-over-quarter change is −2.9 %, not ×1000.
* **(c) Asset offered as the liability.** The COLI cash surrender value of 74.9m is claimed as the plan liability of 61.2m. The binding fails on concept. The CEO's 402(i) balance of 8.4m offered as the plan liability is also caught.

---

## Fixtures (`tests/fixtures/`): real vs synthetic

All real files come from the `dgunning/edgartools` repository (MIT) at commit `e46440ef` (22 Sep 2026). They were taken from its bundled data and its VCR cassettes, which are recorded sec.gov responses.

| File | Real / synthetic | Content |
|---|---|---|
| `13f/real/0001140361-23-013281.txt` | **Real** | MetLife Inc 13F-HR (Combination Report), period 2021-12-31, **filed 2023-03-23**. Its values are in dollars because it was filed after the change (R1). |
| `13f/real/0001619124-21-000002.txt` | **Real** | ACTIAM N.V. 13F-HR, period 2020-12-31, filed 2021-02-12. Values are in thousands. |
| `13f/real/0001790837-23-000002.txt` | **Real** | Bull Street Advisors 13F-HR, period 2022-12-31, filed 2023-02-28. **The filer still reported thousands**, and R3 flags it. |
| `13f/real/0001894188-23-000007.txt` | **Real** | LTS One Management 13F-HR, period 2023-09-30, filed 2023-11-14. Values are in dollars. |
| `13f/real/0001039565-22-000009.{primary_doc,infotable}.xml` | **Real** | Kahn Brothers Q1-2022, filed 2022-05-02. **The filer reported dollars before the change**, and R3 flags it. The summary total is off by $1. The filing date comes from the edgartools fixture README. |
| `13f/sec_samples/*.xml` | SEC official samples (fictitious data) | Sample 13F-HR/A (RESTATEMENT, base64 information table), 13F-NT and information table |
| `13f/xsd/*.xsd` | SEC official | EDGAR Form 13F XML schemas. Every 13F fixture validates against them. |
| `13f/synthetic/*.txt` (+ `make_synthetic.py`) | **Synthetic** | Walk-through 2 scenario with managers A, B and C, invented CIKs (`00090000xx`) and invented CUSIPs (`99999…`). Contents: Q3-2022 original (thousands); Q3 NEW HOLDINGS amendment filed 2023 (dollars); Q4-2022 original and RESTATEMENT; B's duplicate shared row; C's 13F-NT. The files follow the XSDs. |
| `companyfacts/CIK0000320193_subset.json` | **Real** (concept subset) | Apple companyfacts for LongTermDebt, Noncurrent, Current, Cash, diluted EPS and NetIncomeLoss. Values are unmodified. |
| `companyfacts/CIK0001045810_subset.json` | **Real** (concept subset) | NVIDIA companyfacts for DeferredCompensationLiabilityClassifiedNoncurrent and Cash |
| `nqdc/cabot_def14a_fy2023_nqdc_table.html` | **Real** | Item 402(i) table cut unmodified from Cabot Corp's DEF 14A for fiscal 2023 (`data/cabot.DEF14A.ixbrl.html` in edgartools). It stacks two plans per NEO. |
| `nqdc/synthetic_proxy_fy202{2,3}_nqdc.html` | **Synthetic** | Single-plan layout with `$` cells, negative earnings and a Total row. One NEO is deliberately inconsistent for the roll-forward test. |
| Walk-through (a) and (c) amounts in `test_binding_check.py` | **Synthetic** | The illustrative numbers from the proposal (4,120/4,870/750; 61.2m/74.9m/8.4m) |

---

## Open issues

1. **edgartools uses a different unit rule from ours.** In edgartools 5.58, `edgar/thirteenf/units.py` decides the unit per filing:
   * first from implied share prices
   * then from `schemaVersion >= X0202`
   * finally from a **report-period** cutoff (≤ 2022-09-30 means thousands)

   It never uses the filing date. Holdings values from edgartools therefore **must not** serve as ground truth without re-deriving them with R1 from `original_value` and the filing date. Its heuristic also corrects filer deviations silently, where ours flags them.
2. **Sources** (checked 2026-10-03 against the SEC Form 13F FAQ and form): R1 rests on Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03 (amendments of earlier quarters filed on or after 2023-01-03 included); R4 on Form 13F Special Instruction 3, FAQ 58b/58c; R5 on Form 13F Special Instructions 7 and 11.b.vii and FAQ 37/48.
3. **R3 only flags.** The thresholds (50 % below $1; median above $10k) come from two real counter-examples. How to adjudicate a flagged filing (override or keep) needs an annotation-guide entry.
4. **R7 de-duplication is a heuristic** based on exact equality of amount and value. Shared positions that were split differently across members will not be de-duplicated. The group table and the dropped-row log should be reviewed by a person.
5. **The co-filing group table is empty for the real pilot managers.** It has to be curated once the 2 pilot managers are chosen.
6. **The NQDC company-level record has no real fixture yet.** Among the real companyfacts available here, only NVIDIA carries a deferred-compensation concept (noncurrent only), and none carries `CashSurrenderValueOfLifeInsurance`. The liability, funding and plan versions for the curated subset must come from the 10-K note and the exhibits.
7. **The 402(i) parser has been tested on two layouts only** (Cabot's stacked multi-plan rows, and a simple synthetic layout). Other proxies may put the plan in its own column, which `COLUMN_RULES` supports but no test covers. Tables split across pages, or with footnote rows inside the body, may need `table_index` or manual review.
8. **The binding check searches every fact of the entity.** A small number, or a number that recurs often, may match an unrelated fact by coincidence. For this reason zeros are matched only when the claim is exactly zero. The false-match rate should be measured before relying on this check.
9. **`derive_q4` assumes a 52/53-week year** (FY 350–380 days, 9M 260–290 days). It does not handle fiscal-year changes or transition periods.
