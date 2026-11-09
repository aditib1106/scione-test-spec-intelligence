# Structured archive and SQL lab/matching storage

The lab-facing application is React/MUI and FastAPI. SQL stores the archive and primary
lab declarations. Original source artifacts and review/report-scope workspaces remain
file-backed pending authenticated storage migration.

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
| lab_workspaces | Current SQL lab identity/version, scoped by a server-selected workspace ID |
| lab_capability_entries | Separate current capability/test/method/price declarations |
| lab_capability_claims | Queryable parameter/role/stage/configuration/unit plus typed payload |
| lab_profile_revisions | Immutable full lab declaration and normalizer version per edit |
| semantic_matching_runs | Scoped immutable attempts: input, raw response, validated links, failures and comparisons |
| reviewed_extraction_revisions | Immutable human-corrected facts, normalized snapshots, changed rows and source-run version |

Original PDFs, provider text, saved source reviews and retained legacy report artifacts
stay in the workspace. SQL rows reference their document and run IDs. Existing source
files are indexed lazily, and new successful analyses are indexed after saving the run.
An existing lab.json is imported only when the SQL workspace is empty, without editing
the source file. Later reads/edits use SQL. Current entries and claims update in one
transaction with compare-and-swap version checks; historical snapshots remain unchanged.
The old Streamlit interface remains file-backed and must not be used as a SQL lab editor.

## Version boundaries

- Different customer PDF revisions are separate records; users explicitly link them.
- Re-running extraction does not create a new customer revision.
- Raw extraction is immutable. Normalized claims are derived with an explicit normalizer
  version; changing that version can rebuild derived rows without rewriting raw data.
- Source corrections create a separate SQL revision. Business views and matching use the
  effective facts, while the raw extraction index remains unchanged. Changed rows lose
  older confirmations; unchanged rows retain theirs. Earlier mapping and quantity
  approvals cannot silently certify changed facts. The initial editor retains test IDs
  and order; adding wholly missing tests is not implemented by this editor.
- Metadata edits use optimistic versions. Linking a new revision preserves the family
  identity and cannot silently change another customer's specification.
- A comparison fingerprint includes source/review input, lab revision, catalog version,
  normalized policy and saved document metadata. Changed inputs produce a new snapshot.
- PDF bytes are frozen at first export. UI and JSON use that saved comparison.
- Matching attempts freeze exact candidate facts and normalizer version. A later
  normalization change can rebuild an archive index without changing old run artifacts.
- Matching cache includes source review/identity choices, capability/catalog facts,
  model/prompt/validation version and effective generation settings for explicit runs.
  Requested model aliases and resolved response versions
  are saved separately so an alias does not trigger a new call on every click.
- Parameter-policy and input-projection revisions also bind the application matching
  fingerprint. The current source normalizer is `atomic_requirements_v0_9`, parameter
  policy `pilot_parameters_v2`, and semantic validation `semantic_alignment_guards_v0_3`.
- Report-only scope actions and quantities do not invalidate semantic matching. Editing
  lab, extraction or source/identity review inputs does; old attempts remain downloadable.

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

Explicit AI matching only proposes existing test/capability/claim correspondences. All
requirements and claims must be covered exactly once; invented or out-of-candidate IDs
reject the attempt. Context/role conflicts remain unknown. Numerical comparison uses
only accepted links, never arbitrary same-name fallback candidates. Missing source facts
cannot be repaired by a semantic suggestion. Human identity decisions stay authoritative.

Until a current successful semantic attempt exists, the UI explicitly labels rules-only
comparison. Opening a page never calls a model. Failed runs preserve received partial or
invalid output and usage where available; they do not replace a successful result.

The matching artifact export separates raw extraction, normalized source claims, raw AI
matching, validated links, deterministic comparison and the captured human review state.
These are evaluation inputs, not an accuracy claim or independent ground truth.

## Runtime and migration

`SCIONE_DATABASE_URL` accepts a SQLAlchemy MySQL/PyMySQL URL. Local Compose runs MySQL 8.4
with a project volume and loopback-only port. No URL uses embedded SQLite in the workspace.
SQLite is used by offline tests; MySQL DDL is compiled in tests. MySQL runtime needs the
user-started container and separate integration validation.

The archive remains component `library=1`; additive migrations introduce `lab_storage=1`,
`semantic_matching=1` and `reviewed_extractions=1`, recorded in `schema_versions`.
Run `scione database-upgrade` before starting application processes.
Local lazy initialization is serialized per shared
store; deployment migration must precede multiple web/worker processes. Existing archive
tables are preserved. Incompatible component versions fail without resetting data.
This is not yet the full tenant/auth schema migration: document hash identities, company
uniqueness and file ownership still need the planned migration before public use.
Switching engines or servers does not copy data. Document IDs/run IDs and source artifacts
must migrate together. A production client setup should own the database, object storage,
credentials, authentication and backup policy. No public deployment is configured here.

Embedded reset closes connections and moves database/files into recovery storage. A
configured external database blocks reset rather than performing a partial destructive
reset. Cross-version automatic semantic comparison is outside this iteration.
