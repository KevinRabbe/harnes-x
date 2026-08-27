# Milestone 76 — Improvement Observatory

M76 is stacked exactly on frozen M75 `bcfdde078e9b1d979e351cb67b072ccc6ec00521`.

## Objective

Add a desktop-visible, authenticated, read-only observatory for improvement evidence Harness X already owns. The observatory may summarize system versions, diagnosed weaknesses, candidate/revision state, sandbox experiments, regressions, promotion/rollback records, and procedure-improvement campaigns, but it does not diagnose, propose, experiment, promote, roll back, approve, or run campaigns itself.

## Authority boundary

M76 is projection only.

- Existing M15/M16 experiment and promotion authorities remain authoritative for system-level improvement.
- Existing M29 procedure reliability remains the authority for suspension/revalidation.
- Existing M30 procedure revision state remains the authority for candidate validation/promotion.
- Existing M31 procedure campaign state remains the campaign orchestration authority.
- Existing M72 sensitive-action approval, M73 settings, M74 resource provenance, and M75 recovery authority are unchanged.
- Observatory records and UI state never become execution context, model-routing input, verification evidence, promotion evidence, approval evidence, memory truth, or self-improvement authority.
- No M76 POST/PUT/PATCH/DELETE route may mutate improvement state.
- No Promote, Roll back, Run experiment, Generate candidate, Start campaign, Approve, or similar control is in M76 scope.

## Observation root

For a selected Project, the server derives one fixed root from durable server-owned project identity:

`<project workspace>/.harness-x`

The browser cannot supply or override an observatory root, host path, glob, traversal, artifact filename, or filesystem capability.

Observation is bounded and fail-visible:

- never follow symlinks;
- require every resolved source file to remain under the fixed `.harness-x` root;
- inspect only exact allowlisted record basenames and exact canonical project-memory state paths;
- cap traversal depth, candidate file count, individual record bytes, and aggregate bytes;
- parse records with the existing strict Pydantic schemas whenever one exists;
- reject duplicate/conflicting identities instead of choosing one silently;
- expose project-relative source provenance, never a browser-authored absolute path;
- malformed/corrupt records become explicit source errors and do not mutate or repair the source;
- missing data is `not_observed`, not inferred.

Initial allowlisted record families:

- `experiment-report.json` → existing `SandboxExperimentReport`;
- `promotion-record.json` → existing `PromotionRecord`;
- `closed-improvement-loop-report.json` → existing `ClosedImprovementLoopReport`;
- `procedure-improvement-campaign-report.json` → existing `ProcedureImprovementCampaignReport`;
- `.harness-x/project-memory/project-memory.json` → existing `ProjectMemoryState`;
- `.harness-x/project-memory/procedure-reliability.json` → existing `ProcedureReliabilityState`;
- `.harness-x/project-memory/procedure-revisions.json` → existing `ProcedureRevisionStoreState`;
- `.harness-x/project-memory/procedure-improvement-campaigns.json` → existing `ProcedureImprovementCampaignStoreState`;
- `active-config.json` → existing `ActiveConfigPointer`, projected only as version/hash/pointer provenance without loading arbitrary config content.

A promotion record may contain a rollback artifact path. M76 must not follow that path merely because the record says so. It may independently hash a rollback artifact only when the resolved artifact is an ordinary non-symlink file inside the fixed observatory root and remains within the byte bound. Otherwise rollback evidence is shown as recorded-but-not-independently-verified.

## Projection contract

The project observatory should expose a bounded `improvement-observatory-v1` snapshot containing at least:

- Harness X software version;
- observed system/config versions and source provenance;
- diagnosed procedure weaknesses from M29 suspended records;
- procedure revision candidate counts/state and bounded candidate summaries;
- sandbox experiment summaries, disposition, regressions/new failure modes/budget violations, and source fingerprints;
- promotion summaries including qualification, active/denied/rolled-back status, verification status, and rollback evidence state;
- bounded M31 campaign summaries including budget consumption, pending step, terminal status/reason, and promoted candidate identity;
- source health: observed, missing, malformed/corrupt, truncated-by-observatory-budget, and duplicate/conflict states;
- explicit `read_only: true` / `promotion_authority: false` semantics.

Producer assertions remain labeled as producer assertions. For example, a `rollback_artifact_verified` field inside an M16 report is not upgraded into fresh M76 verification unless M76 independently verifies the bounded in-root artifact bytes.

## HTTP boundary

Add authenticated GET-only project projection under the inherited loopback App Server, for example:

`GET /v1/projects/{project_id}/improvement-observatory`

Requirements:

- inherited bearer authentication and loopback/Host protections;
- no query parameters in v1;
- project identity resolved server-side;
- archived projects may be inspected read-only if the durable project still exists, but a missing workspace is fail-visible and returns no fabricated improvement state;
- no CORS expansion;
- bounded JSON response;
- no raw secret/config payloads, environment variables, API keys, bearer material, arbitrary command text, or unrestricted filesystem contents.

## Everyday UI

Add one final M76 bridge after the frozen M75 reliability bridge.

The observatory UI is read-only and should provide concise sections for:

- Version / observed state;
- Weaknesses;
- Candidates;
- Experiments & regressions;
- Promotion / rollback evidence;
- Procedure campaigns;
- Source health / unavailable evidence.

It may refresh the projection explicitly. It must not persist observatory state in browser storage and must not add mutation controls or derive authority from presentation state.

## Safety qualification

Focused tests must prove at least:

- exact M76 asset allowlisting/bootstrap order;
- GET-only authenticated route and no query/path-root input;
- no filesystem mutation from observatory GET, including when project-memory files are absent;
- bounded traversal/file/byte behavior;
- symlink/traversal escape rejection;
- strict parsing and fail-visible corrupt records;
- duplicate/conflicting record fail-visible behavior;
- source-relative provenance only;
- no secret/config payload leakage from active config artifacts;
- no mutation verbs or promotion/rollback/campaign controls in the UI;
- no browser persistence/credential access in the M76 bridge;
- procedure reliability/revision/campaign projections match existing durable state without becoming authority;
- experiment regressions/disposition are projected as recorded evidence, not a promotion command;
- rollback paths outside the observatory root are not followed;
- full inherited Ubuntu/Windows CI and Windows desktop package gate remain green.

## Freeze rule

M76 may freeze only on an exact green PR synthetic merge stacked on frozen M75, after exact diff/review/authority audit. The PR remains draft/open/unmerged unless a merge is separately and explicitly authorized. Any Git head movement invalidates the freeze.
