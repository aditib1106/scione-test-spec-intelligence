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

- **My Lab**: starts empty for a first-use walkthrough; persisted lab profile,
  capabilities, structured operating limits, notes and
  optional prices with currency/pricing basis. The list opens first; Add/Edit opens
  a form and a successful save closes it.
- **Test Catalog**: a searchable synthetic pilot catalog of 38 test identities, 36 methods
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
  quantities, automatic structured operating comparisons, and immutable draft
  PDF/JSON snapshots with paged PDF previews. The default queue shows exceptions only.

Interactive AI chat, partner inventories, automatic quantity/lead-time estimates
and formal quote approval are not implemented.
There is no production database or authentication. Demo lab entries/catalog/prices
are synthetic declarations, not verified capability, accreditation or quotations.
A content confirmation does not approve a catalog match or lab support.

## Automatic decisions and their limits

- The expanded catalog is explicitly curated demonstration metadata, not a universal
  semantic matcher or an authoritative standards library. Source-defined procedures
  and chemical-media families retain their original conditions and source context;
  a material name alone is not a global test alias.
- Clear test/method identities are resolved locally. Unknown editions, qualified
  references, conflicting identities and ambiguous method alternatives remain review
  items. No extra LLM call is made for matching or comparison.
- Complete, independently located customer-procedure rows do not need a formal
  standard number. Missing/partial quotations or omitted standard references still
  request source review; original model output is never rewritten.
- Explicit structured lab limits support conservative numeric/unit comparison.
  Covered operating requirements need no duplicate scope confirmation; an explicit
  limit violation becomes a gap. Unparsed clauses, unclear scope and contradictory
  free-text/structured declarations remain unknown. Acceptance criteria are retained
  for measuring the material, never treated as a promise that the material will pass.
- A test absent from the lab catalog becomes a declared-availability gap, not proof
  of physical impossibility. Once its test identity is established, missing lab
  availability is reportable even if method details remain unresolved; those details
  are preserved, not treated as equivalent. No quantities, partner availability or final quote
  approval are fabricated. Pricing still needs an explicit quantity/basis and valid
  source/matching/operating support.

The current saved 49-record sample can be reprocessed offline to evaluate workflow
coverage. Reduced review counts are not an extraction-accuracy score or a guarantee
for other specifications; ground-truth evaluation is still needed.

## Create a lab-gap report

1. In **Specifications**, check the extracted text against the original PDF.
   **Confirm & next** / **Exclude & next** advance in source order; details scroll
   above a stable action row. **Batch review** requires explicit selection and
   acknowledgment; exclusion also requires a reason. One batch is saved atomically.
   **Continue to lab review** opens the same document in **Reports**. Confirming
   source text does not approve the matched test or the lab's capability.
2. **Review & confirm** opens a pending test automatically. Compare **Customer
   asks for** with **Your lab lists**, check conditions, criteria and scope, then
   **Confirm lab support**, or record why the lab **Cannot meet this scope**.
   A method match alone is not condition coverage, accreditation or test success.
   Missing catalog entries remain gaps, not proof a lab cannot perform a test.
   Already covered operating conditions are labeled as automatic matches, separately
   from human approval. Explicit operating-limit gaps cannot be clicked into support.
   Catalog gaps and automatic operating-limit gaps are already reportable: they show
   their result without a pair of disabled confirmation buttons. Genuine matching
   ambiguity opens the matching form directly. Source-text checks remain separate.
3. Use **Change test or method / record a gap** when the identity or method needs
   correction. Decisions remain separate from extracted source text; **Check
   extracted text** returns to the corresponding highlighted item in Specifications.
   The action row stays in place while details scroll. **Next pending item after
   saving** controls continuous lab review. **Batch lab review** applies only to
   explicitly selected, resolved test/method matches, never unknown identities.
   Source checks, catalog matching and lab scope checks are counted separately;
   they overlap and are not a model error rate.
   Ordinary matching saves need no typed note. Removing a source method or recording
   a lab limitation requires an explanation; missing fields get plain inline guidance.
4. Pricing is optional. A declared catalog rate remains a reference rate until
   lab support, applicable scope and quantity/pricing basis are explicitly
   confirmed. Quote subtotals include only manually selected eligible lines and
   stay separate by currency, without applying currency-specific rounding.
   Changes to lab declarations or prices require scope and quantity reconfirmation.
   Overlapping overview/detail records and regional
   alternatives must not be counted as independent chargeable tests.
5. **Continue to report** opens **Report**. **Create draft report** saves the PDF
   and its JSON snapshot locally, without calling a model. Pending reviews remain
   marked in the draft. Preview, download or choose an earlier saved PDF here.
   Changed inputs mark older snapshots as historical instead of rewriting them.
   **Developer**, the last tab, holds the diagnostic matrix, technical provenance,
   structured fields, history details and the saved report's JSON download.

The PDF is a lab-facing business report: test requests, customer conditions and
acceptance criteria, lab methods/limits, gaps, pending decisions, source pages and
available pricing. It does not print model diagnostics, internal IDs, full source
quotations, hashes, or an audit appendix. Those remain in Developer / the full JSON
snapshot. Excluded items are summarized as outside scope, not priced as active tests.
The front overview surfaces listed methods, separate source/lab review states,
reference rates and supplied quantities/estimates before unresolved details.
Missing prices or quantities are explicitly unpriced, never treated as zero.

Source quotations reconstructed from tables can pass location checks only when
complete cells match one unique source row. Wrong values, pages, cross-row snippets
and unresolved ambiguity remain flagged. A source-location match is not a correctness
score or human confirmation. Review-policy changes invalidate cached assessments
and mark earlier reports as historical; original model output is not rewritten.

JSON snapshots preserve the extraction run, original requirement fields/evidence,
content review, lab/catalog versions and matching/quantity decisions. Unknown partner
costs, sample preparation, missing rates and lead times are not fabricated.
All exports are drafts for engineer review, not approved quotations or capability
certifications. Local reports/working decisions live under ignored `local_data/`.
Existing PDF snapshots stay immutable: generate a new report after an application
layout update rather than expecting an old saved PDF to change.

## Fresh-demo walkthrough and recoverable reset

1. Start in **My Lab**. Enter a capability manually, or explicitly choose **Load
   example lab** to use invented sample capabilities/prices. Nothing is preloaded
   into a new lab automatically; the shared reference catalog remains available.
2. Upload/import a specification, run extraction, inspect the exception queue,
   then create a draft report. The analysis button is the only step that calls a model.
3. To repeat from first use, open **Developer Tools → Reset demo workspace**,
   acknowledge the reset and click **Reset to first use**. The page clears its
   memory/session cache as well as moving saved demo state into recovery storage.

With the web app stopped, the equivalent terminal command is:

```bash
scione reset-demo --yes
```

This moves only `local_data/lab.json`, `local_data/specifications/` and
`local_data/reports/` into an ignored private `.demo-backups/<timestamp-id>/`
directory with a recovery manifest. `.env`, credentials, the virtual environment,
original source PDFs and catalog/code assets are preserved. After a terminal reset,
restart the web app and refresh the browser to clear in-memory state.
Backups may contain customer documents; keep them private and do not commit them.

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
