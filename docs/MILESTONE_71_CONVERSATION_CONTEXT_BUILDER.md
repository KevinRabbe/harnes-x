# Milestone 71 — Conversation Context Builder

## Status

Implementation milestone stacked exactly on frozen M70
`bb17c541135972b3eac4277524969a248a401bb9`.

This document is the first M71 commit and defines the milestone boundary before implementation.

## Objective

Give an M69 conversation execution a stable, bounded, inspectable context package derived from the
existing durable Projects/Chats product state, so a new Harness X work turn can use relevant prior
conversation without making the browser, a model, or an ad-hoc prompt string authoritative.

M71 is a context-construction milestone. It does **not** change the coding controller's authority,
verification semantics, evidence/trust model, memory promotion rules, or the M69 execution identity
contract.

## Required behavior

M71 should:

1. define one versioned conversation-context schema with explicit source/provenance identity;
2. construct context deterministically from authoritative product records already owned by the
   selected project/chat and the exact submitted execution;
3. include the current submitted user turn exactly once and allow bounded prior durable chat turns
   to be represented in chronological order;
4. apply explicit, deterministic item/count/byte bounds rather than relying on model-context-window
   failure or browser-side truncation;
5. preserve role and durable message identity so context can be audited back to product state;
6. pass the resulting context through the App Server execution boundary without granting the UI or
   the context package authority to create assistant/system history;
7. keep restart/idempotency behavior compatible with the existing M69 execution binding and reuse
   the same durable context inputs for the same accepted submission;
8. expose enough bounded metadata for tests/operator inspection to prove what was selected and what
   was omitted, without copying raw causal traces, tool outputs, bearer credentials, or evidence
   secrets into product conversation context;
9. keep the ordinary chat UI compatible with the M68–M70 product workflow; and
10. fail closed on ownership, malformed records, or context/source incoherence.

## Authority and safety invariants

The following remain authoritative and unchanged unless a later milestone explicitly expands them:

- `ProjectChatStore` durable project/chat/message records are the source for conversation history.
- M69 conversation-execution bindings remain the source for project/chat/execution/session identity.
- App Server session state and the existing coding runtime remain authoritative for work execution.
- Existing causal trace, reports, verification results, evidence manifests/signatures/receipts, and
  trust semantics remain authoritative for execution evidence.
- M70 work activity remains a non-authoritative projection only.

M71 must not:

- make browser state, DOM state, or local/session storage a context source;
- add hidden UI-authored system or assistant messages;
- expose or ingest raw reasoning text as conversation context;
- promote chat content into long-term memory or self-model state;
- ingest raw tool outputs or causal-trace payloads into the conversation transcript context;
- relax project/chat/execution ownership checks;
- change verification commands, evidence signing, trust decisions, or self-improvement authority;
- add attachments, settings/profile administration, approval workflows, installer/runtime packaging,
  or unrelated desktop-host features.

## Qualification plan

Before freeze, M71 must demonstrate on the exact head:

- deterministic context construction and stable provenance;
- explicit count/byte bounds, including deterministic omission behavior;
- exact-once representation of the accepted current user submission;
- project/chat ownership and malformed-source rejection;
- idempotent/restart-safe context for an existing execution binding;
- no browser-authored context authority and no raw reasoning/tool/evidence-secret leakage;
- successful integration through the real conversation-execution/App Server boundary;
- inherited full pytest, `harness-x --help`, and default config validation on Ubuntu and Windows;
- inherited Windows desktop restore/build/smoke/publish/artifact qualification; and
- final source/diff/review audit with the exact freeze head recorded only in the PR body.

## Non-goals

M71 does not claim semantic retrieval, embeddings, long-term memory, attachment ingestion, repository
indexing, automatic summarization, user-editable prompt templates, profile/settings UX, approvals,
or a larger model context window. Those require separate explicit milestones if desired.
