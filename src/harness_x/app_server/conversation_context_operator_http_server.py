"""Production M71 adapter over the frozen M70 conversation/work-activity HTTP surface.

The M70 HTTP class remains unchanged. This adapter replaces only the coordinator factory in that
module before re-exporting the exact same server class, preserving all inherited route, auth,
bootstrap, desktop, and module-identity behavior.
"""

from __future__ import annotations

from . import conversation_operator_http_server as _m70
from .contextual_conversation_execution import ContextualConversationExecutionCoordinator

_m70.ConversationExecutionCoordinator = ContextualConversationExecutionCoordinator
LocalOperatorHTTPServer = _m70.LocalOperatorHTTPServer

__all__ = ["LocalOperatorHTTPServer"]
