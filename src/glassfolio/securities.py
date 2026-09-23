"""Security master: identity resolution.

Stable identifiers (FIGI, ISIN, CUSIP) win over tickers. A ticker only
matches when no stable identifier on either side contradicts it, so a
reused or foreign ticker never silently maps onto the wrong company.
"""

from dataclasses import dataclass, replace
from itertools import groupby
from typing import Mapping

import duckdb

from glassfolio.lake import insert_rows, new_id, utc_now

TYPES = ("stock", "etf", "mutual_fund", "cit", "cash", "other")
_IDS = ("figi", "isin", "cusip")


@dataclass(frozen=True)
class Security:
    security_id: str | None
    ticker: str | None
    name: str | None
    type: str
    cusip: str | None = None
    isin: str | None = None
    figi: str | None = None
    proxy_security_id: str | None = None


CURRENT_SQL = """
SELECT security_id, ticker, name, type, cusip, isin, figi, proxy_security_id
FROM (SELECT *, row_number() OVER (
        PARTITION BY security_id ORDER BY created_at DESC) AS rn FROM securities)
WHERE rn = 1
"""


def load_securities(con: duckdb.DuckDBPyConnection) -> tuple[Security, ...]:
    return tuple(Security(*row) for row in con.execute(CURRENT_SQL).fetchall())


def _conflicts(a: Security, b: Security) -> bool:
    return any(
        getattr(a, f) and getattr(b, f) and getattr(a, f) != getattr(b, f) for f in _IDS
    )


Index = Mapping[str, Mapping[str, tuple[Security, ...]]]


def _key(field: str, sec: Security) -> str | None:
    value = getattr(sec, field)
    return value.upper() if value else None


def build_index(master: tuple[Security, ...]) -> Index:
    """Lookup tables per identifier field (figi, isin, cusip, ticker)."""
    def group(field: str) -> Mapping[str, tuple[Security, ...]]:
        keyed = sorted(((_key(field, s), s) for s in master if _key(field, s)),
                       key=lambda pair: pair[0])
        return {k: tuple(s for _, s in g) for k, g in groupby(keyed, key=lambda pair: pair[0])}
    return {field: group(field) for field in (*_IDS, "ticker")}


def resolve_in(index: Index, ref: Security) -> Security | None:
    for field in _IDS:
        key = _key(field, ref)
        hits = index[field].get(key, ()) if key else ()
        if len(hits) == 1:
            return hits[0]
    key = _key("ticker", ref)
    if not key:
        return None
    hits = [s for s in index["ticker"].get(key, ()) if not _conflicts(s, ref)]
    return hits[0] if len(hits) == 1 else None


def resolve(master: tuple[Security, ...], ref: Security) -> Security | None:
    """Find the master record for `ref`, or None if it is new or ambiguous."""
    return resolve_in(build_index(master), ref)


def _merged(existing: Security, ref: Security, type_: str | None) -> Security:
    filled = {f: getattr(existing, f) or getattr(ref, f) for f in _IDS}
    return replace(
        existing, name=existing.name or ref.name, type=type_ or existing.type, **filled
    )


def _union(a: Security, b: Security) -> Security:
    filled = {f: getattr(a, f) or getattr(b, f) for f in (*_IDS, "ticker", "name")}
    return replace(a, **filled)


def _keys(sec: Security) -> tuple[tuple[str, str], ...]:
    return tuple((f, k) for f in (*_IDS, "ticker") if (k := _key(f, sec)))


def _cluster_new(refs: tuple[Security, ...]) -> tuple[tuple[int, ...], tuple[Security, ...]]:
    """Group unresolved refs that share an identifier and conflict on none.

    Returns the cluster index of each ref and the merged ref per cluster.
    """
    clusters: list[Security] = []
    members: dict[tuple[str, str], list[int]] = {}
    assignment: list[int] = []
    for ref in refs:
        candidates = sorted({i for key in _keys(ref) for i in members.get(key, ())})
        match = next((i for i in candidates if not _conflicts(clusters[i], ref)), None)
        if match is None:
            clusters.append(ref)
            match = len(clusters) - 1
        else:
            clusters[match] = _union(clusters[match], ref)
        for key in _keys(clusters[match]):
            if match not in members.setdefault(key, []):
                members[key].append(match)
        assignment.append(match)
    return tuple(assignment), tuple(clusters)


def insert_security(con: duckdb.DuckDBPyConnection, sec: Security) -> None:
    con.execute(
        "INSERT INTO securities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [sec.security_id, sec.ticker, sec.name, sec.type, sec.cusip, sec.isin,
         sec.figi, sec.proxy_security_id, utc_now()],
    )


def plan_securities(
    master: tuple[Security, ...], refs: tuple[Security, ...], force_type: str | None = None
) -> tuple[tuple[str, ...], tuple[Security, ...]]:
    """Pure: map each ref to a security_id and list the rows to append.

    Unresolved refs in one batch that name the same security share one new id.
    """
    index = build_index(master)
    found = tuple(resolve_in(index, ref) for ref in refs)
    unresolved = tuple(i for i, f in enumerate(found) if f is None)
    assignment, clusters = _cluster_new(tuple(refs[i] for i in unresolved))
    created = tuple(replace(c, security_id=new_id("sec"), type=force_type or c.type)
                    for c in clusters)
    new_for = dict(zip(unresolved, (created[a] for a in assignment)))
    rows = tuple(new_for[i] if f is None else _merged(f, refs[i], force_type)
                 for i, f in enumerate(found))
    enriched = tuple(r for r, f in zip(rows, found) if f is not None and r != f)
    earliest = {r.security_id: r for r in reversed(enriched)}
    return (tuple(r.security_id for r in rows),
            created + tuple(earliest[i] for i in dict.fromkeys(r.security_id for r in enriched)))


def ensure_securities(
    con: duckdb.DuckDBPyConnection, refs: tuple[Security, ...], force_type: str | None = None
) -> tuple[tuple[str, ...], int]:
    """Resolve refs against the master, appending new or enriched rows."""
    ids, to_insert = plan_securities(load_securities(con), refs, force_type)
    now = utc_now()
    insert_rows(con, "securities", tuple(
        (s.security_id, s.ticker, s.name, s.type, s.cusip, s.isin, s.figi,
         s.proxy_security_id, now) for s in to_insert))
    return ids, len(to_insert)
