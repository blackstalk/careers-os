"""Deterministic DOCX resume importer.

No LLM required for import — this walks the document's actual paragraph
styles (Title/Subtitle/Heading 1/Role Header/List Bullet/Normal) and table
structure in true document order. It was built by inspecting two real
resumes' actual style usage (see docs/resume-model.md) rather than assumed;
if a resume uses different styles, sections will be skipped with a
recorded warning rather than mis-parsed silently.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import docx
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph

from careers_os.career.skills import SkillsTaxonomy
from careers_os.domain.evidence import Evidence, EvidenceProvenance
from careers_os.domain.resume import CareerRole, ProjectEvidenceEntry, ResumeVariant, RoleFraming
from careers_os.domain.taxonomy import SkillCategory

EMPLOYER_SECTIONS = {
    "professional experience",
    "additional experience",
    "earlier leadership and engineering experience",
}
PROJECT_SECTIONS = {"selected architecture work", "selected portfolio work"}
SUMMARY_SECTION = "professional summary"
SKILLS_TABLE_SECTIONS = {"core capabilities", "technical skills"}
FLAT_SKILLS_SECTION = "technical foundation"
COMPACT_ROLE_SECTION = "earlier technical experience"

_SKILLS_LABEL_TO_CATEGORY: dict[str, SkillCategory] = {
    "architecture": SkillCategory.ARCHITECTURE,
    "delivery": SkillCategory.RESPONSIBILITY,
    "systems": SkillCategory.CLOUD,
    "ai and automation": SkillCategory.AI_ML,
    "platforms": SkillCategory.FRAMEWORK,
    "leadership": SkillCategory.LEADERSHIP,
    "development": SkillCategory.PROGRAMMING_LANGUAGE,
    "cloud and delivery": SkillCategory.CLOUD,
    "data and analytics": SkillCategory.DATA,
}

# Priority order for picking one display `type` when a bullet's text hits
# taxonomy skills from multiple categories — canonical_skills (used for
# actual matching) always carries every hit regardless of this ordering.
_CATEGORY_PRIORITY = [
    SkillCategory.ARCHITECTURE,
    SkillCategory.AI_ML,
    SkillCategory.CLOUD,
    SkillCategory.LEADERSHIP,
    SkillCategory.CUSTOMER_FACING,
    SkillCategory.DATA,
    SkillCategory.FRAMEWORK,
    SkillCategory.PROGRAMMING_LANGUAGE,
    SkillCategory.DOMAIN,
    SkillCategory.EDUCATION,
]

_MONTH_YEAR_RE = re.compile(r"([A-Za-z]+ \d{4}|\d{4})\s*-\s*(Present|[A-Za-z]+ \d{4}|\d{4})")


@dataclass
class ResumeImportResult:
    variant: ResumeVariant
    career_roles: list[CareerRole] = field(default_factory=list)
    role_framings: list[RoleFraming] = field(default_factory=list)
    project_evidence: list[ProjectEvidenceEntry] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _iter_block_items(document):
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield DocxTable(child, document)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "unknown"


def _parse_date_token(token: str, *, end_of_range: bool) -> Optional[date]:
    token = token.strip()
    if token.lower() == "present":
        return None
    try:
        return datetime.strptime(token, "%B %Y").date()
    except ValueError:
        pass
    if re.fullmatch(r"\d{4}", token):
        year = int(token)
        return date(year, 12, 1) if end_of_range else date(year, 1, 1)
    return None


def _parse_date_range(text: str, warnings: list[str]) -> tuple[Optional[date], Optional[date], bool]:
    match = _MONTH_YEAR_RE.search(text)
    if not match:
        warnings.append(f"Could not parse a date range from: {text!r}")
        return None, None, False
    start_token, end_token = match.group(1), match.group(2)
    start = _parse_date_token(start_token, end_of_range=False)
    if len(start_token) == 4:
        warnings.append(f"Year-only start date ({start_token}) — defaulted to January.")
    is_current = end_token.strip().lower() == "present"
    end = None if is_current else _parse_date_token(end_token, end_of_range=True)
    return start, end, is_current


def _split_pipes(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s*\|\s*", text) if p.strip()]


def _pick_category(skills_found: list[str], taxonomy: SkillsTaxonomy) -> SkillCategory:
    categories = {taxonomy.skills[k].category for k in skills_found if k in taxonomy.skills}
    for preferred in _CATEGORY_PRIORITY:
        if preferred in categories:
            return preferred
    return SkillCategory.RESPONSIBILITY


def _make_evidence_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]
    return f"ev-{digest}"


def _build_evidence_from_statement(
    statement: str,
    *,
    taxonomy: SkillsTaxonomy,
    provenance: EvidenceProvenance,
    start_date: Optional[date],
    end_date: Optional[date],
) -> Evidence:
    skills_found = taxonomy.find_in_text(statement)
    category = _pick_category(skills_found, taxonomy)
    technologies = [
        taxonomy.display_name(k)
        for k in skills_found
        if taxonomy.skills[k].category in (SkillCategory.PROGRAMMING_LANGUAGE, SkillCategory.FRAMEWORK, SkillCategory.CLOUD, SkillCategory.DATA)
    ]
    themes = [
        taxonomy.display_name(k)
        for k in skills_found
        if taxonomy.skills[k].category in (SkillCategory.ARCHITECTURE, SkillCategory.LEADERSHIP, SkillCategory.CUSTOMER_FACING, SkillCategory.AI_ML, SkillCategory.DOMAIN)
    ]
    return Evidence(
        id=_make_evidence_id(provenance.variant_slug or "", provenance.company or "", statement),
        type=category,
        statement=statement,
        canonical_skills=skills_found,
        technologies=technologies,
        themes=themes,
        provenance=provenance,
        start_date=start_date,
        end_date=end_date,
    )


def import_resume_docx(
    file_path: Path, variant_slug: str, *, taxonomy: Optional[SkillsTaxonomy] = None
) -> ResumeImportResult:
    taxonomy = taxonomy or SkillsTaxonomy.load()
    file_bytes = Path(file_path).read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    document = docx.Document(file_path)
    warnings: list[str] = []

    positioning_title: Optional[str] = None
    summary_lines: list[str] = []
    current_section: Optional[str] = None

    career_roles: list[CareerRole] = []
    role_framings: list[RoleFraming] = []
    project_evidence: list[ProjectEvidenceEntry] = []
    evidence: list[Evidence] = []

    imported_at = datetime.now().astimezone()

    # State for the block currently being accumulated.
    pending_kind: Optional[str] = None  # "role" | "project"
    pending_role: Optional[CareerRole] = None
    pending_project: Optional[ProjectEvidenceEntry] = None
    pending_bullets: list[str] = []

    def flush_pending() -> None:
        nonlocal pending_kind, pending_role, pending_project, pending_bullets
        if pending_kind == "role" and pending_role is not None:
            career_roles.append(pending_role)
            role_framings.append(
                RoleFraming(
                    career_role_id=pending_role.id,
                    variant_slug=variant_slug,
                    bullets=list(pending_bullets),
                    section=current_section or "",
                )
            )
            for bullet in pending_bullets:
                provenance = EvidenceProvenance(
                    source_type="resume",
                    source_name=variant_slug,
                    section=current_section,
                    company=pending_role.company,
                    career_role_id=pending_role.id,
                    variant_slug=variant_slug,
                    extracted_at=imported_at,
                )
                evidence.append(
                    _build_evidence_from_statement(
                        bullet,
                        taxonomy=taxonomy,
                        provenance=provenance,
                        start_date=pending_role.start_date,
                        end_date=pending_role.end_date,
                    )
                )
        elif pending_kind == "project" and pending_project is not None:
            pending_project.bullets = list(pending_bullets)
            project_evidence.append(pending_project)
            for bullet in pending_bullets:
                provenance = EvidenceProvenance(
                    source_type="resume",
                    source_name=variant_slug,
                    section=current_section,
                    company=None,  # parent employer not explicit in source text — see docs/resume-model.md
                    career_role_id=None,
                    variant_slug=variant_slug,
                    extracted_at=imported_at,
                )
                evidence.append(
                    _build_evidence_from_statement(
                        bullet,
                        taxonomy=taxonomy,
                        provenance=provenance,
                        start_date=None,
                        end_date=None,
                    )
                )
        pending_kind, pending_role, pending_project, pending_bullets = None, None, None, []

    for block in _iter_block_items(document):
        if isinstance(block, DocxTable):
            if current_section in SKILLS_TABLE_SECTIONS:
                for row in block.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if len(cells) < 2 or not cells[0] or not cells[1]:
                        continue
                    label, values = cells[0], cells[1]
                    category = _SKILLS_LABEL_TO_CATEGORY.get(label.lower(), SkillCategory.RESPONSIBILITY)
                    statement = f"{label}: {values}"
                    skills_found = taxonomy.find_in_text(values)
                    provenance = EvidenceProvenance(
                        source_type="resume",
                        source_name=variant_slug,
                        section=current_section,
                        company=None,
                        career_role_id=None,
                        variant_slug=variant_slug,
                        extracted_at=imported_at,
                    )
                    evidence.append(
                        Evidence(
                            id=_make_evidence_id(variant_slug, current_section, label),
                            type=category,
                            statement=statement,
                            canonical_skills=skills_found,
                            technologies=[v.strip() for v in values.split(",")],
                            themes=[],
                            provenance=provenance,
                            start_date=None,
                            end_date=None,
                        )
                    )
            else:
                warnings.append(f"Unhandled table under section {current_section!r} — skipped.")
            continue

        text = block.text.strip()
        if not text:
            continue
        style = block.style.name if block.style else ""

        if style == "Title":
            continue  # candidate name — not needed structurally

        if style == "Subtitle":
            positioning_title = text
            continue

        if style == "Heading 1":
            flush_pending()
            current_section = text.strip().lower()
            continue

        if style == "Role Header":
            flush_pending()
            lines = text.split("\n")
            line1 = lines[0] if lines else ""
            line2 = lines[1] if len(lines) > 1 else ""
            top = _split_pipes(line1)
            bottom = _split_pipes(line2)

            if current_section in PROJECT_SECTIONS:
                title = top[0] if top else line1
                role_descriptor = top[1] if len(top) > 1 else None
                context_line = bottom[0] if bottom else None
                pending_project = ProjectEvidenceEntry(
                    id=_make_evidence_id(variant_slug, current_section or "", title),
                    variant_slug=variant_slug,
                    title=title,
                    role_descriptor=role_descriptor,
                    context_line=context_line,
                    bullets=[],
                    related_career_role_id=None,
                    section=current_section or "",
                )
                pending_kind = "project"
            elif current_section in EMPLOYER_SECTIONS:
                company = top[0] if top else line1
                title = top[1] if len(top) > 1 else ""
                location = bottom[0] if bottom else None
                date_text = bottom[1] if len(bottom) > 1 else line2
                start, end, is_current = _parse_date_range(date_text, warnings)
                if start is None:
                    warnings.append(f"Skipping role with unparseable dates: {line1!r} / {line2!r}")
                    pending_kind = None
                    continue
                role_id = _slugify(f"{company}-{start.isoformat()}")
                pending_role = CareerRole(
                    id=role_id,
                    company=company,
                    title=title,
                    location=location,
                    start_date=start,
                    end_date=end,
                    is_current=is_current,
                )
                pending_kind = "role"
            else:
                warnings.append(
                    f"'Role Header' paragraph under unrecognized section {current_section!r} "
                    f"({line1!r}) — skipped rather than guessed."
                )
                pending_kind = None
            continue

        if style == "List Bullet":
            if pending_kind is not None:
                pending_bullets.append(text)
            else:
                warnings.append(f"Bullet with no preceding role/project header, dropped: {text!r}")
            continue

        # style == "Normal" (or anything else) — section-dependent handling.
        if current_section == SUMMARY_SECTION:
            summary_lines.append(text)
        elif current_section == FLAT_SKILLS_SECTION:
            values = _split_pipes(text)
            skills_found = taxonomy.find_in_text(text)
            provenance = EvidenceProvenance(
                source_type="resume",
                source_name=variant_slug,
                section=current_section,
                company=None,
                career_role_id=None,
                variant_slug=variant_slug,
                extracted_at=imported_at,
            )
            evidence.append(
                Evidence(
                    id=_make_evidence_id(variant_slug, current_section, "flat-skills"),
                    type=SkillCategory.RESPONSIBILITY,
                    statement=f"Technical Foundation: {text}",
                    canonical_skills=skills_found,
                    technologies=values,
                    themes=[],
                    provenance=provenance,
                    start_date=None,
                    end_date=None,
                )
            )
        elif current_section == COMPACT_ROLE_SECTION:
            parts = _split_pipes(text)
            if len(parts) < 3:
                warnings.append(f"Could not parse compact role line: {text!r}")
                continue
            company, title, date_text = parts[0], parts[1], parts[2]
            start, end, is_current = _parse_date_range(date_text, warnings)
            if start is None:
                warnings.append(f"Skipping compact role with unparseable dates: {text!r}")
                continue
            career_roles.append(
                CareerRole(
                    id=_slugify(f"{company}-{start.isoformat()}"),
                    company=company,
                    title=title,
                    location=None,
                    start_date=start,
                    end_date=end,
                    is_current=is_current,
                )
            )
        elif current_section in PROJECT_SECTIONS:
            # SeniorDev's "Selected Portfolio Work" is a single flat line of
            # titles with no per-project description — real but too thin to
            # extract evidence from; kept only as bare provenance stubs.
            for title in _split_pipes(text):
                project_evidence.append(
                    ProjectEvidenceEntry(
                        id=_make_evidence_id(variant_slug, current_section, title),
                        variant_slug=variant_slug,
                        title=title,
                        role_descriptor=None,
                        context_line=None,
                        bullets=[],
                        related_career_role_id=None,
                        section=current_section,
                    )
                )
        # else: unrecognized Normal-style content under an unhandled
        # section (e.g. contact line) — intentionally ignored, not an error.

    flush_pending()

    variant = ResumeVariant(
        slug=variant_slug,
        display_name=positioning_title or variant_slug,
        positioning_title=positioning_title,
        summary=" ".join(summary_lines) or None,
        source_file_name=Path(file_path).name,
        source_file_hash=file_hash,
        imported_at=imported_at,
    )

    return ResumeImportResult(
        variant=variant,
        career_roles=career_roles,
        role_framings=role_framings,
        project_evidence=project_evidence,
        evidence=evidence,
        warnings=warnings,
    )
