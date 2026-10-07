from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from careers_os.domain.enums import OperatingMode, SearchTrack
from careers_os.domain.opportunity_decision import PursueRecommendation, WorkStyle

DEFAULT_PREFERENCES_PATH = Path(__file__).parent / "data" / "preferences.yaml"


class FullTimeComp(BaseModel):
    minimum_annual: float
    strong_annual: float


class ContractComp(BaseModel):
    minimum_hourly: float
    strong_hourly: float
    exceptional_hourly: float


class CompensationPreferences(BaseModel):
    full_time: FullTimeComp
    contract: ContractComp
    missing_compensation_penalty: float = 0.0


class WorkArrangementPreferences(BaseModel):
    preferred: list[str]
    onsite_penalty: float
    hybrid_penalty: float


class ComponentWeights(BaseModel):
    role_fit: float
    technical_fit: float
    career_direction_fit: float
    compensation_fit: float
    work_arrangement_fit: float
    experience_fit: float

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class DiscoveryRankingWeights(BaseModel):
    """Weights combining overall_fit with the Phase 2.5 discovery-specific
    dimensions (bridge role, immediate opportunity, career direction) into
    one sortable rank score — see docs/discovery.md#ranking-philosophy.
    Each dimension stays independently visible in `jobs discover` output;
    this is only used to order results.
    """

    overall_fit: float
    bridge_role: float
    immediate_opportunity: float
    career_direction: float

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class FreshnessThresholds(BaseModel):
    """Age-in-days boundaries for career/freshness.py's fresh/recent/aging/
    stale classification. Configurable rather than hard-coded — see
    docs/pursue-recommendation.md#freshness.
    """

    fresh_days: int = 7
    recent_days: int = 30
    aging_days: int = 90


class AlertModeSettings(BaseModel):
    """The pursue-recommendation floor required to send an alert in one
    operating mode — see docs/alerts.md#operating-modes. Deliberately just a
    `PursueRecommendation` value, not a new score: alert-worthiness is a
    threshold on the *existing* decision layer, never a competing model.

    `max_alerts_per_run` (Phase 4.1) is the separate, config-driven cap on
    how many notifications one `jobs run` may send — see
    docs/alerts.md#alert-prioritization-and-opportunity-clustering.
    Crossing the pursue threshold means an opportunity is *eligible* for
    an alert, never that it *will* be alerted; this budget is what
    actually decides how many of the eligible opportunities interrupt you
    this run.
    """

    minimum_pursue: PursueRecommendation
    max_alerts_per_run: int = 5


class TrackAlertSettings(BaseModel):
    """Per-track alert settings (Phase 5). Each search track gets its own
    threshold and budget so a compelling exploratory role can never
    consume a slot meant for a replacement opportunity.

    `max_verification_alerts_per_run` is a *separate* allowance for
    opportunities that look strong but whose pay or eligibility is
    unconfirmed. It never displaces a confirmed opportunity: it is spent
    only after the confirmed budget is filled or exhausted.
    """

    minimum_pursue: PursueRecommendation
    max_alerts_per_run: int = Field(default=4, ge=0)
    max_verification_alerts_per_run: int = Field(default=0, ge=0)
    # Verification alerts use their own, lower floor: a job whose pay or
    # eligibility is unconfirmed can never reach `strong_pursue` (that
    # requires readiness `immediate_fit`), so holding it to the confirmed
    # threshold would make the verification lane unreachable.
    verification_minimum_pursue: PursueRecommendation = PursueRecommendation.PURSUE


class AlertPolicy(BaseModel):
    passive: AlertModeSettings
    active: AlertModeSettings
    tracks: dict[SearchTrack, TrackAlertSettings] = Field(default_factory=dict)

    def for_mode(self, mode: OperatingMode) -> AlertModeSettings:
        return self.passive if mode == OperatingMode.PASSIVE else self.active

    def for_track(self, track: SearchTrack, mode: OperatingMode) -> TrackAlertSettings:
        """Track settings when configured, otherwise the operating mode's
        settings — so a preferences.yaml without a `tracks:` block keeps
        behaving exactly as it did before Phase 5."""
        configured = self.tracks.get(track)
        if configured is not None:
            return configured
        mode_settings = self.for_mode(mode)
        return TrackAlertSettings(
            minimum_pursue=mode_settings.minimum_pursue,
            max_alerts_per_run=mode_settings.max_alerts_per_run,
        )


class WorkStylePreferences(BaseModel):
    """Day-to-day role shapes the candidate wants to move away from — see
    docs/pursue-recommendation.md#work-style-fit. A disfavored style caps
    the pursue recommendation at `consider`; it never makes a role
    ineligible."""

    disfavored: list[WorkStyle] = Field(default_factory=list)


class AIRefinementSettings(BaseModel):
    """Budget for the optional Claude review of finalists (Phase 4.3) —
    see docs/scoring.md#bounded-ai-refinement. Only *new* API calls count;
    a job whose content hasn't changed since its last review reuses the
    stored result for free. 0 turns new calls off entirely."""

    max_refinements_per_run: int = Field(default=15, ge=0)


class Preferences(BaseModel):
    compensation: CompensationPreferences
    work_arrangement: WorkArrangementPreferences
    component_weights: ComponentWeights
    discovery_ranking_weights: Optional[DiscoveryRankingWeights] = None
    freshness_thresholds: Optional[FreshnessThresholds] = None
    operating_mode: OperatingMode = OperatingMode.PASSIVE
    alert_policy: Optional[AlertPolicy] = None
    work_style: WorkStylePreferences = Field(default_factory=WorkStylePreferences)
    ai_refinement: AIRefinementSettings = Field(default_factory=AIRefinementSettings)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Preferences":
        path = path or DEFAULT_PREFERENCES_PATH
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)
