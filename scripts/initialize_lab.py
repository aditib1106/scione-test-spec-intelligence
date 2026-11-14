"""Explicitly initialize one lab from fixed synthetic development declarations.

Never runs at application startup. Existing declarations require an explicit
merge or replacement flag and remain available as a previous SQL revision.
"""

import argparse

from scione.api.tenancy import TenantWorkspaceRegistry
from scione.api.workspace import Workspace
from scione.auth.store import AuthStore, AuthStoreError, username_key
from scione.catalogs.baseline import development_lab_baseline, ms_pnt_410_lab_baseline
from scione.config import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    existing = parser.add_mutually_exclusive_group()
    existing.add_argument("--replace-existing", action="store_true")
    existing.add_argument(
        "--merge-existing", action="store_true",
        help="Append missing capability IDs; retain every existing declaration unchanged.",
    )
    parser.add_argument("--profile", choices=("development", "ms-pnt-410"), default="development")
    parser.add_argument("--lab-user", help="Existing active lab account, required with accounts.")
    args = parser.parse_args(argv)
    settings = Settings()
    workspace = registry = auth = None
    try:
        if settings.scione_auth_enabled:
            if not args.lab_user:
                parser.error("Accounts are enabled; specify the target lab with --lab-user.")
            auth = AuthStore(settings)
            auth.ensure_ready()
            try:
                user = auth.user_by_username(username_key(args.lab_user))
            except ValueError:
                parser.error("--lab-user must identify an existing active lab account.")
            if not user or not user["active"] or user["role"] != "lab" or not user["lab_id"]:
                parser.error("--lab-user must identify an existing active lab account.")
            lab_account = auth.get_lab(user["lab_id"])
            if not lab_account or not lab_account.active:
                parser.error("The selected account's lab is inactive or unavailable.")
            registry = TenantWorkspaceRegistry(settings)
            workspace = registry.workspace_for(lab_account.id)
        else:
            if args.lab_user:
                parser.error("--lab-user requires accounts to be enabled; no lab was changed.")
            workspace = Workspace(settings)
        lab = workspace.labs.load_or_create()
        baseline = (
            ms_pnt_410_lab_baseline() if args.profile == "ms-pnt-410"
            else development_lab_baseline()
        )
        if lab.capabilities and not (args.replace_existing or args.merge_existing):
            parser.error(
                "Lab already contains declarations; choose --merge-existing or "
                "--replace-existing deliberately."
            )
        capabilities = baseline.capabilities
        if args.merge_existing:
            retained_ids = {cap.id for cap in lab.capabilities}
            capabilities = [
                *lab.capabilities,
                *(cap for cap in baseline.capabilities if cap.id not in retained_ids),
            ]
        workspace.labs.save(lab.model_copy(update={"capabilities": capabilities}))
        saved = workspace.labs.load_or_create()
        print(
            f"Saved {len(saved.capabilities)} lab declarations, revision {saved.version}."
        )
        print(
            "Original profile details, earlier revisions and uploaded specifications are retained."
        )
    except AuthStoreError as exc:
        parser.error(str(exc))
    finally:
        if registry:
            registry.close_all()
        elif workspace:
            workspace.library.store.close()
        if auth:
            auth.close()


if __name__ == "__main__":
    main()
