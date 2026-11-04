# SciOne Test Specification Intelligence

CMU Heinz Capstone: a lab-facing specification archive and capability-gap prototype.
React + TypeScript + MUI is the primary interface. FastAPI owns extraction, validation,
comparison, persistence and PDF generation. Streamlit remains a compatibility tool.

## Current scope

One lab maintains its capabilities, saves customer specifications by company, extracts
requirements, checks source evidence, compares with its declarations and exports a draft
report. Reading the full content of referenced standards is outside the MVP.

- **My Lab**: manually enter tests, methods, conditions, reportable outputs and optional
  rates. New workspaces start empty. Explicit example loading offers CSP-EN-301 (four
  declarations) or the earlier MS-PNT-410 pilot lab; it never replaces existing entries.
- **Test Catalog**: 39 synthetic test identities and 40 methods, with English/Chinese
  aliases. Different method identifiers remain distinct; association is not equivalence.
- **Specifications → Library**: company/search filters, specification metadata, revisions
  and saved requirements. Open a specification, then select a test to browse parameter,
  value, unit, stage and source-page records in a filterable table.
- **Specifications → PDF & review**: PDF previews, highlighted evidence, model analysis and
  separate source-review decisions. PDF pages support zoom, fit width and fullscreen
  reading with pagination. Source confirmation does not approve lab support.
- **Reports**: overview and detailed customer-versus-lab checks, reasons, relevant lab-edit
  links, PDF previews and PDF/JSON downloads. Recompare after editing lab information.
- **Developer Tools**: original provider output, run history and diagnostics. **Reports →
  Advanced review** retains the earlier mapping/scope and manual quantity/price workflow.
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
Reviewing records, comparing saved data and exporting reports do not call a model.

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

## CSP-EN-301 walkthrough

1. **My Lab**: manually enter capabilities, or select the CSP-EN-301 example in an empty lab.
2. **Specifications → Library**: add a PDF with its company and specification details.
   The authorized synthetic PDF is bundled at `docs/samples/CSP-EN-301_demo_spec.pdf`.
3. Open **PDF & review**, select a configured provider/model and analyze after confirming
   sending permission and provider billing tier. This is the model-call step.
4. Return to Library. Inspect saved tests and their parameters, units, stages and source
   links. Reload the page to verify persistence.
5. Open **Reports**. The supplied lab explicitly cannot report post-cycle gloss retention.
   Unrecorded D65/master and separate topcoat-measurement information remain unresolved.
6. Update the relevant capability if you can genuinely supply missing information, return
   to Reports and recompare without re-extracting the PDF. Download the PDF and JSON.

For a walkthrough without provider calls, **Try worked example** imports the same PDF
and an explicitly human-prepared reference extraction. Relevant views label it as prepared
example data. It is not an LLM result, accuracy benchmark or simulated live call. This
button never replaces a saved model extraction. Run analysis to exercise a real provider.

The team's manually written example report is a design reference. Some proposed Exact
statuses assume information absent from the lab sample, so conservative counts can differ.
No prices are fabricated for this example.

## Storage and comparison

The compact extraction prompt asks the model for atomic claims in the same request as
extraction. Claims preserve parameter identity, kind, stage, operator, value/range/tolerance,
unit, source fragment and page. Untouched provider responses stay available. Older runs use
a conservative local parser; unresolved clauses remain visible.

AI handles natural-language extraction and semantic interpretation. Code validates source
and numeric grounding, then compares explicit identities, units, ranges, stages and
reportable outputs. Matching, comparison and export make no model calls. Missing declarations
are unknown; explicit limitations are gaps. Workflow tasks stay visible separately from
capability evidence. Thermal-cycle steps and independent configurations remain separate.

One SQL database stores companies, specification families/versions, extraction records,
requirements, atomic claims, lab revisions and comparison snapshots. Fixed relationships
use relational columns; variable details use JSON. See [storage notes](docs/architecture/structured_archive.md).

Original PDFs, untouched provider output, source reviews and the retained lab editor still
use versioned files under `local_data/`. This is deliberate hybrid storage. Lab revisions
used in assessments are also saved to SQL. Saved JSON and first-rendered PDF bytes remain
immutable for their comparison inputs.

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

`npm --prefix frontend run test:e2e` starts disposable fixture servers itself; run it
explicitly when wanted. Fixtures do not use your `.env`, keys or local workspace. Set
`SCIONE_TEST_PYTHON` and `PLAYWRIGHT_CHROMIUM_EXECUTABLE` when needed. On macOS, test temporary
directories must use a canonical path without symlink traversal.

Embedded workspaces support **Developer Tools → Workspace maintenance → Reset**. With
the app stopped, use `uv run scione reset-demo --yes`. This moves saved lab/spec/report
files and the embedded database to private `.demo-backups/`. An explicitly configured
external/MySQL database blocks reset before changing data: recoverable reset across SQL
and files is not implemented. Use a separate database/workspace for a fresh MySQL demo.

Credentials, database settings, runtime data, backups, dependencies, builds and test output
are ignored. Only approved synthetic demo PDFs are allowlisted; original client reference
and meeting PDFs remain local.

## Deployment and handoff

React/FastAPI can be deployed and attached to a domain. Production still needs authentication,
authorization, deployed host/origin settings, backups and operational configuration. Current
`scione serve` binds to loopback and is not a public hosting configuration. Use a client-owned
managed database and object storage for durable handoff. Credentials stay server-side; the
browser never receives a database URL. Personal student credits are not a hosting dependency.
