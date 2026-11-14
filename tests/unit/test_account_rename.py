"""Operator username changes preserve identity, ownership and password history."""

import json
from argparse import Namespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from scione.auth.accounts_cli import run_command
from scione.auth.manager import AuthManager
from scione.auth.passwords import hash_password
from scione.auth.store import AuthConflict, audit_events, sessions
from scione.cli import build_parser
from scione.config import Settings

PASSWORD = "Private-synthetic-rename-test-password-2026"


@pytest.fixture
def accounts(tmp_path):
    settings = Settings(
        _env_file=None,
        scione_workspace_dir=tmp_path,
        scione_database_url=None,
        scione_auth_database_url=None,
        scione_auth_enabled=True,
    )
    manager = AuthManager(settings)
    manager.initialize()
    lab = manager.store.create_lab("Preserved laboratory", active=True)
    user = manager.store.create_user(
        "old.account",
        "Lab manager",
        "lab",
        hash_password(PASSWORD),
        lab_id=lab.id,
    )
    manager.store.create_user(
        "occupied",
        "Other operator",
        "maintainer",
        hash_password(PASSWORD),
    )
    with manager.store.engine.begin() as connection:
        manager.store._event(connection, "source_reviewed", user_id=user["id"], lab_id=lab.id)
    yield manager, user, lab, settings
    manager.close()


def test_rename_preserves_identity_password_lab_history_and_revokes_old_sessions(accounts):
    manager, user, lab, _settings = accounts
    original_session, raw = manager.login("old.account", PASSWORD, "offline-fixture")
    renamed, changed = manager.store.rename_user("OLD.ACCOUNT", "Lab1")
    assert changed and renamed["username"] == "lab1"
    for field in ("id", "role", "lab_id", "password_hash", "display_name", "active", "created_at"):
        assert renamed[field] == user[field]
    assert renamed["auth_version"] == user["auth_version"] + 1
    assert manager.authenticate(raw) is None
    with manager.store.engine.connect() as connection:
        assert not connection.execute(
            select(sessions).where(sessions.c.token_hash == original_session.token_hash)
        ).first()
        history = connection.execute(
            select(audit_events.c.event, audit_events.c.user_id, audit_events.c.lab_id).where(
                audit_events.c.user_id == user["id"]
            )
        ).all()
    assert ("source_reviewed", user["id"], lab.id) in history
    assert ("account_renamed", user["id"], lab.id) in history
    with pytest.raises(HTTPException) as caught:
        manager.login("old.account", PASSWORD, "offline-fixture")
    assert caught.value.status_code == 401
    current, _raw = manager.login("lab1", PASSWORD, "offline-fixture")
    assert current.principal.user_id == user["id"] and current.principal.lab_id == lab.id


def test_rename_collision_and_missing_source_cannot_touch_another_identity(accounts):
    manager, user, _lab, _settings = accounts
    _session, raw = manager.login("old.account", PASSWORD, "offline-fixture")
    before = manager.store.user_by_username("occupied")
    with pytest.raises(AuthConflict):
        manager.store.rename_user("old.account", "occupied")
    with pytest.raises(AuthConflict):
        manager.store.rename_user("not.present", "occupied")
    assert manager.store.user_by_username("old.account") == user
    assert manager.store.user_by_username("occupied") == before
    assert manager.authenticate(raw) is not None


def test_exact_rename_retry_is_idempotent_without_revoking_new_sessions(accounts):
    manager, user, _lab, _settings = accounts
    manager.store.rename_user("old.account", "lab1")
    _session, raw = manager.login("lab1", PASSWORD, "offline-fixture")
    first = manager.store.user_by_username("lab1")
    replay, changed = manager.store.rename_user("old.account", "lab1")
    assert not changed and replay == first and replay["id"] == user["id"]
    assert manager.authenticate(raw) is not None
    same, changed = manager.store.rename_user("LAB1", "lab1")
    assert not changed and same == first
    with manager.store.engine.connect() as connection:
        events = connection.execute(
            select(audit_events.c.id).where(
                audit_events.c.event == "account_renamed",
                audit_events.c.user_id == user["id"],
            )
        ).all()
    assert len(events) == 1


@pytest.mark.parametrize("name", ["ab", "../lab1", "invalid space"])
def test_invalid_new_username_cannot_modify_existing_account(accounts, name):
    manager, user, *_ = accounts
    with pytest.raises(ValueError):
        manager.store.rename_user("old.account", name)
    assert manager.store.user_by_username("old.account") == user


def test_rename_cli_emits_only_safe_metadata_and_never_writes_credential_files(
    accounts,
    capsys,
):
    manager, user, _lab, settings = accounts
    private = settings.scione_workspace_dir / "private" / "account-credentials.json"
    private.parent.mkdir()
    private.write_text(json.dumps([{"username": "old.account", "password": PASSWORD}]))
    before = private.read_bytes()
    args = Namespace(account_action="rename", username="old.account", new_username="lab1")
    assert run_command(args, settings) == 0
    output = capsys.readouterr().out
    assert '"username": "lab1"' in output and '"renamed": true' in output
    assert PASSWORD not in output and user["password_hash"] not in output
    assert private.read_bytes() == before
    assert manager.store.user_by_username("lab1")["id"] == user["id"]
    assert run_command(args, settings) == 0
    assert '"renamed": false' in capsys.readouterr().out


def test_supported_cli_parses_the_explicit_rename_pair():
    args = build_parser().parse_args(
        [
            "accounts",
            "rename",
            "--username",
            "old.account",
            "--new-username",
            "lab1",
        ]
    )
    assert args.account_action == "rename" and args.new_username == "lab1"
