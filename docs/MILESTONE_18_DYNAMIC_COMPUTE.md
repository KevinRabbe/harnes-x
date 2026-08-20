# Milestone 18 — Dynamic compute allocation

Milestone 18 introduces the first learned peripheral-controller experiment in Harness X.

The milestone does **not** replace the deterministic compute gate and does **not** give a learned model authority over task budgets. It creates a learned recommendation layer, a permanent deterministic baseline, a capability/cost benchmark, and an explicit authority handoff back into the existing software-owned `ComputeGate`.

## Boundary

```text
bounded pre-decision state
          |
          +----------------------+
          |                      |
          v                      v
deterministic baseline      learned controller
          |                      |
          |                recommendation only
          |                      |
          +----------+-----------+
                     |
                     v
             comparison / A-B test
                     |
                     v
          existing deterministic ComputeGate
                     |
              +------+------+
              |             |
            allow        stop/suspend
              |             |
              v             v
       owning runtime    learned request
       may consume       cannot proceed
       external budget
```

The learned controller cannot:

- increment budget usage;
- change the orchestrator mode;
- choose or load a real model directly;
- execute retrieval;
- execute verification;
- spawn candidate work;
- mutate memory;
- promote itself into the live system.

`ComputeAuthorityAdjudicator` converts a recommendation into a conservative `BudgetDelta` and asks the already-qualified deterministic `ComputeGate` whether that allocation fits the hard external budget. `PARALLEL_CANDIDATES` requests two reasoning steps; every other non-stop action requests at least one reasoning step in the initial policy. A learned recommendation therefore cannot create compute capacity that the authoritative budget does not contain.

## Action vocabulary

The interface supports all compute actions planned in `IMPLEMENTATION_PLAN.md`:

```text
stop
another_reasoning_call
larger_context
stronger_model
extra_retrieval
extra_verification
parallel_candidate_generation
```

Supporting an action in the contract does not mean a trace-derived model is automatically allowed to learn it.

Milestone 17 traces currently provide grounded conversion rules for:

```text
stop
another_reasoning_call
extra_retrieval
extra_verification
```

`larger_context`, `stronger_model`, and `parallel_candidate_generation` require explicit future evidence from a runtime/simulator that actually exercises those choices. The trace preparation path refuses to invent those labels.

## Learned controller

The first controller is intentionally small and dependency-free: a nearest-centroid model over a bounded feature schema.

Features include:

- task difficulty;
- uncertainty;
- current progress;
- retrieval usefulness;
- verifier rejection rate;
- context pressure;
- candidate disagreement;
- remaining reasoning/tool ratios;
- requested reasoning ratio;
- current model tier;
- recent reasoning/retrieval/verification counts;
- explicit stop/completion flags.

Training computes deterministic feature ranges plus one centroid/profile per observed action. Each profile records the mean observed value and mean incremental cost of its grounded examples. The serialized artifact contains:

```text
feature schema
training example count
training-data SHA-256
feature scaler
per-action centroids
per-action value/cost statistics
artifact SHA-256
```

The artifact is immutable under its Pydantic contract and is revalidated when loaded.

The nearest-centroid implementation is not intended to be the final learned gate. It proves the replaceable controller boundary without adding NumPy, PyTorch, scikit-learn, or a GPU dependency to normal Harness X operation. A neural controller can later implement the same `DynamicComputeController` protocol.

## Milestone 17 conversion

`prepare_dynamic_compute_examples()` reads only `gate_id == "compute"` records from a verified Milestone 17 dataset.

It omits records when:

- later usefulness is `UNKNOWN`;
- no downstream compute action can be grounded from the observed trajectory;
- the implied action is outside the set that current traces can actually support.

It never uses future outcomes as pre-decision features. Outcome/usefulness evidence is used only to construct the target and observed value/cost fields.

A trace-derived example therefore has the shape:

```text
pre-decision compute state
        |
        +--> grounded observed action
        +--> evidence-backed usefulness value
        +--> measured downstream cost proxy
        +--> causal evidence refs
```

This remains observational training data. An observed successful action is not automatically claimed to be globally optimal.

## Reference simulator

CI also needs examples for all seven planned actions, including actions not yet present in ordinary gate traces. Milestone 18 therefore includes a small deterministic reference simulator fixture.

The reference fixture is **not production training data** and is never mixed silently with a Milestone 17 dataset. Its labels explicitly use `deterministic_reference_simulator` provenance.

It exists to qualify:

- the complete action contract;
- model serialization;
- learned-vs-deterministic comparison;
- capability/cost scoring;
- calibration scoring;
- pathological-policy detection;
- the operator CLI.

## Capability/cost frontier

Controllers are evaluated on realized trajectory utility and realized cost, not on the controller's own claims.

Metrics include:

```text
mean realized utility
mean realized cost
mean utility - cost_weight * cost
exact action accuracy
predicted-value calibration / Brier error
premature stops
unnecessary extra compute
strongest-model calls
retrieval calls
action distribution
```

The default frontier policy requires:

- no realized utility regression;
- a minimum net capability/cost gain;
- no increase in premature stopping;
- no unacceptable calibration regression.

A learned controller is therefore not considered better simply because it asks for more compute and raises raw task utility.

On the deterministic reference fixture, the conservative baseline handles stop/reason/retrieve/verify correctly but intentionally does not adopt the three new expensive actions. The learned reference controller separates those cases and improves the simulated capability/cost frontier. This proves the benchmark/controller mechanism only; real promotion later requires real held-out task evidence.

## Failure-mode coverage

Milestone 18 explicitly tests controllers that:

1. always stop;
2. always request maximum/parallel compute;
3. always route to the strongest model;
4. always request retrieval;
5. confidently predict high value even when the chosen action changes the realized trajectory to a worse outcome.

These policies are rejected by the frontier evaluator.

The fifth case is important: the evaluator computes calibration against the **realized utility after the controller's action**, so a controller cannot remain well-calibrated merely by repeating its own pre-action prediction.

## Operator paths

Train the lightweight controller from a real Milestone 17 dataset:

```bash
harness-x train-dynamic-compute-controller \
  .harness-x/gate-training-data \
  --output .harness-x/dynamic-compute-controller.json
```

Run the deterministic reference benchmark:

```bash
harness-x benchmark-dynamic-compute \
  --output .harness-x/benchmark-dynamic-compute
```

Evaluate a previously trained artifact against the same reference cases:

```bash
harness-x benchmark-dynamic-compute \
  --controller .harness-x/dynamic-compute-controller.json \
  --output .harness-x/benchmark-dynamic-compute
```

A real trace-trained controller may legitimately fail the full seven-action reference benchmark if its source traces contain only a subset of action classes. That is evidence about dataset coverage, not a reason to synthesize missing labels.

## What Milestone 18 proves

Harness X can now:

```text
collect deterministic control trajectories
        -> construct grounded controller examples
        -> fit a replaceable small learned controller
        -> compare it against a permanent deterministic baseline
        -> score realized capability/cost and calibration
        -> reject pathological learned policies
        -> route surviving recommendations back through hard software authority
```

It does **not** yet prove that a learned controller trained on real autonomous workloads beats the deterministic baseline in production. The reference fixture proves the machinery and safety boundary; real promotion still requires held-out empirical evidence and the existing sandbox/promotion process.

## Next research boundary

The implementation plan moves next to the advanced recurrent-depth branch. Milestone 19A should integrate a recurrent/looped reasoning core behind the existing `ReasoningCore` interface, expose fixed depth first, measure depth vs quality/cost, and only then let Harness X compare deterministic versus learned depth selection.
