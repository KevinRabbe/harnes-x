# M66 Project + Chat Design Contract

This document narrows `MILESTONE_66_PROJECT_CHAT_DOMAIN_FOUNDATION.md` into implementable domain and persistence contracts.

## 1. Domain ownership

M66 introduces a product-domain package independent of HTTP and WebView2. The intended dependency direction is:

```text
future UI / App Server API
          |
          v
ProjectChatStore service boundary
          |
          v
Project / Chat / ChatMessage domain records
          |
          v
local filesystem persistence
```

The package must not import the desktop host, browser UI, reasoning core, coding runtime, project-memory subsystem, trace recorder, or improvement promotion authority.

## 2. Identity model

### Project identity

A project receives an opaque stable `project_<uuid>` identifier at creation. Display-name changes never change identity.

Workspace uniqueness is enforced separately by a canonical workspace key:

1. expand user notation;
2. resolve to an absolute normalized path without requiring every future load to find the directory online;
3. apply host path-case normalization (`os.path.normcase`);
4. reject creation when the canonical key is already registered by another project, including an archived project.

Creation requires the workspace root to exist and be a directory. Loading/listing an existing project does not require the workspace to remain currently reachable.

### Chat identity

A chat receives an opaque stable `chat_<uuid>` identifier scoped by an owning project ID. Renaming a chat does not change its identity.

### Message identity

Each message receives a stable `msg_<uuid>` identifier plus a strictly monotonic 1-based `sequence` within its chat. Sequence, not timestamp, is the authoritative chat ordering key.

## 3. Project record

Initial record fields:

- `project_id`
- `name`
- `workspace_root`
- `workspace_key`
- `default_model_profile: str | None`
- `archived: bool`
- `last_opened_chat_id: str | None`
- `created_at`
- `updated_at`
- `last_opened_at`

Validation:

- names are trimmed, nonblank, and bounded;
- workspace root/key are nonblank absolute canonical strings;
- default model profile is an identifier only; M66 does not validate model availability;
- an archived project remains addressable by exact ID but is excluded from ordinary active listings.

## 4. Chat record

Initial record fields:

- `chat_id`
- `project_id`
- `title`
- `archived: bool`
- `message_count`
- `created_at`
- `updated_at`
- `last_opened_at`

Validation:

- title is trimmed, nonblank, and bounded;
- owner project must exist;
- ordinary chat listings exclude archived chats unless explicitly requested;
- archiving does not modify or delete its message ledger.

## 5. Message record

M66 supports typed communication records without prematurely defining M70 execution activity.

Roles:

- `user`
- `assistant`
- `system`

Content variants:

- `text` — normal user/assistant/product prose;
- `system_notice` — bounded product notice with a stable notice code and human-readable text.

A message contains:

- schema version;
- message ID;
- owning project ID;
- owning chat ID;
- 1-based sequence;
- role;
- discriminated typed content;
- UTC creation timestamp.

M66 message ledgers are append-only. Editing/deleting messages is not part of M66.

## 6. Store state

One atomically replaced metadata state owns project/chat summaries and restoration pointers:

- schema version;
- monotonic store revision;
- project tuple;
- chat tuple;
- recent project IDs (most-recent first, bounded);
- `last_opened_project_id`;
- derived state fingerprint.

The fingerprint is SHA-256 over canonical JSON excluding the fingerprint itself. Loading a state whose stored fingerprint does not match the derived fingerprint fails closed.

State-level validation requires:

- unique project IDs;
- unique chat IDs;
- unique workspace keys;
- every chat owner exists;
- each project's `last_opened_chat_id`, when present, refers to one of its own chats;
- recent/last-opened project references exist.

## 7. Filesystem layout

A store rooted at `<root>` uses:

```text
<root>/
  project-chat-state.json
  projects/
    <project-id>/
      chats/
        <chat-id>/
          messages.jsonl
```

No M66 data is stored in the repository workspace itself unless a caller explicitly chooses such a root. The future App Server integration will place this store under its existing local data root, which the M65 desktop host already places beneath `%LOCALAPPDATA%\\Harness X`.

## 8. Durability protocol

### Metadata

Mutable metadata is written as:

`serialize -> temporary writable file -> flush -> fsync -> os.replace`

This matches the Windows-qualified writable-handle fsync pattern established in M64.

### Messages

Message append is:

`construct row -> append canonical JSON + newline -> flush -> fsync -> update metadata state`

The append occurs before metadata replacement. This creates a recoverable crash window: a durable message may exist while `message_count` is stale.

### Restart reconciliation

On load, every existing message ledger is parsed and validated.

For each chat:

- no ledger + committed count 0 is valid;
- ledger shorter than committed `message_count` is corruption and fails closed;
- malformed rows fail closed;
- project/chat identity mismatch fails closed;
- sequences must be exactly contiguous from 1;
- message IDs within one ledger must be unique;
- ledger longer than committed state is treated only as append-first crash recovery;
- reconciliation may monotonically raise `message_count` and chat `updated_at` from the durable rows, then atomically rewrite metadata.

M66 never silently drops a malformed or contradictory row.

## 9. Lifecycle service methods

The initial service boundary should support:

- `create_project(...)`
- `project(project_id)`
- `projects(include_archived=False)`
- `project_for_workspace(path)`
- `rename_project(...)`
- `archive_project(...)`
- `restore_project(...)`
- `open_project(...)`
- `create_chat(project_id, ...)`
- `chat(chat_id)`
- `chats(project_id, include_archived=False)`
- `rename_chat(...)`
- `archive_chat(...)`
- `restore_chat(...)`
- `open_chat(...)`
- `append_text_message(...)`
- `append_system_notice(...)`
- `messages(chat_id)`
- `restoration_state()`

`open_*` is product navigation state, not an execution action.

## 10. Archive semantics

Archive is reversible metadata, not deletion.

- archived projects are excluded from active project lists and ordinary recents;
- archiving the currently open project clears the global last-opened project pointer;
- archived chats are excluded from active chat lists;
- archiving a project's last-opened chat clears that project's last-opened chat pointer;
- exact-ID lookup remains possible for archived records;
- restore does not mutate IDs, messages, or workspace identity.

## 11. Restoration semantics

`restoration_state()` returns enough deterministic product state for a future UI to restore:

- last active project, if still non-archived;
- that project's last active chat, if still non-archived;
- bounded active recent projects in deterministic order.

It must never fabricate a fallback ID. If no valid previous selection exists, it returns `None` for that selection and lets the future UI choose its empty-state behavior.

## 12. Separation from existing memory and evidence

The following invariants are mandatory:

```text
chat transcript != project memory
chat transcript != long-horizon state
chat transcript != trace ledger
chat transcript != evidence manifest
chat transcript != improvement evidence
```

M66 does not automatically copy chat messages into project memory, model context, traces, or improvement evidence.

Future milestones may create explicit projections/links, but authoritative source ownership must remain identifiable.

## 13. Acceptance tests

Focused tests must cover at minimum:

1. create/load project and stable IDs across restart;
2. duplicate canonical workspace rejection, including path-case behavior where applicable;
3. rename without identity change;
4. project archive/restore and active-list semantics;
5. create multiple chats under one project and reject cross-project ownership mistakes;
6. chat rename/archive/restore without message loss;
7. append ordered messages and preserve exact order across restart;
8. append-first crash reconciliation where ledger count exceeds metadata count;
9. reject committed count greater than durable ledger length;
10. reject malformed ledger JSON;
11. reject ledger project/chat identity mismatch;
12. reject non-contiguous sequences and duplicate message IDs;
13. recent-project ordering and deterministic last-opened project/chat restoration;
14. archived selections are cleared rather than silently restored;
15. corrupted metadata fingerprint rejection;
16. state schema/extra-field rejection;
17. Windows-compatible writable-handle metadata fsync path.

## 14. Explicitly deferred

M67 owns HTTP APIs. M68 owns the everyday workspace UI. M69 owns conversation-to-execution. M70 owns streaming work-event vocabulary. M71 owns context selection. M73 owns approval interaction. M74 owns attachments/artifacts/diffs.

Those later milestones must build on this domain rather than changing chat/project identity semantics casually.
