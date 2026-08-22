# Milestone 32 — Personal Model Profiles

M32 adds a deliberately small, explicit model-selection layer for a single-user Harness X setup.
It is **not** a model marketplace, automatic router, voting system, or provider fleet.

The purpose is to make a few personally useful reasoning cores easy to select while preserving the
same software-owned Harness X control boundary.

> A stronger model receives more reasoning capability, not more authority.

Model output is still only a proposal. Harness X still owns tool permissions, task state, memory,
verification, isolation, procedure reliability, revision promotion, and improvement-campaign
control.

## Scope

The built-in shortlist contains four explicit profiles:

| Profile | Intended role | Default model | Default transport |
| --- | --- | --- | --- |
| `main` | primary local coding + reasoning + agentic work | `Qwen/Qwen3.8-27B` | loopback OpenAI-compatible server |
| `coder` | local agentic-coding specialist | `Qwen/Qwen3-Coder-30B-A3B-Instruct` | loopback OpenAI-compatible server |
| `reasoning` | independent local reasoning/second opinion | `deepseek-ai/DeepSeek-R1-Distill-Qwen-14B` | loopback OpenAI-compatible server |
| `api` | optional remote high-end model | `gpt-5.6-sol` | OpenAI API |

The existing direct Qwen3-4B Transformers path remains the no-profile development/stress-test
default. It is intentionally not counted as another production candidate.

The shortlist is an operator convenience, not a claim that these models are permanently optimal.
`--model-profile-file` can replace any profile definition without changing Harness X runtime code.

## Why these current examples

The examples were re-verified against primary model/vendor documentation on 2026-08-22.

### `main` — Qwen3.8-27B

Official Qwen material describes Qwen3.8-27B as a 27B dense model targeting coding,
professional work, research, and long-horizon agentic tasks. It supports configurable
`reasoning_effort`, has stronger environment-feedback handling, and is natively multimodal.

Primary source:

- https://huggingface.co/Qwen/Qwen3.8-27B

The current Harness X in-process Transformers adapter uses `AutoModelForCausalLM` and is
text-only. Qwen3.8 uses a multimodal conditional-generation architecture, so M32 does **not**
pretend the old loader supports it. The profile instead points at an operator-owned loopback
vLLM/SGLang/OpenAI-compatible server.

Qwen recommends thinking-mode sampling around `temperature=1.0`, `top_p=0.95` and supports
`reasoning_effort` values including `xhigh`, `medium`, and `low`; the built-in profile therefore
starts at `xhigh` and can be explicitly overridden.

### `coder` — Qwen3-Coder-30B-A3B-Instruct

Qwen documents this model as a 30.5B-total / 3.3B-active MoE focused on agentic coding,
browser use, tool calling, and repository-scale context. It is a non-thinking model.

Primary source:

- https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct

The built-in profile uses Qwen's recommended `temperature=0.7`, `top_p=0.8` starting point. A
direct in-process Transformers launch remains possible through the legacy flags when the local
hardware/runtime supports it, but the profile uses the same loopback HTTP serving pattern as the
other heavyweight candidates.

### `reasoning` — DeepSeek-R1-Distill-Qwen-14B

This slot is intentionally different from the coding-specialist slot. It exists so the operator can
ask for an independent reasoning path on difficult architecture, diagnosis, algorithms, or
conflicting hypotheses. M32 does not automatically vote between model outputs.

DeepSeek recommends temperature around 0.6 and recommends avoiding a system-role prompt for the
R1 series. The profile therefore uses `temperature=0.6`, `top_p=0.95`, and `prompt_mode=user_prefix`.
Harness X's control instructions are prepended to the user message instead of being removed.

Primary source:

- https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-14B

The selected R1-distill model is an intentionally practical local comparison model. The much larger
DeepSeek V4 family is not hard-coded into this local profile. An operator can use DeepSeek's API or
a different local DeepSeek checkpoint through a custom profile/direct compatible endpoint when
desired.

### `api` — GPT-5.6 Sol

The API profile is optional and never selected implicitly. OpenAI currently documents
`gpt-5.6-sol` as its flagship model for complex reasoning and coding with configurable reasoning
effort and Chat Completions support.

Primary source:

- https://developers.openai.com/api/docs/models/gpt-5.6-sol

Selecting this profile requires `OPENAI_API_KEY`. Harness X checks that the configured environment
variable exists before constructing the remote core. The secret value is never written into model
selection artifacts.

The API profile can later be replaced with another provider/model in the personal profile JSON;
the architectural boundary is OpenAI-compatible HTTP, not an OpenAI-specific authority path.

## Explicit selection

### Development/stress-test model — unchanged

Omitting `--model-profile` preserves the existing direct Qwen3-4B Transformers behavior:

```powershell
harness-x-code D:\repo `
  --task "Repair the issue" `
  --verify "python -m pytest"
```

### Main local model

Serve the selected heavyweight model through a loopback OpenAI-compatible server. For example, a
vLLM/SGLang installation can serve the profile model on port 8000. Server/quantization choices are
operator-owned and intentionally outside Harness X.

Then:

```powershell
harness-x-code D:\repo `
  --model-profile main `
  --task "Repair the issue" `
  --verify "python -m pytest"
```

If the local server is on another port:

```powershell
harness-x-code D:\repo `
  --model-profile main `
  --base-url http://127.0.0.1:9123/v1 `
  --task "Repair the issue" `
  --verify "python -m pytest"
```

### Coding specialist

```powershell
harness-x-code D:\repo `
  --model-profile coder `
  --task "Implement the repository change" `
  --verify "python -m pytest"
```

### DeepSeek reasoning comparison

There is no voting system. The operator simply selects the reasoning model for another run or uses
it for a separately requested analysis:

```powershell
harness-x-code D:\repo `
  --model-profile reasoning `
  --task "Independently diagnose the root cause and implement the verified repair" `
  --verify "python -m pytest"
```

Results can then be compared manually or through an ordinary comparison prompt. Software outcome
claims still come from Harness X verification, not model agreement.

### Optional API

PowerShell example:

```powershell
$env:OPENAI_API_KEY = "..."

harness-x-code D:\repo `
  --model-profile api `
  --task "Solve the difficult repository task" `
  --verify "python -m pytest"
```

Without `OPENAI_API_KEY`, selection fails closed before a remote reasoning core is created.

## Improvement campaigns use the same profiles

M31 remains explicitly operator-triggered. The same model profile can now be used for candidate
proposal/validation runs:

```powershell
harness-x-improve-procedure D:\repo `
  --model-profile main `
  --parent-procedure-id pmem_... `
  --task "Repair the regression this procedure should handle" `
  --verification-plan D:\plans\verification.json
```

Choosing a stronger model does not change M31's proposal/trial budgets or M30 promotion rules.

## Personal overrides

The repository contains:

```text
configs/model_profiles.personal.example.json
```

Copy it to a private/local path and edit only the details that matter for the machine, such as:

- model checkpoint;
- loopback port;
- output-token cap;
- reasoning effort;
- sampling values;
- optional API provider endpoint and environment-variable name.

Use it with:

```powershell
harness-x-code D:\repo `
  --model-profile main `
  --model-profile-file D:\harness_x\my-model-profiles.json `
  --task "Repair the issue" `
  --verify "python -m pytest"
```

A custom profile with the same ID replaces the corresponding built-in definition. Harness X does
not grow the built-in catalog automatically.

## Provenance

Both coding and procedure-improvement CLIs write:

```text
model-selection.json
```

before task execution. It records the resolved, secret-free selection:

- profile ID and role, if a profile was used;
- exact model string;
- backend/transport choice;
- revision when relevant;
- endpoint URL;
- API-key **environment variable name**, never its value;
- remote-endpoint permission;
- output-token cap;
- sampling values;
- reasoning effort;
- prompt mode;
- local Transformers loader flags.

This makes later Harness X model comparisons reproducible without adding a voting/evaluation
subsystem to ordinary execution.

## Deliberate non-features

M32 does not implement:

- automatic model routing;
- automatic escalation from local to API;
- model voting or consensus;
- parallel multi-agent comparison;
- automatic downloading of heavyweight models;
- automatic vLLM/SGLang process management;
- GPU/VRAM autodetection;
- automatic quantization selection;
- API-provider account management;
- API-key persistence;
- privileged tool access for stronger models.

If later real usage shows that automatic routing saves meaningful time or compute, it can be added
from evidence. It is not required to make the personal workflow useful.

## Security / authority

Local profiles default to loopback endpoints. The existing OpenAI-compatible adapter rejects remote
hosts unless remote use was explicitly authorized.

The `api` profile is the only built-in profile that enables a remote endpoint. It is selected only
by the operator and requires an environment-variable key.

Regardless of profile:

```text
model
  -> RawReasoningOutput
  -> Harness X validation/gates
  -> permitted tool action
  -> software verification
```

Model selection never creates a second state-mutation path.

## Qualification boundary

M32 is qualified only when an exact branch head passes the complete repository suite and CLI/config
smoke checks with deterministic coverage for:

- exactly four built-in personal profiles;
- local profile resolution without network access;
- custom profile override;
- Qwen reasoning-effort/sampling request shaping;
- DeepSeek user-prefix request shaping;
- API key fail-closed behavior before remote use;
- secret-free model-selection artifact;
- unchanged no-profile Qwen3-4B development behavior;
- coding and M31 campaign CLI profile parsing;
- installed CLI help surfaces profile selection;
- no live model download, GPU inference, or API request in CI.

The exact qualified SHA and CI run are recorded in PR #39 at freeze.
