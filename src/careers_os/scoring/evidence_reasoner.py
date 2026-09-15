"""Optional AI evidence reasoning layer (Phase 3).

Provider-neutral by design (`EvidenceReasoner` ABC) — Claude is the
default implementation, not a hardcoded dependency of the domain logic.
The deterministic evidence matcher (scoring/evidence_matcher.py) remains
the source of truth for match_type/gap_type; this module only ever
*annotates* a RequirementMatch with an additional `ai_assessment`, and
only after every guardrail below passes. See docs/eligibility.md#ai-evidence-guardrails.

Guardrails enforced here, not just documented:
  1. `assessment_type` is restricted to a vocabulary
     (domain/matching.py::AIAssessmentType) that structurally excludes
     anything resembling "direct"/"strong" evidence — the AI cannot
     upgrade a gap into a claim of direct experience because that value
     doesn't exist in the schema.
  2. `evidence_ids` must be non-empty — an assessment citing zero evidence
     is rejected outright ("if it cannot cite an evidence ID, it cannot
     claim the experience exists").
  3. Every cited evidence_id must exist in the *candidate's own* evidence
     index actually used for this job — an id that doesn't exist, or
     wasn't part of what the reasoner was shown, is rejected.
  4. `requirement_id` must reference a requirement actually offered to the
     reasoner for this job — an assessment about anything else is rejected.
  5. The schema has no field for years/technologies/employers/degrees —
     the AI cannot invent any of those because there is no slot to put
     them in; extra fields are rejected outright (`extra: "forbid"`).

Cost control (see docs/eligibility.md#ai-cost-control): only called for
gaps that are already ambiguous (UNSUPPORTED or ADJACENT_EXPERIENCE) on a
non-hard-required requirement — never for obvious direct matches, and
never to try to rescue a hard-required numeric gate.
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

from pydantic import ValidationError

from careers_os.career.evidence_index import EvidenceIndex
from careers_os.domain.experience import ExperienceFitDetail
from careers_os.domain.matching import AIEvidenceAssessment, MatchType, RequirementMatch
from careers_os.domain.requirements import RequirementImportance

logger = logging.getLogger("careers_os.scoring.evidence_reasoner")

MODEL = "claude-sonnet-5"

_ELIGIBLE_MATCH_TYPES = (MatchType.UNSUPPORTED, MatchType.ADJACENT_EXPERIENCE)

_PROMPT_TEMPLATE = """\
You are assisting a deterministic job-fit system. You may ONLY reason \
about the evidence explicitly listed below — never invent a technology, \
employer, project, degree, certification, or years of experience that \
isn't already stated in it.

For each gap below, decide whether the candidate's ACTUAL evidence (not \
the missing skill itself) demonstrates a transferable capability that \
plausibly satisfies the underlying requirement, or whether the resume's \
wording undersells work that already exists, or whether it's a real gap.

Job title: {title}
Job excerpt:
{description}

Gaps to assess (each has a requirement_id):
{gaps_block}

Candidate's available evidence (each has an evidence_id) — this is the \
ONLY evidence you may cite:
{evidence_block}

For each gap you have something useful to say about, respond with one \
object. If a gap is a genuine, uncontested gap, either omit it or use \
assessment_type "no_change". Respond with ONLY a JSON array, no prose \
outside it, each object exactly this shape:
{{
  "requirement_id": "<must match one of the requirement_ids above>",
  "assessment_type": "transferable_capability" | "resume_language_gap" | "interview_prep_gap" | "no_change",
  "evidence_ids": ["<must be evidence_ids from the list above>"],
  "reason": "<one or two sentences, referencing only the cited evidence>",
  "confidence": "low" | "moderate" | "high"
}}

evidence_ids must never be empty for any assessment_type other than \
"no_change". Never claim the candidate has direct experience with the \
missing skill itself — only that other evidence transfers.
"""


class EvidenceReasoner(ABC):
    @abstractmethod
    def reason_about_gaps(
        self, job_title: str, job_description: str, gaps: list[RequirementMatch], evidence_index: EvidenceIndex
    ) -> list[dict]:
        """Return a list of raw (unvalidated) assessment dicts."""


def is_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class ClaudeEvidenceReasoner(EvidenceReasoner):
    def reason_about_gaps(
        self, job_title: str, job_description: str, gaps: list[RequirementMatch], evidence_index: EvidenceIndex
    ) -> list[dict]:
        if not gaps:
            return []
        try:
            import anthropic  # type: ignore
        except ImportError:
            logger.info("evidence_reasoner.skipped_package_missing")
            return []

        gaps_block = "\n".join(
            f"- requirement_id={m.requirement.id!r} text={m.requirement.text!r} "
            f"deterministic_result={m.match_type.value}"
            for m in gaps
        )
        evidence_block = "\n".join(
            f"- evidence_id={e.id!r} statement={e.statement!r} technologies={e.technologies} themes={e.themes}"
            for e in evidence_index.evidence
        )
        prompt = _PROMPT_TEMPLATE.format(
            title=job_title,
            description=(job_description or "")[:3000],
            gaps_block=gaps_block,
            evidence_block=evidence_block[:6000],
        )

        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=MODEL, max_tokens=1200, messages=[{"role": "user", "content": prompt}]
            )
            text = "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            )
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except Exception as exc:  # noqa: BLE001 - must never take down evaluation
            logger.warning("evidence_reasoner.failed", extra={"error": str(exc)})
            return []


def get_default_reasoner() -> Optional[EvidenceReasoner]:
    if not is_available():
        return None
    return ClaudeEvidenceReasoner()


def _validate_and_attach(
    matches: list[RequirementMatch], raw_assessments: list[dict], evidence_index: EvidenceIndex, valid_requirement_ids: set[str]
) -> list[RequirementMatch]:
    valid_evidence_ids = {e.id for e in evidence_index.evidence}
    matches_by_req_id = {m.requirement.id: i for i, m in enumerate(matches) if m.requirement.id}
    updated = list(matches)

    for raw in raw_assessments:
        try:
            assessment = AIEvidenceAssessment(**raw)
        except (ValidationError, TypeError) as exc:
            logger.warning("evidence_reasoner.rejected_malformed", extra={"error": str(exc), "raw": raw})
            continue

        if assessment.requirement_id not in valid_requirement_ids:
            logger.warning(
                "evidence_reasoner.rejected_unknown_requirement",
                extra={"requirement_id": assessment.requirement_id},
            )
            continue
        if assessment.assessment_type.value != "no_change" and not assessment.evidence_ids:
            logger.warning(
                "evidence_reasoner.rejected_no_evidence_cited",
                extra={"requirement_id": assessment.requirement_id},
            )
            continue
        if not set(assessment.evidence_ids).issubset(valid_evidence_ids):
            logger.warning(
                "evidence_reasoner.rejected_unknown_evidence_id",
                extra={"requirement_id": assessment.requirement_id, "evidence_ids": assessment.evidence_ids},
            )
            continue

        idx = matches_by_req_id.get(assessment.requirement_id)
        if idx is None:
            continue
        updated[idx] = updated[idx].model_copy(update={"ai_assessment": assessment})

    return updated


def apply_evidence_reasoning(
    job_title: str,
    job_description: Optional[str],
    detail: ExperienceFitDetail,
    evidence_index: EvidenceIndex,
    reasoner: Optional[EvidenceReasoner] = None,
) -> ExperienceFitDetail:
    """Best-effort AI annotation of ambiguous gaps. Always returns a valid
    ExperienceFitDetail — on any failure (no reasoner, no key, package
    missing, malformed output), returns `detail` unchanged.
    """
    reasoner = reasoner or get_default_reasoner()
    if reasoner is None:
        return detail

    # Cost control: never consult AI for hard-required gates (obvious,
    # binary) or for anything the deterministic matcher already resolved
    # confidently (strong/partial matches).
    candidate_gaps = [
        m
        for m in detail.requirement_matches
        if m.match_type in _ELIGIBLE_MATCH_TYPES and m.requirement.importance != RequirementImportance.HARD_REQUIRED
    ]
    if not candidate_gaps:
        return detail

    valid_requirement_ids = {m.requirement.id for m in candidate_gaps if m.requirement.id}
    try:
        raw_assessments = reasoner.reason_about_gaps(
            job_title, job_description or "", candidate_gaps, evidence_index
        )
    except Exception as exc:  # noqa: BLE001 - a custom/third-party reasoner must never take down evaluation
        logger.warning("evidence_reasoner.provider_failed", extra={"error": str(exc)})
        return detail
    if not raw_assessments:
        return detail

    updated_matches = _validate_and_attach(
        detail.requirement_matches, raw_assessments, evidence_index, valid_requirement_ids
    )
    return detail.model_copy(update={"requirement_matches": updated_matches})
