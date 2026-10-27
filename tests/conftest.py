import pytest

from flightplanner.config import Settings
from flightplanner.db import Store


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def demo_settings(tmp_path):
    return Settings(force_demo=True, data_dir=tmp_path, deal_feeds=[])
