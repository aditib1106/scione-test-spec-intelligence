"""Reject oversized file uploads before multipart parsing fills temporary storage."""

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from scione.specifications.store import MAX_PDF_BYTES

MAX_UPLOAD_BODY_BYTES = MAX_PDF_BYTES + 1024 * 1024


class UploadBodyLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (
            scope["type"] != "http" or scope.get("method") != "POST"
            or scope.get("path", "").rstrip("/") != "/api/specifications"
        ):
            return await self.app(scope, receive, send)
        length = Headers(scope=scope).get("content-length")
        if length is not None:
            try:
                invalid = int(length) < 0
                excessive = int(length) > MAX_UPLOAD_BODY_BYTES
            except ValueError:
                invalid, excessive = True, False
            if invalid or excessive:
                response = JSONResponse(
                    {"detail": "Choose a PDF smaller than 25 MB."}, 413 if excessive else 400
                )
                return await response(scope, receive, send)
        count = 0

        async def limited_receive():
            nonlocal count
            message = await receive()
            if message["type"] == "http.request":
                count += len(message.get("body", b""))
                if count > MAX_UPLOAD_BODY_BYTES:
                    raise HTTPException(413, "Choose a PDF smaller than 25 MB.")
            return message

        return await self.app(scope, limited_receive, send)
