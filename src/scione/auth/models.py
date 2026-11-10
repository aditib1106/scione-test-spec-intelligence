"""Server-owned identities; client input never selects a lab implicitly."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException

Role = Literal["lab", "maintainer"]


@dataclass(frozen=True)
class AuthLab:
    id: str
    name: str
    active: bool
    created_at: int
    provisioned_at: int | None = None

    @property
    def lab_id(self) -> str:
        return self.id

    @property
    def lab_name(self) -> str:
        return self.name


@dataclass(frozen=True)
class AuthPrincipal:
    user_id: str
    username: str
    display_name: str
    role: Role
    lab_id: str | None
    lab_name: str | None

    @property
    def id(self) -> str:
        return self.user_id

    def public(self) -> dict:
        return {
            "id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "lab_id": self.lab_id,
            "lab_name": self.lab_name,
        }


_principal: ContextVar[AuthPrincipal | None] = ContextVar("scione_auth_principal", default=None)


def current_principal() -> AuthPrincipal | None:
    return _principal.get()


def require_principal() -> AuthPrincipal:
    principal = current_principal()
    if principal is None:
        raise HTTPException(401, "Sign in to continue.")
    return principal


def require_maintainer() -> AuthPrincipal:
    principal = require_principal()
    if principal.role != "maintainer":
        raise HTTPException(403, "This action is available to system maintainers.")
    return principal


LOCAL_PRINCIPAL = AuthPrincipal(
    user_id="local",
    username="local",
    display_name="Local development",
    role="maintainer",
    lab_id=None,
    lab_name=None,
)
