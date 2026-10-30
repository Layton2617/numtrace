# Run notes

Specification: GOLD_SPEC.md, dated 2026-10-04. Dataset: step1-v1.0. Snapshot: snap-002c60fedf88e905.

Run from the project directory:

```sh
python reimpl.py
```

Uses only the Python standard library. Reads `sample.json` and `inputs/` from the same directory and writes `reimpl_gold.jsonl`.
Use `--root` to change the input directory and `--output` to change the output file.

Python 3.11.16. Processed 110 questions and produced 444 rows in 14.285 seconds, excluding conda startup.
The sizes and SHA-256 hashes of all 1,240 input files matched INVENTORY.json.

| Family | Questions | Output rows | excluded |
|---|---:|---:|---:|
| ST-LTD | 7 | 21 | 0 |
| ST-DEBT | 7 | 21 | 0 |
| ST-Q4 | 8 | 24 | 0 |
| ST-EPS | 8 | 32 | 0 |
| 13F-POS | 10 | 30 | 0 |
| 13F-UNIT | 10 | 30 | 0 |
| 13F-TOP | 10 | 100 | 0 |
| 13F-GROUP | 10 | 30 | 0 |
| NQDC-402I | 10 | 40 | 0 |
| NQDC-ROLL | 10 | 70 | 0 |
| NQDC-LIMIT | 10 | 16 | 0 |
| NQDC-FUND | 10 | 30 | 0 |
| Total | 110 | 444 | 0 |

No questions were excluded. A few results to note:

- FUND has 14 `not_found_by_rule` slots where no match was found for the specified tag.
- 402I has 9 `not_reported` slots: 4 each for GOOGL FY2022 and TSLA FY2024, and 1 for MSFT FY2023 registrant contributions.
- The reconciliation differences for BLK Kushel FY2025 and HON Boldea FY2024 are 658,271 USD and -3,590 USD, respectively, retained under §4.4.
- The three GROUP slots for PRU Apple 2026Q1 include `accept` under §3.9.

Checks covered question and slot coverage, field types, statuses, duplicate slots, difference formulas, rounding, 13F amendments, group deduplication, and NQDC table and version selection.
The original implementation and existing gold were not read, and no network access was used. Results have not been compared with the original project's answers. See `ambiguities.md` for interpretation choices.
