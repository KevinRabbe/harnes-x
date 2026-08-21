from __future__ import annotations

import socket
import sys
from pathlib import Path

from harness_x.browser import ApplicationServerSpec, FakeBrowserProvider
from harness_x.coding.browser_runtime import (
    BrowserVerifiedIsolatedRepositoryCodingTaskRuntime,
    BrowserVerifiedRepositoryCodingTaskRuntime,
)
from harness_x.coding.browser_verification import (
    BrowserPageVerificationCheck,
    BrowserVerificationPlan,
)
from harness_x.coding.verification import (
    FileContainsVerificationCheck,
    FileExistsVerificationCheck,
    VerificationPlan,
    VerificationVerdict,
)
from harness_x.reasoning import RawActionProposal, RawReasoningOutput, ReasoningCoreInfo


class SequenceCore:
    def __init__(self, outputs: list[RawReasoningOutput]) -> None:
        self.outputs = list(outputs)
        self.contexts: list[str] = []
        self._info = ReasoningCoreInfo(
            name="m26-sequence-core",
            version="m26-sequence-v1",
            model="deterministic-sequence",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        self.contexts.append(context.serialized)
        if not self.outputs:
            raise RuntimeError("sequence core ran out of outputs")
        return self.outputs.pop(0)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_application(port: int) -> ApplicationServerSpec:
    return ApplicationServerSpec(
        argv=(
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
        ),
        base_url=f"http://127.0.0.1:{port}",
        startup_timeout_seconds=10.0,
        shutdown_timeout_seconds=3.0,
    )


def _browser_plan(expected: str = "Ready") -> BrowserVerificationPlan:
    return BrowserVerificationPlan(
        checks=(
            BrowserPageVerificationCheck(
                check_id="dashboard_ready",
                name="dashboard is ready",
                path="/",
                snapshot_contains=(expected,),
            ),
        )
    )


def test_browser_failure_returns_to_model_then_repair_passes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("index.html").write_text("Broken\n", encoding="utf-8")
    port = _free_port()

    def provider_factory(base_url: str, artifact_root: Path):
        content = workspace.joinpath("index.html").read_text(encoding="utf-8")
        state = "Ready" if "Ready" in content else "Broken"
        return FakeBrowserProvider(
            base_url,
            artifact_root,
            pages={"/": f'- heading "{state}" [level=1]'},
        )

    core = SequenceCore(
        [
            RawReasoningOutput(status="complete"),
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_patch",
                        arguments={
                            "mode": "exact",
                            "path": "index.html",
                            "old_text": "Broken",
                            "new_text": "Ready",
                        },
                    ),
                ),
            ),
            RawReasoningOutput(status="complete"),
        ]
    )
    code_plan = VerificationPlan(
        checks=(
            FileExistsVerificationCheck(
                check_id="index_exists",
                name="index exists",
                path="index.html",
            ),
        )
    )
    runtime = BrowserVerifiedRepositoryCodingTaskRuntime(
        workspace,
        core,
        tmp_path / "run",
        application=_http_application(port),
        browser_verification_plan=_browser_plan(),
        browser_provider_factory=provider_factory,
        verification_plan=code_plan,
        baseline_verification=False,
        max_reasoning_steps=8,
        max_tool_actions=20,
    )

    report = runtime.run("Make the dashboard Ready")

    assert report.succeeded is True
    assert workspace.joinpath("index.html").read_text(encoding="utf-8") == "Ready\n"
    assert len(report.browser_verification_runs) == 2
    assert report.browser_verification_runs[0].verdict == VerificationVerdict.FAIL
    assert report.browser_verification_runs[1].verdict == VerificationVerdict.PASS
    assert runtime.browser_session.application_state.running is False
    assert any("browser_snapshot" in context for context in core.contexts)
    assert any("browser_application" in context for context in core.contexts[1:])
    assert (tmp_path / "run" / "browser-verification-runs.json").is_file()


def test_app_source_mutation_invalidates_browser_success(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("index.html").write_text("Ready\n", encoding="utf-8")
    port = _free_port()
    workspace.joinpath("mutating_server.py").write_text(
        "from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler\n"
        "from pathlib import Path\n"
        "import sys\n"
        "Path('server-touched.txt').write_text('mutated by server\\n', encoding='utf-8')\n"
        "ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), SimpleHTTPRequestHandler).serve_forever()\n",
        encoding="utf-8",
    )
    application = ApplicationServerSpec(
        argv=(sys.executable, "mutating_server.py", str(port)),
        base_url=f"http://127.0.0.1:{port}",
        startup_timeout_seconds=10.0,
        shutdown_timeout_seconds=3.0,
    )

    def provider_factory(base_url: str, artifact_root: Path):
        return FakeBrowserProvider(
            base_url,
            artifact_root,
            pages={"/": '- heading "Ready" [level=1]'},
        )

    runtime = BrowserVerifiedRepositoryCodingTaskRuntime(
        workspace,
        SequenceCore(
            [
                RawReasoningOutput(status="complete"),
                RawReasoningOutput(status="blocked"),
            ]
        ),
        tmp_path / "run",
        application=application,
        browser_verification_plan=_browser_plan(),
        browser_provider_factory=provider_factory,
        verification_plan=VerificationPlan(
            checks=(
                FileExistsVerificationCheck(
                    check_id="index_exists",
                    name="index exists",
                    path="index.html",
                ),
            )
        ),
        baseline_verification=False,
        max_reasoning_steps=5,
        max_tool_actions=20,
    )

    report = runtime.run("Confirm the dashboard")

    assert report.succeeded is False
    assert len(report.browser_verification_runs) == 1
    run = report.browser_verification_runs[0]
    assert run.verdict == VerificationVerdict.FAIL
    assert run.code_verification_fresh_after is False
    assert "__code_verification_freshness__" in run.required_failures
    assert workspace.joinpath("server-touched.txt").exists()


def test_isolated_browser_runtime_keeps_operator_source_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("index.html").write_text("Broken\n", encoding="utf-8")
    port = _free_port()

    def provider_factory(base_url: str, artifact_root: Path):
        return FakeBrowserProvider(
            base_url,
            artifact_root,
            pages={"/": '- heading "Ready" [level=1]'},
        )

    core = SequenceCore(
        [
            RawReasoningOutput(
                status="continue",
                actions=(
                    RawActionProposal(
                        tool_name="workspace_patch",
                        arguments={
                            "mode": "exact",
                            "path": "index.html",
                            "old_text": "Broken",
                            "new_text": "Ready",
                        },
                    ),
                ),
            ),
            RawReasoningOutput(status="complete"),
        ]
    )
    runtime = BrowserVerifiedIsolatedRepositoryCodingTaskRuntime(
        source,
        core,
        tmp_path / "run",
        application=_http_application(port),
        browser_verification_plan=_browser_plan(),
        browser_provider_factory=provider_factory,
        verification_plan=VerificationPlan(
            checks=(
                FileContainsVerificationCheck(
                    check_id="ready_file",
                    name="source says Ready",
                    path="index.html",
                    needle="Ready",
                ),
            )
        ),
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=5,
        max_tool_actions=20,
    )

    report = runtime.run("Make the page Ready")

    assert report.succeeded is True
    assert source.joinpath("index.html").read_text(encoding="utf-8") == "Broken\n"
    assert report.isolation.retained is False
    exported = Path(report.isolation.changed_files_root) / "index.html"
    assert exported.read_text(encoding="utf-8") == "Ready\n"


def test_browser_runtime_does_not_reuse_old_browser_failure_for_code_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("index.html").write_text(
        "RequiredValue Broken\n", encoding="utf-8"
    )
    port = _free_port()

    def provider_factory(base_url: str, artifact_root: Path):
        return FakeBrowserProvider(
            base_url,
            artifact_root,
            pages={"/": '- heading "Broken" [level=1]'},
        )

    runtime = BrowserVerifiedRepositoryCodingTaskRuntime(
        workspace,
        SequenceCore(
            [
                RawReasoningOutput(status="complete"),
                RawReasoningOutput(
                    status="continue",
                    actions=(
                        RawActionProposal(
                            tool_name="workspace_patch",
                            arguments={
                                "mode": "exact",
                                "path": "index.html",
                                "old_text": "RequiredValue",
                                "new_text": "MissingValue",
                            },
                        ),
                    ),
                ),
                RawReasoningOutput(status="complete"),
                RawReasoningOutput(status="blocked"),
            ]
        ),
        tmp_path / "run",
        application=_http_application(port),
        browser_verification_plan=_browser_plan(),
        browser_provider_factory=provider_factory,
        verification_plan=VerificationPlan(
            checks=(
                FileContainsVerificationCheck(
                    check_id="required_marker",
                    name="required marker exists",
                    path="index.html",
                    needle="RequiredValue",
                ),
            )
        ),
        baseline_verification=False,
        max_reasoning_steps=8,
        max_tool_actions=24,
    )

    report = runtime.run("Exercise independent verification identity")

    assert report.succeeded is False
    assert len(report.browser_verification_runs) == 1
    assert report.browser_verification_runs[0].verdict == VerificationVerdict.FAIL
    latest_code_run = report.verification_runs[-1]
    assert latest_code_run.verdict == VerificationVerdict.FAIL
    assert latest_code_run.failure_signature is not None
    assert runtime._browser_run_for_current_verification is None
    assert runtime._verification_failure_signature(report.verification) == latest_code_run.failure_signature
