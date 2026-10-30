import os

import pytest

from harness.agent import AgentConfig, DeepResearchAgent, StageOverrideError, stage_output, swap_overrides
from harness.injection import build_attacked
from harness.models import (AnthropicClient, DeepSeekChatClient, MissingAPIKeyError, MockModel, OpenAIChatClient,
                            build_model)
from harness.schema import Stage, StageAttribution, StageOverrides, Trajectory, Verdict
from harness.tools import ArithmeticError_, fmt_usd, safe_eval


def test_every_stage_logged_and_tokens_counted(run, corpus, questions, gold):
    traj, _, _ = run(corpus, questions["q-13f-gamma-nvda"], gold)
    stages = [s.stage for s in traj.steps]
    assert stages[0] == Stage.retrieval and stages[-1] == Stage.synthesis
    assert {Stage.retrieval, Stage.extraction, Stage.calculation, Stage.synthesis} == set(stages)
    assert [s.step_id for s in traj.steps] == list(range(len(traj.steps)))
    tool_steps = [s for s in traj.steps if s.tool == "arith"]
    assert len(tool_steps) == 3
    assert traj.usage.calls == 4 and traj.usage.prompt_tokens > 0 and traj.usage.completion_tokens > 0
    assert traj.usage.token_count_method == "approx_chars_div_4"
    assert Trajectory.model_validate_json(traj.model_dump_json()) == traj


def test_config_hash_stable_and_sensitive():
    a, b = AgentConfig(model="m"), AgentConfig(model="m")
    assert a.config_hash() == b.config_hash()
    assert AgentConfig(model="m", code_tool=True).config_hash() != a.config_hash()


def test_only_one_stage_may_be_substituted(corpus, questions):
    q = questions["q-stmt-acme-ltd"]
    agent = DeepResearchAgent(MockModel(), corpus)
    t = agent.run(q)
    ov = StageOverrides(retrieval=stage_output(t, Stage.retrieval), synthesis="x")
    with pytest.raises(StageOverrideError):
        agent.run(q, ov)


def test_paired_retrieval_swap_locates_the_effect(corpus, questions, run, gold):
    """Attacked run fails; the same run with the clean retrieved set passes -> effect sits at retrieval."""
    q = questions["q-13f-delta-aapl"]
    att, _ = build_attacked(corpus, q, "value_shift", seed=1, label="swapped", primary="absent")
    clean_t, _, _ = run(corpus, q, gold)
    att_t, att_scored, _ = run(att, q, gold)
    assert att_scored[0].verdicts.value == Verdict.fail
    swap_t, swap_scored, _ = run(att, q, gold, overrides=swap_overrides(clean_t, "retrieval"))
    assert swap_t.overrides == ["retrieval"] and swap_t.steps[0].overridden
    assert swap_t.corpus_snapshot_id == att_t.corpus_snapshot_id
    assert swap_scored[0].verdicts.value == Verdict.pass_


def test_extracted_span_swap_repairs_walkthrough_1(corpus, questions, run, gold, walkthrough_mistakes):
    q = questions["q-stmt-acme-ltd"]
    clean_t, _, _ = run(corpus, q, gold)
    t, scored, _ = run(corpus, q, gold, walkthrough_mistakes[q.id],
                       overrides=swap_overrides(clean_t, "extraction", slot_id="ltd_total"))
    ext = [s for s in t.steps if s.stage == Stage.extraction][0]
    assert ext.overridden and ext.input["patched_slots"] == ["ltd_total"]
    assert scored[0].verdicts.binding == Verdict.pass_


def test_calculation_swap_repairs_walkthrough_2(corpus, questions, run, gold, walkthrough_mistakes):
    q = questions["q-13f-gamma-nvda"]
    clean_t, _, _ = run(corpus, q, gold)
    t, scored, _ = run(corpus, q, gold, walkthrough_mistakes[q.id], overrides=swap_overrides(clean_t, "calculation"))
    assert all(s.verdicts.binding == Verdict.pass_ for s in scored)


def test_synthesis_swap_and_unswapped_stage_is_still_wrong(corpus, questions, run, gold, walkthrough_mistakes):
    q = questions["q-nqdc-acme-liability"]
    bad_t, _, _ = run(corpus, q, gold, walkthrough_mistakes[q.id])
    _, scored, _ = run(corpus, q, gold, walkthrough_mistakes[q.id], overrides=swap_overrides(bad_t, "calculation"))
    s = next(x for x in scored if x.claim.slot_id == "liability")
    assert s.verdicts.binding == Verdict.fail and s.provenance.stage_first_visible == StageAttribution.synthesis


@pytest.mark.parametrize("cls,env", [(OpenAIChatClient, "OPENAI_API_KEY"), (AnthropicClient, "ANTHROPIC_API_KEY"),
                                     (DeepSeekChatClient, "DEEPSEEK_API_KEY")])
def test_real_adapters_need_keys(monkeypatch, cls, env):
    monkeypatch.delenv(env, raising=False)
    with pytest.raises(MissingAPIKeyError, match=env):
        cls("some-model")


def test_build_model_switches_by_config(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(build_model({"provider": "mock", "name": "m"}), MockModel)
    with pytest.raises(MissingAPIKeyError):
        build_model({"provider": "openai", "model": "gpt-4o"})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("NUMTRACE_OFFLINE", "1")
    client = build_model({"provider": "openai", "model": "gpt-4o"})
    assert client.name == "gpt-4o"
    from harness.models import OfflineModeError, StageRequest
    with pytest.raises(OfflineModeError):
        client.complete(StageRequest(stage=Stage.retrieval, messages=[{"role": "user", "content": "hi"}]))


def test_safe_eval():
    assert safe_eval("b - a", {"a": 2, "b": 5}) == 3
    assert safe_eval("round(x * 1000, 0)", {"x": 1.2345}) == 1234.0
    for bad in ("__import__('os')", "a.b", "[1][0]", "x if x else 1", "open('f')"):
        with pytest.raises(ArithmeticError_):
            safe_eval(bad, {"x": 1, "a": 1})


def test_code_tool_is_off_by_default_and_runs_when_enabled(corpus):
    assert DeepResearchAgent(MockModel(), corpus).code_tool is None
    agent = DeepResearchAgent(MockModel(), corpus, AgentConfig(model="mock", code_tool=True))
    assert agent.code_tool.run("print(2 + 3)") == "5"


def test_fmt_usd():
    assert fmt_usd(4.12e9) == "$4.12 billion"
    assert fmt_usd(-36_567_000) == "-$36.6 million"
    assert fmt_usd(25_000) == "$25,000"
