"""Operator CLI for the Milestone 20 local empirical adapter experiment."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Sequence

from .adapter_training import TrainingBackend
from .empirical_experiment import run_empirical_adapter_experiment
from .evaluation import GeneralRegressionResult


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-x-empirical-adapter",
        description=(
            "Train one self-model adapter locally and produce a signed held-out/context "
            "compression evidence bundle."
        ),
    )
    parser.add_argument("prepared", type=Path, help="Prepared training directory containing cohort/")
    parser.add_argument(
        "--backend",
        choices=tuple(item.value for item in TrainingBackend),
        default=TrainingBackend.UNSLOTH.value,
    )
    parser.add_argument(
        "--base-model-revision",
        default=None,
        help="Exact 40-character model commit SHA; overrides only the temporary effective plan",
    )
    parser.add_argument(
        "--tokenizer-revision",
        default=None,
        help="Exact tokenizer commit SHA; defaults to the model revision",
    )
    parser.add_argument("--no-4bit-eval", action="store_true")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--general-baseline-score", type=float, default=None)
    parser.add_argument("--general-adapter-score", type=float, default=None)
    parser.add_argument("--general-metric-name", default="general_regression_score")
    parser.add_argument(
        "--reference",
        action="store_true",
        help="Run the deterministic mechanics fixture; no model weights or training backend are executed",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".harness-x/empirical-self-model"),
    )
    return parser


def _effective_prepared_copy(
    prepared: Path,
    destination: Path,
    *,
    base_model_revision: str | None,
    tokenizer_revision: str | None,
) -> Path:
    shutil.copytree(prepared, destination)
    plan_path = destination / "training-plan.json"
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    config = dict(payload["config"])
    if base_model_revision is not None:
        config["base_model_revision"] = base_model_revision
    if tokenizer_revision is not None:
        config["tokenizer_revision"] = tokenizer_revision
    elif base_model_revision is not None:
        config["tokenizer_revision"] = base_model_revision
    payload["config"] = config
    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if (args.general_baseline_score is None) != (args.general_adapter_score is None):
        parser.error("both general regression scores must be supplied together")
    general = None
    if args.general_baseline_score is not None:
        general = GeneralRegressionResult(
            baseline_score=args.general_baseline_score,
            adapter_score=args.general_adapter_score,
            metric_name=args.general_metric_name,
        )

    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be empty for an empirical run")

    with tempfile.TemporaryDirectory(prefix="harness-x-empirical-") as temp_root:
        effective = _effective_prepared_copy(
            args.prepared,
            Path(temp_root) / "prepared",
            base_model_revision=args.base_model_revision,
            tokenizer_revision=args.tokenizer_revision,
        )
        report = run_empirical_adapter_experiment(
            effective,
            backend=TrainingBackend(args.backend),
            output_directory=args.output,
            load_in_4bit=not args.no_4bit_eval,
            max_new_tokens=args.max_new_tokens,
            general_regression=general,
            reference=args.reference,
        )

    print(report.model_dump_json(indent=2))
    # A negative adapter result is still a valid experiment. Exit status reports
    # evidence integrity/completion, not whether the adapter or compression won.
    return 0 if report.experiment_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
