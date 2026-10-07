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
    # Ashby-hosted postings (e.g. Ramp) commonly head their required list
    # "WHAT YOU NEED". A skill there still only becomes HARD_REQUIRED when
    # an explicit "N+ years" threshold attaches to it.
    "what you need",
    "what you'll need",
    "what you’ll need",
)

# Headings that open a requirements/qualifications list. Broader than
# _HARD_SECTION_MARKERS on purpose: these mark what the posting treats as
# core (Phase 5), while _HARD_SECTION_MARKERS still governs the stricter
# HARD_REQUIRED gate.
_CORE_SECTION_MARKERS = _HARD_SECTION_MARKERS + (
    "requirements", "qualifications", "what you'll bring", "what you will bring",
    "what you bring", "who you are", "about you", "skills and experience",
    "experience required", "must have", "what we're looking for", "what we are looking for",
    # Phase 5.1: a responsibilities list establishes what the job is just
    # as plainly as a requirements list. Bare "you will"/"you have" are
    # deliberately excluded: they occur mid-prose and would mark the rest
    # of the posting as core.
    "responsibilities", "what you'll do", "what you will do", "what you'll be doing",
    "in this role you will", "your impact", "the role",
)

# A requirement-section line reads as core when it is phrased as one.
_CORE_PHRASING = re.compile(
    r"\d+\+?\s*(?:years?|yrs?)|experience (?:with|in|building|developing)|proficien\w+|"
    r"strong (?:knowledge|experience|background|command)|expertise (?:with|in)|"
    r"deep (?:knowledge|experience)|must have|solid (?:experience|understanding)",
    re.IGNORECASE,
)
_MAX_UNMATCHED_CORE = 6

_YEARS_PATTERN = re.compile(r"(\d+)\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
_CONTEXT_WINDOW = 60

# A section marker phrase can appear twice: once as a real heading
# ("Minimum requirements\n6+ years...") and once inside ordinary prose
# referencing the concept ("The preferred qualifications are a bonus, not
# a requirement."). Real postings do this (observed on Stripe's own
# boilerplate) — a heading is followed directly by content, prose is
# followed by a linking verb. Skip occurrences that look like prose.
_PROSE_CONTINUATION_WORDS = frozenset(
    {"are", "is", "were", "was", "will", "would", "should", "must", "the", "that", "which", "listed", "section", "to"}
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
    # Nothing looked heading-like. The original markers keep the historical
    # fall-back-to-first-occurrence behavior; the looser "what you need"
    # phrasings are common in ordinary prose, so they don't.
    for pos, marker in candidates:
        if not marker.startswith("what you"):
            return pos
    return None


def _preferred_section_start(text_lower: str) -> Optional[int]:
    return _section_start(text_lower, _PREFERRED_SECTION_MARKERS)


def _core_span(text_lower: str, preferred_start: Optional[int]) -> Optional[tuple[int, int]]:
    """Character span of the posting's requirements/qualifications list:
    from the first such heading to the preferred-qualifications heading
    (or the end). Everything inside it is what the employer presents as
    core."""
    start = _section_start(text_lower, _CORE_SECTION_MARKERS)
    if start is None:
        return None
    end = preferred_start if preferred_start is not None and preferred_start > start else len(text_lower)
    return start, end


def unmatched_core_requirement_lines(
    job: NormalizedJob, taxonomy: Optional[SkillsTaxonomy] = None
) -> list[str]:
    """Requirement-section lines phrased as core requirements that matched
    no taxonomy skill (Phase 5). Conservative by construction: only lines
    inside the requirements section, only ones using requirement
    phrasing, and only ones containing no recognized skill alias at all.
    These are reported as uncertainty, never scored as gaps."""
    taxonomy = taxonomy or SkillsTaxonomy.load()
    text = f"{job.title}\n{job.description or ''}"
    text_lower = text.lower()
    span = _core_span(text_lower, _preferred_section_start(text_lower))
    if span is None:
        return []
    start, end = span

    aliases = [a.lower().strip() for d in taxonomy.skills.values() for a in d.aliases]
    lines: list[str] = []
    for raw_line in re.split(r"[\n\r]+|(?<=[.;])\s{1,}", text[start:end]):
        line = " ".join(raw_line.split()).strip(" -•*\t")
        if not (25 <= len(line) <= 220) or not _CORE_PHRASING.search(line):
            continue
        low = line.lower()
        if any(alias in low for alias in aliases):
            continue
        lines.append(line)
        if len(lines) >= _MAX_UNMATCHED_CORE:
            break
    return lines


def extract_requirements(
    job: NormalizedJob, taxonomy: Optional[SkillsTaxonomy] = None
) -> list[JobRequirement]:
    taxonomy = taxonomy or SkillsTaxonomy.load()
    text = f"{job.title}\n{job.description or ''}"
    text_lower = text.lower()
    preferred_start = _preferred_section_start(text_lower)
    hard_start = _section_start(text_lower, _HARD_SECTION_MARKERS)
    core_span = _core_span(text_lower, preferred_start)
    # Space-padded around punctuation: a title like "Database Automation
    # (Go)" or "Senior Engineer, React/Next.js" must still mark those
    # technologies core. This only affects whether an already-extracted
    # requirement counts as core, never what gets extracted (Phase 5.1).
    title_lower = " " + re.sub(r"[^a-z0-9.+#]+", " ", (job.title or "").lower()) + " "

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
        in_core_section = core_span is not None and core_span[0] <= first_position < core_span[1]
        in_title = any(alias.lower() in title_lower for alias in definition.aliases)
        requirements[key] = JobRequirement(
            is_core=bool(not is_preferred and (in_core_section or in_title)),
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
                # A threshold inside the required section belongs to a
                # skill named inside that section, never to one mentioned
                # in the prose just above the heading.
                if years_in_hard_section and start + alias_pos < hard_start:
                    continue
                distance = abs((start + alias_pos) - match.start())
                if nearest_distance is None or distance < nearest_distance:
                    nearest_distance = distance
                    nearest_key = key

        attached = nearest_key is not None
        if attached:
            existing = requirements[nearest_key]
            if existing.min_years is None or years > existing.min_years:
                updates: dict = {"min_years": years, "is_core": not years_in_preferred_section}
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
                    is_core=not years_in_preferred_section,
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
