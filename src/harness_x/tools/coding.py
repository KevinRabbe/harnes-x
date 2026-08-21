"""Workspace-scoped tools for real coding tasks."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import SideEffectLevel, ToolDefinition, ToolRegistry, ToolSpec

_DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
        "build",
    }
)
_DEFAULT_ALLOWED_EXECUTABLES = frozenset(
    {"python", "python3", "pytest", "ruff", "node", "npm", "pnpm", "yarn", "git"}
)
_READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {"status", "diff", "show", "log", "ls-files", "rev-parse"}
)
_PACKAGE_MANAGER_SUBCOMMANDS = frozenset({"run", "test"})


def _inside(root: Path, relative_path: str) -> Path:
    relative_path = relative_path.strip() or "."
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("workspace tool refuses paths outside its root") from exc
    return candidate


def _relative(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    return "." if not relative.parts else relative.as_posix()


def _walk_files(root: Path, start: Path, *, max_files: int = 4000):
    seen = 0
    for current, dirs, files in os.walk(start):
        dirs[:] = sorted(name for name in dirs if name not in _DEFAULT_IGNORED_DIRS)
        base = Path(current)
        for name in sorted(files):
            path = base / name
            try:
                path.relative_to(root)
            except ValueError:
                continue
            seen += 1
            if seen > max_files:
                return
            yield path


class WorkspaceListInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = "."
    recursive: bool = False
    max_entries: int = Field(default=200, ge=1, le=1000)


class WorkspaceEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    kind: str
    size_bytes: int = Field(ge=0)


class WorkspaceListOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    entries: tuple[WorkspaceEntry, ...]
    truncated: bool = False


def workspace_list_definition(root: str | Path) -> ToolDefinition:
    workspace_root = Path(root).resolve()

    def handler(request: WorkspaceListInput) -> WorkspaceListOutput:
        target = _inside(workspace_root, request.path)
        if not target.exists():
            raise FileNotFoundError(request.path)
        if not target.is_dir():
            raise ValueError("workspace_list path must be a directory")
        paths = (
            sorted(target.rglob("*"), key=lambda p: p.as_posix())
            if request.recursive
            else sorted(target.iterdir(), key=lambda p: p.name.casefold())
        )
        entries: list[WorkspaceEntry] = []
        truncated = False
        for path in paths:
            if any(
                part in _DEFAULT_IGNORED_DIRS
                for part in path.relative_to(workspace_root).parts
            ):
                continue
            if len(entries) >= request.max_entries:
                truncated = True
                break
            try:
                size = path.stat().st_size if path.is_file() else 0
            except OSError:
                size = 0
            entries.append(
                WorkspaceEntry(
                    path=_relative(workspace_root, path),
                    kind="directory" if path.is_dir() else "file",
                    size_bytes=size,
                )
            )
        return WorkspaceListOutput(entries=tuple(entries), truncated=truncated)

    return ToolDefinition(
        spec=ToolSpec(
            name="workspace_list",
            version="workspace-list-v1",
            input_schema=WorkspaceListInput.model_json_schema(),
            output_schema=WorkspaceListOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=5.0,
            idempotent=True,
        ),
        input_model=WorkspaceListInput,
        output_model=WorkspaceListOutput,
        handler=handler,
    )


class WorkspaceReadInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=300, ge=1, le=1000)
    max_bytes: int = Field(default=131072, ge=1024, le=1048576)


class WorkspaceReadOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    start_line: int
    end_line: int
    total_lines: int
    content: str
    truncated: bool


def workspace_read_definition(root: str | Path) -> ToolDefinition:
    workspace_root = Path(root).resolve()

    def handler(request: WorkspaceReadInput) -> WorkspaceReadOutput:
        target = _inside(workspace_root, request.path)
        if not target.is_file():
            raise FileNotFoundError(request.path)
        raw = target.read_bytes()
        byte_truncated = len(raw) > request.max_bytes
        raw = raw[: request.max_bytes]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("workspace_read only supports UTF-8 text files") from exc
        lines = text.splitlines(keepends=True)
        start = request.start_line - 1
        selected = lines[start : start + request.max_lines]
        end_line = request.start_line + max(0, len(selected) - 1)
        return WorkspaceReadOutput(
            path=_relative(workspace_root, target),
            start_line=request.start_line,
            end_line=end_line,
            total_lines=len(lines),
            content="".join(selected),
            truncated=byte_truncated or start + len(selected) < len(lines),
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="workspace_read",
            version="workspace-read-v1",
            input_schema=WorkspaceReadInput.model_json_schema(),
            output_schema=WorkspaceReadOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=5.0,
            idempotent=True,
        ),
        input_model=WorkspaceReadInput,
        output_model=WorkspaceReadOutput,
        handler=handler,
    )


class WorkspaceSearchInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=500)
    path: str = "."
    max_matches: int = Field(default=100, ge=1, le=500)
    max_file_bytes: int = Field(default=262144, ge=1024, le=2097152)


class WorkspaceSearchMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    line: int = Field(ge=1)
    text: str


class WorkspaceSearchOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    matches: tuple[WorkspaceSearchMatch, ...]
    truncated: bool


def workspace_search_definition(root: str | Path) -> ToolDefinition:
    workspace_root = Path(root).resolve()

    def handler(request: WorkspaceSearchInput) -> WorkspaceSearchOutput:
        start = _inside(workspace_root, request.path)
        if not start.exists():
            raise FileNotFoundError(request.path)
        files = (start,) if start.is_file() else _walk_files(workspace_root, start)
        matches: list[WorkspaceSearchMatch] = []
        truncated = False
        for path in files:
            try:
                if not path.is_file() or path.stat().st_size > request.max_file_bytes:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if request.query not in line:
                    continue
                if len(matches) >= request.max_matches:
                    truncated = True
                    break
                matches.append(
                    WorkspaceSearchMatch(
                        path=_relative(workspace_root, path),
                        line=line_no,
                        text=line[:500],
                    )
                )
            if truncated:
                break
        return WorkspaceSearchOutput(matches=tuple(matches), truncated=truncated)

    return ToolDefinition(
        spec=ToolSpec(
            name="workspace_search",
            version="workspace-search-v1",
            input_schema=WorkspaceSearchInput.model_json_schema(),
            output_schema=WorkspaceSearchOutput.model_json_schema(),
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=10.0,
            idempotent=True,
        ),
        input_model=WorkspaceSearchInput,
        output_model=WorkspaceSearchOutput,
        handler=handler,
    )


class WorkspaceWriteInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    content: str = Field(max_length=1000000)
    overwrite: bool = False


class WorkspaceWriteOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    bytes_written: int = Field(ge=0)
    created: bool


def workspace_write_definition(root: str | Path) -> ToolDefinition:
    workspace_root = Path(root).resolve()

    def handler(request: WorkspaceWriteInput) -> WorkspaceWriteOutput:
        target = _inside(workspace_root, request.path)
        existed = target.exists()
        if existed and not request.overwrite:
            raise FileExistsError(
                "target exists; set overwrite=true or use workspace_patch"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = request.content.encode("utf-8")
        target.write_bytes(payload)
        return WorkspaceWriteOutput(
            path=_relative(workspace_root, target),
            bytes_written=len(payload),
            created=not existed,
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="workspace_write",
            version="workspace-write-v1",
            input_schema=WorkspaceWriteInput.model_json_schema(),
            output_schema=WorkspaceWriteOutput.model_json_schema(),
            permissions=("workspace.write",),
            side_effect_level=SideEffectLevel.PERSISTENT,
            cost_class="medium",
            timeout_seconds=10.0,
            idempotent=True,
        ),
        input_model=WorkspaceWriteInput,
        output_model=WorkspaceWriteOutput,
        handler=handler,
    )


class WorkspacePatchInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1, max_length=500000)
    new_text: str = Field(max_length=500000)
    expected_occurrences: int = Field(default=1, ge=1, le=100)


class WorkspacePatchOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    replacements: int = Field(ge=1)
    bytes_written: int = Field(ge=0)


def workspace_patch_definition(root: str | Path) -> ToolDefinition:
    workspace_root = Path(root).resolve()

    def handler(request: WorkspacePatchInput) -> WorkspacePatchOutput:
        target = _inside(workspace_root, request.path)
        if not target.is_file():
            raise FileNotFoundError(request.path)
        text = target.read_text(encoding="utf-8")
        occurrences = text.count(request.old_text)
        if occurrences != request.expected_occurrences:
            raise ValueError(
                f"expected {request.expected_occurrences} exact occurrences, found {occurrences}"
            )
        updated = text.replace(
            request.old_text, request.new_text, request.expected_occurrences
        )
        payload = updated.encode("utf-8")
        target.write_bytes(payload)
        return WorkspacePatchOutput(
            path=_relative(workspace_root, target),
            replacements=request.expected_occurrences,
            bytes_written=len(payload),
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="workspace_patch",
            version="workspace-patch-v1",
            input_schema=WorkspacePatchInput.model_json_schema(),
            output_schema=WorkspacePatchOutput.model_json_schema(),
            permissions=("workspace.write",),
            side_effect_level=SideEffectLevel.PERSISTENT,
            cost_class="medium",
            timeout_seconds=10.0,
            idempotent=False,
        ),
        input_model=WorkspacePatchInput,
        output_model=WorkspacePatchOutput,
        handler=handler,
    )


class ProcessRunInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    argv: tuple[str, ...] = Field(min_length=1, max_length=32)
    cwd: str = "."
    timeout_seconds: float = Field(default=120.0, gt=0.0, le=300.0)
    max_output_chars: int = Field(default=20000, ge=1000, le=100000)

    @field_validator("argv")
    @classmethod
    def non_blank_argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("process argv cannot contain blank entries")
        return value


class ProcessRunOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    argv: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    output_truncated: bool


def _normalized_executable(value: str) -> str:
    name = Path(value).name.casefold()
    return name[:-4] if name.endswith(".exe") else name


def _validate_command(argv: tuple[str, ...], allowed: frozenset[str]) -> None:
    executable = _normalized_executable(argv[0])
    if executable not in allowed:
        raise PermissionError(f"executable {executable!r} is not allowed")
    if executable == "git":
        if len(argv) < 2 or argv[1].casefold() not in _READ_ONLY_GIT_SUBCOMMANDS:
            raise PermissionError("coding runtime permits read-only git commands only")
    if executable in {"npm", "pnpm", "yarn"}:
        if len(argv) < 2 or argv[1].casefold() not in _PACKAGE_MANAGER_SUBCOMMANDS:
            raise PermissionError(
                f"coding runtime permits only {sorted(_PACKAGE_MANAGER_SUBCOMMANDS)} for {executable}"
            )


def _sanitized_env() -> dict[str, str]:
    keep = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "PYTHONHOME",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.setdefault("PYTHONUTF8", "1")
    return env


def process_run_definition(
    root: str | Path,
    *,
    allowed_executables: frozenset[str] = _DEFAULT_ALLOWED_EXECUTABLES,
) -> ToolDefinition:
    workspace_root = Path(root).resolve()
    normalized_allowed = frozenset(
        _normalized_executable(item) for item in allowed_executables
    )

    def handler(request: ProcessRunInput) -> ProcessRunOutput:
        _validate_command(request.argv, normalized_allowed)
        cwd = _inside(workspace_root, request.cwd)
        if not cwd.is_dir():
            raise NotADirectoryError(request.cwd)
        try:
            completed = subprocess.run(
                list(request.argv),
                cwd=cwd,
                env=_sanitized_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=request.timeout_seconds,
                check=False,
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            stdout += f"\nPROCESS TIMEOUT after {request.timeout_seconds}s"
            returncode = 124
        combined_length = len(stdout) + len(stderr)
        limit = request.max_output_chars
        if combined_length > limit:
            half = max(1, limit // 2)
            stdout = stdout[:half]
            stderr = stderr[: max(0, limit - len(stdout))]
        return ProcessRunOutput(
            argv=request.argv,
            cwd=_relative(workspace_root, cwd),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            output_truncated=combined_length > limit,
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="process_run",
            version="process-run-v1",
            input_schema=ProcessRunInput.model_json_schema(),
            output_schema=ProcessRunOutput.model_json_schema(),
            permissions=("workspace.execute",),
            side_effect_level=SideEffectLevel.PERSISTENT,
            cost_class="high",
            timeout_seconds=305.0,
            idempotent=False,
        ),
        input_model=ProcessRunInput,
        output_model=ProcessRunOutput,
        handler=handler,
    )


def build_coding_registry(
    root: str | Path,
    *,
    allowed_executables: frozenset[str] = _DEFAULT_ALLOWED_EXECUTABLES,
) -> ToolRegistry:
    workspace_root = Path(root).resolve()
    if not workspace_root.is_dir():
        raise ValueError("coding workspace root must be an existing directory")
    registry = ToolRegistry()
    registry.register(workspace_list_definition(workspace_root))
    registry.register(workspace_read_definition(workspace_root))
    registry.register(workspace_search_definition(workspace_root))
    registry.register(workspace_write_definition(workspace_root))
    registry.register(workspace_patch_definition(workspace_root))
    registry.register(
        process_run_definition(
            workspace_root, allowed_executables=allowed_executables
        )
    )
    return registry
