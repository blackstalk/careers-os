"""Opportunity clustering and alert ranking (Phase 4.1).

Detection and notification selection are separate concerns: discovery,
eligibility, qualification, transferable fit, career direction,
opportunity value, and pursue-worthiness (career/pursue.py) decide
whether an opportunity is *good*. Nothing here re-decides that — this
module only groups already alert-eligible opportunities that likely
represent the same underlying role posted multiple times (different
team/location/segment), and ranks the resulting families using signals
those existing layers already produced. See
docs/alerts.md#alert-prioritization-and-opportunity-clustering.

Clustering never deletes, merges, or mutates an opportunity record —
every discovered job stays independently stored, scored, and inspectable
via `jobs show`/`jobs evaluate` regardless of cluster membership. A
cluster is a notification-time grouping only.
"""

import re
from dataclasses import dataclass

from careers_os.ingestion.discovery import DiscoveryOpportunity
from careers_os.notifications.policy import alert_rank

_SEPARATOR_RE = re.compile(r"\s*(?:[—\-–|]|,)\s*")
_PAREN_SUFFIX_RE = re.compile(r"\s*\(([^)]*)\)\s*$")

# Generic, industry-wide qualifiers commonly appended to a base title —
# never a company-specific team/product code name, which would risk
# collapsing genuinely different roles at one company into the same
# family. Deliberately small and editable, not exhaustive.
_GENERIC_SUFFIX_NOISE = {
    "remote", "hybrid", "onsite", "on-site",
    "enterprise", "commercial", "startups", "startup",
    "public sector", "government", "gov",
    "new grad", "intern", "internship",
}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _location_tokens(location: str | None) -> set[str]:
    """A title suffix like "- Tokyo" is matched against the job's own
    structured `location` field (e.g. "Tokyo, Japan") rather than a
    hardcoded city list — reuses existing structured data instead of
    guessing at geography.
    """
    if not location:
        return set()
    return {_normalize_text(p) for p in re.split(r"[,/]", location) if p.strip()}


def _is_noise_segment(segment: str, location_tokens: set[str]) -> bool:
    normalized = _normalize_text(segment)
    if normalized in _GENERIC_SUFFIX_NOISE or normalized in location_tokens:
        return True
    # A multi-word segment like "Large Enterprise" or "UK Government"
    # still counts as noise if its LAST word alone is a known qualifier
    # or location token — e.g. "New Grad - UK Government" and "... - US
    # Government" both reduce to the same family via "government" alone,
    # without needing every phrasing spelled out in the noise set.
    last_word = normalized.rsplit(" ", 1)[-1]
    return last_word in _GENERIC_SUFFIX_NOISE or last_word in location_tokens


def title_family(title: str, location: str | None = None) -> str:
    """Deterministic, explainable title normalization for clustering.

    Only strips a trailing parenthetical or comma/dash-delimited segment
    when it is either a known generic qualifier or textually derived from
    this job's own location — never a blind split on the first
    separator. A title like "Physical Design Engineer, Forward Deployed
    Engineering" keeps its full text, since "Forward Deployed
    Engineering" matches neither signal — under-clustering is preferred
    over incorrectly collapsing two different roles.
    """
    text = title.strip()
    location_tokens = _location_tokens(location)

    paren_match = _PAREN_SUFFIX_RE.search(text)
    if paren_match and _is_noise_segment(paren_match.group(1), location_tokens):
        text = text[: paren_match.start()].strip()

    parts = [p for p in _SEPARATOR_RE.split(text) if p.strip()]
    while len(parts) > 1 and _is_noise_segment(parts[-1], location_tokens):
        parts = parts[:-1]

    return _normalize_text(" ".join(parts)) if parts else _normalize_text(title)


def _company_key(opp: DiscoveryOpportunity) -> str:
    return _normalize_text(opp.job.company or "unknown")


def _rank_key(opp: DiscoveryOpportunity) -> tuple[int, float]:
    """(pursue tier, existing discovery rank_score) — the same two
    signals `jobs discover` already surfaces, reused verbatim rather than
    inventing a new score. Pursue tier is primary so a `strong_pursue`
    opportunity always outranks a `pursue` one (relevant in active mode,
    where both tiers can be alert-eligible at once); `rank_score` (fit,
    bridge-role strength, immediate opportunity, career direction —
    career/discovery_ranking.py) breaks ties within a tier.
    """
    return (alert_rank(opp.decision.pursue.recommendation), opp.rank_score)


@dataclass
class OpportunityCluster:
    company: str
    title_family: str
    representative: DiscoveryOpportunity
    variants: list[DiscoveryOpportunity]

    @property
    def additional_variant_count(self) -> int:
        return len(self.variants) - 1


def cluster_opportunities(opportunities: list[DiscoveryOpportunity]) -> list[OpportunityCluster]:
    """Group alert-eligible opportunities into families and pick the
    strongest member of each as its representative. Input order is
    preserved as the grouping/tie-break order (callers typically pass
    opportunities already sorted by `rank_score`, so this stays
    deterministic).
    """
    groups: dict[tuple[str, str], list[DiscoveryOpportunity]] = {}
    order: list[tuple[str, str]] = []
    for opp in opportunities:
        key = (_company_key(opp), title_family(opp.job.title, opp.job.location))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(opp)

    clusters = []
    for company, family in order:
        variants = groups[(company, family)]
        representative = max(variants, key=_rank_key)
        clusters.append(
            OpportunityCluster(company=company, title_family=family, representative=representative, variants=variants)
        )
    return clusters


def rank_clusters(clusters: list[OpportunityCluster]) -> list[OpportunityCluster]:
    """Order cluster representatives by the same (pursue tier, rank_score)
    signal used to pick each representative — deterministic, explainable,
    and identical regardless of which provider/source the opportunity
    came from (no company/provider identity is part of the sort key).
    """
    return sorted(clusters, key=lambda c: _rank_key(c.representative), reverse=True)
