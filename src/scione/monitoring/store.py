"""Small durable operation ledger; never stores prompts, output text or secrets."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from scione.library.store import LibraryStoreError, schema_versions

metadata = MetaData()
operations = Table(
    "monitoring_operations",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("kind", String(20), nullable=False),
    Column("document_id", String(16), nullable=False),
    Column("started_at", String(40), nullable=False),
    Column("finished_at", String(40)),
    Column("status", String(20), nullable=False),
    Column("duration_ms", Float),
    Column("provider_latency_ms", Float),
    Column("provider_calls", Integer),
    Column("run_id", String(500)),
    Column("page_count", Integer),
    Column("test_count", Integer),
)
Index("ix_monitoring_started", operations.c.started_at)


def upgrade_monitoring_schema(library) -> None:
    with library.schema_upgrade() as engine:
        with engine.connect() as connection:
            version = connection.execute(
                select(schema_versions.c.version).where(schema_versions.c.component == "monitoring")
            ).scalar_one_or_none()
        if version not in {None, 1}:
            raise LibraryStoreError("The operation metrics schema needs a compatible version.")
        if version is None:
            metadata.create_all(engine)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        insert(schema_versions).values(component="monitoring", version=1)
                    )
            except IntegrityError:
                with engine.connect() as connection:
                    if (
                        connection.execute(
                            select(schema_versions.c.version).where(
                                schema_versions.c.component == "monitoring"
                            )
                        ).scalar_one()
                        != 1
                    ):
                        raise LibraryStoreError("The operation metrics schema changed.") from None


class MonitoringStore:
    def __init__(self, library):
        self.library = library

    def start(self, kind: str, document_id: str, *, page_count: int | None = None) -> str:
        if kind not in {"extraction", "matching"}:
            raise ValueError("Choose extraction or matching metrics.")
        if not isinstance(document_id, str) or len(document_id) != 16:
            raise ValueError("Choose a saved document.")
        upgrade_monitoring_schema(self.library)
        identifier = uuid4().hex
        with self.library.engine.begin() as connection:
            connection.execute(
                insert(operations).values(
                    id=identifier,
                    kind=kind,
                    document_id=document_id,
                    started_at=datetime.now(UTC).isoformat(),
                    status="running",
                    page_count=page_count,
                )
            )
        return identifier

    def finish(
        self,
        identifier: str,
        *,
        status: str,
        duration_ms: float,
        run_id: str | None = None,
        provider_latency_ms: float | None = None,
        provider_calls: int | None = None,
        page_count: int | None = None,
        test_count: int | None = None,
    ) -> None:
        if status not in {"success", "failure", "cache_reuse"}:
            raise ValueError("Choose a completed operation status.")
        for value in (duration_ms, provider_latency_ms, provider_calls, page_count, test_count):
            if value is not None and (value < 0 or value != value or value == float("inf")):
                raise ValueError("Operation measurements must be finite and nonnegative.")
        upgrade_monitoring_schema(self.library)
        values = {
            "finished_at": datetime.now(UTC).isoformat(),
            "status": status,
            "duration_ms": duration_ms,
            "run_id": run_id,
            "provider_latency_ms": provider_latency_ms,
            "provider_calls": provider_calls,
            "test_count": test_count,
        }
        if page_count is not None:
            values["page_count"] = page_count
        with self.library.engine.begin() as connection:
            result = connection.execute(
                update(operations)
                .where(operations.c.id == identifier, operations.c.status == "running")
                .values(**values)
            )
            if result.rowcount != 1:
                raise LibraryStoreError("The operation metrics entry is missing or complete.")

    def listing(self, since: str, *, limit: int) -> list[dict]:
        upgrade_monitoring_schema(self.library)
        with self.library.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(operations)
                    .where(operations.c.started_at >= since)
                    .order_by(operations.c.started_at.desc(), operations.c.id.desc())
                    .limit(limit)
                ).mappings()
            ]

    def latest_document(self, document_id: str) -> dict | None:
        """Return a bounded, metadata-only status for one owned document."""
        upgrade_monitoring_schema(self.library)
        with self.library.engine.connect() as connection:
            row = connection.execute(
                select(
                    operations.c.id, operations.c.kind, operations.c.status,
                    operations.c.started_at, operations.c.finished_at,
                    operations.c.run_id, operations.c.duration_ms,
                )
                .where(operations.c.document_id == document_id)
                .order_by(operations.c.started_at.desc(), operations.c.id.desc())
                .limit(1)
            ).mappings().first()
            return dict(row) if row is not None else None
