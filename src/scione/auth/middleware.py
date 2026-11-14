"""Pure ASGI authentication with request-local, server-owned lab identities."""

from __future__ import annotations

import re

from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from scione.auth.manager import AuthManager
from scione.auth.models import LOCAL_PRINCIPAL, _principal
from scione.auth.store import AuthStoreError

_PUBLIC = {"/api/health", "/api/auth/session", "/api/auth/login"}
_SAFE = {"GET", "HEAD", "OPTIONS"}


def maintainer_only(path: str, method: str) -> bool:
    # Scope changes return the same safe business comparison as the report UI.
    # The remaining legacy report routes expose internal extraction artifacts.
    if method == "POST" and re.fullmatch(r"/api/reports/[0-9a-f]{16}/exclusions", path):
        return False
    return bool(
        path.startswith(
            ("/api/diagnostics/", "/api/developer/", "/api/reports/", "/api/snapshots/")
        )
        or path.endswith("/artifacts")
        or path
        in {
            "/api/workspace/reset",
            "/api/lab/example",
            "/api/specifications/demo",
            "/api/specifications/worked-example",
        }
        or (method not in _SAFE and re.match(r"^/api/(?:catalog|method-bank)(?:/|$)", path))
    )


class AuthMiddleware:
    def __init__(self, app, manager: AuthManager):
        self.app, self.manager = app, manager

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        path, method = request.url.path, request.method
        manager = self.manager
        session = None
        raw_token = request.cookies.get(manager.cookie_name)
        if manager.enabled:
            try:
                session = await run_in_threadpool(manager.authenticate, raw_token)
            except (AuthStoreError, SQLAlchemyError, OSError):
                response = JSONResponse(
                    {"detail": "Sign-in is temporarily unavailable. Please retry."}, 503
                )
                await response(scope, receive, send)
                return
            if path not in _PUBLIC and method != "OPTIONS" and session is None:
                await JSONResponse({"detail": "Sign in to continue."}, 401)(scope, receive, send)
                return
            if session and session.principal.role != "maintainer" and maintainer_only(path, method):
                await JSONResponse(
                    {"detail": "This action is available to system maintainers."}, 403
                )(scope, receive, send)
                return
            if method not in _SAFE:
                if request.headers.get(
                    "sec-fetch-site"
                ) == "cross-site" or not manager.origin_allowed(
                    request.headers.get("origin"), str(request.base_url)
                ):
                    await JSONResponse({"detail": "Open this application to make changes."}, 403)(
                        scope, receive, send
                    )
                    return
                if path != "/api/auth/login" and not manager.csrf_valid(
                    session, raw_token, request.headers.get("x-csrf-token")
                ):
                    await JSONResponse({"detail": "Your session changed. Refresh and retry."}, 403)(
                        scope, receive, send
                    )
                    return
        principal = session.principal if session else None if manager.enabled else LOCAL_PRINCIPAL
        scope.setdefault("state", {}).update(
            auth_principal=principal, auth_session=session, auth_raw_token=raw_token
        )
        context_token = _principal.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _principal.reset(context_token)
