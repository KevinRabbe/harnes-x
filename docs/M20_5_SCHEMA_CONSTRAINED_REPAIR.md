# Milestone 20.5 — Schema-constrained repair decoding

M20.5 is an empirical output-protocol experiment stacked on M20.4. It does not retrain the adapter and does not relax any promotion threshold.

## Why this exists

The physical M20.4 Qwen3-4B pilot preserved the same seven primary STANDARD parse failures as M20.3 and recovered four of them. The three remaining failures were not trailing text after an otherwise valid JSON object. They never reached a valid top-level object:

- two held-out `budget_exhaustion` diagnostic cases expanded malformed `evidence` arrays until the 256-token repair budget ended;
- one structural lifecycle case recursively synthesized invalid `maintenance_recovered_*` identifiers until truncation.

Increasing the token budget or adding another blind retry would extend those failure modes rather than constrain them.

## Policy

The primary evaluation generation is unchanged. Strict `json.loads` remains the final parsing authority.

After a primary strict-parse failure, `--repair-constraint-mode schema` permits the existing single repair generation to use a target-independent JSON Schema token filter. The schema may depend only on:

- the curriculum/task family and tags;
- stable Harness X vocabularies such as `OperatingMode` and `MemoryClass`;
- visible input structure;
- top-level output key names already disclosed by the normal evaluation prompt.

It must not inspect held-out target values, accepted-alternative values, or the malformed primary text.

The model still chooses semantic values. The harness constrains only the output contract.

Examples:

- lifecycle `allowed_targets` may contain only real `OperatingMode` values, but the model still chooses which legal subset to return;
- diagnostic `evidence` is a finite array of evidence objects with stable fields such as `path`, `value`, `minimum`, `relationship`, `repeated_tool_failures`, and `equals_path`; the model still chooses the evidence and diagnosis;
- diagnostic uncertainty is constrained to the protocol vocabulary `low | medium | high`.

Schema mode keeps M20.4 top-level JSON-completion stopping. It does not stack the M20.4 `no_repeat_ngram_size=8` processor on top of schema filtering, avoiding competing token filters. The signed observability report records the effective constraint mode and repetition setting.

## Dependency

Schema-constrained repair uses exactly:

```text
lm-format-enforcer==0.11.3
```

The dependency is included in both the `training` and `unsloth-training` extras. On an already-qualified GPU environment, install only this package rather than re-resolving the whole Unsloth stack.

## Physical pilot

Reuse the preserved Qwen3-4B adapter; do not retrain it:

```powershell
harness-x-empirical-adapter `
  .harness-x/self-model-training-pilot `
  --backend unsloth `
  --base-model-revision cdbee75f17c01a7cc42f958dc650907174af0554 `
  --resume-training .harness-x/empirical-qwen3-4b-pilot-attempt1 `
  --parse-repair-attempts 1 `
  --repair-max-new-tokens 256 `
  --repair-constraint-mode schema `
  --output .harness-x/empirical-qwen3-4b-pilot-schema-repair
```

A successful structured-output recovery is not deployment authority. The normal self-model, context-compression, authority, and general-regression gates remain unchanged.
