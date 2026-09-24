"""Non-secret app settings (which local model to use), stored next to the data.

Holds no financial data and no keys, so it is a plain JSON file.
"""

import json
import os
import threading
from dataclasses import asdict, dataclass, field, replace
from typing import Callable

from glassfolio.config import data_home
from glassfolio.llm import DEFAULT_URL, OpenAICompatModel, require_loopback


@dataclass(frozen=True)
class ModelSettings:
    url: str = DEFAULT_URL
    name: str | None = None                       # None: no model chosen; heuristics only
    evals: dict = field(default_factory=dict)     # model name → last evaluation summary
    require_touch_id: bool = True                 # desktop app: Touch ID (or password) to unlock


def _path():
    return data_home() / "settings.json"


def load_settings() -> ModelSettings:
    try:
        raw = json.loads(_path().read_text())
    except (OSError, json.JSONDecodeError):
        return ModelSettings()
    return ModelSettings(raw.get("url", DEFAULT_URL), raw.get("name"), raw.get("evals", {}),
                         raw.get("require_touch_id", True) is not False)


_WRITING = threading.RLock()  # a model test finishes in a worker thread while you change settings


def save_settings(settings: ModelSettings) -> ModelSettings:
    """Replaces the file whole, so a crash mid-write can't leave half a file."""
    require_loopback(settings.url)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with _WRITING:
        temp.write_text(json.dumps(asdict(settings), indent=2))
        os.replace(temp, path)
    return settings


def _change(change: Callable[[ModelSettings], ModelSettings]) -> ModelSettings:
    """Load, change and save as one step, so two writers never undo each other."""
    with _WRITING:
        return save_settings(change(load_settings()))


def choose_model(url: str, name: str | None) -> ModelSettings:
    return _change(lambda s: replace(s, url=url.rstrip("/"), name=name or None))


def record_eval(name: str, summary: dict) -> ModelSettings:
    return _change(lambda s: replace(s, evals={**s.evals, name: summary}))


def set_touch_id(required: bool) -> ModelSettings:
    return _change(lambda s: replace(s, require_touch_id=bool(required)))


def configured_model() -> OpenAICompatModel | None:
    s = load_settings()
    return OpenAICompatModel(s.name, s.url) if s.name else None
