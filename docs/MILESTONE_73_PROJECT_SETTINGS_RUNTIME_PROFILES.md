# Milestone 73 — Project Settings + Runtime Profiles

## Status

Implementation milestone stacked exactly on frozen M72
`8112124d2c3d33282ebdf9008322161b8a024aa1`.

This document is the first M73 commit and defines the milestone boundary before implementation.

## Roadmap reconciliation

The repository roadmap originally placed Project Settings + Runtime Profiles before Approvals +
Interactive Decisions. M72 intentionally implemented the approval boundary first and explicitly
left project settings UX as a non-goal. M73 fills that deferred capability without renumbering,
rewriting, or moving frozen M72. M74 remains the planned Files, Attachments, Diffs + Artifacts
milestone, and M75 remains the everyday reliability checkpoint.

## Objective

Add durable, authenticated project-level defaults that make everyday chat execution practical
without moving runtime, verification, tool, approval, memory, evidence, or improvement authority
into the product/UI layer.

A Project should be able to remember narrowly scoped defaults for future conversation executions:
model profile, verification strategy, project instructions, and an explicit autonomy/permission
profile. These settings are product configuration inputs only. The existing execution stack still
validates and enforces every effective runtime request.

## Required behavior

M73 should:

1. extend the durable Project contract with a versioned settings object rather than scattering
   unrelated optional fields across UI state;
2. preserve backward compatibility for all M66–M72 projects and migrate/default missing settings
   deterministically on read;
3. support project-level model-profile selection using existing Harness X model profile identities,
   without creating a second model registry in the product layer;
4. support a bounded verification strategy that compiles deterministically into the existing
   App Server `CodingSessionRequest` verification fields;
5. support bounded project instructions that are persisted as project-owned product state and
   injected into future conversation context through an explicit, provenance-visible boundary;
6. support an explicit autonomy/permission profile as product policy input, while preserving M72
   sensitive-action approval and all lower-level permission checks as independent authorities;
7. expose authenticated read/update APIs using the inherited loopback Host/bearer boundary;
8. render a normal Project Settings surface in the everyday workspace using safe DOM operations;
9. ensure settings updates affect only executions submitted after the durable update and never
   mutate already-frozen execution plans, contexts, sessions, approvals, traces, reports, or
   evidence;
10. validate paths/commands/profile names and reject malformed, oversized, unknown, or unsupported
    settings fail-closed rather than silently coercing them; and
11. preserve archived-project, project ownership, restart, and canonical workspace invariants.

## Initial settings boundary

The implementation should remain deliberately small. The planned first-class project settings are:

- `model_profile`: existing Harness X profile name or the current default;
- `verification`: a versioned product strategy compiled into the existing verification command or
  plan boundary, not a new verifier;
- `project_instructions`: bounded UTF-8 text included explicitly in future conversation context;
- `autonomy_profile`: a small enum of product defaults whose meaning is translated into existing
  execution/approval policy, never direct permission grants by the browser.

Narrow environment settings may be added only if a concrete existing runtime requirement makes
one unavoidable during implementation. Arbitrary environment-variable injection is out of scope.

## Authority and safety invariants

The following remain authoritative:

- existing model-profile resolution determines whether a requested model profile is usable;
- existing App Server/coding request validation determines whether an execution may launch;
- existing verifier and verification platform determine verification success;
- existing ToolExecutor permission/schema/budget boundaries determine tool execution eligibility;
- frozen M72 approval policy remains authoritative for sensitive-action release;
- project/chat/execution/context identities remain owned by M66–M72 durable software state;
- traces, reports, evidence, memory, procedures, and improvement promotion remain separate
  authorities.

M73 must not:

- let the browser create model profiles, verification success, tool permissions, shell commands, or
  approval decisions by editing settings;
- reinterpret an autonomy profile as blanket approval for M72-sensitive actions;
- retroactively change an accepted execution or persisted M71 context package;
- inject arbitrary host environment variables, secrets, bearer credentials, signing keys, or
  unrestricted executable paths;
- add file attachments, diff/artifact browsing, native open/reveal actions, installer packaging,
  cloud sync, multi-user settings, or self-improvement policy administration;
- change reasoning, memory, evidence, promotion, or verification authority semantics.

## Qualification plan

Before freeze, M73 must demonstrate on one exact head:

- legacy projects load with deterministic effective defaults;
- settings persist across restart and update atomically/durably;
- malformed/oversized/unknown settings fail closed without partial mutation;
- archived and wrong-project mutations are rejected;
- model-profile settings feed only future conversation execution plans;
- verification settings compile deterministically into the existing request boundary;
- project instructions enter only future M71-style context packages with explicit source identity;
- autonomy defaults cannot bypass M72 sensitive-action approvals;
- authenticated API and safe-DOM Project Settings UI operate without bearer exposure;
- no already-existing execution/session/context/approval/evidence record changes after a settings
  update;
- inherited full pytest, `harness-x --help`, and default config validation pass on Ubuntu and
  Windows;
- inherited Windows desktop restore/build/smoke/publish/artifact qualification passes; and
- final source/diff/review audit records the exact freeze head in the PR body only.

## Non-goals

M73 does not implement attachments, file references, diffs, generated-artifact UX, native open or
reveal actions, crash/reconnect hardening, installer/runtime distribution, remote/cloud settings,
team policy, arbitrary environment injection, model-profile authoring, verifier replacement, or
self-improvement policy editing.
