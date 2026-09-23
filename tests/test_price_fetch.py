"""Spec §5.2: fetch closes (Tiingo), validate, never send holdings."""

from datetime import date

import pytest

from conftest import T1
from glassfolio.flows import list_inbox
from glassfolio.price_fetch import FetchError, RateLimited, guarded_url, refresh_prices, tiingo_symbol

TOKEN = "secret-token-123"


def fake_tiingo(data: dict, calls: list, rate_limit_after: int | None = None):
    def get(url: str, headers: dict):
        calls.append((url, headers))
        if rate_limit_after is not None and len(calls) > rate_limit_after:
            raise RateLimited("429")
        ticker = url.split("/daily/")[1].split("/")[0]
        if ticker not in data:
            raise FetchError("404 not found")
        return data[ticker]
    return get


def bar(day, close, div=0.0, split=1.0):
    return {"date": f"{day}T00:00:00.000Z", "close": close, "divCash": div, "splitFactor": split}


def prices_of(lake, ticker):
    return lake.con.execute(
        """SELECT p.date, p.close::DOUBLE, p.dividend_per_share::DOUBLE FROM prices p
           JOIN securities s USING (security_id) WHERE s.ticker = ? AND p.source = 'tiingo'
           ORDER BY 1""", [ticker]).fetchall()


def test_requests_carry_only_ticker_dates_and_header_token(golden):
    lake, _ = golden
    calls = []
    refresh_prices(lake, date(2026, 10, 1), date(2026, 10, 2), token=TOKEN,
                   http_get=fake_tiingo({}, calls))
    assert calls
    for url, headers in calls:
        assert url.startswith("https://api.tiingo.com/tiingo/daily/")
        assert TOKEN not in url and headers["Authorization"] == f"Token {TOKEN}"
        assert set(url.split("?")[1].split("&")) == {"startDate=2026-10-01", "endDate=2026-10-02"}


def test_held_securities_come_first_within_budget(golden):
    lake, _ = golden
    calls = []
    refresh_prices(lake, T1, T1, token=TOKEN, budget=3, http_get=fake_tiingo({}, calls))
    fetched = {u.split("/daily/")[1].split("/")[0] for u, _ in calls}
    assert len(calls) == 3 and fetched <= {"NVDA", "QQQ", "GFOF", "MSFT", "VTI", "GCIT"}


def test_stores_closes_dividends_and_splits(golden):
    lake, _ = golden
    data = {"NVDA": [bar("2026-10-01", 112.0, div=0.01), bar("2026-10-02", 56.5, split=2.0)]}
    report = refresh_prices(lake, date(2026, 10, 1), date(2026, 10, 2), token=TOKEN,
                            http_get=fake_tiingo(data, []))
    assert prices_of(lake, "NVDA") == [(date(2026, 10, 1), 112.0, 0.01), (date(2026, 10, 2), 56.5, None)]
    split = lake.con.execute("SELECT ratio_or_amount::DOUBLE FROM corporate_actions c JOIN securities s "
                             "USING (security_id) WHERE s.ticker = 'NVDA' AND c.date = '2026-10-02'").fetchone()
    assert split == (2.0,)
    assert "NVDA" in report.fetched
    assert not [i for i in list_inbox(lake) if i.type == "price_review"]  # split, not a crash


def test_large_move_is_flagged_for_review(golden):
    lake, _ = golden
    data = {"NVDA": [bar("2026-10-01", 60.0)]}  # 110 → 60 with no split
    refresh_prices(lake, date(2026, 10, 1), date(2026, 10, 1), token=TOKEN, http_get=fake_tiingo(data, []))
    (item,) = [i for i in list_inbox(lake) if i.type == "price_review"]
    assert item.payload["ticker"] == "NVDA" and item.payload["change"] == pytest.approx(60 / 110 - 1)


def test_rate_limit_stops_and_is_logged(golden):
    lake, _ = golden
    calls = []
    report = refresh_prices(lake, T1, T1, token=TOKEN, http_get=fake_tiingo({}, calls, rate_limit_after=2))
    assert len(calls) == 3 and report.rate_limited
    statuses = [r[0] for r in lake.con.execute("SELECT status FROM fetch_log").fetchall()]
    assert "rate_limited" in statuses


def test_failures_are_logged_without_the_token(golden):
    lake, _ = golden
    refresh_prices(lake, T1, T1, token=TOKEN, http_get=fake_tiingo({}, []))
    rows = lake.con.execute("SELECT source, target, status, error FROM fetch_log").fetchall()
    assert rows and all(r[0] == "tiingo" and r[2] == "failed" for r in rows)
    assert all(TOKEN not in (r[3] or "") for r in rows)


def test_only_allowlisted_hosts():
    assert guarded_url("https://api.tiingo.com/tiingo/daily/X/prices")
    with pytest.raises(FetchError, match="not allowed"):
        guarded_url("https://evil.example/tiingo/daily/X/prices")
    with pytest.raises(FetchError, match="not allowed"):
        guarded_url("http://api.tiingo.com/insecure")


def test_symbol_mapping():
    assert tiingo_symbol("BRK.B") == "BRK-B" and tiingo_symbol("nvda") == "NVDA"


def test_redirects_are_refused():
    import urllib.request
    from glassfolio.price_fetch import _NoRedirect
    req = urllib.request.Request("https://api.tiingo.com/x", headers={"Authorization": "Token t"})
    with pytest.raises(FetchError, match="refused redirect"):
        _NoRedirect().redirect_request(req, None, 302, "Found", {}, "http://evil.example/")


def test_refetching_the_same_days_adds_nothing(golden):
    lake, _ = golden
    data = {"NVDA": [bar("2026-10-01", 60.0, div=0.5)]}
    for _ in range(2):
        refresh_prices(lake, date(2026, 10, 1), date(2026, 10, 1), token=TOKEN, http_get=fake_tiingo(data, []))
    assert len(prices_of(lake, "NVDA")) == 1
    assert len([i for i in list_inbox(lake) if i.type == "price_review"]) == 1
