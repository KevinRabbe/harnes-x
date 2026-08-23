"""Installed Harness X CLI wrapper adding portable evidence verification."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import cli as legacy_cli


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
        help="Verify an exported terminal manifest and local snapshot/lifecycle/report/trace evidence offline",
    )
    verify.add_argument("manifest", type=Path)
    verify.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="Optional local session-snapshot.json export for M47 fingerprint verification",
    )
    verify.add_argument(
        "--lifecycle",
        type=Path,
        default=None,
        help="Optional local session-lifecycle-ledger.json export for M45 event-chain verification",
    )
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
    """Dispatch portable evidence verification and delegate every legacy command unchanged."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(arguments)

    if args.command == "verify-evidence":
        from .evidence_verification import PortableEvidenceVerificationError
        from .snapshot_verification import verify_portable_evidence_with_snapshot

        try:
            result = verify_portable_evidence_with_snapshot(
                args.manifest,
                snapshot_path=args.snapshot,
                lifecycle_path=args.lifecycle,
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