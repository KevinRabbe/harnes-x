"""Local-first OpenAI-compatible chat-completions reasoning adapter."""

from __future__ import annotations

import ipaddress
import json
import os
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..base import RawReasoningOutput, ReasoningCoreError, ReasoningCoreInfo
from ..context_builder import ContextBuildResult


_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


class OpenAICompatibleSettings(BaseModel):
    """Connection settings for llama.cpp/vLLM/SGLang/hosted compatible servers."""

    model_config = ConfigDict(frozen=True)

    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = Field(default="local-model", min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=3600.0)
    max_output_tokens: int = Field(default=2048, gt=0, le=65536)
    api_key_env: str | None = None
    allow_remote_endpoint: bool = False
    use_response_format: bool = True
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    reasoning_effort: str | None = None
    prompt_mode: Literal["system", "user_prefix"] = "system"

    @field_validator("base_url", "model")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reasoning adapter fields cannot be blank")
        return value

    @field_validator("api_key_env", "reasoning_effort")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, value: str | None) -> str | None:
        if value is not None and value not in _REASONING_EFFORTS:
            raise ValueError(
                "reasoning_effort must be one of none, low, medium, high, xhigh, max"
            )
        return value

    @model_validator(mode="after")
    def validate_endpoint(self) -> "OpenAICompatibleSettings":
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an http(s) URL with a hostname")
        if not self.allow_remote_endpoint and not _is_loopback_host(parsed.hostname):
            raise ValueError(
                "remote reasoning endpoints are disabled by default; set "
                "allow_remote_endpoint=True explicitly to use a non-loopback host"
            )
        return self


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip("[]").casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


_SYSTEM_PROMPT = """You are a replaceable reasoning core inside Harness X.
You do not own system state, memory, tools, permissions, budgets, or verification.
Return ONLY a JSON object matching this shape:
{
  "status": "complete|continue|blocked",
  "proposals": [{"summary": "...", "payload": {}}],
  "actions": [{"tool_name": "...", "arguments": {}}],
  "observations": ["short structured observation"],
  "requested_additional_steps": 0
}
Do not invent candidate IDs, provenance, permissions, verification status, or state mutations.
Do not output chain-of-thought or hidden reasoning. Candidate/hypothesis memory is not fact unless its verification state says so.
"""


class OpenAICompatibleReasoningCore:
    """Actual HTTP model boundary with no mandatory third-party runtime dependency."""

    def __init__(self, settings: OpenAICompatibleSettings) -> None:
        self.settings = settings
        self._info = ReasoningCoreInfo(
            name="openai_compatible",
            version="openai-compatible-v2-profile-tunable",
            model=settings.model,
            transport="http_chat_completions",
            model_inference=True,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        if self.settings.prompt_mode == "user_prefix":
            messages = [
                {
                    "role": "user",
                    "content": f"{_SYSTEM_PROMPT}\n\nHARNESS X TASK CONTEXT:\n{context.serialized}",
                }
            ]
        else:
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": context.serialized},
            ]

        body: dict[str, object] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_output_tokens,
        }
        if self.settings.top_p is not None:
            body["top_p"] = self.settings.top_p
        if self.settings.reasoning_effort is not None:
            body["reasoning_effort"] = self.settings.reasoning_effort
        if self.settings.use_response_format:
            body["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.settings.api_key_env:
            token = os.getenv(self.settings.api_key_env)
            if not token:
                raise ReasoningCoreError(
                    f"reasoning API key environment variable {self.settings.api_key_env!r} is not set"
                )
            headers["Authorization"] = f"Bearer {token}"

        endpoint = f"{self.settings.base_url.rstrip('/')}/chat/completions"
        request = Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:2000]
            except Exception:
                detail = ""
            raise ReasoningCoreError(
                f"reasoning endpoint returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ReasoningCoreError(f"reasoning endpoint failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ReasoningCoreError("reasoning endpoint returned invalid JSON") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ReasoningCoreError(
                "reasoning endpoint response did not contain choices[0].message.content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ReasoningCoreError("reasoning endpoint returned empty model content")

        try:
            decoded = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ReasoningCoreError(
                "model content was not the required JSON object"
            ) from exc
        try:
            return RawReasoningOutput.model_validate(decoded)
        except Exception as exc:
            raise ReasoningCoreError(
                f"model output violated the structured reasoning schema: {exc}"
            ) from exc
