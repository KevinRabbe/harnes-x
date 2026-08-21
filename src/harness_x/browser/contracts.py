"""Typed M26 browser/application observation contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BrowserSelectorKind(StrEnum):
    ROLE = "role"
    LABEL = "label"
    TEXT = "text"
    TEST_ID = "test_id"
    CSS = "css"


class BrowserSelector(BaseModel):
    """Bounded semantic selector used by model-facing browser tools."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: BrowserSelectorKind
    role: str | None = Field(default=None, max_length=80)
    name: str | None = Field(default=None, max_length=500)
    value: str | None = Field(default=None, max_length=1000)
    exact: bool = False

    @model_validator(mode="after")
    def validate_shape(self) -> "BrowserSelector":
        if self.kind == BrowserSelectorKind.ROLE:
            if not (self.role or "").strip():
                raise ValueError("role selector requires role")
            if self.value is not None:
                raise ValueError("role selector uses name rather than value")
        else:
            if not (self.value or "").strip():
                raise ValueError(f"{self.kind.value} selector requires value")
            if self.role is not None or self.name is not None:
                raise ValueError("non-role selectors cannot set role/name")
        return self


class BrowserConsoleMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: str = Field(min_length=1, max_length=40)
    text: str = Field(max_length=4000)


class BrowserObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "browser-observation-v2"
    url: str
    title: str = Field(max_length=1000)
    aria_snapshot: str = Field(max_length=30000)
    aria_truncated: bool = False
    console_messages: tuple[BrowserConsoleMessage, ...] = ()
    console_truncated: bool = False
    page_errors: tuple[str, ...] = ()
    page_errors_truncated: bool = False


class BrowserScreenshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    full_page: bool
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)


class BrowserProviderInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    engine: str
    live_browser: bool


class ApplicationServerSpec(BaseModel):
    """Software-owned local application process declaration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["application-server-spec-v1"] = "application-server-spec-v1"
    argv: tuple[str, ...] = Field(min_length=1, max_length=32)
    cwd: str = "."
    base_url: str
    health_path: str = "/"
    startup_timeout_seconds: float = Field(default=30.0, gt=0.0, le=180.0)
    shutdown_timeout_seconds: float = Field(default=8.0, gt=0.0, le=60.0)

    @model_validator(mode="after")
    def validate_local_application(self) -> "ApplicationServerSpec":
        if any(not item.strip() for item in self.argv):
            raise ValueError("application server argv cannot contain blanks")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("application base_url must use http or https")
        if not is_loopback_hostname(parsed.hostname):
            raise ValueError("application base_url must be loopback-only")
        if parsed.username or parsed.password:
            raise ValueError("application base_url cannot contain credentials")
        if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
            raise ValueError("application base_url must be a pure origin without path/query/fragment")
        health = urlparse(self.health_path)
        if health.scheme or health.netloc or not self.health_path.startswith("/"):
            raise ValueError("health_path must be an absolute path on the declared app origin")
        return self


class ApplicationProcessState(BaseModel):
    model_config = ConfigDict(frozen=True)

    running: bool
    pid: int | None = None
    base_url: str
    stdout_path: str
    stderr_path: str
    returncode: int | None = None


def is_loopback_hostname(hostname: str | None) -> bool:
    if hostname is None:
        return False
    normalized = hostname.rstrip(".").casefold()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def ensure_artifact_path(root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute():
        raise ValueError("browser artifact path must be relative")
    target = (root / raw).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("browser artifact path escapes its root") from exc
    return target
