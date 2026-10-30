"""CLI-backed model clients, tested with a fake runner (no CLI is called)."""
import json

import pytest

from harness.agent import AgentConfig, DeepResearchAgent
from harness.cli_models import (CLIError, CLIToolUseError, ClaudeCodeCLIClient, CodexCLIClient, MissingCLIError,
                                RunResult, parse_codex_events)
from harness.models import MissingAPIKeyError, OfflineModeError, Pricing, StageRequest, build_model
from harness.schema import Stage

REQ = StageRequest(stage=Stage.retrieval, messages=[{"role": "system", "content": "SYS"},
                                                    {"role": "user", "content": "USER PROMPT"}])


class FakeRunner:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode, self.calls = stdout, returncode, []

    def __call__(self, cmd, stdin, cwd, timeout):
        self.calls.append((list(cmd), stdin, cwd))
        if "--version" in cmd:
            return RunResult(0, "9.9.9 (fake)\n", "")
        out = self.stdout(cmd, stdin) if callable(self.stdout) else self.stdout
        return RunResult(self.returncode, out, "boom")


CLAUDE_OK = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": '{"queries": ["a"]}',
                        "num_turns": 1, "total_cost_usd": 0.12,
                        "usage": {"input_tokens": 100, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0,
                                  "output_tokens": 50},
                        "modelUsage": {"claude-opus-5-5": {"inputTokens": 1000}}})


def test_claude_command_stdin_and_parsing():
    run = FakeRunner(CLAUDE_OK)
    c = ClaudeCodeCLIClient("claude-opus-5-5", name="opus", effort="high", runner=run,
                            pricing=Pricing(input_per_mtok=4, output_per_mtok=20))
    r = c.complete(REQ)
    cmd, stdin, cwd = run.calls[-1]
    assert cmd[:2] == ["claude", "-p"] and "--output-format" in cmd and "json" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"
    assert cmd[cmd.index("--tools") + 1] == "" and "--safe-mode" in cmd and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert stdin == "USER PROMPT" and "SYS" not in stdin
    assert r.text == '{"queries": ["a"]}'
    assert r.usage.prompt_tokens == 1000 and r.usage.completion_tokens == 50
    assert r.usage.token_count_method == "cli_reported"
    assert abs(r.usage.cost_usd - (1000 * 4 + 50 * 20) / 1e6) < 1e-12
    assert r.model_version == "claude-opus-5-5 via claude 9.9.9 (fake)"
    assert c.call_log[-1]["cli_cost_usd"] == 0.12


def test_claude_error_and_extra_turns():
    err = json.dumps({"subtype": "error_max_turns", "is_error": True, "result": "limit"})
    with pytest.raises(CLIError):
        ClaudeCodeCLIClient("m", runner=FakeRunner(err), max_retries=0).complete(REQ)
    multi = json.loads(CLAUDE_OK)
    multi["num_turns"] = 3
    c = ClaudeCodeCLIClient("m", runner=FakeRunner(json.dumps(multi)), on_tool_use="fail")
    with pytest.raises(CLIToolUseError):
        c.complete(REQ)


def _codex_out(tools=()):
    ev = [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"}]
    ev += [{"type": "item.completed", "item": {"id": f"i{k}", "type": t}} for k, t in enumerate(tools)]
    ev += [{"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": '{"queries": ["b"]}'}},
           {"type": "turn.completed", "usage": {"input_tokens": 2000, "cached_input_tokens": 1500,
                                                 "output_tokens": 80, "reasoning_output_tokens": 40}}]
    return "\n".join(json.dumps(e) for e in ev) + "\n"


def test_codex_command_stdin_and_parsing():
    run = FakeRunner(_codex_out())
    c = CodexCLIClient("gpt-6-astra", effort="medium", runner=run, pricing=Pricing(10, 50))
    r = c.complete(REQ)
    cmd, stdin, cwd = run.calls[-1]
    assert cmd[:3] == ["codex", "exec", "--json"] and cmd[-1] == "-"
    assert cmd[cmd.index("--sandbox") + 1] == "read-only" and "--ephemeral" in cmd and "--ignore-user-config" in cmd
    assert cmd[cmd.index("--cd") + 1] == cwd
    assert 'model_reasoning_effort="medium"' in cmd
    assert stdin.startswith("SYS") and stdin.endswith("USER PROMPT")
    assert r.text == '{"queries": ["b"]}' and r.usage.prompt_tokens == 2000 and r.usage.completion_tokens == 80
    assert c.call_log[-1]["cached_input_tokens"] == 1500 and c.call_log[-1]["tools_used"] == []


def test_codex_separate_home():
    run = FakeRunner(_codex_out())
    CodexCLIClient("gpt-6-astra", codex_home="/tmp/numtrace-codex", runner=run).complete(REQ)
    assert run.calls[-1][0][:3] == ["env", "CODEX_HOME=/tmp/numtrace-codex", "codex"]


def test_codex_tool_use_and_failures():
    with pytest.warns(UserWarning):
        CodexCLIClient("m", runner=FakeRunner(_codex_out(["command_execution"]))).complete(REQ)
    with pytest.raises(CLIToolUseError):
        CodexCLIClient("m", runner=FakeRunner(_codex_out(["web_search"])), on_tool_use="fail").complete(REQ)
    with pytest.raises(CLIError):
        parse_codex_events(json.dumps({"type": "turn.failed", "error": {"message": "x"}}))
    with pytest.raises(CLIError):
        CodexCLIClient("m", runner=FakeRunner("", returncode=1), max_retries=0).complete(REQ)


def test_offline_guard_and_missing_binary(monkeypatch):
    c = ClaudeCodeCLIClient("m", runner=FakeRunner(CLAUDE_OK))
    monkeypatch.setenv("NUMTRACE_OFFLINE", "1")
    with pytest.raises(OfflineModeError):
        c.complete(REQ)
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(MissingCLIError):
        build_model({"provider": "codex", "model": "gpt-6-astra", "name": "gpt-cli"})
    assert issubclass(MissingCLIError, MissingAPIKeyError)   # run_pilot skips such a model cleanly


def test_agent_runs_end_to_end_through_a_cli_client(corpus, questions):
    """The agent's four stages go through the CLI client unchanged (fake CLI answering each stage in JSON)."""
    def answer(cmd, stdin):
        if "search queries" in stdin:
            body = {"queries": ["Acme long-term debt"]}
        elif "quote the single line" in stdin:
            body = {"facts": []}
        elif "calculation" in stdin.lower() or "compute" in stdin.lower():
            body = {"values": []}
        else:
            body = {"report": "No figures found."}
        return json.dumps({"subtype": "success", "result": json.dumps(body), "num_turns": 1,
                           "usage": {"input_tokens": 10, "output_tokens": 5}})
    m = ClaudeCodeCLIClient("claude-opus-5-5", name="opus-cli", runner=FakeRunner(answer))
    q = next(iter(questions.values()))
    t = DeepResearchAgent(m, corpus, AgentConfig(model=m.name)).run(q)
    assert [s.stage for s in t.steps][0] == Stage.retrieval and t.usage.calls >= 2
    assert t.model_version.startswith("claude-opus-5-5 via claude")
