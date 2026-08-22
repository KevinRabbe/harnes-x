# Milestone 37 — Resilient Operator Streams

M37 is stacked directly on frozen M36 and is intentionally narrow. It improves the local operator UI's transport resilience without creating a second lifecycle or causal event truth and without changing any Harness X runtime authority.

## Scope

M36 already exposes authenticated lifecycle and causal trace SSE with authoritative source cursors. M37 adds bounded client-side automatic reconnect/resume after an unexpected stream interruption.

The client must resume only from source identities it has actually received:

- App Server lifecycle stream cursor: durable `AppEvent.sequence`;
- causal trace stream cursor: verified source `TraceRecord.step` projected by M35.

M37 does not invent synthetic cursor values, merge the two streams, or persist browser credentials/cursors outside the current tab.

## Non-goals

M37 does not add a desktop shell, arbitrary artifact/file serving, privileged filesystem pickers, hard running-task cancellation, WebSockets, a frontend framework, or any new model/tool/verification/memory/completion authority.

## Freeze gates

Before M37 is frozen:

- reconnect behavior is bounded and deterministic;
- reconnect resumes from the last actually received authoritative cursor;
- terminal sessions stop reconnecting;
- selection changes/locking abort pending streams and reconnect timers;
- stale stream generations cannot render into a newly selected session;
- bearer authentication remains header-only and in-memory;
- deterministic tests cover reconnect policy/cursor safety;
- packaged JavaScript syntax passes qualification;
- M36→M37 diff is confined to intended UI/tests/docs;
- exact-head Linux CI passes.
