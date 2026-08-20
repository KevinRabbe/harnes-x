# Milestone 19A — Recurrent-depth core experiment

Milestone 19A tests one research question without making recurrent models an architectural dependency:

> Can effective reasoning depth become an externally allocated test-time resource while the surrounding Harness X authority model remains unchanged?

This milestone is deliberately **fixed-depth first**. Adaptive/core-level halting is not trained yet.

## Authority boundary

```text
bounded Harness X context
        |
        v
RecurrentDepthAuthority
        |
        | authorizes one exact depth
        v
FixedDepthRecurrentCore
        |
        v
RecurrentDepthBackend
        |
        v
RawReasoningOutput
        |
        v
existing Harness X normalization / verification / tool boundaries
```

The recurrent model can use an authorized depth. It cannot increase that depth, grant itself budget, mutate memory, execute tools, alter permissions, or promote a new policy.

`RecurrentDepthAuthorization` binds:

- exact requested depth;
- allowed experiment depths;
- external maximum recurrent steps;
- policy version;
- SHA-256 authorization fingerprint.

Unauthorized or over-envelope depths fail before backend invocation.

## Fixed-depth curve first

The default research grid is:

```text
4
8
16
32
64
128
```

Each case is executed at every measured depth under the same input context. The report records:

- structured-output quality;
- exact-answer accuracy;
- normalized recurrent-step cost;
- quality-minus-cost net value;
- backend failures;
- Pareto-frontier membership;
- dominated depths.

A deeper point is dominated when another measured depth has equal-or-better quality at lower-or-equal cost with at least one strict improvement.

The initial cost is intentionally a normalized recurrence proxy (`depth / max_depth`). It is **not** wall-clock, energy, FLOP, or GPU telemetry yet.

## Reference simulator

CI uses a deterministic recurrent-depth simulator with hidden minimum depths for disjoint train/eval cases.

The simulator exists to validate:

- external depth authorization;
- fixed-depth curve construction;
- saturation/dominance detection;
- train/eval isolation;
- learned-selector mechanics;
- artifact integrity;
- CLI/report behavior.

It is not evidence about the quality of a production recurrent model.

The expected reference behavior is:

```text
easy       -> 4 steps
medium     -> 8 steps
reasoning  -> 16 steps
hard       -> 32 steps
deep       -> 64 steps
128        -> no additional quality over 64, therefore dominated
```

The answer and required depth are held in the simulator backend and are never put into the model-visible benchmark context.

## External depth selectors

Only after the fixed-depth training curve exists does Harness X derive selector labels.

For each training case:

```text
measure every authorized depth
        |
        v
find shallowest depth reaching the quality threshold
        |
        v
DepthSelectionExample
```

The selector sees only pre-decision features:

- task difficulty;
- uncertainty;
- context pressure;
- verifier rejection rate;
- remaining budget ratio.

The deterministic selector remains the permanent baseline.

The first learned selector is a dependency-free nearest-centroid model with an immutable SHA-256-bound artifact. It competes on held-out cases against the deterministic selector using:

- mean quality;
- normalized depth cost;
- quality-minus-cost net value;
- exact minimal-depth accuracy.

A learned selector does not qualify by merely choosing shallower depths. It must preserve quality and improve the configured quality/cost frontier.

## Optional Huginn backend

Harness X includes an optional local Transformers adapter for `tomg-group-umd/huginn-0125`.

The adapter uses the model's recurrent `num_steps` generation interface, while Harness X owns the selected fixed depth externally.

The ordinary install remains free of Torch/Transformers. Install the research runtime explicitly:

```bash
python -m pip install -e ".[recurrent]"
```

The model uses custom Transformers code. Loading it therefore requires an explicit operator trust decision:

```text
--allow-remote-code
```

Without that flag the adapter fails before importing/downloading the model runtime.

CI never downloads Huginn weights and never enables remote model code.

## Operator workflow

Run the fully offline reference experiment:

```bash
harness-x-recurrent-depth \
  --backend reference \
  --depths 4 8 16 32 64 128 \
  --output .harness-x/recurrent-depth-reference
```

A real recurrent-model benchmark requires a JSONL file containing both `train` and `eval` cases. Case IDs must be disjoint across splits.

```bash
python -m pip install -e ".[recurrent]"

harness-x-recurrent-depth \
  --backend huginn \
  --cases path/to/recurrent-depth-cases.jsonl \
  --model tomg-group-umd/huginn-0125 \
  --depths 4 8 16 32 64 128 \
  --allow-remote-code \
  --output .harness-x/recurrent-depth-huginn
```

The command writes:

```text
recurrent-depth-report.json
learned-depth-selector.json
```

The same loaded model instance is reused across measured depths so model-loading overhead is not repeatedly mixed into the recurrence experiment.

## What is deliberately not implemented

Milestone 19A does **not** introduce:

- learned per-token halting;
- model-owned depth selection;
- recurrent depth as a live autonomous controller;
- recurrent-step accounting as a first-class `TaskOrchestrator` budget dimension;
- promotion of a depth selector into the active system;
- a claim that the reference simulator reflects Huginn performance;
- a GPU/model benchmark in CI.

Those require evidence from actual recurrent-model trajectories first.

## Promotion rule

Recurrent depth remains an experimental branch unless a real model benchmark demonstrates both:

1. meaningful quality improvement as fixed depth increases; and
2. a dynamic depth policy that improves the capability/cost frontier on held-out tasks.

A successful simulator run qualifies only the **research machinery**, not the production policy.

## Next research step

After a real fixed-depth curve is collected, the next legitimate step is to compare:

```text
fixed conservative depth
        vs
deterministic external depth selector
        vs
learned external depth selector
```

Only after that comparison is grounded should Harness X consider core-level adaptive halting or recurrent-depth training.