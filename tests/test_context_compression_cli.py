from __future__ import annotations

import json
from pathlib import Path

from harness_x.benchmarks.runtime import BenchmarkRuntime
from harness_x.cli import build_parser, main
from harness_x.config import load_config
from harness_x.telemetry.self_schema import SelfSchemaBuilder
from harness_x.training import CurriculumGenerator, build_training_cohort


def _cohort(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "default.yaml")
    runtime = BenchmarkRuntime.create(
        tmp_path / "source",
        config,
        name="compression-cli-source",
        working_capacity=16,
    )
    runtime.create_root_goal("compression cli grounded source")
    schema = SelfSchemaBuilder(
        config=config,
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
        known_limitations=("fixture:compression-cli",),
    ).build()
    dataset = CurriculumGenerator(config).generate(schema)
    cohort = build_training_cohort((dataset,))
    path = tmp_path / "cohort"
    cohort.write(path)
    return path


def test_context_compression_reference_cli_writes_report(tmp_path) -> None:
    cohort = _cohort(tmp_path)
    output = tmp_path / "report"
    code = main(
        [
            "benchmark-context-compression",
            str(cohort),
            "--reference",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    payload = json.loads(
        (output / "context-compression-report.json").read_text(encoding="utf-8")
    )
    assert payload["evidence_kind"] == "reference_simulator"
    assert payload["compression_qualified"] is True
    assert payload["selected_profile"] == "standard"


def test_training_cli_accepts_explicit_backend_without_loading_it() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "train-self-model-adapter",
            "prepared",
            "--backend",
            "unsloth",
        ]
    )
    assert args.backend == "unsloth"
