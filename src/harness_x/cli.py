"""Harness X command line interface."""

from __future__ import annotations

import argparse
import gc
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import load_config
from .telemetry import TraceFixture, TraceReplayer, TraceStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-x",
        description="Architecture-first cognitive harness research system.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    validate = subparsers.add_parser("validate-config", help="Validate a Harness X YAML configuration")
    validate.add_argument("path", type=Path)

    verify_trace = subparsers.add_parser(
        "verify-trace", help="Validate ordering and integrity of an append-only trace ledger"
    )
    verify_trace.add_argument("path", type=Path)

    replay_fixture = subparsers.add_parser(
        "replay-fixture", help="Replay a portable trace fixture and verify its final state"
    )
    replay_fixture.add_argument("path", type=Path)

    benchmark = subparsers.add_parser(
        "benchmark-scripted", help="Run the Milestone 8 long-horizon scripted autonomy suite"
    )
    benchmark.add_argument("config", type=Path)
    benchmark.add_argument("--output", type=Path, default=Path(".harness-x/benchmark-scripted"))

    swap = subparsers.add_parser(
        "benchmark-reasoning-swap",
        help="Compare the deterministic core with an OpenAI-compatible reasoning endpoint",
    )
    swap.add_argument("config", type=Path)
    swap.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    swap.add_argument("--model", default="local-model")
    swap.add_argument("--api-key-env", default=None)
    swap.add_argument("--allow-remote", action="store_true")
    swap.add_argument("--output", type=Path, default=Path(".harness-x/benchmark-reasoning-swap"))

    assisted = subparsers.add_parser(
        "benchmark-model-assisted",
        help="Compare seven model-assisted routine decisions against deterministic baselines",
    )
    assisted.add_argument("config", type=Path)
    assisted.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    assisted.add_argument("--model", default="local-model")
    assisted.add_argument("--api-key-env", default=None)
    assisted.add_argument("--allow-remote", action="store_true")
    assisted.add_argument("--output", type=Path, default=Path(".harness-x/benchmark-model-assisted"))

    curriculum = subparsers.add_parser(
        "generate-self-model-curriculum",
        help="Generate grounded Milestone 12 train/eval JSONL from a SystemSelfSchema snapshot",
    )
    curriculum.add_argument("config", type=Path)
    curriculum.add_argument("self_schema", type=Path)
    curriculum.add_argument("--output", type=Path, default=Path(".harness-x/self-model-curriculum"))

    prepare = subparsers.add_parser(
        "prepare-self-model-training",
        help="Build a Milestone 13 train/eval cohort and PEFT training bundle",
    )
    prepare.add_argument("curricula", type=Path, nargs="+")
    prepare.add_argument("--holdout-architecture", action="append", default=[])
    prepare.add_argument("--base-model", required=True)
    prepare.add_argument("--method", choices=("lora", "qlora"), default="qlora")
    prepare.add_argument("--max-train-examples", type=int, default=1000)
    prepare.add_argument("--output", type=Path, default=Path(".harness-x/self-model-training"))

    train = subparsers.add_parser(
        "train-self-model-adapter",
        help="Run the optional LoRA/QLoRA backend on a prepared Milestone 13 bundle",
    )
    train.add_argument("prepared", type=Path)
    train.add_argument("--output", type=Path, default=Path(".harness-x/self-model-adapter"))

    evaluate = subparsers.add_parser(
        "evaluate-self-model-adapter",
        help="Compare base and adapter on the exact held-out self-model cohort",
    )
    evaluate.add_argument("cohort", type=Path)
    evaluate.add_argument("--base-model", required=True)
    evaluate.add_argument("--adapter", type=Path, required=True)
    evaluate.add_argument("--no-4bit", action="store_true")
    evaluate.add_argument("--max-new-tokens", type=int, default=512)
    evaluate.add_argument("--general-baseline-score", type=float, default=None)
    evaluate.add_argument("--general-adapter-score", type=float, default=None)
    evaluate.add_argument("--general-metric-name", default="general_regression_score")
    evaluate.add_argument("--output", type=Path, default=Path(".harness-x/self-model-evaluation"))

    return parser


def _release_predictor(predictor: object) -> None:
    del predictor
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate-config":
        config = load_config(args.path)
        print(f"valid: system_version={config.system_version}")
        return 0

    if args.command == "verify-trace":
        events = TraceStore(args.path).events()
        trace_count = len({str(event.trace_id) for event in events})
        print(f"valid: events={len(events)} traces={trace_count}")
        return 0

    if args.command == "replay-fixture":
        fixture = TraceFixture.model_validate_json(args.path.read_text(encoding="utf-8"))
        state = TraceReplayer().assert_fixture(fixture)
        print(f"valid: trace={state.trace_id} steps={state.last_step}")
        return 0

    if args.command == "benchmark-scripted":
        from .benchmarks import run_scripted_autonomy_benchmark

        config = load_config(args.config)
        report = run_scripted_autonomy_benchmark(config, args.output)
        print(report.model_dump_json(indent=2))
        return 0 if report.passed else 1

    if args.command == "benchmark-reasoning-swap":
        from .benchmarks import run_reasoning_swap_probe
        from .reasoning import OpenAICompatibleReasoningCore, OpenAICompatibleSettings

        config = load_config(args.config)
        core = OpenAICompatibleReasoningCore(
            OpenAICompatibleSettings(
                base_url=args.base_url,
                model=args.model,
                api_key_env=args.api_key_env,
                allow_remote_endpoint=args.allow_remote,
            )
        )
        args.output.mkdir(parents=True, exist_ok=True)
        report = run_reasoning_swap_probe(args.output, config, real_core=core)
        (args.output / "reasoning-swap-report.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        print(report.model_dump_json(indent=2))
        return 0 if report.passed else 1

    if args.command == "benchmark-model-assisted":
        from .benchmarks import run_model_assisted_benchmark
        from .reasoning import OpenAICompatibleReasoningCore, OpenAICompatibleSettings

        config = load_config(args.config)
        core = OpenAICompatibleReasoningCore(
            OpenAICompatibleSettings(
                base_url=args.base_url,
                model=args.model,
                api_key_env=args.api_key_env,
                allow_remote_endpoint=args.allow_remote,
            )
        )
        args.output.mkdir(parents=True, exist_ok=True)
        report = run_model_assisted_benchmark(args.output, config, core=core)
        (args.output / "model-assisted-report.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        print(report.model_dump_json(indent=2))
        return 0 if report.passed else 1

    if args.command == "generate-self-model-curriculum":
        from .telemetry.self_schema import SystemSelfSchema
        from .training import CurriculumGenerator

        config = load_config(args.config)
        schema = SystemSelfSchema.model_validate_json(args.self_schema.read_text(encoding="utf-8"))
        dataset = CurriculumGenerator(config).generate(schema)
        dataset.write(args.output)
        print(dataset.manifest.model_dump_json(indent=2))
        return 0

    if args.command == "prepare-self-model-training":
        from .training import (
            AdapterMethod,
            AdapterTrainingConfig,
            build_training_cohort,
            load_curriculum,
            prepare_training_bundle,
        )

        datasets = tuple(load_curriculum(path) for path in args.curricula)
        cohort = build_training_cohort(
            datasets, held_out_architecture_families=args.holdout_architecture
        )
        config = AdapterTrainingConfig(
            base_model=args.base_model,
            method=AdapterMethod(args.method),
            max_train_examples=args.max_train_examples,
        )
        bundle = prepare_training_bundle(cohort, config)
        args.output.mkdir(parents=True, exist_ok=True)
        cohort.write(args.output / "cohort")
        bundle.write(args.output)
        print(bundle.model_dump_json(indent=2, exclude={"train_records", "eval_records"}))
        return 0

    if args.command == "train-self-model-adapter":
        from .training import HuggingFacePeftTrainer, load_prepared_training_bundle

        bundle = load_prepared_training_bundle(args.prepared)
        artifact = HuggingFacePeftTrainer().train(bundle, args.output)
        print(artifact.model_dump_json(indent=2))
        return 0

    if args.command == "evaluate-self-model-adapter":
        from .training import (
            GeneralRegressionResult,
            HuggingFaceSelfModelPredictor,
            compare_base_and_adapter,
            evaluate_self_model,
            load_training_cohort,
        )

        cohort = load_training_cohort(args.cohort)
        load_in_4bit = not args.no_4bit
        baseline_predictor = HuggingFaceSelfModelPredictor(
            base_model=args.base_model,
            load_in_4bit=load_in_4bit,
            max_new_tokens=args.max_new_tokens,
        )
        baseline = evaluate_self_model(cohort.eval, baseline_predictor)
        _release_predictor(baseline_predictor)

        adapter_predictor = HuggingFaceSelfModelPredictor(
            base_model=args.base_model,
            adapter_path=args.adapter,
            load_in_4bit=load_in_4bit,
            max_new_tokens=args.max_new_tokens,
        )
        adapter = evaluate_self_model(cohort.eval, adapter_predictor)
        _release_predictor(adapter_predictor)

        general = None
        if args.general_baseline_score is not None or args.general_adapter_score is not None:
            if args.general_baseline_score is None or args.general_adapter_score is None:
                parser.error("both general regression scores must be supplied together")
            general = GeneralRegressionResult(
                baseline_score=args.general_baseline_score,
                adapter_score=args.general_adapter_score,
                metric_name=args.general_metric_name,
            )
        comparison = compare_base_and_adapter(
            baseline, adapter, general_regression=general
        )
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "base-evaluation.json").write_text(
            baseline.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (args.output / "adapter-evaluation.json").write_text(
            adapter.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (args.output / "adapter-comparison.json").write_text(
            comparison.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        print(comparison.model_dump_json(indent=2))
        return 0 if comparison.promotion_allowed else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
