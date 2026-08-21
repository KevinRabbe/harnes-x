"""M23 autonomous coding runtime with bounded repository intelligence."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Iterable

from harness_x.reasoning import RawReasoningOutput, ReasoningCore, ReasoningCoreInfo
from harness_x.reasoning.context_builder import ContextBuildResult
from harness_x.repository import RepositoryIntelligenceService, RepositorySemanticProvider
from harness_x.tools import ToolSpec
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


def _compact_field_contract(schema: dict[str, object]) -> dict[str, object]:
    contract: dict[str, object] = {}
    if "type" in schema:
        contract["type"] = schema["type"]
    if "enum" in schema:
        contract["enum"] = schema["enum"]
    if "default" in schema:
        contract["default"] = schema["default"]
    if "minimum" in schema:
        contract["minimum"] = schema["minimum"]
    if "maximum" in schema:
        contract["maximum"] = schema["maximum"]
    return contract


def _aci_manifest(specs: Iterable[ToolSpec]) -> list[dict[str, object]]:
    """Compact all live tool contracts so char-budget trimming cannot hide capabilities."""

    manifest: list[dict[str, object]] = []
    for spec in specs:
        input_schema = spec.input_schema
        properties = input_schema.get("properties", {})
        compact_properties = {
            name: _compact_field_contract(dict(schema))
            for name, schema in properties.items()
            if isinstance(schema, dict)
        }
        manifest.append(
            {
                "name": spec.name,
                "version": spec.version,
                "required": list(input_schema.get("required", [])),
                "properties": compact_properties,
            }
        )
    return manifest


def _repository_projection(
    service: RepositoryIntelligenceService,
    *,
    tool_specs: Iterable[ToolSpec],
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
        "aci_manifest": _aci_manifest(tool_specs),
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
        tool_specs: Iterable[ToolSpec],
        max_total_chars: int = 40_000,
    ) -> None:
        self.core = core
        self.service = service
        self.max_total_chars = max_total_chars
        self._projection = _repository_projection(
            service,
            tool_specs=tuple(tool_specs),
        )

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

        # Repository orientation has its own bounded envelope above the base context
        # builder's 24k governing-state budget. Reduce the repo map before instruction
        # previews or the compact ACI manifest; those two prevent avoidable rediscovery.
        if len(serialized) > self.max_total_chars:
            overflow = len(serialized) - self.max_total_chars
            compact_map = str(projection.get("compact_map", ""))
            projection["compact_map"] = compact_map[: max(600, len(compact_map) - overflow - 64)]
            serialized = _canonical(payload)

        if len(serialized) > self.max_total_chars:
            projection["compact_map"] = str(projection.get("compact_map", ""))[:600]
            instructions = list(projection.get("instructions", []))
            for item in reversed(instructions):
                if len(serialized) <= self.max_total_chars:
                    break
                preview = str(item.get("preview", ""))
                if len(preview) > 240:
                    item["preview"] = preview[:240]
                    item["truncated"] = True
                    serialized = _canonical(payload)
            projection["instructions"] = instructions
            serialized = _canonical(payload)

        if len(serialized) > self.max_total_chars:
            projection["instructions"] = [
                {
                    "path": item["path"],
                    "kind": item["kind"],
                    "preview": "(use workspace_read)",
                    "truncated": True,
                }
                for item in projection.get("instructions", [])
            ]
            serialized = _canonical(payload)

        if len(serialized) > self.max_total_chars:
            minimal = {
                "schema_version": "repository-context-v1",
                "snapshot_fingerprint": projection["snapshot_fingerprint"],
                "identity": projection["identity"],
                "languages": projection["languages"],
                "manifests": projection["manifests"],
                "aci_manifest": projection["aci_manifest"],
                "freshness_rule": "Use repository tools for exact current details.",
            }
            sections["repository_intelligence"]["data"] = minimal
            serialized = _canonical(payload)

        # If the governing base context itself leaves no room even for the minimal M23
        # projection, preserve task/working-state authority rather than silently dropping it.
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
        workspace = Path(workspace_root).resolve()
        repository = RepositoryIntelligenceService(workspace)
        repository.snapshot()
        repository_registry = build_repository_coding_registry(
            workspace,
            allowed_executables=allowed_executables,
            repository_service=repository,
            semantic_provider=semantic_provider,
        )
        wrapped_core = RepositoryContextReasoningCore(
            core,
            repository,
            tool_specs=repository_registry.specs(),
        )
        super().__init__(
            workspace,
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
        self.registry = repository_registry
        self.allowed_tools = tuple(spec.name for spec in self.registry.specs())
