"""Bounded static assets for the local M36 operator UI.

The UI is presentation-only. Assets are packaged with Harness X and are served from an exact
allowlist; callers cannot turn this helper into arbitrary package or filesystem reads.
"""

from __future__ import annotations

from importlib.resources import files

_UI_ROOT = files("harness_x.app_server").joinpath("ui")
_MAX_UI_ASSET_BYTES = 512 * 1024
_UI_ASSETS: dict[str, tuple[str, str]] = {
    "/ui/": ("index.html", "text/html; charset=utf-8"),
    "/ui/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/ui/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


def load_ui_asset(path: str) -> tuple[str, bytes] | None:
    """Return one exact packaged UI asset, or ``None`` when the path is not public."""

    selected = _UI_ASSETS.get(path)
    if selected is None:
        return None
    filename, content_type = selected
    payload = _UI_ROOT.joinpath(filename).read_bytes()
    if len(payload) > _MAX_UI_ASSET_BYTES:
        raise RuntimeError(f"operator UI asset exceeds {_MAX_UI_ASSET_BYTES} bytes")
    return content_type, payload
