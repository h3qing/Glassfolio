"""Starlette app: API routes, static UI, security middleware, `serve`."""

import os
import re
import sys
import threading
import secrets
import socket
import webbrowser
from pathlib import Path

import duckdb
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from glassfolio.lake import Lake
from glassfolio.paths import resource_root
from glassfolio.llm import ModelError
from glassfolio.parsing import printable
from starlette.formparsers import MultiPartParser

from glassfolio.server.api import MAX_UPLOAD, Api
from glassfolio.server.security import LocalGuard
from glassfolio.server.assist_api import AssistApi
from glassfolio.server.chat_api import ChatApi
from glassfolio.server.tax_api import TaxApi

def static_dir() -> Path:
    """The built UI. GLASSFOLIO_STATIC may point elsewhere in development only: the packaged
    service always serves its own bundled copy."""
    override = os.environ.get("GLASSFOLIO_STATIC")
    if override and not getattr(sys, "frozen", False):
        return Path(override)
    return resource_root() / "web" / "dist"


STATIC = static_dir()
SHELLS = (None, "tauri")
# Keep uploads in memory: Starlette would otherwise spool files over 1 MB to $TMPDIR.
MultiPartParser.spool_max_size = MAX_UPLOAD + 1


def _guarded(handler):
    async def wrapped(request: Request):
        try:
            return await handler(request)
        except (ValueError, KeyError, duckdb.Error, ModelError) as exc:
            detail = f"missing field: {exc}" if isinstance(exc, KeyError) else str(exc)
            return JSONResponse({"error": printable(detail)}, status_code=400)
    return wrapped


def _index(static_dir: Path, shell: str | None):
    """index.html, marked with the shell it runs in (the desktop app shows native glass behind it)."""
    html = (static_dir / "index.html").read_text()
    if shell:
        html = re.sub(r"<html([^>]*)>", lambda m: f'<html{m.group(1)} data-shell="{shell}">', html, count=1)

    async def index(request: Request) -> HTMLResponse:
        return HTMLResponse(html)
    return index


def create_app(lake: Lake, token: str, allowed_hosts, static_dir: Path = STATIC,
               shell: str | None = None) -> Starlette:
    if shell not in SHELLS:
        raise ValueError(f"shell must be one of {SHELLS}")
    api = Api(lake)
    tax = TaxApi(lake, api.default_as_of)
    assist = AssistApi(lake, api)
    chat = ChatApi(lake, api.default_as_of)
    get = [("/api/meta", api.meta), ("/api/exposure", api.exposure),
           ("/api/company/{ticker}", api.company), ("/api/checks", api.checks),
           ("/api/ops", api.ops), ("/api/changes", api.changes), ("/api/history", api.history),
           ("/api/inbox", api.inbox), ("/api/taxes", tax.overview), ("/api/assist/models", assist.models),
           ("/api/jobs", assist.jobs_list), ("/api/jobs/{job_id}", assist.job)]
    post = [("/api/checks", api.run_checks), ("/api/owners", api.add_owner),
            ("/api/accounts", api.add_account), ("/api/profiles", api.add_profile),
            ("/api/import/statement", api.preview_statement), ("/api/import/etf", api.preview_etf),
            ("/api/import/prices", api.import_prices), ("/api/import/commit", api.commit),
            ("/api/inbox/answer", api.answer), ("/api/tax/profile", tax.save_profile),
            ("/api/tax/assign", tax.assign), ("/api/tax/treatment", tax.treatment),
            ("/api/people", tax.add_person), ("/api/import/lots", tax.lots),
            ("/api/assist/model", assist.choose), ("/api/assist/eval", assist.evaluate),
            ("/api/assist/read", assist.read), ("/api/assist/preview", assist.preview),
            ("/api/chat", chat.chat), ("/api/chat/confirm", chat.confirm),
            ("/api/settings/touch-id", assist.touch_id), ("/api/jobs/forget", assist.forget_job)]
    routes = [Route(p, _guarded(h), methods=["GET"]) for p, h in get]
    routes += [Route(p, _guarded(h), methods=["POST"]) for p, h in post]
    if (static_dir / "index.html").exists():
        routes.append(Route("/", _index(static_dir, shell)))
        routes.append(Mount("/", StaticFiles(directory=static_dir)))
    middleware = [Middleware(LocalGuard, token=token, allowed_hosts=allowed_hosts)]
    return Starlette(routes=routes, middleware=middleware)


def exit_when_closed(stream, exit_now=lambda: os._exit(0)) -> None:
    """The desktop app keeps our stdin open; when it quits or crashes, stdin closes and we follow."""
    while stream.readline():
        pass
    exit_now()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bind_local(port: int) -> socket.socket:
    """Bind 127.0.0.1 now (port 0: any free one), so no other process can take the port
    between announcing it and serving on it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    return sock


def serve(lake: Lake, port: int = 8765, open_browser: bool = True, shell: str | None = None) -> None:
    """Serve the UI on 127.0.0.1. port 0 picks a free one. The last stdout line before
    serving is machine-readable for the desktop app: `GLASSFOLIO_READY <url>`."""
    import uvicorn

    if not (STATIC / "index.html").exists():
        raise ValueError("UI not built; run: pnpm -C web install && pnpm -C web build")
    sock = bind_local(port)
    port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
    url = f"http://127.0.0.1:{port}/?token={token}"
    if shell is None:
        print(f"Glassfolio is running at\n\n    {url}\n\nPress Ctrl+C to stop.", flush=True)
    print(f"GLASSFOLIO_READY {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    if shell == "tauri":
        threading.Thread(target=exit_when_closed, args=(sys.stdin,), daemon=True).start()
    config = uvicorn.Config(create_app(lake, token, hosts, shell=shell), log_level="warning")
    uvicorn.Server(config).run(sockets=[sock])
