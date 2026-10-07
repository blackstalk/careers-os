from enum import Enum


class RemoteStatus(str, Enum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    CONTRACT = "contract"
    FREELANCE = "freelance"
    PART_TIME = "part_time"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


class JobStatus(str, Enum):
    """Human review/application lifecycle. Ingestion must never overwrite this."""

    NEW = "new"
    REVIEWING = "reviewing"
    SAVED = "saved"
    REJECTED = "rejected"
    PLANNING_TO_APPLY = "planning_to_apply"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    CLOSED = "closed"


class SourceHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BROKEN = "broken"


class SearchTrack(str, Enum):
    """Which objective a search profile serves (Phase 5). The replacement
    track is the primary one: a remote hands-on development role that can
    replace the current position. The exploratory track keeps the longer
    term career direction (FDE / applied AI / solutions) in view without
    competing for the same alert budget. See docs/discovery.md#tracks.
    """

    REPLACEMENT = "replacement"
    EXPLORATORY = "exploratory"


class OperatingMode(str, Enum):
    """How aggressively Careers OS surfaces opportunities for alerting —
    see docs/alerts.md#operating-modes. Centralized here (not scattered
    through notification code) so a future mode doesn't require touching
    the alert policy's decision logic, only its config.
    """

    PASSIVE = "passive"
    ACTIVE = "active"
