# Milestone 23 — Repository Intelligence and Coding ACI

M23 changes repository orientation from a sequence of model-driven directory listings and file reads into bounded software-owned structure supplied before the first reasoning action and refined through explicit on-demand tools.

M23 is stacked on the qualified M22 coding control plane. It does not replace M22 task ownership, commitments, progress control, budgets, tool permissions, trace authority, or software verification.

## Architecture

```text
workspace
   |
   v
RepositoryIntelligenceService
   |-- Git identity
   |-- bounded file inventory
   |-- manifests / source roots / test roots
   |-- repository instructions
   |-- Python AST index             [exact_ast]
   |-- JS/TS fallback index         [heuristic]
   `-- optional semantic provider   [for example LSP]
   |
   +--> bounded startup repository projection
   |        |
   |        v
   |   RepositoryContextReasoningCore
   |        |
   |        v
   |      Qwen / replaceable reasoning core
   |
   `--> on-demand repository tools
            |
            v
         ToolExecutor
            |
            v
      M22 control + verification
```

The startup projection is orientation, not a substitute for current repository state. It is deliberately bounded. After edits, or whenever exact current structure matters, the reasoner must use the repository/Git/file tools rather than assuming the startup snapshot changed automatically.

## Startup repository projection

Before the first model-selected action, Harness X may provide:

- repository root and Git identity;
- current HEAD, branch, and dirty indicator at snapshot creation;
- detected languages;
- common manifests;
- probable source and test roots;
- bounded previews of repository instructions such as `AGENTS.md`, `CONTRIBUTING.md`, `CLAUDE.md`, and README-family files;
- a bounded file map;
- a bounded symbol map;
- a stable snapshot fingerprint.

Ignored/generated trees include common directories such as `.git`, `.venv`, `node_modules`, Python caches, framework caches, `dist`, `build`, `coverage`, and `.harness-x`.

The repository context wrapper never solves an over-budget context by dropping governing task or working-state authority. It progressively reduces the repository projection and ultimately omits it if necessary.

## Symbol precision

Every indexed symbol/reference declares its source precision.

| Precision | Meaning |
| --- | --- |
| `exact_ast` | Parsed by a language parser with source positions; currently used for Python AST indexing. |
| `heuristic` | Bounded text/pattern fallback. Useful navigation evidence, not compiler truth. |
| `lsp` | Returned by an explicitly configured semantic provider such as an LSP/compiler index. |

M23 ships a `RepositorySemanticProvider` protocol rather than hard-wiring one language server. A provider may answer a request or return `None`; Harness X then falls back to the deterministic local index. Provider results must still declare their own precision.

The default JavaScript/TypeScript index is deliberately labeled `heuristic`. M23 does not claim compiler-accurate JS/TS references or type resolution without a semantic provider.

## Live coding ACI

The M23 coding registry contains 13 live tools.

### Repository navigation

- `repository_map` — bounded repository identity/structure projection, optionally refreshed;
- `file_outline` — symbols for one file plus the current full-file SHA-256;
- `symbol_search` — bounded symbol-name lookup;
- `symbol_definition` — symbol metadata plus a bounded source excerpt and current full-file SHA-256;
- `symbol_references` — bounded reference locations with explicit precision;
- `git_status` — structured current Git state;
- `git_diff` — bounded read-only diff.

### Existing coding tools

- `workspace_list`
- `workspace_read`
- `workspace_search`
- `workspace_write`
- `workspace_patch` (upgraded to v2)
- `process_run`

The total remains below the context builder's action bound, so the model receives the full live tool surface instead of an arbitrarily truncated subset.

## `workspace_patch-v2`

M23 preserves the existing tool name `workspace_patch` so the qualified M22 controller continues to recognize every successful edit as a mutation. That preserves verification invalidation and the transition into implementation state.

The v2 input has two explicit modes.

### Exact mode

```json
{
  "mode": "exact",
  "path": "src/example.py",
  "old_text": "return a - b",
  "new_text": "return a + b",
  "expected_occurrences": 1
}
```

This keeps the M21 exact-occurrence precondition.

### Hash-guarded range mode

```json
{
  "mode": "range",
  "path": "src/example.py",
  "start_line": 42,
  "end_line": 44,
  "expected_sha256": "<full-file SHA-256 obtained from current evidence>",
  "replacement": "..."
}
```

Before writing, software hashes the complete current file and compares it with `expected_sha256`. A mismatch refuses the edit without touching disk. This prevents a long-running reasoner from applying a line-based edit to a file that changed after the model last inspected it.

`file_outline` and `symbol_definition` expose the current full-file digest so the reasoner does not need to invent or compute it.

## Git behavior

`git_status` v2 reads `rev-parse`, branch, and porcelain status directly from Git. It does **not** rebuild the repository file/symbol index. This keeps ordinary progress checks cheap even for large repositories.

`git_diff` is read-only and bounded. Neither tool grants commit, checkout, reset, merge, push, or other repository mutation authority.

## Relationship to M22 progress control

M22 remains authoritative for phases, commitments, completion, verifier freshness, and horizon policy.

M23 deliberately preserves the original mutation identity (`workspace_patch`) so M22 does not need a special compatibility path.

The raw workspace inspection tools (`workspace_list`, `workspace_read`, `workspace_search`) remain subject to M22's dedicated inspection-streak threshold. Structured repository navigation is treated as evidence-producing work rather than raw broad scanning; it is still bounded by duplicate-action detection, no-progress detection, total tool/reasoning budgets, and end-horizon convergence policy.

## Local Transformers protocol

The Transformers backend uses a distinct repository-aware constrained core:

```text
transformers_local_repository_coding
transformers-local-repository-coding-v1
```

LM Format Enforcer constrains outputs to the exact 13-tool action surface. Harness X still owns the semantic protocol invariant:

- `continue` -> exactly one action;
- `complete` -> zero actions;
- `blocked` -> zero actions.

The prompt tells the reasoner to use the startup map instead of reflexive root listing, prefer symbol tools for named code, treat heuristic precision appropriately, and use guarded range edits only with software-supplied current hashes.

## Known limitations

M23 intentionally does not yet provide:

- a bundled language server process or compiler index;
- compiler-accurate JavaScript/TypeScript reference resolution in fallback mode;
- automatic incremental symbol-index updates after every edit;
- Git mutation authority;
- OS/container isolation for subprocess execution;
- worktree isolation or rollback environments (planned for M24).

The startup repository snapshot can become stale after edits. On-demand tools provide current file hashes and Git state; callers may explicitly refresh the repository map when a structural change warrants rebuilding the bounded index.

## Qualification targets

M23 is qualified only when the exact PR head passes the full Harness X test suite plus CLI/config smoke checks. Deterministic coverage includes:

- ignored/generated directory handling;
- explicit symbol precision;
- Python AST and JS/TS fallback indexing;
- repository instructions and compact map bounds;
- symbol search/outline/reference behavior;
- semantic-provider fallback;
- structured Git reads;
- exact and hash-guarded patch modes;
- stale-hash refusal with no write;
- LMFE traversal of repository-aware action shapes;
- repository orientation present before the first model action;
- guarded range edits retaining M22 mutation/verification semantics.
