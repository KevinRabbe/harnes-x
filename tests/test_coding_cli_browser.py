from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness_x.coding.cli import _build_browser_inputs, _load_application_spec, build_parser


def test_browser_cli_is_opt_in_and_requires_paired_inputs(tmp_path: Path) -> None:
    parser = build_parser()
    ordinary = parser.parse_args(
        [".", "--task", "Build site", "--verify", "python -m pytest -q"]
    )
    assert ordinary.application_spec is None
    assert ordinary.browser_verification_plan is None
    assert _build_browser_inputs(ordinary) is None

    only_app = tmp_path / "app.json"
    only_app.write_text(
        json.dumps(
            {
                "argv": ["python", "-m", "http.server", "8000"],
                "base_url": "http://127.0.0.1:8000",
            }
        ),
        encoding="utf-8",
    )
    args = parser.parse_args(
        [
            ".",
            "--task",
            "Build site",
            "--verify",
            "python -m pytest -q",
            "--application-spec",
            str(only_app),
        ]
    )
    with pytest.raises(ValueError, match="requires both"):
        _build_browser_inputs(args)


def test_application_spec_rebinds_python_alias_to_harness_interpreter(tmp_path: Path) -> None:
    path = tmp_path / "app.json"
    path.write_text(
        json.dumps(
            {
                "argv": ["python", "-m", "http.server", "8123"],
                "base_url": "http://localhost:8123",
            }
        ),
        encoding="utf-8",
    )

    spec = _load_application_spec(path)

    assert spec.argv[0] == sys.executable
    assert spec.argv[1:] == ("-m", "http.server", "8123")


def test_browser_cli_loads_application_and_verification_plan(tmp_path: Path) -> None:
    app = tmp_path / "app.json"
    app.write_text(
        json.dumps(
            {
                "argv": ["python", "-m", "http.server", "8124"],
                "base_url": "http://127.0.0.1:8124",
            }
        ),
        encoding="utf-8",
    )
    plan = tmp_path / "browser.json"
    plan.write_text(
        json.dumps(
            {
                "name": "browser acceptance",
                "checks": [
                    {
                        "kind": "browser_page",
                        "check_id": "home",
                        "name": "home is visible",
                        "path": "/",
                        "snapshot_contains": ["Dashboard"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        [
            ".",
            "--task",
            "Build site",
            "--verify",
            "python -m pytest -q",
            "--application-spec",
            str(app),
            "--browser-verification-plan",
            str(plan),
            "--browser-headed",
        ]
    )

    application, browser_plan, provider_factory = _build_browser_inputs(args)

    assert application.base_url == "http://127.0.0.1:8124"
    assert browser_plan.name == "browser acceptance"
    assert browser_plan.checks[0].check_id == "home"
    provider = provider_factory(application.base_url, tmp_path / "browser-artifacts")
    try:
        assert provider.info.engine == "chromium"
        assert provider.headless is False
    finally:
        provider.close()
