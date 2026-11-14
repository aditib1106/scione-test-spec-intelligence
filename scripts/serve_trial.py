"""Run an isolated local preview with real login, without changing old demo data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import uvicorn
from pydantic import SecretStr

from scione.auth.passwords import generated_password
from scione.config import Settings
from scione.deployment import initialize_hosted_storage

ROOT = Path(__file__).resolve().parents[1]


def account_configuration(path: Path) -> list[dict]:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("The private account file must not use symlinks.")
    if path.exists():
        return json.loads(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    definitions = [
        ("admin", "Platform administrator", "maintainer", "Demo Coatings Lab", True),
        ("lab1", "Lab 1 user", "lab", "Demo Coatings Lab", False),
        ("lab1staff", "Lab 1 colleague", "lab", "Demo Coatings Lab", False),
        ("lab2", "Lab 2 user", "lab", "Demo Materials Lab", False),
    ]
    accounts = [
        {
            "username": name, "password": generated_password(), "display_name": display,
            "role": role, "lab_name": lab, "seed_development_lab": seed,
        }
        for name, display, role, lab, seed in definitions
    ]
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(accounts, stream, indent=2)
        stream.write("\n")
    return accounts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--workspace-dir", type=Path, help="Use a separate local demo workspace.")
    args = parser.parse_args()
    root = (args.workspace_dir or ROOT / "local_data" / "trial-preview").expanduser().absolute()
    if root.is_relative_to(ROOT) and not root.is_relative_to(ROOT / "local_data"):
        parser.error(
            "Keep private demo workspaces under ignored local_data or outside the checkout."
        )
    credentials = root / "private" / "account-credentials.json"
    settings = Settings().model_copy(update={
        "scione_environment": "development", "scione_auth_enabled": True,
        "scione_workspace_dir": root, "scione_database_url": None,
        "scione_auth_database_url": None, "scione_cookie_secure": False,
        "scione_public_url": f"http://127.0.0.1:{args.port}",
        "scione_bootstrap_accounts": SecretStr(json.dumps(account_configuration(credentials))),
    })
    initialize_hosted_storage(settings, attempts=1)
    print(f"Private login details: {credentials}")
    print(
        "The preview uses separate local storage. "
        "Existing MySQL and local demo data are unchanged."
    )
    if args.setup_only:
        return
    from scione.api.app import create_app

    print(f"Open http://127.0.0.1:{args.port}")
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
