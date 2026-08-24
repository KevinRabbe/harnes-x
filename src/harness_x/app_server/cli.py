"""Operator entry point for the local single-user Harness X App Server."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Sequence

from harness_x.evidence_verification import PortableEvidenceVerificationError

from .product_operator_http_server import LocalOperatorHTTPServer
from .service import AppServerService


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
        "--desktop-host",
        action="store_true",
        help=(
            "Private redirected-stdio mode for the Windows desktop parent. Emits one "
            "single-use bootstrap URL and shuts down cleanly when redirected stdin reaches EOF."
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


def _require_desktop_redirects(parser: argparse.ArgumentParser) -> None:
    if sys.stdin.isatty() or sys.stdout.isatty():
        parser.error("--desktop-host requires redirected stdin and stdout")


def _start_desktop_lifetime_monitor(server: LocalOperatorHTTPServer) -> threading.Thread:
    """Treat parent pipe EOF as a clean desktop-owned App Server shutdown request."""

    def monitor() -> None:
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except (OSError, ValueError):
            pass
        server.httpd.shutdown()

    thread = threading.Thread(
        target=monitor,
        name="harness-x-desktop-parent-lifetime",
        daemon=True,
    )
    thread.start()
    return thread


def _desktop_start_payload(
    server: LocalOperatorHTTPServer,
    *,
    bootstrap_url: str,
) -> dict[str, object]:
    return {
        "schema_version": "app-server-desktop-start-v1",
        "base_url": server.base_url,
        "ui_url": server.ui_url,
        "ui_bootstrap_url": bootstrap_url,
        "pid": os.getpid(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.desktop_host and args.open_ui:
        parser.error("--desktop-host and --open-ui are mutually exclusive")
    if args.desktop_host:
        _require_desktop_redirects(parser)

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

    if args.desktop_host:
        bootstrap_url = server.issue_ui_bootstrap_url()
        _start_desktop_lifetime_monitor(server)
        startup = _desktop_start_payload(server, bootstrap_url=bootstrap_url)
    else:
        ui_opened = _open_operator_ui(server) if args.open_ui else False
        startup = {
            "schema_version": "app-server-start-v1",
            "base_url": server.base_url,
            "ui_url": server.ui_url,
            "token_path": str(server.token_path),
            "server_info_path": str(server.info_path),
            "ui_open_requested": bool(args.open_ui),
            "ui_opened": ui_opened,
            "pid": os.getpid(),
        }

    print(json.dumps(startup, sort_keys=True), flush=True)
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