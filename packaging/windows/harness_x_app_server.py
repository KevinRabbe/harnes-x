"""PyInstaller entry point for the M77 portable Windows App Server.

Packaging is transport only: the executable delegates directly to the inherited App Server CLI.
"""

from __future__ import annotations

from harness_x.app_server.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
