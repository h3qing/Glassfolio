import pytest

from glassfolio import demo
from glassfolio.lake import open_lake

GOLDEN = demo.GOLDEN
TEST_KEY = "0" * 63 + "1"  # test-only key; real keys come from the Keychain
D, T1 = demo.D, demo.T1


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Never touch the real data folder or settings from tests."""
    monkeypatch.setenv("GLASSFOLIO_HOME", str(tmp_path_factory.mktemp("home")))


@pytest.fixture
def lake(tmp_path):
    return open_lake(tmp_path / "home", TEST_KEY)


def load_etf(lake, name, ticker, fmt="generic", as_of=None, outstanding=None):
    return demo.load_etf(lake, name, ticker, fmt, as_of, outstanding)


def load_statement(lake, name, account, profile_id, as_of):
    return demo.load_statement(lake, name, account, profile_id, as_of)


build_golden = demo.build_golden


@pytest.fixture
def golden(lake):
    profile = build_golden(lake)
    return lake, profile
