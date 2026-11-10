"""Same-origin browser policy, including responses rejected before routing."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, production: bool = False):
        self.app, self.production = app, production

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def protected_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "SAMEORIGIN"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                if scope.get("path", "").startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
                if self.production:
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                    headers["Content-Security-Policy"] = (
                        "default-src 'self'; script-src 'self'; "
                        "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
                        "img-src 'self' data: blob:; connect-src 'self'; "
                        "frame-src 'self' blob:; frame-ancestors 'self'; "
                        "object-src 'none'; base-uri 'self'; form-action 'self'"
                    )
            await send(message)

        return await self.app(scope, receive, protected_send)
