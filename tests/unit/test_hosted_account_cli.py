"""Operator CLI mirrors hosted environment/permissions before accessing storage."""

import pytest

from scione.cli import main
from scione.config import ConfigurationError, Settings


@pytest.fixture
def operator_environment(monkeypatch, tmp_path):
    for key in (
        "RAILWAY_SERVICE_ID",
        "RAILWAY_PUBLIC_DOMAIN",
        "SCIONE_ENVIRONMENT",
        "SCIONE_PUBLIC_URL",
        "SCIONE_ALLOWED_HOSTS",
        "SCIONE_AUTH_ENABLED",
        "SCIONE_COOKIE_SECURE",
        "SCIONE_TRUSTED_ORIGINS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SCIONE_WORKSPACE_DIR", str(tmp_path))
    events = []

    def settings():
        events.append("settings")
        return Settings(_env_file=None)

    class Registry:
        def __init__(self, config):
            events.append("registry")
            if config.scione_environment == "production":
                assert events[-2] == "permissions"

        def close_all(self):
            events.append("close")

    def command(args, config, callback):
        events.append("command")
        return 0

    monkeypatch.setattr("scione.config.Settings", settings)
    monkeypatch.setattr("scione.api.tenancy.TenantWorkspaceRegistry", Registry)
    monkeypatch.setattr("scione.auth.accounts_cli.run_command", command)
    return events


def test_railway_operator_derives_public_origin_and_drops_privileges_before_storage(
    operator_environment,
    monkeypatch,
):
    events = operator_environment
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "synthetic-service")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "operator.example.test")

    def permissions(config):
        assert config.scione_public_url == "https://operator.example.test"
        assert config.scione_auth_enabled and config.scione_environment == "production"
        events.append("permissions")

    monkeypatch.setattr("scione.deployment.prepare_data_directory", permissions)
    assert main(["accounts", "lab-create", "--name", "New laboratory"]) == 0
    assert events == ["settings", "permissions", "registry", "command", "close"]


def test_local_account_cli_preserves_legacy_environment_and_permissions(
    operator_environment,
    monkeypatch,
):
    def forbidden(*args, **kwargs):
        pytest.fail("Local account management must not alter hosted settings or process UID.")

    monkeypatch.setattr("scione.deployment.prepare_host_environment", forbidden)
    monkeypatch.setattr("scione.deployment.prepare_data_directory", forbidden)
    assert main(["accounts", "list"]) == 0
    assert operator_environment == ["settings", "registry", "command", "close"]


def test_hosted_account_permissions_failure_stops_before_storage_without_secret_output(
    operator_environment,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("SCIONE_ENVIRONMENT", "production")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "operator.example.test")

    def unavailable(config):
        raise ConfigurationError("PRIVATE-SERVER-CONFIGURATION-MUST-NOT-BE-PRINTED")

    monkeypatch.setattr("scione.deployment.prepare_data_directory", unavailable)
    assert main(["accounts", "list"]) == 1
    assert operator_environment == ["settings"]
    output = capsys.readouterr().out
    assert "Account operation failed" in output and "PRIVATE-SERVER" not in output
