"""Durable product-domain records for projects, chats, and chat messages."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_STRICT = ConfigDict(frozen=True, extra="forbid")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("project/chat timestamps must be timezone-aware")
    return value


class ChatMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatTextContent(BaseModel):
    model_config = _STRICT
    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=120_000)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("chat message text cannot be blank")
        return value


class ChatSystemNoticeContent(BaseModel):
    model_config = _STRICT
    type: Literal["system_notice"] = "system_notice"
    code: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_.:-]*$")
    text: str = Field(min_length=1, max_length=12_000)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("system notice text cannot be blank")
        return value


ChatMessageContent = Annotated[
    ChatTextContent | ChatSystemNoticeContent,
    Field(discriminator="type"),
]


class ProjectRecord(BaseModel):
    model_config = _STRICT
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=200)
    workspace_root: str = Field(min_length=1, max_length=4000)
    workspace_key: str = Field(min_length=1, max_length=4000)
    default_model_profile: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool = False
    last_opened_chat_id: str | None = Field(default=None, pattern=r"^chat_[0-9a-f]{32}$")
    created_at: datetime
    updated_at: datetime
    last_opened_at: datetime

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

    @field_validator("created_at", "updated_at", "last_opened_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def validate_workspace(self) -> "ProjectRecord":
        if not Path(self.workspace_root).is_absolute():
            raise ValueError("project workspace root must be absolute")
        return self


class ChatRecord(BaseModel):
    model_config = _STRICT
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    title: str = Field(min_length=1, max_length=300)
    archived: bool = False
    message_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    last_opened_at: datetime

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("chat title cannot be blank")
        return value

    @field_validator("created_at", "updated_at", "last_opened_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_aware(value)


class ChatMessage(BaseModel):
    model_config = _STRICT
    schema_version: Literal["chat-message-v1"] = "chat-message-v1"
    message_id: str = Field(pattern=r"^msg_[0-9a-f]{32}$")
    project_id: str = Field(pattern=r"^project_[0-9a-f]{32}$")
    chat_id: str = Field(pattern=r"^chat_[0-9a-f]{32}$")
    sequence: int = Field(ge=1)
    role: ChatMessageRole
    content: ChatMessageContent
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_aware(value)


class ProjectChatState(BaseModel):
    model_config = _STRICT
    schema_version: Literal["project-chat-state-v1"] = "project-chat-state-v1"
    revision: int = Field(ge=1)
    projects: tuple[ProjectRecord, ...] = ()
    chats: tuple[ChatRecord, ...] = ()
    recent_project_ids: tuple[str, ...] = Field(default=(), max_length=32)
    last_opened_project_id: str | None = Field(default=None, pattern=r"^project_[0-9a-f]{32}$")
    fingerprint: str = ""

    @model_validator(mode="after")
    def derive_and_validate(self) -> "ProjectChatState":
        project_by_id = {item.project_id: item for item in self.projects}
        if len(project_by_id) != len(self.projects):
            raise ValueError("project IDs must be unique")
        workspace_keys = [item.workspace_key for item in self.projects]
        if len(set(workspace_keys)) != len(workspace_keys):
            raise ValueError("project workspace keys must be unique")
        chat_by_id = {item.chat_id: item for item in self.chats}
        if len(chat_by_id) != len(self.chats):
            raise ValueError("chat IDs must be unique")
        for chat in self.chats:
            if chat.project_id not in project_by_id:
                raise ValueError("chat owner project is missing")
        for project in self.projects:
            if project.last_opened_chat_id is None:
                continue
            chat = chat_by_id.get(project.last_opened_chat_id)
            if chat is None or chat.project_id != project.project_id:
                raise ValueError("project last-opened chat belongs to another project")
            if chat.archived:
                raise ValueError("project last-opened chat cannot be archived")
        recent = self.recent_project_ids
        if len(set(recent)) != len(recent):
            raise ValueError("recent project IDs must be unique")
        for project_id in recent:
            project = project_by_id.get(project_id)
            if project is None:
                raise ValueError("recent project ID is unknown")
            if project.archived:
                raise ValueError("archived project cannot remain in recents")
        if self.last_opened_project_id is not None:
            project = project_by_id.get(self.last_opened_project_id)
            if project is None:
                raise ValueError("last-opened project ID is unknown")
            if project.archived:
                raise ValueError("last-opened project cannot be archived")
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        object.__setattr__(self, "fingerprint", _sha256(_canonical(material)))
        return self


class ProjectChatRestorationState(BaseModel):
    model_config = _STRICT
    last_opened_project_id: str | None = None
    last_opened_chat_id: str | None = None
    recent_project_ids: tuple[str, ...] = ()
