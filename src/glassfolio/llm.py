"""Local model access through any OpenAI-compatible server (Ollama, LM Studio,
llama.cpp server, MLX server, vLLM, ...). Nothing here assumes a particular model.

- Red line (spec §7): real data only goes to a model on this machine, so the
  client refuses any base URL that isn't loopback, and ignores proxies and redirects.
- Models differ in what they support, so structured output is negotiated: JSON
  schema → JSON mode → JSON pulled out of plain text. Optional request fields a
  server rejects are dropped and the request retried.
- The model only returns configuration; callers validate every answer themselves.
"""

import ipaddress
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:11434/v1"  # Ollama's default; any OpenAI-compatible server works
TIMEOUT = 120
MODES = ("json_schema", "json_object", "text")


class ModelError(Exception):
    pass


class ChatModel(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...


def require_loopback(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ModelError("model URL must be http(s)")
    host = parts.hostname or ""
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False  # other names could re-resolve elsewhere between check and connect
    if not loopback:
        raise ModelError("the model must run on this machine (localhost or a loopback address)")
    return url.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError("model server tried to redirect; refused")


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def _http(url: str, body: dict | None = None, timeout: float = TIMEOUT) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise _Rejected(exc.code) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise ModelError(f"local model server not reachable ({type(exc).__name__})") from None
    except json.JSONDecodeError as exc:
        raise ModelError("model server did not answer with JSON") from exc


class _Rejected(ModelError):
    def __init__(self, status: int):
        super().__init__(f"model server answered HTTP {status}")
        self.status = status


def extract_json(text: str) -> dict:
    """Parse JSON from a reply that may wrap it in prose or ``` fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    candidates = [fenced.group(1)] if fenced else []
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    raise ModelError("model reply contained no JSON object")


@dataclass(frozen=True)
class OpenAICompatModel:
    name: str
    base_url: str = DEFAULT_URL
    mode: str = "json_schema"   # best structured-output mode known to work; negotiated down on rejection

    def _body(self, system: str, user: str, schema: dict, mode: str, extras: bool) -> dict:
        schema_hint = "" if mode == "json_schema" else (
            "\n\nReply with only a JSON object matching this JSON Schema:\n" + json.dumps(schema))
        body = {"model": self.name, "temperature": 0,
                "messages": [{"role": "system", "content": system + schema_hint},
                             {"role": "user", "content": user}]}
        if mode == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "answer", "schema": schema, "strict": True}}
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        if extras:
            body["reasoning_effort"] = "none"  # fast path where supported; dropped if rejected
        return body

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        url = require_loopback(self.base_url) + "/chat/completions"
        modes = MODES[MODES.index(self.mode):]
        last: ModelError | None = None
        for mode in modes:
            for extras in (True, False):
                try:
                    reply = _http(url, self._body(system, user, schema, mode, extras))
                    content = reply["choices"][0]["message"].get("content") or ""
                    return json.loads(content) if mode == "json_schema" else extract_json(content)
                except _Rejected as exc:
                    last = exc
                    if exc.status not in (400, 404, 422, 500, 501):
                        raise
                except (KeyError, IndexError, TypeError, json.JSONDecodeError, ModelError) as exc:
                    last = exc if isinstance(exc, ModelError) else ModelError("unexpected model reply")
                    break  # the server accepted the request; try the next mode
        raise last or ModelError("model gave no usable answer")


def list_models(base_url: str) -> tuple[str, ...]:
    """Models the local server offers (empty if it isn't running)."""
    try:
        reply = _http(require_loopback(base_url) + "/models", timeout=3)
    except ModelError:
        return ()
    return tuple(sorted(m.get("id", "") for m in reply.get("data", []) if m.get("id")))
