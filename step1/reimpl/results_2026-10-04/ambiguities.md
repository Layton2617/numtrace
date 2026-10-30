# Specification ambiguities

Three points needed interpretation when implementing GOLD_SPEC.md (2026-10-04).

1. **Value of a tagged total, §2.5, step 9**

   The formula says `value = sum`, but the following explanation says “The printed / tagged total is the gold”.
   The formula uses current + noncurrent from the latest filing; the explanation could mean using `Ft.val` directly.

   The implementation follows the formula: `LA.val + LB.val`, with `alt_value` equal to the sum of the two component slots.
   Both readings give the same values for this sample.

   Affected questions:
   `S1-ST-DEBT-BA-FY2022`, `S1-ST-DEBT-INTC-FY2024`,
   `S1-ST-DEBT-NVDA-FY2023`, `S1-ST-DEBT-ORCL-FY2023`,
   `S1-ST-LTD-AAPL-FY2023`, `S1-ST-LTD-DOW-FY2022`,
   `S1-ST-LTD-HD-FY2025`, `S1-ST-LTD-HON-FY2025`,
   `S1-ST-LTD-MSFT-FY2026`, `S1-ST-LTD-NEM-FY2024`.

2. **Proxy HTML missing for filings listed in submissions, §§1.1 and 4.1**

   Section 4.1 calls for iterating over proxies; §1.1 says the package contains only files needed by the questions.
   For example, submissions list AAPL FY2023, BA FY2023/FY2025 and BLK FY2022/FY2023, but their HTML is not included.

   Missing files could either prevent proxy selection or be skipped for years outside the sample. The implementation skips them.
   None of these years is needed by a sampled question, including the prior years required by ROLL, so no questions are affected.
   A missing document is not treated as `no_table`.

3. **Row ownership in nested tables, §4.2 and Appendix A.4**

   “rows are the `<tr>` elements of the table” does not specify whether an outer table includes rows from nested tables.
   One reading includes all descendant rows in the outer table and then parses the inner tables separately;
   the other assigns each row only to its own table. The implementation uses the latter.

   No proxy table fragments passing the keyword filter contained nested tables, so no sampled questions are affected.
   The reader uses the standard-library `HTMLParser`. Closing tags unwind the stack to the matching node;
   omitted cell and row closing tags are handled at cell and row boundaries.
   Other readers may recover malformed HTML differently; no such difference was found to affect extraction in this sample.
