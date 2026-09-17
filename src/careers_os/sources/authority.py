"""Which sources hold the employer's own posting, and which only re-list it.

ATS boards (Greenhouse, Ashby, Lever, Workable) and Creative Circle publish
the posting themselves. Aggregators discover postings across many
employers but may carry thinner descriptions or looser location data, so
when both copies of the same job are found in one run, the authoritative
copy's evaluation is the one that counts. See docs/source-evaluation.md.
"""

AGGREGATOR_SOURCES = frozenset({"himalayas"})


def is_aggregator(source: str) -> bool:
    return source in AGGREGATOR_SOURCES
