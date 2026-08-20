"""Child-process entry point for one empirical adapter training run."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .adapter_training import (
    TrainingBackend,
    load_prepared_training_bundle,
    trainer_for_backend,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness_x.training.empirical_worker",
        description="Train one prepared Harness X adapter in an isolated interpreter.",
    )
    parser.add_argument("prepared", type=Path)
    parser.add_argument(
        "--backend",
        choices=tuple(item.value for item in TrainingBackend),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be empty for isolated training")

    bundle = load_prepared_training_bundle(args.prepared)
    backend = TrainingBackend(args.backend)
    artifact = trainer_for_backend(backend).train(bundle, args.output)
    print(artifact.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
