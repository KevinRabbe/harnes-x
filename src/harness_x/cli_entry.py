"""Installed Harness X CLI wrapper adding portable evidence verification/signing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import cli as legacy_cli


def build_parser() -> argparse.ArgumentParser:
    """Extend the qualified legacy parser with portable evidence commands."""

    parser = legacy_cli.build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    )
    verify = subparsers.add_parser(
        "verify-evidence",
        help=(
            "Verify an exported terminal manifest and local snapshot/lifecycle/report/trace "
            "evidence, optionally with a detached signature"
        ),
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
    verify.add_argument(
        "--signature",
        type=Path,
        default=None,
        help="Optional detached app-evidence-signature-v1 JSON envelope",
    )
    verify.add_argument(
        "--public-key",
        type=Path,
        default=None,
        help="Ed25519 public-key PEM paired with --signature",
    )

    keygen = subparsers.add_parser(
        "evidence-keygen",
        help="Generate one operator-managed Ed25519 evidence-signing keypair",
    )
    keygen.add_argument("--private-key", type=Path, required=True)
    keygen.add_argument("--public-key", type=Path, required=True)

    sign = subparsers.add_parser(
        "sign-evidence",
        help="Sign exact portable evidence-manifest bytes with an Ed25519 private key",
    )
    sign.add_argument("manifest", type=Path)
    sign.add_argument("--private-key", type=Path, required=True)
    sign.add_argument("--output", type=Path, required=True)

    extract = subparsers.add_parser(
        "extract-evidence-capsule",
        help="Validate and extract exact manifest/signature bytes from an M55 capsule",
    )
    extract.add_argument("capsule", type=Path)
    extract.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Existing local directory for the two fixed M43/M52 output filenames",
    )

    verify_capsule = subparsers.add_parser(
        "verify-evidence-capsule",
        help="Extract an M55 capsule and verify its exact M43/M52 pair with a public key",
    )
    verify_capsule.add_argument("capsule", type=Path)
    verify_capsule.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Existing directory that will retain the two fixed extracted evidence files",
    )
    verify_capsule.add_argument(
        "--public-key",
        type=Path,
        required=True,
        help="Externally trusted Ed25519 public-key PEM for frozen M52 verification",
    )
    verify_capsule.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="Optional local session-snapshot.json export for M47 fingerprint verification",
    )
    verify_capsule.add_argument(
        "--lifecycle",
        type=Path,
        default=None,
        help="Optional local session-lifecycle-ledger.json export for M45 event-chain verification",
    )
    verify_capsule.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Local coding-task-report.json export when the manifest marks it available",
    )
    verify_capsule.add_argument(
        "--trace",
        type=Path,
        default=None,
        help="Local causal-trace.jsonl export when the manifest marks it available",
    )
    verify_capsule.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help="Optional exclusive output path for an unsigned deterministic M58 verification receipt",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch portable evidence commands and delegate every legacy command unchanged."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(arguments)

    if args.command == "evidence-keygen":
        from .evidence_signing import generate_evidence_keypair
        from .evidence_verification import PortableEvidenceVerificationError

        try:
            result = generate_evidence_keypair(
                private_key_path=args.private_key,
                public_key_path=args.public_key,
            )
        except PortableEvidenceVerificationError as exc:
            parser.error(str(exc))
        print(result.summary())
        return 0

    if args.command == "sign-evidence":
        from .evidence_signing import sign_evidence_manifest
        from .evidence_verification import PortableEvidenceVerificationError

        try:
            result = sign_evidence_manifest(
                args.manifest,
                private_key_path=args.private_key,
                output_path=args.output,
            )
        except PortableEvidenceVerificationError as exc:
            parser.error(str(exc))
        print(result.summary())
        return 0

    if args.command == "extract-evidence-capsule":
        from .evidence_capsule_extraction import extract_evidence_capsule
        from .evidence_verification import PortableEvidenceVerificationError

        try:
            result = extract_evidence_capsule(
                args.capsule,
                output_dir=args.output_dir,
            )
        except PortableEvidenceVerificationError as exc:
            parser.error(str(exc))
        print(result.summary())
        return 0

    if args.command == "verify-evidence-capsule":
        from .evidence_verification import PortableEvidenceVerificationError

        try:
            if args.receipt is None:
                from .evidence_capsule_verification import verify_evidence_capsule

                result = verify_evidence_capsule(
                    args.capsule,
                    output_dir=args.output_dir,
                    public_key_path=args.public_key,
                    snapshot_path=args.snapshot,
                    lifecycle_path=args.lifecycle,
                    report_path=args.report,
                    trace_path=args.trace,
                )
            else:
                from .evidence_verification_receipt import (
                    verify_evidence_capsule_with_receipt,
                )

                result = verify_evidence_capsule_with_receipt(
                    args.capsule,
                    output_dir=args.output_dir,
                    public_key_path=args.public_key,
                    receipt_path=args.receipt,
                    snapshot_path=args.snapshot,
                    lifecycle_path=args.lifecycle,
                    report_path=args.report,
                    trace_path=args.trace,
                )
        except PortableEvidenceVerificationError as exc:
            parser.error(str(exc))
        print(result.summary())
        return 0

    if args.command == "verify-evidence":
        from .evidence_signing import verify_portable_evidence_with_signature
        from .evidence_verification import PortableEvidenceVerificationError

        try:
            result = verify_portable_evidence_with_signature(
                args.manifest,
                signature_path=args.signature,
                public_key_path=args.public_key,
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
