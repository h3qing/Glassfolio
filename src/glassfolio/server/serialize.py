"""Turn dataclasses and dates into JSON-safe values."""

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal


def to_json(value):
    if is_dataclass(value) and not isinstance(value, type):
        out = {f.name: to_json(getattr(value, f.name)) for f in fields(value)}
        props = (name for name in dir(type(value)) if isinstance(getattr(type(value), name), property))
        extras = {name: to_json(getattr(value, name)) for name in props}
        return {**out, **extras}
    if isinstance(value, (list, tuple, frozenset, set)):
        return [to_json(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return None  # raw file content never leaves the lake
    return value
