"""Everyday product-domain primitives independent of UI and execution authority."""

from .models import (
    ChatMessage,
    ChatMessageContent,
    ChatMessageRole,
    ChatRecord,
    ChatSystemNoticeContent,
    ChatTextContent,
    ProjectChatRestorationState,
    ProjectChatState,
    ProjectRecord,
)
from .resources import (
    ProjectAttachmentRecord,
    ProjectResourceStore,
    ProjectWorkspaceFileSnapshotRecord,
)
from .settings import (
    ProjectAutonomyProfile,
    ProjectSettingsRecord,
    ProjectSettingsStore,
    ProjectVerificationStrategy,
)
from .store import ProjectChatStore, canonical_workspace

__all__ = [
    "ChatMessage",
    "ChatMessageContent",
    "ChatMessageRole",
    "ChatRecord",
    "ChatSystemNoticeContent",
    "ChatTextContent",
    "ProjectAttachmentRecord",
    "ProjectAutonomyProfile",
    "ProjectChatRestorationState",
    "ProjectChatState",
    "ProjectChatStore",
    "ProjectRecord",
    "ProjectResourceStore",
    "ProjectSettingsRecord",
    "ProjectSettingsStore",
    "ProjectVerificationStrategy",
    "ProjectWorkspaceFileSnapshotRecord",
    "canonical_workspace",
]
