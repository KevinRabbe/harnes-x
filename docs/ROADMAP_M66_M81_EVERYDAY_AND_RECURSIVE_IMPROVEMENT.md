# Harness X Roadmap — M66 through M81

This roadmap follows frozen M65 and defines the next product sequence. It is a planning contract, not authorization to merge any milestone.

## Product invariant

Harness X may improve its ability to propose better changes, but it must not acquire authority to weaken or bypass the independent rules that decide whether those changes are permitted, promoted, or rolled back.

The everyday product layer must remain a projection/control surface over existing Harness X execution, memory, trace, evidence, and improvement authorities rather than becoming a second implementation of them.

## M66 — Project + Chat Domain Foundation

Add durable local `Project`, `Chat`, and `ChatMessage` contracts, restart-safe persistence, archive semantics, recent/last-opened state, canonical workspace identity, and strict separation from cognitive memory and execution evidence. No model invocation from chat yet.

## M67 — Project + Chat App Server API

Expose authenticated local APIs for project/chat lifecycle, message append/read, recent projects, restoration state, and minimal project defaults. Storage remains behind service/repository contracts rather than being manipulated by the UI.

## M68 — Everyday Desktop Workspace

Make Projects + Chats the normal WebView2 landing experience. Add project sidebar, chat list, history, composer, create/rename/archive flows, and preserve Local Operator under an Advanced surface.

## M69 — Conversation to Harness X Execution Bridge

Translate a natural user message into a structured work request attached to a chat, and link the resulting existing Harness X execution session back to that chat. A chat may own many sessions; a session is never the chat itself.

## M70 — Streaming Work Activity

Define a stable product projection of execution state, including work start/completion/failure, assistant messages, file/tool/verification activity, approvals, and changed-file summaries. Authoritative traces/evidence remain the source of truth.

### Review point A

Use Harness X on several real repository tasks. Fix interaction-model defects before adding more product surface.

## M71 — Conversation Context Builder

Build explicit context selection across recent relevant chat turns, project instructions, project memory, procedural memory, working state, and verified execution lineage. Record provenance and token/budget accounting; do not blindly replay entire chat transcripts.

## M72 — Project Settings + Runtime Profiles

Add persistent project defaults for model profile, verification strategy, project instructions, autonomy/permission profile, and narrowly required environment settings.

## M73 — Approvals + Interactive Decisions

Bring authority requests and clarification decisions into chat with approve/reject, option selection, additional-information responses, and deterministic resume semantics.

## M74 — Files, Attachments, Diffs + Artifacts

Add attachments, project-file references, generated artifacts, compact changed-file/test summaries, expandable diffs, and native desktop open/reveal actions.

## M75 — Everyday Reliability

Add crash/restart recovery, reconnect behavior, interrupted-work reconciliation, stop/retry/continue, last-project/chat restoration, practical keyboard shortcuts, window state, and polished loading/error/empty states.

### Review point B

Treat M75 as the first daily-use readiness checkpoint. Expand the self-improvement product surface only after the everyday loop survives real use.

## M76 — Improvement Observatory

Expose the existing bounded improvement machinery in the desktop application: current system version, diagnosed weaknesses, candidates, experiments, regressions, promotion state, rollback evidence, and procedure-improvement campaigns. Do not expand promotion authority.

## M77 — Generalized Deterministic Self-Diagnosis

Create a common evidence-derived diagnostic contract across task outcomes, retrieval usefulness, memory pressure, verification failures, compute use, procedure reliability, repeated recovery, and benchmark history. Diagnoses must produce measurable problem statements with evidence references.

## M78 — Improvement Candidate Portfolio

Allow several bounded candidate hypotheses to compete across permitted improvement families. Rank candidates by expected benefit, uncertainty, experimental cost, and risk. Qualification/sandbox/promotion boundaries remain independent.

## M79 — Multi-Generation Improvement Campaigns

Add durable orchestration for repeated `observe -> diagnose -> experiment -> promote -> establish new baseline -> observe` generations, with global budgets, cooldowns, convergence criteria, duplicate-problem suppression, crash reconciliation, and maximum-generation limits.

The campaign controller may schedule improvement work but may not grant promotion authority to itself or the reasoning model.

## M80 — Cross-Generation Benchmark Governance

Maintain protected longitudinal benchmark families and ancestry across generations. Require protected-regression controls, resource-efficiency tracking, metric-gaming checks, exact rollback ancestry, and comparable evidence across V0 -> V1 -> ... -> Vn.

## M81 — Personal Windows Distribution

Package the qualified desktop shell with a managed Harness X Python runtime and pinned dependencies for personal Windows use. Add normal shortcuts, clean uninstall, clean-machine install qualification, and remove the manual `.venv` App Server selection step. Public/enterprise deployment remains a separate decision.

## Standing architecture

```text
Human conversation
        |
        v
Project + Chat product layer
        |
        v
Harness X orchestration/reasoning
        |
        +-- memory
        +-- procedures
        +-- tools
        +-- verification
        +-- telemetry
        |
        v
Improvement diagnosis
        |
        v
Bounded candidates
        |
        v
Isolated experiments
        |
        v
Independent promotion authority
        |
        +-- reject
        +-- activate
        +-- rollback
```

Each milestone must remain independently scoped, stacked on the exact prior frozen head, and qualified before the next milestone claims its behavior.
