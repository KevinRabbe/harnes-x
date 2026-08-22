"""Explicit one-profile-at-a-time execution path for M33 offline comparison."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .cli import (
    _build_browser_inputs,
    _build_verification_inputs,
    _runtime,
    _validate_resume_args,
    build_parser as build_coding_parser,
)
from .model_selection import (
    build_selected_reasoning_core,
    resolve_model_selection,
    write_model_selection_artifact,
)
from .run_manifest import (
    build_coding_run_manifest,
    clone_comparison_memory_seed,
    write_coding_run_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = build_coding_parser()
    parser.prog = "harness-x-profile-run"
    parser.description = (
        "Run exactly one explicit model profile through the normal isolated Harness X coding "
        "stack while recording comparison-grade starting-condition provenance."
    )
    parser.add_argument(
        "--comparison-memory-seed",
        type=Path,
        default=None,
        help=(
            "Optional existing project-memory snapshot copied into the fresh explicit "
            "--project-memory-root before this run. Use the same seed for each profile to make "
            "starting memory exactly comparable."
        ),
    )
    return parser


def _validate_profile_run_args(args: argparse.Namespace) -> None:
    _validate_resume_args(args)
    if not args.model_profile:
        raise ValueError("harness-x-profile-run requires explicit --model-profile")
    if args.in_place:
        raise ValueError("harness-x-profile-run requires isolated execution; --in-place is forbidden")
    if args.resume_long_horizon_state is not None or args.resume_allow_workspace_drift:
        raise ValueError("comparison profile runs cannot resume an earlier task state")
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"comparison profile output must be absent or empty: {output}")
    if args.comparison_memory_seed is not None and args.project_memory_root is None:
        raise ValueError(
            "--comparison-memory-seed requires an explicit fresh --project-memory-root"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    core = None
    try:
        _validate_profile_run_args(args)
        verification_plan, verification_commands = _build_verification_inputs(args)
        browser_inputs = _build_browser_inputs(args)
        selection = resolve_model_selection(args)
        core = build_selected_reasoning_core(selection)

        if args.comparison_memory_seed is not None:
            clone_comparison_memory_seed(
                args.comparison_memory_seed,
                args.project_memory_root,
            )

        write_model_selection_artifact(selection, args.output)
        browser_fingerprint = (
            browser_inputs[1].fingerprint if browser_inputs is not None else None
        )
        manifest = build_coding_run_manifest(
            task=args.task,
            workspace_root=args.workspace,
            output_root=args.output,
            isolated=True,
            verification_plan_fingerprint=verification_plan.fingerprint,
            browser_verification_plan_fingerprint=browser_fingerprint,
            project_memory_root=args.project_memory_root,
            project_memory_key=args.project_memory_key,
            model_selection=selection,
            max_reasoning_steps=args.max_reasoning_steps,
            max_tool_actions=args.max_tool_actions,
            max_output_tokens=args.max_output_tokens,
            baseline_verification=not args.no_baseline_verify,
            max_idle_turns=args.max_idle_turns,
            max_inspection_streak=args.max_inspection_streak,
            max_no_progress_streak=args.max_no_progress_streak,
            max_same_failure_count=args.max_same_failure_count,
            isolation_retention=args.retain_workspace,
            isolation_support_paths=tuple(args.isolation_copy_path),
        )
        write_coding_run_manifest(manifest, args.output)

        runtime = _runtime(args, core, verification_plan, browser_inputs)
        report = runtime.run(args.task, verification_commands=verification_commands)
    except ValueError as exc:
        parser.error(str(exc))
    finally:
        if core is not None:
            close = getattr(core, "close", None)
            if callable(close):
                close()

    print(report.model_dump_json(indent=2))
    return 0 if report.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
