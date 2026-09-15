"""Everything specific to Ashby's public Job Postings API lives here.

Nothing outside `sources/ashby/` should import from this module. See
docs/sources/ashby.md for what was verified by direct testing.

Documented at https://developers.ashbyhq.com/docs/public-job-posting-api
— unlike Greenhouse, there is no separate per-job detail endpoint at all;
the single list endpoint already returns full descriptions and structured
compensation, so `fetch_job` re-fetches the list and finds the matching
id (see source.py).
"""

from careers_os.domain.enums import EmploymentType

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"


def job_board_path(board_name: str) -> str:
    return f"/{board_name}"


USER_AGENT = "careers-os/0.1 (personal job-search assistant; contact: hochoa@gmail.com)"
REQUEST_TIMEOUT_SECONDS = 15.0
MIN_DELAY_BETWEEN_REQUESTS_SECONDS = 1.0

# Ashby's own `workplaceType` field is structured (unlike Greenhouse's
# free-text location), but is frequently null even for a listed job — the
# `isRemote` boolean is checked as a fallback, not because it's more
# reliable, but because it's sometimes populated when workplaceType isn't.
WORKPLACE_TYPE_TO_REMOTE_STATUS = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
}

# Ashby's `employmentType` is a structured enum-like string, not free text
# — see https://developers.ashbyhq.com/docs/public-job-posting-api. No
# heuristic scanning needed, unlike Greenhouse.
EMPLOYMENT_TYPE_MAP = {
    "fulltime": EmploymentType.FULL_TIME,
    "parttime": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "intern": EmploymentType.TEMPORARY,  # consistent with Greenhouse's internship mapping
}
