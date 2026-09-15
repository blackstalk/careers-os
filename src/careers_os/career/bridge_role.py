"""Bridge-role classification (Phase 2.5).

A "bridge role" (see docs/discovery.md) uses technologies I already have
strong market credibility in (PHP, Laravel, Craft CMS, WordPress — the
Phase 2.5 discovery anchors) while also moving toward the target career
direction (architecture, cloud, AI/ML, leadership, customer-facing
technical work).

Deliberately category-presence based, not keyword-frequency based — see
"avoid keyword dominance": a role mentioning "architecture" once counts
the same as one mentioning it five times. Distinct *categories* of
forward-leaning signal are what separates strong from moderate from weak,
not how many times any one word appears.
"""

from enum import Enum

from pydantic import BaseModel, Field

from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.taxonomy import SkillCategory

# Categories that count as "moving toward the target direction" when found
# in a job's own title/description — see career/data/profile.yaml for the
# longer-term direction this is anchored to.
BRIDGE_SIGNAL_CATEGORIES = (
    SkillCategory.ARCHITECTURE,
    SkillCategory.CLOUD,
    SkillCategory.AI_ML,
    SkillCategory.LEADERSHIP,
    SkillCategory.CUSTOMER_FACING,
)

COMMODITY_WORK_PHRASES = (
    "content update",  # also matches "content updates" (substring)
    "theme modification",
    "theme customization",
    "landing page production",
    "landing pages production",
    "data entry",
    "plugin configuration",
    "plugin setup",
    "basic maintenance",
    "site maintenance",
    "template edits",
    "template editing",
    "production support only",
    "marketing support",
)


class BridgeClassification(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NONE = "none"
    UNKNOWN = "unknown"


class BridgeRoleResult(BaseModel):
    classification: BridgeClassification
    reason: str
    signals: list[str] = Field(default_factory=list)
    commodity_signals: list[str] = Field(default_factory=list)


def _find_commodity_phrases(text_lower: str) -> list[str]:
    return [p for p in COMMODITY_WORK_PHRASES if p in text_lower]


def classify_bridge_role(
    title: str, description: str | None, taxonomy: SkillsTaxonomy | None = None
) -> BridgeRoleResult:
    taxonomy = taxonomy or SkillsTaxonomy.load()
    text = f"{title}\n{description or ''}"

    if not description or len(description.strip()) < 40:
        return BridgeRoleResult(
            classification=BridgeClassification.UNKNOWN,
            reason="Not enough description text to judge whether this role moves toward "
            "the target career direction.",
        )

    matched_skills = taxonomy.find_in_text(text)
    matched_categories = {taxonomy.skills[k].category for k in matched_skills if k in taxonomy.skills}

    anchor_categories = {SkillCategory.PROGRAMMING_LANGUAGE, SkillCategory.FRAMEWORK}
    has_anchor = bool(matched_categories & anchor_categories)

    bridge_categories_present = matched_categories & set(BRIDGE_SIGNAL_CATEGORIES)
    bridge_signals = sorted(
        taxonomy.display_name(k)
        for k in matched_skills
        if k in taxonomy.skills and taxonomy.skills[k].category in bridge_categories_present
    )

    commodity_hits = _find_commodity_phrases(text.lower())

    if not has_anchor:
        return BridgeRoleResult(
            classification=BridgeClassification.NONE,
            reason="No evidence of the discovery-anchor technologies (PHP/Laravel/Craft "
            "CMS/WordPress) in this role's own text.",
            signals=bridge_signals,
            commodity_signals=commodity_hits,
        )

    category_count = len(bridge_categories_present)
    if category_count >= 3:
        classification = BridgeClassification.STRONG
        reason = (
            f"Uses discovery-anchor technology while adding {category_count} distinct "
            f"forward-direction signal categories: {', '.join(sorted(c.value for c in bridge_categories_present))}."
        )
    elif category_count >= 1:
        classification = BridgeClassification.MODERATE
        reason = (
            f"Uses discovery-anchor technology with some forward-direction signal "
            f"({', '.join(sorted(c.value for c in bridge_categories_present))}), but not broadly."
        )
    else:
        classification = BridgeClassification.WEAK
        reason = "Uses discovery-anchor technology only — no architecture/cloud/AI/leadership/" \
            "customer-facing signal found in the role's own text."
        if commodity_hits:
            reason += f" Signals of commodity production work: {', '.join(commodity_hits)}."

    return BridgeRoleResult(
        classification=classification,
        reason=reason,
        signals=bridge_signals,
        commodity_signals=commodity_hits,
    )
