from pathlib import Path

import pytest

from store_agent import db

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def conn():
    connection = db.fresh_store(DATA_DIR)
    yield connection
    connection.close()
