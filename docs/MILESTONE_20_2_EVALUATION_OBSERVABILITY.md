# Milestone 20.2 — Empirical Evaluation Observability

Milestone 20.2 makes the first real self-model adapter result inspectable without
weakening any evaluation or promotion threshold.

The first Qwen3-4B pilot showed a useful adapter improvement but failed promotion
because 7 of 132 STANDARD predictions could not be parsed as valid structured JSON.
The aggregate report correctly rejected the adapter, but it did not preserve the raw
held-out outputs needed to diagnose those seven cases. The same pilot also exposed a
context-compression policy blind spot: full diagnostic exact-match was zero in both
RICH and MINIMAL, hiding a large drop in the dedicated diagnostic-component metric.

## Per-case prediction ledger

`harness-x-empirical-adapter` now records every held-out prediction boundary while the
existing M20/M20.1 experiment runs. It does not record hidden model reasoning.

Each JSONL row contains:

- evaluation name and context profile;
- predictor name;
- scenario ID and signed scenario fingerprint;
- architecture, curriculum and fault families;
- grounded expected decision and accepted alternatives;
- raw generated model text;
- parsed decision and parse error;
- confidence when supplied;
- exact-match result;
- field-match numerator/denominator;
- authority-boundary violation status.

The traces are written append-only to a sibling `*.evaluation-staging` directory as
each prediction completes. If evaluation fails later, that staging directory remains
available for diagnosis. On a successful experiment it is moved into:

```text
<experiment>/evaluation-traces/
```

The final trace files are SHA-256 indexed by a separately signed:

```text
<experiment>/evaluation-observability.json
```

That manifest is cryptographically tied to the original M20
`experiment-manifest.json` through its `report_fingerprint` and held-out evaluation
fingerprint. M20's original evidence schema and promotion decision remain unchanged.

## Diagnostic compression protection

Context-compression qualification still permits at most a 0.02 diagnostic regression,
but the protected quantity is now the dedicated:

```text
diagnostic_component_accuracy
```

rather than the diagnostic family's full exact-match accuracy.

This matters when both profiles have zero full diagnostic exact matches. A profile
that falls from, for example, 0.80 to 0.10 component identification accuracy is now
rejected even if both full-exact values remain 0.0.

No threshold was relaxed.

## Physical rerun

M20.2 is designed to reuse an already-trained M20.1 adapter:

```powershell
harness-x-empirical-adapter `
  .harness-x/self-model-training-pilot `
  --backend unsloth `
  --base-model-revision cdbee75f17c01a7cc42f958dc650907174af0554 `
  --resume-training .harness-x/empirical-qwen3-4b-pilot-attempt1 `
  --output .harness-x/empirical-qwen3-4b-pilot-observed
```

No QLoRA retraining is performed. The existing adapter is validated through M20.1,
then the same held-out evaluations are repeated with append-only prediction traces.

## Non-goals

Milestone 20.2 does not:

- change curriculum examples;
- change adapter weights or training hyperparameters;
- relax parse, authority, diagnostic, structural or accuracy thresholds;
- infer hidden chain-of-thought;
- promote or install an adapter;
- replace the original M20 signed experiment report.
