"""Conversation-only M72 wrapper around the existing HarnessCodingRunner."""

from __future__ import annotations

from harness_x.coding import autonomous_runtime as autonomous_runtime_module
from harness_x.coding import runtime as coding_runtime_module

from .sensitive_approval import (
    ApprovalAwareToolExecutor,
    ConversationSensitiveActionGate,
    SensitiveActionApprovalBroker,
    activate_sensitive_action_gate,
)
from .service import HarnessCodingRunner


class ApprovalAwareHarnessCodingRunner:
    """Install M72 only for run roots pre-bound to a durable conversation execution."""

    def __init__(self, broker: SensitiveActionApprovalBroker) -> None:
        self.approval_broker = broker
        self.delegate = HarnessCodingRunner()
        # Both coding loops construct ToolExecutor through their module globals. Replace only
        # those App-Server-process references; direct harness-x coding CLI processes never
        # import this wrapper. With no active contextvar gate the subclass is behavior-compatible.
        coding_runtime_module.ToolExecutor = ApprovalAwareToolExecutor
        autonomous_runtime_module.ToolExecutor = ApprovalAwareToolExecutor

    def __call__(self, snapshot):
        context = self.approval_broker.context_for_output_root(snapshot.output_root)
        if context is None:
            return self.delegate(snapshot)
        gate = ConversationSensitiveActionGate(
            self.approval_broker,
            context,
            snapshot.session_id,
        )
        with activate_sensitive_action_gate(gate):
            return self.delegate(snapshot)
