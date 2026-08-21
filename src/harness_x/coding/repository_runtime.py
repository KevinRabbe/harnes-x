"""M23 autonomous coding runtime with bounded repository intelligence."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from harness_x.reasoning import RawReasoningOutput, ReasoningCore, ReasoningCoreInfo
from harness_x.reasoning.context_builder import ContextBuildResult
from harness_x.repository import RepositoryIntelligenceService, RepositorySemanticProvider
from harness_x.tools.coding_repository import build_repository_coding_registry

from .autonomous_runtime import AutonomousCodingTaskRuntime


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _repository_projection(
    service: RepositoryIntelligenceService,
    *,
    max_map_chars: int = 4500,
    max_instruction_chars: int = 2800,
) -> dict[str, object]:
    snapshot = service.snapshot()
    instructions: list[dict[str, object]] = []
    remaining = max_instruction_chars
    for item in snapshot.instructions:
        if remaining <= 0:
            break
        preview = item.preview[: min(1200, remaining)]
        instructions.append(
            {
                "path": item.path,
                "kind": item.kind,
                "preview": preview,
                "truncated": item.truncated or len(preview) < len(item.preview),
            }
        )
        remaining -= len(preview)
    return {
        "schema_version": "repository-context-v1",
        "snapshot_fingerprint": snapshot.fingerprint,
        "identity": snapshot.identity.model_dump(mode="json"),
        "languages": list(snapshot.languages),
        "manifests": list(snapshot.manifests[:24]),
        "source_roots": list(snapshot.source_roots),
        "test_roots": list(snapshot.test_roots),
        "instructions": instructions,
        "compact_map": snapshot.compact_map[:max_map_chars],
        "snapshot_truncated": snapshot.truncated,
        "freshness_rule": (
            "This is bounded startup orientation. After structural edits or when exact current "
            "state matters, use repository_map(refresh=true), git_status, file_outline, or "
            "symbol tools instead of assuming this snapshot changed automatically."
        ),
    }


class RepositoryContextReasoningCore:
    """Inject bounded repository orientation without granting the model repository authority."""

    def __init__(
        self,
        core: ReasoningCore,
        service: RepositoryIntelligenceService,
        *,
        max_total_chars: int = 24_000,
    ) -> None:
        self.core = core
        self.service = service
        self.max_total_chars = max_total_chars
        self._projection = _repository_projection(service)

    @property
    def info(self) -> ReasoningCoreInfo:
        return self.core.info

    def generate(self, context: ContextBuildResult) -> RawReasoningOutput:
        enriched = self._enrich(context)
        return self.core.generate(enriched)

    def close(self) -> None:
        close = getattr(self.core, "close", None)
        if callable(close):
            close()

    def _enrich(self, context: ContextBuildResult) -> ContextBuildResult:
        payload = copy.deepcopy(context.payload)
        sections = payload.setdefault("sections", {})
        projection = copy.deepcopy(self._projection)
        sections["repository_intelligence"] = {
            "authority": "authoritative_bounded_repository_orientation",
            "rule": (
                "Repository structure and instruction excerpts here are software-derived. "
                "Symbol precision is explicit; heuristic results are not compiler facts."
            ),
            "data": projection,
        }
        serialized = _canonical(payload)

        if len(serialized) > self.max_total_chars:
            overflow = len(serialized) - self.max_total_chars
            compact_map = str(projection.get("compact_map", ""))
            projection["compact_map"] = compact_map[: max(600, len(compact_map) - overflow - 64)]
            serialized = _canonical(payload)

        if len(serialized) > self.max_total_chars:
            projection["instructions"] = [
                {"path": item["path"], "kind": item["kind"], "preview": "(use workspace_read)", "truncated": True}
                for item in projection.get("instructions", [])
            ]
            projection["compact_map"] = str(projection.get("compact_map", ""))[:1200]
            serialized = _canonical(payload)

        if len(serialized) > self.max_total_chars:
            minimal = {
                "schema_version": "repository-context-v1",
                "snapshot_fingerprint": projection["snapshot_fingerprint"],
                "identity": projection["identity"],
                "languages": projection["languages"],
                "manifests": projection["manifests"],
                "freshness_rule": "Use repository tools for details.",
            }
            sections["repository_intelligence"]["data"] = minimal
            serialized = _canonical(payload)

        # The base context builder already fits its own 24k budget. If even the minimal
        # repository projection cannot fit, preserve governing state and omit orientation
        # rather than silently dropping authoritative task/working-state sections.
        if len(serialized) > self.max_total_chars:
            sections.pop("repository_intelligence", None)
            serialized = _canonical(payload)

        fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return context.model_copy(
            update={
                "payload": payload,
                "serialized": serialized,
                "fingerprint": fingerprint,
                "char_count": len(serialized),
            }
        )


class RepositoryAwareAutonomousCodingTaskRuntime(AutonomousCodingTaskRuntime):
    """Qualified M22 controller plus M23 repository intelligence/ACI tools."""

    def __init__(
        self,
        workspace_root: str | Path,
        core: ReasoningCore,
        output_root: str | Path,
        *,
        semantic_provider: RepositorySemanticProvider | None = None,
        max_reasoning_steps: int = 32,
        max_tool_actions: int = 48,
        max_output_tokens: int = 65536,
        system_version: str = "0.1.0a0+coding23-repository",
        allowed_executables: frozenset[str] | None = None,
        baseline_verification: bool = True,
        max_idle_turns: int = 3,
        max_inspection_streak: int = 6,
        max_no_progress_streak: int = 4,
        max_same_failure_count: int = 3,
    ) -> None:
        repository = RepositoryIntelligenceService(workspace_root)
        repository.snapshot()
        wrapped_core = RepositoryContextReasoningCore(core, repository)
        super().__init__(
            workspace_root,
            wrapped_core,
            output_root,
            max_reasoning_steps=max_reasoning_steps,
            max_tool_actions=max_tool_actions,
            max_output_tokens=max_output_tokens,
            system_version=system_version,
            allowed_executables=allowed_executables,
            baseline_verification=baseline_verification,
            max_idle_turns=max_idle_turns,
            max_inspection_streak=max_inspection_streak,
            max_no_progress_streak=max_no_progress_streak,
            max_same_failure_count=max_same_failure_count,
        )
        self.repository = repository
        self.semantic_provider = semantic_provider
        self.registry = build_repository_coding_registry(
            self.workspace_root,
            allowed_executables=allowed_executables,
            repository_service=repository,
            semantic_provider=semantic_provider,
        )
        self.allowed_tools = tuple(spec.name for spec in self.registry.specs())
