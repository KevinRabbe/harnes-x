"""Operator CLI for the optional Milestone 19A recurrent-depth research branch."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .recurrent_depth import (
    DEFAULT_DEPTHS,
    HuginnTransformersBackend,
    HuginnTransformersSettings,
    load_recurrent_depth_cases,
    run_recurrent_depth_research,
    run_reference_recurrent_depth_research,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-x-recurrent-depth",
        description=(
            "Benchmark fixed recurrent depth, then compare deterministic and learned "
            "external depth selectors without granting the model depth authority."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=("reference", "huginn"),
        default="reference",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=None,
        help="Train/eval JSONL required for --backend huginn",
    )
    parser.add_argument(
        "--depths",
        type=int,
        nargs="+",
        default=list(DEFAULT_DEPTHS),
    )
    parser.add_argument("--model", default="tomg-group-umd/huginn-0125")
    parser.add_argument("--device", default="auto")
    parser.add_argument(
        "--dtype",
        choices=("bfloat16", "float16", "float32"),
        default="bfloat16",
    )
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--allow-remote-code", action="store_true")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".harness-x/recurrent-depth"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    depths = tuple(sorted(set(args.depths)))
    if not depths or depths[0] < 1:
        parser.error("--depths must contain positive integers")

    if args.backend == "reference":
        if args.cases is not None:
            parser.error("--cases is only used with --backend huginn")
        report = run_reference_recurrent_depth_research(depths=depths)
    else:
        if args.cases is None:
            parser.error("--backend huginn requires --cases train/eval JSONL")
        train_cases, eval_cases = load_recurrent_depth_cases(args.cases)
        backend = HuginnTransformersBackend(
            HuginnTransformersSettings(
                model=args.model,
                device=args.device,
                dtype=args.dtype,
                max_new_tokens=args.max_new_tokens,
                local_files_only=args.local_files_only,
                allow_remote_code=args.allow_remote_code,
            )
        )
        try:
            report = run_recurrent_depth_research(
                train_cases,
                eval_cases,
                backend,
                depths=depths,
                evidence_kind="model_benchmark",
            )
        finally:
            backend.close()

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "recurrent-depth-report.json").write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output / "learned-depth-selector.json").write_text(
        report.learned_selector.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(report.model_dump_json(indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
