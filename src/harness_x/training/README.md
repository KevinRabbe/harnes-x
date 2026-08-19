# Grounded self-model training

Milestones 12–13 build the first training path for teaching a replaceable reasoning core how Harness X actually works.

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

## Optional LoRA / QLoRA backend

The normal Harness X install does not require Torch or Hugging Face training libraries. Install the optional backend only on a training machine:

```bash
python -m pip install -e ".[training]"
```

Then run:

```bash
harness-x train-self-model-adapter \
  .harness-x/self-model-training \
  --output .harness-x/self-model-adapter
```

`lora` trains adapters on the normally loaded base model. `qlora` loads the base model in 4-bit and trains LoRA adapters. The base weights remain the permanent comparison baseline.

## Held-out evaluation

```bash
harness-x evaluate-self-model-adapter \
  .harness-x/self-model-training/cohort \
  --base-model <same-base-model> \
  --adapter .harness-x/self-model-adapter/adapter \
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
