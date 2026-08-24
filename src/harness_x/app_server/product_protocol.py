"""Strict request contracts for the M67 authenticated Project + Chat API."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness_x.product import ChatTextContent

_STRICT = ConfigDict(frozen=True, extra="forbid")


class CreateProjectRequest(BaseModel):
    model_config = _STRICT
    schema_version: Literal["create-project-request-v1"] = "create-project-request-v1"
    name: str = Field(min_length=1, max_length=200)
    workspace_root: Path
    default_model_profile: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("project name cannot be blank")
        return value

    @field_validator("default_model_profile")
    @classmethod
    def normalize_profile(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("default model profile cannot be blank")
        return value


class RenameProjectRequest(BaseModel):
    model_config = _STRICT
    schema_version: Literal["rename-project-request-v1"] = "rename-project-request-v1"
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("project name cannot be blank")
        return value


class CreateChatRequest(BaseModel):
    model_config = _STRICT
    schema_version: Literal["create-chat-request-v1"] = "create-chat-request-v1"
    title: str = Field(min_length=1, max_length=300)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("chat title cannot be blank")
        return value


class RenameChatRequest(BaseModel):
    model_config = _STRICT
    schema_version: Literal["rename-chat-request-v1"] = "rename-chat-request-v1"
    title: str = Field(min_length=1, max_length=300)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("chat title cannot be blank")
        return value


class AppendUserMessageRequest(BaseModel):
    """External callers may append only operator-authored text.

    Assistant and system records remain software-owned so a future UI cannot forge a Harness X
    response merely by possessing the normal operator API credential.
    """

    model_config = _STRICT
    schema_version: Literal["append-user-message-request-v1"] = (
        "append-user-message-request-v1"
    )
    role: Literal["user"] = "user"
    content: ChatTextContent

    @property
    def text(self) -> str:
        return self.content.text
