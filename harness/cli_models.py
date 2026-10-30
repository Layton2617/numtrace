"""Model clients that call the Claude Code and Codex command-line tools instead of the HTTP APIs.

Purpose: run the harness on a local machine with the models behind logged-in Claude Code and Codex
CLIs. Each stage of the agent is ONE non-interactive CLI call used as a plain text model:
  * Claude Code: ``claude -p --output-format json --system-prompt ... --tools "" --safe-mode`` (all tools
    removed, default system prompt replaced, no session saved), prompt on stdin. ``--safe-mode`` is needed:
    without it the user's global ~/.claude/CLAUDE.md and the account's claude.ai MCP connectors are loaded
    even in an empty directory (probed 2026-09-28, Claude Code 2.1.284).
  * Codex: ``codex exec --json --sandbox read-only --ephemeral --skip-git-repo-check --ignore-user-config
    --cd <empty dir> -``, prompt on stdin. ``--ignore-user-config`` drops the MCP servers and plugins of
    ~/.codex/config.toml, but the global $CODEX_HOME/AGENTS.md is still read (probed 2026-09-28, codex-cli
    0.157.1); set ``codex_home`` to a separate CODEX_HOME with its own login to keep it out. Codex has
    no flag that removes its tools or replaces its system prompt, so the harness's system text is prepended to the prompt, the call runs read-only in an empty directory,
    and any tool use seen in the event stream is recorded (``on_tool_use = "warn"`` or ``"fail"``).
Both run in a fresh empty working directory, so no project CLAUDE.md / AGENTS.md is picked up there.

What differs from the API clients (write it in the methods section when these runs are reported):
temperature and seed cannot be set; the CLI's own version is part of the configuration (logged as
``model_version``); token counts are the CLI's own; ``cost_usd`` is the API-equivalent cost from the
configured prices (an upper bound: cached input is priced as uncached), not what the subscription costs.

Flags checked against the vendors' CLI references on 2026-09-28:
  https://code.claude.com/docs/en/cli-reference
  https://learn.chatgpt.com/docs/developer-commands?surface=cli  (codex exec)
  https://learn.chatgpt.com/docs/non-interactive-mode            (codex exec --json events)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import warnings
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .models import MissingAPIKeyError, ModelResponse, OfflineModeError, Pricing, StageRequest
from .schema import Usage


class MissingCLIError(MissingAPIKeyError):
    """The CLI binary is not on PATH. Subclasses MissingAPIKeyError so run_pilot skips the model cleanly."""


class CLIError(RuntimeError):
    pass


class CLIToolUseError(CLIError):
    pass


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], str, str, float], RunResult]   # (cmd, stdin, cwd, timeout) -> result


def subprocess_runner(cmd: Sequence[str], stdin: str, cwd: str, timeout_s: float) -> RunResult:  # pragma: no cover
    p = subprocess.run(list(cmd), input=stdin, capture_output=True, text=True, cwd=cwd, timeout=timeout_s)
    return RunResult(p.returncode, p.stdout, p.stderr)


class _CLIClient:
    provider = "cli"
    default_binary = ""

    def __init__(self, model: str, *, name: Optional[str] = None, pricing: Optional[Pricing] = None,
                 binary: Optional[str] = None, effort: Optional[str] = None, extra_args: Sequence[str] = (),
                 timeout_s: float = 900.0, max_retries: int = 2, on_tool_use: str = "warn",
                 runner: Optional[Runner] = None, workdir: Optional[str] = None) -> None:
        self.binary = binary or self.default_binary
        self.runner = runner or subprocess_runner
        if runner is None and shutil.which(self.binary) is None:
            raise MissingCLIError(f"{type(self).__name__}: '{self.binary}' is not on PATH; install it and log in, "
                                  f"or choose another model. No call was made.")
        if on_tool_use not in ("warn", "fail"):
            raise ValueError("on_tool_use must be 'warn' or 'fail'")
        self.model = model
        self.name = name or model
        self.pricing = pricing or Pricing()
        self.effort = effort
        self.extra_args = list(extra_args)
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.on_tool_use = on_tool_use
        self.workdir = workdir or tempfile.mkdtemp(prefix="numtrace-cli-")
        self.call_log: list[dict] = []          # one entry per call: tokens, CLI cost, tool use, turns
        self.cli_version = self._version()

    # -- helpers ---------------------------------------------------------------------------------
    def _version(self) -> str:
        try:
            r = self.runner([self.binary, "--version"], "", self.workdir, 60.0)
            return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else "unknown"
        except Exception:  # pragma: no cover - version is informational
            return "unknown"

    @staticmethod
    def _split(messages: list[dict]) -> tuple[str, str]:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        user = "\n\n".join(m["content"] for m in messages if m["role"] != "system")
        return system, user

    def _run(self, cmd: list[str], stdin: str) -> RunResult:
        if os.environ.get("NUMTRACE_OFFLINE") == "1":
            raise OfflineModeError("NUMTRACE_OFFLINE=1: refusing to call a model CLI")
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self.runner(cmd, stdin, self.workdir, self.timeout_s)
            except subprocess.TimeoutExpired as e:  # pragma: no cover
                last = CLIError(f"{self.binary} timed out after {self.timeout_s}s")
            else:
                if r.returncode == 0:
                    return r
                last = CLIError(f"{self.binary} exited {r.returncode}: {(r.stderr or r.stdout)[-800:]}")
            if attempt < self.max_retries:
                time.sleep(2 ** attempt)
        raise last  # type: ignore[misc]

    def _tool_use(self, tools: list[str]) -> None:
        if not tools:
            return
        msg = f"{self.name}: the CLI used tools {tools}; this call is not a pure text completion"
        if self.on_tool_use == "fail":
            raise CLIToolUseError(msg)
        warnings.warn(msg)

    def _response(self, text: str, pt: int, ct: int, resolved: str, meta: dict) -> ModelResponse:
        self.call_log.append({"model": resolved, "prompt_tokens": pt, "completion_tokens": ct, **meta})
        return ModelResponse(text=text,
                             usage=Usage(prompt_tokens=pt, completion_tokens=ct, calls=1,
                                         cost_usd=self.pricing.cost(pt, ct), token_count_method="cli_reported"),
                             model_version=f"{resolved} via {self.binary} {self.cli_version}")


class ClaudeCodeCLIClient(_CLIClient):
    provider = "claude_code"
    default_binary = "claude"

    def __init__(self, model: str, *, max_turns: int = 2, **kw) -> None:
        self.max_turns = max_turns
        super().__init__(model, **kw)

    def command(self, system: str) -> list[str]:
        cmd = [self.binary, "-p", "--output-format", "json", "--model", self.model,
               "--system-prompt", system or " ", "--tools", "", "--safe-mode",
               "--no-session-persistence", "--max-turns", str(self.max_turns)]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd + self.extra_args

    def complete(self, request: StageRequest) -> ModelResponse:
        system, user = self._split(request.messages)
        r = self._run(self.command(system), user)
        data = parse_claude_json(r.stdout)
        if data.get("is_error") or data.get("subtype", "success") != "success":
            raise CLIError(f"claude returned an error: {str(data.get('result') or data.get('subtype'))[:500]}")
        u = data.get("usage", {}) or {}
        pt = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) \
            + int(u.get("cache_creation_input_tokens", 0))
        ct = int(u.get("output_tokens", 0))
        resolved = ",".join(sorted((data.get("modelUsage") or {}).keys())) or self.model
        turns = int(data.get("num_turns", 1) or 1)
        self._tool_use(["(num_turns=%d)" % turns] if turns > 1 else [])
        return self._response(str(data.get("result", "")), pt, ct, resolved,
                              {"cli_cost_usd": data.get("total_cost_usd"), "num_turns": turns, "tools_used": [],
                               "cache_read_tokens": int(u.get("cache_read_input_tokens", 0))})


def parse_claude_json(stdout: str) -> dict:
    """claude -p --output-format json prints one JSON object (take the last JSON line if there is noise)."""
    s = stdout.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        for line in reversed(s.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    raise CLIError(f"could not parse claude JSON output: {s[:300]}")


_CODEX_TOOL_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "web_search", "todo_list"}


class CodexCLIClient(_CLIClient):
    provider = "codex"
    default_binary = "codex"

    def __init__(self, model: str, *, codex_home: Optional[str] = None, **kw) -> None:
        self.codex_home = os.path.expanduser(codex_home) if codex_home else None
        super().__init__(model, **kw)

    def command(self) -> list[str]:
        cmd = ["env", f"CODEX_HOME={self.codex_home}"] if self.codex_home else []
        cmd += [self.binary, "exec", "--json", "--model", self.model, "--sandbox", "read-only",
                "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--cd", self.workdir]
        if self.effort:
            cmd += ["-c", f'model_reasoning_effort="{self.effort}"']
        return cmd + self.extra_args + ["-"]

    def complete(self, request: StageRequest) -> ModelResponse:
        system, user = self._split(request.messages)
        prompt = (f"{system}\n\nAnswer directly in your reply. Do not run commands, read files or search the web.\n\n"
                  f"{user}") if system else user
        r = self._run(self.command(), prompt)
        text, usage, tools = parse_codex_events(r.stdout)
        self._tool_use(tools)
        pt, ct = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        return self._response(text, pt, ct, self.model,
                              {"cached_input_tokens": int(usage.get("cached_input_tokens", 0)),
                               "reasoning_output_tokens": int(usage.get("reasoning_output_tokens", 0)),
                               "tools_used": tools})


def parse_codex_events(stdout: str) -> tuple[str, dict, list[str]]:
    """codex exec --json: JSONL events. Final text = last completed agent_message; usage summed over
    turn.completed events; tool items are collected. turn.failed / error raise."""
    text, usage, tools = None, {}, []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type", "")
        if t in ("turn.failed", "error"):
            raise CLIError(f"codex {t}: {json.dumps(ev)[:500]}")
        if t == "item.completed":
            item = ev.get("item", {}) or {}
            if item.get("type") == "agent_message":
                text = item.get("text", "")
            elif item.get("type") in _CODEX_TOOL_ITEMS:
                tools.append(item["type"])
        if t == "turn.completed":
            for k, v in (ev.get("usage") or {}).items():
                if isinstance(v, (int, float)):
                    usage[k] = usage.get(k, 0) + v
    if text is None:
        raise CLIError("codex produced no agent_message")
    return text, usage, tools


CLI_PROVIDERS = {"claude_code": ClaudeCodeCLIClient, "codex": CodexCLIClient}
