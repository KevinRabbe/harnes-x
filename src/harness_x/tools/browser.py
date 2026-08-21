"""Model-facing M26 browser tools backed by a software-owned BrowserProvider."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.browser import BrowserObservation, BrowserProvider, BrowserScreenshot, BrowserSelector

from .base import SideEffectLevel, ToolDefinition, ToolSpec


class BrowserOpenInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str = Field(default="/", min_length=1, max_length=1000)


class BrowserSnapshotInput(BaseModel):
    model_config = ConfigDict(frozen=True)


class BrowserSelectorInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    selector: BrowserSelector


class BrowserFillInput(BrowserSelectorInput):
    value: str = Field(max_length=5000)


class BrowserSelectInput(BrowserSelectorInput):
    value: str = Field(min_length=1, max_length=1000)


class BrowserScreenshotInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str = Field(default="page", min_length=1, max_length=80)
    full_page: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
            raise ValueError("screenshot name must be a bounded artifact slug")
        return value


class BrowserObservationOutput(BrowserObservation):
    pass


class BrowserScreenshotOutput(BrowserScreenshot):
    pass


class BrowserConsoleOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    url: str
    console_messages: tuple[dict[str, str], ...] = ()
    page_errors: tuple[str, ...] = ()


def _observation_definition(
    *,
    name: str,
    version: str,
    input_model: type[BaseModel],
    provider: BrowserProvider,
    handler,
    permissions: tuple[str, ...],
    side_effect_level: SideEffectLevel,
    idempotent: bool,
) -> ToolDefinition:
    return ToolDefinition(
        spec=ToolSpec(
            name=name,
            version=version,
            input_schema=input_model.model_json_schema(),
            output_schema=BrowserObservationOutput.model_json_schema(),
            permissions=permissions,
            side_effect_level=side_effect_level,
            cost_class="medium",
            timeout_seconds=15.0,
            idempotent=idempotent,
        ),
        input_model=input_model,
        output_model=BrowserObservationOutput,
        handler=handler,
    )


def browser_tool_definitions(provider: BrowserProvider) -> tuple[ToolDefinition, ...]:
    def open_handler(request: BrowserOpenInput) -> BrowserObservationOutput:
        return BrowserObservationOutput.model_validate(provider.open(request.path))

    def snapshot_handler(_request: BrowserSnapshotInput) -> BrowserObservationOutput:
        return BrowserObservationOutput.model_validate(provider.snapshot())

    def click_handler(request: BrowserSelectorInput) -> BrowserObservationOutput:
        return BrowserObservationOutput.model_validate(provider.click(request.selector))

    def fill_handler(request: BrowserFillInput) -> BrowserObservationOutput:
        return BrowserObservationOutput.model_validate(
            provider.fill(request.selector, request.value)
        )

    def select_handler(request: BrowserSelectInput) -> BrowserObservationOutput:
        return BrowserObservationOutput.model_validate(
            provider.select(request.selector, request.value)
        )

    def screenshot_handler(request: BrowserScreenshotInput) -> BrowserScreenshotOutput:
        screenshot = provider.screenshot(
            f"screenshots/{request.name}.png", full_page=request.full_page
        )
        return BrowserScreenshotOutput.model_validate(screenshot)

    def console_handler(_request: BrowserSnapshotInput) -> BrowserConsoleOutput:
        observation = provider.snapshot()
        return BrowserConsoleOutput(
            url=observation.url,
            console_messages=tuple(
                {"level": item.level, "text": item.text}
                for item in observation.console_messages
            ),
            page_errors=observation.page_errors,
        )

    return (
        _observation_definition(
            name="browser_open",
            version="browser-open-v1",
            input_model=BrowserOpenInput,
            provider=provider,
            handler=open_handler,
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            idempotent=True,
        ),
        _observation_definition(
            name="browser_snapshot",
            version="browser-snapshot-v1",
            input_model=BrowserSnapshotInput,
            provider=provider,
            handler=snapshot_handler,
            permissions=("workspace.read",),
            side_effect_level=SideEffectLevel.NONE,
            idempotent=True,
        ),
        _observation_definition(
            name="browser_click",
            version="browser-click-v1",
            input_model=BrowserSelectorInput,
            provider=provider,
            handler=click_handler,
            permissions=("workspace.execute",),
            side_effect_level=SideEffectLevel.PERSISTENT,
            idempotent=False,
        ),
        _observation_definition(
            name="browser_fill",
            version="browser-fill-v1",
            input_model=BrowserFillInput,
            provider=provider,
            handler=fill_handler,
            permissions=("workspace.execute",),
            side_effect_level=SideEffectLevel.PERSISTENT,
            idempotent=False,
        ),
        _observation_definition(
            name="browser_select",
            version="browser-select-v1",
            input_model=BrowserSelectInput,
            provider=provider,
            handler=select_handler,
            permissions=("workspace.execute",),
            side_effect_level=SideEffectLevel.PERSISTENT,
            idempotent=False,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="browser_screenshot",
                version="browser-screenshot-v1",
                input_schema=BrowserScreenshotInput.model_json_schema(),
                output_schema=BrowserScreenshotOutput.model_json_schema(),
                permissions=("workspace.read",),
                side_effect_level=SideEffectLevel.PERSISTENT,
                cost_class="medium",
                timeout_seconds=15.0,
                idempotent=False,
            ),
            input_model=BrowserScreenshotInput,
            output_model=BrowserScreenshotOutput,
            handler=screenshot_handler,
        ),
        ToolDefinition(
            spec=ToolSpec(
                name="browser_console",
                version="browser-console-v1",
                input_schema=BrowserSnapshotInput.model_json_schema(),
                output_schema=BrowserConsoleOutput.model_json_schema(),
                permissions=("workspace.read",),
                side_effect_level=SideEffectLevel.NONE,
                cost_class="low",
                timeout_seconds=10.0,
                idempotent=True,
            ),
            input_model=BrowserSnapshotInput,
            output_model=BrowserConsoleOutput,
            handler=console_handler,
        ),
    )
