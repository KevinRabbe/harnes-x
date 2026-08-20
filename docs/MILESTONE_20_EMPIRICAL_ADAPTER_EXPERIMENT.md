# Milestone 20 — empirical self-model adapter experiment

Milestone 20 turns the existing curriculum, QLoRA, held-out evaluation, and context-compression components into one auditable local experiment.

The milestone does **not** claim that a real adapter has already won. GitHub Actions executes the deterministic reference path only. A real result exists only after an operator runs the empirical command on local model weights.

## Question

The experiment asks:

> Can one small model learn stable Harness X operating knowledge well enough to beat its untouched base model on held-out Harness X cases, while requiring less repeated architecture context, at an acceptable training/runtime cost?

The evidence chain is:

```text
signed prepared cohort
        |
        v
exact model identity
        |
        v
hardware/software snapshot
        |
        v
LoRA / QLoRA training
        |
        v
hashed PEFT adapter artifact
        |
   +----+-------------------+
   |                        |
   v                        v
base STANDARD         adapter STANDARD
   |                        |
   +-----------+------------+
               v
      held-out comparison
               |
               +----------------------+
               |                      |
               v                      v
        adapter RICH           adapter STANDARD/MINIMAL
               |                      |
               +----------+-----------+
                          v
                context-compression report
                          |
                          v
                 signed experiment manifest
```

## Exact model identity

A remote model is accepted as exact empirical evidence only when `base_model_revision` is a 40-character commit SHA. The tokenizer revision must also resolve to an exact commit SHA; when omitted it inherits the model revision.

This is stricter than recording a branch or tag name in metadata. The revision is passed into the actual model/tokenizer loaders.

For a local model directory, Harness X hashes every regular file outside `.git` and records a tree fingerprint. Modifying a local weight/config/tokenizer file therefore changes model identity.

## Backend neutrality

The same `PreparedTrainingBundle` can be sent to:

- `huggingface_peft`
- `unsloth`

Both backends consume the same cohort fingerprint, base model identity, LoRA/QLoRA method, target modules, training count, and hyperparameters. Both return a PEFT adapter so downstream evaluation remains identical.

## Environment evidence

The experiment manifest records, when available:

- Python version;
- operating system/platform and machine architecture;
- Harness X version;
- Torch, Transformers, Datasets, Accelerate, PEFT, TRL, bitsandbytes, Unsloth, and Unsloth Zoo versions;
- CUDA availability/runtime version;
- GPU name;
- total GPU memory;
- compute capability.

Environment variables and secrets are deliberately not dumped.

The training artifact separately records training wall time and peak allocated CUDA memory when the selected backend can measure them.

## Artifact integrity

Before training/evaluation, every file in the effective prepared directory is SHA-256 hashed. After training, every adapter file is SHA-256 hashed.

The final experiment report is itself SHA-256 fingerprinted over its complete content except the fingerprint field.

Tampering with the persisted manifest causes validation failure.

## Evaluation

The run evaluates:

```text
base + STANDARD
adapter + STANDARD
```

on the exact same held-out cohort for the Milestone 13 self-model qualification policy.

The same run then evaluates:

```text
base + RICH
adapter + RICH
adapter + STANDARD
adapter + MINIMAL
```

for Milestone 19B context compression.

Base weights are released before the adapter-backed model is loaded. The adapter model is loaded once and reused across its three context profiles.

## Valid experiment != winning adapter

A completed experiment can validly conclude:

- adapter failed self-model qualification;
- adapter passed but context compression failed;
- both passed but general regression was not evaluated;
- all relevant evidence passed.

The CLI exits successfully when the experiment evidence is valid, even if the adapter loses. Negative evidence must not be converted into reruns until a preferred result appears.

## Promotion readiness

`promotion_ready` requires all of:

```text
experiment integrity valid
+ real empirical evidence (not reference simulator)
+ held-out self-model adapter qualification
+ context-compression qualification
+ supplied general-regression evidence within policy
```

Even then, the report is evidence only. It does not load the adapter into the live Harness X reasoning path and does not bypass the separate improvement/promotion authority.

## Operator path

Install one training backend. For Unsloth:

```bash
python -m pip install -e ".[unsloth-training]"
```

Then run:

```bash
harness-x-empirical-adapter \
  .harness-x/self-model-training \
  --backend unsloth \
  --base-model-revision <40-char-model-commit-sha> \
  --output .harness-x/empirical-self-model
```

Or use the original HF/PEFT environment:

```bash
python -m pip install -e ".[training]"

harness-x-empirical-adapter \
  .harness-x/self-model-training \
  --backend huggingface_peft \
  --base-model-revision <40-char-model-commit-sha> \
  --output .harness-x/empirical-self-model
```

If an existing prepared bundle predates the revision fields, the CLI creates a temporary effective copy containing the revision override. The original prepared directory is not modified.

## Reference mode

CI exercises the complete orchestration without model weights:

```bash
harness-x-empirical-adapter \
  .harness-x/self-model-training \
  --reference \
  --output .harness-x/empirical-reference
```

Reference mode still writes input hashes, a reference adapter artifact, held-out reports, context-compression evidence, environment metadata, and a signed experiment manifest. It is permanently marked `reference_simulator` and can never become promotion-ready.

## Next evidence step

After this milestone is qualified, the next meaningful work is an actual local run using one deliberately modest base model and a representative grounded Harness X cohort. The result should be kept even if it is negative.
