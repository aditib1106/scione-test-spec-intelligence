"""Create project-local database credentials without changing model keys.

Run with: uv run python scripts/setup_database.py
This only writes .env.database; Docker is started explicitly by the user.
"""

import os
import secrets
from pathlib import Path

from sqlalchemy.engine import URL


def main(root: Path | None = None) -> None:
    root = root if root is not None else Path(__file__).resolve().parents[1]
    destination = root / ".env.database"
    password = secrets.token_urlsafe(32)
    url = URL.create(
        "mysql+pymysql",
        username="scione",
        password=password,
        host="127.0.0.1",
        port=3307,
        database="scione",
        query={"charset": "utf8mb4"},
    )
    content = "\n".join(
        [
            "# Private local database settings. Never commit this file.",
            "MYSQL_DATABASE=scione",
            "MYSQL_USER=scione",
            f"MYSQL_PASSWORD={password}",
            f"MYSQL_ROOT_PASSWORD={secrets.token_urlsafe(40)}",
            "SCIONE_DB_PORT=3307",
            f"SCIONE_DATABASE_URL={url.render_as_string(hide_password=False)}",
            "",
        ]
    )
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(".env.database already exists; existing database settings were preserved.")
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    print("Created private .env.database. No services were started and .env was preserved.")
    print("Start MySQL: docker compose --env-file .env.database up -d --wait")


if __name__ == "__main__":
    main()
