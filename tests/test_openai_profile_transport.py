from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from harness_x.reasoning import (
    OpenAICompatibleReasoningCore,
    OpenAICompatibleSettings,
    ReasoningCoreError,
)
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


class _ProbeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]


class _ProbeOpener:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        return self.response


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
            provider="qwen",
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
            provider="deepseek",
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


def test_connection_probe_is_bounded_and_returns_only_safe_model_metadata(monkeypatch) -> None:
    opener = _ProbeOpener(
        _ProbeResponse({"data": [{"id": f"model-{index}"} for index in range(30)]})
    )
    monkeypatch.setattr(adapter_module, "build_opener", lambda *args: opener)
    core = OpenAICompatibleReasoningCore(
        OpenAICompatibleSettings(
            base_url="http://127.0.0.1:8000/v1",
            model="Qwen/Qwen3-8B",
            provider="qwen",
        )
    )

    result = core.test_connection()

    assert result.ready is True
    assert result.provider == "qwen"
    assert result.configured_model == "Qwen/Qwen3-8B"
    assert len(result.advertised_model_ids) == 16
    assert result.advertised_model_ids[0] == "model-0"
    assert opener.request.full_url == "http://127.0.0.1:8000/v1/models"
    assert opener.timeout == 30.0
    assert "Authorization" not in opener.request.headers


def test_connection_probe_uses_existing_api_key_boundary_without_exposing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _ProbeOpener(_ProbeResponse({"data": [{"id": "gpt-5.6-sol"}]}))
    monkeypatch.setattr(adapter_module, "build_opener", lambda *args: opener)
    monkeypatch.setenv("OPENAI_API_KEY", "probe-secret")
    core = OpenAICompatibleReasoningCore(
        OpenAICompatibleSettings(
            base_url="https://api.openai.com/v1",
            model="gpt-5.6-sol",
            provider="openai",
            api_key_env="OPENAI_API_KEY",
            allow_remote_endpoint=True,
        )
    )

    result = core.test_connection()

    assert result.ready is True
    assert opener.request.get_header("Authorization") == "Bearer probe-secret"
    serialized = result.model_dump_json()
    assert "probe-secret" not in serialized
    assert "OPENAI_API_KEY" not in serialized
    assert "api.openai.com" not in serialized


def test_connection_probe_fails_closed_on_redirect_or_bad_payload(monkeypatch) -> None:
    redirect = HTTPError(
        "http://127.0.0.1:8000/v1/models",
        302,
        "redirect",
        {},
        None,
    )
    opener = _ProbeOpener(error=redirect)
    monkeypatch.setattr(adapter_module, "build_opener", lambda *args: opener)
    core = OpenAICompatibleReasoningCore(
        OpenAICompatibleSettings(
            base_url="http://127.0.0.1:8000/v1",
            model="local-model",
        )
    )
    assert core.test_connection().ready is False

    bad = _ProbeOpener(_ProbeResponse({"unexpected": []}))
    monkeypatch.setattr(adapter_module, "build_opener", lambda *args: bad)
    assert core.test_connection().ready is False


def test_connection_probe_requires_configured_api_key_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    core = OpenAICompatibleReasoningCore(
        OpenAICompatibleSettings(
            base_url="https://api.openai.com/v1",
            model="gpt-5.6-sol",
            provider="openai",
            api_key_env="OPENAI_API_KEY",
            allow_remote_endpoint=True,
        )
    )
    with pytest.raises(ReasoningCoreError, match="OPENAI_API_KEY"):
        core.test_connection()
