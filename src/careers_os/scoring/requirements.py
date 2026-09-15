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
from careers_os.domain.requirements import JobRequirement, RequirementImportance
from careers_os.domain.taxonomy import SkillCategory

_PREFERRED_SECTION_MARKERS = (
    "preferred qualifications",
    "nice to have",
    "nice-to-have",
    "bonus points",
    "bonus if",
    "preferred skills",
)

# A "minimum requirements"-style heading is about as unambiguous a signal
# as free text gives us that what follows is a hard gate, not a
# descriptive nice-to-have — see domain/requirements.py's
# RequirementImportance and docs/eligibility.md.
_HARD_SECTION_MARKERS = (
    "minimum requirements",
    "minimum qualifications",
    "required qualifications",
    "basic qualifications",
)

_YEARS_PATTERN = re.compile(r"(\d+)\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
_CONTEXT_WINDOW = 60

# A section marker phrase can appear twice: once as a real heading
# ("Minimum requirements\n6+ years...") and once inside ordinary prose
# referencing the concept ("The preferred qualifications are a bonus, not
# a requirement."). Real postings do this (observed on Stripe's own
# boilerplate) — a heading is followed directly by content, prose is
# followed by a linking verb. Skip occurrences that look like prose.
_PROSE_CONTINUATION_WORDS = frozenset(
    {"are", "is", "were", "was", "will", "would", "should", "must", "the", "that", "which", "listed", "section"}
)


def _section_start(text_lower: str, markers: tuple[str, ...]) -> Optional[int]:
    candidates: list[tuple[int, str]] = []
    for marker in markers:
        start = 0
        while True:
            pos = text_lower.find(marker, start)
            if pos == -1:
                break
            candidates.append((pos, marker))
            start = pos + 1
    if not candidates:
        return None
    candidates.sort()
    for pos, marker in candidates:
        after = text_lower[pos + len(marker) : pos + len(marker) + 20].strip()
        first_word = after.split(" ", 1)[0].strip(".,:") if after else ""
        if first_word not in _PROSE_CONTINUATION_WORDS:
            return pos
    return candidates[0][0]  # nothing looked heading-like; fall back to the first occurrence


def _preferred_section_start(text_lower: str) -> Optional[int]:
    return _section_start(text_lower, _PREFERRED_SECTION_MARKERS)


def extract_requirements(
    job: NormalizedJob, taxonomy: Optional[SkillsTaxonomy] = None
) -> list[JobRequirement]:
    taxonomy = taxonomy or SkillsTaxonomy.load()
    text = f"{job.title}\n{job.description or ''}"
    text_lower = text.lower()
    preferred_start = _preferred_section_start(text_lower)
    hard_start = _section_start(text_lower, _HARD_SECTION_MARKERS)

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
            id=key,
            category=definition.category,
            canonical_skill=key,
            text=definition.display_name,
            is_preferred=is_preferred,
            # A bare skill mention inside a "Minimum requirements" section
            # isn't itself a hard numeric gate — it only becomes
            # HARD_REQUIRED once a min_years threshold attaches to it,
            # below. Until then it's just REQUIRED (still not preferred).
            importance=RequirementImportance.PREFERRED if is_preferred else RequirementImportance.REQUIRED,
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
        years_in_hard_section = hard_start is not None and match.start() >= hard_start
        years_in_preferred_section = preferred_start is not None and match.start() >= preferred_start

        # Attach only to the single *nearest* skill mention in the window,
        # not every skill that happens to occur somewhere in it — on short
        # or dense text a fixed-size window can otherwise span multiple
        # unrelated "X+ years of SKILL" clauses at once.
        nearest_key: Optional[str] = None
        nearest_distance: Optional[int] = None
        for key, definition in taxonomy.skills.items():
            if key not in requirements:
                continue
            for alias in definition.aliases:
                alias_pos = context_lower.find(alias.lower())
                if alias_pos == -1:
                    continue
                distance = abs((start + alias_pos) - match.start())
                if nearest_distance is None or distance < nearest_distance:
                    nearest_distance = distance
                    nearest_key = key

        attached = nearest_key is not None
        if attached:
            existing = requirements[nearest_key]
            if existing.min_years is None or years > existing.min_years:
                updates: dict = {"min_years": years}
                if years_in_hard_section and not years_in_preferred_section:
                    updates["importance"] = RequirementImportance.HARD_REQUIRED
                requirements[nearest_key] = existing.model_copy(update=updates)

        if not attached:
            generic_years.append(
                JobRequirement(
                    id=f"years_{years}_{match.start()}",
                    category=SkillCategory.RESPONSIBILITY,
                    canonical_skill=None,
                    text=f"{years}+ years of relevant experience",
                    raw_context=context.strip(),
                    min_years=years,
                    importance=(
                        RequirementImportance.HARD_REQUIRED
                        if years_in_hard_section and not years_in_preferred_section
                        else RequirementImportance.REQUIRED
                    ),
                )
            )

    return list(requirements.values()) + generic_years
