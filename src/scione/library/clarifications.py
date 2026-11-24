"""Manually recorded customer questions/replies, separate from machine findings."""

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

from scione.api.public_views import current_actor
from scione.auth.models import current_principal
from scione.library.store import (
    LibraryConflict,
    LibraryStore,
    LibraryStoreError,
    digest,
    now,
    schema_versions,
)

metadata = MetaData()
clarifications = Table(
    "customer_clarification_events",
    metadata,
    Column("workspace_id", String(100), primary_key=True),
    Column("event_id", String(36), primary_key=True),
    Column("document_id", String(16), nullable=False),
    Column("source_run_id", String(500), nullable=False),
    Column("requirement_id", String(100), nullable=False),
    Column("comparison_id", String(32), nullable=False),
    Column("client_request_id", String(36), nullable=False),
    Column("request_fingerprint", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint(
        "workspace_id",
        "document_id",
        "source_run_id",
        "requirement_id",
        "version",
        name="uq_customer_clarification_version",
    ),
    UniqueConstraint(
        "workspace_id",
        "client_request_id",
        name="uq_customer_clarification_request",
    ),
)


def upgrade_clarifications_schema(library: LibraryStore) -> None:
    """Add one independent component; existing source and review rows are untouched."""
    with library.schema_upgrade() as engine:
        with engine.connect() as connection:
            version = connection.execute(
                select(schema_versions.c.version).where(
                    schema_versions.c.component == "customer_clarifications"
                )
            ).scalar_one_or_none()
        if version not in (None, 1):
            raise LibraryStoreError(
                "The customer-clarification schema needs a compatible application."
            )
        if version is None:
            metadata.create_all(engine)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        insert(schema_versions).values(
                            component="customer_clarifications", version=1
                        )
                    )
            except IntegrityError:
                with engine.connect() as connection:
                    current = connection.execute(
                        select(schema_versions.c.version).where(
                            schema_versions.c.component == "customer_clarifications"
                        )
                    ).scalar_one()
                if current != 1:
                    raise LibraryStoreError(
                        "The customer-clarification schema changed concurrently."
                    ) from None


class SQLCustomerClarifications:
    def __init__(self, library: LibraryStore, workspace_id: str):
        self.library, self.workspace_id = library, workspace_id

    def _scope(self, document_id: str, source_run_id: str, requirement_id: str):
        return (
            clarifications.c.workspace_id == self.workspace_id,
            clarifications.c.document_id == document_id,
            clarifications.c.source_run_id == source_run_id,
            clarifications.c.requirement_id == requirement_id,
        )

    def history(self, document_id: str, source_run_id: str, requirement_id: str) -> list[dict]:
        upgrade_clarifications_schema(self.library)
        with self.library.engine.connect() as connection:
            return list(
                connection.execute(
                    select(clarifications.c.payload)
                    .where(*self._scope(document_id, source_run_id, requirement_id))
                    .order_by(clarifications.c.version)
                ).scalars()
            )

    def snapshot(self, assessment: dict) -> dict[str, dict]:
        """Capture only this authorized source run's currently present tests."""
        upgrade_clarifications_schema(self.library)
        identifiers = {row["requirement_id"] for row in assessment["requirements"]}
        if not identifiers:
            return {}
        with self.library.engine.connect() as connection:
            events = connection.execute(
                select(clarifications.c.payload)
                .where(
                    clarifications.c.workspace_id == self.workspace_id,
                    clarifications.c.document_id == assessment["document"]["document_id"],
                    clarifications.c.source_run_id == assessment["source_run_id"],
                    clarifications.c.requirement_id.in_(identifiers),
                )
                .order_by(clarifications.c.requirement_id, clarifications.c.version)
            ).scalars()
            snapshot: dict[str, dict] = {}
            for event in events:
                thread = snapshot.setdefault(event["requirement_id"], {"version": 0, "events": []})
                thread["version"] = event["version"]
                thread["events"].append(event)
        return snapshot

    def save(self, assessment: dict, requirement: dict, body) -> dict:
        """Append once, with thread-version locking and scoped idempotent retries."""
        upgrade_clarifications_schema(self.library)
        document_id = assessment["document"]["document_id"]
        source_run_id = assessment["source_run_id"]
        requirement_id = requirement["requirement_id"]
        scope = self._scope(document_id, source_run_id, requirement_id)
        request_id = str(body.client_request_id)
        actor = current_actor()
        principal = current_principal()
        actor_label = "Local reviewer"
        if principal is not None and principal.user_id != "local":
            actor_label = principal.display_name.strip() or principal.username.strip() or "Lab user"
        fingerprint = digest(
            {
                "document_id": document_id,
                "source_run_id": source_run_id,
                "actor": actor,
                "request": body.model_dump(mode="json"),
            }
        )

        def prior_request(connection):
            prior = connection.execute(
                select(clarifications.c.request_fingerprint, clarifications.c.payload).where(
                    clarifications.c.workspace_id == self.workspace_id,
                    clarifications.c.client_request_id == request_id,
                )
            ).one_or_none()
            if prior is None:
                return None
            if prior.request_fingerprint != fingerprint:
                raise LibraryConflict(
                    "This save request was already used for different clarification details."
                )
            return prior.payload

        event_id = str(uuid4())
        event = {
            "event_id": event_id,
            "question_id": str(body.question_id) if body.question_id else event_id,
            "document_id": document_id,
            "source_run_id": source_run_id,
            "requirement_id": requirement_id,
            "comparison_id": assessment["comparison_id"],
            "input_fingerprint": assessment["report_input_fingerprint"],
            "version": body.expected_version + 1,
            "client_request_id": request_id,
            "event_kind": body.event_kind,
            "text": body.text,
            "contact": body.contact,
            "actor": actor,
            "actor_label": actor_label,
            "created_at": now(),
        }
        try:
            with self.library.engine.begin() as connection:
                prior = prior_request(connection)
                if prior is not None:
                    return prior
                previous = connection.execute(
                    select(clarifications.c.version)
                    .where(*scope)
                    .order_by(clarifications.c.version.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if (previous or 0) != body.expected_version:
                    raise LibraryConflict(
                        "This clarification log changed. Refresh it before saving."
                    )
                if body.event_kind == "customer_reply":
                    question = connection.execute(
                        select(clarifications.c.payload).where(
                            *scope, clarifications.c.event_id == str(body.question_id)
                        )
                    ).scalar_one_or_none()
                    if question is None or question["event_kind"] != "question":
                        raise LibraryConflict(
                            "Choose a question from this test's current clarification log."
                        )
                connection.execute(
                    insert(clarifications).values(
                        workspace_id=self.workspace_id,
                        event_id=event_id,
                        document_id=document_id,
                        source_run_id=source_run_id,
                        requirement_id=requirement_id,
                        comparison_id=assessment["comparison_id"],
                        client_request_id=request_id,
                        request_fingerprint=fingerprint,
                        version=event["version"],
                        created_at=event["created_at"],
                        payload=event,
                    )
                )
        except IntegrityError:
            # A concurrent identical request succeeds once. A different write
            # at the same thread version must be reviewed instead of overwritten.
            with self.library.engine.connect() as connection:
                prior = prior_request(connection)
                if prior is not None:
                    return prior
            raise LibraryConflict(
                "This clarification log changed. Refresh it before saving."
            ) from None
        return event
