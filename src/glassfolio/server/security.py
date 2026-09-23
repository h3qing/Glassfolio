"""Guards for a localhost server holding financial data.

Other websites open in the same browser can send requests to 127.0.0.1, so:
- Host must be one of ours (defeats DNS rebinding);
- API calls need the session cookie, set only via the one-time token URL
  printed in the terminal; SameSite=Strict keeps it off cross-site requests;
- API calls must come from our own page: Origin, if present, must match.
"""

import hmac
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

COOKIE = "gf_session"


class LocalGuard(BaseHTTPMiddleware):
    def __init__(self, app, token: str, allowed_hosts: Iterable[str]):
        super().__init__(app)
        self.token = token
        self.hosts = frozenset(allowed_hosts)

    def _authorized(self, request: Request) -> bool:
        return hmac.compare_digest(request.cookies.get(COOKIE, ""), self.token)

    def _same_origin(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        return origin is None or origin.split("://", 1)[-1] in self.hosts

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.headers.get("host") not in self.hosts:
            return PlainTextResponse("unknown host", status_code=421)
        supplied = request.query_params.get("token")
        if request.url.path == "/" and supplied:
            if not hmac.compare_digest(supplied, self.token):
                return PlainTextResponse("invalid token", status_code=403)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE, self.token, httponly=True, samesite="strict")
            return response
        if request.url.path.startswith("/api/"):
            if not self._authorized(request):
                return JSONResponse({"error": "open the link printed by `glassfolio serve`"},
                                    status_code=401)
            if not self._same_origin(request):
                return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        return await call_next(request)
