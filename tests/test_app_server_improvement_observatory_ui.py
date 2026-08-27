from __future__ import annotations

from pathlib import Path

from harness_x.app_server.ui_assets import load_ui_asset


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "harness_x" / "app_server" / "ui"


def _text(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def test_observatory_asset_is_allowlisted_and_loaded_after_m75_reliability() -> None:
    asset = load_ui_asset("/ui/improvement_observatory_bridge.js")
    assert asset is not None
    content_type, payload = asset
    assert content_type == "text/javascript; charset=utf-8"
    assert payload == (UI / "improvement_observatory_bridge.js").read_bytes()

    bootstrap = _text("bootstrap.js")
    reliability = 'await loadEverydayReliabilityBridge();'
    observatory = 'await loadImprovementObservatoryBridge();'
    assert reliability in bootstrap
    assert observatory in bootstrap
    assert bootstrap.index(reliability) < bootstrap.index(observatory)
    assert '"/ui/improvement_observatory_bridge.js"' in bootstrap


def test_observatory_bridge_is_explicit_get_only_and_has_no_browser_persistence_or_credentials() -> None:
    source = _text("improvement_observatory_bridge.js")
    assert "Refresh observatory" in source
    assert "improvement-observatory" in source
    assert "promotion_authority !== false" in source
    assert "read_only !== true" in source
    assert "await api(improvementObservatoryPath(projectId))" in source

    forbidden = (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
        "Authorization",
        "Bearer ",
        "access_token",
        'method: "POST"',
        'method: "PUT"',
        'method: "PATCH"',
        'method: "DELETE"',
        "workspace_root",
        "api_key",
        "verification_commands",
    )
    for marker in forbidden:
        assert marker not in source


def test_observatory_ui_exposes_read_only_sections_without_improvement_action_controls() -> None:
    source = _text("improvement_observatory_bridge.js")
    for heading in (
        "Observed versions",
        "Diagnosed weaknesses",
        "Candidates",
        "Experiments and regressions",
        "Promotion and rollback evidence",
        "Procedure-improvement campaigns",
        "Source health",
    ):
        assert heading in source

    # The only M76 button is refresh; improvement lifecycle verbs may appear as evidence labels,
    # but they are never wired as action buttons or API mutation suffixes.
    assert 'improvementObservatoryNode("button", "Refresh observatory"' in source
    assert '"Promote"' not in source
    assert '"Roll back"' not in source
    assert '"Run experiment"' not in source
    assert '"Generate candidate"' not in source
    assert '"Start campaign"' not in source
    assert '"Approve"' not in source
    assert "/promote" not in source
    assert "/rollback" not in source
