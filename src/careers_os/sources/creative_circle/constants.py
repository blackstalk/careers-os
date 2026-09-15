"""Everything specific to the Creative Circle candidate portal lives here.

Nothing outside `sources/creative_circle/` should import from this module.
See docs/sources/creative-circle.md for how these were discovered — this is
an undocumented internal API, not a published/versioned one, so treat every
value here as "true as of the investigation date" rather than contractual.
"""

from careers_os.domain.enums import EmploymentType, RemoteStatus

BASE_URL = "https://candidateportal.creativecircle.com"
SEARCH_PATH = "/ccv5-jobs/search"
DETAIL_PATH = "/ccv5-jobs/details"

# The candidate portal is shared, white-labeled infrastructure across
# multiple ASGN staffing brands. `buid` selects which brand's jobs are
# returned. Creative Circle is 3; the others are documented for context
# only — this adapter only ever queries buid=3.
BUSINESS_UNIT_ID = 3
BUSINESS_UNIT_IDS_KNOWN = {
    1: "CyberCoders",
    2: "Apex Systems",
    3: "Creative Circle",
}

DEFAULT_ROWS = 20
MAX_ROWS = 50  # self-imposed cap; not a documented server limit

# workLocationTypeId, as used by both the search and detail endpoints.
WORK_LOCATION_TYPE_TO_REMOTE_STATUS = {
    1: RemoteStatus.ONSITE,
    2: RemoteStatus.HYBRID,
    3: RemoteStatus.REMOTE,
}
REMOTE_WORK_LOCATION_TYPE_ID = 3

# taxTerm -> employment type. Confirmed by reading the site's own bundle
# (`getJobType()`): PERM -> "Full-Time", everything else -> "Freelance".
# Creative Circle does not surface a further split (e.g. temp vs contract).
TAX_TERM_TO_EMPLOYMENT_TYPE = {
    "PERM": EmploymentType.FULL_TIME,
    "CONT": EmploymentType.FREELANCE,
}

# Allowed values for the `daysPosted` filter, confirmed against the live
# API. Any other value is silently ignored by the server, so callers must
# snap to one of these.
SUPPORTED_DAYS_POSTED = (1, 3, 7, 14)

SORT_TYPE_RELEVANCE = "relevance"
SORT_TYPE_DATE = "date"

USER_AGENT = "careers-os/0.1 (personal job-search assistant; contact: hochoa@gmail.com)"
REQUEST_TIMEOUT_SECONDS = 15.0
MIN_DELAY_BETWEEN_REQUESTS_SECONDS = 1.0
