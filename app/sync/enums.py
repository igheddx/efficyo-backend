"""Sync pipeline enumerations (stored as strings in DB)."""

from __future__ import annotations

from enum import Enum


class SyncJobStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    COLLECTING = "collecting"
    ANALYZING = "analyzing"
    SCORING = "scoring"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_JOB_STATUSES: frozenset[str] = frozenset(
    {
        SyncJobStatus.QUEUED.value,
        SyncJobStatus.PLANNING.value,
        SyncJobStatus.COLLECTING.value,
        SyncJobStatus.ANALYZING.value,
        SyncJobStatus.SCORING.value,
        SyncJobStatus.SUMMARIZING.value,
    }
)


class SyncTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES: frozenset[str] = frozenset(
    {
        SyncTaskStatus.SUCCEEDED.value,
        SyncTaskStatus.FAILED.value,
        SyncTaskStatus.SKIPPED.value,
        SyncTaskStatus.CANCELLED.value,
    }
)


class SyncTaskCategory(str, Enum):
    COLLECTOR = "collector"
    ANALYZER = "analyzer"
    SCORER = "scorer"
    SUMMARIZER = "summarizer"
    MAINTENANCE = "maintenance"


class SyncTriggerType(str, Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    RETRY = "retry"
    WEBHOOK = "webhook"
    SYSTEM = "system"


class SyncErrorCode(str, Enum):
    AUTH_ERROR = "auth_error"
    ROLE_ASSUMPTION_FAILED = "role_assumption_failed"
    RATE_LIMITED = "rate_limited"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_API_ERROR = "provider_api_error"
    VALIDATION_ERROR = "validation_error"
    UNSUPPORTED_RESOURCE = "unsupported_resource"
    ANALYSIS_ERROR = "analysis_error"
    SCORING_ERROR = "scoring_error"
    UNKNOWN_ERROR = "unknown_error"


CRITICAL_ERROR_CODES: frozenset[str] = frozenset(
    {
        SyncErrorCode.AUTH_ERROR.value,
        SyncErrorCode.ROLE_ASSUMPTION_FAILED.value,
    }
)
