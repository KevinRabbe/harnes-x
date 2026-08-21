from __future__ import annotations

import socket
import sys
from pathlib import Path

from harness_x.browser import ApplicationServerSpec, FakeBrowserProvider
from harness_x.coding.browser_verification import (
    BrowserPageVerificationCheck,
    BrowserVerificationPlan,
)
from harness_x.coding.long_horizon_runtime import (
    LongHorizonBrowserIsolatedRepositoryCodingTaskRuntime,
)
from harness_x.coding.verification import FileContainsVerificationCheck, VerificationPlan
from harness_x.reasoning import (
    RawActionProposal,
    RawProposal,
    RawReasoningOutput,
    ReasoningCoreInfo,
)


class SequenceCore:
    def __init__(self, outputs: list[RawReasoningOutput]) -> None:
        self.outputs = list(outputs)
        self._info = ReasoningCoreInfo(
            name="m27-browser-sequence",
            version="1",
            model="deterministic",
            transport="in_process",
            model_inference=False,
        )

    @property
    def info(self) -> ReasoningCoreInfo:
        return self._info

    def generate(self, context) -> RawReasoningOutput:
        if not self.outputs:
            raise RuntimeError("sequence exhausted")
        return self.outputs.pop(0)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_long_horizon_state_composes_with_browser_verification_and_m24_isolation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("index.html").write_text("Broken\n", encoding="utf-8")
    port = _free_port()
    application = ApplicationServerSpec(
        argv=(sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"),
        base_url=f"http://127.0.0.1:{port}",
    )
    browser_plan = BrowserVerificationPlan(
        checks=(
            BrowserPageVerificationCheck(
                check_id="page_ready",
                name="page reports Ready",
                snapshot_contains=("Ready",),
            ),
        )
    )
    code_plan = VerificationPlan(
        checks=(
            FileContainsVerificationCheck(
                check_id="source_ready",
                name="source contains Ready",
                path="index.html",
                needle="Ready",
            ),
        )
    )

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
                proposals=(
                    RawProposal(
                        summary="retain UI obligation",
                        payload={
                            "kind": "long_horizon_state_update",
                            "add_obligations": [
                                {
                                    "text": "The browser must continue to show Ready",
                                    "rationale": "browser acceptance criterion",
                                    "priority": 0.9,
                                }
                            ],
                        },
                    ),
                ),
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

    report = LongHorizonBrowserIsolatedRepositoryCodingTaskRuntime(
        source,
        core,
        tmp_path / "run",
        application=application,
        browser_verification_plan=browser_plan,
        browser_provider_factory=provider_factory,
        verification_plan=code_plan,
        isolation_root=tmp_path / "isolated",
        retention="never",
        baseline_verification=False,
        max_reasoning_steps=6,
        max_tool_actions=20,
    ).run("Make the local page report Ready")

    assert report.succeeded is True
    assert source.joinpath("index.html").read_text(encoding="utf-8") == "Broken\n"
    exported = Path(report.isolation.changed_files_root) / "index.html"
    assert exported.read_text(encoding="utf-8") == "Ready\n"
    assert report.browser_verification_runs[-1].verdict.value == "pass"
    assert report.long_horizon_checkpoint_count >= 3
    state_path = Path(report.long_horizon_state_path)
    evidence_path = Path(report.long_horizon_evidence_path)
    assert state_path.is_file()
    assert evidence_path.is_file()
    assert "The browser must continue to show Ready" in state_path.read_text(encoding="utf-8")
    evidence = evidence_path.read_text(encoding="utf-8")
    assert "tool:workspace_patch" in evidence
    assert "typed_browser_verification" in evidence
