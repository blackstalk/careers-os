"""Everything specific to Himalayas' public remote-jobs API lives here.

Nothing outside `sources/himalayas/` should import from this module. See
docs/sources/himalayas.md for what was verified by direct testing.

Documented at https://himalayas.app/docs/remote-jobs-api — free, no key.
Unlike the ATS adapters this is an aggregator with real server-side
keyword search across many employers, and every listing is remote.
"""

BASE_URL = "https://himalayas.app/jobs/api"
SEARCH_PATH = "/search"

USER_AGENT = "careers-os/0.1 (personal job-search assistant; contact: hochoa@gmail.com)"
REQUEST_TIMEOUT_SECONDS = 20.0
MIN_DELAY_BETWEEN_REQUESTS_SECONDS = 1.0

# The API returns at most 20 results per page and its data only refreshes
# daily. Broad queries ("Backend Engineer") match thousands of postings,
# so each query is capped at the most recent PAGES_PER_QUERY pages — enough
# to catch each day's new postings without scoring thousands of stale ones.
PAGE_SIZE = 20
PAGES_PER_QUERY = 2

# Restrict search to postings open to US-based applicants (country-
# restricted to the US, or worldwide). The candidate is US-based.
COUNTRY = "US"

EMPLOYMENT_TYPE_MAP = {
    "full time": "full_time",
    "part time": "part_time",
    "contractor": "contract",
    "temporary": "temporary",
    "intern": "temporary",
}
