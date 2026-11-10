"""Server-selected laboratory workspaces, isolated on disk and in SQL.

Only an authenticated principal supplies a laboratory identifier. Route handlers
share a proxy, never a default laboratory. MySQL tenants use independent schemas;
embedded deployments use independent database files. Central account data and
legacy local files are never used as a tenant's business workspace.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from scione.api.workspace import Workspace
from scione.config import ConfigurationError, Settings
from scione.library.store import LibraryStoreError, comparisons
from scione.library.workspace import LibraryWorkspace
from scione.matching.store import matching_runs
from scione.specifications.store import SpecStoreError
from scione.specifications.upload_names import UploadNameSpecStore

_LAB_ID = re.compile(r"^[a-f0-9]{32}$")
_DOCUMENT_ID = re.compile(r"^[a-f0-9]{16}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_BOUND_WORKSPACE: ContextVar[tuple[object, str, Workspace] | None] = ContextVar(
    "scione_bound_laboratory_workspace", default=None
)
_DOCUMENT_ROUTES = {"specifications", "library", "comparisons", "reports", "diagnostics"}
_PUBLIC_BUSINESS_PATHS = {"/api/catalog", "/api/parameters"}


def validate_lab_id(lab_id: str) -> str:
    if not isinstance(lab_id, str) or not _LAB_ID.fullmatch(lab_id):
        raise ConfigurationError("The laboratory workspace identifier is invalid.")
    return lab_id


def tenant_schema(lab_id: str) -> str:
    return f"scione_{validate_lab_id(lab_id)}"


class _TenantLibraryWorkspace(LibraryWorkspace):
    def __init__(self, workspace: Workspace, store):
        self.workspace = workspace
        self.store = store

    def _document_view(self, stored, run, review) -> dict:
        return {
            **super()._document_view(stored, run, review),
            "original_name": stored.original_name,
        }

    def _assert_comparison_inputs_current(
        self, report, document: dict, matching_revision: str | None = None
    ) -> None:
        # The SQL index deliberately retains the first immutable upload name.
        # Validate every ordinary SQL/source/lab revision through the base guard,
        # then validate the separate live name view as its own input. Otherwise
        # a harmless reupload would make all comparisons fail as stale forever.
        original = self.store.document(report.document_id)["original_name"]
        super()._assert_comparison_inputs_current(
            report, {**document, "original_name": original}, matching_revision
        )
        if (
            self.workspace.specs.load_document(report.document_id).original_name
            != document["original_name"]
        ):
            raise HTTPException(409, "The uploaded filename changed. Reload before comparing.")


class TenantWorkspace(Workspace):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.specs = UploadNameSpecStore(settings.scione_workspace_dir)
        self.specs.added_requirement_ids = self._added_requirement_ids
        # All SQL domain stores retain the exact same tenant-specific LibraryStore.
        self.library = _TenantLibraryWorkspace(self, self.library.store)


class WorkspaceProxy:
    """Resolve every attribute against the request's authenticated laboratory."""

    def __init__(self, registry: TenantWorkspaceRegistry):
        self._registry = registry

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry.current(), name)


class TenantWorkspaceRegistry:
    """Process-local connection pools; laboratory authority lives in auth storage."""

    def __init__(
        self,
        settings: Settings,
        *,
        workspace_factory: Callable[[Settings], Workspace] = TenantWorkspace,
    ):
        self.settings = settings
        # Canonicalize the operator's configured root once; no caller can choose
        # it, and an existing symlink must not redirect the configured volume.
        configured_root = settings.scione_workspace_dir.expanduser().absolute()
        if ".." in configured_root.parts or any(
            path.is_symlink()
            for path in [*reversed(configured_root.parents), configured_root]
        ):
            raise ConfigurationError("Laboratory workspace storage is unavailable.")
        self.root = configured_root.resolve()
        self._factory = workspace_factory
        self._workspaces: dict[str, Workspace] = {}
        self._lock = threading.RLock()
        self._closed = False
        self.proxy = WorkspaceProxy(self)

    def tenant_root(self, lab_id: str) -> Path:
        path = self.root / "tenants" / validate_lab_id(lab_id)
        # Guard the configured root as well as every tenant component each time,
        # including cached workspaces, so a replacement symlink cannot redirect it.
        for component in [*reversed(path.parents), path]:
            if component.is_symlink() or (component.exists() and not component.is_dir()):
                raise ConfigurationError("Laboratory workspace storage is unavailable.")
        return path

    def database_url(self, lab_id: str) -> str | None:
        root = self.tenant_root(lab_id)
        configured = self.settings.scione_database_url
        if configured is None:
            self._assert_separate_database(
                URL.create("sqlite", database=str(root / "library.sqlite3"))
            )
            return None
        try:
            url = make_url(configured.get_secret_value())
        except (ValueError, TypeError, SQLAlchemyError) as exc:
            raise ConfigurationError("The laboratory database configuration is invalid.") from exc
        if url.get_backend_name() == "sqlite":
            # Explicit embedded mode still must never reuse the central DB path.
            derived = URL.create("sqlite", database=str(root / "library.sqlite3"))
            self._assert_separate_database(derived)
            return derived.render_as_string(hide_password=False)
        if url.get_backend_name() != "mysql" or not url.database:
            raise ConfigurationError("Laboratory storage requires MySQL or embedded SQLite.")
        derived = url.set(database=tenant_schema(lab_id))
        self._assert_separate_database(derived)
        return derived.render_as_string(hide_password=False)

    def _assert_separate_database(self, derived: URL) -> None:
        for configured in (
            self.settings.scione_database_url,
            self.settings.scione_auth_database_url,
        ):
            if configured is None:
                continue
            try:
                central = make_url(configured.get_secret_value())
            except (ValueError, TypeError, SQLAlchemyError) as exc:
                raise ConfigurationError("The account database configuration is invalid.") from exc
            if central.get_backend_name() != derived.get_backend_name():
                continue
            if derived.get_backend_name() == "sqlite":
                same_target = bool(
                    central.database not in {None, "", ":memory:"}
                    and Path(central.database).absolute().resolve()
                    == Path(derived.database).absolute().resolve()
                )
            else:
                same_target = (
                    central.database == derived.database
                    and central.host == derived.host
                    and (central.port or 3306) == (derived.port or 3306)
                    and central.query.get("unix_socket") == derived.query.get("unix_socket")
                )
            if same_target:
                raise ConfigurationError(
                    "Laboratory business storage must be separate from account storage."
                )

    def workspace_for(self, lab_id: str) -> Workspace:
        lab_id = validate_lab_id(lab_id)
        with self._lock:
            if self._closed:
                raise ConfigurationError("Laboratory workspace storage has stopped.")
            root = self.tenant_root(lab_id)
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.tenant_root(lab_id)
            if lab_id not in self._workspaces:
                url = self.database_url(lab_id)
                settings = self.settings.model_copy(
                    update={
                        "scione_workspace_dir": root,
                        "scione_database_url": SecretStr(url) if url else None,
                    }
                )
                self._workspaces[lab_id] = self._factory(settings)
            return self._workspaces[lab_id]

    def current(self) -> Workspace:
        bound = _BOUND_WORKSPACE.get()
        if bound is None or bound[0] is not self:
            raise HTTPException(403, "Select a laboratory workspace first.")
        # Recheck path integrity before returning even an already cached workspace.
        self.tenant_root(bound[1])
        return bound[2]

    def current_lab_id(self) -> str:
        self.current()
        return _BOUND_WORKSPACE.get()[1]  # type: ignore[index]

    @contextmanager
    def bind(self, lab_id: str | None) -> Iterator[Workspace | None]:
        workspace = self.workspace_for(lab_id) if lab_id is not None else None
        token = _BOUND_WORKSPACE.set((self, lab_id, workspace) if workspace else None)
        try:
            yield workspace
        finally:
            _BOUND_WORKSPACE.reset(token)

    def provision(self, lab_id: str, *, admin_database_url: str | None = None) -> dict:
        """Operator-only provisioning hook. Runtime requests never create databases.

        Create the central auth lab first, then invoke this from the account CLI.
        The admin URL is used transiently and is neither persisted nor returned.
        A failed MySQL provision never switches the laboratory to SQLite.
        """
        lab_id = validate_lab_id(lab_id)
        url = self.database_url(lab_id)
        backend = make_url(url).get_backend_name() if url else "sqlite"
        if backend != "mysql" and admin_database_url:
            raise ConfigurationError(
                "Configure MySQL storage before providing database administrator credentials."
            )
        if backend == "mysql":
            runtime = make_url(url)
            try:
                admin = make_url(admin_database_url) if admin_database_url else runtime
                if (
                    admin.get_backend_name() != "mysql"
                    or admin.host != runtime.host
                    or admin.port != runtime.port
                    or admin.query.get("unix_socket") != runtime.query.get("unix_socket")
                ):
                    raise ConfigurationError("The provisioning database server does not match.")
                # URL.set ignores None, so it cannot remove a selected schema.
                # Connect without any database before creating a new tenant.
                server_url = URL.create(
                    admin.drivername, username=admin.username, password=admin.password,
                    host=admin.host, port=admin.port, database=None, query=admin.query,
                )
                engine = create_engine(
                    server_url,
                    pool_pre_ping=True,
                    connect_args={"connect_timeout": 5},
                )
                try:
                    with engine.begin() as connection:
                        # Identifier is entirely server-generated and strictly 32hex.
                        connection.execute(
                            text(
                                f"CREATE DATABASE IF NOT EXISTS `{tenant_schema(lab_id)}` "
                                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                            )
                        )
                finally:
                    engine.dispose()
            except (SQLAlchemyError, ValueError, TypeError) as exc:
                raise ConfigurationError(
                    "Laboratory database provisioning failed. Existing data was preserved."
                ) from exc
        workspace = self.workspace_for(lab_id)
        # Initialize tables only in the provisioned schema/file. This never
        # invokes a model, creates accounts, or reads the legacy local lab.
        _ = workspace.library.store.engine
        from scione.catalogs.sql_lab_store import upgrade_lab_schema
        from scione.library.assessment_reviews import upgrade_assessment_reviews_schema
        from scione.matching.store import upgrade_matching_schema
        from scione.monitoring.store import upgrade_monitoring_schema
        from scione.specifications.corrections import upgrade_corrections_schema

        for upgrade in (
            upgrade_lab_schema,
            upgrade_matching_schema,
            upgrade_corrections_schema,
            upgrade_assessment_reviews_schema,
            upgrade_monitoring_schema,
        ):
            upgrade(workspace.library.store)
        return {"lab_id": lab_id, "backend": backend, "schema": tenant_schema(lab_id)}

    def close_all(self) -> None:
        with self._lock:
            self._closed = True
            workspaces = list(self._workspaces.values())
            self._workspaces.clear()
        for workspace in workspaces:
            workspace.library.store.close()

    shutdown = close_all

    def owns_path(self, lab_id: str, scope: Scope) -> bool:
        """Hide foreign artifact IDs before domain loaders can expose path details."""
        workspace = self.workspace_for(lab_id)
        parts = scope.get("path", "").strip("/").split("/")
        if len(parts) < 3 or parts[0] != "api":
            return True
        group, identifier = parts[1:3]
        if group in _DOCUMENT_ROUTES:
            # Static collection actions are not document identifiers.
            if group == "specifications" and identifier in {"demo", "worked-example"}:
                return True
            if not _DOCUMENT_ID.fullmatch(identifier):
                return False
            try:
                directory = workspace.specs._document_directory(identifier)
                # Ownership is a path check, not a second full PDF hash/read.
                # Domain stores still validate every artifact before use.
                if not all(
                    (directory / name).is_file() and not (directory / name).is_symlink()
                    for name in ("original.pdf", "document.json", "metadata.json")
                ):
                    return False
            except SpecStoreError:
                return False
            query = parse_qs(scope.get("query_string", b"").decode("ascii", "replace"))
            for name in ("run_id", "baseline_id"):
                for run_id in query.get(name, []):
                    if not _RUN_ID.fullmatch(run_id):
                        return False
                    if not workspace.specs._run_directory(identifier, run_id).joinpath(
                        "run.json"
                    ).is_file():
                        return False
            if len(parts) > 4 and parts[3] == "matching":
                run_id = parts[4]
                if not _RUN_ID.fullmatch(run_id):
                    return False
                with workspace.matching.store._engine().connect() as connection:
                    return connection.execute(
                        select(matching_runs.c.run_id).where(
                            matching_runs.c.workspace_id == workspace.labs.workspace_id,
                            matching_runs.c.document_id == identifier,
                            matching_runs.c.run_id == run_id,
                        )
                    ).first() is not None
            if len(parts) > 4 and parts[3] == "analysis-failures":
                attempt = parts[4]
                if not _RUN_ID.fullmatch(attempt):
                    return False
                return workspace.specs._failure_artifact_path(identifier, attempt).is_file()
        elif group == "comparison-snapshots":
            if not _LAB_ID.fullmatch(identifier):
                return False
            with workspace.library.store.engine.connect() as connection:
                return connection.execute(
                    select(comparisons.c.comparison_id).where(
                        comparisons.c.comparison_id == identifier
                    )
                ).first() is not None
        elif group == "snapshots":
            try:
                return workspace.reports._snapshot_directory(identifier).is_dir()
            except (ValueError, RuntimeError):
                return False
        return True


class TenantWorkspaceMiddleware:
    """Bind only the server-authenticated principal; ignore caller tenant hints.

    Install this *inside* authentication middleware. Pure ASGI middleware keeps
    ContextVars consistent through async handlers and FastAPI worker threads.
    """

    def __init__(self, app: ASGIApp, registry: TenantWorkspaceRegistry):
        self.app = app
        self.registry = registry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        principal = scope.get("state", {}).get("auth_principal")
        lab_id = getattr(principal, "lab_id", None)
        try:
            # Auth/health/static routes need no active laboratory, but must not
            # inherit an ambient binding from another request or test harness.
            with self.registry.bind(lab_id):
                if lab_id and not await run_in_threadpool(
                    self.registry.owns_path, lab_id, scope
                ):
                    await JSONResponse({"detail": "This resource was not found."}, 404)(
                        scope, receive, send
                    )
                    return
                if (
                    principal is not None
                    and not lab_id
                    and scope["path"].startswith("/api/")
                    and scope["path"] not in _PUBLIC_BUSINESS_PATHS
                    and not scope["path"].startswith(("/api/auth/", "/api/admin/"))
                    and not (
                        getattr(principal, "role", None) == "maintainer"
                        and scope["path"] in {"/api/developer/metrics", "/api/operations"}
                    )
                    and scope["path"] != "/api/health"
                ):
                    await JSONResponse(
                        {"detail": "Select a laboratory workspace first."}, 403
                    )(scope, receive, send)
                    return
                await self.app(scope, receive, send)
        except (ConfigurationError, LibraryStoreError, SQLAlchemyError):
            await JSONResponse(
                {"detail": "Laboratory storage is unavailable. Contact the administrator."},
                503,
            )(scope, receive, send)
