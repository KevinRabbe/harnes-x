"""Coding-task runtime for Harness X."""

from .autonomous_runtime import AutonomousCodingTaskRuntime
from .isolated_runtime import (
    IsolatedCodingTaskReport,
    IsolatedRepositoryCodingTaskRuntime,
)
from .isolation import (
    IsolationResult,
    IsolationRetention,
    IsolationStrategy,
    SourceWorkspaceIdentity,
    TaskWorkspaceChange,
    TaskWorkspaceIsolationManager,
)
from .repository_runtime import (
    RepositoryAwareAutonomousCodingTaskRuntime,
    RepositoryContextReasoningCore,
)
from .runtime import CodingTaskReport, CodingTaskRuntime, CodingVerificationResult
from .strict_verification import StrictVerificationPlatform
from .verification import (
    CommandVerificationCheck,
    FileContainsVerificationCheck,
    FileExistsVerificationCheck,
    VerificationCheckResult,
    VerificationCheckStatus,
    VerificationPlan,
    VerificationPlatform,
    VerificationRequirement,
    VerificationRun,
    VerificationVerdict,
    command_verification_plan,
    load_verification_plan,
)
from .verified_runtime import (
    VerificationContextReasoningCore,
    VerifiedCodingTaskReport,
    VerifiedIsolatedCodingTaskReport,
    VerifiedIsolatedRepositoryCodingTaskRuntime,
    VerifiedRepositoryCodingTaskRuntime,
)

__all__ = [
    "AutonomousCodingTaskRuntime",
    "CodingTaskReport",
    "CodingTaskRuntime",
    "CodingVerificationResult",
    "CommandVerificationCheck",
    "FileContainsVerificationCheck",
    "FileExistsVerificationCheck",
    "IsolatedCodingTaskReport",
    "IsolatedRepositoryCodingTaskRuntime",
    "IsolationResult",
    "IsolationRetention",
    "IsolationStrategy",
    "RepositoryAwareAutonomousCodingTaskRuntime",
    "RepositoryContextReasoningCore",
    "SourceWorkspaceIdentity",
    "StrictVerificationPlatform",
    "TaskWorkspaceChange",
    "TaskWorkspaceIsolationManager",
    "VerificationCheckResult",
    "VerificationCheckStatus",
    "VerificationContextReasoningCore",
    "VerificationPlan",
    "VerificationPlatform",
    "VerificationRequirement",
    "VerificationRun",
    "VerificationVerdict",
    "VerifiedCodingTaskReport",
    "VerifiedIsolatedCodingTaskReport",
    "VerifiedIsolatedRepositoryCodingTaskRuntime",
    "VerifiedRepositoryCodingTaskRuntime",
    "command_verification_plan",
    "load_verification_plan",
]
