"""NumTrace data layer: ground truth for 10-K/10-Q facts, Form 13F holdings and NQDC.

Modules:
  facts          the common Fact record
  thirteen_f     13F parsing, unit rule (Form 13F FAQ 62; FAQ 36 for dollars rounded from 2023-01-03), amendment merge (Form 13F Special Instruction 3, FAQ 58b/58c), co-filing groups
  statements     companyfacts fact store, concept binding table, derivations
  nqdc           Item 402(i) table schema/parser, company-level NQDC record
  binding_check  concept check of a claim against a fact store
"""
