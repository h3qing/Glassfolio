"""Guards for a localhost server holding financial data.

Other websites, and other servers on 127.0.0.1 (cookies are not port-scoped),
can reach this server through the browser, so:
- Host must be exactly ours (defeats DNS rebinding);
- the launch token in the URL works once and is exchanged for a separate random
  session secret in an HttpOnly, SameSite=Strict cookie named after our port;
- API calls need that cookie and our custom header (forces a CORS preflight that we
  never approve), and must not be cross-site per Origin / Sec-Fetch-Site;
- pages refuse to be framed (no clickjacking of Import/confirm).
"""

import hmac
import secrets
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

API_HEADER = "x-glassfolio"
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class LocalGuard(BaseHTTPMiddleware):
    def __init__(self, app, token: str, allowed_hosts: Iterable[str]):
        super().__init__(app)
        self.token: str | None = token  # launch token; consumed on first use
        self.session = secrets.token_urlsafe(32)
        self.hosts = frozenset(allowed_hosts)
        self.origins = frozenset(f"http://{h}" for h in self.hosts)
        port = next(iter(sorted(self.hosts))).rsplit(":", 1)[-1]
        self.cookie = f"gf_session_{port}" if port.isdigit() else "gf_session"

    def _login(self, supplied: str) -> Response:
        if self.token is None or not _same(supplied, self.token):
            return PlainTextResponse(
                "This link was already used or is wrong. Restart `glassfolio serve` for a new one.",
                status_code=403)
        self.token = None
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(self.cookie, self.session, httponly=True, samesite="strict")
        return response

    def _api_refusal(self, request: Request) -> Response | None:
        if not _same(request.cookies.get(self.cookie, ""), self.session):
            return JSONResponse({"error": "open the link printed by `glassfolio serve`"}, status_code=401)
        if request.headers.get(API_HEADER) != "1":
            return JSONResponse({"error": "missing client header"}, status_code=403)
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        if (origin is not None and origin not in self.origins) or site not in (None, "same-origin"):
            return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        return None

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.headers.get("host") not in self.hosts:
            return PlainTextResponse("unknown host", status_code=421)
        supplied = request.query_params.get("token")
        if request.url.path == "/" and supplied is not None:
            response = self._login(supplied)
        elif request.url.path.startswith("/api/"):
            response = self._api_refusal(request) or await call_next(request)
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response
