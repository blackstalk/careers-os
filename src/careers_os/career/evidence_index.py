"""In-memory index over imported career evidence.

Lives in `career/` (not `scoring/`) because it's a view over evidence
data — `storage/resume_repository.py` builds one directly from the
database — not a scoring decision. `scoring/evidence_matcher.py` consumes
it but doesn't own it, keeping the dependency direction
domain -> career -> scoring -> storage/ingestion/cli consistent (see
docs/architecture.md's layer list).
"""

from collections import defaultdict
from dataclasses import dataclass, field

from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.evidence import Evidence
from careers_os.domain.resume import CareerRole


@dataclass
class EvidenceIndex:
    """Index over all imported evidence + career roles, built once per
    scoring run and passed into the matcher. Kept intentionally simple (a
    dict of lists) — there's no need for a real search index at this data
    scale.
    """

    evidence: list[Evidence]
    career_roles: list[CareerRole]
    taxonomy: SkillsTaxonomy = field(default_factory=SkillsTaxonomy.load)
    _by_skill: dict[str, list[Evidence]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        by_skill: dict[str, list[Evidence]] = defaultdict(list)
        for item in self.evidence:
            for skill in item.canonical_skills:
                by_skill[skill].append(item)
        self._by_skill = dict(by_skill)

    def evidence_for_skill(self, skill: str) -> list[Evidence]:
        return self._by_skill.get(skill, [])

    def has_any_evidence(self) -> bool:
        return len(self.evidence) > 0
