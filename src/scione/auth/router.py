"""Small same-origin login API; credentials are never echoed in validation errors."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from scione.auth.manager import token_hash
from scione.auth.models import require_maintainer
from scione.auth.passwords import hash_password, verify_password
from scione.auth.store import AuthStoreError

router = APIRouter(prefix="/api/auth", tags=["Account"])


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80)
    password: SecretStr


class WorkspaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lab_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr
    new_password: SecretStr


async def _body(request: Request, schema):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 16384:
            raise HTTPException(413, "The sign-in request is too large.")
    try:
        value = json.loads(data.decode("utf-8"))
        parsed = schema.model_validate(value)
        if hasattr(parsed, "username"):
            parsed.username.encode("utf-8")
        for name in ("password", "current_password", "new_password"):
            secret = getattr(parsed, name, None)
            if secret is not None:
                text = secret.get_secret_value()
                if not 1 <= len(text) <= 1024:
                    raise ValueError("Invalid password size")
                text.encode("utf-8")
        return parsed
    except (ValueError, UnicodeError, ValidationError):
        raise HTTPException(400, "Check the account details and try again.") from None


def _manager(request: Request):
    return request.app.state.auth_manager


@router.get("/session")
def session(request: Request):
    manager = _manager(request)
    current = getattr(request.state, "auth_session", None)
    raw = getattr(request.state, "auth_raw_token", None)
    response = JSONResponse(manager.session_payload(current, raw))
    response.headers["Cache-Control"] = "no-store"
    if manager.enabled and raw and current is None:
        manager.clear_cookie(response)
    return response


@router.post("/login")
async def login(request: Request):
    manager = _manager(request)
    if not manager.enabled:
        raise HTTPException(404, "Accounts are disabled in local development mode.")
    body = await _body(request, LoginBody)
    try:
        current, raw = await run_in_threadpool(
            manager.login,
            body.username,
            body.password.get_secret_value(),
            request.client.host if request.client else "unknown",
            request.cookies.get(manager.cookie_name),
        )
    except (AuthStoreError, SQLAlchemyError, OSError):
        raise HTTPException(
            503, "Sign-in is temporarily unavailable. Please try again shortly."
        ) from None
    response = JSONResponse(manager.session_payload(current, raw))
    response.headers["Cache-Control"] = "no-store"
    manager.set_cookie(response, raw, current)
    return response


@router.post("/logout")
def logout(request: Request):
    manager = _manager(request)
    raw = getattr(request.state, "auth_raw_token", None)
    if manager.enabled and raw:
        manager.store.revoke_session(token_hash(raw))
    response = JSONResponse(
        {"enabled": manager.enabled, "authenticated": False, "user": None, "csrf_token": None}
    )
    response.headers["Cache-Control"] = "no-store"
    manager.clear_cookie(response)
    return response


@router.get("/workspaces")
def workspaces(request: Request):
    require_maintainer()
    manager = _manager(request)
    if not manager.enabled:
        return {"labs": []}
    return {"labs": [{"lab_id": lab.id, "lab_name": lab.name} for lab in manager.list_labs()]}


@router.post("/workspace")
async def workspace(request: Request):
    require_maintainer()
    manager = _manager(request)
    if not manager.enabled:
        raise HTTPException(404, "Accounts are disabled in local development mode.")
    body = await _body(request, WorkspaceBody)
    current, raw = await run_in_threadpool(
        manager.switch_workspace, request.state.auth_session, body.lab_id
    )
    response = JSONResponse(manager.session_payload(current, raw))
    response.headers["Cache-Control"] = "no-store"
    manager.set_cookie(response, raw, current)
    return response


@router.post("/password")
async def password(request: Request):
    manager = _manager(request)
    if not manager.enabled:
        raise HTTPException(404, "Accounts are disabled in local development mode.")
    body = await _body(request, PasswordBody)
    principal = request.state.auth_principal
    user = await run_in_threadpool(manager.store.user_by_username, principal.username)
    if not user or not await run_in_threadpool(
        verify_password, body.current_password.get_secret_value(), user["password_hash"]
    ):
        raise HTTPException(400, "The current password is incorrect.")
    try:
        encoded = await run_in_threadpool(hash_password, body.new_password.get_secret_value())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    await run_in_threadpool(manager.store.reset_password, principal.user_id, encoded)
    response = JSONResponse(
        {"enabled": True, "authenticated": False, "user": None, "csrf_token": None}
    )
    response.headers["Cache-Control"] = "no-store"
    manager.clear_cookie(response)
    return response
