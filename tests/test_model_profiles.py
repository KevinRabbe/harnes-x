from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_x.coding.cli import _build_core, build_parser
from harness_x.coding.model_selection import (
    build_selected_reasoning_core,
    resolve_model_selection,
    write_model_selection_artifact,
)
from harness_x.reasoning import (
    ModelProfileBackend,
    ModelProfileRole,
    OpenAICompatibleReasoningCore,
    builtin_model_profiles,
    model_profile_registry,
)


def _args(*extra: str):
    return build_parser().parse_args(
        [
            ".",
            "--task",
            "repair the repository",
            "--verify",
            "python -m pytest",
            *extra,
        ]
    )


def test_builtin_personal_profile_shortlist_is_small_and_explicit() -> None:
    registry = builtin_model_profiles()
    assert tuple(item.profile_id for item in registry.profiles) == (
        "main",
        "coder",
        "reasoning",
        "api",
    )

    main = registry.get("main")
    assert main.role == ModelProfileRole.MAIN
    assert main.backend == ModelProfileBackend.OPENAI_COMPATIBLE
    assert main.model == "Qwen/Qwen3.8-27B"
    assert main.base_url == "http://127.0.0.1:8000/v1"
    assert main.reasoning_effort == "xhigh"
    assert main.allow_remote_endpoint is False

    coder = registry.get("coder")
    assert coder.model == "Qwen/Qwen3-Coder-30B-A3B-Instruct"
    assert coder.reasoning_effort is None
    assert coder.temperature == 0.7

    reasoning = registry.get("reasoning")
    assert reasoning.model == "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"
    assert reasoning.prompt_mode == "user_prefix"
    assert reasoning.temperature == 0.6

    api = registry.get("api")
    assert api.model == "gpt-5.6-sol"
    assert api.role == ModelProfileRole.API
    assert api.allow_remote_endpoint is True
    assert api.requires_api_key is True
    assert api.api_key_env == "OPENAI_API_KEY"


def test_custom_registry_can_override_one_builtin_without_growing_a_catalog(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "model-profile-registry-v1",
                "profiles": [
                    {
                        "schema_version": "model-profile-v1",
                        "profile_id": "main",
                        "role": "main",
                        "backend": "openai",
                        "model": "my-local-main-model",
                        "base_url": "http://127.0.0.1:9100/v1",
                        "temperature": 0.4,
                        "max_output_tokens": 12000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    registry = model_profile_registry(path)
    assert len(registry.profiles) == 4
    assert registry.get("main").model == "my-local-main-model"
    assert registry.get("main").base_url == "http://127.0.0.1:9100/v1"
    assert registry.get("coder").model == "Qwen/Qwen3-Coder-30B-A3B-Instruct"


def test_main_profile_resolves_to_local_openai_compatible_core_without_network() -> None:
    args = _args("--model-profile", "main")
    selection = resolve_model_selection(args)
    assert selection.source == "profile"
    assert selection.profile_id == "main"
    assert selection.model == "Qwen/Qwen3.8-27B"
    assert selection.reasoning_effort == "xhigh"
    assert selection.allow_remote_endpoint is False

    core = _build_core(args)
    assert isinstance(core, OpenAICompatibleReasoningCore)
    assert core.settings.base_url == "http://127.0.0.1:8000/v1"
    assert core.settings.temperature == 1.0
    assert core.settings.top_p == 0.95
    assert core.settings.reasoning_effort == "xhigh"


def test_profile_endpoint_and_reasoning_effort_can_be_machine_overridden() -> None:
    args = _args(
        "--model-profile",
        "main",
        "--base-url",
        "http://127.0.0.1:9123/v1",
        "--reasoning-effort",
        "medium",
    )
    selection = resolve_model_selection(args)
    assert selection.base_url == "http://127.0.0.1:9123/v1"
    assert selection.reasoning_effort == "medium"
    assert selection.model == "Qwen/Qwen3.8-27B"


def test_profile_rejects_ambiguous_direct_model_selection() -> None:
    args = _args("--model-profile", "main", "--model", "another-model")
    with pytest.raises(ValueError, match="--model cannot be combined"):
        resolve_model_selection(args)


def test_api_profile_fails_closed_without_key_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    selection = resolve_model_selection(_args("--model-profile", "api"))
    with pytest.raises(ValueError, match="requires API key environment"):
        build_selected_reasoning_core(selection)


def test_api_profile_builds_only_after_explicit_selection_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-secret")
    selection = resolve_model_selection(_args("--model-profile", "api"))
    core = build_selected_reasoning_core(selection)
    assert isinstance(core, OpenAICompatibleReasoningCore)
    assert core.settings.allow_remote_endpoint is True
    assert core.settings.api_key_env == "OPENAI_API_KEY"
    assert core.settings.model == "gpt-5.6-sol"
    assert core.settings.reasoning_effort == "high"


def test_model_selection_artifact_records_env_name_but_never_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")
    selection = resolve_model_selection(_args("--model-profile", "api"))
    path = write_model_selection_artifact(selection, tmp_path)
    content = path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in content
    assert "super-secret-value" not in content
    payload = json.loads(content)
    assert payload["profile_id"] == "api"
    assert payload["model"] == "gpt-5.6-sol"


def test_no_profile_preserves_development_transformers_default() -> None:
    selection = resolve_model_selection(_args())
    assert selection.source == "direct_flags"
    assert selection.backend == ModelProfileBackend.TRANSFORMERS
    assert selection.model == "Qwen/Qwen3-4B-Instruct-2507"
    assert selection.max_output_tokens == 4096
