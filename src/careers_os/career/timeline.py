"""Career chronology: interval merging and years-of-experience estimation.

The one rule this module exists to enforce: overlapping/concurrent roles
must never be summed as if they were sequential. Two roles covering the
same 3-year window contribute 3 years, not 6 — see
docs/evidence-model.md#years-of-experience.
"""

from datetime import date
from typing import Optional

from careers_os.domain.evidence import Evidence
from careers_os.domain.experience import YearsEstimate
from careers_os.domain.resume import CareerRole

DAYS_PER_YEAR = 365.25


def merge_intervals(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Merge overlapping/adjacent (start, end) date ranges."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda iv: iv[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def total_years(intervals: list[tuple[date, date]]) -> float:
    merged = merge_intervals(intervals)
    total_days = sum((end - start).days for start, end in merged)
    return round(total_days / DAYS_PER_YEAR, 2)


def humanize_years(years: float) -> str:
    """Deliberately bucketed, not false-precision. See module docstring
    reference in docs/evidence-model.md.
    """
    if years < 1:
        return "under 1 year"
    if years < 3:
        return "1-2 years"
    if years < 6:
        return "3-5 years"
    if years < 10:
        return "6-9 years"
    return f"{int(years)}+ years"


def total_career_span_years(career_roles: list[CareerRole], as_of: Optional[date] = None) -> float:
    as_of = as_of or date.today()
    intervals = [(role.start_date, role.end_date or as_of) for role in career_roles]
    return total_years(intervals)


def years_for_skill(
    canonical_skill: str,
    evidence_items: list[Evidence],
    career_roles: list[CareerRole],
    *,
    as_of: Optional[date] = None,
) -> YearsEstimate:
    """Estimate how long a specific skill has been evidenced.

    Only counts evidence with an actual date range (tied to a CareerRole).
    Evidence with no date range (e.g. a top-level "Technical Skills" table
    entry not attributed to one job) is a real but weaker signal — it
    contributes to confidence only if no dated evidence exists at all, and
    even then the estimate is honest about being unanchored.
    """
    as_of = as_of or date.today()
    role_by_id = {role.id: role for role in career_roles}

    dated_intervals: list[tuple[date, date]] = []
    supporting_companies: set[str] = set()
    undated_hit = False

    for evidence in evidence_items:
        if canonical_skill not in evidence.canonical_skills:
            continue
        if evidence.start_date is not None:
            end = evidence.end_date or as_of
            dated_intervals.append((evidence.start_date, end))
            if evidence.provenance.company:
                supporting_companies.add(evidence.provenance.company)
            elif evidence.provenance.career_role_id and evidence.provenance.career_role_id in role_by_id:
                supporting_companies.add(role_by_id[evidence.provenance.career_role_id].company)
        else:
            undated_hit = True

    if dated_intervals:
        years = total_years(dated_intervals)
        confidence = min(0.95, 0.55 + 0.15 * len(dated_intervals))
        return YearsEstimate(
            skill=canonical_skill,
            display_years=humanize_years(years),
            numeric_years=years,
            confidence=round(confidence, 2),
            supporting_companies=sorted(supporting_companies),
        )

    if undated_hit:
        # Skill appears only in an undated, career-wide inventory (e.g. a
        # resume's skills table) — real signal, but we can't anchor it to a
        # time range, so confidence stays low and years stays honestly 0.
        return YearsEstimate(
            skill=canonical_skill,
            display_years="unknown (mentioned but not tied to a dated role)",
            numeric_years=0.0,
            confidence=0.15,
            supporting_companies=[],
        )

    return YearsEstimate(
        skill=canonical_skill,
        display_years="no evidence found",
        numeric_years=0.0,
        confidence=0.0,
        supporting_companies=[],
    )
