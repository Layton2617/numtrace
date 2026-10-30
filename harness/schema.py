"""Typed records for the NumTrace harness (the provenance record).

Everything that crosses a module boundary or is written to disk is a pydantic model here, so
trajectories, claims and verdicts round-trip through JSONL unchanged.

Conventions
-----------
* Monetary values in Claim / ComputedValue are in *base units* (USD, not thousands).
* ``printed_value`` is the number exactly as printed in the source, before any scale is applied.
* ``scale`` is the multiplier from printed to base units (1, 1e3, 1e6, 1e9).
* Concepts are namespaced strings, e.g. ``us-gaap:LongTermDebt``, ``13f:Value``,
  ``nqdc:PlanLiability``. Each question slot names the concept it asks for.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(obj: Any) -> str:
    """Stable JSON used for hashing (sorted keys, no whitespace)."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FilingType(str, Enum):
    statement = "statement"
    f13 = "13f"
    nqdc = "nqdc"


class Stage(str, Enum):
    retrieval = "retrieval"
    extraction = "extraction"
    calculation = "calculation"
    synthesis = "synthesis"


STAGE_ORDER = [Stage.retrieval, Stage.extraction, Stage.calculation, Stage.synthesis]


class StageAttribution(str, Enum):
    """Where a failure first becomes visible; adds unknown / multiple to the four stages."""

    retrieval = "retrieval"
    extraction = "extraction"
    calculation = "calculation"
    synthesis = "synthesis"
    unknown = "unknown"
    multiple = "multiple"
    none = "none"


class Verdict(str, Enum):
    pass_ = "pass"
    fail = "fail"
    na = "na"


class DerivationKind(str, Enum):
    reported = "reported"
    computed = "computed"


class SlotStatus(str, Enum):
    answered = "answered"
    formula_only = "formula_only"
    missing = "missing"
    refused = "refused"


class BindingField(str, Enum):
    entity = "entity"
    concept = "concept"
    period = "period"
    unit = "unit"
    scope = "scope"


class AnswerSlot(_Model):
    """One expected answer in a question. A report must fill every slot."""

    slot_id: str
    concept: str
    entity: str
    period: str
    unit: str = "USD"
    derivation: DerivationKind = DerivationKind.reported
    formula: Optional[str] = None
    inputs: dict[str, str] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    expected_value: Optional[float] = None
    scope: Optional[str] = None


class Question(_Model):
    id: str
    filing_type: FilingType
    entity: str
    period: str
    text: str
    target_concepts: list[str]
    slots: list[AnswerSlot]
    family: Optional[str] = None

    def slot(self, slot_id: str) -> AnswerSlot:
        for s in self.slots:
            if s.slot_id == slot_id:
                return s
        raise KeyError(slot_id)


class Doc(_Model):
    """A document in a frozen corpus snapshot."""

    doc_id: str
    source_label: str
    accession: Optional[str] = None
    entity: Optional[str] = None
    form_type: Optional[str] = None
    period: Optional[str] = None
    filing_date: Optional[str] = None
    is_primary_filing: bool = False
    title: str = ""
    text: str
    meta: dict[str, Any] = Field(default_factory=dict)


class RetrievedDoc(Doc):
    """A retrieved doc. With several queries the agent unions the per-query top-k in (query, rank) order:
    ``query_index``/``score`` are those of the first query that returned it, and ``query_scores`` holds its
    BM25 score under every query that returned it (query index -> score). Per-query scores of an untouched
    doc are identical across a clean and an attacked snapshot (shared BM25 reference statistics)."""
    rank: int
    score: float
    query_index: Optional[int] = None
    query_scores: dict[int, float] = Field(default_factory=dict)


class RetrievalConfig(_Model):
    method: Literal["bm25"] = "bm25"
    k: int = 5
    k1: float = 1.5
    b: float = 0.75
    tokenizer: str = "lower_alnum_v1"


class Span(_Model):
    doc_id: str
    start: int
    end: int
    text: str


class ExtractedFact(_Model):
    """Output of the extraction stage: a number read from a span, and what the agent bound it to."""

    fact_id: str
    doc_id: str
    span: Span
    label: str
    printed_value: float
    unit_text: Optional[str] = None
    bound_slot: Optional[str] = None
    bound_concept: Optional[str] = None
    entity: Optional[str] = None
    period: Optional[str] = None


class DerivationInput(_Model):
    ref: str
    var: str = "x"
    value: float
    printed_value: Optional[float] = None
    scale: Optional[float] = None
    concept: Optional[str] = None
    period: Optional[str] = None
    doc_id: Optional[str] = None
    span: Optional[Span] = None


class ComputedValue(_Model):
    """Output of the calculation stage: a value in base units bound to a slot."""

    value_id: str
    slot_id: Optional[str] = None
    concept: Optional[str] = None
    period: Optional[str] = None
    value: float
    unit: str = "USD"
    formula: str = "x"
    derivation: DerivationKind = DerivationKind.reported
    inputs: list[DerivationInput] = Field(default_factory=list)
    tool: str = "arith"


class Usage(_Model):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    token_count_method: str = "provider"

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            calls=self.calls + other.calls,
            cost_usd=self.cost_usd + other.cost_usd,
            token_count_method=other.token_count_method or self.token_count_method,
        )


class StepLog(_Model):
    step_id: int
    stage: Stage
    input: dict[str, Any]
    output: dict[str, Any]
    tool: Optional[str] = None
    timestamp: str = Field(default_factory=utcnow)
    model: Optional[str] = None
    usage: Optional[Usage] = None
    raw_model_output: Optional[str] = None
    overridden: bool = False


class StageOverrides(_Model):
    """Substitute exactly one stage's output (paired single-stage swaps)."""

    retrieval: Optional[list[RetrievedDoc]] = None
    extraction: Optional[list[ExtractedFact]] = None
    extraction_patch: Optional[dict[str, ExtractedFact]] = None
    calculation: Optional[list[ComputedValue]] = None
    synthesis: Optional[str] = None

    def active(self) -> list[str]:
        return [k for k, v in self.model_dump(exclude_none=True).items() if v is not None]


class Trajectory(_Model):
    trajectory_id: str
    question_id: str
    model: str
    model_version: Optional[str] = None
    corpus_snapshot_id: str
    retrieval_config: RetrievalConfig
    config_hash: str
    condition: str = "clean"
    steps: list[StepLog] = Field(default_factory=list)
    report: str = ""
    usage: Usage = Field(default_factory=Usage)
    overrides: list[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=utcnow)

    def steps_for(self, stage: Stage) -> list[StepLog]:
        return [s for s in self.steps if s.stage == stage]


class Derivation(_Model):
    kind: DerivationKind
    formula: Optional[str] = None
    inputs: list[DerivationInput] = Field(default_factory=list)


class Claim(_Model):
    claim_id: str
    question_id: str
    trajectory_id: str
    text: str
    number_text: str
    value: float
    rel_tol: float = 0.0
    unit: str = "USD"
    entity: Optional[str] = None
    concept_as_stated: Optional[str] = None
    concept_phrase: Optional[str] = None
    period: Optional[str] = None
    derivation: Derivation
    source: Optional[Span] = None
    citation: Optional[str] = None
    slot_id: Optional[str] = None


class Binding(_Model):
    entity: Optional[str] = None
    concept: Optional[str] = None
    period: Optional[str] = None
    unit: Optional[str] = None
    scale: Optional[float] = None
    scope: Optional[str] = None


class ProvenanceRecord(_Model):
    claim_id: str
    source: Optional[Span]
    binding: Binding
    gold_binding: Optional[Binding] = None
    derivation: Derivation
    stage_first_visible: StageAttribution
    stage_evidence: dict[str, str] = Field(default_factory=dict)


class Verdicts(_Model):
    value: Verdict
    binding: Verdict
    support: Verdict
    binding_failures: list[BindingField] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FactCheckResult(_Model):
    """What the data layer's fact_check returns for one claim (see scoring.FactChecker)."""

    value: Verdict
    binding: Verdict
    support: Verdict
    binding_failures: list[BindingField] = Field(default_factory=list)
    value_failure: Optional[str] = None
    gold_binding: Optional[Binding] = None
    expected_binding: Optional[Binding] = None
    gold_value: Optional[float] = None
    matched_scale: Optional[float] = None
    source_flags: list[str] = Field(default_factory=list)
    gold_source_doc_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class SlotScore(_Model):
    question_id: str
    trajectory_id: str
    slot_id: str
    status: SlotStatus
    claim_ids: list[str] = Field(default_factory=list)


class ScoredClaim(_Model):
    claim: Claim
    provenance: ProvenanceRecord
    verdicts: Verdicts
    fact_check: FactCheckResult


class InterventionKind(str, Enum):
    none = "none"
    label_swap = "label_swap"
    primary_absent = "primary_absent"
    value_shift = "value_shift"
    unit_substitution = "unit_substitution"
    amendment_type = "amendment_type"
    funding_asset_as_liability = "funding_asset_as_liability"
    stale_plan_document = "stale_plan_document"


class Intervention(_Model):
    question_id: str
    kind: InterventionKind
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int = 0
    added_doc_ids: list[str] = Field(default_factory=list)
    removed_doc_ids: list[str] = Field(default_factory=list)
    modified_doc_ids: list[str] = Field(default_factory=list)
    presentation_order: list[str] = Field(default_factory=list)


JSONable = Union[dict, list, str, int, float, None]
