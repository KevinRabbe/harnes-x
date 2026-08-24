# Milestone 66 — Project + Chat Domain Foundation

## Authority

M66 is stacked exactly on frozen M65:

`c902d2e6bfd429c70acf3b4e3d2f3aaef2af3a92`

M65 remains frozen. M66 may not rewrite the qualified Windows desktop-host boundary, authentication model, execution authority, evidence semantics, or self-improvement promotion authority.

## Objective

Define and implement the durable local domain foundation required for everyday Harness X use:

1. first-class projects bound to workspace roots;
2. multiple durable chats per project;
3. append-oriented durable chat messages;
4. create/open/rename/archive lifecycle semantics;
5. deterministic recent-project and last-opened project/chat restoration;
6. explicit separation between chat history, Harness X cognitive memory, execution sessions, and authoritative traces/evidence.

M66 is deliberately persistence-first. It does not yet make a chat message invoke the reasoning or coding runtime.

## Domain boundary

The product model is:

`Project -> Chat -> ChatMessage`

A project is a durable operator workspace. A chat is a durable conversation container. A chat may later link to zero or more Harness X execution sessions, but an execution session is not itself a chat.

Chat history records what the operator and product communicated. It must not become a replacement for project memory, procedural memory, traces, evidence, or execution state.

## Initial project fields

The minimal M66 project contract should include:

- stable project ID;
- human-readable name;
- canonical workspace root;
- created/updated/last-opened timestamps;
- optional default model profile identifier;
- archived state.

Broader autonomy, verification, environment, branch-policy, and permission settings are deferred unless required to preserve a clean forward-compatible boundary.

## Initial chat fields

The minimal M66 chat contract should include:

- stable chat ID;
- owning project ID;
- human-readable title;
- created/updated/last-opened timestamps;
- archived state;
- durable ordered message history.

## Message boundary

Messages must have stable IDs, ordering metadata, timestamps, role/type information, and typed content sufficient to distinguish at least user, assistant/product, and system-notice records without treating arbitrary execution telemetry as ordinary prose.

M66 does not yet define the complete M70 streaming activity vocabulary.

## Persistence invariants

M66 storage must be local, deterministic, restart-safe, and operator-owned. The design must establish:

- no silent cross-project message mixing;
- canonical workspace identity;
- stable IDs independent of display names;
- append-oriented message durability rather than rewriting an unbounded monolithic transcript;
- atomic metadata replacement for project/chat indexes and mutable summaries;
- deterministic recovery after restart;
- archived records remain addressable but are excluded from ordinary active listings;
- deleting or archiving a chat must not delete authoritative execution evidence that may later be linked to it;
- storage corruption or schema mismatch fails visibly rather than silently fabricating state.

The expected desktop data root remains under `%LOCALAPPDATA%\\Harness X` on Windows through the existing App Server root selected by the M65 desktop host.

## API boundary

M66 may introduce internal repository/service contracts needed to support later App Server APIs, but M67 owns the authenticated project/chat HTTP surface.

Domain and persistence code must therefore remain independent of WebView2 and browser UI implementation details.

## Non-goals

M66 does not:

- invoke a model from a chat message;
- create the conversation-to-execution bridge;
- stream work activity;
- add approvals or attachments;
- redesign the existing Local Operator page;
- change self-improvement policy or promotion authority;
- bundle or install runtimes;
- add cloud sync, accounts, collaboration, or remote execution.

## Qualification

Freeze requires one exact head with:

- full existing pytest on Ubuntu and Windows;
- `harness-x --help` and default-config validation on both lanes;
- focused domain/persistence tests covering project lifecycle, chat lifecycle, message ordering/durability, restart restoration, archive behavior, canonical workspace identity, corruption rejection, and cross-project isolation;
- source/diff audit proving no execution, authentication, evidence, promotion-authority, or desktop-host authority expansion;
- final PR review and review-thread recheck.

## Freeze claim

A qualified M66 may claim that Harness X has a durable local Project + Chat domain/persistence foundation suitable for later everyday conversational UX.

It may not claim that chats execute Harness X work, stream responses, or replace the existing Local Operator workflow.
