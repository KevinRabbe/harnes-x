"""Installed Harness X CLI wrapper adding M44 portable evidence verification."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import cli as legacy_cli
from .evidence_verification import (
    PortableEvidenceVerificationError,
    verify_portable_evidence,
)


def build_parser() -> argparse.ArgumentParser:
    """Extend the qualified legacy parser with exactly one offline verification command."""

    parser = legacy_cli.build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    )
    verify = subparsers.add_parser(
        "verify-evidence",
        help="Verify an exported terminal evidence manifest and local report/trace files offline",
    )
    verify.add_argument("manifest", type=Path)
    verify.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Local coding-task-report.json export when the manifest marks it available",
    )
    verify.add_argument(
        "--trace",
        type=Path,
        default=None,
        help="Local causal-trace.jsonl export when the manifest marks it available",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch M44 locally and delegate every pre-M44 command unchanged."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(arguments)

    if args.command == "verify-evidence":
        try:
            result = verify_portable_evidence(
                args.manifest,
                report_path=args.report,
                trace_path=args.trace,
            )
        except PortableEvidenceVerificationError as exc:
            parser.error(str(exc))
        print(result.summary())
        return 0

    if args.command is None:
        parser.print_help()
        return 0

    return legacy_cli.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
