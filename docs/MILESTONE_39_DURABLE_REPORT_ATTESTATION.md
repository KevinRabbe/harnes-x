# Milestone 39 — Durable Report Attestation

M39 is stacked directly on frozen M38 and closes one deliberately documented integrity gap: M38 can fingerprint the current coding-report bytes, but the pre-M39 `ARTIFACT_AVAILABLE` event anchors only the report path and therefore cannot prove that current bytes equal the bytes observed when the artifact was durably recorded.

## Scope

M39 adds content attestation only for the canonical App Server coding report. New report artifact events may durably commit:

- the canonical report path;
- the exact source byte count;
- SHA-256 of the exact source bytes;
- an explicit attestation schema/status.

Those values become part of the existing hash-chained, fsynced App Server event ledger before the terminal session transition. M39 does not introduce a second ledger, signature system, certificate authority, generic artifact attestation API, or arbitrary filesystem hashing surface.

## Compatibility

M38-era sessions whose report artifact event contains only a path remain readable and are projected explicitly as `legacy_unattested`. They are not reclassified as corrupt merely because historical bytes were never committed.

For reports produced under M39, a complete durable attestation must match the exact current source byte count and SHA-256 before the report can be projected as verified. A complete attestation mismatch is fail-visible report corruption; it is never silently downgraded to legacy.

If attestation capture itself cannot be completed after the coding runtime returns, that observability failure must not rewrite the independently established coding outcome. The artifact event may record an explicit unavailable attestation state so the operator can distinguish it from both verified and legacy data.

## Authority

M39 attests bytes only. It cannot:

- decide whether the coding task succeeded;
- establish verification from report contents;
- modify report bytes;
- repair or rewrite lifecycle events;
- read caller-selected files;
- attest arbitrary workspace artifacts;
- mutate trace, memory, revision, tool, budget, or model state.

The coding runtime/verifier remain completion and verification authorities. The App Server lifecycle ledger remains transition evidence. M39 only strengthens provenance for one already-produced canonical report.

## Intended acceptance

Before freeze, M39 must prove:

- new canonical reports receive a complete SHA-256/byte-count attestation in the durable artifact event;
- the attestation event precedes the terminal transition;
- report projection verifies exact event-vs-current byte count and digest;
- valid JSON tampering that M38 could not detect becomes fail-visible corruption for attested reports;
- incomplete/malformed attestation payloads cannot masquerade as verified;
- historical M38 path-only events project as `legacy_unattested`;
- explicit attestation-capture failure is distinguishable and does not rewrite task outcome;
- UI surfaces verified/legacy/unavailable provenance without treating it as task authority;
- no generic file hashing/serving surface is added;
- exact M38→M39 diff stays within intended App Server provenance/UI/tests/docs;
- exact-head Linux CI passes.
