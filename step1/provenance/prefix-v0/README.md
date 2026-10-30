# prefix-v0: dataset state before the 2026-10-03 fixes (injection / comparison material)

| path | what it is |
|---|---|
| statements/, 13f/, entities/ | pre-fix statements and 13F batches (archived 21:21, before any regeneration) |
| nqdc_prefix/ | pre-fix NQDC batch (nqdc-limit's backup taken before regeneration) |
| nqdc_intermediate_2121/ | NQDC mid-fix state copied at 21:21; NOT pre-fix, kept only for traceability |
| anchors.jsonl | anchors at 21:17, already under the tightened matching rule; the 13 anchors the old rule had are in anchors_removed.jsonl |
| anchors_removed.jsonl | anchors dropped by the tightened matching rule (full records + cause) |
| header_misalignment.jsonl | 1,144 table cells in snapshot snap-dacfb2dbaf112676 rendered under the wrong column header (lower bound) |
| st13f_fix_log.jsonl | every statements/13F change: question_id, slot, changed_field, old, new, evidence, cause |
| nqdc_fix_log.jsonl | every NQDC change, same fields; replaced questions are stored whole in old/new |
| families.json, DATASET.md | families.json at 21:19 (partly edited), DATASET.md of the last pre-fix verification |

Fields are objective only; no error classification.
