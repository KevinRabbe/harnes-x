# Milestone 72 — Sensitive Action Approval Boundary

## Status

Implementation milestone stacked exactly on frozen M71
`7cbfda75f9247d2b109647f6ec641d6f234e77e2`.

This document is the first M72 commit and defines the milestone boundary before implementation.

## Roadmap position

M72 implements step 9 of the previously agreed everyday-use roadmap: make approvals and sensitive
actions explicit and visibly confirmable after Projects/Chats execution, grounded work activity, and
bounded conversation context are already durable.

M72 is an approval-boundary milestone. It does not make the browser, chat transcript, or approval
record authoritative for choosing tools, constructing actions, deciding verification success, or
promoting self-improvement candidates.

## Objective

Add one durable, authenticated, fail-closed approval boundary for conversation work that attempts an
action classified by existing Harness X policy as requiring explicit operator consent. A pending
approval must describe a software-proposed action without executing it; only an exact matching
operator decision may release that already-proposed action.

## Required behavior

M72 should:

1. identify the narrow existing runtime seam where an action is already proposed and policy can
   classify whether operator approval is required before execution;
2. define versioned durable approval-request and approval-decision records with stable IDs,
   project/chat/execution/session/action correlation, bounded display metadata, timestamps, and
   fingerprints/provenance sufficient for restart-safe reconciliation;
3. ensure a pending approval is created before the sensitive action executes and that the runtime
   waits without busy-spinning while the decision is unresolved;
4. expose authenticated project/chat/execution-scoped approval reads and exact approve/reject
   mutations through the existing loopback App Server authority boundary;
5. make approval decisions exact-once/idempotent and reject cross-project/chat/execution/action
   substitution, stale IDs, conflicting second decisions, malformed requests, and decisions for
   already-terminal executions;
6. on approval, release only the exact software-proposed action that produced that approval record;
7. on rejection, fail or recover through the existing runtime/controller semantics without
   fabricating a successful action result;
8. preserve restart behavior explicitly: unresolved approvals must remain visible/durable, and a
   process restart must never silently convert an unresolved approval into approval or execute it;
9. render pending approval state in the everyday chat UI using safe DOM text rendering, with
   explicit approve/reject controls and no bearer exposure or browser-authored action payload;
10. keep ordinary non-sensitive actions and direct non-conversation App Sessions behavior
    unchanged unless they cross the same established approval policy intentionally; and
11. keep approval state separate from chat history, causal trace/evidence, long-term memory, and
    self-improvement promotion authority.

## Authority and safety invariants

The following remain authoritative:

- the existing runtime/model/controller proposes actions;
- existing tool/permission policy determines action eligibility and sensitivity classification;
- the existing tool executor remains the only component that executes an approved action;
- existing verification and completion logic determine task outcome;
- App Server session state, causal trace, reports, and evidence remain authoritative execution
  records;
- M69–M71 project/chat/execution/context identity remains authoritative for everyday conversation
  ownership;
- the operator supplies only the approval decision for an already-proposed exact action.

M72 must not:

- allow the browser to author tool names, arguments, shell commands, paths, or action proposals;
- interpret chat prose such as “yes” as an approval decision;
- auto-approve based on model output, prior approvals, profile defaults, timeout, restart, or UI
  presence;
- allow an approval for one action/execution/project/chat to authorize another;
- expose raw tool output, private reasoning, bearer credentials, signing keys, or evidence secrets
  in approval records;
- weaken existing tool permission checks or verification requirements;
- promote memory, procedures, model adapters, improvement candidates, or configuration changes;
- add attachment ingestion, file/artifact chat integration, project run-profile administration,
  installer/runtime packaging, remote/cloud approvals, multi-user identity, or unrelated UI work.

## Qualification plan

Before freeze, M72 must demonstrate on one exact head:

- one software-proposed sensitive action remains unexecuted while approval is pending;
- authenticated exact approve releases only that action and executes it once;
- authenticated reject prevents execution and produces a fail-visible runtime outcome;
- repeated identical decisions are idempotent while conflicting decisions fail closed;
- ownership/action substitution and malformed/stale decisions are rejected;
- unresolved approval survives durable reload without becoming approved;
- restart/interruption does not silently execute unresolved work;
- ordinary non-sensitive execution remains regression-compatible;
- everyday UI displays pending approval and sends only approval ID + decision through the inherited
  authenticated request helper with safe DOM rendering;
- no browser-authored action payload, hidden auto-approval path, bearer leak, raw reasoning/tool
  output, or authority expansion exists;
- inherited full pytest, `harness-x --help`, and default config validation pass on Ubuntu and
  Windows;
- inherited Windows desktop restore/build/smoke/publish/artifact qualification passes; and
- final source/diff/review audit records the exact freeze head in the PR body only.

## Non-goals

M72 does not claim a complete enterprise authorization system, role-based access control, remote
human-in-the-loop service, cryptographic signer identity for approvals, policy administration,
approval delegation, attachment/file UX, project settings UX, or automatic self-improvement
promotion. Those require later explicit milestones.
