import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from careers_os.career.resume_import import ResumeImportResult
from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.resume import CareerRole
from careers_os.career.evidence_index import EvidenceIndex
from careers_os.storage.db import (
    CareerRoleRecord,
    EvidenceRecord,
    ProjectEvidenceRecord,
    ResumeVariantRecord,
    RoleFramingRecord,
)

logger = logging.getLogger("careers_os.storage")


@dataclass
class ImportSummary:
    variant_slug: str
    career_roles_new: int
    career_roles_updated: int
    role_framings: int
    project_evidence: int
    evidence: int
    warnings: list[str]


class ResumeRepository:
    """Persists imported resume variants, career roles, framings, and
    evidence. Career-role deduplication mirrors JobRepository's pattern
    from Phase 1: identity by key (here, CareerRole.id, which already
    encodes company+start_date — see career/resume_import.py), refresh
    fields on re-import, never duplicate rows.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def import_result(self, result: ResumeImportResult) -> ImportSummary:
        variant = result.variant
        existing_variant = self.session.get(ResumeVariantRecord, variant.slug)
        if existing_variant is None:
            self.session.add(
                ResumeVariantRecord(
                    slug=variant.slug,
                    display_name=variant.display_name,
                    positioning_title=variant.positioning_title,
                    summary=variant.summary,
                    source_file_name=variant.source_file_name,
                    source_file_hash=variant.source_file_hash,
                    imported_at=variant.imported_at,
                )
            )
        else:
            existing_variant.display_name = variant.display_name
            existing_variant.positioning_title = variant.positioning_title
            existing_variant.summary = variant.summary
            existing_variant.source_file_name = variant.source_file_name
            existing_variant.source_file_hash = variant.source_file_hash
            existing_variant.imported_at = variant.imported_at

        new_roles = 0
        updated_roles = 0
        for role in result.career_roles:
            existing_role = self.session.get(CareerRoleRecord, role.id)
            if existing_role is None:
                self.session.add(
                    CareerRoleRecord(
                        id=role.id,
                        company=role.company,
                        title=role.title,
                        location=role.location,
                        start_date=role.start_date,
                        end_date=role.end_date,
                        is_current=role.is_current,
                    )
                )
                new_roles += 1
            else:
                existing_role.title = role.title
                existing_role.location = role.location
                existing_role.end_date = role.end_date
                existing_role.is_current = role.is_current
                updated_roles += 1
        self.session.flush()

        # Re-importing a variant replaces its framings/project evidence
        # rather than accumulating duplicates.
        for framing in self.session.execute(
            select(RoleFramingRecord).where(RoleFramingRecord.variant_slug == variant.slug)
        ).scalars():
            self.session.delete(framing)
        for entry in self.session.execute(
            select(ProjectEvidenceRecord).where(ProjectEvidenceRecord.variant_slug == variant.slug)
        ).scalars():
            self.session.delete(entry)
        for ev in self.session.execute(
            select(EvidenceRecord).where(EvidenceRecord.variant_slug == variant.slug)
        ).scalars():
            self.session.delete(ev)
        self.session.flush()

        for framing in result.role_framings:
            self.session.add(
                RoleFramingRecord(
                    career_role_id=framing.career_role_id,
                    variant_slug=framing.variant_slug,
                    bullets=framing.bullets,
                    section=framing.section,
                )
            )
        for entry in result.project_evidence:
            self.session.add(
                ProjectEvidenceRecord(
                    id=entry.id,
                    variant_slug=entry.variant_slug,
                    title=entry.title,
                    role_descriptor=entry.role_descriptor,
                    context_line=entry.context_line,
                    bullets=entry.bullets,
                    related_career_role_id=entry.related_career_role_id,
                    section=entry.section,
                )
            )
        for ev in result.evidence:
            self.session.add(
                EvidenceRecord(
                    id=ev.id,
                    type=ev.type.value,
                    statement=ev.statement,
                    canonical_skills=ev.canonical_skills,
                    technologies=ev.technologies,
                    themes=ev.themes,
                    provenance=ev.provenance.model_dump(mode="json"),
                    start_date=ev.start_date,
                    end_date=ev.end_date,
                    variant_slug=variant.slug,
                )
            )

        self.session.flush()
        logger.info(
            "resume_repository.import_completed",
            extra={
                "variant": variant.slug,
                "career_roles_new": new_roles,
                "career_roles_updated": updated_roles,
                "evidence": len(result.evidence),
            },
        )
        return ImportSummary(
            variant_slug=variant.slug,
            career_roles_new=new_roles,
            career_roles_updated=updated_roles,
            role_framings=len(result.role_framings),
            project_evidence=len(result.project_evidence),
            evidence=len(result.evidence),
            warnings=result.warnings,
        )

    def list_variants(self) -> list[ResumeVariantRecord]:
        return list(self.session.execute(select(ResumeVariantRecord)).scalars().all())

    def list_career_roles(self) -> list[CareerRoleRecord]:
        stmt = select(CareerRoleRecord).order_by(CareerRoleRecord.start_date.desc())
        return list(self.session.execute(stmt).scalars().all())

    def get_role_framings(self, variant_slug: str) -> list[RoleFramingRecord]:
        stmt = select(RoleFramingRecord).where(RoleFramingRecord.variant_slug == variant_slug)
        return list(self.session.execute(stmt).scalars().all())

    def load_evidence_index(self, taxonomy: SkillsTaxonomy | None = None) -> EvidenceIndex:
        """Load everything imported so far into an in-memory EvidenceIndex
        for scoring (see scoring/evidence_matcher.py). Cheap at this data
        scale — no need for a real query layer here.
        """
        evidence_records = self.session.execute(select(EvidenceRecord)).scalars().all()
        role_records = self.session.execute(select(CareerRoleRecord)).scalars().all()

        evidence = [
            Evidence(
                id=r.id,
                type=r.type,
                statement=r.statement,
                canonical_skills=r.canonical_skills,
                technologies=r.technologies,
                themes=r.themes,
                provenance=EvidenceProvenance(**r.provenance),
                start_date=r.start_date,
                end_date=r.end_date,
            )
            for r in evidence_records
        ]
        career_roles = [
            CareerRole(
                id=r.id,
                company=r.company,
                title=r.title,
                location=r.location,
                start_date=r.start_date,
                end_date=r.end_date,
                is_current=r.is_current,
            )
            for r in role_records
        ]
        return EvidenceIndex(
            evidence=evidence, career_roles=career_roles, taxonomy=taxonomy or SkillsTaxonomy.load()
        )

    def commit(self) -> None:
        self.session.commit()
