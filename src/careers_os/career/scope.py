"""Scope/ownership classification (Phase 3).

Distinguishes "implement approved wireframes in an existing template"
from "own platform architecture and API design" — deterministic
category/phrase presence, not keyword counts (same discipline as
career/bridge_role.py). Reuses the shared skills taxonomy's own
categories where they already carry the right meaning (architecture,
leadership, customer-facing, AI/ML) rather than inventing a parallel
vocabulary; the remaining dimensions (execution, ownership, strategy,
maintenance, platform) use their own small phrase lists since the skills
taxonomy has no equivalent concept for them.
"""

from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.opportunity_decision import ScopeDimension, ScopeResult
from careers_os.domain.taxonomy import SkillCategory

_TAXONOMY_CATEGORY_TO_DIMENSION = {
    SkillCategory.ARCHITECTURE: ScopeDimension.ARCHITECTURE,
    SkillCategory.LEADERSHIP: ScopeDimension.TECHNICAL_LEADERSHIP,
    SkillCategory.CUSTOMER_FACING: ScopeDimension.CUSTOMER_FACING,
    SkillCategory.AI_ML: ScopeDimension.AI_ML,
    SkillCategory.CLOUD: ScopeDimension.PLATFORM,
}

_PHRASE_MARKERS: dict[ScopeDimension, tuple[str, ...]] = {
    ScopeDimension.EXECUTION: (
        "implement", "execute the", "based on approved", "following the scope of work",
        "build features",
    ),
    ScopeDimension.OWNERSHIP: (
        "own the", "ownership of", "end-to-end ownership", "responsible for the entire",
        "you will own",
    ),
    ScopeDimension.STRATEGY: (
        "roadmap", "long-term vision", "define the direction", "strategic direction",
        "product strategy",
    ),
    ScopeDimension.MAINTENANCE: (
        "maintenance", "bug fixes", "ongoing support", "keep the lights on",
        "template-driven", "content updates",
    ),
}

# Priority order for picking one "primary" dimension when several are
# present — higher-order/less-common signals surface first.
_PRIORITY_ORDER = [
    ScopeDimension.ARCHITECTURE,
    ScopeDimension.STRATEGY,
    ScopeDimension.TECHNICAL_LEADERSHIP,
    ScopeDimension.OWNERSHIP,
    ScopeDimension.PLATFORM,
    ScopeDimension.AI_ML,
    ScopeDimension.CUSTOMER_FACING,
    ScopeDimension.EXECUTION,
    ScopeDimension.MAINTENANCE,
]


def classify_scope(title: str, description: str | None, taxonomy: SkillsTaxonomy | None = None) -> ScopeResult:
    taxonomy = taxonomy or SkillsTaxonomy.load()
    text = f"{title}\n{description or ''}"
    text_lower = text.lower()

    dimensions: set[ScopeDimension] = set()

    matched_skills = taxonomy.find_in_text(text)
    matched_categories = {taxonomy.skills[k].category for k in matched_skills if k in taxonomy.skills}
    for category, dimension in _TAXONOMY_CATEGORY_TO_DIMENSION.items():
        if category in matched_categories:
            dimensions.add(dimension)

    for dimension, phrases in _PHRASE_MARKERS.items():
        if any(phrase in text_lower for phrase in phrases):
            dimensions.add(dimension)

    if not dimensions:
        return ScopeResult(
            dimensions_present=[],
            primary_dimension=None,
            reason="No scope/ownership signal detected in the available text.",
        )

    primary = next((d for d in _PRIORITY_ORDER if d in dimensions), None)
    ordered = [d for d in _PRIORITY_ORDER if d in dimensions]
    return ScopeResult(
        dimensions_present=ordered,
        primary_dimension=primary,
        reason=f"Scope signal: {', '.join(d.value for d in ordered)}.",
    )
