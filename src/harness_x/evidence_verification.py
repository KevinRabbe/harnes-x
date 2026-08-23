"""Offline verification for the portable M43 terminal evidence set."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from harness_x.app_server.evidence_manifest import (
    CodingReportEvidenceAvailable,
    TerminalEvidenceManifest,
    TraceEvidenceAvailable,
)
from harness_x.app_server.report_attestation import MAX_REPORT_BYTES
from harness_x.app_server.trace_export import MAX_TRACE_EXPORT_BYTES
from harness_x.app_server.trace_projection import verify_trace_payload
from harness_x.core.errors import TraceCorruptionError

MAX_EVIDENCE_MANIFEST_BYTES = 2 * 1024 * 1024


class PortableEvidenceVerificationError(RuntimeError):
    """Portable evidence input is malformed, inconsistent, or does not match its manifest."""


@dataclass(frozen=True, slots=True)
class BoundedEvidenceSource:
    """One exact bounded local-file read used for offline verification."""

    path: str
    payload: bytes
    source_bytes: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class PortableEvidenceVerification:
    """Successful offline verification result."""

    session_id: str
    manifest_bytes: int
    manifest_sha256: str
    report_status: str
    trace_status: str
    trace_records: int | None

    def summary(self) -> str:
        trace_records = "none" if self.trace_records is None else str(self.trace_records)
        return (
            "valid: "
            f"session={self.session_id} "
            f"manifest_bytes={self.manifest_bytes} "
            f"manifest_sha256={self.manifest_sha256} "
            f"report={self.report_status} "
            f"trace={self.trace_status} "
            f"trace_records={trace_records}"
        )


def _bounded_regular_file(path: str | Path, *, maximum_bytes: int) -> BoundedEvidenceSource:
    if maximum_bytes < 1:
        raise ValueError("maximum_bytes must be positive")

    supplied = Path(path).expanduser()
    lexical = supplied.absolute()
    try:
        final_metadata = os.lstat(lexical)
    except OSError as exc:
        raise PortableEvidenceVerificationError(
            f"evidence source is unavailable: {lexical}: {exc}"
        ) from exc
    if stat.S_ISLNK(final_metadata.st_mode):
        raise PortableEvidenceVerificationError(
            f"evidence source cannot be a symbolic link: {lexical}"
        )
    if not stat.S_ISREG(final_metadata.st_mode):
        raise PortableEvidenceVerificationError(
            f"evidence source is not a regular file: {lexical}"
        )

    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise PortableEvidenceVerificationError(
            f"cannot resolve evidence source: {lexical}: {exc}"
        ) from exc
    if resolved != lexical:
        raise PortableEvidenceVerificationError(
            f"evidence source resolves through symbolic-link path substitution: {lexical}"
        )

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lexical, flags)
    except OSError as exc:
        raise PortableEvidenceVerificationError(
            f"cannot open evidence source: {lexical}: {exc}"
        ) from exc

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PortableEvidenceVerificationError(
                f"evidence source is not a regular file after open: {lexical}"
            )
        if metadata.st_size > maximum_bytes:
            raise PortableEvidenceVerificationError(
                f"evidence source exceeds {maximum_bytes} byte limit: {lexical}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise PortableEvidenceVerificationError(
                f"evidence source exceeds {maximum_bytes} byte limit: {lexical}"
            )
    finally:
        os.close(descriptor)

    return BoundedEvidenceSource(
        path=str(lexical),
        payload=payload,
        source_bytes=len(payload),
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PortableEvidenceVerificationError(
                f"manifest JSON contains duplicate object key: {key}"
            )
        result[key] = value
    return result


def _load_manifest(source: BoundedEvidenceSource) -> TerminalEvidenceManifest:
    try:
        text = source.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PortableEvidenceVerificationError("manifest is not valid UTF-8") from exc
    try:
        raw = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except PortableEvidenceVerificationError:
        raise
    except json.JSONDecodeError as exc:
        raise PortableEvidenceVerificationError(
            f"manifest is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise PortableEvidenceVerificationError("manifest JSON root must be an object")

    supplied_fingerprint = raw.get("fingerprint")
    if not isinstance(supplied_fingerprint, str):
        raise PortableEvidenceVerificationError("manifest fingerprint is missing or invalid")
    try:
        manifest = TerminalEvidenceManifest.model_validate(raw)
    except ValidationError as exc:
        raise PortableEvidenceVerificationError(
            f"manifest does not satisfy app-terminal-evidence-manifest-v1: {exc}"
        ) from exc
    if supplied_fingerprint != manifest.fingerprint:
        raise PortableEvidenceVerificationError(
            "manifest fingerprint does not match manifest contents"
        )
    return manifest


def _verify_report_provenance(report: CodingReportEvidenceAvailable) -> None:
    attested_bytes = report.attested_source_bytes
    attested_sha = report.attested_source_sha256
    if report.attestation_status == "verified":
        if attested_bytes is None or attested_sha is None:
            raise PortableEvidenceVerificationError(
                "verified report provenance is missing durable attested byte identity"
            )
        if attested_bytes != report.source_bytes or attested_sha != report.source_sha256:
            raise PortableEvidenceVerificationError(
                "verified report provenance disagrees with current source identity"
            )
        return
    if attested_bytes is not None or attested_sha is not None:
        raise PortableEvidenceVerificationError(
            f"{report.attestation_status} report provenance cannot contain attested byte identity"
        )


def _verify_report(
    manifest: TerminalEvidenceManifest,
    report_path: str | Path | None,
) -> str:
    evidence = manifest.coding_report
    if evidence.availability == "not_available":
        if report_path is not None:
            raise PortableEvidenceVerificationError(
                "manifest marks coding report not_available but --report was supplied"
            )
        return "not_available"

    if report_path is None:
        raise PortableEvidenceVerificationError(
            "manifest requires coding report evidence; supply --report"
        )
    if not isinstance(evidence, CodingReportEvidenceAvailable):
        raise PortableEvidenceVerificationError("manifest coding report evidence is invalid")
    _verify_report_provenance(evidence)

    source = _bounded_regular_file(report_path, maximum_bytes=MAX_REPORT_BYTES)
    if source.source_bytes != evidence.source_bytes:
        raise PortableEvidenceVerificationError(
            "coding report byte count does not match manifest"
        )
    if source.source_sha256 != evidence.source_sha256:
        raise PortableEvidenceVerificationError(
            "coding report SHA-256 does not match manifest"
        )
    try:
        text = source.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PortableEvidenceVerificationError("coding report is not valid UTF-8") from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PortableEvidenceVerificationError(
            f"coding report is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise PortableEvidenceVerificationError("coding report JSON root must be an object")
    return evidence.attestation_status


def _verify_trace(
    manifest: TerminalEvidenceManifest,
    trace_path: str | Path | None,
) -> tuple[str, int | None]:
    evidence = manifest.causal_trace
    if evidence.availability == "not_available":
        if trace_path is not None:
            raise PortableEvidenceVerificationError(
                "manifest marks causal trace not_available but --trace was supplied"
            )
        return "not_available", None

    if trace_path is None:
        raise PortableEvidenceVerificationError(
            "manifest requires causal trace evidence; supply --trace"
        )
    if not isinstance(evidence, TraceEvidenceAvailable):
        raise PortableEvidenceVerificationError("manifest causal trace evidence is invalid")

    source = _bounded_regular_file(trace_path, maximum_bytes=MAX_TRACE_EXPORT_BYTES)
    if source.source_bytes != evidence.source_bytes:
        raise PortableEvidenceVerificationError(
            "causal trace byte count does not match manifest"
        )
    if source.source_sha256 != evidence.source_sha256:
        raise PortableEvidenceVerificationError(
            "causal trace SHA-256 does not match manifest"
        )
    try:
        records, partial_ignored = verify_trace_payload(
            source.payload,
            expected_trace_id=evidence.trace_id,
            require_complete_final_line=True,
            source_label=source.path,
        )
    except TraceCorruptionError as exc:
        raise PortableEvidenceVerificationError(
            f"causal trace integrity verification failed: {exc}"
        ) from exc
    if partial_ignored:
        raise PortableEvidenceVerificationError(
            "causal trace verification unexpectedly ignored a partial final record"
        )
    if len(records) != evidence.record_count:
        raise PortableEvidenceVerificationError(
            "causal trace record count does not match manifest"
        )
    final_event_hash = records[-1].event_hash if records else None
    if final_event_hash != evidence.final_event_hash:
        raise PortableEvidenceVerificationError(
            "causal trace final event hash does not match manifest"
        )
    return "verified", len(records)


def verify_portable_evidence(
    manifest_path: str | Path,
    *,
    report_path: str | Path | None = None,
    trace_path: str | Path | None = None,
) -> PortableEvidenceVerification:
    """Verify one portable M43 manifest and its explicitly supplied evidence files offline."""

    manifest_source = _bounded_regular_file(
        manifest_path,
        maximum_bytes=MAX_EVIDENCE_MANIFEST_BYTES,
    )
    manifest = _load_manifest(manifest_source)
    report_status = _verify_report(manifest, report_path)
    trace_status, trace_records = _verify_trace(manifest, trace_path)
    return PortableEvidenceVerification(
        session_id=manifest.session_id,
        manifest_bytes=manifest_source.source_bytes,
        manifest_sha256=manifest_source.source_sha256,
        report_status=report_status,
        trace_status=trace_status,
        trace_records=trace_records,
    )
