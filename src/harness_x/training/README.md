- parse-failure rate;
- optional confidence/Brier calibration;
- per-curriculum-family accuracy.

Adapter promotion is separate from successful training. The default policy requires measurable exact-accuracy improvement, no structural regression, zero authority violations, bounded parse/calibration regression, and optionally a bounded external general-capability regression.

Training loss alone never authorizes the adapter for Harness X use.

## Milestone 19B context-compression benchmark

A fine-tuned self-model is only strategically useful if stable operating knowledge can eventually reduce repeated prompt overhead. The benchmark therefore tests the exact same held-out examples under three context profiles:

- `rich` — normal live state plus a repeated static Harness X architecture reference;
- `standard` — the unchanged Milestone 13 training format;
- `minimal` — shorter instruction/metadata while preserving task, current system version, source-state fingerprint, live input state, and required output shape.

The generic base receives `rich` context. The adapter is measured under all three profiles.

For real models:

```bash
harness-x benchmark-context-compression \
  .harness-x/self-model-training/cohort \
  --base-model <same-base-model> \
  --adapter .harness-x/self-model-adapter-unsloth/adapter \
  --output .harness-x/context-compression
```

The base is evaluated and released first. One adapter-backed model is then reused for rich/standard/minimal evaluation, so the test does not require two model instances in VRAM at once.

Tokenizer-aware evaluation records exact prompt-token counts and reports accuracy per 1k context tokens. A compressed profile is rejected if context savings are obtained by regressing exact accuracy, structural self-knowledge, diagnostics, authority behavior, or parsing reliability.

CI uses an explicit deterministic reference fixture only to qualify the benchmark mechanics:

```bash
harness-x benchmark-context-compression \
  .harness-x/self-model-training/cohort \
  --reference \
  --output .harness-x/context-compression-reference
```

A green reference fixture is not evidence that a real trained adapter has compressed context. That claim requires an empirical model run.

## Milestone 20 one-command empirical experiment

Milestone 20 binds training, evaluation, context compression, hardware/software telemetry, and artifact integrity into one local evidence bundle.

For a remote model, use an exact model commit SHA rather than a branch/tag:

```bash
harness-x-empirical-adapter \
  .harness-x/self-model-training \
  --backend unsloth \
  --base-model-revision <40-char-model-commit-sha> \
  --output .harness-x/empirical-self-model
```

The same command can use `--backend huggingface_peft` in a `[training]` environment.

The experiment records:

- exact cohort fingerprint;
- SHA-256 hashes for every effective prepared input file;
- exact remote model/tokenizer commit identity, or a file-tree fingerprint for a local model directory;
- Python/platform/package versions;
- CUDA/GPU identity and total memory when available;
- training wall time and peak allocated CUDA memory when available;
- SHA-256 hashes for every produced adapter file;
- base-vs-adapter STANDARD held-out evaluation;
- RICH/STANDARD/MINIMAL context-compression evaluation;
- one SHA-256-bound final experiment manifest.

A valid experiment may still report that the adapter lost. CLI success means the evidence chain completed correctly, not that the model passed qualification.

`promotion_ready` is intentionally stricter than successful training. It requires real empirical evidence, held-out adapter qualification, context-compression qualification, and an external general-regression result. The experiment report itself still has no authority to deploy the adapter.

Reference mode exercises the complete orchestration in CI without model weights:

```bash
harness-x-empirical-adapter \
  .harness-x/self-model-training \
  --reference \
  --output .harness-x/empirical-reference
```

Reference reports are permanently marked non-empirical and cannot become promotion-ready.

### M20.5 schema-constrained repair

After strict parsing fails, an empirical run may enable one target-independent schema-constrained retry:

```bash
harness-x-empirical-adapter \
  .harness-x/self-model-training \
  --backend unsloth \
  --base-model-revision <40-char-model-commit-sha> \
  --resume-training <existing-experiment-or-training-dir> \
  --parse-repair-attempts 1 \
  --repair-constraint-mode schema \
  --output .harness-x/empirical-schema-repair
```

Schema mode uses `lm-format-enforcer==0.11.3` only on the single retry. Harness X deliberately bypasses LM Format Enforcer's optional `integrations.transformers` shim and adapts its stable lower-level `TokenEnforcer` API directly, because Transformers 5.x moved tokenizer base classes used by the shim. The schema is derived only from task family/tags, stable Harness X vocabularies, visible protocol structure, and top-level keys already disclosed by the normal prompt; held-out target values and accepted-alternative values are never used to construct it. Strict `json.loads` remains the final parse authority.
