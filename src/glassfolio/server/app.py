"""Starlette app: API routes, static UI, security middleware, `serve`."""

import secrets
import webbrowser
from pathlib import Path

import duckdb
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from glassfolio.lake import Lake
from glassfolio.llm import ModelError
from glassfolio.parsing import printable
from starlette.formparsers import MultiPartParser

from glassfolio.server.api import MAX_UPLOAD, Api
from glassfolio.server.security import LocalGuard
from glassfolio.server.assist_api import AssistApi
from glassfolio.server.chat_api import ChatApi
from glassfolio.server.tax_api import TaxApi

STATIC = Path(__file__).resolve().parents[3] / "web" / "dist"
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


def create_app(lake: Lake, token: str, allowed_hosts, static_dir: Path = STATIC) -> Starlette:
    api = Api(lake)
    tax = TaxApi(lake, api.default_as_of)
    assist = AssistApi(lake, api)
    chat = ChatApi(lake, api.default_as_of)
    get = [("/api/meta", api.meta), ("/api/exposure", api.exposure),
           ("/api/company/{ticker}", api.company), ("/api/checks", api.checks),
           ("/api/ops", api.ops), ("/api/changes", api.changes), ("/api/history", api.history),
           ("/api/inbox", api.inbox), ("/api/taxes", tax.overview), ("/api/assist/models", assist.models)]
    post = [("/api/checks", api.run_checks), ("/api/owners", api.add_owner),
            ("/api/accounts", api.add_account), ("/api/profiles", api.add_profile),
            ("/api/import/statement", api.preview_statement), ("/api/import/etf", api.preview_etf),
            ("/api/import/prices", api.import_prices), ("/api/import/commit", api.commit),
            ("/api/inbox/answer", api.answer), ("/api/tax/profile", tax.save_profile),
            ("/api/tax/assign", tax.assign), ("/api/tax/treatment", tax.treatment),
            ("/api/people", tax.add_person), ("/api/import/lots", tax.lots),
            ("/api/assist/model", assist.choose), ("/api/assist/eval", assist.evaluate),
            ("/api/assist/read", assist.read), ("/api/assist/preview", assist.preview),
            ("/api/chat", chat.chat), ("/api/chat/confirm", chat.confirm)]
    routes = [Route(p, _guarded(h), methods=["GET"]) for p, h in get]
    routes += [Route(p, _guarded(h), methods=["POST"]) for p, h in post]
    if (static_dir / "index.html").exists():
        routes.append(Route("/", lambda r: FileResponse(static_dir / "index.html")))
        routes.append(Mount("/", StaticFiles(directory=static_dir)))
    middleware = [Middleware(LocalGuard, token=token, allowed_hosts=allowed_hosts)]
    return Starlette(routes=routes, middleware=middleware)


def serve(lake: Lake, port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    if not (STATIC / "index.html").exists():
        raise ValueError("UI not built; run: pnpm -C web install && pnpm -C web build")
    token = secrets.token_urlsafe(32)
    hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
    url = f"http://127.0.0.1:{port}/?token={token}"
    print(f"Glassfolio is running at\n\n    {url}\n\nPress Ctrl+C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(lake, token, hosts), host="127.0.0.1", port=port, log_level="warning")
