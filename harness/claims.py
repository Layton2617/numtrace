"""Claim extraction from a report and alignment of claims to expected answer slots.

``RegexClaimTagger`` is deterministic and good enough for the mock and for well-formatted
reports: every money amount in the report becomes a claim; its stated concept is the nearest
preceding concept phrase (question slot aliases + a small lexicon); its source is resolved from
the citation ([F#] extracted fact, [C#] computed value) through the trajectory log.

``LLMClaimTagger`` is the hook for an external LLM claim tagger: it asks a
model for (number, concept phrase, citation) triples and reuses the same resolution code.
"""
from __future__ import annotations

import re
from typing import Callable, Optional, Protocol

from .agent import parse_json, shown_slots, stage_output
from .periods import same_period
from .schema import (
    Claim,
    ComputedValue,
    Derivation,
    DerivationInput,
    DerivationKind,
    ExtractedFact,
    Question,
    SlotScore,
    SlotStatus,
    Stage,
    Trajectory,
)
from .tools import MoneyMention, find_counts, find_money, parse_printed_number, scale_invariant_match, values_match

DEFAULT_LEXICON: dict[str, list[str]] = {
    "us-gaap:LongTermDebtNoncurrent": ["long-term debt, net of current portion", "noncurrent long-term debt"],
    "us-gaap:LongTermDebtCurrent": ["current portion of long-term debt", "current portion"],
    "us-gaap:LongTermDebt": ["total long-term debt", "long-term debt"],
    "us-gaap:CashAndCashEquivalentsAtCarryingValue": ["cash and cash equivalents"],
    "nqdc:PlanLiability": ["deferred compensation obligation", "deferred compensation liability", "obligation",
                           "plan liability"],
    "nqdc:COLICashSurrenderValue": ["cash surrender value", "corporate-owned life insurance", "life insurance"],
    "nqdc:AggregateBalance": ["aggregate balance", "year-end balance"],
    "nqdc:ExecutiveContributions": ["executive contributions"],
    "nqdc:RegistrantContributions": ["registrant contributions", "company contributions"],
    "nqdc:AggregateEarnings": ["aggregate earnings"],
}

_CITE_RE = re.compile(r"\[(?P<id>[FC]\d+)\]")
_PERIOD_RE = re.compile(r"\b(?:(?:Q[1-4]|[369]M)\sFY\d{4}|FY\s?\d{4}|\d{4}Q[1-4]|Q[1-4]\s\d{4})\b")
_NUM_RE = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?")
_OPERATOR_RE = re.compile(r"\s*[-−+×*/]\s*")
PHRASE_WINDOW = 60       # a concept phrase overrides the citation's slot only this close before the number
OTHER_PERIOD = "other"   # a comparative read from another cell of the cited row; never aligned to a slot
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*$\d\[(])")
REFUSAL_RE = re.compile(r"\b(cannot be determined|can ?not|can[’']t|unable to|not available|not disclosed|insufficient|"
                        r"not (?:answerable|determinable)|do(?:es)? not (?:report|disclose|contain))\b", re.I)
FORMULA_RE = re.compile(r"\b(can be (?:calculated|computed|derived)|is (?:calculated|computed) as|equals?)\b|=", re.I)
_HEDGE_RE = re.compile(r"(?:\b(?:about|approximately|roughly|around)|[~≈])[\s*]*$", re.I)
# The number is a difference or comparison, not a level: "exceeded the COLI value by $117 million", "an increase of
# $33 million", "$5 million more than". "by" counts only after a verb or adjective of change ("funded by" does not).
_COMPARE_BEFORE_RE = re.compile(
    r"(?:\b(?:exceed|surpass|outstrip|increas|decreas|rose|rise|rising|fell|fall|grew|grow|declin|drop|jump|climb|"
    r"gain|lost|los|higher|lower|more|less|greater|smaller|larger)\w*\b[^.;:$\d]{0,40}?\bby"
    r"|\b(?:increase|decrease|rise|drop|decline|gain|loss|change|difference|gap|shortfall|excess|surplus)\s+of"
    r"|\b(?:up|down))(?:\s+(?:about|approximately|roughly|around))?[\s*]*[+−-]?[\s*]*$", re.I)
_COMPARE_AFTER_RE = re.compile(r"[\s*]*(?:(?:more|less|higher|lower|greater|smaller|larger)\s+than|difference|gap|"
                               r"shortfall|below|above)\b", re.I)
# A ratio or a figure the report says it worked out from what it cites: "$358 per share", "(derived from [F1])".
_DERIVED_AFTER_RE = re.compile(r"[\s*]*(?:per\s+(?:share|unit)\b|[^.;\[]{0,40}?\b(?:derived|calculated|computed)\s+"
                               r"from\b)", re.I)


class ClaimTagger(Protocol):
    def tag(self, report: str, question: Question, trajectory: Trajectory) -> list[Claim]: ...


def concept_phrases(question: Question) -> list[tuple[str, str]]:
    """(phrase, concept), longest first. Question aliases win over the default lexicon."""
    pairs: dict[str, str] = {}
    for concept, phrases in DEFAULT_LEXICON.items():
        for p in phrases:
            pairs[p.lower()] = concept
    for s in question.slots:
        for p in s.aliases:
            pairs[p.lower()] = s.concept
    return sorted(pairs.items(), key=lambda kv: -len(kv[0]))


def split_sentences(report: str) -> list[tuple[int, str]]:
    """Lines first (headings, bullets and table rows are separate units), then sentences within a line."""
    out = []
    for line in re.finditer(r"[^\n]+", report):
        text, pos = line.group(0), 0
        for part in _SENT_RE.split(text):
            i = text.find(part, pos)
            pos = i + len(part)
            if part.strip():
                out.append((line.start() + i, part))
    return out


def slot_of(bound: Optional[str], question: Question, period: Optional[str] = None) -> Optional[str]:
    """The question slot a fact or value was bound to. Some agents write the slot's concept
    ("13f:Shares[169656105]") instead of its id; that names the slot if no other slot has the concept,
    or if the period tells them apart."""
    if any(s.slot_id == bound for s in question.slots):
        return bound
    cands = [s for s in question.slots if s.concept == bound]
    if len(cands) > 1:
        cands = [s for s in cands if same_period(period, s.period, s.entity)]
    return cands[0].slot_id if len(cands) == 1 else None


def _norm_period(p: str) -> str:
    m = re.fullmatch(r"Q([1-4])\s(\d{4})", p)
    if m:
        return f"{m.group(2)}Q{m.group(1)}"
    return p if re.fullmatch(r"(?:Q[1-4]|[369]M) FY\d{4}", p) else p.replace(" ", "")


def _nearest_phrase(sentence: str, at: int, phrases: list[tuple[str, str]]) -> Optional[tuple[str, str, int]]:
    low = sentence.lower()
    best = None
    for phrase, concept in phrases:
        for m in re.finditer(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", low):
            if m.end() <= at:
                key = (m.end(), len(phrase))
                if best is None or key > best[0]:
                    best = (key, phrase, concept)
    return (best[1], best[2], best[0][0]) if best else None


class TrajectoryIndex:
    """Facts and computed values of a trajectory, by id."""

    def __init__(self, traj: Trajectory) -> None:
        self.facts: dict[str, ExtractedFact] = {}
        self.values: dict[str, ComputedValue] = {}
        if traj.steps_for(Stage.extraction):
            self.facts = {f.fact_id: f for f in stage_output(traj, Stage.extraction)}
        if any("values" in s.output for s in traj.steps_for(Stage.calculation)):
            self.values = {v.value_id: v for v in stage_output(traj, Stage.calculation)}

    def is_component(self, f: ExtractedFact) -> bool:
        """The fact is an input to a computed value for its own slot (e.g. one 13F row of a filer total)."""
        return any(v.slot_id == f.bound_slot and len(v.inputs) > 1 and f.fact_id in {i.ref for i in v.inputs}
                   for v in self.values.values())


def resolve_claim(*, idx: int, sentence: str, mention: MoneyMention, citation: Optional[str],
                  concept: Optional[tuple[str, str]], question: Question, traj: Trajectory,
                  tindex: TrajectoryIndex, phrase_end: Optional[int] = None, derived: bool = False) -> Claim:
    period = None
    source = None
    cite_slot = None
    # derived: a sum or difference of what it cites, operation not stated (no formula)
    derivation = Derivation(kind=DerivationKind.computed if derived else DerivationKind.reported)
    if citation and citation in tindex.facts:
        f = tindex.facts[citation]
        if _other_cell(mention, f):
            period = OTHER_PERIOD
        else:
            source, period, cite_slot = f.span, f.period, slot_of(f.bound_slot, question, f.period)
            if tindex.is_component(f):
                cite_slot, concept = None, None    # one row of a total the agent computed, not the answer
            scale = mention.value / f.printed_value if f.printed_value else None
            derivation = Derivation(kind=DerivationKind.reported, formula="x", inputs=[DerivationInput(
                ref=f.fact_id, value=mention.value, printed_value=f.printed_value, scale=scale,
                concept=f.bound_concept, period=f.period, doc_id=f.doc_id, span=f.span)])
    elif citation and citation in tindex.values:
        v = tindex.values[citation]
        period, cite_slot = v.period, slot_of(v.slot_id, question, v.period)
        is_identity = v.formula.strip() == "x" and len(v.inputs) == 1 and v.inputs[0].span is not None
        if is_identity:
            source = v.inputs[0].span
            derivation = Derivation(kind=DerivationKind.reported, formula="x", inputs=list(v.inputs))
        else:
            derivation = Derivation(kind=DerivationKind.computed, formula=v.formula, inputs=list(v.inputs))
    if period is None:
        pm = {_norm_period(p) for p in _PERIOD_RE.findall(sentence)}
        period = pm.pop() if len(pm) == 1 else question.period
    # The report's own concept phrase wins when it names one of the question's quantities; otherwise the
    # citation tells what the number is (the agent's own binding), so the report is not penalised for wording.
    slot_concepts = {s.slot_id: s.concept for s in question.slots}
    # Only a cited *fact* can be re-bound by the report's words (walk-through 3); a computed value carries the
    # slot the calculation stage assigned it.
    near = phrase_end is not None and mention.start - phrase_end <= PHRASE_WINDOW and citation in tindex.facts
    if near and concept and _in_cited_label(sentence, phrase_end - len(concept[0]), tindex.facts[citation]):
        # the phrase is part of the cited line's own name as the report repeats it ("current maturities of long-term
        # debt"): it names that line, not another quantity
        near = False
        if not cite_slot:
            concept = None
    if cite_slot and concept and concept[1] in slot_concepts.values() and near:
        if concept[1] != slot_concepts[cite_slot]:
            cite_slot = None     # the report names a different quantity: align by its words (walk-through 3)
    elif cite_slot:
        concept = (concept[0] if concept else None, slot_concepts[cite_slot])
    return Claim(
        claim_id=f"{traj.trajectory_id}:c{idx}", question_id=question.id, trajectory_id=traj.trajectory_id,
        text=sentence.strip(), number_text=mention.text, value=mention.value, rel_tol=mention.rel_tol,
        entity=question.entity, concept_phrase=concept[0] if concept else None,
        concept_as_stated=concept[1] if concept else None, period=period, derivation=derivation, source=source,
        citation=citation, slot_id=cite_slot)


def _in_cited_label(sentence: str, at: int, f: ExtractedFact) -> bool:
    label = (f.span.text if f.span else "").split("|")[0].strip().lower()
    if len(label) < 4:
        return False
    low = sentence.lower()
    return any(m.start() <= at < m.end() for m in re.finditer(re.escape(label), low))


def _other_cell(mention: MoneyMention, f: ExtractedFact) -> bool:
    """The number is not the cited fact's but another cell of the cited row (a prior-period comparative)."""
    if scale_invariant_match(mention.value, f.printed_value, mention.rel_tol) is not None:
        return False
    for m in _NUM_RE.finditer(f.span.text):
        try:
            n = parse_printed_number(m.group(0))
        except ValueError:
            continue
        if n != f.printed_value and scale_invariant_match(mention.value, n, mention.rel_tol) is not None:
            return True
    return False


_SCALE_AFTER = re.compile(r"\)?\s?(thousand|million|billion|trillion)\b", re.I)
_SCALED = re.compile(r"(?:thousand|million|billion|trillion|[kKmMbB]n?)$")
_SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}


def _half(m: MoneyMention) -> float:
    """Half a unit of the mention's last printed digit, in base units (a zero has no relative precision)."""
    if m.value:
        return m.rel_tol * abs(m.value)
    n = re.search(r"\d[\d,]*(?:\.(\d+))?", m.text)
    w = re.search(r"(thousand|million|billion|trillion)", m.text, re.I)
    return 0.5 * 10.0 ** -len((n[1] if n else "") or "") * (_SCALES[w[1].lower()] if w else 1.0)


def _complete(m: MoneyMention, sent: str) -> MoneyMention:
    """The scale word after a count or a parenthesised amount ("429 million shares", "($47.26) billion") and an
    en-dash minus ("–$0.02 million") belong to the number."""
    if not _SCALED.search(m.text) and (w := _SCALE_AFTER.match(sent, m.end)):
        scale = _SCALES[w[1].lower()]
        rel = m.rel_tol or (0.5 / abs(m.value) if m.value else 0.0)
        m = MoneyMention(text=sent[m.start:w.end()], value=m.value * scale, rel_tol=rel, start=m.start, end=w.end())
    if m.start > 0 and sent[m.start - 1] == "–" and (m.start == 1 or sent[m.start - 2] in " (*") and m.value > 0:
        m = MoneyMention(text=sent[m.start - 1:m.end], value=-m.value, rel_tol=m.rel_tol, start=m.start - 1, end=m.end)
    return m


_DECIMAL_COUNT_RE = re.compile(r"(?<![\w$.,])(\d+\.\d+)\s+(thousand|million|billion)\s+(?:[A-Za-z-]+\s+){0,3}?shares\b",
                               re.I)


def _decimal_counts(sent: str) -> list[MoneyMention]:
    """"496.9 million shares": find_counts reads integers only."""
    out = []
    for m in _DECIMAL_COUNT_RE.finditer(sent):
        scale, dec = _SCALES[m[2].lower()], len(m[1].partition(".")[2])
        v = float(m[1]) * scale
        out.append(MoneyMention(text=sent[m.start(1):m.end(2)], value=v, rel_tol=0.5 * 10.0 ** -dec * scale / v,
                                start=m.start(1), end=m.end(2)))
    return out


_PERCENT_RE = re.compile(r"(?<![\w.,$])(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.I)


def _percents(sent: str) -> list[MoneyMention]:
    """"up to **50%** of base salary": for percent slots (NQDC deferral limits)."""
    out = []
    for m in _PERCENT_RE.finditer(sent):
        v, dec = float(m[1]), len(m[1].partition(".")[2])
        out.append(MoneyMention(text=m[0], value=v, rel_tol=0.5 * 10.0 ** -dec / v if v else 0.0, start=m.start(),
                                end=m.end()))
    return out


def _mentions(sent: str, counts: bool, percents: bool = False) -> list[MoneyMention]:
    """Money (and, for share/count slots, counts; for percent slots, percentages); a restatement in parentheses right after a number
    ("$2.13 billion ($2,130 million)") is merged into one mention at the finer precision."""
    ms = sorted((_complete(m, sent) for m in find_money(sent) + (find_counts(sent) + _decimal_counts(sent)
                                                                   if counts else []) +
                                              (_percents(sent) if percents else [])),
                key=lambda m: m.start)
    out: list[MoneyMention] = []
    for m in ms:
        p = out[-1] if out else None
        if p and re.fullmatch(r"\s*\(\s*", sent[p.end:m.start]) and \
                values_match(p.value, m.value, rel_tol=max(p.rel_tol, m.rel_tol) * 1.0001, abs_tol=max(_half(p), _half(m), 0.5)):
            keep = m if _half(m) < _half(p) else p
            out[-1] = MoneyMention(text=sent[p.start:m.end], value=keep.value, rel_tol=keep.rel_tol,
                                   start=p.start, end=m.end)
        else:
            out.append(m)
    return out


def _holds(cite: str, m: MoneyMention, tindex: TrajectoryIndex) -> bool:
    if cite in tindex.facts:
        return scale_invariant_match(m.value, tindex.facts[cite].printed_value, m.rel_tol) is not None
    return cite in tindex.values and scale_invariant_match(m.value, tindex.values[cite].value, m.rel_tol) is not None


def _citation(m: MoneyMention, cites: list[str], before: str, after: str,
              tindex: TrajectoryIndex) -> tuple[Optional[str], bool]:
    """(citation, derived). The citation is the first one that holds the number, preferring one whose quote was
    found in a document; else a fact whose row prints it in another cell. A number none of its citations holds is
    derived from them (a sum or difference) when it cites several, or when the wording makes it a comparison."""
    held = [c for c in cites if _holds(c, m, tindex)]
    if held:
        located = [c for c in held if c not in tindex.facts or tindex.facts[c].span.start >= 0]
        return (located or held)[0], False
    other = [c for c in cites if c in tindex.facts and _other_cell(m, tindex.facts[c])]
    if other:
        return other[0], False
    if len(cites) > 1 or _COMPARE_BEFORE_RE.search(before) or _COMPARE_AFTER_RE.match(after) or \
            _DERIVED_AFTER_RE.match(after):
        return None, True
    return (cites[0] if cites else None), False


def _dedupe(claims: list[Claim], tindex: TrajectoryIndex) -> list[Claim]:
    """One claim per number (guide 6.13): a repeat from the same source, or an unsourced repeat of any claimed
    value, is dropped. An identity value [C#] = x([F#]) has its fact's source; a number its citation does not hold
    (another cell of the cited row) has none."""
    def same(a: Claim, b: Claim) -> bool:
        return values_match(a.value, b.value, rel_tol=max(a.rel_tol, b.rel_tol) * 1.0001)

    def source(c: Claim) -> Optional[str]:
        if c.period == OTHER_PERIOD:
            return None
        v = tindex.values.get(c.citation)
        return v.inputs[0].ref if v and v.formula.strip() == "x" and len(v.inputs) == 1 else c.citation
    sourced = [c for c in claims if source(c)]
    out: list[Claim] = []
    for c in claims:
        if source(c) and any(source(o) == source(c) and same(o, c) for o in out):
            continue
        if not source(c) and any(same(o, c) for o in sourced + out):
            continue
        out.append(c)
    return out


class RegexClaimTagger:
    def tag(self, report: str, question: Question, trajectory: Trajectory) -> list[Claim]:
        phrases = concept_phrases(question)
        tindex = TrajectoryIndex(trajectory)
        claims: list[Claim] = []
        counts = any(s.unit in ("shares", "count") for s in question.slots)
        percents = any(s.unit == "percent" for s in question.slots)
        for _, sent in split_sentences(report):
            ms = _mentions(sent, counts, percents)
            # Operands of an arithmetic aside ("$3.08 billion ($6.56 billion − $3.49 billion) [C3]") are not
            # claims; the citation after them belongs to the result.
            operand = [(k > 0 and bool(_OPERATOR_RE.fullmatch(sent[ms[k - 1].end:m.start]))) or
                       (k + 1 < len(ms) and bool(_OPERATOR_RE.fullmatch(sent[m.end:ms[k + 1].start])))
                       for k, m in enumerate(ms)]
            mentions = [m for m, op in zip(ms, operand) if not op]
            for j, m in enumerate(mentions):
                nxt = mentions[j + 1].start if j + 1 < len(mentions) else len(sent)
                if _HEDGE_RE.search(sent[:m.start]):     # "about $3.08 billion": +-1 in the last digit (guide 6.1)
                    m = MoneyMention(text=m.text, value=m.value, rel_tol=2 * m.rel_tol, start=m.start, end=m.end)
                cites = list(dict.fromkeys(_CITE_RE.findall(sent, m.end, nxt)))
                citation, derived = _citation(m, cites, sent[:m.start], sent[m.end:nxt], tindex)
                found = _nearest_phrase(sent, m.start, phrases)
                # a phrase before an earlier number names that number: it does not override this one's citation
                own = found is not None and (j == 0 or found[2] >= mentions[j - 1].end)
                claims.append(resolve_claim(idx=len(claims) + 1, sentence=sent, mention=m, citation=citation,
                                            concept=found[:2] if found else None, question=question,
                                            traj=trajectory, tindex=tindex, phrase_end=found[2] if own else None,
                                            derived=derived))
        return _dedupe(claims, tindex)


class LLMClaimTagger:
    """Model-based tagger. ``llm`` maps a prompt to a JSON string:
    {"claims": [{"number_text": "$4.12 billion", "concept_phrase": "total long-term debt", "citation": "F1"}]}
    Plug in an external tagger by wrapping it as such a callable."""

    PROMPT = ("List every numerical claim in the report. For each give the number exactly as written, the phrase "
              "naming what the number is, and the bracketed citation after it (or null). Return JSON "
              '{{"claims": [{{"number_text": "...", "concept_phrase": "...", "citation": "F1"}}]}}.\n\nREPORT:\n{report}')

    def __init__(self, llm: Callable[[str], str]) -> None:
        self.llm = llm

    def tag(self, report: str, question: Question, trajectory: Trajectory) -> list[Claim]:
        items = parse_json(self.llm(self.PROMPT.format(report=report))).get("claims", [])
        phrases = dict(concept_phrases(question))
        tindex = TrajectoryIndex(trajectory)
        out = []
        for it in items:
            ms = find_money(it.get("number_text", ""))
            if not ms:
                continue
            sent = next((s for _, s in split_sentences(report) if it["number_text"] in s), it["number_text"])
            cp = (it.get("concept_phrase") or "").lower()
            found = _nearest_phrase(sent, sent.find(it["number_text"]), list(phrases.items()))
            concept = (cp, phrases[cp]) if cp in phrases else (found[:2] if found else None)
            out.append(resolve_claim(idx=len(out) + 1, sentence=sent, mention=ms[0], citation=it.get("citation"),
                                     concept=concept, question=question, traj=trajectory, tindex=tindex))
        return out


def align_claims(claims: list[Claim], question: Question) -> list[Claim]:
    """Assign each claim to at most one expected answer slot: first through its citation (the slot the agent
    bound the cited fact or value to), then by stated concept and period to a slot not yet taken."""
    taken = {c.slot_id for c in claims if c.slot_id}
    out = []
    for c in claims:
        slot_id = c.slot_id
        if slot_id is None and c.period != OTHER_PERIOD:
            # a derived number (a difference, a comparison) may answer a computed slot, never a reported level
            cands = [s for s in question.slots if s.concept == c.concept_as_stated and
                     (c.derivation.kind == DerivationKind.reported or s.derivation == DerivationKind.computed)]
            if len(cands) > 1:
                cands = [s for s in cands if same_period(c.period, s.period, s.entity)] or cands
            free = [s for s in cands if s.slot_id not in taken]
            slot_id = free[0].slot_id if free else None
            if slot_id:
                taken.add(slot_id)
        out.append(c.model_copy(update={"slot_id": slot_id}))
    return out


def score_slots(report: str, claims: list[Claim], question: Question, trajectory_id: str) -> list[SlotScore]:
    """answered | formula_only | missing | refused for every slot the question asks for. A hidden slot (an input the
    question does not name) is still scored as a claim when the report states it, but never counts as missing."""
    sentences = [s for _, s in split_sentences(report)]
    shown = shown_slots(question)
    asked = set(shown.values())
    out = []
    for slot in (s for s in question.slots if s.slot_id in asked):
        ids = [c.claim_id for c in claims if c.slot_id == slot.slot_id]
        if ids:
            status = SlotStatus.answered
        else:
            aliases = [a.lower() for a in slot.aliases] or [slot.concept.lower()]
            # the report may name the slot by the id it was shown ("position1_value_change: cannot be determined")
            names = [n.lower() for n, sid in shown.items() if sid == slot.slot_id and n != slot.slot_id]
            mentions = [s for s in sentences if any(a in s.lower() for a in aliases + names)]
            shared = any(set(aliases) & {a.lower() for a in o.aliases} for o in question.slots if o is not slot)
            if shared:
                pat = re.compile(r"(?<![\w-])" + re.escape(slot.period) + r"(?![\w-])", re.I)
                mentions = [s for s in mentions if pat.search(s)]
            if any(REFUSAL_RE.search(s) for s in mentions):
                status = SlotStatus.refused
            elif any(FORMULA_RE.search(s) and not find_money(s) for s in mentions):
                status = SlotStatus.formula_only
            elif not claims and REFUSAL_RE.search(report):
                status = SlotStatus.refused      # no number at all and a refusal ("None of the four slots can be answered")
            else:
                status = SlotStatus.missing
        out.append(SlotScore(question_id=question.id, trajectory_id=trajectory_id, slot_id=slot.slot_id,
                             status=status, claim_ids=ids))
    return out


def extract_and_align(report: str, question: Question, trajectory: Trajectory,
                      tagger: Optional[ClaimTagger] = None) -> tuple[list[Claim], list[SlotScore]]:
    tagger = tagger or RegexClaimTagger()
    claims = align_claims(tagger.tag(report, question, trajectory), question)
    return claims, score_slots(report, claims, question, trajectory.trajectory_id)


__all__ = ["ClaimTagger", "RegexClaimTagger", "LLMClaimTagger", "align_claims", "score_slots", "extract_and_align"]
