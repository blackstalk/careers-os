"""Everything specific to Greenhouse's public Job Board API lives here.

Nothing outside `sources/greenhouse/` should import from this module.
See docs/sources/greenhouse.md for what was verified by direct testing.

Unlike Creative Circle, this is a genuinely documented public API
(https://developers.greenhouse.io/job-board.html) — no reverse engineering
was required for the endpoint shapes. What still had to be discovered by
testing: the API returns every job for a board in one response (no
server-side pagination, search, or filtering at all), and per-board custom
`metadata` fields are completely inconsistent between companies.
"""

BASE_URL = "https://boards-api.greenhouse.io/v1"


def jobs_list_path(board_token: str) -> str:
    return f"/boards/{board_token}/jobs"


def job_detail_path(board_token: str, job_id: str) -> str:
    return f"/boards/{board_token}/jobs/{job_id}"


USER_AGENT = "careers-os/0.1 (personal job-search assistant; contact: hochoa@gmail.com)"
REQUEST_TIMEOUT_SECONDS = 15.0
MIN_DELAY_BETWEEN_REQUESTS_SECONDS = 1.0

# Best-effort remote-status detection. Greenhouse has no standardized field
# for this — some boards expose a custom `metadata` entry (name varies per
# company; these are the label spellings observed during investigation),
# otherwise we fall back to scanning the free-text `location.name` string.
# Both are heuristics; see docs/sources/greenhouse.md "Known limitations".
REMOTE_METADATA_LABELS = {"location type", "workplace type", "work location", "work type"}
REMOTE_VALUE_TO_STATUS = {
    "remote": "remote",
    "fully remote": "remote",
    "hybrid": "hybrid",
    "on-site": "onsite",
    "onsite": "onsite",
    "in-office": "onsite",
}
LOCATION_TEXT_REMOTE_MARKERS = ("remote",)

# Employment-type heuristic: scanned against title + metadata values only
# (never against the full description, to avoid false positives from a
# job that merely *mentions* "contract" work in its responsibilities).
EMPLOYMENT_TYPE_METADATA_LABELS = {"employment type", "job type"}
EMPLOYMENT_TYPE_TEXT_MARKERS = {
    "full_time": ("full-time", "full time", "fulltime"),
    "contract": ("contract", "contractor"),
    "freelance": ("freelance",),
    "part_time": ("part-time", "part time"),
    "temporary": ("temp", "temporary", "internship", "intern"),
}

# Compensation is not a structured field in the Job Board API. Some
# companies (observed: Anthropic) embed a figure in the free-text
# description as boilerplate compliance language, e.g.
# "Annual Salary: $222,800 — $290,000 USD". This regex is a best-effort,
# low-confidence extraction of that pattern — never treated as reliably
# present the way Creative Circle's SalaryMin/SalaryMax fields are.
SALARY_TEXT_PATTERN = (
    r"(?:annual\s+salary|salary\s+range|compensation\s+range|pay\s+range)"
    r"[^$]{0,40}\$([\d,]{4,})\s*(?:-|—|–|&mdash;|to)\s*\$([\d,]{4,})"
)
