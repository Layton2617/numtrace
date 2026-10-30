"""Real models return nulls and odd shapes; the agent must log them and go on, and one failure must not
end a run (first GPT-6 Astra pilot run, 2026-09-28: a fact with "quote": null crashed extraction)."""
import json

from harness.agent import AgentConfig, DeepResearchAgent, _as_number, stage_output
from harness.models import ModelResponse
from harness.schema import Stage, Usage


def test_as_number():
    assert _as_number("2,130") == 2130 and _as_number("(1,234)") == -1234 and _as_number("$1.5") == 1.5
    assert _as_number(None) is None and _as_number("not found") is None and _as_number(True) is None


class Scripted:
    name = "scripted"

    def __init__(self, extraction, calculation):
        self.extraction, self.calculation = extraction, calculation

    def complete(self, req):
        body = {Stage.retrieval: {"queries": ["long-term debt"]}, Stage.extraction: self.extraction,
                Stage.calculation: self.calculation}.get(req.stage)
        text = json.dumps(body) if body is not None else "No figure found."
        return ModelResponse(text=text, usage=Usage(prompt_tokens=1, completion_tokens=1, calls=1))


def test_null_quote_and_value_are_logged_not_fatal(corpus, questions):
    q = next(iter(questions.values()))
    doc = corpus.docs[0]
    extraction = {"facts": [
        {"fact_id": "F1", "doc_id": doc.doc_id, "quote": None, "printed_value": "1,234", "slot_id": q.slots[0].slot_id},
        {"fact_id": "F2", "doc_id": doc.doc_id, "quote": None, "printed_value": None, "slot_id": "x"},
        "garbage"]}
    calculation = {"values": [{"slot_id": q.slots[0].slot_id, "formula": "x", "inputs": {"x": {"fact_id": "F1"}}},
                              {"slot_id": "y", "formula": "a - b", "inputs": {"a": {"fact_id": "F2"}}}, "junk"]}
    t = DeepResearchAgent(Scripted(extraction, calculation), corpus, AgentConfig(model="scripted")).run(q)
    ext = next(s for s in t.steps if s.stage == Stage.extraction and s.tool == "model")
    assert [f["fact_id"] for f in ext.output["facts"]] == ["F1"] and len(ext.output["unusable"]) == 2
    assert any(s.tool == "arith_error" for s in t.steps)          # a - b with no inputs: logged, not raised
    assert [v.value for v in stage_output(t, Stage.calculation)] == [1234.0]


def test_run_continues_after_a_failed_question(tmp_path, monkeypatch):
    from harness import run_pilot
    real = run_pilot.DeepResearchAgent.run
    calls = {"n": 0}

    def flaky(self, q, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("model returned nonsense")
        return real(self, q, **kw)
    monkeypatch.setattr(run_pilot.DeepResearchAgent, "run", flaky)
    out = run_pilot.run(run_pilot.load_config(None), ["mock"], out_dir=str(tmp_path), n_questions=3, quiet=True)
    meta = json.loads((out / "run.json").read_text())
    assert len(meta["failed"]) == 1 and "nonsense" in meta["failed"][0]["error"]
    assert len((out / "reports.jsonl").read_text().splitlines()) == 2
