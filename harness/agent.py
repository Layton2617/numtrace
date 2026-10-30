"""Four-stage deep-research agent: retrieve -> extract -> compute -> synthesise.

Every stage is written to a ``StepLog`` with its structured output, so provenance can be traced
back through the trajectory. Any single stage's output can be substituted (``StageOverrides``) to
run paired single-stage swaps.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field

from .corpus import Corpus
from .models import ModelClient, StageRequest
from .schema import (
    ComputedValue,
    DerivationInput,
    DerivationKind,
    ExtractedFact,
    Question,
    RetrievalConfig,
    RetrievedDoc,
    Span,
    Stage,
    StageOverrides,
    StepLog,
    Trajectory,
    Usage,
    sha256_of,
)
from .tools import CodeTool, safe_eval

PROMPT_VERSION = "v2"


class AgentConfig(BaseModel):
    """Everything that defines a run except the corpus snapshot. Hashed into ``config_hash``."""

    model: str
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    max_queries: int = 2
    max_tool_calls: int = 20
    code_tool: bool = False
    temperature: float = 0.0
    seed: Optional[int] = 0
    max_tokens: int = 2048
    prompt_version: str = PROMPT_VERSION
    log_doc_text: bool = True

    def config_hash(self) -> str:
        return "cfg-" + sha256_of(self.model_dump(mode="json"))[:16]


class StageOverrideError(ValueError):
    pass


SYSTEM = (
    "You are a financial deep-research agent working only from the documents provided. "
    "Follow the output format exactly."
)


# The agent sees, and the scorer counts as answered/missing, only the slots the question asks for. An input of a
# computed slot, or a slot listed here, is asked only when its cue is in the question text; otherwise listing it gives
# away the method (Q4 = FY - 9M; a change from two quarter-end values) or asks for more than the question does
# (a position value when only the share count was asked). See harness/LEAKAGE.md.
ASK_CUES = {
    "market_value": r"\bworth\b|\bvalue\b",
    "group_value": r"\bworth\b|\bvalue\b",
    "current_portion": r"due within (?:one|a) year|current and noncurrent",
    "noncurrent": r"\bnoncurrent\b",
    "diluted_eps": r"diluted earnings per share",
    "diluted_eps_prior_year": r"same quarter of fiscal",
}

# What the agent sees instead of the slot id: only the question's own words. Gold slot ids name the concept, scope or
# method (coli_value, shares_total_filer_level, registrant_contributions), which is the binding being measured.
DISPLAY_IDS = {
    "current_portion": "due_within_one_year",
    "total_including_current": "total_long_term_debt",
    "q4_capex": "capital_expenditures_fourth_quarter",
    "q4_ocf": "operating_cash_fourth_quarter",
    "q4_operating_cash_flow": "operating_cash_fourth_quarter",
    "diluted_eps_prior_year": "diluted_eps_same_quarter_prior_year",
    "diluted_shares": "weighted_average_diluted_shares",
    "executive_contributions": "amount_deferred",
    "registrant_contributions": "company_contribution",
    "aggregate_earnings": "account_earnings",
    "aggregate_withdrawals": "amount_withdrawn",
    "prior_year_end_balance": "earlier_balance",
    "reconciliation_difference": "difference",
    "plan_liability": "obligation",
    "benefit_obligation": "obligation",
    "coli_value": "life_insurance_value",
    "coli_cash_surrender_value": "assets_held_against_obligation",
    "shares_total_filer_level": "shares",
    "group_shares": "shares",
    "market_value": "position_value",
    "group_value": "position_value",
    "held_at_2023-12-31": "holding",
    "change": "value_change",
}


def _display_id(slot_id: str) -> str:
    m = re.fullmatch(r"top(\d)_change", slot_id)
    return f"position{m[1]}_value_change" if m else DISPLAY_IDS.get(slot_id, slot_id)


def shown_slots(q: Question) -> dict[str, str]:
    """Display id -> slot id for the slots the question asks for, in question order."""
    inputs = {i for s in q.slots for i in s.inputs.values()}
    return {_display_id(s.slot_id): s.slot_id for s in q.slots
            if (re.search(ASK_CUES[s.slot_id], q.text, re.I) if s.slot_id in ASK_CUES else s.slot_id not in inputs)}


def real_slot(q: Question, name: Any) -> Any:
    """The slot id behind a name the model wrote; anything else (its own label, null) passes through."""
    return shown_slots(q).get(name, name) if isinstance(name, str) else name


def shown_slot(q: Question, slot_id: Optional[str]) -> Optional[str]:
    """How a bound slot id is rendered back to the model: its display id, None for a hidden slot."""
    if slot_id is None:
        return None
    back = {v: k for k, v in shown_slots(q).items()}
    if slot_id in back:
        return back[slot_id]
    return None if any(s.slot_id == slot_id for s in q.slots) else slot_id


def _q_block(q: Question) -> str:
    # Display ids only: the slot's concept, period, unit, scope and formula are the binding the study measures,
    # so showing them would hand the agent the answer to "right number, wrong account".
    slots = "\n".join(f"- {d}" for d in shown_slots(q))
    return f"QUESTION ({q.filing_type.value}): {q.text}\nANSWER SLOTS (use these ids):\n{slots}"


def _doc_block(docs: list[dict]) -> str:
    return "\n\n".join(
        f"[DOC {d['doc_id']}] source: {d['source_label']} | form: {d.get('form_type')} | "
        f"filed: {d.get('filing_date')} | entity: {d.get('entity')} | period: {d.get('period')}\n{d['text']}"
        for d in docs
    )


PROMPTS = {
    Stage.retrieval: "{q}\n\nWrite up to {n} search queries for a BM25 index of filings. "
                     'Return JSON: {{"queries": ["..."]}}',
    Stage.extraction: "{q}\n\nDOCUMENTS:\n{docs}\n\nQuote each line whose number you need to answer the "
                      "slots, one fact per line. Set slot_id to the answer slot the printed number answers as it "
                      "stands, or null if you need it only as an input to a calculation. Return JSON: {{\"facts\": [{{\"fact_id\": \"F1\", \"doc_id\": \"...\", "
                      "\"quote\": \"exact line\", \"label\": \"line label\", \"printed_value\": 0, "
                      "\"unit_text\": \"unit statement in the doc or null\", \"slot_id\": \"...\", \"concept\": \"...\", "
                      "\"entity\": \"...\", \"period\": \"...\"}}]}}",
    Stage.calculation: "{q}\n\nDOCUMENT METADATA:\n{meta}\n\nEXTRACTED FACTS:\n{facts}\n\nConvert every fact to base "
                       "units by choosing a scale (amounts in dollars, share counts in shares, per-share figures as "
                       "printed), and give a value for every answer slot: a fact as printed (formula "
                       "\"x\") or arithmetic over facts and other slots' values. Return JSON: "
                       "{{\"values\": [{{\"slot_id\": \"...\", \"formula\": \"x\", \"inputs\": {{\"x\": {{\"fact_id\": \"F1\", "
                       "\"scale\": 1000}}}}}}, {{\"slot_id\": \"...\", \"formula\": \"arithmetic in a, b\", \"inputs\": "
                       "{{\"a\": {{\"fact_id\": \"F2\", \"scale\": 1}}, \"b\": {{\"slot_ref\": \"...\"}}}}}}]}}",
    Stage.synthesis: "{q}\n\nFACTS:\n{facts}\n\nVALUES (base units):\n{values}\n\nWrite a short report answering every "
                     "slot. After each number cite its fact or value id in brackets, e.g. [F1] or [C2]. Write money "
                     "as $X.XX million/billion.",
}


def _model_view(d: RetrievedDoc) -> dict:
    """What the model is allowed to see of a document: never is_primary_filing or meta."""
    return {k: getattr(d, k) for k in ("doc_id", "source_label", "form_type", "filing_date", "entity", "period",
                                        "title", "text")}


def parse_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _as_number(x) -> Optional[float]:
    """A model-supplied number as float: accepts numbers and strings like "2,130", "(1,234)", "$1.2"; else None."""
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    t = str(x).strip().replace(",", "").replace("$", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def locate_span(doc_text: str, doc_id: str, quote: str) -> Span:
    """Find the quoted text in the doc (exact, then whitespace-insensitive). start=-1 if not found."""
    i = doc_text.find(quote)
    if i >= 0:
        return Span(doc_id=doc_id, start=i, end=i + len(quote), text=quote)
    pat = r"\s+".join(re.escape(t) for t in quote.split())
    m = re.search(pat, doc_text) if pat else None
    if m:
        return Span(doc_id=doc_id, start=m.start(), end=m.end(), text=m.group(0))
    return Span(doc_id=doc_id, start=-1, end=-1, text=quote)


class DeepResearchAgent:
    def __init__(self, model: ModelClient, corpus: Corpus, config: Optional[AgentConfig] = None) -> None:
        self.model = model
        self.corpus = corpus
        self.config = config or AgentConfig(model=model.name)
        self.code_tool = CodeTool() if self.config.code_tool else None

    def _call(self, stage: Stage, q: Question, context: dict, fmt: dict, response_format: str = "json"):
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": PROMPTS[stage].format(q=_q_block(q), **fmt)}]
        req = StageRequest(stage=stage, messages=messages, context=context, response_format=response_format,
                           temperature=self.config.temperature, seed=self.config.seed,
                           max_tokens=self.config.max_tokens)
        return self.model.complete(req)

    def _log(self, traj: Trajectory, stage: Stage, inp: dict, out: dict, *, tool: Optional[str] = None,
             resp=None, overridden: bool = False) -> None:
        traj.steps.append(StepLog(step_id=len(traj.steps), stage=stage, input=inp, output=out, tool=tool,
                                  model=self.model.name if resp else None, usage=resp.usage if resp else None,
                                  raw_model_output=resp.text if resp else None, overridden=overridden))
        if resp:
            traj.usage = traj.usage.add(resp.usage)
            traj.model_version = resp.model_version or traj.model_version

    def run(self, question: Question, overrides: Optional[StageOverrides] = None, *,
            condition: str = "clean", allow_multiple_overrides: bool = False) -> Trajectory:
        ov = overrides or StageOverrides()
        active = ov.active()
        if len(active) > 1 and not allow_multiple_overrides:
            raise StageOverrideError(f"exactly one stage may be substituted per run, got {active}")
        traj = Trajectory(
            trajectory_id=f"traj-{uuid.uuid4().hex[:12]}",
            question_id=question.id,
            model=self.model.name,
            corpus_snapshot_id=self.corpus.snapshot_id,
            retrieval_config=self.config.retrieval,
            config_hash=self.config.config_hash(),
            condition=condition,
            overrides=active,
            usage=Usage(token_count_method="approx_chars_div_4" if getattr(self.model, "provider", "") == "mock"
                        else "provider"),
        )
        docs = self._retrieve(question, traj, ov)
        facts = self._extract(question, docs, traj, ov)
        values = self._calculate(question, docs, facts, traj, ov)
        traj.report = self._synthesise(question, facts, values, traj, ov)
        return traj

    def _retrieve(self, q: Question, traj: Trajectory, ov: StageOverrides) -> list[RetrievedDoc]:
        cfg = self.config.retrieval
        if ov.retrieval is not None:
            docs = list(ov.retrieval)
            self._log(traj, Stage.retrieval, {"override": True},
                      {"docs": [self._doc_out(d) for d in docs]}, tool="override", overridden=True)
            return docs
        resp = self._call(Stage.retrieval, q, {"question": q.model_dump(mode="json")},
                          {"n": self.config.max_queries})
        queries = [str(x) for x in parse_json(resp.text).get("queries", [])][: self.config.max_queries] or [q.text]
        # Every query's own top-k is logged (per_query), so a doc's score never depends on which query
        # happened to find it first; the union keeps (query, rank) order.
        first: dict[str, RetrievedDoc] = {}
        scores: dict[str, dict[int, float]] = {}
        per_query = []
        for qi, query in enumerate(queries):
            hits = self.corpus.retrieve(query, cfg)
            per_query.append({"query_index": qi, "query": query,
                              "hits": [{"doc_id": d.doc_id, "rank": d.rank, "score": d.score} for d in hits]})
            for d in hits:
                scores.setdefault(d.doc_id, {})[qi] = d.score
                if d.doc_id not in first:
                    first[d.doc_id] = d.model_copy(update={"query_index": qi})
        docs = [d.model_copy(update={"rank": i, "query_scores": scores[d.doc_id]})
                for i, d in enumerate(first.values(), start=1)]
        self._log(traj, Stage.retrieval,
                  {"queries": queries, "retrieval_config": cfg.model_dump(), "snapshot_id": self.corpus.snapshot_id},
                  {"docs": [self._doc_out(d) for d in docs], "per_query": per_query}, tool="bm25", resp=resp)
        return docs

    def _doc_out(self, d: RetrievedDoc) -> dict:
        out = d.model_dump(mode="json")
        if not self.config.log_doc_text:
            out["text"] = None
        return out

    def _extract(self, q: Question, docs: list[RetrievedDoc], traj: Trajectory, ov: StageOverrides) -> list[ExtractedFact]:
        if ov.extraction is not None:
            facts = list(ov.extraction)
            self._log(traj, Stage.extraction, {"override": True},
                      {"facts": [f.model_dump(mode="json") for f in facts]}, tool="override", overridden=True)
            return facts
        view = [_model_view(d) for d in docs]
        resp = self._call(Stage.extraction, q, {"question": q.model_dump(mode="json"), "docs": view},
                          {"docs": _doc_block(view)})
        by_id = {d.doc_id: d for d in docs}
        facts, unusable = [], []
        raw_facts = parse_json(resp.text).get("facts", []) or []
        for i, f in enumerate(raw_facts, start=1):
            if not isinstance(f, dict):
                unusable.append({"item": f, "reason": "not an object"})
                continue
            value = _as_number(f.get("printed_value"))
            if value is None:  # the model reported the slot as not found, or gave no number: no fact, logged
                unusable.append({"item": f, "reason": "no numeric printed_value"})
                continue
            quote = str(f.get("quote") or "")
            doc = by_id.get(f.get("doc_id"))
            span = locate_span(doc.text, doc.doc_id, quote) if doc else \
                Span(doc_id=str(f.get("doc_id")), start=-1, end=-1, text=quote)
            facts.append(ExtractedFact(
                fact_id=str(f.get("fact_id") or f"F{i}"), doc_id=span.doc_id, span=span, label=str(f.get("label") or ""),
                printed_value=value, unit_text=f.get("unit_text"),
                bound_slot=real_slot(q, f.get("slot_id")), bound_concept=f.get("concept"),
                entity=f.get("entity"), period=f.get("period")))
        overridden = False
        if ov.extraction_patch:
            overridden = True
            for slot_id, patch in ov.extraction_patch.items():
                idx = next((j for j, f in enumerate(facts) if f.bound_slot == slot_id), None)
                if idx is None:
                    facts.append(patch)
                else:
                    facts[idx] = patch.model_copy(update={"fact_id": facts[idx].fact_id})
        self._log(traj, Stage.extraction, {"doc_ids": [d.doc_id for d in docs],
                                           "patched_slots": sorted(ov.extraction_patch or {})},
                  {"facts": [f.model_dump(mode="json") for f in facts], "unusable": unusable}, tool="model", resp=resp,
                  overridden=overridden)
        return facts

    def _calculate(self, q: Question, docs: list[RetrievedDoc], facts: list[ExtractedFact], traj: Trajectory,
                   ov: StageOverrides) -> list[ComputedValue]:
        if ov.calculation is not None:
            values = list(ov.calculation)
            self._log(traj, Stage.calculation, {"override": True},
                      {"values": [v.model_dump(mode="json") for v in values]}, tool="override", overridden=True)
            return values
        meta = [{k: d.model_dump()[k] for k in ("doc_id", "source_label", "form_type", "filing_date", "entity", "period")}
                for d in docs]
        fdump = [f.model_dump(mode="json") for f in facts]
        ctx_facts = [{**f, "quote": f["span"]["text"]} for f in fdump]
        shown_facts = [{**f, "bound_slot": shown_slot(q, f["bound_slot"])} for f in ctx_facts]
        resp = self._call(Stage.calculation, q,
                          {"question": q.model_dump(mode="json"), "facts": ctx_facts, "docs": meta},
                          {"meta": json.dumps(meta, indent=1), "facts": json.dumps(shown_facts, indent=1)})
        plan = parse_json(resp.text).get("values", [])
        for item in plan:
            if isinstance(item, dict):
                item["slot_id"] = real_slot(q, item.get("slot_id"))
                for spec in (item.get("inputs") or {}).values():
                    if isinstance(spec, dict) and "slot_ref" in spec:
                        spec["slot_ref"] = real_slot(q, spec["slot_ref"])
        self._log(traj, Stage.calculation, {"fact_ids": [f.fact_id for f in facts]}, {"plan": plan},
                  tool="model", resp=resp)
        fact_by_id = {f.fact_id: f for f in facts}
        slot_values: dict[str, ComputedValue] = {}
        values: list[ComputedValue] = []
        tool_calls = 0
        pending = list(plan)
        for _ in range(len(pending) + 1):
            if not pending:
                break
            nxt = []
            for item in pending:
                ins = item.get("inputs") or {} if isinstance(item, dict) else {}
                if not isinstance(item, dict):
                    continue
                if any("slot_ref" in spec and spec["slot_ref"] not in slot_values for spec in ins.values()):
                    nxt.append(item)
                    continue
                if tool_calls >= self.config.max_tool_calls:
                    break
                dins, env = [], {}
                for var, spec in ins.items():
                    if not isinstance(spec, dict):
                        continue
                    if "fact_id" in spec:
                        f = fact_by_id.get(spec["fact_id"])
                        if f is None:
                            continue
                        scale = _as_number(spec.get("scale")) or 1.0
                        env[var] = f.printed_value * scale
                        dins.append(DerivationInput(ref=f.fact_id, var=var, value=env[var], printed_value=f.printed_value,
                                                    scale=scale, concept=f.bound_concept, period=f.period,
                                                    doc_id=f.doc_id, span=f.span))
                    else:
                        v = slot_values[spec["slot_ref"]]
                        env[var] = v.value
                        src = v.inputs[0] if v.formula.strip() == "x" and len(v.inputs) == 1 else None
                        dins.append(DerivationInput(
                            ref=v.value_id, var=var, value=v.value, concept=v.concept, period=v.period,
                            printed_value=src.printed_value if src else None, scale=src.scale if src else None,
                            doc_id=src.doc_id if src else None, span=src.span if src else None))
                formula = str(item.get("formula") or "x")
                tool = "arith"
                try:
                    if item.get("code") and self.code_tool:
                        tool = "code"
                        result = float(self.code_tool.run(item["code"]))
                    else:
                        result = safe_eval(formula, env)
                except Exception as e:  # e.g. an input fact that extraction could not supply: logged, no value
                    self._log(traj, Stage.calculation, {"formula": formula, "env": env, "slot_id": item.get("slot_id")},
                              {"error": f"{type(e).__name__}: {e}"}, tool=f"{tool}_error")
                    continue
                tool_calls += 1
                slot = next((s for s in q.slots if s.slot_id == item.get("slot_id")), None)
                cv = ComputedValue(
                    value_id=f"C{len(values) + 1}", slot_id=item.get("slot_id"),
                    concept=slot.concept if slot else (dins[0].concept if dins else None),
                    period=slot.period if slot else (dins[0].period if dins else None),
                    value=result, formula=formula, inputs=dins, tool=tool,
                    derivation=DerivationKind.reported if formula.strip() == "x" and len(dins) == 1
                    else DerivationKind.computed)
                values.append(cv)
                if cv.slot_id:
                    slot_values[cv.slot_id] = cv
                self._log(traj, Stage.calculation, {"formula": formula, "env": env},
                          {"result": result, "value_id": cv.value_id}, tool=tool)
            pending = nxt
        self._log(traj, Stage.calculation, {"value_ids": [v.value_id for v in values]},
                  {"values": [v.model_dump(mode="json") for v in values]}, tool="aggregate")
        return values

    def _synthesise(self, q: Question, facts: list[ExtractedFact], values: list[ComputedValue], traj: Trajectory,
                    ov: StageOverrides) -> str:
        if ov.synthesis is not None:
            self._log(traj, Stage.synthesis, {"override": True}, {"report": ov.synthesis}, tool="override",
                      overridden=True)
            return ov.synthesis
        fdump = [f.model_dump(mode="json") for f in facts]
        vdump = [v.model_dump(mode="json") for v in values]
        resp = self._call(
            Stage.synthesis, q, {"question": q.model_dump(mode="json"), "facts": fdump, "values": vdump},
            {"facts": "\n".join(f"[{f.fact_id}] {f.span.text} (doc {f.doc_id}; slot {shown_slot(q, f.bound_slot)})"
                                for f in facts),
             "values": "\n".join(f"[{v.value_id}] {shown_slot(q, v.slot_id)} = {v.value} ({v.formula})"
                                 for v in values)},
            response_format="text")
        self._log(traj, Stage.synthesis, {"fact_ids": [f.fact_id for f in facts],
                                          "value_ids": [v.value_id for v in values]},
                  {"report": resp.text}, tool="model", resp=resp)
        return resp.text


def stage_output(traj: Trajectory, stage: Stage) -> Any:
    """The structured final output of one stage in a logged trajectory."""
    if stage == Stage.retrieval:
        step = traj.steps_for(Stage.retrieval)[-1]
        return [RetrievedDoc.model_validate(d) for d in step.output["docs"]]
    if stage == Stage.extraction:
        step = traj.steps_for(Stage.extraction)[-1]
        return [ExtractedFact.model_validate(f) for f in step.output["facts"]]
    if stage == Stage.calculation:
        step = [s for s in traj.steps_for(Stage.calculation) if "values" in s.output][-1]
        return [ComputedValue.model_validate(v) for v in step.output["values"]]
    return traj.report


def swap_overrides(donor: Trajectory, stage: Stage | str, *, slot_id: Optional[str] = None) -> StageOverrides:
    """Build overrides that substitute ``donor``'s output for one stage (e.g. clean -> attacked run).
    With ``stage='extraction'`` and ``slot_id``, only that slot's extracted span is swapped."""
    stage = Stage(stage)
    out = stage_output(donor, stage)
    if stage == Stage.retrieval:
        return StageOverrides(retrieval=out)
    if stage == Stage.extraction:
        if slot_id:
            fact = next(f for f in out if f.bound_slot == slot_id)
            return StageOverrides(extraction_patch={slot_id: fact})
        return StageOverrides(extraction=out)
    if stage == Stage.calculation:
        return StageOverrides(calculation=out)
    return StageOverrides(synthesis=out)
