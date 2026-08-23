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
MAX_RELOAD_CAPABILITY_FAMILIES = 4096
_MAX_RELOAD_CAPABILITY_TTL_SECONDS = 5.0 * 60.0
_MAX_RELOAD_CAPABILITY_COUNT = 64
_MAX_RELOAD_CAPABILITY_FAMILIES = 4096
_MAX_GENERATION_ATTEMPTS = 8


class ReloadCapabilityFamilyRevokedError(RuntimeError):
    """Raised when family-aware issuance targets a process-tombstoned family."""


class ReloadCapabilityFamilyCapacityError(RuntimeError):
    """Raised when a new family cannot be registered without dropping tombstones."""


class ReloadCapabilities:
    """Keep bounded one-time reload digests and optional revocation-family metadata."""

    def __init__(
        self,
        *,
        ttl_seconds: float = RELOAD_CAPABILITY_TTL_SECONDS,
        max_outstanding: int = MAX_OUTSTANDING_RELOAD_CAPABILITIES,
        max_families: int = MAX_RELOAD_CAPABILITY_FAMILIES,
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
        if max_families < 1 or max_families > _MAX_RELOAD_CAPABILITY_FAMILIES:
            raise ValueError(
                f"reload capability max_families must be between 1 and {_MAX_RELOAD_CAPABILITY_FAMILIES}"
            )
        self._ttl_seconds = float(ttl_seconds)
        self._max_outstanding = int(max_outstanding)
        self._max_families = int(max_families)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: list[tuple[bytes, float]] = []
        self._entry_families: dict[bytes, bytes] = {}
        self._family_revoked: dict[bytes, bool] = {}

    @staticmethod
    def _digest(value: object) -> bytes | None:
        if not isinstance(value, str):
            return None
        try:
            return hashlib.sha256(value.encode("ascii")).digest()
        except UnicodeEncodeError:
            return None

    def _prune_locked(self, now: float) -> None:
        self._entries = [
            (digest, expires_at)
            for digest, expires_at in self._entries
            if now < expires_at
        ]
        live = {digest for digest, _ in self._entries}
        self._entry_families = {
            digest: family
            for digest, family in self._entry_families.items()
            if digest in live
        }

    def _remove_digest_locked(self, candidate: bytes) -> bool:
        matched_index: int | None = None
        for index, (stored, _) in enumerate(self._entries):
            if hmac.compare_digest(candidate, stored):
                matched_index = index
        if matched_index is None:
            return False
        digest, _ = self._entries.pop(matched_index)
        self._entry_families.pop(digest, None)
        return True

    def _remove_family_locked(self, family_digest: bytes) -> int:
        removed = 0
        kept: list[tuple[bytes, float]] = []
        for digest, expires_at in self._entries:
            entry_family = self._entry_families.get(digest)
            if entry_family is not None and hmac.compare_digest(entry_family, family_digest):
                self._entry_families.pop(digest, None)
                removed += 1
            else:
                kept.append((digest, expires_at))
        self._entries = kept
        return removed

    def _generate_locked(self, previous_digest: bytes | None) -> tuple[str, bytes]:
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
                return candidate_ticket, candidate_digest
        raise RuntimeError("failed to generate a unique reload capability")

    def _evict_oldest_locked(self) -> None:
        if len(self._entries) < self._max_outstanding:
            return
        oldest_index = min(
            range(len(self._entries)),
            key=lambda index: self._entries[index][1],
        )
        digest, _ = self._entries.pop(oldest_index)
        self._entry_families.pop(digest, None)

    def issue(self, *, previous_ticket: object = None) -> str:
        """Issue one legacy bounded capability and atomically replace a supplied ticket."""

        now = self._clock()
        expires_at = now + self._ttl_seconds
        previous_digest = self._digest(previous_ticket)

        with self._lock:
            self._prune_locked(now)
            ticket, digest = self._generate_locked(previous_digest)
            if previous_digest is not None:
                self._remove_digest_locked(previous_digest)
            self._evict_oldest_locked()
            self._entries.append((digest, expires_at))
        return ticket

    def issue_for_family(self, *, family: object, previous_ticket: object = None) -> str:
        """Issue exactly one current ticket for a bounded process-scoped family."""

        family_digest = self._digest(family)
        if family_digest is None:
            raise ValueError("reload capability family must be ASCII text")
        previous_digest = self._digest(previous_ticket)
        now = self._clock()
        expires_at = now + self._ttl_seconds

        with self._lock:
            self._prune_locked(now)
            revoked = self._family_revoked.get(family_digest)
            if revoked is True:
                raise ReloadCapabilityFamilyRevokedError("reload capability family is revoked")
            if revoked is None:
                if len(self._family_revoked) >= self._max_families:
                    raise ReloadCapabilityFamilyCapacityError(
                        "reload capability family registry is full"
                    )
                self._family_revoked[family_digest] = False

            if previous_digest is not None:
                self._remove_digest_locked(previous_digest)
            self._remove_family_locked(family_digest)
            ticket, digest = self._generate_locked(previous_digest)
            self._evict_oldest_locked()
            self._entries.append((digest, expires_at))
            self._entry_families[digest] = family_digest
        return ticket

    def redeem(self, ticket: object) -> bool:
        """Consume one matching unexpired capability without exposing stored digests."""

        candidate = self._digest(ticket)
        if candidate is None:
            return False
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            return self._remove_digest_locked(candidate)

    def revoke(self, ticket: object) -> bool:
        """Remove one matching unexpired capability without exposing validity over HTTP."""

        candidate = self._digest(ticket)
        if candidate is None:
            return False
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            return self._remove_digest_locked(candidate)

    def revoke_family(self, family: object) -> int:
        """Tombstone one family for this process and remove all of its current tickets."""

        family_digest = self._digest(family)
        if family_digest is None:
            raise ValueError("reload capability family must be ASCII text")
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            if family_digest not in self._family_revoked:
                if len(self._family_revoked) >= self._max_families:
                    raise ReloadCapabilityFamilyCapacityError(
                        "reload capability family registry is full"
                    )
                self._family_revoked[family_digest] = True
            else:
                self._family_revoked[family_digest] = True
            return self._remove_family_locked(family_digest)

    @property
    def outstanding_count(self) -> int:
        """Return the number of currently unexpired digests without exposing them."""

        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            return len(self._entries)

    @property
    def family_count(self) -> int:
        """Return bounded process family count for internal qualification only."""

        with self._lock:
            return len(self._family_revoked)

    @property
    def revoked_family_count(self) -> int:
        """Return process tombstone count for internal qualification only."""

        with self._lock:
            return sum(1 for revoked in self._family_revoked.values() if revoked)
