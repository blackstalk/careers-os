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
