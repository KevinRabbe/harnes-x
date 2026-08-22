from __future__ import annotations

import hashlib

import pytest

from harness_x.app_server.bootstrap import OneTimeBootstrapTickets


def test_bootstrap_ticket_is_digest_only_single_use_and_replacement_invalidating() -> None:
    now = [100.0]
    tickets = OneTimeBootstrapTickets(ttl_seconds=10.0, clock=lambda: now[0])

    first = tickets.issue()
    assert len(first) >= 40
    assert tickets.has_outstanding_ticket
    assert tickets._ticket_digest == hashlib.sha256(first.encode("ascii")).digest()
    assert first not in repr(vars(tickets))

    second = tickets.issue()
    assert first != second
    assert tickets._ticket_digest == hashlib.sha256(second.encode("ascii")).digest()
    assert not tickets.redeem(first)
    assert tickets.redeem(second)
    assert not tickets.redeem(second)
    assert not tickets.has_outstanding_ticket


def test_bootstrap_ticket_expires_and_invalid_inputs_do_not_redeem() -> None:
    now = [7.0]
    tickets = OneTimeBootstrapTickets(ttl_seconds=3.0, clock=lambda: now[0])
    ticket = tickets.issue()

    assert not tickets.redeem(None)
    assert not tickets.redeem("é")
    assert tickets.has_outstanding_ticket

    now[0] = 10.0
    assert not tickets.redeem(ticket)
    assert not tickets.has_outstanding_ticket


def test_bootstrap_ticket_can_be_explicitly_invalidated() -> None:
    tickets = OneTimeBootstrapTickets()
    ticket = tickets.issue()
    tickets.invalidate()
    assert not tickets.redeem(ticket)
    assert not tickets.has_outstanding_ticket


@pytest.mark.parametrize("ttl", [0, -1, 301])
def test_bootstrap_ticket_rejects_invalid_lifetime(ttl: float) -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        OneTimeBootstrapTickets(ttl_seconds=ttl)
