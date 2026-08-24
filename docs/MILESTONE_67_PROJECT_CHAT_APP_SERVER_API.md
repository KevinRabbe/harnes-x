# Milestone 67 — Project + Chat App Server API

## Authority

M67 is stacked exactly on frozen M66:

`d5a71a34143380c624e631324cddd815b4742ecd`

M66 remains frozen. M67 may expose the frozen Project + Chat domain through the existing authenticated local App Server, but it may not change project/chat identity, persistence semantics, chat-to-memory separation, execution authority, evidence authority, desktop authentication, or self-improvement promotion authority.

## Objective

Expose a minimal authenticated local HTTP API over the frozen M66 `ProjectChatStore` sufficient for the future everyday desktop workspace:

1. list/create/read/open/rename/archive/restore projects;
2. list/create/read/open/rename/archive/restore chats;
3. read and append typed chat messages;
4. read deterministic restoration state;
5. keep all product storage under the existing App Server data root;
6. preserve the existing App Server bearer/bootstrap authentication boundary for every project/chat endpoint.

M67 is an API milestone. It does not make a user message invoke a model, coding runtime, tool, or verification session.

## Storage integration

`AppServerService` may own one `ProjectChatStore` rooted under its existing local data root, e.g. `<app-server-data>/product`.

The HTTP layer must call service/store methods rather than manipulate M66 state/JSONL files directly.

## HTTP boundary

The API should be explicitly versioned under the existing local server origin, for example `/api/v1/projects` and subordinate resources.

Required resource groups:

- projects;
- project chats;
- chat messages;
- product restoration state.

Request bodies and response payloads must use strict typed schemas. Invalid JSON, unknown fields, wrong ownership, archived-state violations, invalid IDs, duplicate workspaces, and malformed operations must produce deterministic client errors rather than internal-server errors or silent mutation.

## Authentication

Every new API endpoint remains protected by the existing App Server authentication model. M67 must not introduce a second token, cookie authority, bypass path, unauthenticated read endpoint, or WebView2-specific privileged channel.

The one-time M65 bootstrap mechanism and persistent bearer ownership remain unchanged.

## Concurrency and lifecycle

The App Server can receive HTTP requests concurrently. M67 must therefore ensure product mutations cannot race into duplicate message sequences, lost metadata updates, or overlapping atomic replacements.

The narrow initial approach may serialize `ProjectChatStore` operations through an App Server/service lock. More elaborate database or optimistic-concurrency machinery is out of scope.

App Server shutdown must close no new background workers because M67 introduces no new execution worker.

## Response projection

M67 should return product-domain data, not internal filesystem implementation details. In particular, API responses must not expose:

- message ledger file paths;
- metadata state file paths;
- token/access-token paths;
- server-info paths;
- internal temporary files;
- unrelated project memory, trace, evidence, or improvement state.

## Non-goals

M67 does not:

- redesign the UI;
- invoke models from messages;
- create Harness X coding sessions from chats;
- stream execution activity;
- add attachments/artifacts/diffs;
- implement approvals;
- implement conversation context selection;
- alter Local Operator/session APIs;
- change project/chat persistence schemas unless a demonstrated API integration defect requires a backward-compatible M66 correction.

## Qualification

Freeze requires one exact head with:

- full pytest on Ubuntu and Windows;
- existing CLI/config checks on both OS lanes;
- inherited M65 Windows desktop restore/build/smoke/publish gates;
- focused authenticated HTTP tests covering project/chat/message/restoration CRUD and lifecycle operations;
- explicit unauthenticated/invalid-auth rejection for the new endpoints;
- malformed/extra-field/invalid-ID/wrong-project/archived-state/duplicate-workspace error tests;
- concurrency coverage proving serialized message append yields unique contiguous sequences;
- restart coverage proving API-created product state survives a new App Server service instance;
- source/diff audit proving no reasoning/coding/evidence/improvement authority expansion;
- final review/review-thread recheck.

## Freeze claim

A qualified M67 may claim that the local authenticated Harness X App Server exposes the durable M66 Project + Chat product domain through a versioned API suitable for the future everyday desktop workspace.

It may not claim that chats execute Harness X work or that the everyday Projects + Chats UI exists yet.
