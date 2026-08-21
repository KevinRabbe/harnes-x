"""M23 workspace patch v2: exact-text and hash-guarded range modes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import SideEffectLevel, ToolDefinition, ToolSpec


def _inside(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path.strip()).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("workspace patch refuses paths outside its root") from exc
    return candidate


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class WorkspacePatchV2Input(BaseModel):
    """One syntactically simple model contract with software-enforced mode invariants."""

    model_config = ConfigDict(frozen=True)

    mode: Literal["exact", "range"] = "exact"
    path: str = Field(min_length=1)

    # exact mode
    old_text: str = Field(default="", max_length=500000)
    new_text: str = Field(default="", max_length=500000)
    expected_occurrences: int = Field(default=1, ge=1, le=100)

    # range mode
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=1, ge=1)
    expected_sha256: str = Field(default="", max_length=64)
    replacement: str = Field(default="", max_length=500000)
    preserve_trailing_newline: bool = True

    @model_validator(mode="after")
    def validate_mode_contract(self) -> "WorkspacePatchV2Input":
        if self.mode == "exact":
            if not self.old_text:
                raise ValueError("exact patch mode requires non-empty old_text")
            return self
        digest = self.expected_sha256.strip().casefold()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("range patch mode requires expected_sha256 as 64 hex characters")
        if self.start_line > self.end_line:
            raise ValueError("range patch start_line cannot exceed end_line")
        object.__setattr__(self, "expected_sha256", digest)
        return self


class WorkspacePatchV2Output(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["exact", "range"]
    path: str
    bytes_written: int = Field(ge=0)
    replacements: int = Field(default=0, ge=0)
    start_line: int | None = None
    end_line: int | None = None
    sha256_before: str | None = None
    sha256_after: str | None = None


def workspace_patch_v2_definition(root: str | Path) -> ToolDefinition:
    workspace_root = Path(root).resolve()

    def handler(request: WorkspacePatchV2Input) -> WorkspacePatchV2Output:
        target = _inside(workspace_root, request.path)
        if not target.is_file():
            raise FileNotFoundError(request.path)
        raw = target.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("workspace_patch only supports UTF-8 text files") from exc

        if request.mode == "exact":
            occurrences = text.count(request.old_text)
            if occurrences != request.expected_occurrences:
                raise ValueError(
                    f"expected {request.expected_occurrences} exact occurrences, found {occurrences}"
                )
            updated = text.replace(
                request.old_text,
                request.new_text,
                request.expected_occurrences,
            )
            payload = updated.encode("utf-8")
            target.write_bytes(payload)
            return WorkspacePatchV2Output(
                mode="exact",
                path=_relative(workspace_root, target),
                bytes_written=len(payload),
                replacements=request.expected_occurrences,
                sha256_before=_sha256(raw),
                sha256_after=_sha256(payload),
            )

        before = _sha256(raw)
        if before != request.expected_sha256:
            raise ValueError(
                "workspace_patch range mode refused stale file: expected_sha256 does not match"
            )
        lines = text.splitlines(keepends=True)
        if request.end_line > len(lines):
            raise ValueError(
                f"end_line {request.end_line} exceeds file line count {len(lines)}"
            )
        replacement = request.replacement
        selected_last = lines[request.end_line - 1]
        if (
            request.preserve_trailing_newline
            and replacement
            and request.end_line < len(lines)
            and not replacement.endswith(("\n", "\r"))
        ):
            if selected_last.endswith("\r\n"):
                replacement += "\r\n"
            elif selected_last.endswith("\n"):
                replacement += "\n"
        updated = "".join(
            [
                *lines[: request.start_line - 1],
                replacement,
                *lines[request.end_line :],
            ]
        )
        payload = updated.encode("utf-8")
        target.write_bytes(payload)
        return WorkspacePatchV2Output(
            mode="range",
            path=_relative(workspace_root, target),
            bytes_written=len(payload),
            start_line=request.start_line,
            end_line=request.end_line,
            sha256_before=before,
            sha256_after=_sha256(payload),
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="workspace_patch",
            version="workspace-patch-v2",
            input_schema=WorkspacePatchV2Input.model_json_schema(),
            output_schema=WorkspacePatchV2Output.model_json_schema(),
            permissions=("workspace.write",),
            side_effect_level=SideEffectLevel.PERSISTENT,
            cost_class="medium",
            timeout_seconds=10.0,
            idempotent=False,
        ),
        input_model=WorkspacePatchV2Input,
        output_model=WorkspacePatchV2Output,
        handler=handler,
    )
