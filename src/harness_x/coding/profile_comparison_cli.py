"""CLI for strict offline comparison of two M33 profile-run artifact roots."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .profile_comparison import compare_profile_run_roots, write_profile_run_comparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-x-compare-runs",
        description=(
            "Compare two independently completed Harness X profile runs. The command never "
            "launches a model, chooses a winner, or changes routing; it validates starting "
            "conditions and emits descriptive evidence/outcome deltas."
        ),
    )
    parser.add_argument("left", type=Path, help="First comparison-grade profile run root.")
    parser.add_argument("right", type=Path, help="Second comparison-grade profile run root.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("profile-run-comparison.json"),
        help="Comparison JSON path or output directory.",
    )
    parser.add_argument(
        "--allow-incomparable",
        action="store_true",
        help=(
            "Return success even when strict comparability checks fail. The report still lists "
            "every incompatibility and must not be treated as a controlled comparison."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = compare_profile_run_roots(args.left, args.right)
        write_profile_run_comparison(report, args.output)
    except ValueError as exc:
        parser.error(str(exc))

    print(report.model_dump_json(indent=2))
    if report.strictly_comparable or args.allow_incomparable:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
