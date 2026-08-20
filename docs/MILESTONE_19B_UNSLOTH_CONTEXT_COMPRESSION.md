# Milestone 19B — Unsloth backend and self-model context compression

Milestone 19B tests two related engineering hypotheses:

1. the same grounded Harness X self-model cohort should be trainable through more than one PEFT implementation without changing evaluation semantics; and
2. successful self-model training should eventually reduce how much **stable architecture explanation** must be repeated in every reasoning prompt.

Neither hypothesis transfers authority to a model or training library.

## 1. Backend-neutral training contract

The prepared Milestone 13 training bundle remains the source of truth for:

- exact train/eval cohort fingerprints;
- base model identity;
- LoRA versus QLoRA;
- rank / alpha / dropout;
- target modules;
- learning rate / epochs / batch / accumulation;
- sequence length and seed.

The execution backend is selected only after that bundle exists:

```text
signed PreparedTrainingBundle
          |
     +----+------------------+
     |                       |
     v                       v
HuggingFacePeftTrainer   UnslothPeftTrainer
     |                       |
     +-----------+-----------+
                 v
          PEFT adapter
                 |
                 v
       same Harness X evaluator
```

The backend therefore cannot silently alter the held-out set or promotion criteria.

### Backends

- `huggingface_peft` — the original Transformers + PEFT + TRL implementation.
- `unsloth` — an optional local Unsloth implementation using `FastLanguageModel`, LoRA/QLoRA, SFT, and response-only loss masking.

Unsloth is deliberately installed through a separate optional extra. Its current dependency matrix moves faster than the original Harness X training pins, so selecting Unsloth should not mutate the default HF/PEFT environment.

### Default LoRA targets

The default target set is now:

```text
q_proj
k_proj
v_proj
o_proj
gate_proj
up_proj
down_proj
```

This covers both attention and feed-forward projections. Operators can still supply another validated module tuple through `AdapterTrainingConfig` when a model architecture requires different names.

### Backend evidence

Every adapter artifact records:

- backend;
- base model;
- LoRA/QLoRA method;
- exact cohort fingerprint;
- training example count;
- wall-clock training seconds;
- peak allocated GPU memory when CUDA telemetry exists;
- trainer-reported metrics.

Unsloth saves the **adapter**, not a merged model. This keeps comparison against the untouched base model explicit and leaves rollback straightforward.

## 2. Why context compression matters

Harness X separates:

```text
weights          stable learned priors / skills
external memory  changing knowledge
context          current working state
controllers      resource allocation
software owners  authority
```

A stable fact such as "a gate recommends but does not own state" should not necessarily consume repeated prompt tokens forever if a trained self-model has genuinely learned that operating rule.

However, current/live facts must never be hidden merely to make the prompt smaller.

Milestone 19B therefore compresses **repeatable descriptive context only**.

## 3. Three evaluation profiles

### RICH

The generic-model advantage condition.

It includes:

- the ordinary Harness X self-model instruction;
- live task and state;
- current system version and source-state fingerprint;
- output shape;
- architecture/curriculum family metadata;
- a static architecture reference explaining authority, epistemic, verification, memory, tool, compute, and improvement boundaries.

This is analogous to giving the base model the stable answer key in its prompt.

### STANDARD

The unchanged Milestone 13 training format.

It retains all live state and normal metadata but removes the repeated static architecture reference.

### MINIMAL

The most aggressive initial compression condition.

It still retains:

- task;
- current system version;
- source-state fingerprint;
- complete live input state;
- required output keys;
- an abbreviated authority-safe system instruction.

It removes architecture-family/curriculum-family description and the static architecture reference.

`MINIMAL` is not assumed to be safe. It must earn that status empirically.

## 4. Qualification policy

The benchmark evaluates the **same held-out examples** under:

```text
base + RICH
adapter + RICH
adapter + STANDARD
adapter + MINIMAL
```

A compressed adapter profile is rejected when any configured condition fails, including:

- insufficient context/token reduction;
- excessive exact-accuracy loss versus adapter + RICH;
- structural self-knowledge regression;
- diagnostic regression;
- authority-violation regression;
- parse-failure regression;
- worse accuracy than the generic base model even though the base receives RICH context.

The benchmark reports both raw context size and capability/context efficiency.

For tokenizer-aware predictors:

```text
exact accuracy per 1k prompt tokens
```

is measured from the actual model tokenizer rather than a character estimate.

## 5. GPU-safe evaluation order

A 16 GB machine should not need to hold both base and adapter model instances at once.

The real operator path is therefore:

```text
load base
  -> evaluate RICH
  -> release base / clear cache

load base + adapter
  -> evaluate RICH
  -> evaluate STANDARD
  -> evaluate MINIMAL
  -> release adapter

compare signed reports
```

The adapter is loaded only once for all three profiles.

## 6. Operator workflow

Prepare the cohort once:

```bash
harness-x prepare-self-model-training <curriculum...> \
  --base-model <model> \
  --method qlora \
  --output .harness-x/self-model-training
```

Train using the original backend:

```bash
harness-x train-self-model-adapter \
  .harness-x/self-model-training \
  --backend huggingface_peft \
  --output .harness-x/self-model-adapter-hf
```

Or install/select Unsloth:

```bash
python -m pip install -e ".[unsloth-training]"

harness-x train-self-model-adapter \
  .harness-x/self-model-training \
  --backend unsloth \
  --output .harness-x/self-model-adapter-unsloth
```

Then run the empirical compression test:

```bash
harness-x benchmark-context-compression \
  .harness-x/self-model-training/cohort \
  --base-model <model> \
  --adapter .harness-x/self-model-adapter-unsloth/adapter \
  --output .harness-x/context-compression
```

CI can validate the comparison mechanics without model weights:

```bash
harness-x benchmark-context-compression \
  .harness-x/self-model-training/cohort \
  --reference \
  --output .harness-x/context-compression-reference
```

## 7. Evidence boundary

The deterministic reference predictor exists only to test:

- context-profile construction;
- live-state preservation;
- prompt-size accounting;
- exact held-out-set binding;
- qualification and rejection behavior;
- CLI/report persistence.

It is **not** evidence that a real trained adapter already compresses context successfully.

Likewise, adding Unsloth does not establish that it is faster or more memory-efficient for the Harness X workload. A real comparison must train the same bundle with both backends and compare wall time, peak memory, and the same held-out evaluation.

## 8. Success criterion

The useful result is not merely:

```text
adapter accuracy > base accuracy
```

It is closer to:

```text
adapter + less repeated static context
    >= base + rich architecture context
```

while preserving authority and diagnostic behavior.

The long-term metric is therefore capability per unit of transient context, not prompt size alone.
