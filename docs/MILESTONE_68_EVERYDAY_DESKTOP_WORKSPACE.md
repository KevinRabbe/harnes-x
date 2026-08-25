# Milestone 68 — Everyday Desktop Workspace

## Authority

M68 is stacked exactly on frozen M67:

`6d9e1329ff87e0407f3e403487e02ba6a336fa36`

It implements the roadmap objective: make Projects + Chats the normal WebView2 landing experience while preserving the existing Local Operator diagnostics under an Advanced surface.

This milestone is a product/UI projection over the frozen M66/M67 Project + Chat domain/API. It does not acquire execution, reasoning, memory, evidence, or self-improvement promotion authority.

## Objective

Replace the current diagnostic-first landing page with an everyday single-user workspace that supports:

- project sidebar with recent active projects;
- create project from a name and workspace directory path;
- select/open a project and restore the last selection;
- per-project chat list;
- create/select/rename/archive chats;
- rename/archive/restore projects where exposed by the product API;
- ordered durable chat history;
- operator text composer writing typed user messages through M67;
- responsive empty/loading/error states suitable for daily desktop use;
- an explicit Advanced surface retaining the existing Local Operator session/evidence tools.

## Hard boundaries

M68 does **not**:

- invoke a reasoning model from chat;
- create or attach coding sessions from chat messages;
- generate assistant/system messages;
- stream execution activity;
- add approvals, attachments, file browsing, diffs, or artifacts to chat;
- change Project/Chat persistence semantics;
- change bearer/bootstrap authentication;
- change trace/evidence/signature semantics;
- change project memory, long-horizon state, procedures, improvement campaigns, or promotion authority;
- move trusted behavior into the Windows WebView host.

The browser UI remains an authenticated same-origin client of the App Server. Durable truth remains behind the M66 store and M67 API.

## Interaction model

The normal `/ui/` experience is three conceptual regions:

1. **Projects** — persistent project selection and project lifecycle controls.
2. **Chats** — chats for the selected project plus conversation history and composer.
3. **Advanced** — the inherited Local Operator diagnostics/session/evidence interface, available on demand rather than serving as the landing experience.

The UI may submit only the M67 operator-authored message shape:

```json
{
  "schema_version": "append-user-message-request-v1",
  "role": "user",
  "content": {
    "type": "text",
    "text": "..."
  }
}
```

M68 must not synthesize an assistant response after submission. Until M69, a sent user message is durably visible in history with a clear product indication that conversational execution is not connected yet.

## Restoration behavior

On authenticated startup the UI reads the frozen M67 restoration state and project list.

- If the restored project still exists and is active, select it.
- Otherwise select the most recent active project when available.
- If the restored chat belongs to the selected project and is active, select it.
- Otherwise select that project's most recent active chat when available.
- Empty states must never fabricate project/chat identifiers.
- Selecting project/chat writes restoration state through M67.

## Advanced preservation

The pre-M68 Local Operator functionality remains reachable from the same loopback UI origin under an explicit Advanced mode/surface. M68 may reorganize its markup and JavaScript, but must preserve access to existing session creation/history, report/trace/evidence projections and exports that were already available through the UI.

## Security constraints

- no bearer token in URL/query/history;
- bootstrap fragment remains one-time and is removed/replaced according to the inherited UI bootstrap flow;
- no cross-origin API access;
- no arbitrary HTML injection from project names, chat titles, or message text;
- user-controlled text is rendered with text-content semantics, not raw HTML;
- normal UI actions continue to use authenticated same-origin requests;
- Advanced does not weaken inherited evidence/export authentication.

## Qualification

Focused tests must establish at least:

1. `/ui/` assets identify Projects + Chats as the primary workspace rather than Local Operator diagnostics.
2. Bootstrap/authentication still uses the inherited one-time ticket and bearer flow.
3. UI source calls the M67 project/chat/restoration/message endpoints and does not call `/v1/sessions` when sending a chat message.
4. Project create/select/rename/archive flows are represented in the UI client.
5. Chat create/select/rename/archive flows are represented in the UI client.
6. Message history uses ordered M67 records and composer writes only typed `role=user` text.
7. User-controlled content is inserted with non-HTML DOM APIs.
8. Empty/loading/error states are represented for project and chat workspace states.
9. Existing Local Operator behavior remains reachable under Advanced.
10. Existing App Server/operator/UI tests continue to pass.
11. Full Ubuntu and Windows pytest suites pass.
12. `harness-x --help` and default config validation pass.
13. Windows desktop restore/build/smoke/publish/artifact gates remain green.

## Freeze rule

M68 freezes only one exact head after exact-head dual-platform CI, compare/diff audit, and final review/thread audit. The PR remains draft/open/unmerged unless the operator explicitly authorizes a merge.
