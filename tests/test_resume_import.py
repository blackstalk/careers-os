from datetime import date
from pathlib import Path

import pytest

from careers_os.career.resume_import import import_resume_docx

FIXTURE = Path(__file__).parent / "fixtures" / "resume" / "sample_resume.docx"


@pytest.fixture
def import_result():
    return import_resume_docx(FIXTURE, "test_variant")


class TestVariantMetadata:
    def test_positioning_and_summary_extracted(self, import_result):
        variant = import_result.variant
        assert variant.slug == "test_variant"
        assert "Platform Engineer" in variant.positioning_title
        assert "Fictional platform engineer" in variant.summary

    def test_file_hash_is_deterministic(self, import_result):
        again = import_resume_docx(FIXTURE, "test_variant")
        assert import_result.variant.source_file_hash == again.variant.source_file_hash

    def test_no_warnings_for_well_formed_fixture(self, import_result):
        assert import_result.warnings == []


class TestCareerRoles:
    def test_all_roles_extracted_with_correct_dates(self, import_result):
        roles_by_company = {r.company: r for r in import_result.career_roles}
        assert set(roles_by_company) == {"Acme Corp", "Side Consulting LLC", "Old Job Inc"}
        acme = roles_by_company["Acme Corp"]
        assert acme.start_date == date(2020, 6, 1)
        assert acme.end_date is None
        assert acme.is_current is True

    def test_overlapping_roles_both_preserved_not_merged_at_import_time(self, import_result):
        # Acme Corp (2020-Present) and Side Consulting LLC (2020-2022)
        # genuinely overlap — the importer must keep both as distinct
        # CareerRole records; only timeline.py's years-of-experience math
        # is responsible for not double-counting them.
        roles_by_company = {r.company: r for r in import_result.career_roles}
        assert roles_by_company["Acme Corp"].start_date == roles_by_company["Side Consulting LLC"].start_date

    def test_role_id_encodes_company_and_start_date(self, import_result):
        ids = {r.id for r in import_result.career_roles}
        assert "acme-corp-2020-06-01" in ids


class TestRoleFramings:
    def test_each_role_has_a_framing_with_its_bullets(self, import_result):
        acme_id = next(r.id for r in import_result.career_roles if r.company == "Acme Corp")
        framing = next(f for f in import_result.role_framings if f.career_role_id == acme_id)
        assert framing.variant_slug == "test_variant"
        assert len(framing.bullets) == 2


class TestProjectEvidence:
    def test_selected_work_project_captured_without_employer_link(self, import_result):
        assert len(import_result.project_evidence) == 1
        project = import_result.project_evidence[0]
        assert project.title == "Internal Platform Rebuild"
        assert project.related_career_role_id is None  # never guessed — see docs/resume-model.md
        assert len(project.bullets) == 1


class TestEvidenceExtraction:
    def test_bullet_evidence_is_dated_from_its_career_role(self, import_result):
        acme_id = next(r.id for r in import_result.career_roles if r.company == "Acme Corp")
        bullet_evidence = [
            e for e in import_result.evidence if e.provenance.career_role_id == acme_id
        ]
        assert bullet_evidence
        for e in bullet_evidence:
            assert e.start_date == date(2020, 6, 1)
            assert e.end_date is None  # ongoing role

    def test_project_evidence_bullet_has_no_dates(self, import_result):
        project_evidence = [e for e in import_result.evidence if e.provenance.section == "selected architecture work"]
        assert project_evidence
        assert project_evidence[0].start_date is None
        assert project_evidence[0].provenance.company is None

    def test_canonical_skills_detected_in_bullets(self, import_result):
        rest_api_evidence = [e for e in import_result.evidence if "rest_api" in e.canonical_skills]
        assert rest_api_evidence

    def test_skills_table_rows_produce_undated_evidence(self, import_result):
        table_evidence = [e for e in import_result.evidence if e.statement.startswith("Architecture:")]
        assert len(table_evidence) == 1
        assert table_evidence[0].start_date is None
        assert "solutions_architecture" in table_evidence[0].canonical_skills

    def test_every_evidence_item_has_provenance(self, import_result):
        for e in import_result.evidence:
            assert e.provenance.source_type == "resume"
            assert e.provenance.source_name == "test_variant"
            assert e.provenance.variant_slug == "test_variant"
