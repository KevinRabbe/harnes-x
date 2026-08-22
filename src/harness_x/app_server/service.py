"""Single-user orchestration service for the local Harness X App Server.

The service schedules existing coding runtimes but does not acquire any of their authority.
Model output, tool execution, verification, memory, and completion remain owned by the coding
stack. M35 additionally discovers the one authoritative TraceStore ledger created inside a
session output root so HTTP clients can project it read-only.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from harness_x.coding.cli import (
    _build_browser_inputs,
    _build_verification_inputs,
    _runtime,
    build_parser as build_coding_parser,
)
from harness_x.coding.model_selection import (
    build_selected_reasoning_core,
    resolve_model_selection,
    write_model_selection_artifact,
)
from harness_x.core import TraceId

from .protocol import (
    AppEventKind,
    AppServerHealth,
    AppSessionSnapshot,
    AppSessionStatus,
    CodingSessionRequest,
)
from .store import AppSessionStore


class CodingRunner(Protocol):
    def __call__(self, snapshot: AppSessionSnapshot) -> BaseModel: ...


class HarnessCodingRunner:
    """Adapt an M34 request to the existing isolated M30/M32 coding runtime."""

    def __call__(self, snapshot: AppSessionSnapshot) -> BaseModel:
        request = snapshot.request
        argv = [
            str(request.workspace_root),
            "--task",
            request.task,
            "--model-profile",
            request.model_profile,
            "--output",
            snapshot.output_root,
            "--max-reasoning-steps",
            str(request.max_reasoning_steps),
            "--max-tool-actions",
            str(request.max_tool_actions),
            "--max-output-tokens",
            str(request.max_output_tokens),
        ]
        for command in request.verification_commands:
            argv.extend(("--verify", command))
        if request.verification_plan_path is not None:
            argv.extend(("--verification-plan", str(request.verification_plan_path)))
        if request.project_memory_root is not None:
            argv.extend(("--project-memory-root", str(request.project_memory_root)))
        if request.project_memory_key is not None:
            argv.extend(("--project-memory-key", request.project_memory_key))
        if not request.baseline_verification:
            argv.append("--no-baseline-verify")
        if request.browser_application_spec_path is not None:
            argv.extend(
                ("--application-spec", str(request.browser_application_spec_path))
            )
        if request.browser_verification_plan_path is not None:
            argv.extend(
                (
                    "--browser-verification-plan",
                    str(request.browser_verification_plan_path),
                )
            )
        if request.browser_headed:
            argv.append("--browser-headed")

        args = build_coding_parser().parse_args(argv)
        verification_plan, verification_commands = _build_verification_inputs(args)
        browser_inputs = _build_browser_inputs(args)
        selection = resolve_model_selection(args)
        core = build_selected_reasoning_core(selection)
        write_model_selection_artifact(selection, args.output)
        runtime = _runtime(args, core, verification_plan, browser_inputs)
        try:
            return runtime.run(
                args.task,
                verification_commands=verification_commands,
            )
        finally:
            close = getattr(core, "close", None)
            if callable(close):
                close()


class AppServerService:
    """Persistent one-worker session scheduler for personal local use."""

    def __init__(
        self,
        root: str | Path,
        *,
        runner: CodingRunner | None = None,
        server_version: str = "0.1.0a0+app-server35-trace-projection",
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = AppSessionStore(self.root / "sessions")
        self.run_root = self.root / "runs"
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.runner = runner or HarnessCodingRunner()
        self.server_version = server_version
        self._queue: deque[str] = deque()
        self._condition = threading.Condition()
        self._stopping = False
        self._active_session_id: str | None = None
        self._recover_sessions()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="harness-x-app-server-worker",
            daemon=True,
        )
        self._worker.start()

    @property
    def active_session_id(self) -> str | None:
        with self._condition:
            return self._active_session_id

    def health(self) -> AppServerHealth:
        sessions = self.store.sessions
        active = sum(
            1
            for item in sessions
            if item.status in {AppSessionStatus.RUNNING, AppSessionStatus.CANCEL_REQUESTED}
        )
        return AppServerHealth(
            server_version=self.server_version,
            active_sessions=active,
            total_sessions=len(sessions),
        )

    def create_session(self, request: CodingSessionRequest) -> AppSessionSnapshot:
        self._validate_launch_request(request)
        run_root = self.run_root / f"run_{uuid.uuid4().hex}"
        snapshot = self.store.create_session(request, output_root=run_root)
        with self._condition:
            if self._stopping:
                self.store.transition(
                    snapshot.session_id,
                    status=AppSessionStatus.FAILED,
                    kind=AppEventKind.SESSION_FAILED,
                    failure_reason="app_server_is_stopping",
                )
                return self.store.session(snapshot.session_id)
            self._queue.append(snapshot.session_id)
            self._condition.notify_all()
        return snapshot

    def session(self, session_id: str) -> AppSessionSnapshot:
        return self.store.session(session_id)

    def sessions(self) -> tuple[AppSessionSnapshot, ...]:
        return self.store.sessions

    def discover_trace(self, session_id: str) -> AppSessionSnapshot:
        """Attach the unique TraceStore file if the runtime has created it yet.

        Discovery is idempotent and does not parse or copy trace records. Integrity validation
        happens when M35 projects the source ledger.
        """

        snapshot = self.store.session(session_id)
        if snapshot.trace_id is not None:
            return snapshot
        output_root = Path(snapshot.output_root).resolve()
        if not output_root.is_dir():
            return snapshot
        matches = sorted(
            path.resolve()
            for path in output_root.glob("trace_*.jsonl")
            if path.is_file()
        )
        if not matches:
            return snapshot
        if len(matches) != 1:
            raise RuntimeError(
                f"app session has ambiguous causal traces: {len(matches)} files"
            )
        trace_path = matches[0]
        trace_id = trace_path.stem
        TraceId(value=trace_id)
        return self.store.attach_trace(
            session_id,
            trace_id=trace_id,
            path=trace_path,
        )

    def cancel(self, session_id: str) -> AppSessionSnapshot:
        snapshot = self.store.request_cancel(session_id)
        with self._condition:
            if session_id in self._queue:
                self._queue = deque(item for item in self._queue if item != session_id)
                snapshot = self.store.transition(
                    session_id,
                    status=AppSessionStatus.CANCELLED,
                    kind=AppEventKind.SESSION_CANCELLED,
                    payload={"reason": "cancelled_before_execution"},
                )
            self._condition.notify_all()
        return snapshot

    def close(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._worker.is_alive() and threading.current_thread() is not self._worker:
            self._worker.join(timeout=2.0)

    @staticmethod
    def _validate_launch_request(request: CodingSessionRequest) -> None:
        if not request.workspace_root.is_dir():
            raise ValueError(
                f"app-server workspace does not exist: {request.workspace_root}"
            )
        for label, path in (
            ("verification_plan_path", request.verification_plan_path),
            ("browser_application_spec_path", request.browser_application_spec_path),
            ("browser_verification_plan_path", request.browser_verification_plan_path),
        ):
            if path is not None and not path.is_file():
                raise ValueError(f"app-server {label} does not exist: {path}")
        memory_root = request.project_memory_root
        if memory_root is not None and memory_root.exists() and not memory_root.is_dir():
            raise ValueError(
                f"app-server project_memory_root is not a directory: {memory_root}"
            )

    def _recover_sessions(self) -> None:
        for snapshot in self.store.sessions:
            if snapshot.status == AppSessionStatus.CREATED:
                try:
                    self._validate_launch_request(snapshot.request)
                except ValueError as exc:
                    self.store.transition(
                        snapshot.session_id,
                        status=AppSessionStatus.FAILED,
                        kind=AppEventKind.SESSION_FAILED,
                        failure_reason=f"restart_launch_validation_failed: {exc}",
                    )
                else:
                    self._queue.append(snapshot.session_id)
            elif snapshot.status in {
                AppSessionStatus.RUNNING,
                AppSessionStatus.CANCEL_REQUESTED,
            }:
                # Preserve the durable trace pointer if it was already attached; running-task
                # instruction state is still not resumed.
                self.store.transition(
                    snapshot.session_id,
                    status=AppSessionStatus.FAILED,
                    kind=AppEventKind.SESSION_FAILED,
                    failure_reason="app_server_restart_interrupted_running_session",
                )

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._stopping:
                    self._condition.wait(timeout=0.5)
                if self._stopping:
                    return
                session_id = self._queue.popleft()
                self._active_session_id = session_id
            try:
                self._run_one(session_id)
            finally:
                with self._condition:
                    self._active_session_id = None
                    self._condition.notify_all()

    def _discover_trace_without_affecting_outcome(self, session_id: str) -> None:
        try:
            self.discover_trace(session_id)
        except (OSError, RuntimeError, ValueError):
            # Trace projection is observability only. A discovery/projection issue must not
            # rewrite the coding runtime's independently established success/failure outcome.
            return

    def _run_one(self, session_id: str) -> None:
        current = self.store.session(session_id)
        if current.status == AppSessionStatus.CANCEL_REQUESTED:
            self.store.transition(
                session_id,
                status=AppSessionStatus.CANCELLED,
                kind=AppEventKind.SESSION_CANCELLED,
                payload={"reason": "cancelled_before_execution"},
            )
            return
        if current.status != AppSessionStatus.CREATED:
            return
        self.store.transition(
            session_id,
            status=AppSessionStatus.RUNNING,
            kind=AppEventKind.SESSION_STARTED,
        )
        try:
            report = self.runner(self.store.session(session_id))
        except BaseException as exc:
            self._discover_trace_without_affecting_outcome(session_id)
            self.store.transition(
                session_id,
                status=AppSessionStatus.FAILED,
                kind=AppEventKind.SESSION_FAILED,
                failure_reason=f"{type(exc).__name__}: {str(exc)[:3600]}",
            )
            return

        self._discover_trace_without_affecting_outcome(session_id)
        report_path = Path(self.store.session(session_id).output_root) / "coding-task-report.json"
        if report_path.exists():
            self.store.add_artifact(
                session_id,
                artifact_kind="coding_task_report",
                path=report_path,
            )
        succeeded = bool(getattr(report, "succeeded", False))
        failure_reason = getattr(report, "failure_reason", None)
        current = self.store.session(session_id)
        if current.status == AppSessionStatus.CANCEL_REQUESTED and not succeeded:
            self.store.transition(
                session_id,
                status=AppSessionStatus.CANCELLED,
                kind=AppEventKind.SESSION_CANCELLED,
                coding_report_path=str(report_path) if report_path.exists() else None,
                failure_reason=str(failure_reason) if failure_reason else None,
                payload={"reason": "run_completed_after_cancel_request"},
            )
            return
        self.store.transition(
            session_id,
            status=(AppSessionStatus.SUCCEEDED if succeeded else AppSessionStatus.FAILED),
            kind=(AppEventKind.SESSION_COMPLETED if succeeded else AppEventKind.SESSION_FAILED),
            coding_report_path=str(report_path) if report_path.exists() else None,
            failure_reason=(str(failure_reason) if failure_reason else None),
        )
