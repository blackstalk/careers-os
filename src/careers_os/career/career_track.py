"""Career-track relevance (Phase 4.3): does this job belong on one of the
candidate's two real paths, and does its description back that up?

- **Stack-adjacent engineering**: PHP/Laravel/Craft/WordPress and backend,
  API, integration, platform, and architecture work.
- **Career-direction engineering**: applied AI / AI engineering, forward
  deployed and implementation-heavy solutions/customer engineering, and
  platform/systems work around them.

Two inputs, each doing one job:

1. The **title** says what the job *is*. A technology or domain named in
   the title defines the role ("Senior React Native Developer", "SAP
   Commerce Platform Engineer"); the same word deep in the description is
   usually incidental (an FDE posting that mentions React). So only the
   title can put a job off-track.
2. The **requirement matches** (scoring/evidence_matcher.py) say whether
   the description actually contains work the candidate has done. A
   target-looking title alone is never enough for `aligned`.

Used by career/pursue.py: `strong_pursue` requires `aligned`; an
off-track title caps the recommendation at `consider`. Deterministic and
explainable — every result names the words and evidence behind it. See
docs/pursue-recommendation.md#career-track.
"""

import re
from typing import Optional

from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.matching import MatchType
from careers_os.domain.opportunity_decision import CareerTrack, CareerTrackResult, TrackAlignment
from careers_os.domain.requirements import RequirementImportance

# --- Title families -------------------------------------------------------
# Each entry: (label, pattern). Matched against the lowercased title.

_OFF_TRACK_TITLE_FAMILIES: tuple[tuple[str, str], ...] = (
    ("mobile development", r"react native|\bios\b|\bandroid\b|\bmobile\b|\bflutter\b|\bswift\b|\bkotlin\b"),
    ("frontend specialism", r"front[- ]?end|\bui engineer|\bdesign systems?\b|\bux\b"),
    ("hardware / embedded", r"\bembedded\b|\bfirmware\b|\bhardware\b|\bfpga\b|\basic\b|semiconductor|\bsilicon\b"
                            r"|on-device|\bcontrols\b|\brobotics\b"),
    ("enterprise-product specialism", r"\bsap\b|\bappian\b|\binformatica\b|\bmulesoft\b|\bservicenow\b|\bpega\b"
                                      r"|\bsitecore\b|\bnetsuite\b|\bworkday\b|\bdynamics 365\b|\boracle\b"),
    ("data science / research", r"\bscientist\b|\bresearch engineer\b|\bresearcher\b|\bstatistician\b|\bquantitative\b"),
    ("security specialism", r"\bsecurity (engineer|analyst|architect|researcher)\b|\bpenetration\b|\bdetection engineer"),
    ("management", r"\bmanager\b|\bdirector\b|\bhead of\b|\bvp\b|\bvice president\b"),
    ("sales / account role", r"\baccount executive\b|\bpre-?sales\b|\bsales (representative|executive|manager)\b"
                             r"|\bbusiness development\b"),
    ("junior / intern level", r"\bintern(ship)?\b|\bnew grad\b|\bgraduate\b|\bjunior\b|\bjr\.?\b|\bentry[- ]level\b"
                              r"|\bapprentice\b|(?<!senior )\bassociate\b"),
)

_CAREER_DIRECTION_AI_TITLES = (
    r"\bai engineer|\bapplied ai\b|\bml engineer|\bmachine learning engineer|\bml systems\b|\bai platform\b"
    r"|\bllm\b|\bgenai\b|\bgenerative ai\b|\bai agents?\b|\bagentic\b|\bai (software|backend|infrastructure) engineer"
)
_CAREER_DIRECTION_DELIVERY_TITLES = (
    r"forward[- ]deployed|\bdeployed (engineer|architect)\b|\bsolutions? architect|\bsolutions? engineer"
    r"|\bcustomer engineer|\bfield engineer|\bimplementation (engineer|architect|consultant)"
    r"|\bintegrations? (engineer|architect)|\bsolutions integration|\bsystems (engineer|architect)"
    r"|\bplatform engineer|\btechnical consultant|\barchitect\b"
)
_STACK_ADJACENT_TITLES = (
    r"\bphp\b|\blaravel\b|\bcraft\b|\bwordpress\b|\bback[- ]?end\b|\bfull[- ]?stack\b|\bsoftware (engineer|developer)"
    r"|\bweb (developer|engineer)|\bapi engineer|\b(staff|principal|lead|senior) engineer\b|\bstaff engineer"
    r"|\bprincipal engineer"
)

# --- Positive evidence ----------------------------------------------------

_STACK_SKILLS = frozenset({"php", "laravel", "craft_cms", "wordpress", "cms"})
_SYSTEMS_SKILLS = frozenset({
    "rest_api", "graphql", "microservices", "distributed_systems", "solutions_architecture",
    "platform_engineering", "integration", "automation", "cloud_infrastructure", "aws", "gcp",
    "azure", "sql", "ci_cd", "kubernetes", "docker", "terraform", "linux",
})
_AI_SKILLS = frozenset({"ai_ml", "data_pipeline", "ml_model_training"})
_SUPPORTED = (MatchType.STRONG_MATCH, MatchType.PARTIAL_MATCH)


def _first_match(title: str, families: tuple[tuple[str, str], ...]) -> Optional[tuple[str, str]]:
    for label, pattern in families:
        match = re.search(pattern, title)
        if match:
            return label, match.group(0)
    return None


def _supported_skills(detail: Optional[ExperienceFitDetail]) -> set[str]:
    if detail is None:
        return set()
    return {
        m.requirement.canonical_skill
        for m in detail.requirement_matches
        if m.requirement.canonical_skill
        and m.match_type in _SUPPORTED
        and m.requirement.importance != RequirementImportance.PREFERRED
    }


def classify_career_track(title: str, detail: Optional[ExperienceFitDetail]) -> CareerTrackResult:
    title_lower = (title or "").lower()

    off = _first_match(title_lower, _OFF_TRACK_TITLE_FAMILIES)
    if off:
        label, word = off
        return CareerTrackResult(
            track=CareerTrack.NONE,
            alignment=TrackAlignment.OFF_TRACK,
            reason=f"The title defines this as {label} ('{word}'), which is outside both career tracks.",
            evidence=[f"title: {word}"],
        )

    supported = _supported_skills(detail)
    stack = sorted(supported & _STACK_SKILLS)
    systems = sorted(supported & _SYSTEMS_SKILLS)
    ai = sorted(supported & _AI_SKILLS)
    evidence = [f"stack: {s}" for s in stack] + [f"systems: {s}" for s in systems] + [f"ai: {s}" for s in ai]

    ai_title = re.search(_CAREER_DIRECTION_AI_TITLES, title_lower)
    delivery_title = re.search(_CAREER_DIRECTION_DELIVERY_TITLES, title_lower)
    stack_title = re.search(_STACK_ADJACENT_TITLES, title_lower)

    if ai_title:
        track = CareerTrack.CAREER_DIRECTION
        backed = bool(ai) and bool(systems)
        needs = "AI/ML work plus supporting systems work"
        word = ai_title.group(0)
    elif delivery_title:
        track = CareerTrack.CAREER_DIRECTION
        backed = len(systems) >= 2
        needs = "at least two backend/integration/platform requirements"
        word = delivery_title.group(0)
    elif stack_title:
        track = CareerTrack.STACK_ADJACENT
        backed = (bool(stack) and bool(systems)) or len(systems) >= 2
        needs = "stack overlap or at least two backend/integration/platform requirements"
        word = stack_title.group(0)
    else:
        return CareerTrackResult(
            track=CareerTrack.NONE,
            alignment=TrackAlignment.UNCLEAR,
            reason="The title doesn't identify a role on either career track.",
            evidence=evidence,
        )

    evidence = [f"title: {word}", *evidence]
    track_label = track.value.replace("_", " ")
    if backed:
        return CareerTrackResult(
            track=track,
            alignment=TrackAlignment.ALIGNED,
            reason=f"{track_label.capitalize()} role ('{word}') backed by matched experience: "
            f"{', '.join(stack + systems + ai)}.",
            evidence=evidence,
        )
    return CareerTrackResult(
        track=track,
        alignment=TrackAlignment.UNCLEAR,
        reason=f"The title fits the {track_label} track ('{word}'), but the description doesn't show "
        f"{needs} that the candidate's experience supports.",
        evidence=evidence,
    )
