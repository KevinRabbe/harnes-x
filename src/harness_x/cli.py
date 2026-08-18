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

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
