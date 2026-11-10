# Structured archive and SQL lab/matching storage

The lab-facing application is React/MUI and FastAPI. SQL stores the archive and primary
lab declarations. Original source artifacts and review/report-scope workspaces remain
file-backed pending authenticated storage migration.

| Record                        | Purpose                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------ |
| companies                     | User-supplied customer names; no invented source metadata                                  |
| specification_families        | One specification identity within a company                                                |
| specification_versions        | Uploaded document and source revision, with edit version                                   |
| extraction_records            | Immutable extracted source for a model/reference run                                       |
| requirements                  | Test-level rows in source order, with raw values and method references                     |
| requirement_claims            | Atomic parameter/kind/stage/unit columns and complete JSON detail                          |
| capability_revisions          | Saved lab declaration used in an assessment                                                |
| comparison_runs               | Immutable business assessment JSON and first-rendered PDF bytes                            |
| lab_workspaces                | Current SQL lab identity/version, scoped by a server-selected workspace ID                 |
| lab_capability_entries        | Separate current capability/test/method/price declarations                                 |
| lab_capability_claims         | Queryable parameter/role/stage/configuration/unit plus typed payload                       |
| lab_profile_revisions         | Immutable full lab declaration and normalizer version per edit                             |
| semantic_matching_runs        | Scoped immutable attempts: input, raw response, validated links, failures and comparisons  |
| reviewed_extraction_revisions | Immutable human-corrected facts, normalized snapshots, changed rows and source-run version |

Original PDFs, provider text, saved source reviews and retained legacy report artifacts
stay in the workspace. SQL rows reference their document and run IDs. Existing source
files are indexed lazily, and new successful analyses are indexed after saving the run.
Current analysis/review can use a derived geometric cache for ready legacy native
content-stream PDFs. It is keyed by the uploaded PDF, original representation and
parser policy. The original PDF, `document.json`, run history and raw output are
not replaced; OCR/non-native inputs do not enter that reorder path.
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
  approvals cannot silently certify changed facts. Edits retain existing test IDs and
  order. A separate source-backed append action assigns a `req_human_` ID and saves
  another facts revision; it never rewrites the machine result. Added rows participate
  in review, library views and matching. Duplicate additions, stale versions and
  unlocated source quotations are rejected. Removal is an explicit exclusion rather
  than deletion of source evidence.
  The review editor presents a selected parameter separately from raw source text;
  parameter edits retain their source link and undergo the same normalization guards.
  Original-wording edits require an explicit acknowledgement before rebuilding that
  test's detailed fields; ordinary parameter corrections preserve unrelated facts.
- Metadata edits use optimistic versions. Linking a new revision preserves the family
  identity and cannot silently change another customer's specification.
- A comparison fingerprint includes source/review input, lab revision, catalog version,
  normalized policy and saved document metadata. Changed inputs produce a new snapshot.
- Assessment identity hashes the complete computed payload, including the source-review
  policy version and coverage warnings. A warning-policy upgrade creates a new snapshot
  even with unchanged source/lab inputs; earlier PDF/JSON snapshots stay immutable.
- PDF bytes are frozen at first export. UI and JSON use that saved comparison.
- Matching attempts freeze exact candidate facts and normalizer version. A later
  normalization change can rebuild an archive index without changing old run artifacts.
- Matching cache includes source review/identity choices, capability/catalog facts,
  model/prompt/validation version and effective generation settings for developer model runs.
  The normal generation flow can reuse an existing accepted comparison for unchanged inputs.
  Requested model aliases and resolved response versions
  are saved separately so an alias does not trigger a new call on every click.
- Parameter-policy and input-projection revisions also bind the application matching
  fingerprint. The current source normalizer is `atomic_requirements_v0_32`, parameter
  policy `pilot_parameters_v6`, source review `0.13`, and semantic validation
  `semantic_alignment_guards_v0_17`. Extraction uses `customer_requirements_compact_v0.18`
  and semantic matching uses `semantic_matching_v0_8`.
- Report-only scope actions and quantities do not invalidate semantic matching. Editing
  lab, extraction or source/identity review inputs does; old attempts remain downloadable.

## Comparison policy

Method identity uses the catalog; the catalog contains metadata rather than standard text.
Natural-language extraction can produce grounded atomic claims in one provider request.
The local parser is a conservative fallback for historical runs. Numerical bounds, units,
stages and reportability are compared by code. Source validation and identity ambiguity
remain separate from a lab's declaration.

Provider JSON quantities are parsed as exact decimal/integer values rather than
rounded through binary floats. Typed JSON preserves decimals as strings, alongside
the untouched provider response and source fragments. Software precision/exponent
limits reject pathological values; unsupported source arithmetic remains unresolved.

Every active requested clause stays represented. Missing information is distinct from an
explicit limitation. A measurement/reporting range does not promise the material passes.
Workflow instructions are retained as instructions, not inferred lab capabilities.
Alternative methods and independent equipment configurations cannot be merged silently.
Report policy `decisive_rules_first_v5` retains document-level source-coverage hints
in `source_coverage_issues` and report notes. The interactive report shows one compact
warning with exact expandable notes and a link to the current specification. Filtering
extracted items cannot hide that document warning; it does not rewrite per-claim
verdicts or establish that an unverified phrase is a semantic omission.

Explicit AI matching proposes existing test/capability/claim correspondences and optional
wording equivalence for a selected pair of recorded context/category strings. All
requirements and claims must be covered exactly once; invented or out-of-candidate IDs
reject the attempt. Wording proposals retain both exact strings and a reason. Known
contradictions, missing fields, different unfamiliar codes, numeric changes and
incompatible stage/sequence/configuration cannot be overridden by a proposal.
Accepted wording remains a fallible semantic judgement. Numerical comparison uses
accepted links or unique, compatible registered fields within an already aligned
test/method declaration. It never uses arbitrary same-name fallback candidates. Missing source facts
cannot be repaired by a semantic suggestion. Human identity decisions stay authoritative.

A guarded `joint_scope_equivalence` can factor a positive nonnumeric context that
occurs verbatim once in the counterpart's text value. It binds both supplied joint
strings and one selected lab claim, and cannot combine declarations, infer a missing
qualifier or erase scientific differences. Original values/contexts and the proposal
remain separate from comparison copies. A plain measurement/reporting request can
use an explicit typed lab reporting capability without fabricating a numeric pass limit.
One required native reporting entry that combines total film and separate layer
thickness stays unresolved for a source edit into separate measurement claims.
Normalizer and semantic guards prevent a topcoat-only declaration from certifying
both outputs. Correctly separated atoms may share the original compound quotation;
no automatic source split or raw-run replacement occurs.

A missing named colour scale can be supplied to a detached normalized view only by
a unique, same-page physical body/summary proof with matching owner, parameter,
operator and value. The native model unit stays unchanged, with explicit source-span
proofs retained. Generic model commentary is never evidence for this projection or
for source consistency; closed unused comments are audited separately.
A separate closed-template acceptance-summary reconciliation can make a handling
comment nonblocking only when all four properties agree between unique physical
body/summary evidence and independently owned native criteria. Its scope, source
spans, roles, phases and original comment are retained; it is neither a general
summary evaluator nor a completeness certificate.

An accepted parameter link uses the registered comparison policy found on either side,
without choosing between conflicting known definitions. Policy selection and wording
bridges operate on comparison copies; stored identities, quantities, roles and original
text remain unchanged. A negative for one named material/resource does not establish a
negative for another value, and a missing category is not a wildcard presence request.

Until a current successful semantic attempt exists, the normal UI labels the comparison
as a Preview. **Generate report** in source review is an explicit, consumed navigation
action that starts configured-model comparison; **Generate/Update report** in Reports
does the same without exposing model controls. An existing current result is reused.
Opening/refreshing a report and changing its filters never call a model. Developer report
tools retain model settings, original history and eval downloads. Failed runs preserve
received output and usage where available without replacing a successful result.

Matching plans use `whole_requirement_batches_v6`: preflighted whole-test batches,
at most eight calls. Sail/Fireworks use `matching_keyed_v3` output and waves of at most
two concurrent calls; in-flight results are saved and later waves stop after a known
failure. The exact source test/claim keys are required by the native schema. Each row's
catalog/capability ID choices and lab-claim links are bounded to that row's candidates;
unresolved identity can use null, and candidate-free rows have empty unresolved links.
Runtime validation still checks the actual selected capability's claim ownership,
cross-field identity, complete coverage and scientific compatibility. Schema-valid
output remains a fallible proposal. No partial plan is accepted. Source extraction
still has a 60,000-character total input guard and is not shared-clause-aware chunking.

A source-proved unapproved alternative can remain an informational route note while
the explicit primary criterion is assessed. Matching snapshots preserve the physical
route proof separately from AI links. Selecting the alternative keeps its unissued
criterion unknown and never supplies it from the primary method's class. Source approval
and capability support remain different decisions.

The optional 134-field CSP-EN-301 demonstration lab is a source-authored synthetic profile
for walkthroughs, with explicit preparation, dimensions and reporting and a deliberate
post-cycle gloss limitation. It is not independent evaluation gold or real lab capability.
Loading it requires an explicit user action; replacement preserves prior lab revisions
and uploaded source data. The earlier 76-field profile remains available.

The matching artifact export separates raw extraction, normalized source claims, raw AI
matching, validated links, deterministic comparison and the captured human review state.
These are evaluation inputs, not an accuracy claim or independent ground truth.
Developer diagnostics return a current-policy `source_review_audits` bundle for a
selected source run. A new matching attempt separately freezes it in
`policy_snapshot.source_review_audits` before inference, including source run/PDF
identity and the review policy version. Physical summary/revision/unit proofs and
unused comments stay distinguishable. Current diagnostics cannot backfill or
certify the historical review state of an older attempt.
The semantic replay CLI labels recompilation with current guards as
`CURRENT_POLICY_RECOMPILE` and records actual policy source hashes. To score historical
machine verdicts, replay the original `comparison_validated` rather than silently
recompiling an old response with new code. Original run/score artifacts stay unchanged.

## Runtime and migration

`SCIONE_DATABASE_URL` accepts a SQLAlchemy MySQL/PyMySQL URL. Local Compose runs MySQL 8.4
with a project volume and loopback-only port. No URL uses embedded SQLite in the workspace.
SQLite is used by offline tests; MySQL DDL is compiled in tests. MySQL runtime needs the
user-started container and separate integration validation.

Matching history queries sort metadata rather than full JSON artifacts. Latest-run
selection first orders identifiers, then loads candidate payloads by key. This fixes
MySQL sort-buffer failures without increasing server buffers, changing schema versions,
resetting records or altering historical output.

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

## Generated-corpus evaluation artifacts

Use three separate layers: source-PDF extraction, matching with manually typed source
facts and independently authored synthetic lab declarations, and the complete production
workspace flow using actual saved model extractions. Contract validity and source location
are not factual accuracy. Code/atom identity, parameter role, units, stage, configuration,
missing information and unsupported claims need separate review.

`tests/fixtures/generated_matching_reference_v1.json` freezes 28 controlled scenarios
(29 parameter decisions) before their model outputs. `scripts/evaluate_generated_matching.py`
without saved runs reports software-policy probes, not model accuracy. Supply repeated
`--run` paths to score saved live matching batches offline. Missing/failed batches remain
in the full denominator; original source/label hashes and policy hashes are retained.

`scripts/evaluate_generated_corpus.py` accepts an explicitly supplied, source-only partial
reference and saved extraction directory. Its exact-designator and selected typed-payload
recovery metrics are lower bounds over those annotations, not whole-document precision
or production accuracy. Keep original runs/scores unchanged; label subsequent policy
replays and targeted development re-tests separately. The 22 generated readable PDFs
are development data after iterative inspection, not a blinded independent benchmark.

The matching export's `comparison_validated` evaluates matching against the supplied
facts. The user report additionally applies source-review checks and can be stricter.
`matching_run.child_attempts` contains actual per-batch raw outputs; the parent has no
fabricated raw response. Human corrections and source/quote decisions are separate.
