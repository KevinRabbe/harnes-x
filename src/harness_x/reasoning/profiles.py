"""Small operator-owned model profile registry for Harness X.

M32 deliberately does not implement automatic model routing or voting. A profile is an explicit
operator choice that resolves to one existing reasoning transport. The selected model receives no
additional authority over Harness X state, tools, verification, memory, or improvement machinery.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ModelProfileRole(StrEnum):
    MAIN = "main"
    CODER = "coder"
    REASONING = "reasoning"
    API = "api"
    CUSTOM = "custom"


class ModelProfileBackend(StrEnum):
    TRANSFORMERS = "transformers"
    OPENAI_COMPATIBLE = "openai"


class ModelProvider(StrEnum):
    QWEN = "qwen"
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    CUSTOM = "custom"


class ModelCapability(StrEnum):
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_USE = "tool_use"
    REASONING = "reasoning"


class ModelProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["model-profile-v1"] = "model-profile-v1"
    profile_id: str = Field(min_length=1, max_length=64)
    role: ModelProfileRole = ModelProfileRole.CUSTOM
    provider: ModelProvider = ModelProvider.CUSTOM
    capabilities: tuple[ModelCapability, ...] = ()
    backend: ModelProfileBackend
    model: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=1200)

    revision: str | None = None
    max_output_tokens: int = Field(default=32768, ge=64, le=65536)

    base_url: str | None = None
    api_key_env: str | None = None
    allow_remote_endpoint: bool = False
    requires_api_key: bool = False
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    reasoning_effort: str | None = None
    prompt_mode: Literal["system", "user_prefix"] = "system"

    load_in_4bit: bool = True
    local_files_only: bool = False

    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        value = value.strip().casefold()
        if not _PROFILE_ID.fullmatch(value):
            raise ValueError(
                "profile_id must start with an alphanumeric character and contain only "
                "lowercase letters, digits, dot, underscore, or hyphen"
            )
        return value

    @field_validator("model", "description")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("revision", "base_url", "api_key_env", "reasoning_effort")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def validate_transport_shape(self) -> "ModelProfile":
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("model profile capabilities must be unique")
        if self.backend == ModelProfileBackend.OPENAI_COMPATIBLE:
            if self.base_url is None:
                raise ValueError("openai model profiles require base_url")
            if self.requires_api_key and self.api_key_env is None:
                raise ValueError("API-key-required model profiles require api_key_env")
        else:
            if self.base_url is not None or self.api_key_env is not None:
                raise ValueError("transformers model profiles cannot define HTTP endpoint fields")
            if self.allow_remote_endpoint or self.requires_api_key:
                raise ValueError("transformers model profiles cannot enable remote/API-key flags")
            if self.reasoning_effort is not None:
                raise ValueError(
                    "in-process transformers profiles do not currently expose reasoning_effort"
                )
            if self.prompt_mode != "system":
                raise ValueError("in-process transformers profiles require prompt_mode=system")
        return self


class ModelProfileRegistry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["model-profile-registry-v1"] = "model-profile-registry-v1"
    profiles: tuple[ModelProfile, ...] = ()

    @model_validator(mode="after")
    def unique_ids(self) -> "ModelProfileRegistry":
        ids = [item.profile_id for item in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("model profile IDs must be unique")
        return self

    def get(self, profile_id: str) -> ModelProfile:
        normalized = profile_id.strip().casefold()
        for item in self.profiles:
            if item.profile_id == normalized:
                return item
        available = ", ".join(item.profile_id for item in self.profiles)
        raise KeyError(f"unknown model profile {profile_id!r}; available: {available}")


_BUILTIN_PROFILES = (
    ModelProfile(
        profile_id="main",
        role=ModelProfileRole.MAIN,
        provider=ModelProvider.QWEN,
        capabilities=(
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.TOOL_USE,
            ModelCapability.REASONING,
        ),
        backend=ModelProfileBackend.OPENAI_COMPATIBLE,
        model="Qwen/Qwen3.8-27B",
        description=(
            "Primary strong local coding/reasoning/agentic model; expected to be served by "
            "vLLM, SGLang, or another OpenAI-compatible loopback server."
        ),
        base_url="http://127.0.0.1:8000/v1",
        max_output_tokens=32768,
        temperature=1.0,
        top_p=0.95,
        reasoning_effort="xhigh",
    ),
    ModelProfile(
        profile_id="qwen3-8b",
        role=ModelProfileRole.CODER,
        provider=ModelProvider.QWEN,
        capabilities=(
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.TOOL_USE,
            ModelCapability.REASONING,
        ),
        backend=ModelProfileBackend.OPENAI_COMPATIBLE,
        model="Qwen/Qwen3-8B",
        description=(
            "Smaller local Qwen profile for cheap repeatable single-PC experiments through an "
            "OpenAI-compatible loopback server."
        ),
        base_url="http://127.0.0.1:8000/v1",
        max_output_tokens=16384,
        temperature=0.6,
        top_p=0.95,
    ),
    ModelProfile(
        profile_id="coder",
        role=ModelProfileRole.CODER,
        provider=ModelProvider.QWEN,
        capabilities=(
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.TOOL_USE,
        ),
        backend=ModelProfileBackend.OPENAI_COMPATIBLE,
        model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        description=(
            "Local agentic-coding specialist; non-thinking Qwen MoE served through a loopback "
            "OpenAI-compatible endpoint."
        ),
        base_url="http://127.0.0.1:8000/v1",
        max_output_tokens=32768,
        temperature=0.7,
        top_p=0.8,
    ),
    ModelProfile(
        profile_id="reasoning",
        role=ModelProfileRole.REASONING,
        provider=ModelProvider.DEEPSEEK,
        capabilities=(
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.REASONING,
        ),
        backend=ModelProfileBackend.OPENAI_COMPATIBLE,
        model="deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
        description=(
            "Independent local DeepSeek reasoning comparison core; intended for second-opinion "
            "analysis rather than automatic model voting."
        ),
        base_url="http://127.0.0.1:8000/v1",
        max_output_tokens=32768,
        temperature=0.6,
        top_p=0.95,
        prompt_mode="user_prefix",
    ),
    ModelProfile(
        profile_id="api",
        role=ModelProfileRole.API,
        provider=ModelProvider.OPENAI,
        capabilities=(
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.TOOL_USE,
            ModelCapability.REASONING,
        ),
        backend=ModelProfileBackend.OPENAI_COMPATIBLE,
        model="gpt-5.6-sol",
        description=(
            "Optional remote OpenAI reasoning/coding core. It is never selected implicitly and "
            "requires OPENAI_API_KEY when explicitly chosen."
        ),
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        allow_remote_endpoint=True,
        requires_api_key=True,
        max_output_tokens=32768,
        temperature=0.0,
        reasoning_effort="high",
    ),
)


def builtin_model_profiles() -> ModelProfileRegistry:
    return ModelProfileRegistry(profiles=_BUILTIN_PROFILES)


def load_model_profile_registry(path: str | Path) -> ModelProfileRegistry:
    path = Path(path)
    try:
        return ModelProfileRegistry.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid model profile registry {path}: {exc}") from exc


def model_profile_registry(path: str | Path | None = None) -> ModelProfileRegistry:
    """Return built-ins with optional operator definitions overriding by profile ID."""

    builtins = {item.profile_id: item for item in _BUILTIN_PROFILES}
    if path is not None:
        custom = load_model_profile_registry(path)
        for item in custom.profiles:
            builtins[item.profile_id] = item
    return ModelProfileRegistry(profiles=tuple(builtins.values()))


def model_profile_registry_json(path: str | Path | None = None) -> str:
    return model_profile_registry(path).model_dump_json(indent=2) + "\n"
