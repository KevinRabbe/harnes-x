"""Operator entry point for the local single-user Harness X App Server."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from .operator_http_server import LocalOperatorHTTPServer
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    service = AppServerService(
        root / "data",
        server_version="0.1.0a0+app-server36-local-operator-ui",
    )
    server = LocalOperatorHTTPServer(service, root, host=args.host, port=args.port)
    print(
        json.dumps(
            {
                "schema_version": "app-server-start-v1",
                "base_url": server.base_url,
                "ui_url": server.ui_url,
                "token_path": str(server.token_path),
                "server_info_path": str(server.info_path),
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
