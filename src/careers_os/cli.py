import logging
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from careers_os.career.resume_import import import_resume_docx
from careers_os.career.resume_recommendation import recommend_resume_variant
from careers_os.career.search_profiles import SearchProfilesConfig
from careers_os.career.timeline import humanize_years
from careers_os.domain.enums import EmploymentType, JobStatus
from careers_os.domain.matching import MatchType
from careers_os.domain.query import JobSearchQuery, SortOrder
from careers_os.ingestion.discovery import DiscoveryOpportunity, run_discovery
from careers_os.ingestion.pipeline import IngestionResult, run_search_ingestion
from careers_os.observability.logging import configure_logging
from careers_os.career.evidence_index import EvidenceIndex
from careers_os.sources.base import JobSource, SourceHealth
from careers_os.sources.creative_circle.source import CreativeCircleSource
from careers_os.sources.greenhouse.config import GreenhouseBoardsConfig
from careers_os.sources.greenhouse.source import GreenhouseSource
from careers_os.storage.db import get_engine, get_session
from careers_os.storage.repository import JobRepository
from careers_os.storage.resume_repository import ResumeRepository

load_dotenv()
configure_logging()
logger = logging.getLogger("careers_os.cli")

app = typer.Typer(help="Personal AI-assisted job search / career OS — job sources and tracking.")
search_app = typer.Typer(help="Search a job source and ingest results.")
app.add_typer(search_app, name="search")
console = Console()

careers_app = typer.Typer(help="Manage your resume/experience/evidence data.")


def _repository() -> JobRepository:
    engine = get_engine()
    session = get_session(engine)
    return JobRepository(session)


def _resume_repository() -> ResumeRepository:
    engine = get_engine()
    session = get_session(engine)
    return ResumeRepository(session)


def _evidence_index() -> EvidenceIndex:
    """Best-effort evidence index for scoring — empty (not an error) if no
    resume has been imported yet; scoring/engine.py handles that gracefully.
    """
    return _resume_repository().load_evidence_index()


def _comp_str(job) -> str:
    parts = []
    if job.salary_min or job.salary_max:
        lo = f"${job.salary_min:,.0f}" if job.salary_min else "?"
        hi = f"${job.salary_max:,.0f}" if job.salary_max else "?"
        parts.append(f"{lo}-{hi}/yr")
    if job.hourly_min or job.hourly_max:
        lo = f"${job.hourly_min:,.0f}" if job.hourly_min else "?"
        hi = f"${job.hourly_max:,.0f}" if job.hourly_max else "?"
        parts.append(f"{lo}-{hi}/hr")
    return ", ".join(parts) if parts else "(not published)"


def _print_health_line(label: str, health: SourceHealth, jobs_discovered: int, new: int, updated: int, errors: int) -> None:
    console.print(
        f"[bold]{label}[/bold]: discovered {jobs_discovered} "
        f"({new} new, {updated} updated, {errors} parse errors) — "
        f"source health: [bold]{health.status.value}[/bold]"
    )
    for note in health.notes:
        console.print(f"[yellow]note:[/yellow] {note}")


def _print_results_table(ranked_jobs) -> None:
    if not ranked_jobs:
        console.print("No results.")
        return

    table = Table(show_lines=False)
    table.add_column("Fit")
    table.add_column("Title")
    table.add_column("Location")
    table.add_column("Type")
    table.add_column("Compensation")
    table.add_column("Posted")
    table.add_column("Job ID")
    table.add_column("URL", overflow="fold")

    for ranked in ranked_jobs:
        job = ranked.job
        fit_str = f"{ranked.fit.overall_fit:.2f}" if ranked.fit else "-"
        posted = job.posted_at.date().isoformat() if job.posted_at else "-"
        table.add_row(
            fit_str,
            job.title,
            job.location or "-",
            job.employment_type,
            _comp_str(job),
            posted,
            job.source_job_id,
            job.source_url,
        )
    console.print(table)


def _build_query(
    query: Optional[str],
    location: Optional[str],
    remote: bool,
    days: Optional[int],
    employment_type: Optional[EmploymentType],
    sort: SortOrder,
    limit: int,
) -> JobSearchQuery:
    return JobSearchQuery(
        keyword=query,
        location=location,
        remote_only=remote,
        employment_type=employment_type,
        posted_within_days=days,
        page_size=limit,
        sort=sort,
    )


@search_app.command("creative-circle")
def search_creative_circle(
    query: Optional[str] = typer.Option(None, "--query", help="Keyword/title search."),
    location: Optional[str] = typer.Option(None, "--location", help="City/state text search."),
    remote: bool = typer.Option(False, "--remote", help="Remote-only."),
    days: Optional[int] = typer.Option(None, "--days", help="Posted within N days."),
    employment_type: Optional[EmploymentType] = typer.Option(
        None, "--employment-type", help="full_time or freelance/contract."
    ),
    sort: SortOrder = typer.Option(SortOrder.RELEVANCE, "--sort"),
    limit: int = typer.Option(20, "--limit", help="Max results to fetch (server page size)."),
    score: bool = typer.Option(True, "--score/--no-score", help="Compute career-fit scores."),
    ai: bool = typer.Option(
        True, "--ai/--no-ai", help="Use AI evaluation if ANTHROPIC_API_KEY is set."
    ),
) -> None:
    """Search Creative Circle and ingest/normalize/score real results.

    Example:
        jobs search creative-circle --query "solutions architect" --remote --days 30
    """
    search_query = _build_query(query, location, remote, days, employment_type, sort, limit)

    repo = _repository()
    with CreativeCircleSource() as source:
        result = run_search_ingestion(
            source, search_query, repo, score=score, use_ai=ai, evidence_index=_evidence_index()
        )
        health = source.health()

    _print_health_line(
        "Creative Circle", health, result.jobs_discovered, result.jobs_new,
        result.jobs_updated, result.parser_errors,
    )
    _print_results_table(result.ranked_jobs)


@search_app.command("greenhouse")
def search_greenhouse(
    board: str = typer.Option(..., "--board", help="Greenhouse board token, e.g. 'anthropic'."),
    query: Optional[str] = typer.Option(None, "--query", help="Keyword/title search (client-side)."),
    location: Optional[str] = typer.Option(None, "--location", help="Location text search (client-side)."),
    remote: bool = typer.Option(False, "--remote", help="Remote-only (best-effort detection)."),
    days: Optional[int] = typer.Option(None, "--days", help="Posted within N days."),
    employment_type: Optional[EmploymentType] = typer.Option(
        None, "--employment-type", help="full_time, contract, etc. (best-effort detection)."
    ),
    sort: SortOrder = typer.Option(SortOrder.RELEVANCE, "--sort"),
    limit: int = typer.Option(20, "--limit", help="Max results per page."),
    score: bool = typer.Option(True, "--score/--no-score"),
    ai: bool = typer.Option(True, "--ai/--no-ai"),
) -> None:
    """Search one Greenhouse board and ingest/normalize/score real results.

    Note: Greenhouse's public Job Board API has no server-side search or
    filtering — every filter here runs client-side over that board's full
    posting list (see docs/sources/greenhouse.md).

    Example:
        jobs search greenhouse --board anthropic --query "solutions architect"
    """
    search_query = _build_query(query, location, remote, days, employment_type, sort, limit)

    repo = _repository()
    with GreenhouseSource(board) as source:
        result = run_search_ingestion(
            source, search_query, repo, score=score, use_ai=ai, evidence_index=_evidence_index()
        )
        health = source.health()

    _print_health_line(
        f"Greenhouse ({board})", health, result.jobs_discovered, result.jobs_new,
        result.jobs_updated, result.parser_errors,
    )
    _print_results_table(result.ranked_jobs)


@search_app.command("greenhouse-all")
def search_greenhouse_all(
    query: Optional[str] = typer.Option(None, "--query"),
    location: Optional[str] = typer.Option(None, "--location"),
    remote: bool = typer.Option(False, "--remote"),
    days: Optional[int] = typer.Option(None, "--days"),
    employment_type: Optional[EmploymentType] = typer.Option(None, "--employment-type"),
    sort: SortOrder = typer.Option(SortOrder.RELEVANCE, "--sort"),
    limit: int = typer.Option(20, "--limit", help="Max results per board."),
    score: bool = typer.Option(True, "--score/--no-score"),
    ai: bool = typer.Option(True, "--ai/--no-ai"),
) -> None:
    """Search every board configured in sources/greenhouse/boards.yaml.

    Each board is its own GreenhouseSource instance (see source.py) — this
    command just loops over them and merges the ranked results for display.
    """
    search_query = _build_query(query, location, remote, days, employment_type, sort, limit)
    boards = GreenhouseBoardsConfig.load().boards

    repo = _repository()
    evidence_index = _evidence_index()  # loaded once, reused across every board
    combined: list = []
    for board in boards:
        with GreenhouseSource(board) as source:
            try:
                result: IngestionResult = run_search_ingestion(
                    source, search_query, repo, score=score, use_ai=ai,
                    evidence_index=evidence_index,
                )
            except Exception as exc:  # noqa: BLE001 - one bad board shouldn't kill the rest
                console.print(f"[red]{board}: {exc}[/red]")
                continue
            health = source.health()
        _print_health_line(
            f"Greenhouse ({board})", health, result.jobs_discovered, result.jobs_new,
            result.jobs_updated, result.parser_errors,
        )
        combined.extend(result.ranked_jobs)

    combined.sort(key=lambda r: r.fit.overall_fit if r.fit else 0.0, reverse=True)
    console.print()
    _print_results_table(combined)


def _print_opportunity(rank: int, opp: DiscoveryOpportunity) -> None:
    job = opp.job
    console.print(f"[bold]{rank}. {job.title}[/bold]")
    console.print(f"   Company: {job.company or 'unknown'}")
    console.print(f"   Source: {job.source}")
    console.print(f"   Overall Fit: {opp.fit.overall_fit * 100:.0f}%")
    console.print()
    console.print(f"   Immediate Opportunity: {opp.immediate.level.value.capitalize()}")
    console.print(f"   Career Bridge: {opp.bridge.classification.value.capitalize()}")

    detail = opp.fit.experience_detail
    if detail is not None and detail.strong_matches:
        console.print()
        console.print("   Strong Evidence:")
        for m in detail.strong_matches[:6]:
            console.print(f"   [green]✓[/green] {m.requirement.text}")
    if detail is not None and detail.real_experience_gaps:
        console.print()
        console.print("   Gap:")
        for m in detail.real_experience_gaps[:3]:
            console.print(f"   [red]✗[/red] {m.requirement.text}")

    console.print()
    console.print(f"   Compensation: {_comp_str(job)}")

    if detail is not None:
        rec = recommend_resume_variant(detail)
        if rec.recommended_variant:
            console.print(f"   Recommended Resume: {rec.recommended_variant}")

    console.print(f"   Matched profiles: {', '.join(opp.matched_profiles) or '-'}")
    console.print(f"   Why it ranks: {opp.bridge.reason}")
    console.print(f"   Job: {job.source}/{job.source_job_id}  |  {job.source_url}")
    console.print()


@app.command("discover")
def discover(
    profile: list[str] = typer.Option(
        None, "--profile", help="Limit to specific search profile(s) (repeatable). Default: all enabled."
    ),
    source: Optional[str] = typer.Option(
        None, "--source", help="Limit to 'creative-circle' or 'greenhouse'. Default: both."
    ),
    remote: bool = typer.Option(False, "--remote", help="Remote-only."),
    employment_type: list[EmploymentType] = typer.Option(
        None,
        "--employment-type",
        help="Override the profile config's default employment-type filter (repeatable).",
    ),
    min_fit: Optional[float] = typer.Option(
        None, "--min-fit", help="Only show opportunities with overall_fit >= this (0.0-1.0)."
    ),
    limit: int = typer.Option(20, "--limit", help="Max opportunities to display."),
    ai: bool = typer.Option(True, "--ai/--no-ai", help="Use AI evaluation if ANTHROPIC_API_KEY is set."),
) -> None:
    """Search every enabled discovery profile across available sources,
    rank the results, and show the strongest opportunities.

    Reuses the existing search -> normalize -> dedupe -> score -> evidence-match
    pipeline for every (source, query) pair — this command only adds
    ranking (bridge-role strength, immediate opportunity, career
    direction) and cross-profile discovery provenance on top. See
    docs/discovery.md.

    Example:
        jobs discover
        jobs discover --profile laravel --profile craft --remote --limit 10
    """
    if source is not None and source not in ("creative-circle", "greenhouse"):
        console.print(f"[red]Unknown source: {source}[/red] (expected creative-circle or greenhouse)")
        raise typer.Exit(code=1)

    profiles_config = SearchProfilesConfig.load()
    try:
        profiles = profiles_config.enabled_profiles(profile or None)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if not profiles:
        console.print("No search profiles enabled.")
        raise typer.Exit()

    employment_types = employment_type or profiles_config.default_employment_types

    sources: list[tuple[str, JobSource]] = []
    if source in (None, "creative-circle"):
        sources.append(("creative_circle", CreativeCircleSource()))
    if source in (None, "greenhouse"):
        for board in GreenhouseBoardsConfig.load().boards:
            sources.append(("greenhouse", GreenhouseSource(board)))

    console.print("[bold]CAREER OS — DISCOVERY[/bold]")
    console.print(
        f"Profiles: {', '.join(p.name for p in profiles)}  |  "
        f"Employment types: {', '.join(t.value for t in employment_types) if employment_types else 'any'}\n"
    )

    repo = _repository()
    evidence_index = _evidence_index()
    try:
        result = run_discovery(
            repo, sources, profiles,
            remote_only=remote, employment_types=employment_types, min_fit=min_fit,
            limit=limit, use_ai=ai, evidence_index=evidence_index,
        )
    finally:
        for _family, src in sources:
            src.close()

    m = result.metrics
    console.print(f"Sources queried: {m.sources_queried}")
    console.print(f"Search profiles: {m.search_profiles}")
    console.print(f"Raw jobs discovered: {m.raw_jobs_discovered}")
    console.print(f"Unique jobs: {m.unique_jobs}")
    console.print(f"Strong matches: {m.strong_matches}")
    console.print(f"Strong bridge roles: {m.strong_bridge_roles}")
    console.print(f"New jobs: {m.new_jobs}")
    console.print(f"Updated jobs: {m.updated_jobs}")
    for note in result.notes:
        console.print(f"[yellow]note:[/yellow] {note}")
    console.print()

    if not result.opportunities:
        console.print("No opportunities matched the current filters.")
        raise typer.Exit()

    for i, opp in enumerate(result.opportunities, 1):
        _print_opportunity(i, opp)


@app.command("list")
def list_jobs(
    source: Optional[str] = typer.Option(None, "--source"),
    status: Optional[JobStatus] = typer.Option(None, "--status"),
    limit: int = typer.Option(50, "--limit"),
) -> None:
    """List previously ingested jobs from local storage."""
    repo = _repository()
    jobs = repo.list_jobs(source=source, status=status, limit=limit)
    if not jobs:
        console.print("No jobs stored yet. Try `jobs search creative-circle ...` first.")
        raise typer.Exit()

    table = Table()
    table.add_column("Status")
    table.add_column("Title")
    table.add_column("Company")
    table.add_column("Location")
    table.add_column("Last seen")
    table.add_column("Job ID")
    for job in jobs:
        table.add_row(
            job.status,
            job.title,
            job.company or "-",
            job.location or "-",
            job.last_seen_at.date().isoformat(),
            f"{job.source}/{job.source_job_id}",
        )
    console.print(table)


@app.command("show")
def show_job(source: str, source_job_id: str) -> None:
    """Show full stored detail (and latest score breakdown) for one job."""
    repo = _repository()
    job = repo.get_job(source, source_job_id)
    if job is None:
        console.print(f"[red]No stored job for {source}/{source_job_id}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold]{job.title}[/bold] — {job.company or 'unknown company'}")
    console.print(f"Location: {job.location or '-'}  |  Type: {job.employment_type}  |  "
                  f"Remote: {job.remote_status}")
    console.print(f"Compensation: {_comp_str(job)}")
    console.print(f"Status: {job.status}  |  First seen: {job.first_seen_at}  |  "
                  f"Last seen: {job.last_seen_at}")
    console.print(f"URL: {job.source_url}")
    if job.recruiter_name:
        console.print(f"Recruiter: {job.recruiter_name} <{job.recruiter_contact or 'no email published'}>")
    if job.scores:
        latest = job.scores[0]
        console.print(f"\n[bold]Latest fit score: {latest.overall_fit:.2f}[/bold] "
                      f"({latest.scorer_version}, ai={latest.ai_evaluation_included})")
        for name, comp in latest.components.items():
            console.print(
                f"  {name}: {comp['score']:.2f} (confidence {comp['confidence']:.2f}) — {comp['reason']}"
            )
        detail = _latest_experience_detail(job)
        if detail is not None:
            _print_matches(detail.strong_matches, "Strong Matches", "[green]✓[/green]")
            _print_matches(detail.partial_matches, "Partial / Adjacent", "[yellow]~[/yellow]")
            _print_matches(detail.real_experience_gaps, "Gaps", "[red]✗[/red]")

            best_evidence = _best_evidence(detail, limit=3)
            if best_evidence:
                console.print("\n[bold]Best Evidence[/bold]")
                for i, statement in enumerate(best_evidence, 1):
                    console.print(f"  {i}. {statement}")

            rec = recommend_resume_variant(detail)
            if rec.recommended_variant:
                console.print(f"\n[bold]Recommended Resume[/bold]: {rec.recommended_variant}")
                console.print(f"  {rec.note}")

        pros, cons = _pros_cons(latest.components)
        if pros:
            console.print("\n[bold]Why This Role May Be Worth Pursuing[/bold]")
            for p in pros:
                console.print(f"  + {p}")
        if cons:
            console.print("\n[bold]Why It May Not Be[/bold]")
            for c in cons:
                console.print(f"  - {c}")

    if job.description:
        console.print("\n[bold]Description[/bold]:")
        console.print(job.description[:2000])


def _get_job_or_exit(source: str, source_job_id: str):
    repo = _repository()
    job = repo.get_job(source, source_job_id)
    if job is None:
        console.print(f"[red]No stored job for {source}/{source_job_id}[/red]")
        raise typer.Exit(code=1)
    return job


def _latest_experience_detail(job):
    from careers_os.domain.experience import ExperienceFitDetail

    if not job.scores:
        return None
    raw = job.scores[0].experience_detail
    if not raw:
        return None
    return ExperienceFitDetail.model_validate(raw)


def _print_matches(matches, title: str, symbol: str) -> None:
    if not matches:
        return
    console.print(f"\n[bold]{title}[/bold]")
    for m in matches:
        console.print(f"  {symbol} {m.requirement.text} — {m.reason}")


def _best_evidence(detail, limit: int = 5) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in detail.strong_matches:
        for ref in m.matched_evidence:
            if ref.evidence_id in seen:
                continue
            seen.add(ref.evidence_id)
            company = f" ({ref.company})" if ref.company else ""
            out.append(f"{ref.statement}{company}")
            if len(out) >= limit:
                return out
    return out


def _pros_cons(components: dict) -> tuple[list[str], list[str]]:
    ranked = sorted(components.items(), key=lambda kv: kv[1]["score"], reverse=True)
    pros = [f"{name}: {comp['reason']}" for name, comp in ranked[:2] if comp["score"] >= 0.5]
    cons = [f"{name}: {comp['reason']}" for name, comp in ranked[-2:] if comp["score"] < 0.5]
    return pros, cons


@app.command("match")
def match_job(source: str, source_job_id: str) -> None:
    """Show every requirement match (strong/partial/adjacent/unsupported) for a job."""
    job = _get_job_or_exit(source, source_job_id)
    detail = _latest_experience_detail(job)
    if detail is None:
        console.print("No experience-fit detail available — needs a resume imported before scoring.")
        raise typer.Exit()

    console.print(f"[bold]{job.title}[/bold] — requirement matches\n")
    for match_type in MatchType:
        matches = [m for m in detail.requirement_matches if m.match_type == match_type]
        if not matches:
            continue
        console.print(f"[bold]{match_type.value}[/bold] ({len(matches)})")
        for m in matches:
            console.print(f"  - {m.requirement.text}: {m.reason}")
        console.print()


@app.command("evidence")
def evidence_for_job(source: str, source_job_id: str) -> None:
    """Show the best cited evidence supporting a job's strong matches."""
    job = _get_job_or_exit(source, source_job_id)
    detail = _latest_experience_detail(job)
    if detail is None:
        console.print("No experience-fit detail available.")
        raise typer.Exit()

    console.print(f"[bold]Best evidence for {job.title}[/bold]\n")
    for statement in _best_evidence(detail, limit=20):
        console.print(f"  - {statement}")


@app.command("gaps")
def gaps_for_job(source: str, source_job_id: str) -> None:
    """Show classified skill gaps for a job (real / resume-language / interview-prep)."""
    from careers_os.domain.matching import GapType

    job = _get_job_or_exit(source, source_job_id)
    detail = _latest_experience_detail(job)
    if detail is None:
        console.print("No experience-fit detail available.")
        raise typer.Exit()

    for gap_type in GapType:
        matches = [m for m in detail.gaps if m.gap_type == gap_type]
        if not matches:
            continue
        console.print(f"[bold]{gap_type.value}[/bold] ({len(matches)})")
        for m in matches:
            console.print(f"  - {m.requirement.text}: {m.reason}")
        console.print()


@app.command("recommend-resume")
def recommend_resume(source: str, source_job_id: str) -> None:
    """Recommend which imported resume variant is strongest for this job."""
    job = _get_job_or_exit(source, source_job_id)
    detail = _latest_experience_detail(job)
    if detail is None:
        console.print("No experience-fit detail available.")
        raise typer.Exit()

    rec = recommend_resume_variant(detail)
    if rec.recommended_variant is None:
        console.print(rec.note)
        raise typer.Exit()

    console.print(f"[bold]Recommended resume: {rec.recommended_variant}[/bold]")
    console.print(rec.note)
    console.print("\nScores by variant:")
    for variant, score in rec.scores.items():
        console.print(f"  {variant}: {score:.2f}")
    console.print(f"\nWhy '{rec.recommended_variant}':")
    for reason in rec.reasons.get(rec.recommended_variant, []):
        console.print(f"  - {reason}")


@app.command("duplicates")
def list_duplicates() -> None:
    """List cross-source possible-duplicate flags (never auto-merged).

    See docs/architecture.md#deduplication and storage/dedup.py — a flag
    here is always a suggestion for human review, never an automatic merge.
    """
    repo = _repository()
    flags = repo.list_duplicates()
    if not flags:
        console.print("No possible duplicates flagged.")
        raise typer.Exit()

    from careers_os.storage.db import JobRecord as _JobRecord

    table = Table()
    table.add_column("Confidence")
    table.add_column("Job A")
    table.add_column("Job B")
    table.add_column("Matched signals")
    for flag in flags:
        job_a = repo.session.get(_JobRecord, flag.job_a_id)
        job_b = repo.session.get(_JobRecord, flag.job_b_id)
        table.add_row(
            f"{flag.confidence:.2f}",
            f"{job_a.title} [{job_a.source}]" if job_a else str(flag.job_a_id),
            f"{job_b.title} [{job_b.source}]" if job_b else str(flag.job_b_id),
            ", ".join(flag.matched_signals),
        )
    console.print(table)


@app.command("history")
def job_history(source: str, source_job_id: str) -> None:
    """Show the field-level change log for a job across re-syncs."""
    job = _get_job_or_exit(source, source_job_id)
    repo = _repository()
    changes = repo.list_changes(job.id)
    if not changes:
        console.print("No recorded changes for this job yet (re-run search to detect any).")
        raise typer.Exit()

    table = Table()
    table.add_column("Detected")
    table.add_column("Field")
    table.add_column("Old")
    table.add_column("New")
    for c in changes:
        table.add_row(c.detected_at.isoformat(), c.field, c.old_value or "-", c.new_value or "-")
    console.print(table)


@app.command("status")
def set_status(source: str, source_job_id: str, status: JobStatus) -> None:
    """Set a job's human workflow status (survives future syncs)."""
    repo = _repository()
    try:
        job = repo.set_status(source, source_job_id, status)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    repo.commit()
    console.print(f"{job.title} -> {job.status}")


@app.command("health")
def health(
    source_name: str = typer.Argument("creative-circle", help="creative-circle or greenhouse"),
    board: Optional[str] = typer.Option(None, "--board", help="Required when source_name is greenhouse."),
) -> None:
    """Report a source adapter's current health (after a throwaway probe search)."""
    if source_name == "creative-circle":
        source_cm = CreativeCircleSource()
    elif source_name == "greenhouse":
        if not board:
            console.print("[red]--board is required for greenhouse[/red]")
            raise typer.Exit(code=1)
        source_cm = GreenhouseSource(board)
    else:
        console.print(f"[red]Unknown source: {source_name}[/red]")
        raise typer.Exit(code=1)

    with source_cm as source:
        try:
            source.search(JobSearchQuery(page_size=1))
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Probe search failed: {exc}[/red]")
        h = source.health()
    console.print(h.model_dump())


@careers_app.command("import-resume")
def import_resume(
    file_path: Path,
    variant: str = typer.Option(..., "--variant", help="Slug for this resume variant, e.g. 'fde'."),
) -> None:
    """Import a DOCX resume as a named variant.

    Deterministic (no LLM) — parses the document's own paragraph styles and
    tables (see career/resume_import.py). Re-importing the same variant
    slug replaces its role framings/evidence rather than duplicating them;
    shared career roles (same company + start date) are reconciled across
    variants, not duplicated.

    Example:
        careers import-resume ~/Downloads/resume.docx --variant fde
    """
    if not file_path.exists():
        console.print(f"[red]File not found: {file_path}[/red]")
        raise typer.Exit(code=1)

    result = import_resume_docx(file_path, variant)
    repo = _resume_repository()
    summary = repo.import_result(result)
    repo.commit()

    console.print(f"[bold]Imported '{variant}'[/bold] from {file_path.name}")
    console.print(
        f"  career roles: {summary.career_roles_new} new, {summary.career_roles_updated} updated"
    )
    console.print(
        f"  role framings: {summary.role_framings}  "
        f"project evidence: {summary.project_evidence}  "
        f"evidence items: {summary.evidence}"
    )
    for w in summary.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")


@careers_app.command("resumes")
def list_resumes() -> None:
    """List imported resume variants."""
    repo = _resume_repository()
    variants = repo.list_variants()
    if not variants:
        console.print("No resumes imported yet. Try `careers import-resume <file> --variant <slug>`.")
        raise typer.Exit()

    table = Table()
    table.add_column("Variant")
    table.add_column("Positioning")
    table.add_column("Source file")
    table.add_column("Imported")
    for v in variants:
        table.add_row(
            v.slug,
            v.positioning_title or "-",
            v.source_file_name,
            v.imported_at.date().isoformat(),
        )
    console.print(table)


@careers_app.command("experience")
def show_experience() -> None:
    """Show the reconciled career timeline and per-skill years-of-experience
    estimates, computed from every imported resume variant's evidence.
    """
    from careers_os.career.timeline import total_career_span_years, years_for_skill

    repo = _resume_repository()
    roles = repo.list_career_roles()
    if not roles:
        console.print("No resumes imported yet. Try `careers import-resume <file> --variant <slug>`.")
        raise typer.Exit()

    table = Table(title="Career Timeline")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Start")
    table.add_column("End")
    for r in roles:
        end = "Present" if r.is_current else (r.end_date.isoformat() if r.end_date else "-")
        table.add_row(r.company, r.title, r.start_date.isoformat(), end)
    console.print(table)

    index = repo.load_evidence_index()
    span = total_career_span_years(index.career_roles)
    console.print(
        f"\n[bold]Total career span: {humanize_years(span)}[/bold] "
        f"({span} years — overlapping roles merged, not double-counted)"
    )

    console.print("\n[bold]Skill experience estimates[/bold] (only skills with any evidence):")
    skill_table = Table()
    skill_table.add_column("Skill")
    skill_table.add_column("Estimated experience")
    skill_table.add_column("Confidence")
    skill_table.add_column("Companies")
    rows = []
    for key in index.taxonomy.skills:
        est = years_for_skill(key, index.evidence, index.career_roles)
        if est.confidence > 0:
            rows.append((est.numeric_years, key, est))
    rows.sort(reverse=True)
    for _, key, est in rows:
        skill_table.add_row(
            index.taxonomy.display_name(key),
            est.display_years,
            f"{est.confidence:.2f}",
            ", ".join(est.supporting_companies) or "-",
        )
    console.print(skill_table)


if __name__ == "__main__":
    app()
