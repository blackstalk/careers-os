"""Optional AI semantic evaluation layer.

Deterministic scoring (deterministic.py) never depends on this module.
This exists purely as an optional refinement: if an Anthropic API key is
configured, ask an LLM for the kind of nuanced judgment keyword-matching
can't do (e.g. "is this role *actually* architectural, or just titled
that?"), and use it to adjust the role_fit / career_direction_fit
components. Any failure here — missing key, missing package, network
error, malformed response — must degrade to "skip AI evaluation", never
break ingestion or scoring.
"""

import importlib.util
import json
import logging
import os
from typing import Optional

from careers_os.career.profile import CareerProfile
from careers_os.domain.job import NormalizedJob
from careers_os.domain.scoring import CareerFitResult, FitComponent
from careers_os.scoring.deterministic import forward_direction_matches

logger = logging.getLogger("careers_os.scoring.ai")

MODEL = "claude-sonnet-5"
# Part of the stored-review cache key (storage AIRefinementRecord): bump
# whenever the prompt or its inputs change meaning, so old reviews are
# not reused for a different question.
PROMPT_VERSION = "finalist-review-v3"

_PROMPT_TEMPLATE = """\
You are the final reviewer for a job-fit system. A deterministic filter \
already ranked this job among its top matches; your job is to catch the cases it \
can't: judge whether the job's *actual responsibilities* (not its title or \
buzzwords) fit the candidate below. Be skeptical of title inflation, of \
roles whose primary technology or domain is unrelated to the candidate's \
(mobile, frontend-only, embedded, enterprise-product specialists, data \
science/research), and of roles dominated by meetings, account management, \
or pre-sales rather than hands-on building. Score low when that's the case. \
Customer contact is optional: a hands-on internal or platform engineering \
role can fit fully. Keep every text field short.

Target career profile:
{narrative}
Target roles: {target_roles}

Job title: {title}
Job description:
{description}

The deterministic matcher found the candidate has real evidence for these \
requirements of this posting: {matched}. Judge the role, not the resume — \
but do not treat something in that list as a gap.

Respond with ONLY a JSON object of this exact shape, no prose outside it:
{{
  "role_fit_score": <0.0-1.0>,
  "role_fit_reason": "<one or two sentences>",
  "role_fit_evidence": ["<short phrase from the listing>", ...],
  "career_direction_score": <0.0-1.0>,
  "career_direction_reason": "<one or two sentences>",
  "strengths": ["<short phrase>", ... at most 3],
  "risks": ["<short phrase>", ... at most 3]
}}
"""


def is_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def readiness() -> tuple[bool, str]:
    """Whether a review can actually be made, and why not — checked once
    per run so a missing key or package is reported once, not per job."""
    if not is_available():
        return False, "ANTHROPIC_API_KEY is not set"
    if importlib.util.find_spec("anthropic") is None:
        return False, "the 'anthropic' package is not installed (pip install -e '.[ai]')"
    return True, "ready"


def evaluate(job: NormalizedJob, profile: CareerProfile, matched: str = "") -> Optional[dict]:
    """Return a raw dict of AI-derived fields, or None if unavailable/failed."""
    if not is_available():
        return None
    if not job.description:
        logger.info("scoring.ai.skipped_no_description", extra={"job_id": job.id})
        return None

    try:
        import anthropic  # type: ignore
    except ImportError:
        logger.info("scoring.ai.skipped_package_missing")
        return None

    prompt = _PROMPT_TEMPLATE.format(
        narrative=profile.narrative,
        target_roles=", ".join(profile.target_roles),
        title=job.title,
        description=job.description[:4000],
        matched=matched or "(none recorded)",
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        return _parse_json_object(text)
    except Exception as exc:  # noqa: BLE001 - AI eval must never take down scoring
        logger.warning("scoring.ai.failed", extra={"error": str(exc)})
        return None


def _parse_json_object(text: str) -> Optional[dict]:
    """The prompt asks for bare JSON, but tolerate a code fence or a
    sentence around it: parse the outermost {...}."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    parsed = json.loads(text[start : end + 1])
    return parsed if isinstance(parsed, dict) else None


def apply_ai_evaluation(result: CareerFitResult, ai_result: dict) -> CareerFitResult:
    """Merge AI findings into a deterministic CareerFitResult, if present."""
    updated = result.model_copy(deep=True)

    if "role_fit_score" in ai_result:
        updated.role_fit = FitComponent(
            score=float(ai_result["role_fit_score"]),
            reason=ai_result.get("role_fit_reason", updated.role_fit.reason),
            evidence=ai_result.get("role_fit_evidence", updated.role_fit.evidence),
            confidence=max(updated.role_fit.confidence, 0.7),
        )
    if "career_direction_score" in ai_result:
        ai_score = float(ai_result["career_direction_score"])
        ai_reason = ai_result.get("career_direction_reason", updated.career_direction_fit.reason)
        if forward_direction_matches(updated.experience_detail):
            # Evidence-grounded score: the AI judges whether the role is
            # *really* in the target direction (title inflation), which
            # can only lower it — it never sees candidate evidence, so it
            # must never raise a score above what that evidence supports.
            deterministic_score = updated.career_direction_fit.score
            if ai_score < deterministic_score:
                # Only call it "lowered" when the AI materially disagreed —
                # 0.85 against a deterministic 1.0 is agreement, and saying
                # otherwise misreads the alert (Phase 4.4).
                verb = "AI review lowered this" if deterministic_score - ai_score >= 0.1 else "AI review"
                updated.career_direction_fit = FitComponent(
                    score=ai_score,
                    reason=f"{updated.career_direction_fit.reason} {verb}: {ai_reason}",
                    evidence=updated.career_direction_fit.evidence,
                    confidence=updated.career_direction_fit.confidence,
                )
        else:
            updated.career_direction_fit = FitComponent(
                score=ai_score,
                reason=ai_reason,
                evidence=updated.career_direction_fit.evidence,
                confidence=max(updated.career_direction_fit.confidence, 0.7),
            )
    updated.ai_evaluation_included = True
    return updated
