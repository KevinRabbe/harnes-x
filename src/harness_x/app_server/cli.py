"""Operator entry point for the local single-user Harness X App Server."""

from __future__ import annotations

import argparse
import json
import os
import webbrowser
from pathlib import Path
from typing import Sequence

from harness_x.evidence_verification import PortableEvidenceVerificationError

from .service import AppServerService
from .signed_evidence_operator_http_server import LocalOperatorHTTPServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-x-app-server",
        description=(
            "Run the local single-user Harness X App Server with its authenticated operator UI."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(".harness-x/app-server"),
        help="Persistent app-server root for session state, run artifacts, token, and server info.",
    )
    parser.add_argument(
        "--host",
        choices=("127.0.0.1",),
        default="127.0.0.1",
        help="The local App Server intentionally supports loopback IPv4 only.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Loopback TCP port. Use 0 to ask the OS for an ephemeral port.",
    )
    parser.add_argument(
        "--open-ui",
        action="store_true",
        help=(
            "Open the operator UI with a short-lived single-use local bootstrap ticket; "
            "the persistent bearer is never placed in the URL."
        ),
    )
    parser.add_argument(
        "--evidence-signing-private-key",
        type=Path,
        default=None,
        help=(
            "Optional local unencrypted Ed25519 PKCS8 private-key PEM used only to sign "
            "terminal evidence-manifest bytes."
        ),
    )
    return parser


def _open_operator_ui(server: LocalOperatorHTTPServer) -> bool:
    """Open one disposable bootstrap URL without ever printing or persisting it."""

    bootstrap_url = server.issue_ui_bootstrap_url()
    try:
        opened = bool(webbrowser.open(bootstrap_url, new=2))
    except Exception:
        server.bootstrap_tickets.invalidate()
        return False
    if not opened:
        server.bootstrap_tickets.invalidate()
    return opened


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    service = AppServerService(
        root / "data",
        server_version="0.1.0a0+app-server40-one-time-ui-bootstrap",
    )
    try:
        server = LocalOperatorHTTPServer(
            service,
            root,
            host=args.host,
            port=args.port,
            evidence_signing_private_key=args.evidence_signing_private_key,
        )
    except PortableEvidenceVerificationError as exc:
        service.close()
        parser.error(str(exc))
    ui_opened = _open_operator_ui(server) if args.open_ui else False
    print(
        json.dumps(
            {
                "schema_version": "app-server-start-v1",
                "base_url": server.base_url,
                "ui_url": server.ui_url,
                "token_path": str(server.token_path),
                "server_info_path": str(server.info_path),
                "ui_open_requested": bool(args.open_ui),
                "ui_opened": ui_opened,
                "pid": os.getpid(),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.httpd.server_close()
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
