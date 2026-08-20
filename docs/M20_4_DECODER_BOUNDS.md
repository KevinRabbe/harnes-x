# M20.4 — decoder-bounded structured recovery

The first empirical Qwen3-4B pilot showed that the remaining M20.3 STANDARD parse failures were not short JSON syntax mistakes. All three surviving failures were bounded-generation runaways: two diagnostic cases expanded the `evidence` array until the 256-token repair budget ended, and one structural case recursively generated identifiers such as `maintenance_recovered_recovered_...` until truncation.

M20.4 therefore does not add another retry, increase the generation budget, relax strict JSON parsing, alter the held-out targets, or change any promotion threshold.

## Decoder boundary

All Hugging Face empirical generations now carry a lexical top-level JSON completion stop. The detector understands nested objects, quoted braces, escaping, and leading whitespace. Once the first complete top-level `{...}` object closes, generation stops. The decoded result is also deterministically truncated at that same completion boundary before strict `json.loads` validation.

The completion detector is not a parser or repair mechanism. An incomplete or malformed prefix remains incomplete or malformed and still fails strict parsing.

## Repair-only repetition bound

The one M20.3 repair attempt remains optional and still receives only the original grounded prompt plus formatting instructions. It never receives the malformed primary output or held-out target values.

When repair is enabled, M20.4 additionally applies:

- `no_repeat_ngram_size = 8` only to the repair generation;
- a uniform instruction that arrays contain at most eight items;
- an explicit prohibition on duplicate array items;
- an explicit prohibition on recursively appending the same identifier suffix;
- the existing independent repair token budget (default 256).

The array limit and repetition window are fixed policy constants, not values inferred from the expected decision.

## Evidence

Per-case trace schema v3 now stores both attempts explicitly:

- `primary_raw_text` / `primary_parse_error`;
- `repair_attempted` / `repair_succeeded`;
- `repair_raw_text` / `repair_parse_error`;
- final `raw_text` / `parse_error` used by the unchanged evaluator.

The signed observability manifest also records the decoder-bound policy, including repair budget, no-repeat window, array limit, and JSON-completion stopping.

## Physical qualification

Reuse the already-trained pilot adapter; do not retrain:

```powershell
harness-x-empirical-adapter `
  .harness-x/self-model-training-pilot `
  --backend unsloth `
  --base-model-revision cdbee75f17c01a7cc42f958dc650907174af0554 `
  --resume-training .harness-x/empirical-qwen3-4b-pilot-attempt1 `
  --parse-repair-attempts 1 `
  --repair-max-new-tokens 256 `
  --output .harness-x/empirical-qwen3-4b-pilot-decoder-bounded
```

A negative result remains valid evidence. Do not relax the 2% parse-failure threshold to force qualification.
