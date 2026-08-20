# Milestone 17 — Grounded Gate Training Data

Milestone 17 begins the learned-control phase without replacing any deterministic gate.

The purpose of this milestone is to turn existing causal traces into controller-training records that preserve three distinct facts:

```text
what the gate saw and chose
        |
        v
what actually happened afterward
        |
        v
what later usefulness can be supported by evidence
```

These layers are intentionally separate. A deterministic decision is always available as a supervised baseline label, while later usefulness remains `unknown` when the trace does not contain enough direct evidence to make a causal claim.

## Record contract

Each `GateTrainingRecord` contains:

```text
source whole-system version
source trace fingerprint
trace / task / decision event identity
decision step
gate id
policy version
input-state fingerprint
state features
deterministic policy decision
policy confidence
immediate gate cost
optional model/shadow recommendation
bounded downstream outcome summary
later-usefulness label
record fingerprint
```

The collector reads the `input_state`, `decision`, `policy_version`, `confidence`, `cost`, and canonical input fingerprint already written by the deterministic gate layer. It does not reconstruct those values from prose or ask a model what the gate probably saw.

## Optional model recommendations

Model recommendations are optional annotations keyed to the exact `GATE_DECISION` event ID:

```json
{
  "schema_version": "gate-model-recommendation-v1",
  "decision_event_id": {"value": "event_..."},
  "gate_id": "retrieval",
  "source": "shadow-model",
  "model_version": "local-model-v1",
  "recommendation": {"retrieve": true},
  "confidence": 0.73
}
```

Milestone 17 deliberately does **not** infer a gate recommendation from unrelated model-assisted routine output. If a future learned/shadow gate supplies a recommendation, it must bind itself explicitly to the gate event being compared.

## Outcome windows

For each gate event, the collector observes a bounded downstream window. The default horizon is 32 trace steps and the interval closes before the next decision from the same gate when one appears first.

The outcome summary records measurable trajectory facts such as:

- mode changes;
- reasoning completions;
- tool actions;
- verifier accept/reject counts;
- recorded errors;
- memory writes and evictions;
- retrieval attempts and non-empty retrievals;
- routine successes/failures;
- the next same-gate input state when it is available;
- causal event IDs supporting those measurements.

This is not a hidden reward model. It is a compact projection of the causal trace.

## Usefulness labels

Usefulness labels are intentionally conservative:

```text
positive
negative
unknown
```

`unknown` is a first-class state, not missing-data cleanup.

Examples in `gate-usefulness-v1`:

- retrieval requested and returned relevant/non-empty evidence -> positive;
- retrieval requested and returned zero results -> negative;
- retrieval not requested -> unknown, because the counterfactual result is not observed;
- a written memory later retrieved -> positive;
- a write occurred but no later reuse was observed -> unknown;
- focused state referenced downstream -> positive;
- compute allowed and produced reasoning/action/verified progress -> positive;
- maintenance triggered and produced an observed maintenance transition plus pressure relief/eviction -> positive;
- insufficient evidence for any of the above -> unknown.

The dataset therefore supports two different learning modes later:

1. **supervised imitation / distillation** from deterministic gate policy decisions;
2. **outcome-aware ranking/offline learning** only on records whose later evidence is meaningful.

A later controller must not treat every `unknown` record as reward `0`.

## Integrity

Every source trace receives a SHA-256 fingerprint over its exact ordered events.

Every training record receives a separate SHA-256 fingerprint over its complete content except the fingerprint field itself.

The dataset manifest binds:

- collector version;
- usefulness-label policy version;
- outcome horizon;
- exact source trace descriptors;
- ordered record fingerprints;
- record counts by gate;
- usefulness counts;
- model-recommendation count.

Loading the dataset revalidates record hashes, aggregate counts, and the whole-dataset fingerprint.

Duplicate copies of the same trace are rejected instead of silently overweighting that trajectory. Orphan model recommendations are also rejected.

## Side-effect boundary

Collection is observationally pure:

```text
verified trace ledger
      |
      v
GateTrainingDataCollector
      |
      +--> records.jsonl
      +--> manifest.json

source trace bytes remain unchanged
```

No memory owner, orchestrator, gate, routine, candidate, model, or active configuration is mutated while data is collected.

## Operator path

```bash
harness-x collect-gate-training-data \
  .harness-x/benchmark-scripted/dependency.jsonl \
  .harness-x/benchmark-scripted/interruption.jsonl \
  --outcome-horizon-steps 32 \
  --output .harness-x/gate-training-data
```

Optional shadow recommendations can be supplied as JSONL:

```bash
harness-x collect-gate-training-data \
  path/to/trace.jsonl \
  --recommendations path/to/gate-recommendations.jsonl \
  --output .harness-x/gate-training-data
```

The output is:

```text
gate-training-data/
  records.jsonl
  manifest.json
```

## What Milestone 17 does not do

Milestone 17 does **not**:

- train a learned gate;
- replace a deterministic gate;
- grant a model gate authority;
- fabricate counterfactual rewards;
- promote a controller;
- alter live policy versions.

The deterministic gates remain the permanent baseline.

## Next boundary

Milestone 18 can now use these records to introduce the first dynamic learned controller experiments. Every learned controller must compete against its deterministic predecessor on the same held-out trajectories and must improve the capability/cost frontier before it can become eligible for sandbox/promotion.
