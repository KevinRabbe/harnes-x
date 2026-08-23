from __future__ import annotations

import pytest

import harness_x.app_server.reload_auth as reload_auth
from harness_x.app_server.reload_auth import ReloadCapabilities


def test_reload_capability_generation_retries_outstanding_and_previous_collisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter(["A" * 43, "A" * 43, "B" * 43, "B" * 43, "C" * 43])
    monkeypatch.setattr(reload_auth.secrets, "token_urlsafe", lambda _size: next(values))

    tickets = ReloadCapabilities(max_outstanding=4)
    first = tickets.issue()
    assert first == "A" * 43

    second = tickets.issue()
    assert second == "B" * 43

    replacement = tickets.issue(previous_ticket=second)
    assert replacement == "C" * 43
    assert not tickets.redeem(second)
    assert tickets.redeem(first)
    assert tickets.redeem(replacement)


def test_failed_rotation_preserves_the_still_valid_previous_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter(["A" * 43] + ["A" * 43] * 8)
    monkeypatch.setattr(reload_auth.secrets, "token_urlsafe", lambda _size: next(values))
    tickets = ReloadCapabilities(max_outstanding=2)
    previous = tickets.issue()
    assert previous == "A" * 43

    with pytest.raises(RuntimeError, match="unique reload capability"):
        tickets.issue(previous_ticket=previous)

    assert tickets.redeem(previous)
    assert not tickets.redeem(previous)
