"""Versioned SQL lab declarations with a recoverable, one-time legacy import.

The archive and this store share one database. Each store is bound to a server-
chosen workspace ID; claim IDs are scoped to a capability and that workspace.
This does not itself authenticate requests: the local API remains local-only
until account and document isolation are implemented.
"""

from __future__ import annotations

import threading
from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from scione.catalogs.lab_store import FileLabStore, LabStoreError, LabVersionConflict
from scione.catalogs.models import LabProfile, ReferenceCatalog, validate_lab_references
from scione.library.store import LibraryStore, LibraryStoreError, digest, now, schema_versions
from scione.structured import normalize_capability
from scione.structured.models import NORMALIZER_VERSION

LAB_SCHEMA_VERSION = 1
lab_metadata = MetaData()
lab_workspaces = Table(
    "lab_workspaces",
    lab_metadata,
    Column("workspace_id", String(100), primary_key=True),
    Column("profile_id", String(200), nullable=False),
    Column("name", String(300), nullable=False),
    Column("description", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("legacy_imported", Integer, nullable=False),
)
lab_revisions = Table(
    "lab_profile_revisions",
    lab_metadata,
    Column("workspace_id", String(100), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("normalizer_version", String(100), nullable=False),
    Column("payload", JSON, nullable=False),
    ForeignKeyConstraint(["workspace_id"], ["lab_workspaces.workspace_id"]),
)
lab_entries = Table(
    "lab_capability_entries",
    lab_metadata,
    Column("workspace_id", String(100), primary_key=True),
    Column("capability_id", String(100), primary_key=True),
    Column("position", Integer, nullable=False),
    Column("canonical_test_id", String(200), nullable=False),
    Column("method_ids", JSON, nullable=False),
    Column("declared_constraints", Text),
    Column("structured_limits", JSON, nullable=False),
    Column("explicit_claims", JSON, nullable=False),
    Column("notes", Text),
    Column("price", String(100)),
    Column("currency", String(3)),
    Column("pricing_basis", Text),
    ForeignKeyConstraint(["workspace_id"], ["lab_workspaces.workspace_id"]),
)
lab_claims = Table(
    "lab_capability_claims",
    lab_metadata,
    Column("workspace_id", String(100), primary_key=True),
    Column("capability_id", String(100), primary_key=True),
    Column("claim_id", String(160), primary_key=True),
    Column("position", Integer, nullable=False),
    Column("parameter_id", String(200)),
    Column("kind", String(50), nullable=False),
    Column("stage", String(200), nullable=False),
    Column("configuration", String(160)),
    Column("sequence", Integer),
    Column("unit", String(60)),
    Column("normalizer_version", String(100), nullable=False),
    Column("payload", JSON, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "capability_id"],
        ["lab_capability_entries.workspace_id", "lab_capability_entries.capability_id"],
    ),
)


def upgrade_lab_schema(library: LibraryStore) -> None:
    """Apply the first additive lab migration; never rewrite existing archive tables.

    Deployment will run migrations before starting workers. Lazy application is
    retained for the existing offline/local workflow. The marker is written only
    after all additive tables exist, allowing a failed creation to be retried.
    """
    with library.schema_upgrade() as engine:
        _upgrade_lab_schema(engine)


def _upgrade_lab_schema(engine) -> None:
    with engine.connect() as connection:
        version = connection.execute(
            select(schema_versions.c.version).where(schema_versions.c.component == "lab_storage")
        ).scalar_one_or_none()
    if version not in (None, LAB_SCHEMA_VERSION):
        raise LibraryStoreError("The lab database needs a compatible application version.")
    if version is not None:
        return
    lab_metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(schema_versions).values(component="lab_storage", version=LAB_SCHEMA_VERSION)
            )
    except IntegrityError:
        # Another initializer may have completed this same additive migration.
        with engine.connect() as connection:
            actual = connection.execute(
                select(schema_versions.c.version).where(
                    schema_versions.c.component == "lab_storage"
                )
            ).scalar_one()
        if actual != LAB_SCHEMA_VERSION:
            raise LibraryStoreError("The lab database migration changed concurrently.") from None


class SQLLabStore:
    """Load/save the current lab plus immutable revisions and queryable claims.

    ``lab.json`` is read only if no SQL lab exists. It is preserved unchanged as
    a migration source, and is never a second live write target after import.
    Transactions and compare-and-swap protect concurrent editor updates.
    """

    def __init__(
        self,
        library: LibraryStore,
        catalog: ReferenceCatalog,
        *,
        workspace_id: str = "local_lab",
        legacy_root: Path | None = None,
    ):
        if not workspace_id or len(workspace_id) > 100:
            raise ValueError("A valid server-selected lab workspace ID is required.")
        self.library = library
        self.catalog = catalog
        self.workspace_id = workspace_id
        self.legacy_root = legacy_root
        self._lock = threading.RLock()

    def _engine(self):
        upgrade_lab_schema(self.library)
        return self.library.engine

    def _validate(self, lab: LabProfile) -> LabProfile:
        checked = LabProfile.model_validate(lab.model_dump())
        validate_lab_references(checked, self.catalog)
        return checked

    def load_or_create(self) -> LabProfile:
        try:
            with self._lock:
                engine = self._engine()
                with engine.connect() as connection:
                    current = self._read(connection)
                if current is not None:
                    return current
                legacy = None
                if self.legacy_root is not None:
                    path = self.legacy_root / "lab.json"
                    if path.exists() or path.is_symlink():
                        legacy = FileLabStore(
                            self.legacy_root, self.catalog, seed_demo=False
                        ).load_or_create()
                lab = self._validate(
                    legacy or LabProfile(id="demo_lab", name="My Lab", capabilities=[])
                )
                try:
                    with engine.begin() as connection:
                        connection.execute(
                            insert(lab_workspaces).values(
                                workspace_id=self.workspace_id,
                                profile_id=lab.id,
                                name=lab.name,
                                description=lab.description,
                                version=lab.version,
                                created_at=now(),
                                updated_at=now(),
                                legacy_imported=int(legacy is not None),
                            )
                        )
                        self._write_entries(connection, lab)
                        self._revision(connection, lab)
                except IntegrityError:
                    # A competing creator won. Never overwrite the winner.
                    with engine.connect() as connection:
                        winner = self._read(connection)
                    if winner is None:
                        raise
                    return winner
                return lab
        except (SQLAlchemyError, ValueError) as exc:
            raise LabStoreError(
                "Unable to load the lab database. Existing declarations have been preserved."
            ) from exc

    def save(self, lab: LabProfile) -> None:
        try:
            candidate = self._validate(lab)
            with self._lock, self._engine().begin() as connection:
                current = (
                    connection.execute(
                        select(lab_workspaces).where(
                            lab_workspaces.c.workspace_id == self.workspace_id
                        )
                    )
                    .mappings()
                    .first()
                )
                if current is None:
                    raise LabStoreError("No saved lab exists; call load_or_create first.")
                if candidate.id != current["profile_id"]:
                    raise LabStoreError("The saved lab ID cannot be changed.")
                changed = connection.execute(
                    update(lab_workspaces)
                    .where(
                        lab_workspaces.c.workspace_id == self.workspace_id,
                        lab_workspaces.c.version == candidate.version,
                    )
                    .values(
                        name=candidate.name,
                        description=candidate.description,
                        version=candidate.version + 1,
                        updated_at=now(),
                    )
                )
                if changed.rowcount != 1:
                    raise LabVersionConflict("The lab has changed. Reload before saving.")
                saved = candidate.model_copy(update={"version": candidate.version + 1})
                self._write_entries(connection, saved)
                self._revision(connection, saved)
        except (SQLAlchemyError, ValueError) as exc:
            raise LabStoreError(
                "Unable to save the lab database. Your previous declaration is unchanged."
            ) from exc

    def _read(self, connection) -> LabProfile | None:
        records = (
            connection.execute(
                select(lab_workspaces, lab_entries)
                .select_from(
                    lab_workspaces.outerjoin(
                        lab_entries, lab_workspaces.c.workspace_id == lab_entries.c.workspace_id
                    )
                )
                .where(lab_workspaces.c.workspace_id == self.workspace_id)
                .order_by(lab_entries.c.position)
            )
            .mappings()
            .all()
        )
        if not records:
            return None
        # One statement captures profile and entries together, including SQLite
        # readers whose driver may not begin a read transaction for SELECT.
        row = records[0]
        entries = [entry for entry in records if entry["capability_id"] is not None]
        return self._validate(
            LabProfile(
                id=row["profile_id"],
                name=row["name"],
                description=row["description"],
                version=row["version"],
                capabilities=[
                    {
                        "id": entry["capability_id"],
                        "canonical_test_id": entry["canonical_test_id"],
                        "method_ids": entry["method_ids"],
                        "declared_constraints": entry["declared_constraints"],
                        "structured_limits": entry["structured_limits"],
                        "atomic_claims": entry["explicit_claims"],
                        "notes": entry["notes"],
                        "price": entry["price"],
                        "currency": entry["currency"],
                        "pricing_basis": entry["pricing_basis"],
                    }
                    for entry in entries
                ],
            )
        )

    def _write_entries(self, connection, lab: LabProfile) -> None:
        connection.execute(delete(lab_claims).where(lab_claims.c.workspace_id == self.workspace_id))
        connection.execute(
            delete(lab_entries).where(lab_entries.c.workspace_id == self.workspace_id)
        )
        for position, capability in enumerate(lab.capabilities):
            payload = capability.model_dump(mode="json")
            connection.execute(
                insert(lab_entries).values(
                    workspace_id=self.workspace_id,
                    capability_id=capability.id,
                    position=position,
                    canonical_test_id=capability.canonical_test_id,
                    method_ids=capability.method_ids,
                    declared_constraints=capability.declared_constraints,
                    structured_limits=payload["structured_limits"],
                    explicit_claims=payload["atomic_claims"],
                    notes=capability.notes,
                    price=str(capability.price) if capability.price is not None else None,
                    currency=capability.currency,
                    pricing_basis=capability.pricing_basis,
                )
            )
            for atom_position, claim in enumerate(normalize_capability(capability)):
                connection.execute(
                    insert(lab_claims).values(
                        workspace_id=self.workspace_id,
                        capability_id=capability.id,
                        claim_id=claim.id,
                        position=atom_position,
                        parameter_id=claim.parameter_id,
                        kind=claim.kind,
                        stage=claim.stage,
                        configuration=claim.configuration,
                        sequence=claim.sequence,
                        unit=claim.unit,
                        normalizer_version=NORMALIZER_VERSION,
                        payload=claim.model_dump(mode="json"),
                    )
                )

    def _revision(self, connection, lab: LabProfile) -> None:
        connection.execute(
            insert(lab_revisions).values(
                workspace_id=self.workspace_id,
                version=lab.version,
                fingerprint=digest(lab.model_dump(mode="json")),
                created_at=now(),
                normalizer_version=NORMALIZER_VERSION,
                payload=lab.model_dump(mode="json"),
            )
        )

    def records(self) -> dict:
        """Read indexed facts from SQL, without reparsing prose or calling a model."""
        lab = self.load_or_create()
        with self._engine().connect() as connection:
            rows = (
                connection.execute(
                    select(lab_claims)
                    .where(lab_claims.c.workspace_id == self.workspace_id)
                    .order_by(lab_claims.c.capability_id, lab_claims.c.position)
                )
                .mappings()
                .all()
            )
            revisions = (
                connection.execute(
                    select(
                        lab_revisions.c.version,
                        lab_revisions.c.created_at,
                        lab_revisions.c.fingerprint,
                        lab_revisions.c.normalizer_version,
                    )
                    .where(lab_revisions.c.workspace_id == self.workspace_id)
                    .order_by(lab_revisions.c.version.desc())
                )
                .mappings()
                .all()
            )
        return {
            "storage": "sql",
            "backend": self.library.backend,
            "workspace_id": self.workspace_id,
            "lab_version": lab.version,
            "capability_count": len(lab.capabilities),
            "claim_count": len(rows),
            "claims": [
                {
                    "capability_id": row["capability_id"],
                    "normalizer_version": row["normalizer_version"],
                    **row["payload"],
                }
                for row in rows
            ],
            "revisions": [dict(row) for row in revisions],
        }
