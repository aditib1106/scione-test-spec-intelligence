"""Immutable SQL matching attempts, scoped to the server-selected workspace."""

from typing import Any

from sqlalchemy import JSON, Column, Index, MetaData, String, Table, insert, select
from sqlalchemy.exc import IntegrityError

from scione.library.store import (
    LibraryConflict,
    LibraryStore,
    LibraryStoreError,
    schema_versions,
)
from scione.matching.models import (
    MATCHING_PROMPT_VERSION,
    MATCHING_VALIDATION_VERSION,
    SemanticMatchingRun,
)

metadata = MetaData()
matching_runs = Table(
    "semantic_matching_runs",
    metadata,
    Column("workspace_id", String(100), primary_key=True),
    Column("run_id", String(200), primary_key=True),
    Column("document_id", String(100), nullable=False),
    Column("extraction_run_id", String(200), nullable=False),
    Column("input_fingerprint", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("status", String(20), nullable=False),
    Column("provider", String(100), nullable=False),
    Column("model", String(200), nullable=False),
    Column("prompt_version", String(100), nullable=False),
    Column("validation_version", String(100), nullable=False),
    Column("payload", JSON, nullable=False),
)
Index("ix_semantic_matching_document", matching_runs.c.workspace_id, matching_runs.c.document_id)


def upgrade_matching_schema(library: LibraryStore) -> None:
    with library.schema_upgrade() as engine:
        _upgrade_matching_schema(engine)


def _upgrade_matching_schema(engine) -> None:
    with engine.connect() as connection:
        version = connection.execute(
            select(schema_versions.c.version).where(
                schema_versions.c.component == "semantic_matching"
            )
        ).scalar_one_or_none()
    if version not in (None, 1):
        raise LibraryStoreError("The matching database needs a compatible application version.")
    if version is not None:
        return
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(schema_versions).values(component="semantic_matching", version=1)
            )
    except IntegrityError:
        with engine.connect() as connection:
            current = connection.execute(
                select(schema_versions.c.version).where(
                    schema_versions.c.component == "semantic_matching"
                )
            ).scalar_one()
        if current != 1:
            raise LibraryStoreError("The matching schema changed concurrently.") from None


class SQLMatchingStore:
    def __init__(self, library: LibraryStore, workspace_id: str):
        self.library = library
        self.workspace_id = workspace_id

    def _engine(self):
        upgrade_matching_schema(self.library)
        return self.library.engine

    def save(self, document_id: str, attempt: SemanticMatchingRun) -> None:
        payload = attempt.model_dump(mode="json")
        try:
            with self._engine().begin() as connection:
                connection.execute(
                    insert(matching_runs).values(
                        workspace_id=self.workspace_id,
                        run_id=attempt.run_id,
                        document_id=document_id,
                        extraction_run_id=attempt.input_snapshot.extraction_run_id,
                        input_fingerprint=attempt.input_fingerprint,
                        created_at=attempt.started_at.isoformat(),
                        status=attempt.status,
                        provider=attempt.requested_provider or attempt.provider,
                        model=attempt.requested_model or attempt.model,
                        prompt_version=attempt.prompt_version,
                        validation_version=attempt.validation_version,
                        payload=payload,
                    )
                )
        except IntegrityError:
            existing = self.load(document_id, attempt.run_id)
            if existing.model_dump(mode="json") != payload:
                raise LibraryConflict("A saved matching attempt cannot be overwritten.") from None

    def load(self, document_id: str, run_id: str) -> SemanticMatchingRun:
        with self._engine().connect() as connection:
            payload = connection.execute(
                select(matching_runs.c.payload).where(
                    matching_runs.c.workspace_id == self.workspace_id,
                    matching_runs.c.document_id == document_id,
                    matching_runs.c.run_id == run_id,
                )
            ).scalar_one_or_none()
        if payload is None:
            raise LibraryStoreError("This matching attempt was not found in this workspace.")
        return SemanticMatchingRun.model_validate(payload)

    def latest_completed(
        self,
        document_id: str,
        fingerprint: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        generation_settings: dict[str, Any] | None = None,
    ) -> SemanticMatchingRun | None:
        """Return the newest compatible success without changing saved history.

        Explicit inference requests supply their effective generation settings;
        browsing/report compilation leaves them unspecified to retain the latest
        result for the current source. Legacy attempts with no saved settings
        cannot satisfy a request that specifies a nonempty generation profile.
        """
        conditions = [
            matching_runs.c.workspace_id == self.workspace_id,
            matching_runs.c.document_id == document_id,
            matching_runs.c.input_fingerprint == fingerprint,
            matching_runs.c.status == "completed",
            matching_runs.c.prompt_version == MATCHING_PROMPT_VERSION,
            matching_runs.c.validation_version == MATCHING_VALIDATION_VERSION,
        ]
        if provider is not None:
            conditions.append(matching_runs.c.provider == provider)
        if model is not None:
            conditions.append(matching_runs.c.model == model)
        with self._engine().connect() as connection:
            candidates = connection.execute(
                select(matching_runs.c.payload)
                .where(*conditions)
                .order_by(matching_runs.c.created_at.desc(), matching_runs.c.run_id.desc())
            ).scalars()
            for payload in candidates:
                if (
                    generation_settings is not None
                    and payload.get("generation_settings", {}) != generation_settings
                ):
                    continue
                return SemanticMatchingRun.model_validate(payload)
        return None

    def listing(self, document_id: str) -> list[dict]:
        with self._engine().connect() as connection:
            rows = connection.execute(
                select(matching_runs)
                .where(
                    matching_runs.c.workspace_id == self.workspace_id,
                    matching_runs.c.document_id == document_id,
                )
                .order_by(matching_runs.c.created_at.desc(), matching_runs.c.run_id.desc())
            ).mappings()
            return [
                {key: value for key, value in row.items() if key not in {"payload", "workspace_id"}}
                for row in rows
            ]
