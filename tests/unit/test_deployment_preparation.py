"""Deployment preparation exports only intended secrets, never local data URLs."""

import json
import runpy
import stat
from pathlib import Path

import pytest
from pydantic import SecretStr

from scione.config import Settings

SCRIPT = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/prepare_deployment.py"))


def test_cloud_variables_do_not_copy_local_database_or_other_provider_keys():
    config = Settings(
        model_provider="sail", sail_api_key=SecretStr("fixture-sail-key"),
        groq_api_key=SecretStr("fixture-other-key"),
        scione_database_url=SecretStr("mysql+pymysql://local:fixture@localhost/local"),
    )
    values = SCRIPT["deployment_variables"](config, [])
    assert values["SCIONE_DATABASE_URL"] == "${{MySQL.MYSQL_URL}}"
    assert values["SAIL_API_KEY"] == "fixture-sail-key"
    assert "GROQ_API_KEY" not in values
    assert "localhost" not in json.dumps(values)
    assert values["SCIONE_AUTH_ENABLED"] == "true"
    assert values["SCIONE_ENVIRONMENT"] == "production"


def test_preparation_retains_strong_passwords_in_private_files(tmp_path):
    path = tmp_path / "private/accounts.json"
    accounts = SCRIPT["deployment_accounts"](path)
    assert len(accounts) == 5
    assert {row["username"] for row in accounts} == {"admin", "lab1", "lab2", "lab3", "lab4"}
    assert len({row["lab_name"] for row in accounts}) == 4
    assert len({entry["password"] for entry in accounts}) == 5
    assert all(len(entry["password"]) >= 32 for entry in accounts)
    assert sum(entry["role"] == "maintainer" for entry in accounts) == 1
    assert SCRIPT["deployment_accounts"](path) == accounts
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert sum(entry["seed_development_lab"] for entry in accounts) == 4


def test_private_outputs_refuse_symlinks(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("unchanged")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        SCRIPT["write_private"](link, {"secret": "fixture"})
    assert target.read_text() == "unchanged"
