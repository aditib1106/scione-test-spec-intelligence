"""Local setup must preserve existing secrets and never log generated credentials."""

import importlib.util
import stat
from pathlib import Path

from dotenv import dotenv_values


def test_database_setup_is_private_reproducible_and_preserves_model_keys(tmp_path, capsys):
    path = Path(__file__).parents[2] / "scripts" / "setup_database.py"
    spec = importlib.util.spec_from_file_location("setup_local_database", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=existing-local-key\n")

    module.main(tmp_path)

    private = tmp_path / ".env.database"
    values = dotenv_values(private)
    original = private.read_bytes()
    output = capsys.readouterr().out
    assert values["MYSQL_PASSWORD"] not in output
    assert values["MYSQL_ROOT_PASSWORD"] not in output
    assert values["MYSQL_PASSWORD"] != values["MYSQL_ROOT_PASSWORD"]
    assert "mysql+pymysql" in values["SCIONE_DATABASE_URL"]
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert env.read_text() == "GEMINI_API_KEY=existing-local-key\n"

    module.main(tmp_path)
    assert private.read_bytes() == original
    assert "already exists" in capsys.readouterr().out
