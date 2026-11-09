# Structured archive, schema v1

The lab-facing application is React/MUI and FastAPI. SQL stores the new archive;
immutable source artifacts and the retained lab/review editor continue using file stores.

| Record | Purpose |
| --- | --- |
| companies | User-supplied customer names; no invented source metadata |
| specification_families | One specification identity within a company |
| specification_versions | Uploaded document and source revision, with edit version |
| extraction_records | Immutable extracted source for a model/reference run |
| requirements | Test-level rows in source order, with raw values and method references |
| requirement_claims | Atomic parameter/kind/stage/unit columns and complete JSON detail |
| capability_revisions | Saved lab declaration used in an assessment |
| comparison_runs | Immutable business assessment JSON and first-rendered PDF bytes |

Original PDFs, provider text, saved source reviews and retained legacy report artifacts
stay in the workspace. SQL rows reference their document and run IDs. Existing source
files are indexed lazily, and new successful analyses are indexed after saving the run.
The lab editor remains file-backed; assessments persist the exact lab revision to SQL.

## Version boundaries

- Different customer PDF revisions are separate records; users explicitly link them.
- Re-running extraction does not create a new customer revision.
- Raw extraction is immutable. Normalized claims are derived with an explicit normalizer
  version; changing that version can rebuild derived rows without rewriting raw data.
- Metadata edits use optimistic versions. Linking a new revision preserves the family
  identity and cannot silently change another customer's specification.
- A comparison fingerprint includes source/review input, lab revision, catalog version,
  normalized policy and saved document metadata. Changed inputs produce a new snapshot.
- PDF bytes are frozen at first export. UI and JSON use that saved comparison.

## Comparison policy

Method identity uses the catalog; the catalog contains metadata rather than standard text.
Natural-language extraction can produce grounded atomic claims in one provider request.
The local parser is a conservative fallback for historical runs. Numerical bounds, units,
stages and reportability are compared by code. Source validation and identity ambiguity
remain separate from a lab's declaration.

Every active requested clause stays represented. Missing information is distinct from an
explicit limitation. A measurement/reporting range does not promise the material passes.
Workflow instructions are retained as instructions, not inferred lab capabilities.
Alternative methods and independent equipment configurations cannot be merged silently.

## Runtime and migration

`SCIONE_DATABASE_URL` accepts a SQLAlchemy MySQL/PyMySQL URL. Local Compose runs MySQL 8.4
with a project volume and loopback-only port. No URL uses embedded SQLite in the workspace.
SQLite is used by offline tests; MySQL DDL is compiled in tests. MySQL runtime needs the
user-started container and separate integration validation.

Schema v1 is created on first use and recorded in `schema_versions`; incompatible recorded
versions are rejected. Future schema changes need explicit migration scripts and backups.
Switching engines or servers does not copy data. Document IDs/run IDs and source artifacts
must migrate together. A production client setup should own the database, object storage,
credentials, authentication and backup policy. No public deployment is configured here.

Embedded reset closes connections and moves database/files into recovery storage. A
configured external database blocks reset rather than performing a partial destructive
reset. Cross-version automatic semantic comparison is outside this iteration.
