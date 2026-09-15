"""Deterministic job-requirement extraction.

No LLM required — this is plain keyword/regex extraction against the
shared skills taxonomy (career/data/skills_taxonomy.yaml). It is
deliberately conservative: an unrecognized responsibility sentence is left
unextracted rather than guessed at from arbitrary NLP heuristics.
Interpreting ambiguous responsibility prose is explicitly left to the
optional AI layer (see docs/evidence-model.md), not attempted here.
"""

import re
from typing import Optional

from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.job import NormalizedJob
from careers_os.domain.requirements import JobRequirement
from careers_os.domain.taxonomy import SkillCategory

_PREFERRED_SECTION_MARKERS = (
    "preferred qualifications",
    "nice to have",
    "nice-to-have",
    "bonus points",
    "bonus if",
    "preferred skills",
)

_YEARS_PATTERN = re.compile(r"(\d+)\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
_CONTEXT_WINDOW = 60


def _preferred_section_start(text_lower: str) -> Optional[int]:
    positions = [text_lower.find(marker) for marker in _PREFERRED_SECTION_MARKERS]
    positions = [p for p in positions if p != -1]
    return min(positions) if positions else None


def extract_requirements(
    job: NormalizedJob, taxonomy: Optional[SkillsTaxonomy] = None
) -> list[JobRequirement]:
    taxonomy = taxonomy or SkillsTaxonomy.load()
    text = f"{job.title}\n{job.description or ''}"
    text_lower = text.lower()
    preferred_start = _preferred_section_start(text_lower)

    requirements: dict[str, JobRequirement] = {}

    for key, definition in taxonomy.skills.items():
        first_position = None
        for alias in definition.aliases:
            pos = text_lower.find(alias.lower())
            if pos != -1 and (first_position is None or pos < first_position):
                first_position = pos
        if first_position is None:
            continue

        is_preferred = preferred_start is not None and first_position >= preferred_start
        requirements[key] = JobRequirement(
            category=definition.category,
            canonical_skill=key,
            text=definition.display_name,
            is_preferred=is_preferred,
        )

    # Years-of-experience mentions, attached to the nearest matched skill
    # within a small context window; otherwise recorded as a standalone
    # generic experience requirement.
    generic_years: list[JobRequirement] = []
    for match in _YEARS_PATTERN.finditer(text):
        years = int(match.group(1))
        start = max(0, match.start() - _CONTEXT_WINDOW)
        end = min(len(text), match.end() + _CONTEXT_WINDOW)
        context = text[start:end]
        context_lower = context.lower()

        attached = False
        for key, definition in taxonomy.skills.items():
            if key not in requirements:
                continue
            if any(alias.lower() in context_lower for alias in definition.aliases):
                existing = requirements[key]
                if existing.min_years is None or years > existing.min_years:
                    requirements[key] = existing.model_copy(update={"min_years": years})
                attached = True

        if not attached:
            generic_years.append(
                JobRequirement(
                    category=SkillCategory.RESPONSIBILITY,
                    canonical_skill=None,
                    text=f"{years}+ years of relevant experience",
                    raw_context=context.strip(),
                    min_years=years,
                )
            )

    return list(requirements.values()) + generic_years
