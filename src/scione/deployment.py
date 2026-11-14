"""Portable hosted startup: migrate, provision accounts, then serve one worker.

The image contains application code, never local databases, uploads or keys.
Railway supplies MySQL and a persistent /data volume. Other Docker hosts can use
the same image and environment contract behind an HTTPS reverse proxy.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from scione.config import ConfigurationError, Settings

LOGGER = logging.getLogger(__name__)


def prepare_host_environment(environ=None) -> None:
    env = os.environ if environ is None else environ
    env.setdefault("SCIONE_ENVIRONMENT", "production")
    env.setdefault("SCIONE_AUTH_ENABLED", "true")
    domain = env.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if domain and not env.get("SCIONE_PUBLIC_URL"):
        env["SCIONE_PUBLIC_URL"] = "https://" + domain
    public = env.get("SCIONE_PUBLIC_URL", "")
    if public and not env.get("SCIONE_ALLOWED_HOSTS"):
        host = urlsplit(public).hostname
        if host:
            env["SCIONE_ALLOWED_HOSTS"] = json.dumps(
                [host, "healthcheck.railway.app", "localhost", "127.0.0.1", "[::1]"]
            )


def hosted_port(value: str | None) -> int:
    try:
        port = int(value or "8080")
    except ValueError:
        raise ConfigurationError("PORT must be a valid TCP port.") from None
    if not 1024 <= port <= 65535:
        raise ConfigurationError("PORT must be between 1024 and 65535.")
    return port


def prepare_data_directory(settings: Settings, *, environ=None) -> Path:
    env = os.environ if environ is None else environ
    root = settings.scione_workspace_dir.expanduser().absolute()
    for part in [*reversed(root.parents), root]:
        if part.is_symlink() or (part.exists() and not part.is_dir()):
            raise ConfigurationError("Persistent storage must be a regular directory.")
    if env.get("RAILWAY_SERVICE_ID"):
        mount = env.get("RAILWAY_VOLUME_MOUNT_PATH")
        if not mount or Path(mount).absolute() != root:
            raise ConfigurationError("Attach the application volume at SCIONE_WORKSPACE_DIR.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Railway mounts volumes at runtime. Fix only the mount root, then run all
    # database/file/model operations without root privileges. Existing children
    # retain their ownership and are never recursively rewritten.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        import pwd

        try:
            account = pwd.getpwnam("scione")
        except KeyError:
            raise ConfigurationError("The application container user is unavailable.") from None
        os.chown(root, account.pw_uid, account.pw_gid, follow_symlinks=False)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    return root


def upgrade_business_workspace(workspace) -> None:
    from scione.catalogs.sql_lab_store import upgrade_lab_schema
    from scione.library.assessment_reviews import upgrade_assessment_reviews_schema
    from scione.matching.store import upgrade_matching_schema
    from scione.monitoring.store import upgrade_monitoring_schema
    from scione.specifications.corrections import upgrade_corrections_schema
    from scione.specifications.document_notes import upgrade_document_notes_schema

    store = workspace.library.store
    upgrade_lab_schema(store)
    upgrade_matching_schema(store)
    upgrade_corrections_schema(store)
    upgrade_assessment_reviews_schema(store)
    upgrade_monitoring_schema(store)
    upgrade_document_notes_schema(store)


def provision_callback(registry):
    def provision(lab, *, seed_development_lab=False):
        registry.provision(lab.id)
        workspace = registry.workspace_for(lab.id)
        upgrade_business_workspace(workspace)
        current = workspace.labs.load_or_create()
        if not current.capabilities:
            update = {"name": lab.name, "description": "Laboratory testing capabilities."}
            if seed_development_lab:
                from scione.catalogs.baseline import development_lab_baseline

                update.update({
                    "capabilities": development_lab_baseline().capabilities,
                    "description": "Coating testing and materials characterization.",
                })
            workspace.labs.save(current.model_copy(update=update))
    return provision


def initialize_hosted_storage(settings: Settings, *, attempts=30, pause=time.sleep) -> dict:
    from scione.api.tenancy import TenantWorkspaceRegistry
    from scione.auth.bootstrap import bootstrap_from_environment
    from scione.auth.manager import AuthManager
    from scione.auth.store import AuthStoreError

    manager = AuthManager(settings)
    registry = TenantWorkspaceRegistry(settings)
    try:
        for attempt in range(max(1, min(attempts, 30))):
            try:
                manager.initialize()
                break
            except AuthStoreError:
                if attempt + 1 >= max(1, min(attempts, 30)):
                    raise
                LOGGER.warning("Waiting for the account database (%s/%s).", attempt + 1, attempts)
                pause(2)
        result = bootstrap_from_environment(manager, provision_callback(registry))
        if not manager.store.list_users():
            raise ConfigurationError("Provision the first maintainer account before serving.")
        for lab in manager.list_labs():
            upgrade_business_workspace(registry.workspace_for(lab.id))
        manager.ensure_ready()
        return result
    finally:
        registry.close_all()
        manager.close()


def main() -> int:
    import uvicorn
    from pydantic import ValidationError
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import SQLAlchemyError

    from scione.auth.store import AuthStoreError
    from scione.library.store import LibraryStoreError

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    phase = "environment validation"
    try:
        prepare_host_environment()
        settings = Settings()
        port = hosted_port(os.environ.get("PORT"))
        if settings.scione_environment == "production":
            url = settings.scione_database_url
            if url is None or make_url(url.get_secret_value()).get_backend_name() != "mysql":
                raise ConfigurationError("Hosted production requires the configured MySQL service.")
        phase = "persistent storage"
        prepare_data_directory(settings)
        phase = "account and laboratory migrations"
        summary = initialize_hosted_storage(settings)
        LOGGER.info("Storage is ready; %s missing accounts created.", summary["created"])
    except (
        ConfigurationError, AuthStoreError, LibraryStoreError, ValidationError,
        OSError, SQLAlchemyError,
    ) as exc:
        LOGGER.error("Startup stopped during %s (%s). Existing data was preserved.", phase,
                     type(exc).__name__)
        if isinstance(exc, ConfigurationError):
            LOGGER.error("%s", exc)
        elif isinstance(exc, ValidationError):
            LOGGER.error("Check server settings: %s", exc.errors(
                include_input=False, include_context=False, include_url=False
            ))
        return 1
    uvicorn.run(
        "scione.api.app:app", host="0.0.0.0", port=port, workers=1,
        proxy_headers=True,
        forwarded_allow_ips=(
            "*" if os.environ.get("RAILWAY_SERVICE_ID") else "127.0.0.1"
        ),
        timeout_graceful_shutdown=30,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
