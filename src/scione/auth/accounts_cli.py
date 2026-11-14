"""Operator account management; generated passwords go only to a private file."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from scione.auth.manager import AuthManager
from scione.auth.passwords import generated_password, hash_password
from scione.auth.store import AuthStoreError, username_key


def configure_cli(subparsers):
    parser = subparsers.add_parser("accounts", help="Manage private lab accounts and passwords.")
    commands = parser.add_subparsers(dest="account_action", required=True)
    commands.add_parser("init", help="Apply additive central account/session migrations.")
    commands.add_parser("list", help="List accounts without passwords or password hashes.")
    lab = commands.add_parser("lab-create", help="Provision an independent lab workspace.")
    lab.add_argument("--name", required=True)
    lab.add_argument("--seed-development-lab", action="store_true")
    create = commands.add_parser("create", help="Create an account with a generated password.")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--role", choices=["lab", "maintainer"], required=True)
    target = create.add_mutually_exclusive_group()
    target.add_argument("--lab-id")
    target.add_argument("--lab-name")
    create.add_argument("--credentials-file", type=Path)
    reset = commands.add_parser("reset-password", help="Reset a password and revoke its sessions.")
    reset.add_argument("--username", required=True)
    reset.add_argument("--credentials-file", type=Path)
    disable = commands.add_parser("disable", help="Disable an account and revoke its sessions.")
    disable.add_argument("--username", required=True)
    rename = commands.add_parser(
        "rename", help="Rename an account; preserve its identity/password and revoke sessions."
    )
    rename.add_argument("--username", required=True)
    rename.add_argument("--new-username", required=True)
    return parser


def _credential_destination(settings, requested: Path | None) -> Path:
    path = (
        requested or settings.scione_workspace_dir / "private" / "account-credentials.json"
    ).absolute()
    if path.is_symlink():
        raise ValueError("The credentials destination must be a private regular file.")
    path = path.resolve()
    assets = [Path(__file__).resolve().parents[3] / "frontend" / "dist"]
    if settings.scione_frontend_dir is not None:
        assets.append(settings.scione_frontend_dir.resolve())
    if any(path.is_relative_to(directory) for directory in assets):
        raise ValueError("Credentials cannot be saved in public application assets.")
    # Within a checkout, refuse an accidentally public credential artifact.
    try:
        repository = Path(
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
        if (
            path.is_relative_to(repository)
            and subprocess.run(
                ["git", "check-ignore", "-q", str(path.relative_to(repository))]
            ).returncode
            != 0
        ):
            raise ValueError(
                "Choose an ignored private credentials file outside application assets."
            )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _write_credentials(path: Path, user: dict, password: str, lab_name=None):
    records = json.loads(path.read_text()) if path.exists() else []
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError("The private credentials file must contain an account list.")
    records = [row for row in records if row.get("username") != user["username"]]
    records.append(
        {
            "username": user["username"],
            "password": password,
            "display_name": user["display_name"],
            "role": user["role"],
            "lab_id": user["lab_id"],
            "lab_name": lab_name,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    temporary = path.with_name("." + path.name + "." + uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(records, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_command(args, settings, provision_lab_callback=None) -> int:
    manager = AuthManager(settings)
    try:
        manager.initialize()
        action = args.account_action
        if action == "init":
            print("Account/session schema is ready. Existing data was preserved.")
            return 0
        if action == "list":
            print(json.dumps(manager.store.list_users(), ensure_ascii=False, indent=2))
            return 0
        if action == "lab-create":
            if provision_lab_callback is None:
                raise ValueError("Configure lab storage provisioning before creating a lab.")
            lab = manager.store.lab_by_name(args.name) or manager.store.create_lab(args.name)
            if lab.provisioned_at is None:
                provision_lab_callback(lab, seed_development_lab=args.seed_development_lab)
                lab = manager.store.mark_lab_provisioned(lab.id)
            print(json.dumps({"lab_id": lab.id, "lab_name": lab.name}))
            return 0
        username = username_key(args.username)
        if action == "rename":
            user, changed = manager.store.rename_user(username, args.new_username)
            print(
                json.dumps(
                    {
                        "user_id": user["id"],
                        "username": user["username"],
                        "renamed": changed,
                    }
                )
            )
            print(
                "Account renamed; existing sessions revoked. The password is unchanged."
                if changed
                else "This account already has the requested username."
            )
            return 0
        if action in {"disable", "reset-password"}:
            user = manager.store.user_by_username(username)
            if user is None:
                raise ValueError("That account was not found.")
            if action == "disable":
                manager.store.disable_user(user["id"])
                print("Account disabled and existing sessions revoked.")
                return 0
            destination = _credential_destination(settings, args.credentials_file)
            password = generated_password()
            manager.store.reset_password(user["id"], hash_password(password))
            lab = manager.store.get_lab(user["lab_id"]) if user["lab_id"] else None
            _write_credentials(destination, user, password, lab.name if lab else None)
            print(f"Password reset; sessions revoked. Credentials saved to {destination}")
            return 0
        destination = _credential_destination(settings, args.credentials_file)
        lab_id = args.lab_id
        if args.lab_name:
            lab = manager.store.lab_by_name(args.lab_name)
            if lab is None or not lab.active:
                raise ValueError("Provision that lab first with accounts lab-create.")
            lab_id = lab.id
        password = generated_password()
        user = manager.store.create_user(
            username, args.display_name, args.role, hash_password(password), lab_id=lab_id
        )
        lab = manager.store.get_lab(lab_id) if lab_id else None
        _write_credentials(destination, user, password, lab.name if lab else None)
        print(f"Account created. Credentials saved to {destination}")
        return 0
    except (AuthStoreError, SQLAlchemyError, OSError, ValueError):
        print("Account operation failed. Check the private configuration and account details.")
        return 1
    finally:
        manager.close()
