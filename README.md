# SciOne Test Specification Intelligence

CMU Heinz Capstone: a lab-facing specification archive and capability-gap prototype.
React + TypeScript + MUI is the primary interface. FastAPI owns extraction, validation,
comparison, persistence and PDF generation. Streamlit remains a compatibility tool.

## Current scope

One lab maintains its capabilities, saves customer specifications by company, extracts
requirements, checks source evidence, compares with its declarations and exports a draft
report. Reading the full content of referenced standards is outside the MVP.

- **My Lab**: manually enter tests, methods, conditions, reportable outputs and optional
  rates. Normal startup preserves existing declarations. A deliberate development setup
  can initialize the team's synthetic lab with `python scripts/initialize_lab.py`;
  replacement requires `--replace-existing` and preserves previous SQL revisions.
  There are no sample-loading buttons in the user workflow.
  Detailed parameters support arbitrary names, values/ranges, units and context. Current
  declarations, queryable parameter rows and immutable revisions are saved to SQL.
  Identical general text declarations recorded on every service appear once under
  **Shared lab services**. Service summaries prioritize their own limits and outputs;
  numerical limits and unavailable services are never grouped away.
  Conditions and Criteria / reporting offer common parameter presets and typed input.
  Unfamiliar custom fields remain visible as unresolved; they are not automatically
  certified because their labels resemble a familiar parameter.
- **Test Catalog**: 99 synthetic test identities and 153 active methods, with curated
  aliases and stable pilot IDs. All 32,000 rows of the data team's synthetic method
  metadata are retained in a separate searchable index. Only bounded, relevant
  candidates enter matching; a catalog entry never creates lab capability or
  establishes equivalence between different methods.
- **Specifications → Library**: company/search filters, specification metadata, revisions
  and saved requirements. Remove a specification version from the normal library with a
  confirmation; source files and historical reports remain stored. Re-uploading the identical
  PDF restores it. Select a test to see method/condition/criteria summaries;
  expand detailed requirements for parameter, unit, stage and source-page records.
  Identical PDF bytes reopen the saved record, retaining the first upload's filename
  and latest analysis. A notice explains reuse; **Analyze again** creates a new run.
- **Specifications → PDF & review**: a PDF reading pane beside a selected test's
  method, condition and criterion tables. Individual parameter rows show value,
  unit and stage; compact source icons and page badges locate their evidence.
  Conditions and Criteria are the main groups, with sample plans, applicability,
  instructions and original wording available on demand. Source notes stay collapsed;
  unresolved notes show the preserved source wording rather than internal parser fragments.
  Meaningful unparsed text is retained. Sample planning keeps lot counts
  and allocation separate from operating conditions. PDF pages support zoom, fit width
  and fullscreen reading with pagination. Source confirmation does not approve lab support.
  **Edit test** is the single review-pane edit entry. The editor focuses on one selected requirement or parameter and saves a separate
  reviewed-facts revision. Other parameters, method roles, stage details and optional
  review notes expand only when needed. Optional bulk review sits in the test-header
  menu rather than the reading pane. Matching and
  Library use the corrected draft; original model output remains unchanged. Changed
  rows lose their earlier confirmation, without resetting unchanged rows.
  **Add missing test** recovers source-backed omissions. Under Edit's detailed
  options, changing original wording requires an explicit acknowledgement before
  rebuilding that test's detailed fields. Ordinary parameter edits preserve the
  other fields; original model results remain unchanged. A document revision question appears once
  in review, Library and Reports rather than flagging every test. A source-proved,
  approval-dependent alternative can remain an expandable note while the explicit
  primary route is reviewed; selecting that alternative does not supply its missing criterion.
- **Reports**: a comparison workspace with result filtering, search and priority/source
  ordering in the test list. Method codes appear side by side in the main table;
  Condition and Criteria expand independently into parameter comparison tables.
  Result badges distinguish matches, gaps and missing information; explanations are
  available on demand. Unresolved source-coverage hints stay in one document warning
  with expandable notes and a link to PDF review, including when results are filtered.
  Filters, selection and expanded
  groups stay in place during refresh and report-scope actions. PDF previews and
  PDF/JSON downloads are a separate optional view. **Generate report** in source review
  continues to Reports and starts the configured-model comparison. **Generate/Update
  report** is also available in Reports; current completed comparisons are reused.
  **Accept assessment** records that the lab reviewed this test's findings. **Edit
  decision** records a follow-up plan and note, such as customer clarification,
  outsourcing or declining the test. These append-only reviews belong to the exact
  saved assessment; changed inputs require a new review. Acceptance does not turn
  a gap or missing information into a capability match. Source and lab corrections
  remain separate operations. Review history has its own JSON download; the saved
  comparison PDF remains an immutable machine-assessment snapshot.
  AI aligns supplied identities/parameters and code checks the linked values. Browsing,
  refreshing and changing filters never start inference. Progress shows elapsed time;
  users can keep viewing the saved report while it runs. Remove/restore tests without
  deleting source data. Model settings, history and machine artifacts are under
  **Developer report tools**. The main navigation
  can collapse to an icon rail on desktop without leaving the current page.
- **Developer Tools**: original provider output, run history and diagnostics. **Reports →
  Report options → Review test matching** retains the earlier mapping/scope and manual
  quantity/price workflow.
  Its older report snapshots remain separate from the new structured reports.

There is no production authentication, multi-lab routing or formal quote approval.
Unknown prices, quantities, partner costs and lead times are not invented. Capability
assessment does not predict whether the customer's material will pass a test.

## Setup and run

Use Python 3.11+, Node.js 22.12+, and uv. For local MySQL, use Docker Desktop;
you do not need a host MySQL service. Run commands from the repository root:

```bash
uv sync --extra dev --locked
```

For model analysis, copy `.env.example` to `.env` only if `.env` does not already exist,
then enter your provider key. Preserve existing keys. Never commit `.env` or credentials.
Opening saved records/reports and exporting do not call a model. Analysis and report
generation start only after their explicit user actions.

### Fireworks and Sail model APIs

Create a key in [Fireworks API Keys](https://app.fireworks.ai/settings/users/api-keys)
or the [Sail dashboard](https://app.sailresearch.com/). Copy the corresponding new
block from `.env.example` into your existing `.env`, preserving existing keys and
database settings. Fill only the local key value:

| Provider  | Key variable        | Default model                                        |
| --------- | ------------------- | ---------------------------------------------------- |
| Fireworks | `FIREWORKS_API_KEY` | `accounts/fireworks/models/deepseek-v4p1-flash-1017` |
| Sail      | `SAIL_API_KEY`      | `zai-org/GLM-5.3`                                    |

The configured workspace default is **Sail / GLM-5.3** (`MODEL_PROVIDER=sail`).
Fireworks and the other providers remain explicit alternatives for development.
You can configure either or both. No model deployment, Sailbox, GPU allocation or
additional vendor SDK is required; these adapters use the existing HTTP client.
The fixed API endpoints are `https://api.fireworks.ai/inference/v1` and
`https://api.sailresearch.com/v1`. Keys are sent only to the matching provider.
See the official [Fireworks quickstart](https://docs.fireworks.ai/getting-started/quickstart)
and [Sail quickstart](https://docs.sailresearch.com/quickstart).

Restart the Python server after editing `.env`. Normal report generation uses the
configured provider/model. Model selection for experiments remains in PDF analysis
settings and **Reports → Developer report tools → Run AI matching**. Those advanced
controls expose the model, output limit and sending/usage consent.
Selecting a provider or browsing a saved result makes no model call; saved result
metadata continues to describe the provider/model that actually produced it.
`MODEL_PROVIDER=fireworks` or `MODEL_PROVIDER=sail` can set the configured default.

Both adapters request JSON Schema output and use one attempt, without automatic
retries. Account/model rate limits still apply despite available credit, and valid
JSON does not establish factual extraction or capability accuracy. Model IDs must
be available to your account. The retained Streamlit controls still offer Groq and
Gemini; use React/FastAPI for Fireworks and Sail.

The Sail baseline uses temperature **0**, reasoning **low**, a **12,288-token**
completion ceiling, and a **300-second total local HTTP deadline**. This bounds
local waiting; a provider may finish and bill an already accepted request after a
browser closes, a timeout, or server shutdown. Idle pages never call the model.
There is no automatic repair call, provider fallback, retry loop or inference job
queue. The default guided extraction makes two planned requests; the selectable
compact strategy makes one. Complex AI matching uses a preflighted
plan of complete-test batches, with at most eight requests. Sail/Fireworks use waves
of at most two concurrent requests; other providers remain sequential. Both in-flight
attempts are retained, no later wave starts after a known failure, and partial plans
are never accepted. Their native matching schema requires every source test/claim ID
and limits catalog, capability and linked lab-claim IDs to that test's candidates.
Runtime guards still check the selected capability's ownership, complete coverage and
scientific compatibility; schema validity alone cannot establish a correct match.
The explicitly selected advanced focused
extraction mode can also make up to eight requests.

The source input guard is 60,000 characters including source, prompt, guide and schema.
Matching has a separate 120,000-character request guard. These are software budgets,
not guarantees that arbitrary long PDFs fit. Oversized inputs fail before inference
without silent truncation. Focused extraction repeats the full source and is not
input chunking; scanned/handwritten files still need the separate OCR integration.

Malformed/truncated outputs, inconsistent fields, unknown IDs and incomplete
matching coverage fail validation. A failed analysis preserves the previous
accepted result and exposes a raw attempt download under the collapsed developer
details; retrying is a new explicit action. Concurrent AI operations for the same
document are blocked in this single-process pilot. Multiworker deployment needs a
shared database lease. Effective generation settings are saved with new model
artifacts. Developer model experiments do not reuse an incompatible generation
profile; normal report generation can reuse the current accepted comparison.
An existing local `SAIL_TIMEOUT_SECONDS=150` still overrides the new default; update
that local setting to `300` if the longer deadline is wanted. The application never
silently edits `.env`.
See the [inference policy](docs/architecture/inference_policy.md) for recovery and
shutdown behavior.

### MySQL in Docker

Open Docker Desktop yourself, then generate local database credentials once:

```bash
uv run python scripts/setup_database.py
docker compose --env-file .env.database up -d --wait
```

The script writes a private, ignored `.env.database`, preserving an existing file and
leaving `.env` unchanged. It starts no services. The app reads both env files. MySQL
listens on `127.0.0.1:3307` and stores data in the project's dedicated Docker volume.
Once the database is healthy:

```bash
npm --prefix frontend ci
npm --prefix frontend run build
uv run scione database-upgrade
uv run scione serve
```

Open **http://127.0.0.1:8000**. Build before starting the server so it registers frontend
routes. For frontend development, use `npm --prefix frontend run dev` in a second terminal;
port 5173 proxies `/api` to 8000. Stop the app with Ctrl+C. To stop/remove the database
container while retaining its data:

```bash
docker compose --env-file .env.database down
```

The database volume and downloaded image remain after container removal. Adding
`--volumes` permanently deletes the database: export needed data first. Clean only
project resources; a system-wide Docker prune is unnecessary.

Without `SCIONE_DATABASE_URL`, the archive uses `local_data/library.sqlite3`, a real
embedded SQL database requiring no service. This is the development/test default.
Changing the URL does not migrate existing data. A cloud MySQL migration needs data
migration and runtime validation, in addition to connection configuration.

## Workflow with the team's customer documents

The catalog loads automatically. For the development lab, initialize the packaged
source-derived declarations once, then maintain them through **My Lab**:

```bash
uv run python scripts/initialize_lab.py
```

The frozen development baseline has 70 exact-method services and 1,681 structured
fields across 26 independent service modules. It includes 22 explicit unavailable
condition/output declarations. Seven other methods referenced by the local corpus
are deliberately not offered. The team's original 57 CSV rows remain provenance;
service limits and reporting policies are independently authored synthetic data,
not customer acceptance values copied into a lab profile. Unknown pricing remains
unknown. Normal startup never initializes or overwrites a lab.

`tests/fixtures/corpus_lab_reference_v2.json` contains 70 source-cited, model-authored
reference assertions across the 22 readable teammate documents, with frozen lab and
PDF hashes. These are development references, not client-validated accuracy claims.
Scanned/handwritten documents remain reserved for the separate OCR workflow.

Controlled validation against this frozen lab demonstrated three fully matched
items and one partial item on the original `CSP-EN-301.pdf`, using Sail for matching.
The partial item includes an explicitly unavailable post-cycle gloss output.
This does not establish accuracy across the corpus: broad matching runs still
contain substantial source-normalization, method-role and missing-information
issues. The faster Fireworks inventory-guided extraction is an explicit experiment;
Sail remains the configured baseline. Review source evidence before relying on a
draft assessment. Model-authored reference assertions are not production accuracy.

1. Upload an original PDF under **Specifications** and assign its company, identifier
   and revision. Start with `docs/Generated Customer Spec/CSP-EN-301.pdf` or
   `CSP-EN-302.pdf` if the team's local source folder is available. The shared
   `docs/samples/CSP-EN-301_demo_spec.pdf` remains an older source document; the system
   accepts normal file uploads and does not pre-fill an extraction.
2. Analyze with a configured provider. Review Method, Conditions and Criteria against
   the PDF; the source icon locates evidence. Correct, remove or add source-backed tests
   when needed. Original model output remains immutable.
3. Choose **Generate report** to compare saved requirements with the current lab.
   Inspect the interactive Method/Condition/Criteria rows and expand detailed parameters.
   Missing information remains distinct from an explicit limitation. A full match
   means the lab can perform and assess the request, not that specimens will pass.
4. Resolve actionable gaps or remove/restore a test from the report scope. PDF and JSON
   exports are optional. Reloading a saved report does not call a model.
5. Future revisions can be saved under the same company's specification family. Library
   retains versions and offers version comparison; removal hides only the selected version.

The source PDFs under the team's generated folder remain local and may need to be
obtained separately. Packaged catalog/lab metadata does not depend on those local PDFs
at runtime. No model accuracy claim follows from a successful walkthrough.

## Storage and comparison

The default guided extraction uses a source-reading inventory followed by full-document
extraction; both requests and their original outputs are retained. The source-verified
guide is partial when a quotation cannot be located, and never replaces the source or
acts as a hard whitelist. One-call compact extraction remains selectable. The extraction
pass asks for atomic claims, preserving parameter identity, kind, stage, operator, value/range/tolerance,
unit, source fragment and page. Untouched provider responses stay available. Older runs use
a conservative local parser; unresolved clauses remain visible.

Legacy ready native PDFs saved in content-stream order can use a cached geometric
text view for current analysis and source review. Uploaded PDF bytes, original
`document.json`, saved runs and raw provider text stay unchanged. This is text-order
handling, not OCR or a guarantee that every PDF layout is read correctly. A uniquely
located physical summary can prove an omitted named colour scale on a derived view;
it never changes the raw model value or unit, and ambiguous/conflicting evidence stays unresolved.

AI handles extraction and, when explicitly requested, semantic matching of the supplied
catalog/lab candidates. Code validates candidate IDs, source grounding, units, ranges,
stages, contexts and reportable outputs. Accepted links must refer to supplied lab declarations;
numeric comparison uses preserved source facts. Browsing comparisons, expanding rows and
exporting make no model calls.
The model may also propose a wording equivalence between two recorded category values or
contexts. Such proposals retain both exact input strings and a reason; software rejects
known contradictions, missing evidence and changes to unfamiliar identifiers. Accepted
wording remains a fallible AI judgement, not independent verification. Numeric comparison
uses exact decimal quantities and does not ask the model for arithmetic or a final verdict.
One narrowly guarded cross-field proposal can account for the same literal context
stored inside the counterpart's text value; it cannot supply an absent qualifier,
merge lab declarations or override scientific differences. A request to measure/report
a property need not invent an acceptance threshold: code checks an explicitly declared
reportable output in the same context, without predicting whether the specimen passes.
One extracted reporting entry that combines total film and separate layer thickness
needs source correction into distinct measurements; a topcoat-only lab declaration
cannot certify both. Correctly separated measurement claims may share the same source quote.
Missing declarations
are unknown; explicit limitations are gaps. Workflow tasks stay visible separately from
capability evidence. Thermal-cycle steps and independent configurations remain separate.

One SQL database stores companies, specification families/versions, extraction records,
requirements, atomic claims, lab revisions and comparison snapshots. Fixed relationships
use relational columns; variable details use JSON. See [storage notes](docs/architecture/structured_archive.md).
A warning-policy upgrade creates a new report snapshot while preserving earlier PDF/JSON exports.
The [inference policy](docs/architecture/inference_policy.md) describes model settings,
validation, recovery and shutdown behavior. Synthetic evaluation protocols are documented
with the [development fixtures](tests/fixtures/README.md).

Original PDFs, extraction responses, source reviews and report-scope actions still use
files under `local_data/`. The primary React lab editor now uses SQL: an existing lab.json
imports once, remains unchanged for recovery, and is not a second live data source.
Semantic matching attempts store full candidate inputs, raw/validated output, token usage,
normalized extraction and deterministic comparison artifacts in SQL. Saved JSON and
first-rendered PDF bytes remain immutable for their comparison inputs.
Matching history sorts only small metadata/identifier columns and fetches full JSON
artifacts separately. This avoids MySQL sorting large request/response payloads and
requires no new schema migration or reset of saved data.
Developer diagnostics expose current-policy source-review proofs. New matching
artifacts capture those proofs separately with the attempt; replay diagnostics
do not rewrite or certify a historical model result.

The retained Streamlit compatibility interface still uses legacy file stores; use the
React/FastAPI application for the SQL lab and semantic matching workflow. These are local
milestones: authentication, tenant-wide document isolation, durable jobs and cloud storage
must be completed before exposing this branch to external users.

New customer revisions can be linked to an existing specification; earlier versions remain
viewable. Re-running a model does not create a new customer revision. Automatic semantic
version diffs and cross-customer comparison are not implemented. Identical PDF bytes reuse
one document and are not silently reassigned to another company.

## Tests and maintenance

```bash
uv run pytest
uv run ruff check src tests scripts
npm --prefix frontend run build
```

The semantic challenge evaluator is offline. Without saved runs it probes comparison
policy using manual reference alignments; it does not measure a model. With saved runs:

```bash
uv run python scripts/evaluate_semantic_matching.py \
  --run saved_batch_a.run.json saved_batch_b.run.json \
  --output local_data/experiments/semantic-replay
```

This is labelled `CURRENT_POLICY_RECOMPILE`: it applies current guards and comparison
rules to the recorded inputs/links, preserving source and policy hashes. It is not a
reconstruction of historical machine verdicts. For those, use
`scripts/evaluate_capability.py --artifacts` with the original exported
`comparison_validated` and independently aligned labels. Keep original run/score files
unchanged; protocols and synthetic-data limitations are in the
[fixture guide](tests/fixtures/README.md).

`npm --prefix frontend run test:e2e` starts disposable fixture servers itself; run it
explicitly when wanted. Fixtures do not use your `.env`, keys or local workspace. Set
`SCIONE_TEST_PYTHON` and `PLAYWRIGHT_CHROMIUM_EXECUTABLE` when needed. On macOS, test temporary
directories must use a canonical path without symlink traversal.

Embedded workspaces support **Developer Tools → Workspace maintenance → Reset**. With
the app stopped, use `uv run scione reset-demo --yes`. This moves saved lab/spec/report
files and the embedded database to private `.demo-backups/`. An explicitly configured
external/MySQL database blocks reset before changing data: recoverable reset across SQL
and files is not implemented. Use a separate database/workspace for a fresh MySQL demo.

Generated-corpus evaluation uses source-only references prepared before inspecting
model output. It separates extraction, matching with manually typed facts, and complete
workflow checks. The annotations are partial internal synthetic references, not client
validated gold or a production accuracy claim. See the [fixture guide](tests/fixtures/README.md).

The application bundles the complete synthetic method CSV as a lossless `.csv.gz`,
its provenance manifest, the runtime identity excerpt and the lab examples under
`src/scione/catalogs/data/`. It does not need the original CSV or Excel files at startup.
The small reviewed method-reference workbook in `docs/Generated Customer Spec/` is
retained for rebuilding that excerpt with `python -m scione.catalogs.build_bank`.
The duplicate raw CSV/Excel exports and the obsolete 50-method lab workbook remain
local and ignored. Decompressing the packaged GZ recovers the original CSV bytes.

Additional generated PDFs are optional development inputs, kept locally in
`docs/Generated Customer Spec/`; obtain them from the team's shared dataset when
evaluating that corpus. Normal startup and offline tests use the bundled demo PDFs
and fixtures. Only active prompt templates are packaged; retired committed templates
remain in Git history, and saved inference attempts retain their captured requests.

Credentials, database settings, runtime data, backups, dependencies, builds and test output
are ignored. Only approved synthetic demo PDFs are allowlisted; original client reference
and meeting PDFs remain local.

## Review workflow checks

Source confirmation and lab support review are separate decisions. In PDF review,
the selected test appears as a name/method/conditions/criteria table beside the source.
Click a page link to locate a field; when an exact field location cannot be proved,
the viewer identifies the item-level fallback. **Confirm**, **Edit** and **Remove**
apply to this extracted source item, not to whether the lab can perform it.
Use **Add missing test** to recover an omitted item with a literal PDF quotation;
the original model output remains available separately. Use **Keep pending & next**
when a source issue remains unresolved.
**Reports → Report options → Review test matching** shows customer requirements alongside lab declarations,
comparison reasons and scope checks. Pending items remain visible in draft reports.
If a real lab entry was suggested but its scope remains uncertain, the comparison shows
**Lab information to review** rather than treating it as a confirmed match or an absent record.
Per-test matched/gap/clarification counts show completed checks even when one missing
lab detail keeps the overall item unresolved. Loading bars show elapsed waiting time,
not a model confidence or estimated completion percentage. The disabled OCR entry
marks the future scanned-document adapter; it makes no API call.

The browser suite includes a hand-authored partial fixture from five test rows in the
MS-PNT-410 sample. It checks source review, lab method/scope gaps and draft exports;
it is UI test data, not model output or an extraction accuracy benchmark.

## Deployment and handoff

React/FastAPI can be deployed and attached to a domain. Production still needs authentication,
authorization, deployed host/origin settings, backups and operational configuration. Current
`scione serve` binds to loopback and is not a public hosting configuration. Use a client-owned
managed database and object storage for durable handoff. Credentials stay server-side; the
browser never receives a database URL. Personal student credits are not a hosting dependency.
