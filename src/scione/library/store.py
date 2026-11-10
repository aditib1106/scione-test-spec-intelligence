"""Small relational archive; original PDFs/model responses remain immutable files.

SQLite is an embedded development/test default. The same schema supports MySQL
through SCIONE_DATABASE_URL. No connection is opened by construction/import.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool

from scione.specifications.models import CustomerExtraction


class LibraryStoreError(RuntimeError):
    """A database operation failed, without exposing connection credentials."""


class LibraryConflict(LibraryStoreError):
    """The saved record changed or a revision relationship is inconsistent."""


metadata = MetaData()
schema_versions = Table(
    "schema_versions",
    metadata,
    Column("component", String(64), primary_key=True),
    Column("version", Integer, nullable=False),
)
companies = Table(
    "companies",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("lookup_key", String(64), unique=True, nullable=False),
    Column("name", String(300), nullable=False),
)
families = Table(
    "specification_families",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("company_id", String(32), ForeignKey("companies.id")),
    Column("specification_id", String(300)),
    Column("title", String(500)),
)
documents = Table(
    "specification_versions",
    metadata,
    Column("document_id", String(16), primary_key=True),
    Column("family_id", String(32), ForeignKey("specification_families.id"), nullable=False),
    Column("original_name", String(500), nullable=False),
    Column("revision", String(300)),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("version", Integer, nullable=False, default=0),
)
runs = Table(
    "extraction_records",
    metadata,
    Column("run_id", String(500), primary_key=True),
    Column(
        "document_id", String(16), ForeignKey("specification_versions.document_id"), nullable=False
    ),
    Column("normalizer_version", String(100), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("source", JSON, nullable=False),
)
requirements = Table(
    "requirements",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("run_id", String(500), ForeignKey("extraction_records.run_id"), nullable=False),
    Column("requirement_id", String(100), nullable=False),
    Column("position", Integer, nullable=False),
    Column("test_name", String(500), nullable=False),
    Column("methods", JSON, nullable=False),
    Column("raw", JSON, nullable=False),
    UniqueConstraint("run_id", "requirement_id", name="uq_requirements_run_requirement"),
)
claims = Table(
    "requirement_claims",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("requirement_key", String(64), ForeignKey("requirements.key"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("parameter_id", String(200)),
    Column("kind", String(50), nullable=False),
    Column("stage", String(200)),
    Column("unit", String(60)),
    Column("payload", JSON, nullable=False),
)
capabilities = Table(
    "capability_revisions",
    metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("lab_id", String(200), nullable=False),
    Column("lab_version", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
)
comparisons = Table(
    "comparison_runs",
    metadata,
    Column("comparison_id", String(32), primary_key=True),
    Column(
        "document_id", String(16), ForeignKey("specification_versions.document_id"), nullable=False
    ),
    Column("input_fingerprint", String(64), nullable=False, unique=True),
    Column("created_at", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("pdf", LargeBinary().with_variant(LONGBLOB(), "mysql")),
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _same_extraction_source(archived: object, current: dict) -> bool:
    """Allow additive contract defaults without replacing historical source JSON.

    Older archives can omit newly introduced optional fields, such as an atom's
    source_context. Revalidate that snapshot through the current strict contract
    for comparison only. Unknown fields, invalid records and actual source edits
    still conflict; no normalized content is written back to the source archive.
    """
    if archived == current:
        return True
    try:
        compatible = CustomerExtraction.model_validate(archived).model_dump(mode="json")
    except ValidationError:
        return False
    return compatible == current


class LibraryStore:
    def __init__(self, root: Path, database_url: str | None = None):
        self.root = Path(root).absolute()
        self.url = database_url
        self._engine: Engine | None = None
        self._lock = threading.RLock()

    @property
    def backend(self) -> str:
        return make_url(self.url).get_backend_name() if self.url else "sqlite"

    @property
    def engine(self) -> Engine:
        """The shared SQL connection pool, initialized lazily and without exposing its URL."""
        return self._db()

    @contextmanager
    def schema_upgrade(self):
        """Serialize this process's lazy schema initialization across requests.

        Cloud releases execute database-upgrade before web/worker start. This
        also protects the local app's concurrent first-use requests.
        """
        with self._lock:
            yield self._db()

    def _db(self) -> Engine:
        with self._lock:
            if self._engine is None:
                url = self.url
                if not url:
                    path = self.root / "library.sqlite3"
                    if path.is_symlink() or any(
                        p.is_symlink() for p in [self.root, *self.root.parents]
                    ):
                        raise LibraryStoreError("Database storage must not traverse a symlink.")
                    self.root.mkdir(parents=True, exist_ok=True)
                    url = f"sqlite:///{path}"
                engine = None
                try:
                    options = (
                        {"connect_args": {"check_same_thread": False, "timeout": 15}}
                        if make_url(url).get_backend_name() == "sqlite"
                        else {
                            "pool_pre_ping": True,
                            "pool_recycle": 1800,
                            "connect_args": {"connect_timeout": 5},
                        }
                    )
                    sqlite = make_url(url).get_backend_name() == "sqlite"
                    if sqlite and make_url(url).database in {None, "", ":memory:"}:
                        options["poolclass"] = StaticPool
                    engine = create_engine(url, **options)
                    if sqlite:

                        @event.listens_for(engine, "connect")
                        def sqlite_constraints(dbapi_connection, _record):
                            cursor = dbapi_connection.cursor()
                            cursor.execute("PRAGMA foreign_keys=ON")
                            cursor.close()

                    metadata.create_all(engine)
                    with engine.begin() as connection:
                        version = connection.execute(
                            select(schema_versions.c.version).where(
                                schema_versions.c.component == "library"
                            )
                        ).scalar_one_or_none()
                        if version is None:
                            connection.execute(
                                insert(schema_versions).values(component="library", version=1)
                            )
                        elif version != 1:
                            raise LibraryStoreError(
                                "This database schema needs a compatible application version."
                            )
                    self._engine = engine
                except (SQLAlchemyError, ValueError) as exc:
                    if engine is not None:
                        engine.dispose()
                    raise LibraryStoreError(
                        "The specification database is unavailable. Start the project database "
                        "and check its connection settings. Existing files have been preserved."
                    ) from exc
                except LibraryStoreError:
                    if engine is not None:
                        engine.dispose()
                    raise
            return self._engine

    def close(self) -> None:
        with self._lock:
            if self._engine:
                self._engine.dispose()
                self._engine = None

    def ensure_document(self, stored, source_metadata=None) -> None:
        """Index old file-backed documents lazily; never guess their company."""
        with self._lock, self._db().begin() as connection:
            existing = (
                connection.execute(
                    select(documents).where(documents.c.document_id == stored.document_id)
                )
                .mappings()
                .first()
            )
            if existing:
                indexed = connection.execute(
                    select(runs.c.run_id).where(runs.c.document_id == stored.document_id).limit(1)
                ).first()
                if source_metadata is not None and indexed is None:
                    family = (
                        connection.execute(
                            select(families).where(families.c.id == existing["family_id"])
                        )
                        .mappings()
                        .one()
                    )
                    additions = {
                        key: value
                        for key, value in {
                            "specification_id": source_metadata.identifier,
                            "title": source_metadata.title,
                        }.items()
                        if value and not family[key]
                    }
                    family_indexed = connection.execute(
                        select(runs.c.run_id)
                        .join(documents, runs.c.document_id == documents.c.document_id)
                        .where(documents.c.family_id == existing["family_id"])
                        .limit(1)
                    ).first()
                    if family_indexed is not None:
                        # A later revision cannot refill shared details that
                        # someone may have cleared after an earlier analysis.
                        additions = {}
                    if additions:
                        connection.execute(
                            update(families)
                            .where(families.c.id == existing["family_id"])
                            .values(**additions)
                        )
                        # Source metadata can become available after the user
                        # has supplied only a company. Fill blank fields without
                        # replacing entered values, and invalidate open editors
                        # of every version sharing those family details.
                        connection.execute(
                            update(documents)
                            .where(documents.c.family_id == existing["family_id"])
                            .values(version=documents.c.version + 1, updated_at=now())
                        )
                    if not existing["revision"] and source_metadata.revision:
                        changes = {"revision": source_metadata.revision, "updated_at": now()}
                        if not additions:
                            changes["version"] = documents.c.version + 1
                        result = connection.execute(
                            update(documents)
                            .where(
                                documents.c.document_id == stored.document_id,
                                documents.c.version == existing["version"] + bool(additions),
                            )
                            .values(**changes)
                        )
                        if result.rowcount != 1:
                            raise LibraryConflict(
                                "Specification details changed while indexing source metadata."
                            )
                return
            family_id = uuid4().hex
            connection.execute(
                insert(families).values(
                    id=family_id,
                    specification_id=source_metadata.identifier if source_metadata else None,
                    title=source_metadata.title if source_metadata else None,
                )
            )
            connection.execute(
                insert(documents).values(
                    document_id=stored.document_id,
                    family_id=family_id,
                    original_name=stored.original_name,
                    revision=source_metadata.revision if source_metadata else None,
                    created_at=stored.created_at.isoformat(),
                    updated_at=now(),
                    version=0,
                )
            )

    def document(self, document_id: str) -> dict:
        query = (
            select(
                documents,
                families.c.company_id,
                families.c.specification_id,
                families.c.title,
                companies.c.name.label("company_name"),
            )
            .join(families, documents.c.family_id == families.c.id)
            .outerjoin(companies, families.c.company_id == companies.c.id)
        )
        with self._db().connect() as connection:
            row = (
                connection.execute(query.where(documents.c.document_id == document_id))
                .mappings()
                .first()
            )
        if row is None:
            raise LibraryStoreError("This specification has not been saved to the library.")
        return dict(row)

    def edit_metadata(self, document_id: str, values: dict, expected_version: int) -> None:
        with self._lock, self._db().begin() as connection:
            current = (
                connection.execute(select(documents).where(documents.c.document_id == document_id))
                .mappings()
                .first()
            )
            if current is None or current["version"] != expected_version:
                raise LibraryConflict("Specification details changed. Reload before saving.")
            company_name = (values.get("company_name") or "").strip()
            company_id = None
            if company_name:
                lookup = digest(company_name.casefold())
                company_id = connection.execute(
                    select(companies.c.id).where(companies.c.lookup_key == lookup)
                ).scalar_one_or_none()
                if company_id is None:
                    company_id = uuid4().hex
                    connection.execute(
                        insert(companies).values(
                            id=company_id, name=company_name, lookup_key=lookup
                        )
                    )
            target = values.get("family_id") or current["family_id"]
            family = (
                connection.execute(select(families).where(families.c.id == target))
                .mappings()
                .first()
            )
            if family is None:
                raise LibraryConflict(
                    "Choose an existing specification or create a separate record."
                )
            if target != current["family_id"] and family["company_id"] != company_id:
                raise LibraryConflict("Choose a specification from the same customer company.")
            siblings = connection.execute(
                select(documents.c.document_id).where(
                    documents.c.family_id == target, documents.c.document_id != document_id
                )
            ).all()
            if target == current["family_id"] and siblings and family["company_id"] != company_id:
                raise LibraryConflict(
                    "A version must stay with its specification's customer company."
                )
            if target != current["family_id"]:
                # The existing family's identity belongs to all of its versions.
                # Linking an upload must not rename their specification or erase
                # its title because the new file has different/absent metadata.
                requested_id = values.get("specification_id") or None
                if (
                    requested_id
                    and family["specification_id"]
                    and (requested_id != family["specification_id"])
                ):
                    raise LibraryConflict(
                        "This version has a different specification number. "
                        "Choose the matching specification or save it separately."
                    )
            else:
                family_values = {
                    "company_id": company_id,
                    "specification_id": values.get("specification_id") or None,
                    "title": values.get("title") or None,
                }
                changed = any(family[key] != value for key, value in family_values.items())
                connection.execute(
                    update(families).where(families.c.id == target).values(**family_values)
                )
                if changed and siblings:
                    # Metadata is shared; invalidate sibling editors as well as
                    # the current editor when shared details are changed.
                    connection.execute(
                        update(documents)
                        .where(
                            documents.c.family_id == target,
                            documents.c.document_id != document_id,
                        )
                        .values(version=documents.c.version + 1, updated_at=now())
                    )
            result = connection.execute(
                update(documents)
                .where(
                    documents.c.document_id == document_id, documents.c.version == expected_version
                )
                .values(
                    family_id=target,
                    revision=values.get("revision") or None,
                    updated_at=now(),
                    version=expected_version + 1,
                )
            )
            if result.rowcount != 1:
                raise LibraryConflict("Specification details changed. Reload before saving.")

    def save_requirements(self, run, normalized: list) -> None:
        raw_ids = [item.id for item in run.extraction.requirements]
        normalized_by_id = {item.requirement_id: item for item in normalized}
        if len(normalized_by_id) != len(normalized) or set(normalized_by_id) != set(raw_ids):
            raise LibraryConflict("Structured requirements do not match this extraction run.")
        normalizer_version = normalized[0].normalizer_version if normalized else "none"
        if any(item.normalizer_version != normalizer_version for item in normalized):
            raise LibraryConflict(
                "Structured requirements must use the same normalization version."
            )
        source = run.extraction.model_dump(mode="json")
        with self._lock, self._db().begin() as connection:
            previous = (
                connection.execute(select(runs).where(runs.c.run_id == run.run_id))
                .mappings()
                .first()
            )
            if previous and (
                previous["document_id"] != run.document_id
                or not _same_extraction_source(previous["source"], source)
            ):
                raise LibraryConflict(
                    "An extraction run cannot be replaced with different source data."
                )
            if previous and previous["normalizer_version"] == normalizer_version:
                return
            if previous:
                keys = select(requirements.c.key).where(requirements.c.run_id == run.run_id)
                connection.execute(delete(claims).where(claims.c.requirement_key.in_(keys)))
                connection.execute(delete(requirements).where(requirements.c.run_id == run.run_id))
                connection.execute(
                    update(runs)
                    .where(runs.c.run_id == run.run_id)
                    .values(normalizer_version=normalizer_version)
                )
            else:
                connection.execute(
                    insert(runs).values(
                        run_id=run.run_id,
                        document_id=run.document_id,
                        normalizer_version=normalizer_version,
                        created_at=run.started_at.isoformat(),
                        source=source,
                    )
                )
            for position, raw in enumerate(run.extraction.requirements):
                item = normalized_by_id[raw.id]
                key = digest([run.document_id, run.run_id, raw.id])
                connection.execute(
                    insert(requirements).values(
                        key=key,
                        run_id=run.run_id,
                        requirement_id=raw.id,
                        position=position,
                        test_name=raw.original_test_name,
                        methods=[m.code for m in raw.requested_methods],
                        raw=raw.model_dump(mode="json"),
                    )
                )
                for index, claim in enumerate(item.claims):
                    connection.execute(
                        insert(claims).values(
                            key=digest([key, index]),
                            requirement_key=key,
                            position=index,
                            parameter_id=claim.parameter_id,
                            kind=claim.kind,
                            stage=claim.stage,
                            unit=claim.unit,
                            payload=claim.model_dump(mode="json"),
                        )
                    )

    def load_requirements(self, run_id: str) -> list[dict]:
        with self._db().connect() as connection:
            rows = (
                connection.execute(
                    select(requirements)
                    .where(requirements.c.run_id == run_id)
                    .order_by(requirements.c.position)
                )
                .mappings()
                .all()
            )
            result = []
            for row in rows:
                atoms = (
                    connection.execute(
                        select(claims.c.payload)
                        .where(claims.c.requirement_key == row["key"])
                        .order_by(claims.c.position)
                    )
                    .scalars()
                    .all()
                )
                result.append(
                    {
                        "requirement_id": row["requirement_id"],
                        "test_name": row["test_name"],
                        "methods": row["methods"],
                        "claims": list(atoms),
                    }
                )
            return result

    def save_capability_revision(self, lab, fingerprint: str) -> None:
        with self._lock, self._db().begin() as connection:
            previous = connection.execute(
                select(capabilities.c.payload).where(capabilities.c.fingerprint == fingerprint)
            ).scalar_one_or_none()
            if previous is not None and previous != lab.model_dump(mode="json"):
                raise LibraryConflict(
                    "A capability snapshot cannot be replaced with different data."
                )
            if previous is None:
                connection.execute(
                    insert(capabilities).values(
                        fingerprint=fingerprint,
                        lab_id=lab.id,
                        lab_version=lab.version,
                        created_at=now(),
                        payload=lab.model_dump(mode="json"),
                    )
                )

    def save_comparison(self, input_fingerprint: str, payload: dict) -> dict:
        with self._lock, self._db().begin() as connection:
            existing = connection.execute(
                select(comparisons.c.payload).where(
                    comparisons.c.input_fingerprint == input_fingerprint
                )
            ).scalar_one_or_none()
            if existing is not None:
                original = {
                    key: value
                    for key, value in existing.items()
                    if key not in {"comparison_id", "created_at"}
                }
                supplied = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"comparison_id", "created_at"}
                }
                if original != supplied:
                    raise LibraryConflict(
                        "Comparison inputs changed. Refresh before creating a new assessment."
                    )
                return existing
            saved = {**deepcopy(payload), "comparison_id": uuid4().hex, "created_at": now()}
            connection.execute(
                insert(comparisons).values(
                    comparison_id=saved["comparison_id"],
                    document_id=saved["document"]["document_id"],
                    input_fingerprint=input_fingerprint,
                    created_at=saved["created_at"],
                    payload=saved,
                )
            )
            return saved

    def comparison(self, comparison_id: str) -> dict:
        with self._db().connect() as connection:
            payload = connection.execute(
                select(comparisons.c.payload).where(comparisons.c.comparison_id == comparison_id)
            ).scalar_one_or_none()
        if payload is None:
            raise LibraryStoreError("This saved comparison was not found.")
        return payload

    def comparison_pdf(self, comparison_id: str, renderer: Callable[[dict], bytes]) -> bytes:
        """Freeze the first successful PDF export with its immutable JSON snapshot.

        Concurrent renderers may both prepare bytes, but only the first saved
        artifact wins. All callers return the stored bytes, even after a layout
        update, so a downloaded historical report never silently changes.
        """

        with self._db().connect() as connection:
            row = (
                connection.execute(
                    select(comparisons.c.payload, comparisons.c.pdf).where(
                        comparisons.c.comparison_id == comparison_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise LibraryStoreError("This saved comparison was not found.")
        if row["pdf"] is not None:
            return bytes(row["pdf"])
        rendered = renderer(deepcopy(row["payload"]))
        if not isinstance(rendered, bytes) or not rendered.startswith(b"%PDF-"):
            raise LibraryStoreError("The report renderer did not return a valid PDF artifact.")
        with self._lock, self._db().begin() as connection:
            connection.execute(
                update(comparisons)
                .where(
                    comparisons.c.comparison_id == comparison_id,
                    comparisons.c.pdf.is_(None),
                )
                .values(pdf=rendered)
            )
            stored = connection.execute(
                select(comparisons.c.pdf).where(comparisons.c.comparison_id == comparison_id)
            ).scalar_one_or_none()
        if stored is None:
            raise LibraryConflict("The saved comparison changed while creating its PDF.")
        return bytes(stored)

    def clear(self) -> None:
        """Used only by the explicit recoverable workspace reset."""
        with self._lock, self._db().begin() as connection:
            for table in reversed(metadata.sorted_tables):
                if table is not schema_versions:
                    connection.execute(delete(table))
