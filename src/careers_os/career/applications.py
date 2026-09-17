"""Application tracking the scheduled production run can see.

The job database's `status` column is human-owned but lives in whichever
SQLite file you edit — and production runs against its own copy in the
GitHub Actions cache. `career/data/applications.yaml` is committed to the
repo, so anything listed there reaches every run. See
docs/applications.md.

A job matches an entry by normalized posting URL, or by normalized
company + title. The company + title rule deliberately also catches
aggregator copies and regional clones of a role already applied to.
"""

import datetime as dt
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field

from careers_os.storage.dedup import normalize_company, normalize_title

DEFAULT_APPLICATIONS_PATH = Path(__file__).parent / "data" / "applications.yaml"

APPLICATION_STATUSES = ("applied", "interviewing", "offer", "rejected", "withdrawn", "not_interested")


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = re.sub(r"/(application|apply)/?$", "", parts.path.rstrip("/"))
    return f"{host}{path}".lower()


class ApplicationEntry(BaseModel):
    company: str
    title: str
    status: str = "applied"
    url: Optional[str] = None
    date: Optional[dt.date] = None
    note: Optional[str] = None


class ApplicationLog(BaseModel):
    applications: list[ApplicationEntry] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "ApplicationLog":
        path = path or DEFAULT_APPLICATIONS_PATH
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        return cls(**data)

    def save(self, path: Optional[Path] = None) -> None:
        path = path or DEFAULT_APPLICATIONS_PATH
        header = (
            "# Jobs already applied to (or ruled out). Committed on purpose: the\n"
            "# scheduled GitHub Actions run never alerts on anything listed here.\n"
            "# Add entries with `jobs applied <url>`, then commit and push.\n"
            "# See docs/applications.md.\n"
        )
        body = yaml.safe_dump(self.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True)
        path.write_text(header + body)

    def match(self, *, url: Optional[str], company: Optional[str], title: Optional[str]) -> Optional[ApplicationEntry]:
        url_key = normalize_url(url) if url else None
        company_key = normalize_company(company) if company else None
        title_key = normalize_title(title) if title else None
        for entry in self.applications:
            if url_key and entry.url and normalize_url(entry.url) == url_key:
                return entry
            if (
                company_key and title_key
                and normalize_company(entry.company) == company_key
                and normalize_title(entry.title) == title_key
            ):
                return entry
        return None

    def add(self, entry: ApplicationEntry) -> bool:
        """Add or update; returns False if an identical entry already existed."""
        existing = self.match(url=entry.url, company=entry.company, title=entry.title)
        if existing is not None:
            if existing.status == entry.status:
                return False
            existing.status = entry.status
            existing.note = entry.note or existing.note
            return True
        self.applications.append(entry)
        return True
