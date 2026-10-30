# Step 1 · 13F candidate managers

Status (2026-10-02): all 111 managers have been read from EDGAR; `13f_candidates.csv` has one row per manager with all fields and evidence links.
The time window is report periods 2022Q3–2026Q2, i.e. 2022-09-30 to 2026-06-30, 16 quarters in total.
Data cutoff 2026-10-02: amendments filed after that are not in the table.

The 30 and 20 managers below were picked mechanically by the scoring rules and are suggestions; the final list is `13f_selected.csv`.

## Reproduction and download volume

```bash
SEC_USER_AGENT='<approved UA>' python -m step1.thirteen_f_candidates --discover
SEC_USER_AGENT='<approved UA>' python -m step1.thirteen_f_candidates
python -m step1.thirteen_f_candidates --offline      # everything is already cached, a rerun sends no requests
```

- **New downloads 145.4 MB**, within the 300 MB budget:
  - www.sec.gov/Archives 74.6 MB (cover pages primary_doc, index.json, information tables)
  - data.sec.gov/submissions 69.3 MB, mostly the banks' filing histories (JPMorgan alone needed 29 old pages)
  - efts 1.4 MB
- The roughly 130 MB of companyfacts in data/raw are not counted.
- **Request pacing**: at most 5 per second. 4 threads share one throttle with a 0.2-second interval. `_request()` now also calls `edgar_fetch.throttle()`, sharing the machine-wide interval with other processes.
- **Limits**:
  - When an information table exceeds 500 KB, only the first 500 KB are fetched (HTTP Range), about 700 rows, and `q3_partial` / `q4_partial` in the CSV are True.
  - Old submissions pages are read up to 100,000 entries per manager; none exceeded that this time.

## Method

1. **Seeds**:
   - First, 94 managers were listed by hand by type (the type classification in the table below).
   - Then EDGAR full-text search was run over 13F-HR/As filed 2022-10 to 2026-09 for "NEW HOLDINGS" (1,736 documents) and "confidential treatment" (469), counting only amendments whose report period is in the window, and 17 managers with many amendments were added.
   - 111 in total.
   - 5 originally had no CIK and were found by full-text search: Harvard Management 1082621, Temasek 1021944, CPPIB 1283718, OTPP 937567, NY Common 810265.
   - The EDGAR names of all 111 CIKs match the intended managers (the CSV's `name` and `label` columns are side by side).
2. **Filing history**: read the submissions JSON, only the pages overlapping the window, taking 13F-HR, 13F-HR/A and 13F-NT whose report period is in the window.
3. **Cover and summary pages**: `primary_doc.xml` was read for every filing, 2,373 in the window. Fields read:
   - `amendmentType`: RESTATEMENT replaces, NEW HOLDINGS appends (Form 13F Special Instruction 3, FAQ 58b/58c)
   - Confidential treatment: `isConfidentialOmitted`, `confDeniedExpired`, `reasonForNonConfidentiality`
   - `reportType`: HOLDINGS / COMBINATION / NOTICE
   - `otherManagers2Info` (other included managers) and `otherManagersInfo` (other managers reporting for this manager)
   - `tableEntryTotal`, `tableValueTotal`
4. **Units**: covers the 2022Q3 and 2022Q4 original filings, plus all amendments for those two report periods.
   - After reading the information table, `data_layer.thirteen_f` infers the unit from the implied per-share price and compares it with the rule unit set by filing date (Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03).
   - **Strong flag**: the R3 diagnostic `unit_diagnostics` considers it suspect, and the majority-of-rows reading also contradicts the rule.
   - **Weak flag**: only one of the two signals contradicts the rule.
     - Warrants below $1 and SPAC units around $10 bias the row-level majority vote and R3's median check respectively, so one signal is not enough.
   - Flag only, no changes.
5. **Co-filing cross-references**: the other included managers lists are compared among the candidates, with 13F file numbers normalised first (028-04545 recorded as 28-4545).

## Scoring rules

Each component corresponds to one kind of question; the total is the sum of the components.

| Component | Question type | Scoring |
|---|---|---|
| `s_amend` 0–4 | Amendments (restatement replaces vs new holdings appends) | +2 for a RESTATEMENT in the window, +2 for a NEW HOLDINGS |
| `s_cofile` 0–3 | Co-filing / scope (filer total vs one manager's rows) | Maximum number of other included managers: 0 gives 0, 1–2 gives 1, 3–9 gives 2, ≥10 gives 3; additionally +1 for having a 13F-NT, having someone report for it, or being listed as an other manager by another candidate; capped at 3 |
| `s_unit` 0–3 | Unit follows filing date | +1 if the 2022Q3 original was filed before 2023-01-03 and the 2022Q4 after; +1 if an amendment for a 2022 report period was filed on or after 2023-01-03; +1 for a strong flag |
| `s_shares` 0–2 | Shares, position counts | +1 for PUT/CALL rows in the 2022Q4 table; +1 for one CUSIP spanning several rows (rows > 1.2 × CUSIPs) |
| `s_coverage` −1–1 | First holding, quarter-over-quarter | +1 if all 16 quarters have an original filing; 12–15 gives 0; fewer than 12 gives −1 and excludes it from the suggestions |
| `s_size` −2–1 | Size and cost of building gold | +1 for 10–2000 rows in the latest period; fewer than 10 rows or 2001–10000 rows gives 0; 10001–30000 rows −1; more than 30000 rows −2 |

**How the suggested 30 are picked**:
- First by type quota; within each type by score from high to low, ties broken by fewer rows first.
- The pilot's Pershing and Berkshire are fixed picks.
- Quotas: activist 7, other hedge 3, multi-manager/quant 4, insurance/conglomerate 4, bank 4, asset manager 4, endowment/sovereign 3, small adviser 1.

**How the boundary 20 are picked**:
- These 20 only use the two periods 2022Q3/Q4 and are picked from outside the 30.
- First those with a strong flag; then those with an amendment for a 2022 report period filed on or after 2023-01-03, a late 2022Q3 filing, or only a weak flag; the remaining slots go to a clean control group.
- Within each group, smaller 2022Q4 tables first.
- The function is `boundary_pick`; the result is the CSV's `boundary` column with value Y.

**What these rules favour**: the amendment component is worth up to 4 points, so the suggestion list favours event-driven funds with many amendments. 12 of the 30 were found by full-text search. If fame or size diversity matters more, managers like Renaissance, Citadel, Vanguard or Morgan Stanley can be swapped in. The cost is heavier gold: their latest periods all exceed 3,000 rows, and Morgan Stanley has 45,905 rows.

## Overall numbers (111 managers)

- **Amendments**:
  - 68 managers have 13F-HR/As in the window, 631 in total: 163 RESTATEMENT, 468 NEW HOLDINGS, all types read.
  - 50 have RESTATEMENT, 42 have NEW HOLDINGS, 24 have both.
- **Confidential treatment**:
  - 19 have filings with `isConfidentialOmitted` ticked on the cover.
  - 14 have NEW HOLDINGS filed because confidential treatment expired or was denied.
- **Co-filing**:
  - Maximum number of other included managers: 0 for 40 managers, 1–2 for 25, 3–9 for 22, ≥10 for 24.
  - 6 have 13F-NTs: Pershing, Jana, Vanguard, Barclays (41), Temasek, MetLife.
- **Coverage**: 13 do not cover all 16 quarters, mostly because the filing entity changed; see below.
- **Units**:
  - 34 filings with strong flags across 25 managers; 4 with weak flags.
  - 102 amendments for 2022 report periods filed on or after 2023-01-03: 78 in dollars, consistent with the rule; 16 still in thousands of dollars; 8 undeterminable.

## Suggested 30 (suggestion)

| Type | Manager (CIK) | Score | Reason |
|---|---|---|---|
| Activist | Elliott Investment Management (1791786) | 10 | 2 RESTATEMENTs (the 2022Q4 one filed 2023-05), 1 NEW HOLDINGS (confidential treatment expired); 14 option rows in 2022Q4; 29 rows |
| Activist | Biglari Capital (1334429) | 9 | 3 other included managers; the 2022Q3 NEW HOLDINGS filed 2023-02-10, in dollars; 19 rows |
| Activist | Carl Icahn (921669) | 8 | 7–10 other managers; 15 with confidential treatment ticked; **2022Q4 still in thousands of dollars (strong flag)**; 18 rows |
| Activist | Starboard Value (1517137) | 8 | 5 other managers; 1 NEW HOLDINGS; has options; 22 rows |
| Activist | Trian (1345471) | 8 | Two RESTATEMENTs for 2022Q4 filed on the same day, one in thousands of dollars, one in dollars; 2022Q4 original in thousands of dollars (strong flag); 9 rows |
| Activist | ValueAct (1418814) | 7 | All 16 periods are COMBINATION REPORTs (ValueAct Capital Management reports part); 1 NEW HOLDINGS |
| Activist | Pershing Square (1336528) | 5 | Pilot M1, fixed pick; 1 NEW HOLDINGS in 2024Q4; **2026Q2 is a 13F-NT, reported by Pershing Square Inc.** |
| Other hedge | Taconic Capital (1390113) | 11 | 65 amendments (63 NEW HOLDINGS, confidential treatment expired); the 5 2022Q3 NEW HOLDINGS filed 2023–2025 are **still in thousands of dollars**; 25 rows |
| Other hedge | Weiss Asset Management (1357550) | 11 | 2 RESTATEMENTs, 14 NEW HOLDINGS; the 2022Q4 NEW HOLDINGS (filed 2023-11) in thousands of dollars; 624 rows |
| Other hedge | Farallon (909661) | 10 | 47 amendments (45 NEW HOLDINGS); amendments for 2022 report periods all in dollars, consistent with the rule; 89 rows |
| Multi-manager/quant | HBK Investments (1011443) | 10 | 26 amendments; 169 option rows in 2022Q4; 276 rows |
| Multi-manager/quant | Jump Financial (1831577) | 10 | 8 RESTATEMENTs, 10 NEW HOLDINGS; 4–7 other managers; 2022Q4 rows / CUSIPs about 2.3; 3,795 rows |
| Multi-manager/quant | O'Connor (UBS AM) (1132716) | 9 | 53 amendments; **2022Q3 original apparently already in dollars (weak flag, many SPACs)**; no longer files under this entity after 2025Q4 |
| Multi-manager/quant | Point72 (1603466) | 8 | 4 RESTATEMENTs; 12 COMBINATION periods (Cubist etc. report part); 254 option rows |
| Insurance/conglomerate | Brookfield Corp (1001085) | 12 | 3 RESTATEMENTs, 9 NEW HOLDINGS; 6–8 other managers; Oaktree lists it as an other manager, and its COMBINATION is reported by Oaktree |
| Insurance/conglomerate | Berkshire Hathaway (1067983) | 11 | Pilot M2, fixed pick; 14–15 other managers; 1 RESTATEMENT, 3 NEW HOLDINGS (the 2025Q1 one filed 2025-08-14 is also NEW HOLDINGS) |
| Insurance/conglomerate | Prudential Financial (1137774) | 9 | 5–9 other managers; 16 COMBINATION periods; 2 NEW HOLDINGS for 2022 report periods filed 2024-06, in dollars; 3,685 rows |
| Insurance/conglomerate | MetLife Investment Management (1529735) | 8 | 11 RESTATEMENTs, 8 NEW HOLDINGS; part of the MetLife group (lists MetLife Inc as an other manager); 2,884 rows |
| Bank | Nomura (1163653) | 13 | 4 RESTATEMENTs, 1 NEW HOLDINGS; the 2022Q4 original and its restatement both in thousands of dollars (strong flag); 2,655 rows |
| Bank | Goldman Sachs Group (886982) | 12 | **In 2024-05 restated 6 periods 2022Q3–2023Q4 at once**: the 2022Q3 one in thousands of dollars, the 2022Q4 one on the same day in dollars; 14,070 rows |
| Bank | Jefferies Financial Group (96223) | 11 | 3 RESTATEMENTs; 0–6 other managers; 234 option rows; 1,621 rows |
| Bank | Barclays (312069) | 10 | **41 13F-NTs**; 16 RESTATEMENTs, 12 of them filed together in 2026-03 restating 2022Q4–2025Q3; 2022Q4 in thousands of dollars; 11,383 rows |
| Asset manager | Blackstone (1393818) | 13 | 1 RESTATEMENT, 5 NEW HOLDINGS (confidential); 8–10 other managers; the 2022Q3 RESTATEMENT filed 2023-02-10 still in thousands of dollars; 557 rows |
| Asset manager | Ameriprise Financial (820027) | 9 | 12–13 other managers; 16 COMBINATION periods; 2 RESTATEMENTs, 1 NEW HOLDINGS; 11,508 rows, on the heavy side |
| Asset manager | T. Rowe Price Associates (80255) | 9 | 5–6 other managers; COMBINATION (Oak Hill etc.); 2022Q4 in thousands of dollars (strong flag); 4,722 rows |
| Asset manager | Troy Asset Management (1601407) | 9 | 9 RESTATEMENTs, 21 NEW HOLDINGS; the 2022Q4 original filed 2023-01-20, still in thousands of dollars; 29 rows |
| Endowment/sovereign | Temasek Holdings (1021944) | 12 | **26–39 other managers**; two restatements of 2022Q3, the 2023-04 one in thousands of dollars, the 2023-05 one in dollars; 1 13F-NT; 196 rows |
| Endowment/sovereign | Provida Pension Fund Administrator (931588) | 9 | 22 amendments (19 NEW HOLDINGS); part of the MetLife group; the latest period and 2022Q4 have only 1 row each (the 2022Q4 row is an option), narrow question scope |
| Endowment/sovereign | Ontario Teachers' Pension Plan Board (937567) | 8 | 9 RESTATEMENTs, 1 NEW HOLDINGS; 60 rows |
| Small adviser | Kahn Brothers (1039565) | 6 | **2022Q3 (filed 2022-11-15) already in dollars** (strong flag, median implied price $24,830); 1 RESTATEMENT; 48 rows |

One-line version: Elliott, Biglari, Icahn, Starboard, Trian, ValueAct, Pershing, Taconic, Weiss, Farallon, HBK, Jump, O'Connor, Point72, Brookfield, Berkshire, Prudential, MetLife IM, Nomura, Goldman, Jefferies, Barclays, Blackstone, Ameriprise, T. Rowe Price, Troy, Temasek, Provida, OTPP, Kahn Brothers.

## Suggested boundary 20 (2022Q3/Q4 only, suggestion)

- **12 with strong flags**:

  | Manager | Situation | 2022Q3 / 2022Q4 rows |
  |---|---|---|
  | Himalaya | 2022Q4 still in thousands of dollars | 6 / 6 |
  | Baupost | 2022Q4 still in thousands of dollars | 45 / 34 |
  | Bull Street | 2022Q4 still in thousands of dollars | 89 / 90 |
  | Oaktree | 2022Q4 original in thousands of dollars, the 2023-04 restatement switched to dollars | 380 / 345 |
  | Davidson Kempner | 2022Q4 in thousands of dollars; of the 2022Q3 NEW HOLDINGS, the two filed 2023-02 are in thousands of dollars, later ones in dollars | 539 / 433 |
  | Whitebox | The 2022Q3/Q4 NEW HOLDINGS filed 2026-08-26 still in thousands of dollars; the 2022Q4 original has a weak flag | 949 / 771 |
  | NY Common | 2022Q4 filed 2023-01-30, still in thousands of dollars | 3,257 / 3,236 |
  | Dimensional | 2022Q4 still in thousands of dollars | 3,455 / 3,457 |
  | Renaissance | 2022Q4 still in thousands of dollars | 3,976 / 4,073 |
  | Geode | 2022Q4 still in thousands of dollars | 5,029 / 4,898 |
  | Millennium | 2022Q4 still in thousands of dollars | 7,016 / 6,550 |
  | JPMorgan | 2022Q4 still in thousands of dollars; the two periods' tables are about 40 MB, can be dropped if too heavy | 24,311 / 26,794 |

- **4 straddling the boundary**:
  - MetLife: for 2022Q3 it first filed a 13F-NT in 2022-11, then filed a 13F-HR on 2023-03-23; by rule it is in dollars, and the evidence is also dollars.
  - Norges Bank: the 2022Q3 NEW HOLDINGS filed 2023-11, in dollars.
  - State Street: 2022Q4 has **two original 13F-HRs** (2023-02-14 and 2023-05-15, identical content), plus a RESTATEMENT filed the same day.
  - Yale: the 2022Q3/Q4 NEW HOLDINGS filed 2023-03 have only one or two rows; the unit cannot be determined.
- **4 controls**: Cevian (1 row), Dalal Street (5 rows), Engaged Capital (9 rows), Scion (9 rows). For these, rule and evidence agree and the tables are small.

## Filings that violate the filing-date unit rule

- **2022Q4 originals still in thousands of dollars (strong flag, 17 managers, 16% of 105 filings)**:
  - Barclays, Baupost, Bull Street, Icahn, Davidson Kempner, Dimensional, Geode, Himalaya, JPMorgan, Millennium, NY Common, Nomura, Oaktree, Renaissance, T. Rowe Price, Trian, Troy.
  - These filings were all filed after 2023-01-03 and use the new X0202 schema, but 97–100% of rows read in dollars give per-share prices below $1.
  - Two were spot-checked:
    - JPMorgan 2022Q4: 1-800-Flowers 15,230 shares, value 146, read in thousands of dollars is $9.6 per share.
    - Icahn 2022Q4: Cheniere 1,000,000 shares, value 149,960, read in thousands of dollars is $149.96 per share.
- **2022Q3 originals already in dollars**:
  - Kahn Brothers: strong flag, median price $24,830.
  - O'Connor: weak flag. 401 rows read in dollars, 105 rows in thousands of dollars; R3's median $9,970 is just below the $10,000 threshold. The table is mostly SPACs around $10, which suggests it is already in dollars.
- **Amendments for 2022 report periods filed on or after 2023-01-03**:
  - 78 in dollars, 16 still in thousands of dollars.
  - The latter include: Taconic 2022Q3 (5), Davidson Kempner 2022Q3 (2), Whitebox (2, filed in 2026), Goldman 2022Q3, Blackstone 2022Q3, Temasek 2022Q3 (the 2023-04 one), Trian 2022Q4, Nomura 2022Q4, O'Connor 2022Q4, Weiss 2022Q4.
  - Pattern: many managers amending an old quarter keep that period's unit, i.e. by report period rather than filing date. Goldman's 2022Q3 and 2022Q4 restatements filed on the same day are one in thousands of dollars and one in dollars.
- **Impact**:
  - Gold for 2022Q4 "unit" questions cannot be built from the rule alone. Both the rule unit and the evidence unit are stored, and mismatches are adjudicated by a human filing by filing.
  - If the "unit follows filing date" family is meant to test only the rule itself, these managers must be avoided, or made into separate "filer violation" questions.

## Other question material (entity changes, co-filing)

- **Entity changes**:
  - Jana: JANA Partners LLC up to 2023Q2; in 2023Q3 it filed a 13F-NT, reported by the new entity JANA Partners Management, LP (1998597), which lists the old entity as an other manager.
  - BlackRock: the old holding company (1364742) reports up to 2024Q2, the new holding company (2012383) from 2024Q3, 8 periods each, with the latest periods about 48–50 thousand rows.
  - Vanguard: 2026Q1 and 2026Q2 are 13F-NTs, reported by Vanguard Capital Management, Vanguard Portfolio Management, etc.
  - O'Connor: no longer files under this entity after 2025Q4.
  - Alleghany: only 2022Q3, then merged into Berkshire.
  - Others with incomplete coverage: Greenlight has only the 6 periods 2022Q3–2023Q4; Oxford starts in 2023Q4; Raymond James starts in 2024Q4.
- **MetLife group**:
  - MetLife Inc's 2022Q3: first an NT, then an HR.
  - Provida and MetLife IM list MetLife Inc as an other manager; MetLife's NT cover lists Metropolitan Life, Provida, MetLife IM.
  - Can be turned directly into a co-filing group table.
- **Brookfield / Oaktree**: Oaktree lists Brookfield as an other included manager, and Brookfield's COMBINATION is in turn reported by Oaktree.
- **Notes for data_layer**:
  - State Street 2022Q4 has two original 13F-HRs; `merge_all` will report "expected one original filing".
  - Bank of New York Mellon's 2022 information tables have `xmlns:xsi=" http://..."` with an extra leading space; strict lxml parsing fails, so this manager's unit check was not done.
  - R3's median threshold misses tables with many SPACs that are already filed in dollars (O'Connor 2022Q3).

## Still unknown

1. **Amendments of earlier quarters**: Form 13F FAQ 62 says every report filed on or after 2023-01-03, including amendments for earlier quarters, must use the updated form (dollars; FAQ 36). Checked 2026-10-03. The data show that 16% of 2022Q4 originals and 16 boundary-straddling amendments did not follow the filing date.
2. **4 weak flags**: O'Connor 2022Q3, Whitebox 2022Q4 original, Weiss 2022Q3 NEW HOLDINGS, Jump 2022Q4 NEW HOLDINGS.
3. **Large tables were only checked for the first ~700 rows**: 43 managers in 2022Q3 and 44 in 2022Q4 (`partial` column True). Whether later rows mix units is unknown.
4. **Whether anyone kept filing in thousands of dollars after 2023Q1** (e.g. Bull Street's later periods) was not checked this time.
5. **"Appears in someone else's other managers list" was only compared among the 111**; who filers outside the candidate list name was not checked.
6. **The scoring thresholds are mine** (1.2×, 2000 rows, 12 quarters, etc.), only to make the ranking transparent; they have not been validated.
7. **Data cutoff 2026-10-02**: e.g. State Street's 2026Q2 restatement was only filed on 2026-09-22, and more amendments may follow.
