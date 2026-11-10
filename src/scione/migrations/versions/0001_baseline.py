"""Frozen library v1 schema. Never import runtime metadata here."""

from alembic import op
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
    insert,
)
from sqlalchemy.dialects.mysql import LONGBLOB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

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


def upgrade():
    metadata.create_all(op.get_bind())
    op.get_bind().execute(insert(schema_versions).values(component="library", version=1))


def downgrade():
    raise RuntimeError("Restore the database and workspace backup to roll back.")
