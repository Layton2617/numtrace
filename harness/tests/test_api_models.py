"""HTTP API clients: request shape for reasoning models (effort, token limit, no sampling) and parsing.
A fake transport is injected; no network call is made."""
from harness.models import AnthropicClient, DeepSeekChatClient, OpenAIChatClient, Pricing, StageRequest
from harness.schema import Stage

REQ = StageRequest(stage=Stage.extraction, temperature=0.0, seed=0,
                   messages=[{"role": "system", "content": "SYS"}, {"role": "user", "content": "U"}])


class Fake:
    def __init__(self, out):
        self.out, self.seen = out, []

    def __call__(self, url, body, headers):
        self.seen.append((url, body, headers))
        return self.out


def test_anthropic_effort_and_no_temperature():
    f = Fake({"model": "claude-opus-5-5", "content": [{"type": "thinking", "thinking": "..."},
                                                      {"type": "text", "text": "{}"}],
              "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 7}})
    c = AnthropicClient("claude-opus-5-5", effort="medium", post=f, pricing=Pricing(4, 20))
    r = c.complete(REQ)
    url, body, _ = f.seen[0]
    assert url.endswith("/messages") and body["output_config"] == {"effort": "medium"}
    assert "temperature" not in body and body["max_tokens"] == 32000 and body["system"] == "SYS"
    assert r.text == "{}" and r.usage.prompt_tokens == 100 and r.usage.completion_tokens == 7
    assert r.model_version == "claude-opus-5-5 (effort=medium)"


def test_openai_responses_api():
    f = Fake({"model": "gpt-6-astra", "output": [{"type": "reasoning"},
                                                 {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}],
              "usage": {"input_tokens": 50, "output_tokens": 20}})
    r = OpenAIChatClient("gpt-6-astra", effort="medium", json_mode=True, post=f).complete(REQ)
    url, body, _ = f.seen[0]
    assert url.endswith("/responses") and body["reasoning"] == {"effort": "medium"}
    assert body["max_output_tokens"] == 32000 and "temperature" not in body and "seed" not in body
    assert body["text"] == {"format": {"type": "json_object"}} and body["input"][0]["role"] == "system"
    assert r.text == "{}" and r.usage.prompt_tokens == 50 and r.usage.completion_tokens == 20


def test_deepseek_thinking_effort():
    f = Fake({"model": "deepseek-flash", "choices": [{"message": {"content": "{}", "reasoning_content": "..."}}],
              "usage": {"prompt_tokens": 30, "completion_tokens": 12}})
    r = DeepSeekChatClient("deepseek-flash", effort="high", post=f).complete(REQ)
    _, body, _ = f.seen[0]
    assert body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "high"
    assert "temperature" not in body and body["max_tokens"] == 32000
    assert "response_format" not in body          # JSON mode off by default, like the CLI models
    assert r.text == "{}" and r.usage.completion_tokens == 12


def test_sampling_only_when_asked():
    f = Fake({"choices": [{"message": {"content": "{}"}}], "usage": {}})
    DeepSeekChatClient("m", post=f, send_sampling=True).complete(REQ)
    assert f.seen[0][1]["temperature"] == 0.0 and f.seen[0][1]["seed"] == 0
