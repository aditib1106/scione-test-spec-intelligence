"""Private account provisioning, password handling and deployment fail-closed rules."""

import json
import stat
from argparse import Namespace

import pytest
from pydantic import SecretStr, ValidationError

from scione.auth.accounts_cli import _credential_destination, run_command
from scione.auth.bootstrap import bootstrap_from_environment
from scione.auth.manager import AuthManager
from scione.auth.passwords import generated_password, hash_password, verify_password
from scione.auth.store import AuthStoreError
from scione.config import Settings

PASSWORD = "Synthetic-private-test-password-2026"


def configuration(tmp_path, **overrides):
    return Settings(
        _env_file=None,
        scione_workspace_dir=tmp_path / "workspace",
        scione_database_url=None,
        scione_auth_database_url=None,
        **overrides,
    )


def test_passwords_have_independent_salts_and_reject_unbounded_hash_work():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and PASSWORD not in first
    assert verify_password(PASSWORD, first)
    assert not verify_password("different-password", first)
    for invalid in ("bad", first.replace("$600000$", "$999999999$"), first + "$extra"):
        assert not verify_password(PASSWORD, invalid)
    assert not verify_password("\ud800", first)
    with pytest.raises(ValueError, match="15"):
        hash_password("short-password")
    assert len(generated_password()) >= 32


@pytest.mark.parametrize(
    "override",
    [
        {"scione_auth_enabled": False},
        {"scione_public_url": None},
        {"scione_public_url": "http://example.test"},
        {"scione_cookie_secure": False},
        {"scione_trusted_origins": ["http://example.test"]},
        {"scione_public_url": "https://example.test/login"},
    ],
)
def test_production_cannot_start_with_anonymous_or_insecure_configuration(override):
    values = {
        "scione_environment": "production",
        "scione_auth_enabled": True,
        "scione_public_url": "https://example.test",
    }
    values.update(override)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_mysql_cloud_urls_use_installed_driver_and_never_echo_secrets():
    secret = "synthetic-connection-password"
    settings = Settings(
        _env_file=None,
        scione_database_url=f"mysql://account:{secret}@db.test/main",
        scione_auth_database_url=f"mysql://account:{secret}@db.test/accounts",
    )
    assert settings.scione_database_url.get_secret_value().startswith("mysql+pymysql://")
    assert settings.scione_auth_database_url.get_secret_value().startswith("mysql+pymysql://")
    assert secret not in repr(settings)
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, scione_database_url=f"not-a-url-{secret}")
    assert secret not in str(caught.value)


def test_auth_database_missing_does_not_fall_back_to_anonymous(tmp_path):
    manager = AuthManager(configuration(tmp_path, scione_auth_enabled=True))
    try:
        with pytest.raises(AuthStoreError):
            manager.ensure_ready()
        manager.initialize()
        manager.ensure_ready()
    finally:
        manager.close()


def test_bootstrap_is_idempotent_and_never_resets_or_reseeds(tmp_path):
    account = {
        "username": "lab.account",
        "password": PASSWORD,
        "display_name": "Lab user",
        "role": "lab",
        "lab_name": "Synthetic lab",
        "seed_development_lab": True,
    }
    manager = AuthManager(
        configuration(
            tmp_path,
            scione_bootstrap_accounts=json.dumps([account]),
        )
    )
    manager.initialize()
    provisioned = []

    def provision(lab, *, seed_development_lab):
        assert not lab.active
        provisioned.append((lab.id, seed_development_lab))

    try:
        assert bootstrap_from_environment(manager, provision) == {"created": 1, "existing": 0}
        user = manager.store.user_by_username("lab.account")
        original_hash = user["password_hash"]
        assert manager.store.get_lab(user["lab_id"]).active
        assert len(provisioned) == 1 and provisioned[0][1]
        # Updated deployment variables cannot silently reset an existing account.
        account["password"] = "Another-private-synthetic-password-2026"
        account["lab_name"] = "An unrelated lab"
        manager.settings.scione_bootstrap_accounts = SecretStr(json.dumps([account]))
        assert bootstrap_from_environment(manager, provision) == {"created": 0, "existing": 1}
        assert manager.store.user_by_username("lab.account")["password_hash"] == original_hash
        assert len(provisioned) == 1 and len(manager.store.list_labs()) == 1
    finally:
        manager.close()


def test_failed_lab_provisioning_cannot_create_a_usable_account(tmp_path):
    account = {
        "username": "lab.account",
        "password": PASSWORD,
        "display_name": "Lab user",
        "role": "lab",
        "lab_name": "Unready lab",
    }
    manager = AuthManager(
        configuration(
            tmp_path,
            scione_bootstrap_accounts=json.dumps([account]),
        )
    )
    manager.initialize()

    def unavailable(lab, **kwargs):
        raise AuthStoreError("Storage unavailable")

    try:
        with pytest.raises(AuthStoreError):
            bootstrap_from_environment(manager, unavailable)
        assert manager.store.user_by_username("lab.account") is None
        assert not manager.store.lab_by_name("Unready lab").active
        assert not manager.store.list_labs()
    finally:
        manager.close()


def test_invalid_bootstrap_never_exposes_or_partially_creates_credentials(tmp_path):
    valid = {
        "username": "operator",
        "password": PASSWORD,
        "display_name": "Operator",
        "role": "maintainer",
    }
    manager = AuthManager(
        configuration(
            tmp_path,
            scione_bootstrap_accounts=json.dumps([valid, {**valid, "username": "OPERATOR"}]),
        )
    )
    manager.initialize()
    try:
        with pytest.raises(AuthStoreError) as caught:
            bootstrap_from_environment(manager)
        assert PASSWORD not in str(caught.value)
        assert manager.store.list_users() == []
    finally:
        manager.close()


def test_cli_writes_generated_password_to_private_file_only(tmp_path, capsys):
    settings = configuration(tmp_path)
    destination = tmp_path / "private" / "credentials.json"
    args = Namespace(
        account_action="create",
        username="operator",
        display_name="Operator",
        role="maintainer",
        lab_id=None,
        lab_name=None,
        credentials_file=destination,
    )
    assert run_command(args, settings) == 0
    record = json.loads(destination.read_text())[0]
    assert record["password"] not in capsys.readouterr().out
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    manager = AuthManager(settings)
    try:
        user = manager.store.user_by_username("operator")
        assert verify_password(record["password"], user["password_hash"])
    finally:
        manager.close()


def test_cli_refuses_credentials_in_public_assets_even_if_gitignored(tmp_path):
    assets = tmp_path / "web-assets"
    settings = configuration(tmp_path, scione_frontend_dir=assets)
    with pytest.raises(ValueError, match="public application assets"):
        _credential_destination(settings, assets / "secret.json")
    assert not assets.exists()
