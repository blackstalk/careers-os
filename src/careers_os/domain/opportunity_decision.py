from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from careers_os.domain.eligibility import EligibilityResult
from careers_os.domain.qualification import QualificationResult


class OpportunityCostLevel(str, Enum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    UNKNOWN = "unknown"


class OpportunityCostResult(BaseModel):
    level: OpportunityCostLevel
    reason: str


class ScopeDimension(str, Enum):
    EXECUTION = "execution"
    OWNERSHIP = "ownership"
    ARCHITECTURE = "architecture"
    STRATEGY = "strategy"
    CUSTOMER_FACING = "customer_facing"
    TECHNICAL_LEADERSHIP = "technical_leadership"
    PLATFORM = "platform"
    AI_ML = "ai_ml"
    MAINTENANCE = "maintenance"


class ScopeResult(BaseModel):
    dimensions_present: list[ScopeDimension] = Field(default_factory=list)
    primary_dimension: Optional[ScopeDimension] = None
    reason: str


class Freshness(str, Enum):
    FRESH = "fresh"
    RECENT = "recent"
    AGING = "aging"
    STALE = "stale"
    UNKNOWN = "unknown"


class FreshnessResult(BaseModel):
    level: Freshness
    age_days: Optional[int] = None
    reason: str


class PursueRecommendation(str, Enum):
    STRONG_PURSUE = "strong_pursue"
    PURSUE = "pursue"
    CONSIDER = "consider"
    LOW_PRIORITY = "low_priority"
    VERIFY_FIRST = "verify_first"
    DO_NOT_PURSUE = "do_not_pursue"


class PursueResult(BaseModel):
    recommendation: PursueRecommendation
    reason: str
    contributing_factors: list[str] = Field(default_factory=list)


EVALUATION_VERSION = "opportunity-decision-v1"


class OpportunityDecision(BaseModel):
    """The full reasoning chain for one job, evaluation-versioned so a
    stored recommendation can always be traced back to which logic
    produced it — see docs/pursue-recommendation.md#evaluation-versioning.
    """

    evaluation_version: str = EVALUATION_VERSION

    eligibility: EligibilityResult
    qualification: QualificationResult
    opportunity_cost: OpportunityCostResult
    scope: ScopeResult
    freshness: FreshnessResult
    pursue: PursueResult
