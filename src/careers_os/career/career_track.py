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
3. A **specialist platform** named over and over in the description
   (Workday, Workato, ServiceNow, SAP, ...) defines the job even when the
   title is generic ("Staff Engineer, People Technology"). Repetition is
   what separates defining from incidental: one mention of SAP in an
   integration role is context, eight mentions of Workday is the job.

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
    # People/HR systems: a direction the candidate is explicitly not
    # optimizing for (Phase 4.4), and title-level because it names the
    # domain the role serves.
    ("people / HR systems", r"people (technology|analytics|systems|operations)\b|\bhris\b|human resources"),
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

# Specialist platforms whose depth is the job when a posting leans on them.
# Each entry maps to the taxonomy skill that would show the candidate
# actually has that depth; None means nothing in the taxonomy covers it,
# so it can never be evidenced. Deliberately narrow — this is not a
# blacklist of technologies, it is a list of products whose specialists
# are hired as specialists.
_SPECIALIST_PLATFORMS: tuple[tuple[str, str, Optional[str]], ...] = (
    ("Workday", r"workday", None),
    ("Workato", r"workato", None),
    ("SuccessFactors", r"successfactors", None),
    ("UKG", r"\bukg\b", None),
    ("BambooHR", r"bamboohr", None),
    ("SAP", r"\bsap\b", "erp_systems"),
    ("NetSuite", r"netsuite", "erp_systems"),
    ("Oracle ERP", r"oracle (cloud )?erp|oracle fusion", "erp_systems"),
    ("Dynamics 365", r"dynamics 365", "erp_systems"),
    ("ServiceNow", r"servicenow", None),
    ("Appian", r"appian", None),
    ("Pega", r"\bpega\b", None),
    ("Informatica", r"informatica", None),
    ("MuleSoft", r"mulesoft", None),
    ("Boomi", r"\bboomi\b", None),
    ("Sitecore", r"sitecore", None),
    ("Adobe Experience Manager", r"adobe experience manager|\baem\b", None),
)
# Below this many mentions a platform is context, not the job.
_DEFINING_MENTIONS = 3
# "...ticketing systems (e.g., ServiceNow)" lists integration targets; it
# doesn't make the job a ServiceNow job. Mentions introduced this way are
# not counted (Phase 4.4).
_EXAMPLE_LEAD_IN = re.compile(r"(e\.g\.|such as|including|like|:)[^.;]{0,60}$", re.IGNORECASE)
_SUPPORTED = (MatchType.STRONG_MATCH, MatchType.PARTIAL_MATCH)


def _first_match(title: str, families: tuple[tuple[str, str], ...]) -> Optional[tuple[str, str]]:
    for label, pattern in families:
        match = re.search(pattern, title)
        if match:
            return label, match.group(0)
    return None


def _defining_specialism(text: str, supported: set[str]) -> Optional[tuple[str, int]]:
    """The specialist platform this posting is built around, if any, and how
    often it says so. Skipped when the candidate has evidence for it."""
    for label, pattern, evidence_skill in _SPECIALIST_PLATFORMS:
        if evidence_skill is not None and evidence_skill in supported:
            continue
        count = sum(
            1 for m in re.finditer(pattern, text)
            if not _EXAMPLE_LEAD_IN.search(text[max(0, m.start() - 70):m.start()])
        )
        if count >= _DEFINING_MENTIONS:
            return label, count
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


def classify_career_track(
    title: str, detail: Optional[ExperienceFitDetail], description: Optional[str] = None
) -> CareerTrackResult:
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

    specialism = _defining_specialism(f"{title_lower}\n{(description or '').lower()}", supported)

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
    if specialism is not None:
        label, count = specialism
        return CareerTrackResult(
            track=track,
            alignment=TrackAlignment.UNCLEAR,
            reason=f"The title fits the {track_label} track ('{word}'), but the description is built around "
            f"{label} ({count} mentions) — platform depth the candidate has no evidence for.",
            evidence=[f"specialism: {label} x{count}", *evidence],
        )
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
