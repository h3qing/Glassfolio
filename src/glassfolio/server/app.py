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
from glassfolio.parsing import printable
from glassfolio.server.api import Api
from glassfolio.server.security import LocalGuard

STATIC = Path(__file__).resolve().parents[3] / "web" / "dist"


def _guarded(handler):
    async def wrapped(request: Request):
        try:
            return await handler(request)
        except (ValueError, KeyError, duckdb.Error) as exc:
            detail = f"missing field: {exc}" if isinstance(exc, KeyError) else str(exc)
            return JSONResponse({"error": printable(detail)}, status_code=400)
    return wrapped


def create_app(lake: Lake, token: str, allowed_hosts, static_dir: Path = STATIC) -> Starlette:
    api = Api(lake)
    get = [("/api/meta", api.meta), ("/api/exposure", api.exposure),
           ("/api/company/{ticker}", api.company), ("/api/checks", api.checks),
           ("/api/ops", api.ops)]
    post = [("/api/checks", api.run_checks), ("/api/owners", api.add_owner),
            ("/api/accounts", api.add_account), ("/api/profiles", api.add_profile),
            ("/api/import/statement", api.preview_statement), ("/api/import/etf", api.preview_etf),
            ("/api/import/prices", api.import_prices), ("/api/import/commit", api.commit)]
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
