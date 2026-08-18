"""Deliberately small built-in tools used to validate the tool boundary."""

from __future__ import annotations

import operator
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .base import SideEffectLevel, ToolDefinition, ToolRegistry, ToolSpec


class CalculatorInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation: Literal["add", "subtract", "multiply", "divide"]
    a: float
    b: float


class CalculatorOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: float


def calculator_definition() -> ToolDefinition:
    operations = {
        "add": operator.add,
        "subtract": operator.sub,
        "multiply": operator.mul,
        "divide": operator.truediv,
    }

    def handler(request: CalculatorInput) -> CalculatorOutput:
        return CalculatorOutput(value=operations[request.operation](request.a, request.b))

    return ToolDefinition(
        spec=ToolSpec(
            name="calculator",
            version="calculator-v1",
            input_schema=CalculatorInput.model_json_schema(),
            output_schema=CalculatorOutput.model_json_schema(),
            permissions=(),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=1.0,
            idempotent=True,
        ),
        input_model=CalculatorInput,
        output_model=CalculatorOutput,
        handler=handler,
    )


class KeyValueReadInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1)


class KeyValueReadOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    found: bool
    value: str | None = None


def key_value_read_definition(values: dict[str, str]) -> ToolDefinition:
    snapshot = dict(values)

    def handler(request: KeyValueReadInput) -> KeyValueReadOutput:
        return KeyValueReadOutput(
            found=request.key in snapshot,
            value=snapshot.get(request.key),
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="kv_read",
            version="kv-read-v1",
            input_schema=KeyValueReadInput.model_json_schema(),
            output_schema=KeyValueReadOutput.model_json_schema(),
            permissions=("kv.read",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=1.0,
            idempotent=True,
        ),
        input_model=KeyValueReadInput,
        output_model=KeyValueReadOutput,
        handler=handler,
    )


class SandboxWriteInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    relative_path: str = Field(min_length=1)
    content: str


class SandboxWriteOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    relative_path: str
    bytes_written: int = Field(ge=0)


def sandbox_write_definition(root: str | Path) -> ToolDefinition:
    sandbox_root = Path(root).resolve()
    sandbox_root.mkdir(parents=True, exist_ok=True)

    def handler(request: SandboxWriteInput) -> SandboxWriteOutput:
        destination = (sandbox_root / request.relative_path).resolve()
        try:
            destination.relative_to(sandbox_root)
        except ValueError as exc:
            raise ValueError("sandbox writer refuses paths outside its root") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = request.content.encode("utf-8")
        destination.write_bytes(payload)
        return SandboxWriteOutput(
            relative_path=str(destination.relative_to(sandbox_root)),
            bytes_written=len(payload),
        )

    return ToolDefinition(
        spec=ToolSpec(
            name="sandbox_write",
            version="sandbox-write-v1",
            input_schema=SandboxWriteInput.model_json_schema(),
            output_schema=SandboxWriteOutput.model_json_schema(),
            permissions=("sandbox.write",),
            side_effect_level=SideEffectLevel.PERSISTENT,
            cost_class="medium",
            timeout_seconds=2.0,
            idempotent=True,
        ),
        input_model=SandboxWriteInput,
        output_model=SandboxWriteOutput,
        handler=handler,
    )


class UnreliableInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str = "ok"
    fail: bool = False
    delay_seconds: float = Field(default=0.0, ge=0.0, le=2.0)


class UnreliableOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str


def unreliable_definition(*, timeout_seconds: float = 0.05) -> ToolDefinition:
    def handler(request: UnreliableInput) -> UnreliableOutput:
        if request.delay_seconds:
            time.sleep(request.delay_seconds)
        if request.fail:
            raise RuntimeError("simulated tool failure")
        return UnreliableOutput(value=request.value)

    return ToolDefinition(
        spec=ToolSpec(
            name="unreliable",
            version="unreliable-v1",
            input_schema=UnreliableInput.model_json_schema(),
            output_schema=UnreliableOutput.model_json_schema(),
            permissions=("test.unreliable",),
            side_effect_level=SideEffectLevel.NONE,
            cost_class="low",
            timeout_seconds=timeout_seconds,
            idempotent=True,
        ),
        input_model=UnreliableInput,
        output_model=UnreliableOutput,
        handler=handler,
    )


def build_default_registry(
    *,
    sandbox_root: str | Path,
    key_values: dict[str, str] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(calculator_definition())
    registry.register(key_value_read_definition(key_values or {}))
    registry.register(sandbox_write_definition(sandbox_root))
    registry.register(unreliable_definition())
    return registry
