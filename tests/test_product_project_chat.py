from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness_x.product import ChatMessage, ChatMessageRole, ChatTextContent, ProjectChatStore


def _new_store(tmp_path: Path) -> tuple[ProjectChatStore, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return ProjectChatStore(tmp_path / "state"), workspace


def _ledger(store: ProjectChatStore, project_id: str, chat_id: str) -> Path:
    return store.root / "projects" / project_id / "chats" / chat_id / "messages.jsonl"


def _write_state_payload(store: ProjectChatStore, payload: dict) -> None:
    material = {key: value for key, value in payload.items() if key != "fingerprint"}
    canonical = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode()
    payload["fingerprint"] = hashlib.sha256(canonical).hexdigest()
    store.state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_project_identity_survives_restart_and_duplicate_workspace_is_rejected(tmp_path: Path) -> None:
    store, workspace = _new_store(tmp_path)
    project = store.create_project(name="Harness X", workspace_root=workspace)
    loaded = ProjectChatStore(store.root)
    assert loaded.project(project.project_id) == project
    assert loaded.project_for_workspace(workspace / ".") == project
    with pytest.raises(ValueError, match="already registered"):
        loaded.create_project(name="Duplicate", workspace_root=workspace / ".")


def test_workspace_case_normalization_matches_host_semantics(tmp_path: Path) -> None:
    store, workspace = _new_store(tmp_path)
    project = store.create_project(name="Case", workspace_root=workspace)
    if os.name == "nt":
        assert store.project_for_workspace(str(workspace).swapcase()) == project
    else:
        assert store.project_for_workspace(str(workspace).swapcase()) is None


def test_project_rename_archive_restore_preserves_identity(tmp_path: Path) -> None:
    store, workspace = _new_store(tmp_path)
    project = store.create_project(name="Old", workspace_root=workspace)
    renamed = store.rename_project(project.project_id, name=" New ")
    assert renamed.project_id == project.project_id
    assert renamed.name == "New"
    archived = store.archive_project(project.project_id)
    assert archived.archived
    assert store.projects() == ()
    assert store.project(project.project_id).project_id == project.project_id
    restored = store.restore_project(project.project_id)
    assert not restored.archived
    assert store.projects() == (restored,)


def test_blank_renames_fail_validation(tmp_path: Path) -> None:
    store, workspace = _new_store(tmp_path)
    project = store.create_project(name="Project", workspace_root=workspace)
    chat = store.create_chat(project.project_id, title="Chat")
    with pytest.raises(Exception):
        store.rename_project(project.project_id, name="   ")
    with pytest.raises(Exception):
        store.rename_chat(chat.chat_id, title="   ")


def test_multiple_chats_are_project_scoped_and_unknown_project_is_rejected(tmp_path: Path) -> None:
    store = ProjectChatStore(tmp_path / "state")
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    project_a = store.create_project(name="A", workspace_root=workspace_a)
    project_b = store.create_project(name="B", workspace_root=workspace_b)
    chat_a1 = store.create_chat(project_a.project_id, title="A1")
    chat_a2 = store.create_chat(project_a.project_id, title="A2")
    chat_b = store.create_chat(project_b.project_id, title="B")
    assert store.chats(project_a.project_id) == (chat_a1, chat_a2)
    assert store.chats(project_b.project_id) == (chat_b,)
    with pytest.raises(KeyError):
        store.create_chat("project_" + "0" * 32, title="Nope")


def test_chat_archive_restore_preserves_messages_and_identity(tmp_path: Path) -> None:
    store, workspace = _new_store(tmp_path)
    project = store.create_project(name="Project", workspace_root=workspace)
    chat = store.create_chat(project.project_id, title="Work")
    message = store.append_text_message(chat.chat_id, role="user", text="hello")
    archived = store.archive_chat(chat.chat_id)
    assert archived.archived
    assert store.chats(project.project_id) == ()
    assert store.messages(chat.chat_id) == (message,)
    restored = store.restore_chat(chat.chat_id)
    assert restored.chat_id == chat.chat_id
    assert store.messages(chat.chat_id) == (message,)


def test_messages_are_typed_ordered_and_restart_durable(tmp_path: Path) -> None:
    store, workspace = _new_store(tmp_path)
    project = store.create_project(name="Project", workspace_root=workspace)
    chat = store.create_chat(project.project_id, title="Conversation")
    one = store.append_text_message(chat.chat_id, role=ChatMessageRole.USER, text="first")
    two = store.append_text_message(chat.chat_id, role=ChatMessageRole.ASSISTANT, text="second")
    three = store.append_system_notice(chat.chat_id, code="runtime.ready", text="ready")
    assert [item.sequence for item in store.messages(chat.chat_id)] == [1, 2, 3]
    assert store.chat(chat.chat_id).message_count == 3
    loaded = ProjectChatStore(store.root)
    assert loaded.messages(chat.chat_id) == (one, two, three)


def test_append_first_crash_is_reconciled_from_durable_ledger(tmp_path: Path) -> None:
    store, workspace = _new_store(tmp_path)
    project = store.create_project(name="Project", workspace_root=workspace)
    chat = store.create_chat(project.project_id, title="Crash")
    message = ChatMessage(
        message_id=f"msg_{uuid.uuid4().hex}",
        project_id=project.project_id,
        chat_id=chat.chat_id,
        sequence=1,
        role=ChatMessageRole.USER,
        content=ChatTextContent(text="durable before metadata"),
        created_at=datetime.now(timezone.utc),
    )
    path = _ledger(store, project.project_id, chat.chat_id)
    path.write_text(message.model_dump_json() + "\n", encoding="utf-8")
    loaded = ProjectChatStore(store.root)
    assert loaded.chat(chat.chat_id).message_count == 1
    assert loaded.messages(chat.chat_id) == (message,)


def test_committed_count_longer_than_ledger_fails_closed(tmp_path: Path) -> None:
    store, workspace = _new_store(tmp_path)
    project = store.create_project(name="Project", workspace_root=workspace)
    chat = store.create_chat(project.project_id, title="Chat")
    store.append_text_message(chat.chat_id, role="user", text="one")
    _ledger(store, project.project_id, chat.chat_id).unlink()
    with pytest.raises(ValueError, match="shorter than committed"):
        ProjectChatStore(store.root)


@pytest.mark.parametrize("mode", ["malformed", "identity", "sequence", "duplicate"])
def test_message_ledger_corruption_fails_closed(tmp_path: Path, mode: str) -> None:
    store = ProjectChatStore(tmp_path / "state")
    wa = tmp_path / "a"
    wb = tmp_path / "b"
    wa.mkdir()
    wb.mkdir()
    pa = store.create_project(name="A", workspace_root=wa)
    pb = store.create_project(name="B", workspace_root=wb)
    ca = store.create_chat(pa.project_id, title="A")
    cb = store.create_chat(pb.project_id, title="B")
    path = _ledger(store, pa.project_id, ca.chat_id)
    now = datetime.now(timezone.utc)
    mid = f"msg_{uuid.uuid4().hex}"
    if mode == "malformed":
        path.write_text("{not-json}\n", encoding="utf-8")
    else:
        first = ChatMessage(
            message_id=mid,
            project_id=pb.project_id if mode == "identity" else pa.project_id,
            chat_id=cb.chat_id if mode == "identity" else ca.chat_id,
            sequence=2 if mode == "sequence" else 1,
            role="user",
            content=ChatTextContent(text="one"),
            created_at=now,
        )
        rows = [first.model_dump_json()]
        if mode == "duplicate":
            second = first.model_copy(update={"sequence": 2})
            rows.append(second.model_dump_json())
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ProjectChatStore(store.root)


def test_recent_projects_and_last_opened_chat_restore_deterministically(tmp_path: Path) -> None:
    store = ProjectChatStore(tmp_path / "state")
    wa = tmp_path / "a"
    wb = tmp_path / "b"
    wa.mkdir()
    wb.mkdir()
    pa = store.create_project(name="A", workspace_root=wa)
    pb = store.create_project(name="B", workspace_root=wb)
    ca = store.create_chat(pa.project_id, title="A chat")
    store.open_project(pb.project_id)
    store.open_chat(ca.chat_id)
    restored = ProjectChatStore(store.root).restoration_state()
    assert restored.last_opened_project_id == pa.project_id
    assert restored.last_opened_chat_id == ca.chat_id
    assert restored.recent_project_ids[:2] == (pa.project_id, pb.project_id)


def test_archiving_current_selections_clears_restoration_pointers(tmp_path: Path) -> None:
    store, workspace = _new_store(tmp_path)
    project = store.create_project(name="Project", workspace_root=workspace)
    chat = store.create_chat(project.project_id, title="Chat")
    assert store.restoration_state().last_opened_chat_id == chat.chat_id
    store.archive_chat(chat.chat_id)
    assert store.restoration_state().last_opened_chat_id is None
    store.archive_project(project.project_id)
    restored = store.restoration_state()
    assert restored.last_opened_project_id is None
    assert project.project_id not in restored.recent_project_ids


def test_metadata_fingerprint_corruption_fails_closed(tmp_path: Path) -> None:
    store, workspace = _new_store(tmp_path)
    store.create_project(name="Project", workspace_root=workspace)
    payload = json.loads(store.state_path.read_text(encoding="utf-8"))
    payload["projects"][0]["name"] = "tampered"
    store.state_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        ProjectChatStore(store.root)


def test_state_extra_field_is_rejected(tmp_path: Path) -> None:
    store, _ = _new_store(tmp_path)
    payload = json.loads(store.state_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    _write_state_payload(store, payload)
    with pytest.raises(ValueError, match="cannot validate"):
        ProjectChatStore(store.root)


def test_state_rejects_cross_project_last_opened_chat_reference(tmp_path: Path) -> None:
    store = ProjectChatStore(tmp_path / "state")
    wa = tmp_path / "a"
    wb = tmp_path / "b"
    wa.mkdir()
    wb.mkdir()
    pa = store.create_project(name="A", workspace_root=wa)
    pb = store.create_project(name="B", workspace_root=wb)
    cb = store.create_chat(pb.project_id, title="B")
    payload = json.loads(store.state_path.read_text(encoding="utf-8"))
    for project in payload["projects"]:
        if project["project_id"] == pa.project_id:
            project["last_opened_chat_id"] = cb.chat_id
    _write_state_payload(store, payload)
    with pytest.raises(ValueError, match="cannot validate"):
        ProjectChatStore(store.root)


def test_writes_use_fsync_for_state_and_message_durability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import harness_x.product.store as store_module

    calls: list[int] = []
    real_fsync = store_module.os.fsync

    def tracking_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(store_module.os, "fsync", tracking_fsync)
    store, workspace = _new_store(tmp_path)
    project = store.create_project(name="Project", workspace_root=workspace)
    chat = store.create_chat(project.project_id, title="Chat")
    store.append_text_message(chat.chat_id, role="user", text="hello")
    assert len(calls) >= 5
