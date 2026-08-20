# Milestone 20.3 — Bounded Structured-Output Recovery

Milestone 20.3 tests whether Harness X can remove an observed structured-output
reliability bottleneck through explicit external validation and bounded recovery rather
than parser leniency or adapter retraining.

## Empirical trigger

The first observed Qwen3-4B pilot produced 7 strict JSON parse failures in the 132-case
STANDARD adapter evaluation. Inspection of the signed M20.2 traces separated them into
two failure modes:

- four runaway/truncated generations, including repeated evidence or lifecycle-target
  entries until the 512-token generation ceiling;
- three syntactically malformed JSON objects, including missing property names and
  unquoted keys.

Six of the seven failures were diagnostic cases. Four were the held-out
`budget_exhaustion` fault family, two were `tool_failure_loop`, and one was structural.

Increasing `max_new_tokens` is therefore not a repair: it would mostly allow runaway
outputs to repeat for longer. Likewise, accepting malformed JSON through a permissive
parser would weaken the evidence boundary.

## Protocol

M20.3 introduces an optional prediction-boundary protocol:

```text
primary deterministic generation
        |
        v
strict JSON parser
        |
        +-- valid ----------------------> evaluate
        |
        `-- invalid
              |
              v
       one fresh compact retry
              |
              v
       strict JSON parser
              |
              +-- valid ---------------> evaluate recovered output
              `-- invalid -------------> remain a parse failure
```

The retry is bounded to at most one additional generation. It uses the same already
loaded base/adapter model and the same original grounded state. It does **not** receive:

- the held-out expected decision values;
- hidden chain-of-thought;
- an edited or heuristically repaired JSON object;
- the malformed primary output itself.

The retry only strengthens the already-public output contract: one compact JSON object,
requested top-level keys used at most once, finite concise arrays, no repeated list
entries, and termination immediately after the top-level closing brace.

## Evidence

M20.3 extends the M20.2 append-only trace with both boundaries:

- `primary_raw_text`;
- `primary_parse_error`;
- whether repair was attempted;
- whether repair succeeded;
- final `raw_text`;
- final parsed decision / parse error;
- the existing exact, field and authority-boundary results.

The signed `evaluation-observability.json` reports:

- primary parse-failure count;
- recovered parse-failure count;
- final parse-failure count.

This means recovery cannot make the original formatting defect disappear from the
evidence. Promotion still sees the strict final parser result and all existing accuracy,
authority, diagnostic, structural and compression thresholds remain unchanged.

## Operator experiment

Reuse the exact already-trained pilot adapter; no QLoRA retraining is required:

```powershell
harness-x-empirical-adapter `
  .harness-x/self-model-training-pilot `
  --backend unsloth `
  --base-model-revision cdbee75f17c01a7cc42f958dc650907174af0554 `
  --resume-training .harness-x/empirical-qwen3-4b-pilot-attempt1 `
  --parse-repair-attempts 1 `
  --repair-max-new-tokens 256 `
  --output .harness-x/empirical-qwen3-4b-pilot-recovery
```

Compare that run against the preserved M20.2 strict-primary evidence. The useful
questions are:

1. How many of the 28 primary parse failures across all six evaluation traces recover?
2. Does STANDARD fall below the existing 2% final parse-failure threshold?
3. Does recovery change exact/diagnostic/structural quality rather than merely syntax?
4. Are authority violations still zero?
5. Does STANDARD context compression qualify after formatting failures are removed?

## Non-goals

Milestone 20.3 does not:

- change adapter weights or curriculum data;
- increase the primary 512-token budget;
- accept malformed JSON;
- use target values to repair a held-out answer;
- relax promotion or compression thresholds;
- hide the primary failure from the signed trace;
- perform more than one recovery generation.
