"""Everything specific to Lever's public Postings API lives here.

Nothing outside `sources/lever/` should import from this module. See
docs/sources/lever.md for what was verified by direct testing.

Documented informally by Lever as a way to build a custom careers page
(no formal API reference page, but the shape is stable and widely relied
upon — see docs/sources/lever.md). Unlike Ashby, there IS a per-posting
detail endpoint, same as Greenhouse.
"""

BASE_URL = "https://api.lever.co/v0/postings"


def postings_list_path(company: str) -> str:
    return f"/{company}"


def posting_detail_path(company: str, posting_id: str) -> str:
    return f"/{company}/{posting_id}"


USER_AGENT = "careers-os/0.1 (personal job-search assistant; contact: hochoa@gmail.com)"
REQUEST_TIMEOUT_SECONDS = 15.0
MIN_DELAY_BETWEEN_REQUESTS_SECONDS = 1.0

# Lever's `workplaceType` is a structured field (observed values: "remote",
# "hybrid", "onsite") — see docs/sources/lever.md.
WORKPLACE_TYPE_TO_REMOTE_STATUS = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
}

# `categories.commitment` is free text set per-company (observed: "Full-time",
# "Contract", "Internship", "Part-time") — scanned the same way Greenhouse's
# metadata-label text is, since Lever has no fixed enum for this field.
EMPLOYMENT_TYPE_TEXT_MARKERS = {
    "full_time": ("full-time", "full time", "fulltime"),
    "contract": ("contract", "contractor"),
    "freelance": ("freelance",),
    "part_time": ("part-time", "part time"),
    "temporary": ("temp", "temporary", "internship", "intern"),
}

# Compensation is not a structured field for most Lever postings observed
# (Palantir's board, for example, embeds it in `additionalPlain` free
# text) — same low-confidence, best-effort extraction approach as
# Greenhouse. See docs/sources/lever.md "Known limitations".
SALARY_TEXT_PATTERN = (
    r"(?:annual\s+salary|salary\s+range|compensation\s+range|pay\s+range|estimated\s+salary\s+range)"
    r"[^$]{0,60}\$([\d,]{4,})\s*(?:-|—|–|to)\s*\$([\d,]{4,})"
)
