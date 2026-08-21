from __future__ import annotations

from harness_x.browser import BrowserConsoleMessage, BrowserObservation, PlaywrightBrowserProvider
from harness_x.coding.browser_verification import (
    BrowserConsoleVerificationCheck,
    BrowserPageVerificationCheck,
    BrowserVerificationPlatform,
)
from harness_x.coding.verification import VerificationCheckStatus


def _observation(
    *,
    snapshot: str = '- heading "Dashboard" [level=1]',
    aria_truncated: bool = False,
    console_messages: tuple[BrowserConsoleMessage, ...] = (),
    console_truncated: bool = False,
    page_errors: tuple[str, ...] = (),
    page_errors_truncated: bool = False,
) -> BrowserObservation:
    return BrowserObservation(
        url="http://127.0.0.1:3000/",
        title="Dashboard",
        aria_snapshot=snapshot,
        aria_truncated=aria_truncated,
        console_messages=console_messages,
        console_truncated=console_truncated,
        page_errors=page_errors,
        page_errors_truncated=page_errors_truncated,
    )


def test_missing_required_fragment_in_truncated_snapshot_is_indeterminate() -> None:
    check = BrowserPageVerificationCheck(
        check_id="ready",
        name="page is ready",
        snapshot_contains=("Ready",),
    )

    result = BrowserVerificationPlatform._snapshot_result(
        check,
        'heading "Dashboard"',
        "Dashboard",
        True,
        check.snapshot_contains,
        check.snapshot_excludes,
    )

    assert result.status == VerificationCheckStatus.ERROR
    assert result.failure_code == "browser_snapshot_indeterminate_truncated"


def test_absent_forbidden_fragment_in_truncated_snapshot_is_indeterminate() -> None:
    check = BrowserPageVerificationCheck(
        check_id="no_fatal",
        name="fatal banner is absent",
        snapshot_excludes=("Fatal error",),
    )

    result = BrowserVerificationPlatform._snapshot_result(
        check,
        'heading "Dashboard"',
        "Dashboard",
        True,
        check.snapshot_contains,
        check.snapshot_excludes,
    )

    assert result.status == VerificationCheckStatus.ERROR
    assert result.failure_code == "browser_snapshot_indeterminate_truncated"


def test_positive_match_in_truncated_snapshot_remains_conclusive() -> None:
    check = BrowserPageVerificationCheck(
        check_id="ready",
        name="page is ready",
        snapshot_contains=("Ready",),
    )

    result = BrowserVerificationPlatform._snapshot_result(
        check,
        'heading "Ready"',
        "Dashboard",
        True,
        check.snapshot_contains,
        check.snapshot_excludes,
    )

    assert result.status == VerificationCheckStatus.PASSED
    assert result.failure_code is None


def test_forbidden_match_in_truncated_snapshot_remains_conclusive_failure() -> None:
    check = BrowserPageVerificationCheck(
        check_id="no_fatal",
        name="fatal banner is absent",
        snapshot_excludes=("Fatal error",),
    )

    result = BrowserVerificationPlatform._snapshot_result(
        check,
        'alert "Fatal error"',
        "Dashboard",
        True,
        check.snapshot_contains,
        check.snapshot_excludes,
    )

    assert result.status == VerificationCheckStatus.FAILED
    assert result.failure_code == "browser_expectation_failed"


def test_truncated_console_history_cannot_prove_absence_of_errors() -> None:
    check = BrowserConsoleVerificationCheck(
        check_id="console_clean",
        name="console is clean",
        forbidden_console_levels=("error",),
        require_no_page_errors=False,
    )

    result = BrowserVerificationPlatform._console_result(
        check,
        _observation(console_truncated=True),
    )

    assert result.status == VerificationCheckStatus.ERROR
    assert result.failure_code == "browser_console_evidence_indeterminate_truncated"


def test_truncated_page_error_history_cannot_prove_absence_of_page_errors() -> None:
    check = BrowserConsoleVerificationCheck(
        check_id="page_errors_clean",
        name="page errors are absent",
        forbidden_console_levels=(),
        require_no_page_errors=True,
    )

    result = BrowserVerificationPlatform._console_result(
        check,
        _observation(page_errors_truncated=True),
    )

    assert result.status == VerificationCheckStatus.ERROR
    assert result.failure_code == "browser_console_evidence_indeterminate_truncated"


def test_observed_console_error_is_conclusive_even_when_history_is_truncated() -> None:
    check = BrowserConsoleVerificationCheck(
        check_id="console_clean",
        name="console is clean",
        forbidden_console_levels=("error",),
        require_no_page_errors=False,
    )

    result = BrowserVerificationPlatform._console_result(
        check,
        _observation(
            console_messages=(BrowserConsoleMessage(level="error", text="boom"),),
            console_truncated=True,
        ),
    )

    assert result.status == VerificationCheckStatus.FAILED
    assert result.failure_code == "browser_console_or_page_error"


def test_playwright_network_policy_is_same_origin_not_merely_loopback(tmp_path) -> None:
    provider = PlaywrightBrowserProvider("http://127.0.0.1:3000", tmp_path)

    assert provider._network_url_allowed("http://127.0.0.1:3000/app.js") is True
    assert provider._network_url_allowed("ws://127.0.0.1:3000/socket") is True
    assert provider._network_url_allowed("http://127.0.0.1:3001/other") is False
    assert provider._network_url_allowed("http://localhost:3000/other") is False
    assert provider._network_url_allowed("https://127.0.0.1:3000/other") is False
    assert provider._network_url_allowed("https://example.com/") is False
    assert provider._network_url_allowed("data:text/plain,ok") is True
