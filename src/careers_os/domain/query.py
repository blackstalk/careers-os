from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from careers_os.domain.enums import EmploymentType


class SortOrder(str, Enum):
    RELEVANCE = "relevance"
    DATE = "date"


class JobSearchQuery(BaseModel):
    """Source-agnostic search query.

    Every source adapter is responsible for mapping whatever subset of these
    fields it can honor onto its own native filters, and for reporting (via
    logging) any fields it had to ignore. Nothing here may reference a
    specific source's vocabulary (e.g. no Creative Circle field/enum names).
    """

    keyword: Optional[str] = None
    location: Optional[str] = None
    remote_only: bool = False
    employment_type: Optional[EmploymentType] = None
    posted_within_days: Optional[int] = Field(
        default=None, description="Only return jobs posted within the last N days."
    )
    page: int = 1
    page_size: int = 20
    sort: SortOrder = SortOrder.RELEVANCE
