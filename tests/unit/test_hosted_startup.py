"""Hosted boot must preserve data, require accounts and use persistent storage."""

import json

import pytest
from pydantic import SecretStr

from scione.api.tenancy import TenantWorkspaceRegistry
from scione.auth.manager import AuthManager
from scione.config import ConfigurationError, Settings
from scione.deployment import (
    hosted_port,
    initialize_hosted_storage,
    prepare_data_directory,
    prepare_host_environment,
)


def test_hosted_defaults_use_railway_origin_without_overwriting_operator_settings():
    env = {"RAILWAY_PUBLIC_DOMAIN": "private-trial.up.railway.app"}
    prepare_host_environment(env)
    assert env["SCIONE_AUTH_ENABLED"] == "true"
    assert env["SCIONE_ENVIRONMENT"] == "production"
    assert env["SCIONE_PUBLIC_URL"] == "https://private-trial.up.railway.app"
    assert "private-trial.up.railway.app" in json.loads(env["SCIONE_ALLOWED_HOSTS"])
    env["SCIONE_PUBLIC_URL"] = "https://lab.example.org"
    env["SCIONE_ALLOWED_HOSTS"] = '["lab.example.org"]'
    prepare_host_environment(env)
    assert env["SCIONE_PUBLIC_URL"] == "https://lab.example.org"
    assert env["SCIONE_ALLOWED_HOSTS"] == '["lab.example.org"]'


@pytest.mark.parametrize("port", ["0", "-1", "80", "65536", "abc"])
def test_invalid_hosted_port_is_rejected(port):
    with pytest.raises(ConfigurationError, match="PORT"):
        hosted_port(port)


def test_hosted_port_accepts_platform_value():
    assert hosted_port(None) == 8080
    assert hosted_port("9000") == 9000


def test_railway_does_not_silently_use_ephemeral_upload_storage(tmp_path):
    config = Settings(scione_workspace_dir=tmp_path / "files", scione_database_url=None)
    with pytest.raises(ConfigurationError, match="Attach"):
        prepare_data_directory(config, environ={"RAILWAY_SERVICE_ID": "test"})
    assert not config.scione_workspace_dir.exists()


def test_bootstrap_is_idempotent_and_preserves_changed_lab_data(tmp_path):
    accounts = [{
        "username": "hosted.test", "password": "Generated-test-password-not-a-login1",
        "display_name": "Fixture operator", "role": "maintainer", "lab_name": "Fixture Lab",
    }]
    config = Settings(
        scione_workspace_dir=tmp_path,
        scione_database_url=None,
        scione_auth_database_url=None,
        scione_auth_enabled=True,
        scione_bootstrap_accounts=SecretStr(json.dumps(accounts)),
    )
    assert initialize_hosted_storage(config, attempts=1)["created"] == 1
    manager = AuthManager(config)
    lab = manager.list_labs()[0]
    original_user = manager.store.user_by_username("hosted.test")
    registry = TenantWorkspaceRegistry(config)
    workspace = registry.workspace_for(lab.id)
    current = workspace.labs.load_or_create()
    workspace.labs.save(current.model_copy(update={"description": "A later lab edit"}))
    registry.close_all()
    manager.close()
    assert initialize_hosted_storage(config, attempts=1)["created"] == 0
    again = AuthManager(config)
    assert again.store.user_by_username("hosted.test")["password_hash"] == original_user[
        "password_hash"
    ]
    registry = TenantWorkspaceRegistry(config)
    assert registry.workspace_for(lab.id).labs.load_or_create().description == "A later lab edit"
    registry.close_all()
    again.close()


def test_empty_accounts_never_start_an_anonymous_hosted_workspace(tmp_path):
    config = Settings(
        scione_workspace_dir=tmp_path, scione_database_url=None,
        scione_auth_database_url=None, scione_auth_enabled=True,
        scione_bootstrap_accounts=None,
    )
    with pytest.raises(ConfigurationError, match="maintainer"):
        initialize_hosted_storage(config, attempts=1)
