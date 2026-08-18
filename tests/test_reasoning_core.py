from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness_x.benchmarks import run_reasoning_swap_probe
from harness_x.config import load_config
from harness_x.core.contracts import ComputeBudget, ReasoningRequest
from harness_x.core.ids import GoalId, RoutineId, TaskId
from harness_x.reasoning import (
    BoundedContextBuilder,
    ContextBudget,
    OpenAICompatibleReasoningCore,
    OpenAICompatibleSettings,
    ReasoningCoreError,
)


class _FixtureHandler(BaseHTTPRequestHandler):
    model_content: dict = {}
    received_requests: list[dict] = []

    def do_POST(self):  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).received_requests.append(payload)
        response = {
            "id": "fixture-response",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(type(self).model_content),
                    },
                }
            ],
        }
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):  # noqa: A003 - stdlib handler API
        del format, args


def _server(model_content: dict):
    class Handler(_FixtureHandler):
        pass

    Handler.model_content = model_content
    Handler.received_requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, Handler


def _request() -> ReasoningRequest:
    return ReasoningRequest(
        task_id=TaskId(value="task_reasoning_context"),
        goal_id=GoalId(value="goal_reasoning_context"),
        routine_id=RoutineId(value="routine_reasoning_context"),
        instruction="Choose one declared action and do not claim unverified memory is fact.",
        active_goal={"title": "Compute the requested value", "status": "active"},
        working_state=[
            {
                "memory_id": f"mem_work_{index:03d}",
                "priority": index / 20,
                "pinned": index == 19,
                "content": {"blob": "w" * 160},
            }
            for index in range(20)
        ],
        retrieved_memories=[
            {
                "memory_id": f"mem_episode_{index:03d}",
                "memory_class": "episodic",
                "verification": "unverified",
                "content": {"blob": "r" * 240},
            }
            for index in range(20)
        ],
        self_schema={
            "schema_version": "system-self-schema-v1",
            "system_version": "test-v1",
            "operating_mode": "task_active",
            "state_fingerprint": "a" * 64,
            "known_limitations": ["fixture"],
            "large_detail": "s" * 3000,
        },
        available_actions=[
            {"name": f"tool_{index:02d}", "tool_name": f"tool_{index:02d}"}
            for index in range(20)
        ],
        budget=ComputeBudget(max_reasoning_steps=4, max_tool_actions=2, max_output_tokens=512),
    )


def test_context_builder_is_deterministic_bounded_and_preserves_governing_state() -> None:
    builder = BoundedContextBuilder(
        ContextBudget(
            max_chars=1800,
            max_working_items=8,
            max_retrieved_items=8,
            max_available_actions=8,
        )
    )
    first = builder.build(_request())
    second = builder.build(_request())

    assert first.fingerprint == second.fingerprint
    assert first.serialized == second.serialized
    assert first.char_count <= 1800
    assert first.dropped_retrieved_items > 0
    assert first.dropped_working_items > 0
    assert first.payload["sections"]["active_goal"]["authority"] == "authoritative"
    assert first.payload["sections"]["compute_budget"]["authority"] == "externally_enforced"
    assert "Compute the requested value" in first.serialized


def test_remote_reasoning_endpoint_is_disabled_by_default() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatibleSettings(base_url="https://example.com/v1", model="remote")


def test_openai_compatible_adapter_accepts_only_minimal_structured_output() -> None:
    server, handler = _server(
        {
            "status": "continue",
            "actions": [
                {
                    "tool_name": "calculator",
                    "arguments": {"operation": "multiply", "a": 21, "b": 2},
                }
            ],
            "proposals": [],
            "observations": [],
            "requested_additional_steps": 0,
        }
    )
    try:
        core = OpenAICompatibleReasoningCore(
            OpenAICompatibleSettings(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="fixture-local-model",
                timeout_seconds=2,
            )
        )
        context = BoundedContextBuilder().build(_request())
        output = core.generate(context)
    finally:
        server.shutdown()
        server.server_close()

    assert output.actions[0].tool_name == "calculator"
    assert handler.received_requests
    sent = handler.received_requests[0]
    assert sent["model"] == "fixture-local-model"
    assert sent["temperature"] == 0
    assert "reasoning-context-v1" in sent["messages"][1]["content"]


def test_adapter_rejects_model_attempt_to_mint_candidate_identity() -> None:
    server, _ = _server(
        {
            "status": "continue",
            "actions": [
                {
                    "candidate_id": "candidate_model_tried_to_mint_this",
                    "tool_name": "calculator",
                    "arguments": {"operation": "multiply", "a": 21, "b": 2},
                }
            ],
        }
    )
    try:
        core = OpenAICompatibleReasoningCore(
            OpenAICompatibleSettings(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="fixture-local-model",
                timeout_seconds=2,
            )
        )
        with pytest.raises(ReasoningCoreError):
            core.generate(BoundedContextBuilder().build(_request()))
    finally:
        server.shutdown()
        server.server_close()


def test_same_architecture_runs_stub_and_http_reasoning_core(tmp_path) -> None:
    server, _ = _server(
        {
            "status": "continue",
            "actions": [
                {
                    "tool_name": "calculator",
                    "arguments": {"operation": "multiply", "a": 21, "b": 2},
                }
            ],
            "requested_additional_steps": 0,
        }
    )
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    try:
        real_core = OpenAICompatibleReasoningCore(
            OpenAICompatibleSettings(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="fixture-local-model",
                timeout_seconds=2,
            )
        )
        report = run_reasoning_swap_probe(tmp_path, config, real_core=real_core)
    finally:
        server.shutdown()
        server.server_close()

    assert report.passed
    assert report.same_surrounding_architecture
    assert report.stub.model_inference is False
    assert report.real.model_inference is True
    assert report.stub.tool_succeeded and report.real.tool_succeeded
    assert report.stub.verification_accepted and report.real.verification_accepted
    assert report.stub.proposal_unverified_model_provenance
    assert report.real.proposal_unverified_model_provenance
    assert not report.stub.private_reasoning_recorded
    assert not report.real.private_reasoning_recorded
