"""Small, pure helpers for reading exported files."""

import csv
import hashlib
import io
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

_EMPTY = {"", "-", "--", "—", "–", "n/a", "N/A"}
_STRIP = re.compile(r"[$,%\s]")
MAX_ABS = Decimal(10) ** 15


def read_source(source: "Path | bytes") -> bytes:
    """Accept a file path (CLI) or uploaded bytes (web), never writing a temp file."""
    return source if isinstance(source, bytes) else Path(source).read_bytes()


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def parse_number(raw: str | None) -> Decimal | None:
    """Parse broker-formatted numbers: `$1,234.50`, `(12.30)`, `--`."""
    if raw is None:
        return None
    text = raw.strip()
    if text in _EMPTY:
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = _STRIP.sub("", text.strip("()"))
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {raw!r}") from exc
    if not value.is_finite() or abs(value) >= MAX_ABS:
        raise ValueError(f"number out of range: {raw!r}")
    return -value if negative else value


def read_rows(text: str) -> tuple[tuple[str, ...], ...]:
    try:
        return tuple(tuple(row) for row in csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ValueError(f"malformed CSV: {exc}") from exc


def cell(row: tuple[str, ...], header: tuple[str, ...], column: str | None) -> str | None:
    """Value of `column` in `row`, or None if the column or cell is absent."""
    if column is None or column not in header:
        return None
    idx = header.index(column)
    return row[idx] if idx < len(row) else None


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def printable(text: str | None) -> str:
    """Strip control characters (incl. ANSI escapes) from untrusted file text."""
    return _CONTROL.sub("", text or "")


def clean(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    return None if text in _EMPTY else text


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d %b %Y",
                 "%b %d %Y", "%Y/%m/%d")
_DATE_IN_TEXT = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4}|\d{1,2}-[A-Z][a-z]{2}-\d{4})")


def parse_date(raw: str | None):
    """Parse the date formats brokers use; None if it isn't one."""
    from datetime import datetime

    text = clean(raw)
    if text is None:
        return None
    text = text.split("T")[0].split(" ")[0] if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", text) else text
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.replace(".", ""), fmt).date()
        except ValueError:
            continue
    return None


def find_date(text: str):
    """First recognisable date inside free text such as a title line."""
    for match in _DATE_IN_TEXT.finditer(text):
        parsed = parse_date(match.group(1))
        if parsed is not None:
            return parsed
    return None


def looks_numeric(text: str | None) -> bool:
    try:
        return parse_number(text) is not None
    except ValueError:
        return False
