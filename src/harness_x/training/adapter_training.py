"""Parameter-efficient self-model adapter training harness.

Heavy ML dependencies are imported only when a selected backend is invoked. Normal
Harness X installation and CI remain model-runtime independent. Prepared training
bundles are backend-neutral so Hugging Face/PEFT and Unsloth can train from the exact
same signed cohort and hyperparameter contract.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .cohort import TrainingCohort, TrainingCohortManifest
from .formatting import (
    FormattedSelfModelRecord,
    format_self_model_example,
    render_messages_with_tokenizer,
)


DEFAULT_LORA_TARGET_MODULES: tuple[str, ...] = (
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
)


class AdapterMethod(StrEnum):
    LORA = "lora"
    QLORA = "qlora"


class TrainingBackend(StrEnum):
    HUGGINGFACE_PEFT = "huggingface_peft"
    UNSLOTH = "unsloth"


class AdapterTrainingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-adapter-training-config-v2"
    base_model: str = Field(min_length=1)
    method: AdapterMethod = AdapterMethod.QLORA
    max_train_examples: int = Field(default=1000, gt=0)
    lora_rank: int = Field(default=16, gt=0)
    lora_alpha: int = Field(default=32, gt=0)
    lora_dropout: float = Field(default=0.05, ge=0.0, lt=1.0)
    target_modules: tuple[str, ...] = DEFAULT_LORA_TARGET_MODULES
    learning_rate: float = Field(default=2e-4, gt=0.0)
    num_train_epochs: float = Field(default=2.0, gt=0.0)
    per_device_train_batch_size: int = Field(default=1, gt=0)
    gradient_accumulation_steps: int = Field(default=16, gt=0)
    max_length: int = Field(default=2048, gt=0)
    warmup_ratio: float = Field(default=0.03, ge=0.0, lt=1.0)
    weight_decay: float = Field(default=0.0, ge=0.0)
    gradient_checkpointing: bool = True
    seed: int = 42
    save_steps: int = Field(default=100, gt=0)
    logging_steps: int = Field(default=10, gt=0)

    @field_validator("base_model")
    @classmethod
    def normalize_base_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("base_model cannot be blank")
        return value

    @field_validator("target_modules")
    @classmethod
    def validate_target_modules(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value if item.strip())
        if not normalized:
            raise ValueError("target_modules cannot be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("target_modules cannot contain duplicates")
        return normalized


class PreparedTrainingBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-training-bundle-v1"
    config: AdapterTrainingConfig
    cohort_manifest: TrainingCohortManifest
    train_records: tuple[FormattedSelfModelRecord, ...]
    eval_records: tuple[FormattedSelfModelRecord, ...]

    def write(self, output_directory: str | Path) -> Path:
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        (output / "training-plan.json").write_text(
            self.model_dump_json(indent=2, exclude={"train_records", "eval_records"}) + "\n",
            encoding="utf-8",
        )
        for filename, records in (
            ("train-sft.jsonl", self.train_records),
            ("eval-sft.jsonl", self.eval_records),
        ):
            text = "".join(record.model_dump_json() + "\n" for record in records)
            (output / filename).write_text(text, encoding="utf-8")
        return output


class AdapterTrainingArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "self-model-adapter-artifact-v2"
    base_model: str
    method: AdapterMethod
    backend: TrainingBackend = TrainingBackend.HUGGINGFACE_PEFT
    adapter_path: str
    training_examples: int = Field(ge=0)
    cohort_fingerprint: str = Field(min_length=64, max_length=64)
    wall_seconds: float | None = Field(default=None, ge=0.0)
    peak_gpu_memory_bytes: int | None = Field(default=None, ge=0)
    train_result: dict[str, Any] = Field(default_factory=dict)


class AdapterTrainer(Protocol):
    backend: TrainingBackend

    def train(
        self,
        bundle: PreparedTrainingBundle,
        output_directory: str | Path,
    ) -> AdapterTrainingArtifact: ...


def _balanced_limit(
    records: tuple[FormattedSelfModelRecord, ...],
    limit: int,
) -> tuple[FormattedSelfModelRecord, ...]:
    if len(records) <= limit:
        return records
    buckets: dict[tuple[str, str], list[FormattedSelfModelRecord]] = {}
    for record in records:
        key = (record.architecture_family, record.curriculum_family)
        buckets.setdefault(key, []).append(record)
    for values in buckets.values():
        values.sort(key=lambda item: (item.scenario_id, item.scenario_fingerprint))

    selected: list[FormattedSelfModelRecord] = []
    keys = sorted(buckets)
    while len(selected) < limit:
        progressed = False
        for key in keys:
            values = buckets[key]
            if values and len(selected) < limit:
                selected.append(values.pop(0))
                progressed = True
        if not progressed:
            break
    return tuple(selected)


def prepare_training_bundle(
    cohort: TrainingCohort,
    config: AdapterTrainingConfig,
) -> PreparedTrainingBundle:
    train = tuple(format_self_model_example(item) for item in cohort.train)
    evaluation = tuple(format_self_model_example(item) for item in cohort.eval)
    train = _balanced_limit(train, config.max_train_examples)
    if not train:
        raise ValueError("prepared training bundle contains no training records")
    if not evaluation:
        raise ValueError("prepared training bundle contains no evaluation records")
    return PreparedTrainingBundle(
        config=config,
        cohort_manifest=cohort.manifest,
        train_records=train,
        eval_records=evaluation,
    )


def load_prepared_training_bundle(directory: str | Path) -> PreparedTrainingBundle:
    root = Path(directory)
    metadata = __import__("json").loads(
        (root / "training-plan.json").read_text(encoding="utf-8")
    )

    def read_records(name: str) -> tuple[FormattedSelfModelRecord, ...]:
        values: list[FormattedSelfModelRecord] = []
        for line in (root / name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                values.append(FormattedSelfModelRecord.model_validate_json(line))
        return tuple(values)

    bundle = PreparedTrainingBundle.model_validate(
        {
            **metadata,
            "train_records": read_records("train-sft.jsonl"),
            "eval_records": read_records("eval-sft.jsonl"),
        }
    )
    expected_train = min(
        bundle.cohort_manifest.train_count, bundle.config.max_train_examples
    )
    if len(bundle.train_records) != expected_train:
        raise ValueError("prepared train record count does not match training plan")
    if len(bundle.eval_records) != bundle.cohort_manifest.eval_count:
        raise ValueError("prepared eval record count does not match cohort manifest")
    return bundle


def _gpu_peak_bytes(torch: Any) -> int | None:
    if not torch.cuda.is_available():
        return None
    return int(torch.cuda.max_memory_allocated())


def _reset_gpu_peak(torch: Any) -> None:
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()


def _artifact(
    *,
    bundle: PreparedTrainingBundle,
    backend: TrainingBackend,
    adapter_dir: Path,
    result: Any,
    wall_seconds: float,
    peak_gpu_memory_bytes: int | None,
    extra_metrics: dict[str, Any] | None = None,
) -> AdapterTrainingArtifact:
    metrics = dict(getattr(result, "metrics", {}) or {})
    if extra_metrics:
        metrics.update(extra_metrics)
    return AdapterTrainingArtifact(
        base_model=bundle.config.base_model,
        method=bundle.config.method,
        backend=backend,
        adapter_path=str(adapter_dir),
        training_examples=len(bundle.train_records),
        cohort_fingerprint=bundle.cohort_manifest.cohort_fingerprint,
        wall_seconds=wall_seconds,
        peak_gpu_memory_bytes=peak_gpu_memory_bytes,
        train_result={
            "global_step": int(getattr(result, "global_step", 0)),
            "training_loss": float(getattr(result, "training_loss", 0.0)),
            "metrics": metrics,
        },
    )


def _write_artifact(output: Path, artifact: AdapterTrainingArtifact) -> None:
    (output / "adapter-artifact.json").write_text(
        artifact.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


class HuggingFacePeftTrainer:
    """Optional real LoRA/QLoRA backend using Transformers + PEFT + TRL."""

    backend = TrainingBackend.HUGGINGFACE_PEFT

    def train(
        self,
        bundle: PreparedTrainingBundle,
        output_directory: str | Path,
    ) -> AdapterTrainingArtifact:
        try:
            import torch
            from datasets import Dataset
            from peft import LoraConfig, prepare_model_for_kbit_training
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
            from trl import SFTConfig, SFTTrainer
        except ImportError as exc:  # pragma: no cover - environment-specific backend
            raise RuntimeError(
                "self-model training dependencies are missing; install harness-x[training]"
            ) from exc

        config = bundle.config
        output = Path(output_directory)
        adapter_dir = output / "adapter"
        output.mkdir(parents=True, exist_ok=True)

        tokenizer = AutoTokenizer.from_pretrained(config.base_model, use_fast=True)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token

        model_kwargs: dict[str, Any] = {"device_map": "auto"}
        if config.method == AdapterMethod.QLORA:
            compute_dtype = (
                torch.bfloat16
                if torch.cuda.is_available() and torch.cuda.is_bf16_supported()
                else torch.float16
            )
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=compute_dtype,
            )

        model = AutoModelForCausalLM.from_pretrained(config.base_model, **model_kwargs)
        if config.method == AdapterMethod.QLORA:
            model = prepare_model_for_kbit_training(
                model,
                use_gradient_checkpointing=config.gradient_checkpointing,
            )

        peft_config = LoraConfig(
            r=config.lora_rank,
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=list(config.target_modules),
        )

        # TRL prompt-completion datasets compute loss on completion tokens only.
        rows = []
        for record in bundle.train_records:
            prompt = [item.model_dump(mode="json") for item in record.prompt_messages]
            completion = [record.messages[-1].model_dump(mode="json")]
            rows.append({"prompt": prompt, "completion": completion})
        train_dataset = Dataset.from_list(rows)

        bf16 = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())
        args = SFTConfig(
            output_dir=str(output / "trainer"),
            learning_rate=config.learning_rate,
            num_train_epochs=config.num_train_epochs,
            per_device_train_batch_size=config.per_device_train_batch_size,
            gradient_accumulation_steps=config.gradient_accumulation_steps,
            max_length=config.max_length,
            warmup_ratio=config.warmup_ratio,
            weight_decay=config.weight_decay,
            gradient_checkpointing=config.gradient_checkpointing,
            seed=config.seed,
            save_steps=config.save_steps,
            logging_steps=config.logging_steps,
            completion_only_loss=True,
            bf16=bf16,
            fp16=bool(torch.cuda.is_available() and not bf16),
            report_to="none",
        )
        trainer = SFTTrainer(
            model=model,
            args=args,
            train_dataset=train_dataset,
            processing_class=tokenizer,
            peft_config=peft_config,
        )
        _reset_gpu_peak(torch)
        started = perf_counter()
        result = trainer.train()
        wall_seconds = perf_counter() - started
        peak = _gpu_peak_bytes(torch)
        trainer.model.save_pretrained(adapter_dir, safe_serialization=True)
        tokenizer.save_pretrained(adapter_dir)

        artifact = _artifact(
            bundle=bundle,
            backend=self.backend,
            adapter_dir=adapter_dir,
            result=result,
            wall_seconds=wall_seconds,
            peak_gpu_memory_bytes=peak,
        )
        _write_artifact(output, artifact)
        return artifact


class UnslothPeftTrainer:
    """Optional LoRA/QLoRA backend using the current Unsloth training surface.

    The backend writes an ordinary PEFT adapter rather than a merged model, preserving
    the same Harness X evaluator and rollback boundary used by the Hugging Face path.
    Imports are deliberately lazy so Unsloth never becomes a normal runtime dependency.
    """

    backend = TrainingBackend.UNSLOTH

    def train(
        self,
        bundle: PreparedTrainingBundle,
        output_directory: str | Path,
    ) -> AdapterTrainingArtifact:
        try:
            import torch
            import unsloth
            from datasets import Dataset
            from trl import SFTConfig, SFTTrainer
            from unsloth import FastLanguageModel
            from unsloth.chat_templates import train_on_responses_only
        except ImportError as exc:  # pragma: no cover - environment-specific backend
            raise RuntimeError(
                "Unsloth training dependencies are missing; install Unsloth for your "
                "platform and harness-x[unsloth-training]"
            ) from exc

        config = bundle.config
        output = Path(output_directory)
        adapter_dir = output / "adapter"
        output.mkdir(parents=True, exist_ok=True)

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=config.base_model,
            max_seq_length=config.max_length,
            dtype=None,
            load_in_4bit=config.method == AdapterMethod.QLORA,
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token

        model = FastLanguageModel.get_peft_model(
            model,
            r=config.lora_rank,
            target_modules=list(config.target_modules),
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            bias="none",
            use_gradient_checkpointing=(
                "unsloth" if config.gradient_checkpointing else False
            ),
            random_state=config.seed,
            use_rslora=False,
            loftq_config=None,
        )

        # Unsloth's current SFT path consumes rendered chat text.  Response-only
        # masking is applied after trainer construction so stable system/user context
        # does not dominate the loss merely because it contains more tokens.
        rows = []
        for record in bundle.train_records:
            text = render_messages_with_tokenizer(
                tokenizer, record.messages, add_generation_prompt=False
            )
            rows.append({"text": text})
        train_dataset = Dataset.from_list(rows)

        bf16 = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())
        args = SFTConfig(
            output_dir=str(output / "trainer"),
            dataset_text_field="text",
            learning_rate=config.learning_rate,
            num_train_epochs=config.num_train_epochs,
            per_device_train_batch_size=config.per_device_train_batch_size,
            gradient_accumulation_steps=config.gradient_accumulation_steps,
            warmup_ratio=config.warmup_ratio,
            weight_decay=config.weight_decay,
            gradient_checkpointing=config.gradient_checkpointing,
            seed=config.seed,
            save_steps=config.save_steps,
            logging_steps=config.logging_steps,
            optim="adamw_8bit",
            bf16=bf16,
            fp16=bool(torch.cuda.is_available() and not bf16),
            report_to="none",
        )
        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=train_dataset,
            args=args,
        )
        trainer = train_on_responses_only(trainer)

        _reset_gpu_peak(torch)
        started = perf_counter()
        result = trainer.train()
        wall_seconds = perf_counter() - started
        peak = _gpu_peak_bytes(torch)

        # Save the adapter only. The normal Harness X PEFT evaluator can load it on the
        # untouched base model, which keeps backend comparison and rollback simple.
        model.save_pretrained(adapter_dir)
        tokenizer.save_pretrained(adapter_dir)
        artifact = _artifact(
            bundle=bundle,
            backend=self.backend,
            adapter_dir=adapter_dir,
            result=result,
            wall_seconds=wall_seconds,
            peak_gpu_memory_bytes=peak,
            extra_metrics={"unsloth_version": str(getattr(unsloth, "__version__", "unknown"))},
        )
        _write_artifact(output, artifact)
        return artifact


def trainer_for_backend(backend: TrainingBackend | str) -> AdapterTrainer:
    selected = TrainingBackend(backend)
    if selected == TrainingBackend.HUGGINGFACE_PEFT:
        return HuggingFacePeftTrainer()
    if selected == TrainingBackend.UNSLOTH:
        return UnslothPeftTrainer()
    raise AssertionError(selected)
