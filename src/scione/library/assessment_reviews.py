"""Append-only human processing decisions, separate from machine assessments."""

from __future__ import annotations

from uuid import uuid4

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

from scione.library.store import (
    LibraryConflict,
    LibraryStore,
    LibraryStoreError,
    now,
    schema_versions,
)

metadata = MetaData()
reviews = Table(
    "assessment_review_events",
    metadata,
    Column("workspace_id", String(100), primary_key=True),
    Column("review_id", String(36), primary_key=True),
    Column("comparison_id", String(32), nullable=False),
    Column("requirement_id", String(100), nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint(
        "workspace_id",
        "comparison_id",
        "requirement_id",
        "version",
        name="uq_assessment_review_version",
    ),
)


def upgrade_assessment_reviews_schema(library: LibraryStore) -> None:
    with library.schema_upgrade() as engine:
        with engine.connect() as connection:
            version = connection.execute(
                select(schema_versions.c.version).where(
                    schema_versions.c.component == "assessment_reviews"
                )
            ).scalar_one_or_none()
        if version not in (None, 1):
            raise LibraryStoreError("The report-review schema needs a compatible application.")
        if version is None:
            metadata.create_all(engine)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        insert(schema_versions).values(component="assessment_reviews", version=1)
                    )
            except IntegrityError:
                with engine.connect() as connection:
                    current = connection.execute(
                        select(schema_versions.c.version).where(
                            schema_versions.c.component == "assessment_reviews"
                        )
                    ).scalar_one()
                if current != 1:
                    raise LibraryStoreError(
                        "The report-review schema changed concurrently."
                    ) from None


class SQLAssessmentReviews:
    def __init__(self, library: LibraryStore, workspace_id: str):
        self.library, self.workspace_id = library, workspace_id

    def history(self, comparison_id: str) -> list[dict]:
        upgrade_assessment_reviews_schema(self.library)
        with self.library.engine.connect() as connection:
            return list(
                connection.execute(
                    select(reviews.c.payload)
                    .where(
                        reviews.c.workspace_id == self.workspace_id,
                        reviews.c.comparison_id == comparison_id,
                    )
                    .order_by(reviews.c.created_at, reviews.c.version)
                ).scalars()
            )

    def latest(self, comparison_id: str) -> dict[str, dict]:
        current = {}
        for event in self.history(comparison_id):
            identifier = event["requirement_id"]
            if identifier not in current or event["version"] > current[identifier]["version"]:
                current[identifier] = event
        return current

    def save(self, assessment: dict, requirement: dict, body) -> dict:
        upgrade_assessment_reviews_schema(self.library)
        comparison_id, identifier = assessment["comparison_id"], requirement["requirement_id"]
        event = {
            "review_id": str(uuid4()),
            "comparison_id": comparison_id,
            "requirement_id": identifier,
            "version": body.expected_version + 1,
            "created_at": now(),
            "actor": "local_reviewer",
            "input_fingerprint": assessment["report_input_fingerprint"],
            "machine_status": requirement["comparison"]["status"],
            "review_status": body.review_status,
            "decision": body.decision,
            "note": body.note,
        }
        try:
            with self.library.engine.begin() as connection:
                previous = connection.execute(
                    select(reviews.c.version)
                    .where(
                        reviews.c.workspace_id == self.workspace_id,
                        reviews.c.comparison_id == comparison_id,
                        reviews.c.requirement_id == identifier,
                    )
                    .order_by(reviews.c.version.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if (previous or 0) != body.expected_version:
                    raise LibraryConflict("This review changed. Refresh the report before saving.")
                connection.execute(
                    insert(reviews).values(
                        workspace_id=self.workspace_id,
                        review_id=event["review_id"],
                        comparison_id=comparison_id,
                        requirement_id=identifier,
                        version=event["version"],
                        created_at=event["created_at"],
                        payload=event,
                    )
                )
        except IntegrityError:
            raise LibraryConflict(
                "This review changed. Refresh the report before saving."
            ) from None
        return event
