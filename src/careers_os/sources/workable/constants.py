"""Everything specific to Workable's public job-widget API lives here.

Nothing outside `sources/workable/` should import from this module. See
docs/sources/workable.md for what was verified by direct testing.

This is the unauthenticated endpoint behind Workable's embeddable careers
widget — distinct from Workable's authenticated v3 HR API, which needs an
employer-issued token and is never used here.
"""

BASE_URL = "https://apply.workable.com/api/v1/widget/accounts"


def account_path(account: str) -> str:
    return f"/{account}"


# `details=true` is what adds each job's full HTML `description` to the
# list response; without it the list carries no description at all.
LIST_PARAMS = {"details": "true"}

USER_AGENT = "careers-os/0.1 (personal job-search assistant; contact: hochoa@gmail.com)"
REQUEST_TIMEOUT_SECONDS = 15.0
MIN_DELAY_BETWEEN_REQUESTS_SECONDS = 1.0

# `employment_type` is free text set per posting (observed: "Full-time",
# "Contract", "Part-time") — scanned like Lever's `commitment`.
EMPLOYMENT_TYPE_TEXT_MARKERS = {
    "full_time": ("full-time", "full time", "fulltime"),
    "contract": ("contract", "contractor"),
    "freelance": ("freelance",),
    "part_time": ("part-time", "part time"),
    "temporary": ("temp", "temporary", "internship", "intern"),
}

# No structured compensation field exists in this API. Same low-confidence
# free-text extraction approach as Greenhouse/Lever.
SALARY_TEXT_PATTERN = (
    r"(?:annual\s+salary|salary\s+range|compensation\s+range|pay\s+range|base\s+salary)"
    r"[^$]{0,60}\$([\d,]{4,})\s*(?:-|—|–|to)\s*\$([\d,]{4,})"
)
