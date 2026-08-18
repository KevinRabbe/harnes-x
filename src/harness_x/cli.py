"""Harness X command line interface."""

from __future__ import annotations

import argparse
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

    validate = subparsers.add_parser(
        "validate-config",
        help="Validate a Harness X YAML configuration",
    )
    validate.add_argument("path", type=Path)

    verify_trace = subparsers.add_parser(
        "verify-trace",
        help="Validate ordering and integrity of an append-only trace ledger",
    )
    verify_trace.add_argument("path", type=Path)

    replay_fixture = subparsers.add_parser(
        "replay-fixture",
        help="Replay a portable trace fixture and verify its final state",
    )
    replay_fixture.add_argument("path", type=Path)

    benchmark = subparsers.add_parser(
        "benchmark-scripted",
        help="Run the Milestone 8 long-horizon scripted autonomy suite",
    )
    benchmark.add_argument("config", type=Path)
    benchmark.add_argument(
        "--output",
        type=Path,
        default=Path(".harness-x/benchmark-scripted"),
        help="Directory for scenario traces/checkpoints",
    )

    swap = subparsers.add_parser(
        "benchmark-reasoning-swap",
        help="Compare the deterministic core with an OpenAI-compatible reasoning endpoint",
    )
    swap.add_argument("config", type=Path)
    swap.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080/v1",
        help="OpenAI-compatible API base URL; loopback-only unless --allow-remote is set",
    )
    swap.add_argument("--model", default="local-model")
    swap.add_argument("--api-key-env", default=None)
    swap.add_argument("--allow-remote", action="store_true")
    swap.add_argument(
        "--output",
        type=Path,
        default=Path(".harness-x/benchmark-reasoning-swap"),
        help="Directory for stub/real traces and reasoning-swap-report.json",
    )

    assisted = subparsers.add_parser(
        "benchmark-model-assisted",
        help="Compare seven model-assisted routine decisions against deterministic baselines",
    )
    assisted.add_argument("config", type=Path)
    assisted.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080/v1",
        help="OpenAI-compatible API base URL; loopback-only unless --allow-remote is set",
    )
    assisted.add_argument("--model", default="local-model")
    assisted.add_argument("--api-key-env", default=None)
    assisted.add_argument("--allow-remote", action="store_true")
    assisted.add_argument(
        "--output",
        type=Path,
        default=Path(".harness-x/benchmark-model-assisted"),
        help="Directory for scenario traces and model-assisted-report.json",
    )

    return parser


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
        fixture = TraceFixture.model_validate_json(
            args.path.read_text(encoding="utf-8")
        )
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
        report_path = args.output / "reasoning-swap-report.json"
        report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
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
        report_path = args.output / "model-assisted-report.json"
        report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(report.model_dump_json(indent=2))
        return 0 if report.passed else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
