from pathlib import Path

from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.benchmarks.routines import BenchmarkStepRequest
from harness_x.config import load_config
from harness_x.core.contracts import Observation
from harness_x.telemetry.metrics import JsonlMetricsStore
from harness_x.telemetry.self_schema import SelfSchemaBuilder


def _runtime(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    runtime = BenchmarkRuntime.create(
        tmp_path / "self_schema",
        config,
        name="self_schema",
        working_capacity=12,
    )
    goal_id = runtime.create_root_goal("Expose grounded runtime state")
    return runtime, goal_id


def _builder(runtime):
    return SelfSchemaBuilder(
        config=runtime.config,
        recorder=runtime.recorder,
        orchestrator=runtime.orchestrator,
        goals=runtime.goals,
        working=runtime.working,
        episodic=runtime.episodic,
        errors=runtime.errors,
        semantic=runtime.semantic,
        procedural=runtime.procedural,
        engine=runtime.engine,
        registry=runtime.registry,
        granted_permissions=runtime.bindings.tool_permissions,
        known_limitations=(
            "full cold-restart reconstruction of every memory owner is not implemented",
            "in-process tool timeout cancellation is not guaranteed",
        ),
    )


def _run_one_step(runtime, goal_id):
    observation = Observation(
        task_id=runtime.recorder.task_id,
        kind="self_schema_probe",
        content={"value": 21},
        provenance=runtime.provenance,
    )
    return runtime.engine.execute(
        "benchmark_step",
        BenchmarkStepRequest(
            goal_id=goal_id,
            step_key="self_schema_step_01",
            observation=observation,
            tool_name="calculator",
            tool_arguments={"operation": "multiply", "a": 21, "b": 2},
            expected_result={"value": 42.0},
            required_result_keys=("value",),
        ),
    )


def test_self_schema_is_grounded_complete_and_observationally_pure(tmp_path) -> None:
    runtime, goal_id = _runtime(tmp_path)
    execution = _run_one_step(runtime, goal_id)
    assert execution.result.status.value == "succeeded"

    builder = _builder(runtime)
    trace_path = runtime.recorder.store.path
    before_bytes = trace_path.read_bytes()
    before_count = len(runtime.recorder.store.events(trace_id=runtime.recorder.trace_id))

    first = builder.build()
    second = builder.build()

    after_bytes = trace_path.read_bytes()
    after_count = len(runtime.recorder.store.events(trace_id=runtime.recorder.trace_id))
    assert before_bytes == after_bytes
    assert before_count == after_count
    assert first.state_fingerprint == second.state_fingerprint

    assert first.system_version == str(runtime.orchestrator.session.system_version)
    assert first.task_id == str(runtime.orchestrator.session.task_id)
    assert first.operating_mode == runtime.orchestrator.session.mode.value
    assert first.active_routine is None
    assert first.budget_usage == runtime.orchestrator.session.usage.model_dump(mode="json")

    memory_by_name = {item.memory_class: item for item in first.memories}
    assert set(memory_by_name) == {
        "goal",
        "working",
        "episodic",
        "error",
        "semantic",
        "procedural",
    }
    assert memory_by_name["goal"].item_count == 1
    assert memory_by_name["working"].capacity_units == 12
    assert memory_by_name["working"].used_units == runtime.working.used_units
    assert memory_by_name["episodic"].item_count == 1

    gates = {item.gate_id: item for item in first.gates}
    assert set(gates) == {"retrieval", "write", "focus", "compute", "maintenance"}
    assert gates["retrieval"].policy_version == runtime.config.gates.retrieval.policy_version

    tools = {item.name: item for item in first.tools}
    assert set(tools) == {"calculator", "kv_read", "sandbox_write", "unreliable"}
    assert tools["sandbox_write"].side_effect_level == "persistent"
    assert "sandbox.write" in first.granted_permissions

    assert first.metrics.trace_events == before_count
    assert first.metrics.tool_actions == 1
    assert first.metrics.verifier_checks == 1
    assert first.metrics.verifier_rejections == 0
    assert first.known_limitations
    assert len(first.state_fingerprint) == 64


def test_metrics_samples_are_append_only_derived_data(tmp_path) -> None:
    runtime, goal_id = _runtime(tmp_path)
    builder = _builder(runtime)
    store = JsonlMetricsStore(tmp_path / "metrics.jsonl")

    first = builder.metrics_sample()
    store.append(first)
    _run_one_step(runtime, goal_id)
    second = builder.metrics_sample()
    store.append(second)

    samples = store.samples()
    assert samples == (first, second)
    assert store.latest() == second
    assert second.step > first.step
    assert second.metrics.trace_events > first.metrics.trace_events
    assert second.metrics.tool_actions == 1


def test_self_schema_component_inventory_is_versioned(tmp_path) -> None:
    runtime, _ = _runtime(tmp_path)
    schema = _builder(runtime).build()
    components = {item.component: item for item in schema.components}

    assert components["orchestrator"].version == "orchestrator-v1"
    assert components["trace"].version == "trace-v1"
    assert components["memory.semantic"].version == "semantic-v1"
    assert components["gate.compute"].version == runtime.config.gates.compute.policy_version
    assert components["routine.benchmark_step"].version == "benchmark-step-v1"
    assert components["tool.calculator"].version == "calculator-v1"
