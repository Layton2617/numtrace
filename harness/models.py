"""Model clients.

``ModelClient.complete(StageRequest) -> ModelResponse`` is the only interface the agent uses.
A ``StageRequest`` carries both the rendered chat messages (used by real models) and the
structured context the messages were rendered from (used by ``MockModel``). Real and mock
responses go through the same parser in ``agent.py``.

Real adapters (OpenAI, Anthropic, DeepSeek) use plain HTTPS via urllib and raise
``MissingAPIKeyError`` at construction when their key is not set; nothing is called otherwise.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .schema import Stage, Usage
from .corpus import tokenize
from .tools import detect_unit_text, f13_scale, fmt_usd, parse_table_lines, scale_from_unit_text, unit_text_at


class StageRequest(BaseModel):
    stage: Stage
    messages: list[dict[str, str]]
    context: dict[str, Any] = Field(default_factory=dict)
    response_format: str = "json"
    temperature: float = 0.0
    seed: Optional[int] = None
    max_tokens: int = 2048


class ModelResponse(BaseModel):
    text: str
    usage: Usage
    model_version: Optional[str] = None


@runtime_checkable
class ModelClient(Protocol):
    name: str

    def complete(self, request: StageRequest) -> ModelResponse: ...


def approx_tokens(text: str) -> int:
    """Provider-independent token estimate (~4 chars/token), used for the mock and as a fallback."""
    return max(1, math.ceil(len(text) / 4))


@dataclass
class Pricing:
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.input_per_mtok + completion_tokens * self.output_per_mtok) / 1e6


@dataclass
class MockMistake:
    """A scripted error.

    kind:
      neighbour_line     (extraction)  bind the line ``offset`` lines away from the right one,
                                       or the line whose label contains ``label``
      ignore_unit_rule   (calculation) treat every 13F value as dollars (or ``scale``) regardless of filing date
      wrong_scale        (calculation) apply ``scale`` to ``slot``
      swap_in_synthesis  (synthesis)   write ``use_slot``'s value (and citation) as ``slot``
                                       e.g. the COLI funding asset offered as the plan liability
      refuse / formula_only / drop (synthesis) for answer-slot scoring
    """

    kind: str
    slot: Optional[str] = None
    offset: int = -1
    label: Optional[str] = None
    scale: float = 1.0
    use_slot: Optional[str] = None
    question_id: Optional[str] = None


def _overlap(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _looks_primary(label: str) -> bool:
    return bool(re.search(r"\b(sec|edgar|10-k|10-q|13f|def 14a|filing)\b", label.lower()))


class MockModel:
    """Deterministic scripted model. Correct by default; ``mistakes`` inject specific errors.

    ``noise`` > 0 with a ``seed`` makes extraction occasionally bind the second-best line
    (used to give self-consistency / semantic-entropy monitors something to measure offline).
    """

    provider = "mock"

    def __init__(self, name: str = "mock", mistakes: Optional[list[MockMistake]] = None,
                 noise: float = 0.0, seed: int = 0, pricing: Optional[Pricing] = None,
                 query_per_slot: bool = False) -> None:
        self.name = name
        self.query_per_slot = query_per_slot
        self.mistakes = list(mistakes or [])
        self.noise = noise
        self.seed = seed
        self.pricing = pricing or Pricing()
        self.model_version = f"{name}-v1"

    def _mistakes(self, kind: str, qid: str) -> list[MockMistake]:
        return [m for m in self.mistakes if m.kind == kind and (m.question_id in (None, qid))]

    def _respond(self, request: StageRequest, payload: Any) -> ModelResponse:
        text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
        prompt = "\n".join(m["content"] for m in request.messages)
        pt, ct = approx_tokens(prompt), approx_tokens(text)
        return ModelResponse(
            text=text,
            usage=Usage(prompt_tokens=pt, completion_tokens=ct, calls=1, cost_usd=self.pricing.cost(pt, ct),
                        token_count_method="approx_chars_div_4"),
            model_version=self.model_version,
        )

    def complete(self, request: StageRequest) -> ModelResponse:
        fn = {
            Stage.retrieval: self._retrieval,
            Stage.extraction: self._extraction,
            Stage.calculation: self._calculation,
            Stage.synthesis: self._synthesis,
        }[request.stage]
        return self._respond(request, fn(request))

    def _retrieval(self, req: StageRequest) -> dict:
        q = req.context["question"]
        per_slot = [f"{s['entity']} {s['aliases'][-1]} {s['period']}" for s in q["slots"]
                    if self.query_per_slot and s["derivation"] == "reported" and s.get("aliases")]
        return {"queries": [f"{q['text']} {q['entity']} {q['period']}"] + per_slot}

    def _extraction(self, req: StageRequest) -> dict:
        q = req.context["question"]
        docs = req.context["docs"]
        rng = random.Random(f"{self.seed}:{q['id']}:{req.seed}")
        facts = []
        n = 0
        for slot in q["slots"]:
            if slot["derivation"] != "reported":
                continue
            ranked = self._rank_lines(slot, docs)
            if not ranked:
                continue
            best_i = 0
            if self.noise and len(ranked) > 1 and rng.random() < self.noise:
                best_i = 1
            doc, lines, idx = ranked[best_i][1], ranked[best_i][2], ranked[best_i][3]
            for m in self._mistakes("neighbour_line", q["id"]):
                if m.slot == slot["slot_id"]:
                    if m.label:
                        cand = [i for i, ln in enumerate(lines) if m.label.lower() in ln.label.lower()]
                        idx = cand[0] if cand else idx
                    else:
                        idx = min(max(idx + m.offset, 0), len(lines) - 1)
            line = lines[idx]
            n += 1
            facts.append({
                "fact_id": f"F{n}",
                "doc_id": doc["doc_id"],
                "quote": line.text.strip(),
                "label": line.label,
                "printed_value": line.value,
                "unit_text": unit_text_at(doc["text"], line.start),
                "slot_id": slot["slot_id"],
                "concept": slot["concept"],
                "entity": doc.get("entity") or slot["entity"],
                "period": doc.get("period") or slot["period"],
            })
        return {"facts": facts}

    def _rank_lines(self, slot: dict, docs: list[dict]) -> list[tuple]:
        """Candidate (score, doc, lines, line_idx) for a slot, best first; deterministic."""
        cands = []
        for d_i, doc in enumerate(docs):
            if doc.get("entity") and slot["entity"] and doc["entity"] != slot["entity"]:
                continue
            if doc.get("period") and slot["period"] and doc["period"] != slot["period"]:
                continue
            doc["_unit_text"] = detect_unit_text(doc["text"])
            lines = parse_table_lines(doc["text"])
            for i, ln in enumerate(lines):
                s = max([_overlap(ln.label, a) for a in slot.get("aliases", [])] + [0.0])
                if any(a.lower() == ln.label.lower() for a in slot.get("aliases", [])):
                    s += 1.0
                s += 0.05 * _looks_primary(doc.get("source_label", ""))
                if s > 0:
                    cands.append((-round(s, 6), d_i, i, doc, lines))
        cands.sort(key=lambda c: (c[0], c[1], c[2]))
        return [(-c[0], c[3], c[4], c[2]) for c in cands]

    def _calculation(self, req: StageRequest) -> dict:
        q = req.context["question"]
        facts = req.context["facts"]
        docs = {d["doc_id"]: d for d in req.context["docs"]}
        ignore = self._mistakes("ignore_unit_rule", q["id"])
        wrong = {m.slot: m.scale for m in self._mistakes("wrong_scale", q["id"])}
        units = {s["slot_id"]: s.get("unit", "USD") for s in q["slots"]}
        values = []
        by_slot = {}
        for f in facts:
            doc = docs.get(f["doc_id"], {})
            if q["filing_type"] == "13f" or (doc.get("form_type") or "").upper().startswith("13F"):
                scale = ignore[0].scale if ignore else f13_scale(doc.get("filing_date"))
            else:
                scale = scale_from_unit_text(f.get("unit_text"))
            if f.get("bound_slot") in wrong:
                scale = wrong[f["slot_id"]]
            if units.get(f.get("bound_slot"), "USD") != "USD":
                scale = 1.0
            by_slot[f.get("bound_slot")] = f
            values.append({"slot_id": f.get("bound_slot"), "formula": "x",
                           "inputs": {"x": {"fact_id": f["fact_id"], "scale": scale}}})
        for slot in q["slots"]:
            if slot["derivation"] == "computed" and slot.get("formula"):
                values.append({"slot_id": slot["slot_id"], "formula": slot["formula"],
                               "inputs": {v: {"slot_ref": s} for v, s in slot["inputs"].items()}})
        return {"values": values}

    def _synthesis(self, req: StageRequest) -> str:
        q = req.context["question"]
        facts = {f["fact_id"]: f for f in req.context["facts"]}
        values = req.context["values"]
        slot_val = {v["slot_id"]: v for v in values if v.get("slot_id")}
        slots = {s["slot_id"]: s for s in q["slots"]}
        swaps = {m.slot: m.use_slot for m in self._mistakes("swap_in_synthesis", q["id"])}
        refuse = {m.slot for m in self._mistakes("refuse", q["id"])}
        formula_only = {m.slot for m in self._mistakes("formula_only", q["id"])}
        drop = {m.slot for m in self._mistakes("drop", q["id"])}
        entity = q["entity"] + ("'" if q["entity"].endswith("s") else "'s")

        def cite(v: dict) -> str:
            if v["formula"] == "x" and len(v["inputs"]) == 1 and v["inputs"][0]["ref"] in facts:
                return v["inputs"][0]["ref"]
            return v["value_id"]

        def val_for(slot_id: str) -> Optional[dict]:
            return slot_val.get(swaps.get(slot_id, slot_id))

        def alias(slot_id: str) -> str:
            s = slots[slot_id]
            return (s.get("aliases") or [s["concept"]])[0]

        sentences, consumed = [], set()
        for slot in q["slots"]:
            sid = slot["slot_id"]
            if sid in consumed or sid in drop:
                continue
            if sid in refuse:
                sentences.append(f"The {alias(sid)} for {slot['period']} cannot be determined from the available "
                                 f"documents.")
                continue
            if sid in formula_only:
                ins = " - ".join(alias(s) for s in slot.get("inputs", {}).values()) or "the reported figures"
                sentences.append(f"The {alias(sid)} for {slot['period']} can be calculated as {ins}.")
                continue
            v = val_for(sid)
            if v is None:
                continue
            if slot["derivation"] == "computed" and len(slot.get("inputs", {})) == 2 and "-" in (slot.get("formula") or ""):
                a_var, b_var = re.findall(r"[A-Za-z_]\w*", slot["formula"])[1], re.findall(r"[A-Za-z_]\w*", slot["formula"])[0]
                a_slot, b_slot = slot["inputs"][a_var], slot["inputs"][b_var]
                va, vb = val_for(a_slot), val_for(b_slot)
                if va and vb:
                    verb = "rose" if vb["value"] >= va["value"] else "fell"
                    sentences.append(
                        f"From {slots[a_slot]['period']} to {slots[b_slot]['period']}, {entity} {alias(a_slot)} {verb} "
                        f"from {fmt_usd(va['value'])} [{cite(va)}] to {fmt_usd(vb['value'])} [{cite(vb)}], "
                        f"a change of {fmt_usd(v['value'])} [{cite(v)}]."
                    )
                    consumed |= {a_slot, b_slot}
                    continue
            shown = fmt_usd(v["value"]) if slot.get("unit", "USD") == "USD" else f"{v['value']:,.0f}"
            sentences.append(f"{entity} {alias(sid)} for {slot['period']} was {shown} [{cite(v)}].")
        out = [s for s in sentences if not any(
            s.startswith(f"{entity} {alias(c)} for ") for c in consumed)]
        return " ".join(out) if out else "No figures could be determined."


class MissingAPIKeyError(RuntimeError):
    pass


class OfflineModeError(RuntimeError):
    pass


class _HTTPChatClient:
    """Base for the HTTP API clients.

    Reasoning-model settings (checked on the vendors' docs 2026-09-28):
      * ``effort``: Anthropic ``output_config.effort`` (Opus 5.5 default medium); OpenAI Responses
        ``reasoning.effort`` (gpt-6-astra: default undocumented, no ``none``); DeepSeek ``reasoning_effort``
        (thinking on by default, default high; values low/high/max). Always set it explicitly in the config.
      * ``max_output_tokens``: a hard limit on thinking + answer, so it must be large (default 32000).
      * ``send_sampling``: temperature/seed are sent only when true; the current reasoning models do not
        document them (DeepSeek thinking mode rejects temperature), so the default is false.
      * ``json_mode``: the provider's JSON-output mode is off by default, because the CLI models (Claude Code,
        Codex) have none; every model then gets JSON only from the prompt, the same way (2026-10-02).
    """
    provider = "http"
    api_key_env = ""
    default_base_url = ""

    def __init__(self, model: str, *, name: Optional[str] = None, base_url: Optional[str] = None,
                 pricing: Optional[Pricing] = None, timeout_s: float = 600.0, max_retries: int = 3,
                 effort: Optional[str] = None, max_output_tokens: int = 32000, send_sampling: bool = False,
                 json_mode: bool = False, post: Optional[Any] = None) -> None:
        key = os.environ.get(self.api_key_env)
        if not key and post is None:
            raise MissingAPIKeyError(
                f"{type(self).__name__}: set {self.api_key_env} to use model {model!r}. "
                f"No key found, so no request was made; use the 'mock' provider for offline runs."
            )
        self._key = key or "test"
        self.model = model
        self.name = name or model
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.pricing = pricing or Pricing()
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.effort = effort
        self.max_output_tokens = max_output_tokens
        self.send_sampling = send_sampling
        self.json_mode = json_mode
        self._post_override = post          # tests inject a fake transport here

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        if os.environ.get("NUMTRACE_OFFLINE") == "1":
            raise OfflineModeError("NUMTRACE_OFFLINE=1: refusing to call a model API")
        if self._post_override is not None:
            return self._post_override(url, body, headers)
        data = json.dumps(body).encode()
        last: Optional[Exception] = None
        for attempt in range(self.max_retries):
            req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:  # pragma: no cover - network
                last = e
                if e.code not in (429, 500, 502, 503, 529):
                    raise RuntimeError(f"{self.provider} API error {e.code}: {e.read()[:500]!r}") from None
                time.sleep(2 ** attempt)
        raise RuntimeError(f"model API failed after retries: {last}")  # pragma: no cover

    def _version(self, out: dict) -> str:
        v = out.get("model") or self.model
        return f"{v} (effort={self.effort or 'default'})"


class OpenAIChatClient(_HTTPChatClient):
    """OpenAI through the Responses API (recommended for reasoning models such as gpt-6-astra)."""
    provider = "openai"
    api_key_env = "OPENAI_API_KEY"
    default_base_url = "https://api.openai.com/v1"

    def complete(self, request: StageRequest) -> ModelResponse:
        body: dict[str, Any] = {"model": self.model, "input": request.messages,
                                "max_output_tokens": self.max_output_tokens, "store": False}
        if self.effort:
            body["reasoning"] = {"effort": self.effort}
        if self.send_sampling:
            body["temperature"] = request.temperature
        if self.json_mode and request.response_format == "json":
            body["text"] = {"format": {"type": "json_object"}}
        out = self._post(f"{self.base_url}/responses", body, {"authorization": f"Bearer {self._key}"})
        text = "".join(c.get("text", "") for item in out.get("output", []) if item.get("type") == "message"
                       for c in item.get("content", []) if c.get("type") == "output_text")
        u = out.get("usage", {}) or {}
        pt, ct = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        return ModelResponse(text=text, usage=Usage(prompt_tokens=pt, completion_tokens=ct, calls=1,
                                                    cost_usd=self.pricing.cost(pt, ct)),
                             model_version=self._version(out))


class DeepSeekChatClient(_HTTPChatClient):
    """DeepSeek, OpenAI-compatible Chat Completions; thinking mode on, effort set explicitly."""
    provider = "deepseek"
    api_key_env = "DEEPSEEK_API_KEY"
    default_base_url = "https://api.deepseek.com/v1"

    def complete(self, request: StageRequest) -> ModelResponse:
        body: dict[str, Any] = {"model": self.model, "messages": request.messages,
                                "max_tokens": self.max_output_tokens, "thinking": {"type": "enabled"}}
        if self.effort:
            body["reasoning_effort"] = self.effort
        if self.send_sampling:
            body["temperature"] = request.temperature
            if request.seed is not None:
                body["seed"] = request.seed
        if self.json_mode and request.response_format == "json":
            body["response_format"] = {"type": "json_object"}
        out = self._post(f"{self.base_url}/chat/completions", body, {"authorization": f"Bearer {self._key}"})
        u = out.get("usage", {}) or {}
        pt, ct = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        return ModelResponse(text=out["choices"][0]["message"].get("content") or "",
                             usage=Usage(prompt_tokens=pt, completion_tokens=ct, calls=1,
                                         cost_usd=self.pricing.cost(pt, ct)),
                             model_version=self._version(out))


class AnthropicClient(_HTTPChatClient):
    provider = "anthropic"
    api_key_env = "ANTHROPIC_API_KEY"
    default_base_url = "https://api.anthropic.com/v1"

    def complete(self, request: StageRequest) -> ModelResponse:
        system = "\n".join(m["content"] for m in request.messages if m["role"] == "system")
        msgs = [m for m in request.messages if m["role"] != "system"]
        body: dict[str, Any] = {"model": self.model, "max_tokens": self.max_output_tokens, "messages": msgs}
        if self.effort:
            body["output_config"] = {"effort": self.effort}
        if self.send_sampling:
            body["temperature"] = request.temperature
        if system:
            body["system"] = system
        out = self._post(f"{self.base_url}/messages", body,
                         {"x-api-key": self._key, "anthropic-version": "2023-06-01"})
        text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        u = out.get("usage", {}) or {}
        pt = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0) or 0) \
            + int(u.get("cache_creation_input_tokens", 0) or 0)
        ct = int(u.get("output_tokens", 0))
        return ModelResponse(text=text, usage=Usage(prompt_tokens=pt, completion_tokens=ct, calls=1,
                                                    cost_usd=self.pricing.cost(pt, ct)),
                             model_version=self._version(out))


PROVIDERS = {"mock": MockModel, "openai": OpenAIChatClient, "anthropic": AnthropicClient,
             "deepseek": DeepSeekChatClient}


def build_model(spec: dict) -> ModelClient:
    """Build a client from a config dict, e.g.
    {"name": "gpt", "provider": "openai", "model": "gpt-4o", "pricing": {"input_per_mtok": 2.5, ...}}
    {"name": "mock-neighbour", "provider": "mock", "mistakes": [{"kind": "neighbour_line", "slot": "ltd_total", "offset": -2}]}
    """
    spec = dict(spec)
    provider = spec.pop("provider", "mock")
    pricing = Pricing(**spec.pop("pricing", {}))
    if provider == "mock":
        mistakes = [MockMistake(**m) for m in spec.pop("mistakes", [])]
        return MockModel(name=spec.pop("name", "mock"), mistakes=mistakes, noise=spec.pop("noise", 0.0),
                         seed=spec.pop("seed", 0), pricing=pricing,
                         query_per_slot=spec.pop("query_per_slot", False))
    if provider in ("claude_code", "codex"):  # local CLI logins instead of API keys (harness/cli_models.py)
        from .cli_models import CLI_PROVIDERS
        return CLI_PROVIDERS[provider](spec.pop("model"), name=spec.pop("name", None), pricing=pricing, **spec)
    cls = PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(f"unknown provider {provider!r}; choose from {sorted(PROVIDERS) + ['claude_code', 'codex']}")
    model = spec.pop("model")
    return cls(model, name=spec.pop("name", model), pricing=pricing, **spec)
