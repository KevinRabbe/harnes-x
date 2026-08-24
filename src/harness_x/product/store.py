"""Restart-safe local persistence for the M66 project/chat product domain."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from .models import (
    ChatMessage,
    ChatMessageRole,
    ChatRecord,
    ChatSystemNoticeContent,
    ChatTextContent,
    ProjectChatRestorationState,
    ProjectChatState,
    ProjectRecord,
    _canonical,
)

_MAX_RECENT_PROJECTS = 32
_UNSET = object()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_workspace(path: str | Path, *, require_directory: bool) -> tuple[str, str]:
    candidate = Path(path).expanduser()
    if require_directory and (not candidate.exists() or not candidate.is_dir()):
        raise ValueError("project workspace root must be an existing directory")
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        raise ValueError(f"cannot resolve project workspace root: {exc}") from exc
    if not resolved.is_absolute():
        raise ValueError("project workspace root must resolve to an absolute path")
    root = str(resolved)
    return root, os.path.normcase(os.path.normpath(root))


class ProjectChatStore:
    """Atomic project/chat metadata plus append-only per-chat message ledgers."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "project-chat-state.json"
        self.projects_root = self.root / "projects"
        self.projects_root.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            self.state = self._load_state()
            self._reconcile_message_ledgers()
        else:
            self.state = ProjectChatState(revision=1)
            self._write_state()

    def create_project(
        self,
        *,
        name: str,
        workspace_root: str | Path,
        default_model_profile: str | None = None,
    ) -> ProjectRecord:
        root, key = canonical_workspace(workspace_root, require_directory=True)
        if any(item.workspace_key == key for item in self.state.projects):
            raise ValueError("project workspace is already registered")
        now = _now()
        project = ProjectRecord(
            project_id=f"project_{uuid.uuid4().hex}",
            name=name,
            workspace_root=root,
            workspace_key=key,
            default_model_profile=default_model_profile,
            created_at=now,
            updated_at=now,
            last_opened_at=now,
        )
        self._replace_state(
            projects=(*self.state.projects, project),
            recent_project_ids=self._touch_recent(project.project_id, self.state.recent_project_ids),
            last_opened_project_id=project.project_id,
        )
        return self.project(project.project_id)

    def project(self, project_id: str) -> ProjectRecord:
        return self.state.projects[self._project_index(project_id)]

    def projects(self, *, include_archived: bool = False) -> tuple[ProjectRecord, ...]:
        return tuple(item for item in self.state.projects if include_archived or not item.archived)

    def project_for_workspace(self, workspace_root: str | Path) -> ProjectRecord | None:
        _, key = canonical_workspace(workspace_root, require_directory=False)
        return next((item for item in self.state.projects if item.workspace_key == key), None)

    def rename_project(self, project_id: str, *, name: str) -> ProjectRecord:
        index = self._project_index(project_id)
        current = self.state.projects[index]
        projects = list(self.state.projects)
        projects[index] = ProjectRecord.model_validate(
            {**current.model_dump(mode="python"), "name": name, "updated_at": _now()}
        )
        self._replace_state(projects=tuple(projects))
        return self.project(project_id)

    def archive_project(self, project_id: str) -> ProjectRecord:
        index = self._project_index(project_id)
        current = self.state.projects[index]
        if current.archived:
            return current
        projects = list(self.state.projects)
        projects[index] = current.model_copy(update={"archived": True, "updated_at": _now()})
        self._replace_state(
            projects=tuple(projects),
            recent_project_ids=tuple(item for item in self.state.recent_project_ids if item != project_id),
            last_opened_project_id=(
                None if self.state.last_opened_project_id == project_id else self.state.last_opened_project_id
            ),
        )
        return self.project(project_id)

    def restore_project(self, project_id: str) -> ProjectRecord:
        index = self._project_index(project_id)
        current = self.state.projects[index]
        if not current.archived:
            return current
        projects = list(self.state.projects)
        projects[index] = current.model_copy(update={"archived": False, "updated_at": _now()})
        self._replace_state(projects=tuple(projects))
        return self.project(project_id)

    def open_project(self, project_id: str) -> ProjectRecord:
        index = self._project_index(project_id)
        current = self.state.projects[index]
        if current.archived:
            raise ValueError("cannot open an archived project")
        projects = list(self.state.projects)
        projects[index] = current.model_copy(update={"last_opened_at": _now()})
        self._replace_state(
            projects=tuple(projects),
            recent_project_ids=self._touch_recent(project_id, self.state.recent_project_ids),
            last_opened_project_id=project_id,
        )
        return self.project(project_id)

    def create_chat(self, project_id: str, *, title: str) -> ChatRecord:
        project_index = self._project_index(project_id)
        project = self.state.projects[project_index]
        if project.archived:
            raise ValueError("cannot create a chat in an archived project")
        now = _now()
        chat = ChatRecord(
            chat_id=f"chat_{uuid.uuid4().hex}",
            project_id=project_id,
            title=title,
            created_at=now,
            updated_at=now,
            last_opened_at=now,
        )
        self._chat_directory(chat).mkdir(parents=True, exist_ok=True)
        projects = list(self.state.projects)
        projects[project_index] = project.model_copy(
            update={"last_opened_chat_id": chat.chat_id, "last_opened_at": now}
        )
        self._replace_state(
            projects=tuple(projects),
            chats=(*self.state.chats, chat),
            recent_project_ids=self._touch_recent(project_id, self.state.recent_project_ids),
            last_opened_project_id=project_id,
        )
        return self.chat(chat.chat_id)

    def chat(self, chat_id: str) -> ChatRecord:
        return self.state.chats[self._chat_index(chat_id)]

    def chats(self, project_id: str, *, include_archived: bool = False) -> tuple[ChatRecord, ...]:
        self._project_index(project_id)
        return tuple(
            item
            for item in self.state.chats
            if item.project_id == project_id and (include_archived or not item.archived)
        )

    def rename_chat(self, chat_id: str, *, title: str) -> ChatRecord:
        index = self._chat_index(chat_id)
        current = self.state.chats[index]
        chats = list(self.state.chats)
        chats[index] = ChatRecord.model_validate(
            {**current.model_dump(mode="python"), "title": title, "updated_at": _now()}
        )
        self._replace_state(chats=tuple(chats))
        return self.chat(chat_id)

    def archive_chat(self, chat_id: str) -> ChatRecord:
        chat_index = self._chat_index(chat_id)
        current = self.state.chats[chat_index]
        if current.archived:
            return current
        chats = list(self.state.chats)
        chats[chat_index] = current.model_copy(update={"archived": True, "updated_at": _now()})
        project_index = self._project_index(current.project_id)
        project = self.state.projects[project_index]
        projects = list(self.state.projects)
        if project.last_opened_chat_id == chat_id:
            projects[project_index] = project.model_copy(update={"last_opened_chat_id": None})
        self._replace_state(projects=tuple(projects), chats=tuple(chats))
        return self.chat(chat_id)

    def restore_chat(self, chat_id: str) -> ChatRecord:
        index = self._chat_index(chat_id)
        current = self.state.chats[index]
        owner = self.project(current.project_id)
        if owner.archived:
            raise ValueError("cannot restore a chat while its project is archived")
        if not current.archived:
            return current
        chats = list(self.state.chats)
        chats[index] = current.model_copy(update={"archived": False, "updated_at": _now()})
        self._replace_state(chats=tuple(chats))
        return self.chat(chat_id)

    def open_chat(self, chat_id: str) -> ChatRecord:
        chat_index = self._chat_index(chat_id)
        chat = self.state.chats[chat_index]
        project_index = self._project_index(chat.project_id)
        project = self.state.projects[project_index]
        if project.archived or chat.archived:
            raise ValueError("cannot open an archived project/chat")
        now = _now()
        chats = list(self.state.chats)
        chats[chat_index] = chat.model_copy(update={"last_opened_at": now})
        projects = list(self.state.projects)
        projects[project_index] = project.model_copy(
            update={"last_opened_chat_id": chat_id, "last_opened_at": now}
        )
        self._replace_state(
            projects=tuple(projects),
            chats=tuple(chats),
            recent_project_ids=self._touch_recent(project.project_id, self.state.recent_project_ids),
            last_opened_project_id=project.project_id,
        )
        return self.chat(chat_id)

    def append_text_message(
        self,
        chat_id: str,
        *,
        role: ChatMessageRole | str,
        text: str,
    ) -> ChatMessage:
        return self._append_message(chat_id, role=ChatMessageRole(role), content=ChatTextContent(text=text))

    def append_system_notice(self, chat_id: str, *, code: str, text: str) -> ChatMessage:
        return self._append_message(
            chat_id,
            role=ChatMessageRole.SYSTEM,
            content=ChatSystemNoticeContent(code=code, text=text),
        )

    def messages(self, chat_id: str) -> tuple[ChatMessage, ...]:
        return self._read_message_ledger(self.chat(chat_id))

    def restoration_state(self) -> ProjectChatRestorationState:
        project_id = self.state.last_opened_project_id
        chat_id: str | None = None
        if project_id is not None:
            project = self.project(project_id)
            if project.archived:
                project_id = None
            else:
                chat_id = project.last_opened_chat_id
                if chat_id is not None and self.chat(chat_id).archived:
                    chat_id = None
        return ProjectChatRestorationState(
            last_opened_project_id=project_id,
            last_opened_chat_id=chat_id,
            recent_project_ids=self.state.recent_project_ids,
        )

    def _append_message(
        self,
        chat_id: str,
        *,
        role: ChatMessageRole,
        content: ChatTextContent | ChatSystemNoticeContent,
    ) -> ChatMessage:
        chat_index = self._chat_index(chat_id)
        chat = self.state.chats[chat_index]
        project_index = self._project_index(chat.project_id)
        project = self.state.projects[project_index]
        if project.archived or chat.archived:
            raise ValueError("cannot append to an archived project/chat")
        message = ChatMessage(
            message_id=f"msg_{uuid.uuid4().hex}",
            project_id=project.project_id,
            chat_id=chat.chat_id,
            sequence=chat.message_count + 1,
            role=role,
            content=content,
            created_at=_now(),
        )
        ledger = self._message_path(chat)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("ab") as handle:
            handle.write(_canonical(message.model_dump(mode="json")) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        chats = list(self.state.chats)
        chats[chat_index] = chat.model_copy(
            update={"message_count": message.sequence, "updated_at": message.created_at}
        )
        projects = list(self.state.projects)
        projects[project_index] = project.model_copy(update={"updated_at": message.created_at})
        self._replace_state(projects=tuple(projects), chats=tuple(chats))
        return message

    def _load_state(self) -> ProjectChatState:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load project/chat state: {exc}") from exc
        stored_fingerprint = str(raw.get("fingerprint", ""))
        try:
            state = ProjectChatState.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"cannot validate project/chat state: {exc}") from exc
        if stored_fingerprint != state.fingerprint:
            raise ValueError("project/chat state fingerprint mismatch")
        return state

    def _reconcile_message_ledgers(self) -> None:
        chats = list(self.state.chats)
        changed = False
        for index, chat in enumerate(chats):
            rows = self._read_message_ledger(chat)
            if len(rows) < chat.message_count:
                raise ValueError(f"chat {chat.chat_id} message ledger is shorter than committed state")
            if len(rows) == chat.message_count:
                continue
            last = rows[-1]
            chats[index] = chat.model_copy(
                update={"message_count": len(rows), "updated_at": max(chat.updated_at, last.created_at)}
            )
            changed = True
        if changed:
            self._replace_state(chats=tuple(chats))

    def _read_message_ledger(self, chat: ChatRecord) -> tuple[ChatMessage, ...]:
        ledger = self._message_path(chat)
        if not ledger.exists():
            return ()
        rows: list[ChatMessage] = []
        seen_ids: set[str] = set()
        try:
            with ledger.open("r", encoding="utf-8") as handle:
                for expected_sequence, line in enumerate(handle, start=1):
                    if not line.strip():
                        raise ValueError("blank chat message ledger row")
                    message = ChatMessage.model_validate_json(line)
                    if message.project_id != chat.project_id or message.chat_id != chat.chat_id:
                        raise ValueError("chat message ledger identity mismatch")
                    if message.sequence != expected_sequence:
                        raise ValueError("chat message ledger sequence is not contiguous")
                    if message.message_id in seen_ids:
                        raise ValueError("chat message ledger contains duplicate message IDs")
                    seen_ids.add(message.message_id)
                    rows.append(message)
        except (OSError, ValidationError, ValueError) as exc:
            if isinstance(exc, ValueError) and (
                str(exc).startswith("chat message ledger") or str(exc).startswith("blank chat message")
            ):
                raise
            raise ValueError(f"cannot load chat message ledger: {exc}") from exc
        return tuple(rows)

    def _replace_state(
        self,
        *,
        projects: tuple[ProjectRecord, ...] | None = None,
        chats: tuple[ChatRecord, ...] | None = None,
        recent_project_ids: tuple[str, ...] | None = None,
        last_opened_project_id: str | None | object = _UNSET,
    ) -> None:
        payload = self.state.model_dump(
            mode="python",
            exclude={
                "fingerprint",
                "revision",
                "projects",
                "chats",
                "recent_project_ids",
                "last_opened_project_id",
            },
        )
        payload.update(
            {
                "revision": self.state.revision + 1,
                "projects": self.state.projects if projects is None else projects,
                "chats": self.state.chats if chats is None else chats,
                "recent_project_ids": self.state.recent_project_ids if recent_project_ids is None else recent_project_ids,
                "last_opened_project_id": self.state.last_opened_project_id if last_opened_project_id is _UNSET else last_opened_project_id,
            }
        )
        self.state = ProjectChatState.model_validate(payload)
        self._write_state()

    def _write_state(self) -> None:
        payload = (self.state.model_dump_json(indent=2) + "\n").encode("utf-8")
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_path)

    def _project_index(self, project_id: str) -> int:
        for index, item in enumerate(self.state.projects):
            if item.project_id == project_id:
                return index
        raise KeyError(f"unknown project {project_id}")

    def _chat_index(self, chat_id: str) -> int:
        for index, item in enumerate(self.state.chats):
            if item.chat_id == chat_id:
                return index
        raise KeyError(f"unknown chat {chat_id}")

    def _chat_directory(self, chat: ChatRecord) -> Path:
        return self.projects_root / chat.project_id / "chats" / chat.chat_id

    def _message_path(self, chat: ChatRecord) -> Path:
        return self._chat_directory(chat) / "messages.jsonl"

    @staticmethod
    def _touch_recent(project_id: str, existing: Iterable[str]) -> tuple[str, ...]:
        ordered = [project_id]
        ordered.extend(item for item in existing if item != project_id)
        return tuple(ordered[:_MAX_RECENT_PROJECTS])
