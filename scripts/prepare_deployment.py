"""Prepare private Railway variables and fresh trial accounts; deploy nothing.

Run from the repository root. Secrets are written only to ignored mode-0600
files, never printed or passed as command arguments. Re-running retains account
passwords and refreshes the selected provider configuration from local settings.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from scione.auth.passwords import generated_password
from scione.config import Settings

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "local_data" / "deployment" / "private"


def write_private(path: Path, value) -> None:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Deployment secrets must not use symlinks.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".pending")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def deployment_accounts(path: Path) -> list[dict]:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Deployment secrets must not use symlinks.")
    if path.exists():
        accounts = json.loads(path.read_text())
        from scione.auth.bootstrap import BootstrapAccount

        for account in accounts:
            BootstrapAccount.model_validate(account)
        return accounts
    definitions = [
        ("admin", "Platform administrator", "maintainer", "Lab 1", True),
        ("lab1", "Lab 1 user", "lab", "Lab 1", False),
        ("lab2", "Lab 2 user", "lab", "Lab 2", True),
        ("lab3", "Lab 3 user", "lab", "Lab 3", True),
        ("lab4", "Lab 4 user", "lab", "Lab 4", True),
    ]
    accounts = [
        {
            "username": name, "password": generated_password(), "display_name": display,
            "role": role, "lab_name": lab, "seed_development_lab": seed,
        }
        for name, display, role, lab, seed in definitions
    ]
    write_private(path, accounts)
    return accounts


def deployment_variables(settings: Settings, accounts: list[dict]) -> dict[str, str]:
    provider = settings.model_provider
    if provider not in {"sail", "fireworks", "gemini", "groq"}:
        raise ValueError("Choose a supported server inference provider.")
    secret = getattr(settings, f"{provider}_api_key")
    if not secret or not secret.get_secret_value().strip():
        raise ValueError("Configure the selected provider's API key locally first.")
    values = {
        "SCIONE_ENVIRONMENT": "production",
        "SCIONE_AUTH_ENABLED": "true",
        "SCIONE_WORKSPACE_DIR": "/data",
        "PORT": "8080",
        "SCIONE_DATABASE_URL": "${{MySQL.MYSQL_URL}}",
        "SCIONE_BOOTSTRAP_ACCOUNTS": json.dumps(accounts, separators=(",", ":")),
        "MODEL_PROVIDER": provider,
        "SCIONE_SINGLE_ATTEMPT_MODE": "true",
        "SCIONE_CUSTOMER_INPUT_CHARACTER_LIMIT": str(
            settings.scione_customer_input_character_limit
        ),
        "SCIONE_MATCHING_INPUT_CHARACTER_LIMIT": str(
            settings.scione_matching_input_character_limit
        ),
        f"{provider.upper()}_API_KEY": secret.get_secret_value(),
        f"{provider.upper()}_MODEL": getattr(settings, f"{provider}_model"),
        f"{provider.upper()}_TIMEOUT_SECONDS": str(
            getattr(settings, f"{provider}_timeout_seconds")
        ),
        f"{provider.upper()}_TEMPERATURE": "0.0",
    }
    if provider == "groq":
        values["GROQ_MAX_RETRIES"] = "0"
        values["GROQ_MAX_COMPLETION_TOKENS"] = str(settings.groq_max_completion_tokens)
        values["GROQ_REASONING_EFFORT"] = settings.groq_reasoning_effort
    else:
        values[f"{provider.upper()}_MAX_OUTPUT_TOKENS"] = str(
            getattr(settings, f"{provider}_max_output_tokens")
        )
    if provider == "gemini":
        values["GEMINI_MAX_RETRIES"] = "0"
        values["GEMINI_THINKING_LEVEL"] = settings.gemini_thinking_level
        values["SCIONE_CUSTOMER_GEMINI_OUTPUT_TOKENS"] = str(
            settings.scione_customer_gemini_output_tokens
        )
    if provider == "fireworks" and settings.fireworks_reasoning_effort is not None:
        values["FIREWORKS_REASONING_EFFORT"] = settings.fireworks_reasoning_effort
    return values


def main() -> int:
    accounts_file = PRIVATE / "account-credentials.json"
    variables_file = PRIVATE / "railway-variables.json"
    try:
        accounts = deployment_accounts(accounts_file)
        write_private(variables_file, deployment_variables(Settings(), accounts))
    except (OSError, ValueError):
        print("Preparation stopped. Check provider settings and the private output directory.")
        return 1
    print(f"Private initial login details: {accounts_file}")
    print(f"Private Railway variables: {variables_file}")
    print("No services were created, no code was uploaded and no model was called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
