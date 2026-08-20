# Grounded self-model training

Milestones 12–13 build the first training path for teaching a replaceable reasoning core how Harness X actually works. Milestone 19B adds interchangeable PEFT implementations and a held-out test for whether stable self-model knowledge can reduce repeated context. Milestone 20 packages those components into one signed local empirical experiment.

## Ground-truth curriculum

Milestone 12 generates self-model records from Harness X ground truth. Labels may come only from:

- deterministic system rules and active configuration;
- known fault injections;
- known interventions with deterministic before/after relations.

Teacher-model answers are not used as labels. Each record keeps its scenario seed, architecture family, source system/state fingerprint, expected structured decision, accepted alternatives, rationale metadata, and label source.

The curriculum files are:

- `train.jsonl`
- `eval.jsonl`
- `manifest.json`

Evaluation seed IDs are separate from training IDs and complete diagnostic fault families are held out rather than randomly splitting rows.

## Milestone 13 cohort

Several grounded curricula can be combined into one `TrainingCohort`. An architecture family can be held out completely, which means every example from that configuration is evaluation-only even when its original Milestone 12 record belonged to the training split. The source record is not rewritten; its original content fingerprint remains valid.

A persisted cohort contains:

- `cohort-manifest.json`
- `train-examples.jsonl`
- `eval-examples.jsonl`

The manifest records the exact train/eval architecture families and a cohort fingerprint.

## Preparing training

```bash
harness-x prepare-self-model-training \
  path/to/curriculum-a \
  path/to/curriculum-b \
  --holdout-architecture architecture_<id> \
  --base-model <model-id-or-local-path> \
  --method qlora \
  --max-train-examples 1000 \
  --output .harness-x/self-model-training
```

The first stage is deliberately capped at roughly 1k high-quality examples. If more records exist, selection is deterministic and round-robins across architecture/family buckets instead of taking one large family first.

The prepared directory contains a signed cohort plus:

- `training-plan.json`
- `train-sft.jsonl`
- `eval-sft.jsonl`

The SFT format is conversational prompt/completion. The prompt contains grounded system state and the completion contains only the structured target decision.

The default LoRA target modules now cover both attention and feed-forward projections:

```text
q_proj k_proj v_proj o_proj gate_proj up_proj down_proj
```

A model with different module names can override the tuple through `AdapterTrainingConfig`; the trainer never silently substitutes another target set.

## Interchangeable LoRA / QLoRA backends

The prepared bundle is backend-neutral. The same exact cohort and hyperparameter contract can be passed to either implementation.

### Hugging Face / PEFT

```bash
python -m pip install -e ".[training]"

harness-x train-self-model-adapter \
  .harness-x/self-model-training \
  --backend huggingface_peft \
  --output .harness-x/self-model-adapter-hf
```

### Unsloth

Unsloth is deliberately isolated in its own optional extra because its Transformers/TRL compatibility window moves independently from Harness X's original pinned training environment:

```bash
python -m pip install -e ".[unsloth-training]"

harness-x train-self-model-adapter \
  .harness-x/self-model-training \
  --backend unsloth \
  --output .harness-x/self-model-adapter-unsloth
```

The Unsloth backend uses the same LoRA/QLoRA settings, applies response-only training loss, and saves a PEFT adapter rather than a merged model. The existing Harness X evaluator therefore remains the comparison authority.

Both training artifacts record backend identity, cohort fingerprint, wall-clock training time, peak allocated CUDA memory when available, and trainer metrics. These measurements make an eventual HF-vs-Unsloth comparison empirical rather than based on vendor benchmark claims.

`lora` trains adapters on the normally loaded base model. `qlora` loads the base model in 4-bit and trains LoRA adapters. The base weights remain the permanent comparison baseline.

## Held-out evaluation

```bash
harness-x evaluate-self-model-adapter \
  .harness-x/self-model-training/cohort \
  --base-model <same-base-model> \
  --adapter .harness-x/self-model-adapter-unsloth/adapter \
  --output .harness-x/self-model-evaluation
```

The base model is evaluated first and released before the adapter-backed model is loaded. Both reports are bound to the SHA-256 fingerprint of the exact evaluation examples, and cross-dataset comparison is refused.

Metrics include:

- exact structured-decision accuracy;
- field-level accuracy;
- diagnostic component accuracy;
- safe-next-experiment accuracy;
- uncertainty-label accuracy;
- authority-violation rate;
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
