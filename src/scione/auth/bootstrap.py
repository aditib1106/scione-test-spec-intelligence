"""Explicit, idempotent account bootstrap from server-only deployment secrets."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from scione.auth.passwords import hash_password, validate_password
from scione.auth.store import AuthStoreError, username_key


class BootstrapAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=80)
    password: SecretStr
    display_name: str = Field(min_length=1, max_length=200)
    role: Literal["lab", "maintainer"]
    lab_name: str | None = Field(default=None, max_length=300)
    seed_development_lab: bool = False


def bootstrap_from_environment(manager, provision_callback=None) -> dict:
    """Create missing accounts only; never reset or reseed an existing account.

    Passwords live in the platform's secret environment, never in source,
    command arguments or logs. A lab is activated only after its independently
    provisioned data storage is ready. No model service is called.
    """
    secret = manager.settings.scione_bootstrap_accounts
    if secret is None or not secret.get_secret_value().strip():
        return {"created": 0, "existing": 0}
    try:
        raw = json.loads(secret.get_secret_value())
        if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
            raise ValueError("Expected a bounded account list")
        accounts = [BootstrapAccount.model_validate(item) for item in raw]
        names = [username_key(account.username) for account in accounts]
        if len(set(names)) != len(names):
            raise ValueError("Repeated account")
        for account in accounts:
            validate_password(account.password.get_secret_value())
            if account.role == "lab" and not (account.lab_name or "").strip():
                raise ValueError("Lab accounts need a lab name")
    except (ValueError, ValidationError, TypeError):
        raise AuthStoreError(
            "SCIONE_BOOTSTRAP_ACCOUNTS must contain valid, distinct accounts and strong passwords."
        ) from None
    created = existing = 0
    for account in accounts:
        username = username_key(account.username)
        if manager.store.user_by_username(username) is not None:
            existing += 1
            continue
        lab_id = None
        if account.lab_name:
            lab = manager.store.lab_by_name(account.lab_name)
            if lab is None:
                lab = manager.store.create_lab(account.lab_name)
            if lab.provisioned_at is None:
                if provision_callback is None:
                    raise AuthStoreError(
                        "Lab storage must be provisioned before account bootstrap."
                    )
                provision_callback(lab, seed_development_lab=account.seed_development_lab)
                lab = manager.store.mark_lab_provisioned(lab.id)
            elif not lab.active:
                raise AuthStoreError(
                    "An existing lab is disabled; bootstrap does not reactivate it."
                )
            lab_id = lab.id
        manager.store.create_user(
            username,
            account.display_name,
            account.role,
            hash_password(account.password.get_secret_value()),
            lab_id=lab_id,
        )
        created += 1
    return {"created": created, "existing": existing}
