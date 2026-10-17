# SciOne Test Specification Intelligence

CMU Heinz Capstone — customer-specification review and lab-gap prototype.

## Scope and current status

The current journey is:

Customer specification → extract test requirements → review flagged items →
normalize test/method identities → compare with a lab's declared capabilities →
gap report.

Reading or interpreting the full text of referenced standards is not part of the
MVP. The catalog holds test identities, method identifiers and aliases, not a
library of standard procedures.

Implemented:

- **My Lab**: persisted lab profile, capabilities, operating limits, notes and
  optional prices with currency/pricing basis. The list opens first; Add/Edit opens
  a form and a successful save closes it.
- **Test Catalog**: a searchable pilot catalog of 15 test identities, 24 methods
  and curated English/Chinese aliases. Association does not imply method equivalence.
- **Specifications**: saved PDFs, local page previews/highlights, model extraction
  and separate, versioned human-review decisions.
- **Developer tools**: a collapsed sidebar entry with five diagnostic tabs:
  Overview, Requirements, Evidence & checks, Compare runs, and Raw & metadata.
  Browse saved runs of the same PDF without changing the lab-facing result or
  review decisions. Compare model/prompt metadata and output differences, inspect
  source highlights, and download expanded predictions or untouched provider text.
  Counts and local checks are not accuracy scores; neither run is ground truth.
- **Reports**: local test/alias and method-identifier matching against the lab's
  declarations, separate human matching/scope decisions, optional manual quote
  quantities, and immutable draft PDF/JSON snapshots with paged PDF previews.

Interactive AI chat, partner inventories, automatic quantity/lead-time estimates
and formal quote approval are not implemented.
There is no production database or authentication. Demo lab entries/catalog/prices
are synthetic declarations, not verified capability, accreditation or quotations.
A content confirmation does not approve a catalog match or lab support.

## Create a lab-gap report

1. Open **Reports** and choose an analyzed specification. Matching is local and
   does not call a model or interpret standard procedures.
2. Inspect **Capability review**: method listed in-house, method gap, test not
   listed, condition/scope gap, matching review needed, or excluded. A method
   match does not establish condition coverage, accreditation or test success.
   An absent catalog entry does not prove a lab cannot perform a test.
3. Select a requirement to compare its source-stated conditions, criteria and
   scope with the lab's declaration. **Adjust test matching / confirm lab scope**
   records a separate human decision, with an explanation. Resolve content flags
   in **Specifications**; matching confirmation does not clear content flags.
   **Needs review** includes unclear identities, source-content flags, and listed
   methods whose conditions/scope still need a lab reviewer. The table separates
   content review from lab scope review; relevant review controls open automatically.
4. Pricing is optional. A declared catalog rate remains a reference rate until
   lab support, applicable scope and quantity/pricing basis are explicitly
   confirmed. Quote subtotals include only manually selected eligible lines and
   stay separate by currency, without applying currency-specific rounding.
   Changes to lab declarations or prices require scope and quantity reconfirmation.
   Overlapping overview/detail records and regional
   alternatives must not be counted as independent chargeable tests.
5. **Generate PDF + JSON** saves a draft snapshot. **PDF preview** shows the exact
   downloadable PDF with page navigation. **Saved reports** retains history;
   changed inputs mark older snapshots as historical instead of rewriting them.
   **Developer** is the last tab for matching provenance and structured fields.

The PDF is a lab-facing business report: test requests, customer conditions and
acceptance criteria, lab methods/limits, gaps, pending decisions, source pages and
available pricing. It does not print model diagnostics, internal IDs, full source
quotations, hashes, or an audit appendix. Those remain in Developer / the full JSON
snapshot. Excluded items are summarized as outside scope, not priced as active tests.

JSON snapshots preserve the extraction run, original requirement fields/evidence,
content review, lab/catalog versions and matching/quantity decisions. Unknown partner
costs, sample preparation, missing rates and lead times are not fabricated.
All exports are drafts for engineer review, not approved quotations or capability
certifications. Local reports/working decisions live under ignored `local_data/`.
Existing PDF snapshots stay immutable: generate a new report after an application
layout update rather than expecting an old saved PDF to change.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Set a local provider key in `.env`; never commit that file. If it already exists,
merge any new example settings manually instead of overwriting your credentials.

```bash
python -m streamlit run src/scione/web/app.py
```

No model request is made by opening a page, adding a lab capability, saving a PDF,
browsing sources, reviewing records or inspecting diagnostics.

## Walk through a specification

1. Open **Specifications**, use **Add PDF**, and save an authorized text-based PDF.
   The owner-supplied MS-PNT-410 sample can be imported explicitly if it exists at
   the expected repository-root path. Private local samples are not required by tests.
2. Choose **Analyze PDF** / **Analyze again**. Select the provider and grant sending
   permission. Gemini also requires an explicit Free-tier confirmation. Model IDs,
   output ceilings and reading modes are under **Advanced model settings**.
3. **Needs your review** contains automatically flagged records. Select an entry
   to open its details and jump to highlighted source text. **Relocate** returns
   to its citation; a source-page selector is shown for multiple cited pages.
4. **Confirm** or **Exclude this item** records a decision separately from model
   output, removes it from the pending list and opens the next pending item.
   Notes record corrections to resolve; they do not silently rewrite extracted fields.
5. **All extracted tests** also includes unflagged, confirmed and excluded records.
   Unflagged items do not proactively request confirmation. **Review this item**
   allows an optional decision; **Back to list** returns to the pending overview.

Triage uses source-location checks, missing fields, stated ambiguity and human
decisions, not a made-up model-confidence percentage. A uniquely located quotation
is not proof that its interpretation is correct. Partial/ambiguous/unmatched
citations remain flagged. Located typography/table-format differences alone need
not force confirmation; original output and warnings remain unchanged.

Reference-coverage warnings are separate from item review. An absent method code
may indicate a missing test, preparation or background reference; it is not a test
count, recall score or proof of completeness. Confirming all displayed items does
not resolve possible omissions. A global spec's regional and conditional cases
must not be assumed to apply simultaneously.

## Extraction and provider limits

The provider-neutral runner supports:

- **Compact extraction** (default): one full-context request with a smaller wire
  schema, deterministically expanded into the review schema. Raw provider JSON
  intentionally differs from expanded prediction JSON.
- **Detailed extraction**: one full-context request with the richer schema.
- **Detailed coverage passes**: one request per three physical pages, retaining
  the full source in each request. The planned number is shown before execution,
  with at most eight requests. Only exact-content duplicates are removed;
  similarly named scoped cases are retained for review.

Coverage passes are not input chunking or RAG and do not reduce input-token use.
OCR, multimodal ingestion and multi-document workflows are not implemented.

`SCIONE_CUSTOMER_INPUT_CHARACTER_LIMIT` defaults to 60000 for source, prompt and
schema combined. It is a character guard, not a token or cost guarantee.
`SCIONE_CUSTOMER_GEMINI_OUTPUT_TOKENS` defaults to 16000; this is a ceiling, not an
account quota or a spending cap. The app cannot inspect provider billing. Only
send authorized, non-sensitive samples to a suitable account/project.

Customer extraction disables automatic retries. Truncated generation, invalid
JSON/schema and empty aggregate results are rejected. Every planned call must
succeed before a new result is saved. A failure preserves the previous result and
review, with a distinct failure record and a visible retained-result notice.

Groq diagnostics distinguish output-per-minute from input/total-token-per-minute
limits. Lowering the output ceiling may satisfy a quota request but truncate JSON;
it is not a completeness fix. Increasing it cannot bypass account quotas.

To add another provider, implement `ModelProvider` and register it in
[src/scione/providers/factory.py](src/scione/providers/factory.py). Groq/Gemini
model IDs remain configurable. No real API calls are required by the test suite.

## Data and repository layout

```text
src/scione/
  catalogs/        Shared test/method aliases and local lab storage
  ingestion/       Page-aware PDF text
  providers/       Model adapters, factory and offline fixture provider
  prompts/         Versioned customer-spec prompts
  schemas/         Shared document contracts
  specifications/  Extraction, source location, review and local artifacts
  web/             Lab workspace and current diagnostics
tests/             Offline unit and integration tests
local_data/        Private runtime data; ignored by Git
```

`SCIONE_WORKSPACE_DIR` overrides the local data root. Lab entries live in
`local_data/lab.json`. Each saved specification has its original PDF, page text,
immutable model runs and separate human-review records under
`local_data/specifications/<document-id>/`. Failed-attempt metadata is separate.
For coverage passes, individual untouched responses are in `run.calls`; the
top-level `raw_response` is explicitly a local manifest.

PDF intake is limited to 25 MB / 200 pages. Encrypted PDFs are rejected. Sparse or
image-only PDFs may be previewed but are not accepted for text extraction without
OCR. Corrupt storage is reported, not reset; stale edits cannot overwrite newer
reviews or lab data. Never delete `local_data` just to clear development caches.

The former Moon Glass standard-extraction benchmark, scoring pipeline and
root-level `runs/` store have been retired. Their scores are not evidence for this
scope. Historical tracked files remain recoverable from Git. Current synthetic
test fixtures are generated inside the tests and are not model-quality benchmarks.

## Local checks

Inspect a permitted text PDF without sending it to a model:

```bash
python -m scione ingest "/path/to/customer-spec.pdf"
```

Use `--reading-order geometric` to inspect coordinate ordering when appropriate;
PDF text order depends on document layout.

```bash
pytest
ruff check .
```

For checks that do not recreate repository caches:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider
ruff check --no-cache .
```

`.pytest_cache`, `.ruff_cache` and `__pycache__` are disposable and ignored.
`.venv` and `src/*.egg-info` are environment/editable-install metadata, not project
data; keep them while using the installed development environment.
