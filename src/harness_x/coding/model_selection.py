"""Resolve a small explicit model profile into the existing Harness X reasoning cores."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from harness_x.reasoning import (
    ModelCapability,
    ModelProfileBackend,
    ModelProvider,
    OpenAICompatibleReasoningCore,
    OpenAICompatibleSettings,
    TransformersLocalSettings,
    model_profile_registry,
)
from harness_x.reasoning.adapters.repository_coding_transformers import (
    RepositoryCodingTransformersReasoningCore,
)


DEFAULT_DEVELOPMENT_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"
LEGACY_DIRECT_OPENAI_MAX_OUTPUT_TOKENS = 2048


class ResolvedModelSelection(BaseModel):
    """Auditable, secret-free identity/configuration of the selected reasoning core."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["resolved-model-selection-v1"] = "resolved-model-selection-v1"
    source: Literal["profile", "direct_flags"]
    profile_id: str | None = None
    role: str | None = None
    provider: ModelProvider = ModelProvider.CUSTOM
    capabilities: tuple[ModelCapability, ...] = ()
    backend: ModelProfileBackend
    model: str
    revision: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    allow_remote_endpoint: bool = False
    requires_api_key: bool = False
    max_output_tokens: int = 32768
    temperature: float = 0.0
    top_p: float | None = None
    reasoning_effort: str | None = None
    prompt_mode: str = "system"
    load_in_4bit: bool = True
    local_files_only: bool = False


def resolve_model_selection(args) -> ResolvedModelSelection:
    profile_id = getattr(args, "model_profile", None)
    if profile_id:
        default_backend = "transformers"
        default_model = DEFAULT_DEVELOPMENT_MODEL
        if getattr(args, "backend", default_backend) != default_backend:
            raise ValueError("--backend cannot be combined with --model-profile")
        if getattr(args, "model", default_model) != default_model:
            raise ValueError("--model cannot be combined with --model-profile")
        if getattr(args, "revision", None) is not None:
            raise ValueError("--revision cannot be combined with --model-profile")
        if getattr(args, "no_4bit", False) or getattr(args, "local_files_only", False):
            raise ValueError(
                "transformers loader flags cannot be combined with --model-profile; "
                "use a custom profile or direct flags"
            )

        registry = model_profile_registry(getattr(args, "model_profile_file", None))
        try:
            profile = registry.get(profile_id)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc

        base_url = getattr(args, "base_url", None) or profile.base_url
        api_key_env = getattr(args, "api_key_env", None) or profile.api_key_env
        reasoning_effort = getattr(args, "reasoning_effort", None) or profile.reasoning_effort
        allow_remote = profile.allow_remote_endpoint or bool(getattr(args, "allow_remote", False))
        return ResolvedModelSelection(
            source="profile",
            profile_id=profile.profile_id,
            role=profile.role.value,
            provider=profile.provider,
            capabilities=profile.capabilities,
            backend=profile.backend,
            model=profile.model,
            revision=profile.revision,
            base_url=base_url,
            api_key_env=api_key_env,
            allow_remote_endpoint=allow_remote,
            requires_api_key=profile.requires_api_key,
            max_output_tokens=profile.max_output_tokens,
            temperature=profile.temperature,
            top_p=profile.top_p,
            reasoning_effort=reasoning_effort,
            prompt_mode=profile.prompt_mode,
            load_in_4bit=profile.load_in_4bit,
            local_files_only=profile.local_files_only,
        )

    backend = ModelProfileBackend(getattr(args, "backend", "transformers"))
    model = getattr(args, "model", DEFAULT_DEVELOPMENT_MODEL)
    if backend == ModelProfileBackend.TRANSFORMERS:
        return ResolvedModelSelection(
            source="direct_flags",
            backend=backend,
            model=model,
            revision=getattr(args, "revision", None),
            max_output_tokens=getattr(args, "generation_max_new_tokens", 4096),
            load_in_4bit=not bool(getattr(args, "no_4bit", False)),
            local_files_only=bool(getattr(args, "local_files_only", False)),
        )

    return ResolvedModelSelection(
        source="direct_flags",
        backend=backend,
        model=model,
        base_url=getattr(args, "base_url", None) or DEFAULT_LOCAL_BASE_URL,
        api_key_env=getattr(args, "api_key_env", None),
        allow_remote_endpoint=bool(getattr(args, "allow_remote", False)),
        max_output_tokens=LEGACY_DIRECT_OPENAI_MAX_OUTPUT_TOKENS,
        reasoning_effort=getattr(args, "reasoning_effort", None),
    )


def build_selected_reasoning_core(selection: ResolvedModelSelection):
    if selection.backend == ModelProfileBackend.TRANSFORMERS:
        return RepositoryCodingTransformersReasoningCore(
            TransformersLocalSettings(
                model=selection.model,
                revision=selection.revision,
                max_new_tokens=selection.max_output_tokens,
                load_in_4bit=selection.load_in_4bit,
                local_files_only=selection.local_files_only,
            )
        )

    if selection.requires_api_key:
        if not selection.api_key_env or not os.getenv(selection.api_key_env):
            env_name = selection.api_key_env or "<missing>"
            raise ValueError(
                f"model profile {selection.profile_id!r} requires API key environment "
                f"variable {env_name!r}; no remote request was made"
            )

    return OpenAICompatibleReasoningCore(
        OpenAICompatibleSettings(
            base_url=selection.base_url or DEFAULT_LOCAL_BASE_URL,
            model=selection.model,
            provider=selection.provider.value,
            api_key_env=selection.api_key_env,
            allow_remote_endpoint=selection.allow_remote_endpoint,
            max_output_tokens=selection.max_output_tokens,
            temperature=selection.temperature,
            top_p=selection.top_p,
            reasoning_effort=selection.reasoning_effort,
            prompt_mode=selection.prompt_mode,  # type: ignore[arg-type]
        )
    )


def write_model_selection_artifact(
    selection: ResolvedModelSelection,
    output_root: str | Path,
) -> Path:
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "model-selection.json"
    path.write_text(selection.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
