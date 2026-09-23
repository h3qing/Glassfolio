"""Daily closes from Tiingo (spec §5.2).

- Requests carry a ticker and a date range only — never shares, values or accounts.
- The API token travels in a header, never in the URL or in fetch_log.
- Only allow-listed HTTPS hosts can be reached.
- Free tiers are small, so a per-run budget fetches held securities and funds
  first, then look-through constituents by size. The rest keep the last price
  from their fund's holdings file.
- A daily move beyond ±25% that no split explains goes to the inbox for review.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable
from urllib.parse import quote, urlsplit

from glassfolio.exposure import exposure_lines
from glassfolio.lake import Lake, OpMeta, RowCounts, insert_rows, new_id, run_write, utc_now

ALLOWED_HOSTS = frozenset({"api.tiingo.com"})
BASE = "https://api.tiingo.com/tiingo/daily"
MAX_DAILY_MOVE = 0.25
DEFAULT_BUDGET = 45  # Tiingo's free tier allows about 50 requests per hour


class FetchError(Exception):
    pass


class RateLimited(FetchError):
    pass


HttpGet = Callable[[str, dict], object]


def guarded_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise FetchError(f"host not allowed: {parts.scheme}://{parts.hostname}")
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib would follow them to any host, token header included."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FetchError(f"refused redirect (HTTP {code})")


_OPENER = urllib.request.build_opener(_NoRedirect)


def http_get_json(url: str, headers: dict) -> object:
    request = urllib.request.Request(guarded_url(url), headers=headers)
    try:
        with _OPENER.open(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimited("rate limited (HTTP 429)") from None
        raise FetchError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise FetchError(type(exc).__name__) from None


def tiingo_symbol(ticker: str) -> str:
    return ticker.upper().replace(".", "-").replace("/", "-")


def tickers_to_fetch(lake: Lake, as_of: date, budget: int) -> tuple[tuple[str, str], ...]:
    """(security_id, ticker): held securities and funds first, then constituents by value."""
    lines = exposure_lines(lake, as_of)
    tickers = dict(lake.con.execute(
        """SELECT security_id, ticker FROM securities WHERE type NOT IN ('cash', 'other')
           AND ticker IS NOT NULL QUALIFY row_number() OVER (
               PARTITION BY security_id ORDER BY created_at DESC) = 1""").fetchall())
    held = [l.security_id for l in lines if l.kind == "position"]
    funds = [l.via_security_id for l in lines if l.via_security_id]
    constituents = sorted((l for l in lines if l.kind == "security" and l.via_security_id),
                          key=lambda l: -(l.value or 0))
    ordered = dict.fromkeys(s for s in (*held, *funds, *(l.security_id for l in constituents))
                            if s in tickers)
    return tuple((s, tickers[s]) for s in list(ordered)[:budget])


@dataclass(frozen=True)
class Bar:
    day: date
    close: float
    dividend: float
    split: float


@dataclass(frozen=True)
class FetchReport:
    fetched: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    flagged: tuple[str, ...] = ()
    rate_limited: bool = False
    skipped: tuple[str, ...] = field(default=())


def _bars(payload: object) -> tuple[Bar, ...]:
    if not isinstance(payload, list):
        raise FetchError("unexpected response shape")
    return tuple(Bar(datetime.fromisoformat(r["date"].replace("Z", "+00:00")).date(),
                     float(r["close"]), float(r.get("divCash") or 0), float(r.get("splitFactor") or 1))
                 for r in payload)


def _last_close(con, security_id: str, before: date) -> float | None:
    row = con.execute("""SELECT close::DOUBLE FROM prices WHERE security_id = ? AND date < ?
                         AND close IS NOT NULL ORDER BY date DESC LIMIT 1""",
                      [security_id, before]).fetchone()
    return row[0] if row else None


def _big_moves(prev: float | None, bars: tuple[Bar, ...]) -> tuple[tuple[Bar, float], ...]:
    moves = []
    for bar in bars:
        if prev:
            change = bar.close / (prev / bar.split) - 1
            if abs(change) > MAX_DAILY_MOVE:
                moves.append((bar, change))
        prev = bar.close
    return tuple(moves)


def _fetch_all(targets, start, end, token, http_get):
    """Network phase, outside any transaction. Returns per-ticker outcomes."""
    headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}
    outcomes = []
    for index, (security_id, ticker) in enumerate(targets):
        url = f"{BASE}/{quote(tiingo_symbol(ticker))}/prices?startDate={start}&endDate={end}"
        started = utc_now()
        try:
            outcomes.append((security_id, ticker, started, "ok", _bars(http_get(url, headers)), None))
        except RateLimited as exc:
            outcomes.append((security_id, ticker, started, "rate_limited", (), str(exc)))
            return tuple(outcomes), tuple(t for _, t in targets[index + 1:])
        except (FetchError, KeyError, ValueError, TypeError) as exc:
            outcomes.append((security_id, ticker, started, "failed", (), str(exc).replace(token, "***")))
    return tuple(outcomes), ()


def refresh_prices(lake: Lake, start: date, end: date, token: str, budget: int = DEFAULT_BUDGET,
                   http_get: HttpGet = http_get_json, actor: str = "scheduler") -> FetchReport:
    targets = tickers_to_fetch(lake, end, budget)
    outcomes, skipped = _fetch_all(targets, start, end, token, http_get)
    reviews = tuple((sid, ticker, bar, change) for sid, ticker, _, status, bars, _ in outcomes
                    if status == "ok"
                    for bar, change in _big_moves(_last_close(lake.con, sid, bars[0].day) if bars else None, bars))

    def work(con):
        existing = set(con.execute("SELECT security_id, date FROM corporate_actions "
                                   "WHERE type = 'split'").fetchall())
        have = set(con.execute("SELECT security_id, date FROM prices WHERE source = 'tiingo'").fetchall())
        reviewed = {(p["security_id"], p["date"]) for p in (
            json.loads(r[0]) for r in con.execute(
                "SELECT payload FROM inbox_items WHERE type = 'price_review'").fetchall())}
        prices = tuple((sid, b.day, f"{b.close}", f"{b.dividend}" if b.dividend else None, "tiingo")
                       for sid, _, _, status, bars, _ in outcomes for b in bars
                       if (sid, b.day) not in have)
        fresh = tuple(r for r in reviews if (r[0], r[2].day.isoformat()) not in reviewed)
        splits = tuple((sid, b.day, "split", f"{b.split}")
                       for sid, _, _, _, bars, _ in outcomes for b in bars
                       if b.split != 1 and (sid, b.day) not in existing)
        log = tuple((new_id("fetch"), "tiingo", ticker, started, status, error, None)
                    for _, ticker, started, status, _, error in outcomes)
        n = insert_rows(con, "prices", prices) + insert_rows(con, "corporate_actions", splits)
        n += insert_rows(con, "fetch_log", log)
        for sid, ticker, bar, change in fresh:
            con.execute("INSERT INTO inbox_items VALUES (?, 'price_review', ?, 'open', ?, NULL)",
                        [new_id("inbox"), json.dumps({"security_id": sid, "ticker": ticker,
                         "date": bar.day.isoformat(), "change": change}), utc_now()])
        return None, RowCounts(inserted=n + len(fresh))

    params = {"source": "tiingo", "start": start.isoformat(), "end": end.isoformat(),
              "tickers": len(targets)}
    run_write(lake, OpMeta(actor, "refresh_prices", params, "fetch daily closes"), work)
    return FetchReport(
        fetched=tuple(t for _, t, _, s, _, _ in outcomes if s == "ok"),
        failed=tuple(t for _, t, _, s, _, _ in outcomes if s == "failed"),
        flagged=tuple(t for _, t, _, _ in reviews),
        rate_limited=any(s == "rate_limited" for _, _, _, s, _, _ in outcomes),
        skipped=skipped)
