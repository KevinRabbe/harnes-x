"""Short-lived one-time capabilities for same-tab operator reload recovery."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable

RELOAD_CAPABILITY_TTL_SECONDS = 5.0 * 60.0
RELOAD_CAPABILITY_BYTES = 32
MAX_OUTSTANDING_RELOAD_CAPABILITIES = 32
_MAX_RELOAD_CAPABILITY_TTL_SECONDS = 5.0 * 60.0
_MAX_RELOAD_CAPABILITY_COUNT = 64
_MAX_GENERATION_ATTEMPTS = 8


class ReloadCapabilities:
    """Keep a bounded set of one-time reload capability digests in process memory."""

    def __init__(
        self,
        *,
        ttl_seconds: float = RELOAD_CAPABILITY_TTL_SECONDS,
        max_outstanding: int = MAX_OUTSTANDING_RELOAD_CAPABILITIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or ttl_seconds > _MAX_RELOAD_CAPABILITY_TTL_SECONDS:
            raise ValueError(
                "reload capability ttl_seconds must be > 0 and <= "
                f"{_MAX_RELOAD_CAPABILITY_TTL_SECONDS:g}"
            )
        if max_outstanding < 1 or max_outstanding > _MAX_RELOAD_CAPABILITY_COUNT:
            raise ValueError(
                f"reload capability max_outstanding must be between 1 and {_MAX_RELOAD_CAPABILITY_COUNT}"
            )
        self._ttl_seconds = float(ttl_seconds)
        self._max_outstanding = int(max_outstanding)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: list[tuple[bytes, float]] = []

    @staticmethod
    def _digest(ticket: object) -> bytes | None:
        if not isinstance(ticket, str):
            return None
        try:
            return hashlib.sha256(ticket.encode("ascii")).digest()
        except UnicodeEncodeError:
            return None

    def _prune_locked(self, now: float) -> None:
        self._entries = [
            (digest, expires_at)
            for digest, expires_at in self._entries
            if now < expires_at
        ]

    def issue(self, *, previous_ticket: object = None) -> str:
        """Issue one bounded capability and atomically replace a supplied previous ticket."""

        now = self._clock()
        expires_at = now + self._ttl_seconds
        previous_digest = self._digest(previous_ticket)

        with self._lock:
            self._prune_locked(now)

            ticket: str | None = None
            digest: bytes | None = None
            for _ in range(_MAX_GENERATION_ATTEMPTS):
                candidate_ticket = secrets.token_urlsafe(RELOAD_CAPABILITY_BYTES)
                candidate_digest = hashlib.sha256(candidate_ticket.encode("ascii")).digest()
                collides_with_previous = (
                    previous_digest is not None
                    and hmac.compare_digest(candidate_digest, previous_digest)
                )
                collides_with_outstanding = any(
                    hmac.compare_digest(candidate_digest, stored)
                    for stored, _ in self._entries
                )
                if not collides_with_previous and not collides_with_outstanding:
                    ticket = candidate_ticket
                    digest = candidate_digest
                    break
            if ticket is None or digest is None:
                raise RuntimeError("failed to generate a unique reload capability")

            if previous_digest is not None:
                self._entries = [
                    (stored, expiry)
                    for stored, expiry in self._entries
                    if not hmac.compare_digest(previous_digest, stored)
                ]
            if len(self._entries) >= self._max_outstanding:
                oldest_index = min(
                    range(len(self._entries)),
                    key=lambda index: self._entries[index][1],
                )
                self._entries.pop(oldest_index)
            self._entries.append((digest, expires_at))
        return ticket

    def redeem(self, ticket: object) -> bool:
        """Consume one matching unexpired capability without exposing stored digests."""

        candidate = self._digest(ticket)
        if candidate is None:
            return False
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            matched_index: int | None = None
            for index, (stored, _) in enumerate(self._entries):
                if hmac.compare_digest(candidate, stored):
                    matched_index = index
            if matched_index is None:
                return False
            self._entries.pop(matched_index)
            return True

    @property
    def outstanding_count(self) -> int:
        """Return the number of currently unexpired digests without exposing them."""

        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            return len(self._entries)
