"""Display-only capability chronology, separate from scientific lab declarations.

Existing or imported entries have no invented creation date. The server records
new identities only as part of a successful lab save. These facts never enter a
LabProfile, matching fingerprint, immutable profile revision, or model request.
"""

from __future__ import annotations

from sqlalchemy import Column, MetaData, String, Table, and_, insert, select
from sqlalchemy.exc import IntegrityError

from scione.library.store import LibraryStoreError, now, schema_versions

_COMPONENT = "lab_capability_creation"
metadata = MetaData()
capability_creation = Table(
    "lab_capability_creation",
    metadata,
    Column("workspace_id", String(100), primary_key=True),
    Column("capability_id", String(100), primary_key=True),
    # A null marker retains the unknown original date of a removed legacy entry.
    # Restoring its identity must not manufacture a new creation timestamp.
    Column("created_at", String(40)),
)


def upgrade_capability_creation_schema(engine) -> None:
    """Called under LibraryStore.schema_upgrade after the existing lab migration.

    This independent component leaves lab_storage version 2 and all old rows
    untouched. Older application versions can ignore this additive table.
    """
    with engine.connect() as connection:
        version = connection.execute(select(schema_versions.c.version).where(
            schema_versions.c.component == _COMPONENT
        )).scalar_one_or_none()
    if version not in (None, 1):
        raise LibraryStoreError("The capability display schema needs a compatible application.")
    if version is not None:
        return
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(insert(schema_versions).values(component=_COMPONENT, version=1))
    except IntegrityError:
        with engine.connect() as connection:
            current = connection.execute(select(schema_versions.c.version).where(
                schema_versions.c.component == _COMPONENT
            )).scalar_one()
        if current != 1:
            raise LibraryStoreError("The capability display schema changed concurrently.") from None


def record_capability_creation(connection, workspace_id, *, added_ids, removed_ids) -> None:
    """Use the existing save transaction; edits and retries cannot reset dates.

    No call is made while initially loading or importing a profile. Undated
    legacy removals get an undated marker, not the removal time. Markers remain
    after deletion so restoring the same identity preserves its original date.
    """
    added, removed = set(added_ids), set(removed_ids)
    if not added and not removed:
        return
    existing = set(connection.execute(select(capability_creation.c.capability_id).where(
        capability_creation.c.workspace_id == workspace_id
    )).scalars())
    unknown = (added | removed) - existing
    if not unknown:
        return
    timestamp = now() if added & unknown else None
    connection.execute(insert(capability_creation), [
        {
            "workspace_id": workspace_id,
            "capability_id": identifier,
            "created_at": timestamp if identifier in added else None,
        }
        for identifier in sorted(unknown)
    ])


def capability_creation_view(connection, workspace_id, lab) -> dict | None:
    """Capture current entries, timestamps and version together in one statement.

    If an overview read races a later lab save, return no display hint rather
    than applying chronology from a different profile/version to that page.
    """
    from scione.catalogs.sql_lab_store import lab_entries, lab_workspaces

    rows = connection.execute(
        select(
            lab_workspaces.c.profile_id,
            lab_workspaces.c.version,
            lab_entries.c.capability_id,
            capability_creation.c.created_at,
        )
        .select_from(lab_workspaces.outerjoin(
            lab_entries, lab_entries.c.workspace_id == lab_workspaces.c.workspace_id
        ).outerjoin(capability_creation, and_(
            capability_creation.c.workspace_id == lab_entries.c.workspace_id,
            capability_creation.c.capability_id == lab_entries.c.capability_id,
        )))
        .where(lab_workspaces.c.workspace_id == workspace_id)
    ).mappings().all()
    if not rows or rows[0]["profile_id"] != lab.id or rows[0]["version"] != lab.version:
        return None
    return {
        "lab_id": lab.id,
        "lab_version": lab.version,
        "created_at": {
            row["capability_id"]: row["created_at"]
            for row in rows if row["capability_id"] is not None and row["created_at"] is not None
        },
    }
