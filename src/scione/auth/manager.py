"""Cookie/session lifecycle and origin checks; no customer document access."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from functools import lru_cache
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.responses import Response

from scione.auth.models import LOCAL_PRINCIPAL
from scione.auth.passwords import generated_password, hash_password, verify_password
from scione.auth.store import AuthSession, AuthStore, username_key
from scione.config import Settings


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    # Unknown and disabled usernames perform the same expensive password check.
    # This value belongs to no account and is never an available credential.
    return hash_password(generated_password())


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def csrf_token(token: str, salt: str) -> str:
    return hmac.new(token.encode("ascii"), salt.encode("ascii"), hashlib.sha256).hexdigest()


def _origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.username or parsed.password or parsed.path not in {"", "/"}:
        return ""
    if parsed.query or parsed.fragment:
        return ""
    return parsed.scheme.casefold() + "://" + parsed.netloc.casefold()


class AuthManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = AuthStore(settings)

    @property
    def enabled(self) -> bool:
        return self.settings.scione_auth_enabled

    @property
    def secure_cookies(self) -> bool:
        configured = self.settings.scione_cookie_secure
        return (
            configured
            if configured is not None
            else self.settings.scione_environment == "production"
        )

    @property
    def cookie_name(self) -> str:
        return "__Host-scione_session" if self.secure_cookies else "scione_session"

    def initialize(self) -> None:
        self.store.initialize()
        _dummy_hash()

    def ensure_ready(self) -> None:
        if self.enabled:
            self.store.ensure_ready()
            _dummy_hash()

    def close(self) -> None:
        self.store.close()

    def get_lab(self, lab_id):
        return self.store.get_lab(lab_id)

    def list_labs(self):
        return self.store.list_labs()

    def authenticate(self, raw_token: str | None, now: int | None = None) -> AuthSession | None:
        if not raw_token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw_token):
            return None
        return self.store.load_session(
            token_hash(raw_token), int(time.time()) if now is None else now
        )

    def session_payload(self, session: AuthSession | None, raw_token: str | None) -> dict:
        if not self.enabled:
            return {
                "enabled": False,
                "authenticated": True,
                "user": LOCAL_PRINCIPAL.public(),
                "csrf_token": None,
            }
        return {
            "enabled": True,
            "authenticated": session is not None,
            "user": session.principal.public() if session else None,
            "csrf_token": csrf_token(raw_token, session.csrf_salt)
            if session and raw_token
            else None,
        }

    def origin_allowed(self, origin: str | None, request_origin: str) -> bool:
        if origin is None:
            return True  # Non-browser clients still need the bound CSRF token.
        candidate = _origin(origin)
        if not candidate:
            return False
        allowed = {_origin(value) for value in self.settings.scione_trusted_origins}
        if self.settings.scione_public_url:
            allowed.add(_origin(self.settings.scione_public_url))
        if self.settings.scione_environment == "development":
            allowed.update(
                {"http://localhost:5173", "http://127.0.0.1:5173", _origin(request_origin)}
            )
        return candidate in allowed

    def csrf_valid(self, session, raw_token: str | None, supplied: str | None) -> bool:
        return bool(
            session
            and raw_token
            and supplied
            and re.fullmatch(r"[a-f0-9]{64}", supplied)
            and hmac.compare_digest(supplied, csrf_token(raw_token, session.csrf_salt))
        )

    def set_cookie(self, response: Response, raw_token: str, session: AuthSession):
        response.set_cookie(
            self.cookie_name,
            raw_token,
            max_age=max(0, session.expires_at - int(time.time())),
            httponly=True,
            secure=self.secure_cookies,
            samesite="lax",
            path="/",
        )

    def clear_cookie(self, response: Response):
        response.delete_cookie(
            self.cookie_name,
            path="/",
            secure=self.secure_cookies,
            httponly=True,
            samesite="lax",
        )

    def login(self, username: str, password: str, ip: str, old_token: str | None = None):
        now = int(time.time())
        try:
            normalized = username_key(username)
        except ValueError:
            normalized = "invalid:" + hashlib.sha256(username.encode("utf-8")).hexdigest()
        if not self.store.consume_login_attempt(normalized, ip, now):
            raise HTTPException(
                429,
                "Too many sign-in attempts. Please try again later.",
                headers={"Retry-After": str(self.settings.scione_auth_login_window_seconds)},
            )
        user = self.store.user_by_username(normalized)
        valid = verify_password(password, user["password_hash"] if user else _dummy_hash())
        if not valid or not user or not user["active"] or user["role"] not in {"lab", "maintainer"}:
            raise HTTPException(401, "The username or password is incorrect.")
        lab_id = user["lab_id"]
        if lab_id:
            lab = self.store.get_lab(lab_id)
            if lab is None or not lab.active:
                raise HTTPException(401, "The username or password is incorrect.")
        elif user["role"] == "lab":
            raise HTTPException(401, "The username or password is incorrect.")
        else:
            available = self.store.list_labs()
            lab_id = available[0].id if available else None
        raw_token, salt = secrets.token_urlsafe(32), secrets.token_hex(32)
        self.store.create_session(token_hash(raw_token), salt, user, lab_id, now)
        if old_token and re.fullmatch(r"[A-Za-z0-9_-]{43}", old_token):
            self.store.revoke_session(token_hash(old_token))
        session = self.authenticate(raw_token, now)
        if session is None:
            raise HTTPException(401, "Sign in again to continue.")
        return session, raw_token

    def switch_workspace(self, session: AuthSession, lab_id: str):
        if session.principal.role != "maintainer":
            raise HTTPException(403, "Only system maintainers can switch lab workspaces.")
        raw_token, salt = secrets.token_urlsafe(32), secrets.token_hex(32)
        self.store.rotate_workspace(session, token_hash(raw_token), salt, lab_id, int(time.time()))
        renewed = self.authenticate(raw_token)
        if renewed is None:
            raise HTTPException(401, "Sign in again to continue.")
        return renewed, raw_token
