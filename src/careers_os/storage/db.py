from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "careers.db"


class Base(DeclarativeBase):
    pass


class RawJobRecord(Base):
    """Latest raw snapshot retrieved from a source.

    Phase 1 keeps only the most recent snapshot per (source, source_job_id)
    rather than a full append-only history — see docs/architecture.md's
    Change Tracking section for why that's an intentional scope cut, not an
    oversight.
    """

    __tablename__ = "raw_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String, index=True)
    source_job_id: Mapped[str] = mapped_column(String, index=True)
    source_url: Mapped[str] = mapped_column(String)

    raw_title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_compensation: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_employment_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_posted_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_updated_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_recruiter: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    raw_payload: Mapped[dict] = mapped_column(JSON)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobRecord(Base):
    """Canonical job. `status` is human-owned — ingestion must never
    overwrite it; see JobRepository.upsert_job.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    source: Mapped[str] = mapped_column(String, index=True)
    source_job_id: Mapped[str] = mapped_column(String, index=True)
    source_url: Mapped[str] = mapped_column(String)

    title: Mapped[str] = mapped_column(String)
    company: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    remote_status: Mapped[str] = mapped_column(String)
    employment_type: Mapped[str] = mapped_column(String)

    salary_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    salary_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hourly_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hourly_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    technologies: Mapped[list] = mapped_column(JSON, default=list)

    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    recruiter_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    recruiter_contact: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    status: Mapped[str] = mapped_column(String, default="new")

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    raw_job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("raw_jobs.id"), nullable=True
    )
    raw_job: Mapped[Optional[RawJobRecord]] = relationship()

    # Discovery provenance (Phase 2.5, see docs/discovery.md): which
    # search profile(s) turned this job up. A job found by both "php" and
    # "laravel" queries gets both names here, not two job rows.
    discovered_by_profiles: Mapped[list] = mapped_column(JSON, default=list)

    scores: Mapped[list["JobScoreRecord"]] = relationship(
        back_populates="job", order_by="desc(JobScoreRecord.computed_at)"
    )


class JobScoreRecord(Base):
    """Append-only fit-score history for a job (one row per (re)scoring)."""

    __tablename__ = "job_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    job: Mapped[JobRecord] = relationship(back_populates="scores")

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scorer_version: Mapped[str] = mapped_column(String)
    overall_fit: Mapped[float] = mapped_column(Float)
    ai_evaluation_included: Mapped[bool] = mapped_column(default=False)
    components: Mapped[dict] = mapped_column(JSON)
    # Full per-requirement match breakdown (see domain/experience.py). None
    # when the job was scored without an imported resume.
    experience_detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class ResumeVariantRecord(Base):
    __tablename__ = "resume_variants"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String)
    positioning_title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_file_name: Mapped[str] = mapped_column(String)
    source_file_hash: Mapped[str] = mapped_column(String)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CareerRoleRecord(Base):
    """Career-truth employment record. Identity is (company, start_date),
    encoded into `id` at import time — see career/resume_import.py. Shared
    across every resume variant that mentions this role.
    """

    __tablename__ = "career_roles"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    company: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)


class RoleFramingRecord(Base):
    """How one resume variant presents one CareerRole. Re-importing the
    same variant replaces its framing for a role rather than duplicating it
    — see storage/resume_repository.py.
    """

    __tablename__ = "role_framings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    career_role_id: Mapped[str] = mapped_column(ForeignKey("career_roles.id"), index=True)
    variant_slug: Mapped[str] = mapped_column(ForeignKey("resume_variants.slug"), index=True)
    bullets: Mapped[list] = mapped_column(JSON, default=list)
    section: Mapped[str] = mapped_column(String)


class ProjectEvidenceRecord(Base):
    __tablename__ = "project_evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    variant_slug: Mapped[str] = mapped_column(ForeignKey("resume_variants.slug"), index=True)
    title: Mapped[str] = mapped_column(String)
    role_descriptor: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    context_line: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bullets: Mapped[list] = mapped_column(JSON, default=list)
    related_career_role_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("career_roles.id"), nullable=True
    )
    section: Mapped[str] = mapped_column(String)


class EvidenceRecord(Base):
    """One provenance-backed statement about the candidate's background.
    `provenance` is stored as a JSON blob (it's a small, always-together
    nested object — see domain/evidence.py) rather than exploded columns.
    """

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String)
    statement: Mapped[str] = mapped_column(String)
    canonical_skills: Mapped[list] = mapped_column(JSON, default=list)
    technologies: Mapped[list] = mapped_column(JSON, default=list)
    themes: Mapped[list] = mapped_column(JSON, default=list)
    provenance: Mapped[dict] = mapped_column(JSON)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Denormalized from provenance.variant_slug purely so re-import can
    # cheaply delete-and-replace this variant's evidence — see
    # storage/resume_repository.py.
    variant_slug: Mapped[str] = mapped_column(String, index=True)


class PossibleDuplicateRecord(Base):
    """A conservative, human-reviewable flag that two jobs (normally from
    different sources) might be the same real-world posting. Never an
    automatic merge — see storage/dedup.py.
    """

    __tablename__ = "possible_duplicates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_a_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    job_b_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    matched_signals: Mapped[list] = mapped_column(JSON, default=list)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AIRefinementRecord(Base):
    """One stored Claude review of a job (Phase 4.3), keyed by the job's
    content and the prompt version, so a finalist that is deferred run
    after run is reviewed once, not once per run — see
    ingestion/ai_refinement.py. A changed description or prompt simply
    misses the cache and is reviewed again."""

    __tablename__ = "ai_refinements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobChangeRecord(Base):
    """Append-only field-level change log, written by JobRepository.upsert_job
    when a re-synced job's data differs from what's stored. See
    docs/architecture.md#change-tracking.
    """

    __tablename__ = "job_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    field: Mapped[str] = mapped_column(String)
    old_value: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class JobEvaluationRecord(Base):
    """Persisted Phase 3 opportunity decision — one row per (re)evaluation.

    Append-only like JobScoreRecord, so a job's recommendation history is
    visible over time and it's possible to tell whether a later change
    came from the job's own data changing, the candidate's evidence
    changing, preferences changing, or the evaluation logic itself
    changing (`evaluation_version`) — see docs/pursue-recommendation.md.
    """

    __tablename__ = "job_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evaluation_version: Mapped[str] = mapped_column(String)

    eligibility: Mapped[dict] = mapped_column(JSON)
    qualification: Mapped[dict] = mapped_column(JSON)
    opportunity_cost: Mapped[dict] = mapped_column(JSON)
    scope: Mapped[dict] = mapped_column(JSON)
    freshness: Mapped[dict] = mapped_column(JSON)

    pursue_recommendation: Mapped[str] = mapped_column(String)
    pursue_reason: Mapped[str] = mapped_column(String)
    pursue_factors: Mapped[list] = mapped_column(JSON, default=list)
    work_style: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    career_track: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class NotificationRecord(Base):
    """Append-only record of one alert attempt (Phase 4) — see
    docs/alerts.md#duplicate-suppression.

    A `status="failed"` row is kept for observability but never counted
    as "already alerted" — `ingestion/scheduled_run.py` only checks
    `JobRepository.list_notifications(job_id, status="sent")`, so a
    transient SMTP failure must not silently suppress a future retry.
    `pursue_recommendation` records which tier triggered this specific
    alert, so a later improvement (e.g. pursue -> strong_pursue) can be
    recognized as alert-worthy again rather than treated as a duplicate.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    evaluation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("job_evaluations.id"), nullable=True)

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    channel: Mapped[str] = mapped_column(String)
    operating_mode: Mapped[str] = mapped_column(String)
    pursue_recommendation: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)  # "sent" | "failed"
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)


def _apply_lightweight_migrations(engine: Engine) -> None:
    """Add newly-introduced columns to an already-existing SQLite file.

    `Base.metadata.create_all` only creates missing *tables*, not columns
    on tables that already exist — this project has no real migration
    framework (Alembic would be overkill for a single-user local SQLite
    file), so new columns are added here, one at a time, as they're
    introduced. Each check is a no-op once the column exists.
    """
    inspector = inspect(engine)
    if "jobs" in inspector.get_table_names():
        existing = {c["name"] for c in inspector.get_columns("jobs")}
        if "discovered_by_profiles" not in existing:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE jobs ADD COLUMN discovered_by_profiles JSON DEFAULT '[]'")
                )
    if "job_evaluations" in inspector.get_table_names():
        existing = {c["name"] for c in inspector.get_columns("job_evaluations")}
        if "work_style" not in existing:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE job_evaluations ADD COLUMN work_style JSON"))
        if "career_track" not in existing:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE job_evaluations ADD COLUMN career_track JSON"))


def get_engine(db_path: Optional[Path] = None) -> Engine:
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    _apply_lightweight_migrations(engine)
    return engine


def get_session(engine: Engine) -> Session:
    return sessionmaker(bind=engine)()
