from __future__ import annotations

import json

from harness_x.reasoning import OpenAICompatibleReasoningCore, OpenAICompatibleSettings
from harness_x.reasoning.context_builder import ContextBuildResult
import harness_x.reasoning.adapters.openai_compatible as adapter_module


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(
            {"choices": [{"message": {"content": '{"status":"complete"}'}}]}
        ).encode("utf-8")


def _context() -> ContextBuildResult:
    return ContextBuildResult(
        fingerprint="0" * 64,
        serialized='{"task":"repair"}',
        payload={"task": "repair"},
        char_count=17,
        dropped_working_items=0,
        dropped_retrieved_items=0,
        dropped_actions=0,
    )


def test_qwen_style_request_carries_reasoning_effort_and_sampling(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(adapter_module, "urlopen", fake_urlopen)
    core = OpenAICompatibleReasoningCore(
        OpenAICompatibleSettings(
            base_url="http://127.0.0.1:8000/v1",
            model="Qwen/Qwen3.8-27B",
            max_output_tokens=32768,
            temperature=1.0,
            top_p=0.95,
            reasoning_effort="xhigh",
        )
    )
    output = core.generate(_context())

    assert output.status == "complete"
    body = captured["body"]
    assert captured["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert body["model"] == "Qwen/Qwen3.8-27B"
    assert body["temperature"] == 1.0
    assert body["top_p"] == 0.95
    assert body["reasoning_effort"] == "xhigh"
    assert body["max_tokens"] == 32768
    assert body["messages"][0]["role"] == "system"
    assert body["response_format"] == {"type": "json_object"}


def test_deepseek_user_prefix_mode_avoids_system_role(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    monkeypatch.setattr(adapter_module, "urlopen", fake_urlopen)
    core = OpenAICompatibleReasoningCore(
        OpenAICompatibleSettings(
            base_url="http://127.0.0.1:8000/v1",
            model="deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
            temperature=0.6,
            top_p=0.95,
            prompt_mode="user_prefix",
        )
    )
    core.generate(_context())

    messages = captured["body"]["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "replaceable reasoning core inside Harness X" in messages[0]["content"]
    assert "HARNESS X TASK CONTEXT" in messages[0]["content"]
    assert '{"task":"repair"}' in messages[0]["content"]
    assert captured["body"]["temperature"] == 0.6
    assert captured["body"]["top_p"] == 0.95
    assert "reasoning_effort" not in captured["body"]
