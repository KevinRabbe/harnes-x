"""Ephemeral one-time capability tickets for local operator UI bootstrap."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable

BOOTSTRAP_TTL_SECONDS = 60.0
_BOOTSTRAP_TICKET_BYTES = 32
_MAX_BOOTSTRAP_TTL_SECONDS = 5.0 * 60.0


class OneTimeBootstrapTickets:
    """Keep at most one short-lived bootstrap ticket as a digest in process memory."""

    def __init__(
        self,
        *,
        ttl_seconds: float = BOOTSTRAP_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or ttl_seconds > _MAX_BOOTSTRAP_TTL_SECONDS:
            raise ValueError(
                f"bootstrap ttl_seconds must be > 0 and <= {_MAX_BOOTSTRAP_TTL_SECONDS:g}"
            )
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._ticket_digest: bytes | None = None
        self._expires_at: float | None = None

    def issue(self) -> str:
        """Issue one replacement-invalidating ticket with at least 256 bits of entropy."""

        ticket = secrets.token_urlsafe(_BOOTSTRAP_TICKET_BYTES)
        digest = hashlib.sha256(ticket.encode("ascii")).digest()
        expires_at = self._clock() + self._ttl_seconds
        with self._lock:
            self._ticket_digest = digest
            self._expires_at = expires_at
        return ticket

    def invalidate(self) -> None:
        """Discard any outstanding ticket, for example after a browser launch failure."""

        with self._lock:
            self._ticket_digest = None
            self._expires_at = None

    def redeem(self, ticket: object) -> bool:
        """Consume the outstanding ticket on one valid, unexpired redemption."""

        if not isinstance(ticket, str):
            return False
        try:
            candidate = hashlib.sha256(ticket.encode("ascii")).digest()
        except UnicodeEncodeError:
            return False

        with self._lock:
            expected = self._ticket_digest
            expires_at = self._expires_at
            if expected is None or expires_at is None:
                return False
            if self._clock() >= expires_at:
                self._ticket_digest = None
                self._expires_at = None
                return False
            if not hmac.compare_digest(candidate, expected):
                return False
            self._ticket_digest = None
            self._expires_at = None
            return True

    @property
    def has_outstanding_ticket(self) -> bool:
        """Return whether an unexpired ticket is currently available without exposing it."""

        with self._lock:
            if self._ticket_digest is None or self._expires_at is None:
                return False
            if self._clock() >= self._expires_at:
                self._ticket_digest = None
                self._expires_at = None
                return False
            return True
