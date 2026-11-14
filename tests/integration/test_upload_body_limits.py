"""Large declared and streaming bodies are stopped before unbounded spooling."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from scione.api import body_limits


def limited_app(monkeypatch):
    monkeypatch.setattr(body_limits, "MAX_UPLOAD_BODY_BYTES", 1024)
    app = FastAPI()
    app.add_middleware(body_limits.UploadBodyLimitMiddleware)

    @app.post("/api/specifications")
    async def upload(request: Request):
        return {"received": len(await request.body())}

    return app


def test_declared_oversized_upload_is_rejected_before_endpoint(monkeypatch):
    with TestClient(limited_app(monkeypatch)) as client:
        assert client.post("/api/specifications", content=b"x" * 1025).status_code == 413
        accepted = client.post("/api/specifications", content=b"x" * 1024)
        assert accepted.status_code == 200 and accepted.json()["received"] == 1024


def test_streaming_upload_without_content_length_is_bounded(monkeypatch):
    def chunks():
        yield b"x" * 600
        yield b"y" * 600

    with TestClient(limited_app(monkeypatch)) as client:
        response = client.post("/api/specifications", content=chunks())
        assert response.status_code == 413
