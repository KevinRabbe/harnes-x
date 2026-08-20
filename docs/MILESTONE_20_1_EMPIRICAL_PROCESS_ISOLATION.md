# Milestone 20.1 — Empirical process isolation and safe resume

Milestone 20.1 repairs a real native-Windows empirical run failure discovered with
`Qwen/Qwen3-4B-Instruct-2507` and the Unsloth backend.

## Observed failure

The first real pilot successfully completed QLoRA training and saved a PEFT adapter,
but the subsequent base-model evaluation failed with:

```text
AttributeError: 'Qwen3Attention' object has no attribute 'apply_qkv'
```

The training backend had imported Unsloth in the same interpreter that later created
the ordinary Hugging Face evaluation model. Unsloth patches Transformers/Qwen classes
process-wide; a model created later through `AutoModelForCausalLM` could therefore
inherit a patched forward method without the instance attributes installed by
`FastLanguageModel`.

This is a backend-isolation defect, not a CUDA, driver, cohort, or training failure.

## Isolation boundary

Real empirical training now runs in a child Python interpreter:

```text
parent empirical process
  -> spawn `python -m harness_x.training.empirical_worker`
       -> import selected training backend
       -> train adapter
       -> write adapter + adapter-artifact.json
       -> exit (all backend monkey patches and GPU objects die)
  -> validate exact training artifact identity
  -> copy/rebind PEFT adapter into final evidence tree
  -> load clean Hugging Face base evaluator
  -> load clean Hugging Face + PEFT adapter evaluator
  -> produce the normal signed M20 report
```

The evaluator remains the unchanged M20 evaluator. Training is handed back through a
small validated trainer shim so the existing evidence/report logic does not need to
know about backend-specific process mechanics.

## Artifact validation

A staged or resumed training artifact is accepted only when all of these match the
effective prepared bundle exactly:

- base model;
- base-model revision;
- tokenizer revision;
- adapter method;
- backend;
- training-example count;
- cohort fingerprint.

The adapter directory must also exist and contain files. The source artifact and
adapter tree receive SHA-256 fingerprints which are recorded in the final training
artifact under `train_result.empirical_training_boundary`.

The recorded `adapter_path` inside an old artifact is not treated as authority. This
is intentional: an expensive partial experiment may be moved after an evaluation
failure. The adapter is rebound only to the validated directory located beside the
artifact.

## Resume an already-trained adapter

If training completed but evaluation failed, preserve that run and resume into a new
empty output directory:

```powershell
harness-x-empirical-adapter `
  .harness-x/self-model-training-pilot `
  --backend unsloth `
  --base-model-revision <40-character-commit-sha> `
  --resume-training .harness-x/empirical-qwen3-4b-pilot-attempt1 `
  --output .harness-x/empirical-qwen3-4b-pilot
```

`--resume-training` accepts either the experiment root containing `training/` or the
`training/` directory itself.

Reference mode cannot use `--resume-training` because no real model training exists
in a reference experiment.

## Failure preservation

Fresh real training uses a sibling staging directory. If training itself fails or the
artifact cannot be validated, that staging directory is left in place for inspection.
Once the validated adapter has been copied into the final experiment tree, the
staging directory is redundant and is removed. Therefore a later evaluation failure
does not discard a successful expensive training result.

## Scope

Milestone 20.1 does not change:

- curriculum labels;
- cohort selection;
- QLoRA/LoRA hyperparameters;
- held-out evaluation policy;
- context-compression qualification;
- promotion authority;
- the requirement for exact remote-model revisions.

It changes only the runtime boundary between training and evaluation and adds strict,
move-safe reuse of completed training evidence.
