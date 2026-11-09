"""Immutable human corrections, separate from original model extraction artifacts.

Revisions change facts, not whether a lab can perform a test. A changed row loses
its earlier confirmation; a later explicit review can confirm the corrected row.
The initial editor corrects existing rows. Add/remove use source review exclusions
for now, so IDs remain stable across report actions and evaluation artifacts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    JSON,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
)
from sqlalchemy.exc import IntegrityError

from scione.library.store import LibraryConflict, LibraryStore, LibraryStoreError, schema_versions
from scione.specifications.models import CustomerExtractionRun, CustomerRequirement
from scione.specifications.store import RequirementReviewSet
from scione.structured import normalize_requirement


class FactsRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str
    document_id: str
    source_run_id: str
    version: int = Field(ge=1)
    parent_revision_id: str | None = None
    created_at: datetime
    actor: str = "local_reviewer"
    note: str = Field(min_length=1, max_length=4000)
    requirements: list[CustomerRequirement]
    changed_requirement_ids: list[str]
    item_updated_at: dict[str, datetime]


metadata = MetaData()
facts_revisions = Table(
    "reviewed_extraction_revisions",
    metadata,
    Column("workspace_id", String(100), primary_key=True),
    Column("revision_id", String(36), primary_key=True),
    Column("document_id", String(100), nullable=False),
    Column("source_run_id", String(500), nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("normalized_requirements", JSON, nullable=False),
    UniqueConstraint(
        "workspace_id",
        "document_id",
        "source_run_id",
        "version",
        name="uq_reviewed_extraction_version",
    ),
)


def upgrade_corrections_schema(library: LibraryStore) -> None:
    with library.schema_upgrade() as engine:
        with engine.connect() as connection:
            version = connection.execute(
                select(schema_versions.c.version).where(
                    schema_versions.c.component == "reviewed_extractions"
                )
            ).scalar_one_or_none()
        if version not in (None, 1):
            raise LibraryStoreError("The reviewed-facts schema needs a compatible application.")
        if version is None:
            metadata.create_all(engine)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        insert(schema_versions).values(component="reviewed_extractions", version=1)
                    )
            except IntegrityError:
                with engine.connect() as connection:
                    current = connection.execute(
                        select(schema_versions.c.version).where(
                            schema_versions.c.component == "reviewed_extractions"
                        )
                    ).scalar_one()
                if current != 1:
                    raise LibraryStoreError(
                        "The reviewed-facts schema changed concurrently."
                    ) from None


class SQLFactsStore:
    def __init__(self, library: LibraryStore, workspace_id: str):
        self.library, self.workspace_id = library, workspace_id

    def _engine(self):
        upgrade_corrections_schema(self.library)
        return self.library.engine

    def latest(self, document_id: str, run_id: str) -> FactsRevision | None:
        with self._engine().connect() as connection:
            payload = connection.execute(
                select(facts_revisions.c.payload)
                .where(
                    facts_revisions.c.workspace_id == self.workspace_id,
                    facts_revisions.c.document_id == document_id,
                    facts_revisions.c.source_run_id == run_id,
                )
                .order_by(facts_revisions.c.version.desc())
                .limit(1)
            ).scalar_one_or_none()
        return FactsRevision.model_validate(payload) if payload else None

    def save(
        self,
        run: CustomerExtractionRun,
        requirements: list[CustomerRequirement],
        note: str,
        *,
        expected_version: int,
    ) -> FactsRevision:
        current = self.latest(run.document_id, run.run_id)
        if (current.version if current else 0) != expected_version:
            raise LibraryConflict("Extracted content changed. Reload before saving corrections.")
        previous = current.requirements if current else run.extraction.requirements
        if [item.id for item in requirements] != [item.id for item in previous]:
            raise LibraryStoreError("Corrections must retain the existing test IDs and order.")
        changed = [a.id for a, b in zip(requirements, previous, strict=True) if a != b]
        if not changed:
            raise LibraryStoreError("No extracted content has changed.")
        now = datetime.now(UTC)
        revision = FactsRevision(
            revision_id=str(uuid4()),
            document_id=run.document_id,
            source_run_id=run.run_id,
            version=expected_version + 1,
            parent_revision_id=current.revision_id if current else None,
            created_at=now,
            note=note,
            requirements=requirements,
            changed_requirement_ids=changed,
            item_updated_at={
                **(current.item_updated_at if current else {}),
                **dict.fromkeys(changed, now),
            },
        )
        try:
            with self._engine().begin() as connection:
                connection.execute(
                    insert(facts_revisions).values(
                        workspace_id=self.workspace_id,
                        revision_id=revision.revision_id,
                        document_id=run.document_id,
                        source_run_id=run.run_id,
                        version=revision.version,
                        created_at=now.isoformat(),
                        payload=revision.model_dump(mode="json"),
                        normalized_requirements=[
                            normalize_requirement(item).model_dump(mode="json")
                            for item in requirements
                        ],
                    )
                )
        except IntegrityError:
            raise LibraryConflict(
                "Corrections changed concurrently. Reload before saving."
            ) from None
        return revision


def effective_facts(
    run: CustomerExtractionRun, review: RequirementReviewSet, revision: FactsRevision | None
):
    if revision is None:
        return run, review
    if (revision.document_id, revision.source_run_id) != (run.document_id, run.run_id):
        raise LibraryStoreError("Reviewed facts belong to another source run.")
    effective = run.model_copy(deep=True)
    effective.extraction.requirements = [
        item.model_copy(deep=True) for item in revision.requirements
    ]
    decisions = review.model_copy(deep=True)
    decisions.decisions = {
        identifier: decision
        for identifier, decision in decisions.decisions.items()
        if identifier not in revision.item_updated_at
        or decision.updated_at > revision.item_updated_at[identifier]
    }
    return effective, decisions
