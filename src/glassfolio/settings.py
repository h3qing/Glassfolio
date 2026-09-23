"""Non-secret app settings (which local model to use), stored next to the data.

Holds no financial data and no keys, so it is a plain JSON file.
"""

import json
from dataclasses import asdict, dataclass, field, replace

from glassfolio.config import data_home
from glassfolio.llm import DEFAULT_URL, OpenAICompatModel, require_loopback


@dataclass(frozen=True)
class ModelSettings:
    url: str = DEFAULT_URL
    name: str | None = None                       # None: no model chosen; heuristics only
    evals: dict = field(default_factory=dict)     # model name → last evaluation summary


def _path():
    return data_home() / "settings.json"


def load_settings() -> ModelSettings:
    try:
        raw = json.loads(_path().read_text())
    except (OSError, json.JSONDecodeError):
        return ModelSettings()
    return ModelSettings(raw.get("url", DEFAULT_URL), raw.get("name"), raw.get("evals", {}))


def save_settings(settings: ModelSettings) -> ModelSettings:
    require_loopback(settings.url)
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(asdict(settings), indent=2))
    return settings


def choose_model(url: str, name: str | None) -> ModelSettings:
    return save_settings(replace(load_settings(), url=url.rstrip("/"), name=name or None))


def record_eval(name: str, summary: dict) -> ModelSettings:
    current = load_settings()
    return save_settings(replace(current, evals={**current.evals, name: summary}))


def configured_model() -> OpenAICompatModel | None:
    s = load_settings()
    return OpenAICompatModel(s.name, s.url) if s.name else None
