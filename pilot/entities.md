# Pilot entities

Status legend: **[V]** verified on a public page (URL given) ·
**[V-2nd]** verified only through a secondary aggregator (13f.info, stocktitan, justia) ·
**[U]** unverified: the data layer must confirm it from EDGAR before the corpus freezes.
Checked on 2026-09-24.
**Update (2026-09-24, checked against EDGAR):** every [U] item was checked against EDGAR directly. Resolved items are marked **[V-EDGAR]** with the EDGAR URL; the full list is in "EDGAR verification log" at the end. Raw files are cached under `data/raw/` (git-ignored).

| # | Role | Entity | CIK | Ticker | Why it is in the pilot |
|---|---|---|---|---|---|
| C1 | Company | Target Corporation | 27419 | TGT | Debt note separates total / current / noncurrent. NQDC table in DEF 14A with all five 402(i) columns. COLI **and** prepaid forward contracts sit against an unfunded NQDC plan. **Two dated plan statements (2017, 2025)** of the officer EDCP are public. Fiscal year ends in late January, which gives a free fiscal-label trap |
| C2 | Company | Comcast Corporation | 1166691 | CMCSA | Calendar year, so discrete Q4 = FY − 9M is clean. Balance sheet shows current and noncurrent debt separately. Its NQDC table **has no registrant-contribution column**, a built-in missing-slot test. 10-K note gives the plan-wide obligation and COLI cash surrender value. The 2005 Deferred Compensation Plan was **amended and restated effective 31 Dec 2025**, with a 12 % / 9 % split in the crediting rate |
| M1 | 13F manager (boundary, clean) | Pershing Square Capital Management, L.P. | 1336528 | — | 6 holdings, concentrated. Q3-2022 filed 14 Nov 2022 (thousands) and Q4-2022 filed 13 Feb 2023 (dollars). No co-filers and no amendments for these quarters, so it is the clean control for the unit rule |
| M2 | 13F manager (amendment + co-filing) | Berkshire Hathaway Inc | 1067983 | BRK | Files for itself plus **14–15 other included managers**, so one issuer appears on several rows. Q3-2023 has an **original, a restatement two days later, and a new-holdings amendment (Chubb, confidential treatment) six months later**. Also straddles the 3 Jan 2023 boundary |

---

## C1. Target Corporation (CIK 27419, TGT)

**Filings**
- 10-K for fiscal 2025 (FYE **31 Jan 2026**): filed **11 Mar 2026**, accession **0000027419-26-000016** [V-2nd] https://www.stocktitan.net/sec-filings/TGT/10-k-target-corp-files-annual-report-71d773ec884b.html. The fiscal year end of 31 Jan 2026 and the Commission file no. 1-6049 are [V] from the cover page: https://corporate.target.com/investors/annual/2025-annual-report/10-k-report/10-k-cover. (stocktitan's DEF 14A page says FY ended 1 Feb 2026, which conflicts with the 10-K cover. Use the 10-K.)
- DEF 14A 2026: filed **27 Apr 2026**, meeting 10 Jun 2026. It contains the "Nonqualified Deferred Compensation" table with columns Executive Contributions / Registrant Contributions / Aggregate Earnings / Withdrawals/Distributions / Aggregate Balance at Last FYE [V-2nd] https://www.stocktitan.net/sec-filings/TGT/def-14a-target-corp-definitive-proxy-statement-12d9f85ae32e.html. CEO transition: Fiddelke became CEO 1 Feb 2026 and Cornell became Executive Chair (same source). Accession **0001628280-26-027508** [V-EDGAR] https://www.sec.gov/Archives/edgar/data/27419/000162828026027508/tgt-20260426.htm

**(a) Debt: total, current and noncurrent reported separately** [V] https://corporate.target.com/investors/annual/2025-annual-report/10-k-report/10-k-part-ii/item-8-financial-statements-and-supplementary-data

| $ millions | 31 Jan 2026 | 1 Feb 2025 |
|---|---|---|
| Current portion of long-term debt and other borrowings | 2,130 | 1,636 |
| Long-term debt and other borrowings (noncurrent) | 14,326 | 14,304 |
| Sum | 16,456 | 15,940 |

The debt note (Note 17 "Commercial Paper and Long-Term Debt") also lists "Total notes and debentures" as a separate subtotal above the lease and other lines. The fiscal-2019 extract shows notes and debentures of 9,992, current portion (161) and noncurrent 11,338 [V] https://samples-breakingintowallstreet-com.s3.amazonaws.com/Mastery/06-Target-10-K-Key-Extracts.pdf. In that year the three lines do not add up by construction, because leases and other borrowings sit in between. That makes it a strong **neighbouring-line** trap. FY2025 Note 17 [V-EDGAR] https://www.sec.gov/Archives/edgar/data/27419/000002741926000016/tgt-20260131.htm: total notes and debentures **14,398**, swap valuation adjustments (55), finance lease liabilities 2,113, less amounts due within one year (2,130), long-term debt and other borrowings **14,326**. No total including the current portion is printed; 14,398 − 55 + 2,113 = **16,456** (derived). In structured filing data Target tags **14,398 as `us-gaap:LongTermDebt`**, so the concept named "long-term debt" is the notes-and-debentures subtotal, not the total the question asks for.

**(b) NQDC table (Item 402(i))**: present in the 2026 DEF 14A with all five columns [V-EDGAR] https://www.sec.gov/Archives/edgar/data/27419/000162828026027508/tgt-20260426.htm. Cornell: 132,497 / 168,686 / 688,314 / 0 / **8,005,585**; Fiddelke: 181,980 / 118,035 / 243,043 / 0 / 2,387,966 (columns b / c / d / e / f, $).

**(c) Funding disclosure**
- "Company-owned life insurance investments, net of loans": **$600 M** (31 Jan 2026) and $540 M (1 Feb 2025), in other noncurrent assets [V] (Item 8 page above, Note 13 table).
- "Prepaid forward contracts" appear in the Note 8 fair-value table [V], same page. Fair value **$18 M** (31 Jan 2026) and $23 M (1 Feb 2025), Other Current Assets, Level 1 [V-EDGAR] https://www.sec.gov/Archives/edgar/data/27419/000002741926000016/tgt-20260131.htm (Note 8).
- Wording (fiscal 2019 10-K): "We also maintain an unfunded, nonqualified deferred compensation plan for a broad management group" and "We mitigate our risk of offering the nonqualified plans through investing in company-owned life insurance and prepaid forward contracts" [V] https://samples-breakingintowallstreet-com.s3.amazonaws.com/Mastery/06-Target-10-K-Key-Extracts.pdf. FY2025 Note 24 [V-EDGAR] (same 10-K): "Our total liability under these plans was $**717** million and $684 million as of January 31, 2026, and February 1, 2025"; the risk is mitigated "through investing in company-owned life insurance and prepaid forward contracts", which "are general corporate assets". Structured data tags only `DeferredCompensationLiabilityClassifiedNoncurrent` = **650** for FY2025, the noncurrent part, not the 717 total.
- Nonqualified plan expense table (FY2024 10-K): benefits expense 90 / 59 / (15) and related investment (income)/expense (62) / (43) / 40 for 2024 / 2023 / 2022, net 28 / 16 / 25 ($ M) [V] https://www.sec.gov/Archives/edgar/data/27419/000002741925000018/R31.htm. The offsetting investment line is the accounting footprint of the hedge.
- **Rabbi trust**: the plan documents name the "Target Corporation Deferred Compensation Trust Agreement, dated January 1, 2009" (Sec. 7.3). The trustee pays out from it on a change in control [V-2nd]. It is not a funded trust holding the COLI, so do not describe it as one in gold answers.

**Plan document versions (stale-version trap)**
- Officer EDCP **2017** plan statement: effective 1 Jan 2017, amended and restated 1 May 2017. It lists specific crediting-rate alternatives (Stable Value until 5 Jun 2012, then Intermediate-Term Bond, Company Stock Fund, Money Market) [V-2nd] https://contracts.justia.com/companies/target-1261/contract/332048 (SEC original: exhibit 10.I to the FY2016 10-K, `tgt-20170128xexhibit10i.htm` [V] https://www.sec.gov/Archives/edgar/data/27419/000002741917000008/tgt-20170128xexhibit10i.htm).
- Officer EDCP **2025** plan statement: effective 1 Jan 2025, filed as Ex. 10.9. Crediting alternatives "mirror investment funds available under the Target 401(k) Plan". Includes an "Enhancement" credit and a 2025 clawback amendment. Deferrals up to 80 % of salary and bonus [V-2nd] https://contracts.justia.com/companies/target-1261/contract/1318304. Ex. 10.9 to the FY2024 10-K (acc. 0000027419-25-000018, filed 12 Mar 2025) [V-EDGAR] https://www.sec.gov/Archives/edgar/data/27419/000002741925000018/tgt-20250201xexhibit109.htm, incorporated by reference as Ex. 10.9 of the FY2025 10-K. Its definition of Crediting Rate Alternative adds "Reference … to a specific Crediting Rate Alternative is a reference to the corresponding investment fund available under the Target 401(k) Plan", which the 2017 statement does not have.

**Why Target**: it is the one large issuer found where the funding asset (COLI $600 M) is disclosed next to an unfunded plan, has a hedging instrument (prepaid forwards) that is a second asset-for-liability candidate, and has two dated plan versions. That covers all three NQDC traps.

---

## C2. Comcast Corporation (CIK 1166691, CMCSA)

**Filings**
- 10-K FY2025: filed **3 Feb 2026**, accession **0001628280-26-004994** [V] https://www.cmcsa.com/sec-filings/sec-filing/10-k/0001628280-26-004994
- DEF 14A 2026: filed **24 Apr 2026**, meeting 10 Jun 2026. Contains "Nonqualified Deferred Compensation in and as of Fiscal Year-End". Accession **0001193125-26-177138** [V-EDGAR] https://www.sec.gov/Archives/edgar/data/1166691/000119312526177138/cmcsa-20260424.htm. The five rows below match the EDGAR copy exactly.

**(a) Debt**: balance-sheet lines "Current portion of debt" **$5,958 M** and "Noncurrent portion of debt" **$92,979 M** at 31 Dec 2025. Note 5 carrying value is $98.9 bn at year-end 2025 and $94.6 bn at 31 Mar 2026 [V] (Q1-2026 10-Q) https://www.cmcsa.com/static-files/27845c4d-26b6-427a-8361-09714ef86074. Note that 5,958 + 92,979 = 98,937, which matches the $98.9 bn. This is a clean total.

**(b) NQDC table** (2026 proxy, FY2025) [V] https://materials.proxyvote.com/Approved/20030N/20260408/NPS_632373/PDF/comcast_corp-proxy2026_0051.pdf
- Columns: Executive Contributions in Last FY / Aggregate Earnings in Last FY / Aggregate Withdrawals/Distributions / Aggregate Balance at Last FYE. **There is no Registrant Contributions column.**
- Brian L. Roberts: 17,011,497 / 6,108,352 / — / **168,177,085**
- Michael J. Cavanagh: 9,358,186 / (1,909,423) / (5,150,700) / 43,321,897
- Jason S. Armstrong: — / 1,699,617 / — / 14,379,332
- Thomas J. Reid: — / (319,243) / (501,478) / 1,523,145
- Jennifer Khoury: — / 437,667 / — / 4,306,525
- Narrative: "No amounts represent interest earned in excess of 120% of the long-term applicable federal rate." The company stock fund returned −17.4 % in 2025 (same source).

**(c) Funding disclosure**
- 10-K note "Deferred Compensation Plans" (FY2018 wording): the plans are unfunded, with a benefit obligation of $2,885 M (2018) and interest expense of $222 M. "We have purchased life insurance policies to recover a portion of the future payments", and the cash surrender value was $351 M (2018) vs $820 M (2017) [V] https://materials.proxyvote.com/Approved/20030N/20190405/AR_394903/PDF/comcast-10k2018_0101.pdf. **FY2025** (Note 11) [V-EDGAR] https://www.sec.gov/Archives/edgar/data/1166691/000162828026004994/cmcsa-20251231.htm: benefit obligation **$5,097 M** (2024: 4,812), interest expense $374 M, cash surrender value **$662 M** (2024: 566) in other noncurrent assets. Tagged `us-gaap:DeferredCompensationLiabilityCurrentAndNoncurrent` = 5,097 and `us-gaap:CashSurrenderValueOfLifeInsurance` = 662. Trap: the next paragraph of the same note gives 401(k) plan expense of **$662 M** as well, a second 662 with a different concept.
- Plans: *Comcast Corporation 2005 Deferred Compensation Plan (as amended and restated effective December 31, 2025)*, Ex. 10.5 to the FY2025 10-K. The Income Fund credits **12 %** on "Protected Account Balances" and **9 %** on other deferrals. After termination the rate is the lesser of those and prime + 1 %. The plan is unfunded, participants are general creditors, and no rabbi trust is mentioned [V] https://www.sec.gov/Archives/edgar/data/1166691/000162828026004994/ex105-12312025.htm. *Comcast Select Deferred Compensation Plan (amended and restated effective December 31, 2025)*, Ex. 10.2, uses valuation funds, and the company "may establish one (1) or more trusts" whose assets remain subject to general creditors [V] https://www.sec.gov/Archives/edgar/data/1166691/000162828026004994/ex102-12312025.htm.
- Earlier version of the 2005 plan: *as amended and restated effective May 14, 2024*, Ex. 10.5 to the FY2024 10-K (acc. 0001166691-25-000011) [V-EDGAR] https://www.sec.gov/Archives/edgar/data/1166691/000116669125000011/ex105-12312024.htm. The 12 % / 9 % Applicable Interest Rate is the same in both versions, so CMCSA-4 no longer asks for it. **Stale-version term used instead** [V-EDGAR]: the base-salary deferral limit, Section 2.42.3(a)(i). 2024 version: the maximum base salary available for deferral "shall in no event be **less than** 85%" (a floor); 2025 version: "shall in no event be **more than** 85%" (a cap). Other substantive changes in the 2025 restatement: the $350,000 Annual Rate of Pay test must hold on both the election's filing date and its effective date, and Comcast Spectacor employees are carved out of 2.30.1; a new Regular Deferral Election for signing bonuses; non-New-Key employees can no longer defer a signing bonus by Initial Deferral Election.

**Why Comcast**: calendar-year reporting (a clean discrete Q4), a total that reconciles, and a large plan-wide obligation next to a single named executive's $168 M balance, which makes population mixing easy. The missing registrant-contribution column tests "missing vs zero vs invented". A plan restated on the fiscal year-end date tests plan versions. **Risk**: the Versant cable-network spin-off around the turn of 2026 may restate comparatives (timing [U]). Keep the questions to FY2025 as reported in the 10-K filed 3 Feb 2026.

**Considered and dropped**: Sysco. COLI is historically disclosed, but the FY2026 10-K fair-value and debt pages show no COLI text (https://app.edgar.tools/companies/SYY/disclosures/fair-value), and the next proxy (Oct 2026) postdates the 10-K. Ross Stores has a rabbi-trust plan exhibit, but the 10-K text could not be verified (fetches were cut off). Armstrong World Industries (CIK 7431) has a clean "Cash surrender value of company-owned life insurance policies" $36.1 M / "Investment in employee deferred compensation plans" $14.5 M table (https://www.sec.gov/Archives/edgar/data/7431/000119312526065183/R99.htm). It is a good **reserve** if Target's Note 24 turns out not to state the liability.

---

## M1. Pershing Square Capital Management, L.P. (CIK 1336528)

| Quarter | Form | Accession | Filing date | Unit per form rule | Holdings | Table value (as normalised by 13f.info, $000) |
|---|---|---|---|---|---|---|
| Q3 2022 (30 Sep 2022) | 13F-HR | 0001172661-22-002568 | **14 Nov 2022** (accepted 14 Nov 2022 16:21) | thousands | 6 | 7,877,045 |
| Q4 2022 (31 Dec 2022) | 13F-HR | 0001172661-23-000673 | **13 Feb 2023** (accepted **10 Feb 2023 17:55**) | dollars | 6 | 8,784,004 |

Sources: SEC index pages [V] https://www.sec.gov/Archives/edgar/data/1336528/000117266122002568/0001172661-22-002568-index.html, https://www.sec.gov/Archives/edgar/data/1336528/000117266123000673/0001172661-23-000673-index.html. Counts and values [V-2nd] https://13f.info/manager/0001336528-pershing-square-capital-management-l-p. The SEC index pages list no co-filers.

- Holdings: Q3-2022 top positions LOW, CMG, QSR, HLT, CP, HHC. Q4-2022: LOW, QSR, CMG, HLT, HHC [V-2nd] (13f.info meta descriptions). Share counts and dollar values per row [V-EDGAR] (information tables; see the log below).
- Other included managers: **0** on the Q4-2021 cover page, which also says "confidential information has been omitted … and filed separately" [V] https://www.sec.gov/Archives/edgar/data/1336528/000117266122000776/xslForm13F_X01/primary_doc.xml. Q3/Q4-2022 cover pages [V-EDGAR]: `otherIncludedManagersCount` 0, no confidential-treatment statement, 6 entries each. 13f.info lists no amendments for Q2-2022 to Q2-2023.
- **Filing-date edge case**: the Q4-2022 filing was accepted on Friday 10 Feb 2023 after 17:30, so EDGAR dates it 13 Feb 2023. Both dates fall after 3 Jan 2023, so the unit does not change. Record both dates in the fact store all the same, because the unit rule must key on the **filing date**, not the acceptance timestamp.

**Why Pershing**: few rows, no co-filing and no amendment, so any unit error is an unambiguous binding failure on the unit. It is the control for M2.

---

## M2. Berkshire Hathaway Inc (CIK 1067983)

| Period | Form | Accession | Filing date | Holdings (13f.info, aggregated) | Value ($000, 13f.info) |
|---|---|---|---|---|---|
| Q3 2022 | 13F-HR | 0000950123-22-012275 | 14 Nov 2022 | 49 | 296,096,640 |
| Q4 2022 | 13F-HR | 0000950123-23-002585 | **14 Feb 2023** (accepted 16:00:08) [V] | 49 | 299,007,622 |
| Q1 2023 | 13F-HR | 0000950123-23-005270 | 15 May 2023 | 48 | 325,108,752 |
| Q3 2023 | 13F-HR (original) | 0000950123-23-010898 | 14 Nov 2023 | 45 | 313.3 bn |
| Q3 2023 | **13F-HR/A (RESTATEMENT)** | 0000950123-23-011029 | **16 Nov 2023** | 45 | 313,257,308 |
| Q3 2023 | **13F-HR/A (NEW HOLDINGS)** | 0000950123-24-005653 | **15 May 2024** [V] | 1 | 1,695,320 |
| Q4 2023 | 13F-HR | 0000950123-24-002518 | 14 Feb 2024 | 41 | 347,358,074 |
| Q4 2023 | 13F-HR/A (NEW HOLDINGS) | 0000950123-24-005664 | 15 May 2024 | 1 | 4,542,600 |

Sources: [V-2nd] https://13f.info/manager/0001067983-berkshire-hathaway-inc, https://13f.info/13f/000095012323010898-berkshire-hathaway-inc-q3-2023 ("This filing has been restated"), https://13f.info/13f/000095012323011029-berkshire-hathaway-inc-q3-2023-restatement. SEC index pages [V] for the Q4-2022 filing https://www.sec.gov/Archives/edgar/data/1067983/0000950123-23-002585-index.html and the Q3-2023 new-holdings amendment https://sec.gov/Archives/edgar/data/1067983/0000950123-24-005653-index.html.
- The new-holdings amendments of 15 May 2024 are widely reported as the confidential Chubb stake. [V-EDGAR] The Q3-2023 new-holdings row is **CHUBB LIMITED, CUSIP H1467J104, 8,143,530 sh, $1,695,320,075** (other managers 4 = Buffett Warren E, 11 = National Indemnity) https://www.sec.gov/Archives/edgar/data/1067983/000095012324005653/0000950123-24-005653.txt; the Q4-2023 one is Chubb, 20,100,000 sh, $4,542,600,000 https://www.sec.gov/Archives/edgar/data/1067983/000095012324005664/0000950123-24-005664.txt. The Q3-2023 original, its restatement and the Q4-2023 original all set `isConfidentialOmitted` = true.
- **Other included managers** (Q1-2023 cover, 15 entries) [V-2nd] https://www.streetinsider.com/SEC+Filings/Form++13F-HR+++++BERKSHIRE+HATHAWAY+INC++++For:+Mar+31/21667577.html: Berkshire Hathaway Homestate Insurance Co.; Berkshire Hathaway Life Insurance Co. of Nebraska; BH Finance LLC; **Buffett Warren E**; Columbia Insurance Co; Cypress Insurance Co; Fechheimer Brothers Co; GEICO Corp; Medical Protective Corp; National Fire & Marine Insurance Co; National Indemnity Co; National Liability & Fire Insurance Co; Nebraska Furniture Mart; U.S. Investment Corp; General Re Corp. The Q1-2023 information table has **165 entries for 48 issuers**, so one issuer spans several rows split by other manager. The Q1-2026 cover lists 14 other included managers [V] https://www.sec.gov/Archives/edgar/data/1067983/000119312526226661/xslForm13F_X02/primary_doc.xml. Q3/Q4-2022 lists [V-EDGAR]: **14** other included managers (the list above without General Re Corp). In Q3-2023 General Re Corp is no. 7 and Fechheimer Brothers is gone (still 14). The Q3/Q4-2022 cover pages also list one *other manager reporting for this manager*: New England Asset Management Inc (28-5194).
- **A unit trap observed while researching**: for Q1-2023 the table value total is **$325,108,752,692**, in dollars. 13f.info shows the same filing as 325,108,752 "$000", a unit read in thousands.

**Why Berkshire**: one manager exercises all three 13F traps: the unit boundary (Q3 vs Q4 2022), both amendment types in one quarter (Q3 2023), and co-filing aggregation, where "how much does Berkshire hold" could mean the filer total, one subsidiary's rows, or Buffett's own rows.

---

## Open items for data_layer (status on 2026-09-24)
1. ~~Pull full-text 10-K/DEF 14A for TGT and CMCSA and the 13F information tables~~ done, cached under `data/raw/` (see log).
2. ~~Confirm the us-gaap concept names in `questions.jsonl`~~ done (log below). Two concept traps found: Target tags the notes-and-debentures subtotal as `us-gaap:LongTermDebt`, and tags only the noncurrent NQDC liability (650, not 717).
3. ~~Target Note 17, Note 24 liability, prepaid-forward fair value, Cornell/Fiddelke 402(i) rows~~ done.
4. Comcast: FY2025 obligation and COLI done; prior 2005-plan version found (May 2024). The crediting rate did not change, so CMCSA-4's stale-version slot now asks for the base-salary deferral limit, which did (floor → cap at 85%).
5. ~~Berkshire other-included-manager lists and the new-holdings issuer; Pershing cover pages~~ done.
6. **Still [U]:** Versant spin-off timing (C2). Not checked this week; the questions stay pinned to the FY2025 10-K as filed.

## EDGAR verification log (2026-09-24)

Source for every accession, form and date: EDGAR submissions API `https://data.sec.gov/submissions/CIK##########.json` and the filing index pages below. Values were parsed from the cached documents with `data_layer`.

### 13F filings [V-EDGAR]
| CIK | Period | Form | Accession | Filing date | Accepted | Unit (rule) | Entries | Table total (as filed) | Other incl. mgrs | Index |
|---|---|---|---|---|---|---|---|---|---|---|
| 1336528 | 2022-09-30 | 13F-HR | 0001172661-22-002568 | 2022-11-14 | 2022-11-14 21:21 UTC | thousands | 6 | 7,877,045 | 0 | https://www.sec.gov/Archives/edgar/data/1336528/000117266122002568/0001172661-22-002568-index.html |
| 1336528 | 2022-12-31 | 13F-HR | 0001172661-23-000673 | 2023-02-13 | 2023-02-10 22:55 UTC | dollars | 6 | 8,784,004,892 | 0 | https://www.sec.gov/Archives/edgar/data/1336528/000117266123000673/0001172661-23-000673-index.html |
| 1067983 | 2022-09-30 | 13F-HR | 0000950123-22-012275 | 2022-11-14 | 2022-11-14 21:01 UTC | thousands | 179 | 296,096,640 | 14 | https://www.sec.gov/Archives/edgar/data/1067983/000095012322012275/0000950123-22-012275-index.html |
| 1067983 | 2022-12-31 | 13F-HR | 0000950123-23-002585 | 2023-02-14 | 2023-02-14 21:00 UTC | dollars | 172 | 299,007,622,119 | 14 | https://www.sec.gov/Archives/edgar/data/1067983/000095012323002585/0000950123-23-002585-index.html |
| 1067983 | 2023-09-30 | 13F-HR | 0000950123-23-010898 | 2023-11-14 | 2023-11-14 21:05 UTC | dollars | 152 | 313,257,308,189 | 14 | https://www.sec.gov/Archives/edgar/data/1067983/000095012323010898/0000950123-23-010898-index.html |
| 1067983 | 2023-09-30 | 13F-HR/A RESTATEMENT | 0000950123-23-011029 | 2023-11-16 | 2023-11-16 21:46 UTC | dollars | 152 | 313,257,308,189 | 14 | https://www.sec.gov/Archives/edgar/data/1067983/000095012323011029/0000950123-23-011029-index.html |
| 1067983 | 2023-09-30 | 13F-HR/A NEW HOLDINGS | 0000950123-24-005653 | 2024-05-15 | 2024-05-15 20:11 UTC | dollars | 1 | 1,695,320,075 | 2 | https://www.sec.gov/Archives/edgar/data/1067983/000095012324005653/0000950123-24-005653-index.html |
| 1067983 | 2023-12-31 | 13F-HR | 0000950123-24-002518 | 2024-02-14 | 2024-02-14 21:02 UTC | dollars | 138 | 347,358,074,461 | 14 | https://www.sec.gov/Archives/edgar/data/1067983/000095012324002518/0000950123-24-002518-index.html |
| 1067983 | 2023-12-31 | 13F-HR/A NEW HOLDINGS | 0000950123-24-005664 | 2024-05-15 | 2024-05-15 20:16 UTC | dollars | 1 | 4,542,600,000 | 2 | https://www.sec.gov/Archives/edgar/data/1067983/000095012324005664/0000950123-24-005664-index.html |

Every accession and filing date in the M1/M2 tables above matches EDGAR. Acceptance times are UTC (Pershing Q4-2022: 17:55 ET on Fri 10 Feb 2023, filing date Mon 13 Feb 2023). Row sums equal the cover total in all nine filings. `unit_diagnostics` finds plausible implied prices in all nine (median $56–$226/share), so the filing-date rule and the evidence agree at filing level. Pershing lists no 13F-HR/A between Q3-2022 and Q3-2023. The entry counts are rows; 13f.info's "holdings" counts issuers (Berkshire Q3-2023: 152 rows).

### Company filings [V-EDGAR]
| Entity | Form | Period | Accession | Filed | Document |
|---|---|---|---|---|---|
| TGT | 10-K | FY2025 (2026-01-31) | 0000027419-26-000016 | 2026-03-11 | https://www.sec.gov/Archives/edgar/data/27419/000002741926000016/tgt-20260131.htm |
| TGT | 10-Q | Q3 FY2025 (2025-11-01) | 0000027419-25-000126 | 2025-11-26 | https://www.sec.gov/Archives/edgar/data/27419/000002741925000126/tgt-20251101.htm |
| TGT | DEF 14A | 2026 | 0001628280-26-027508 | 2026-04-27 | https://www.sec.gov/Archives/edgar/data/27419/000162828026027508/tgt-20260426.htm |
| TGT | EX-10.9 Officer EDCP (2025 Plan Statement) | FY2024 10-K | 0000027419-25-000018 | 2025-03-12 | https://www.sec.gov/Archives/edgar/data/27419/000002741925000018/tgt-20250201xexhibit109.htm |
| TGT | EX-10.I Officer EDCP (2017 Plan Statement) | FY2016 10-K | 0000027419-17-000008 | 2017-03-08 | https://www.sec.gov/Archives/edgar/data/27419/000002741917000008/tgt-20170128xexhibit10i.htm |
| CMCSA | 10-K | FY2025 | 0001628280-26-004994 | 2026-02-03 | https://www.sec.gov/Archives/edgar/data/1166691/000162828026004994/cmcsa-20251231.htm |
| CMCSA | 10-Q | Q3 2025 | 0001166691-25-000054 | 2025-10-30 | https://www.sec.gov/Archives/edgar/data/1166691/000116669125000054/cmcsa-20250930.htm |
| CMCSA | DEF 14A | 2026 | 0001193125-26-177138 | 2026-04-24 | https://www.sec.gov/Archives/edgar/data/1166691/000119312526177138/cmcsa-20260424.htm |
| CMCSA | EX-10.5 2005 DCP (restated 2025-12-31) | FY2025 10-K | 0001628280-26-004994 | 2026-02-03 | https://www.sec.gov/Archives/edgar/data/1166691/000162828026004994/ex105-12312025.htm |
| CMCSA | EX-10.5 2005 DCP (restated 2024-05-14) | FY2024 10-K | 0001166691-25-000011 | 2025-01-31 | https://www.sec.gov/Archives/edgar/data/1166691/000116669125000011/ex105-12312024.htm |
| CMCSA | EX-10.2 Select DCP (restated 2025-12-31) | FY2025 10-K | 0001628280-26-004994 | 2026-02-03 | https://www.sec.gov/Archives/edgar/data/1166691/000162828026004994/ex102-12312025.htm |

### Statement concepts in companyfacts [V-EDGAR]
Source: `https://data.sec.gov/api/xbrl/companyfacts/CIK0000027419.json`, `…CIK0001166691.json`. $ millions.

| Entity | Concept | Period | Value | Note |
|---|---|---|---|---|
| TGT | `LongTermDebtAndCapitalLeaseObligationsCurrent` | 2026-01-31 | 2,130 | replaces "(confirm)" in TGT-1 |
| TGT | `LongTermDebtAndCapitalLeaseObligations` | 2026-01-31 | 14,326 | noncurrent line |
| TGT | `LongTermDebt` | 2026-01-31 | 14,398 | **notes and debentures subtotal**, not the total incl. current |
| TGT | `NetCashProvidedByUsedInOperatingActivities` | FY 2025-02-02 → 2026-01-31 | 6,562 | |
| TGT | same | 9M 2025-02-02 → 2025-11-01 (Q3 10-Q) | 3,485 | Q4 = 3,077 (derived) |
| TGT | `DeferredCompensationLiabilityClassifiedNoncurrent` | 2026-01-31 | 650 | note text total: 717 |
| TGT | `LifeInsuranceCorporateOrBankOwnedAmount` | 2026-01-31 | 600 | COLI, net of loans |
| CMCSA | `DebtCurrent` | 2025-12-31 | 5,958 | |
| CMCSA | `LongTermDebtAndCapitalLeaseObligations` | 2025-12-31 | 92,979 | total 98,937 (derived) |
| CMCSA | `PaymentsToAcquirePropertyPlantAndEquipment` | FY 2025 | 11,750 | |
| CMCSA | same | 9M to 2025-09-30 | 8,001 | Q4 = 3,749 (derived) |
| CMCSA | `DeferredCompensationLiabilityCurrentAndNoncurrent` | 2025-12-31 | 5,097 | instance document |
| CMCSA | `CashSurrenderValueOfLifeInsurance` | 2025-12-31 | 662 | |
