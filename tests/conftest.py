import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from careers_os.storage.db import Base

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "creative_circle"


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _no_real_ai(request, monkeypatch):
    """Offline tests never reach Claude, even when the developer's shell
    has ANTHROPIC_API_KEY set; AI behavior is tested with fake evaluators.
    Live tests (`-m live`) are left alone."""
    if request.node.get_closest_marker("live") is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def search_response() -> dict:
    return load_fixture("search_response.json")


@pytest.fixture
def malformed_search_response() -> dict:
    return load_fixture("search_response_malformed.json")


@pytest.fixture
def detail_response() -> dict:
    return load_fixture("detail_response.json")


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
