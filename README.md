# scione-test-spec-intelligence
CMU MISM Capstone — AI Test Specification Intelligence for SciOne AI

## Current scope and implementation status

The revised product direction is customer-specification extraction, shared test/method
identity normalization, human review, and comparison against a lab's declared
capabilities to produce a gap report. Reading referenced standard procedures is not a
dependency of this workflow.

The first implementation step provides:

- **My Lab**: a local demo-lab profile, saved capabilities, and add/edit controls for
  test identities, declared methods, operating limits, notes, and optional pricing.
- **Test Catalog**: a searchable pilot catalog with 15 test identities, 24 method
  identities, and curated English/Chinese aliases. Associated methods are not declared
  equivalent, and unknown editions remain unspecified.
- **Developer Tools**: the existing PDF extraction, model selection, saved-run review,
  and provisional-answer-key evaluation remain directly accessible in the sidebar.

**Specifications** and **Reports** are clearly marked placeholders. Customer-spec
extraction/review, normalization decisions, capability matching, and PDF gap reports
are not implemented yet. Alias search in Test Catalog is browsing, not an implemented
semantic-normalization pipeline. There is no login or production database.

The pilot catalog and initial lab declarations/prices are demo fixtures, not verified
client or laboratory data. The catalog ships in `src/scione/catalogs/seed.py`. On first
opening My Lab, the app creates `local_data/lab.json`; changes persist across restarts.
`SCIONE_WORKSPACE_DIR` can override that directory without database setup. Local data
is ignored by Git. Prices include a currency and pricing basis and are not quotations.
Invalid or corrupt lab data is reported rather than silently reset; stale edits are
rejected until the editor is reloaded.

The legacy extraction path supports digitally generated PDFs and preserves source
pages, structured output, raw responses, and run metadata. OCR/multimodal ingestion
and multi-file or chunked strategies remain future stages. Legacy Moon Glass scores
are not evidence for the revised lab-gap workflow.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Create local configuration before using a hosted model provider:

```bash
cp .env.example .env
```

Then replace the placeholder API key in `.env`. The real `.env` file is ignored by
Git and must never be committed.

## Inspect a text PDF

```bash
python -m scione ingest \
  "docs/samples/Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf"
```

The JSON output preserves page boundaries and reports whether the document appears to
need a future OCR or multimodal ingestion adapter. Text defaults to PDF content-stream
order because it preserves the columns in the current synthetic sample; the adapter also
supports geometric ordering for later comparison across document layouts.

Use `--reading-order geometric` to compare PyMuPDF's coordinate-based ordering
when a document's internal content-stream order is not usable.

## Evaluate structured output

The tracked Moon Glass answer key is a provisional infrastructure fixture. It must be
reviewed by the Data team before its scores are presented as benchmark results.

```bash
python -m scione evaluate \
  benchmark/cases/moon_glass_standard/ground_truth.json \
  benchmark/cases/moon_glass_standard/ground_truth.json
```

The evaluator reports separate precision/recall/F1 values for document identity, test
items, methods, exposures, conditions, parameters, classifications, references, and
acceptance criteria. It normalizes non-semantic formatting differences, reports model
extras separately from missed truth values, and verifies that cited evidence occurs on
the claimed physical PDF page. A mean F1 is displayed only as a diagnostic summary;
the field-level results remain the source of truth.

## Test the extraction pipeline without an API

The static provider exercises prompt construction, provider orchestration, schema
validation, and run metadata without making a network request:

```bash
python -m scione extract-method-static \
  "docs/samples/Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf" \
  benchmark/cases/moon_glass_standard/ground_truth.json
```

This adapter is only a deterministic pipeline fixture. It is not an extraction model and
its output must not be reported as model accuracy.

## Run the synthetic sample with a hosted provider

Only use documents you are authorized to send to an external provider. The tracked Moon
Glass document is fully synthetic and safe for this prototype call.

```bash
python -m scione extract-method-groq \
  "docs/samples/Fictional_Test_Method_Moon_Glass_ASTM_D3359_Style.pdf" \
  --ground-truth benchmark/cases/moon_glass_standard/ground_truth.json
```

The command prints a small summary and saves the complete, reproducible artifacts under
the ignored `runs/<run-id>/` directory. API keys are loaded from `.env` and are never
written into run artifacts. The default configuration uses Groq's
`qwen/qwen3.8-27b` in strict JSON Schema mode; provider and model choices remain
environment configuration rather than extraction-pipeline code.

The web workbench also supports Google Gemini through the official `google-genai` SDK.
The default is `gemini-3.5-flash-lite`, which completed the full synthetic structured
extraction more reliably than `gemini-3.8-flash` during Free-tier testing. The model ID
remains configurable. Gemini uses the same extracted text, prompt, JSON Schema,
evaluator, and artifact store as Groq so model results remain comparable. Keep the
Google project on the Free tier for no-cost synthetic experiments. The application
cannot inspect or change the project's billing status. Free-tier Gemini data may be
used by Google to improve its products, so do not send client, licensed, or confidential
documents.

## Open the lab workspace and developer tools

```bash
python -m streamlit run src/scione/web/app.py
```

Start in **My Lab**, save a capability, and reopen the app to check persistence. In
**Test Catalog**, try `铅笔硬度` or `ISO 15184`. **Developer Tools** is a visible sidebar
entry, not a hidden or collapsed mode. Its legacy extraction controls are independent
of the new lab configuration.

The web interface loads saved runs without making another model call. A new request is
sent only after pressing **Run extraction** in Developer Tools. Lab/catalog page
navigation, editing and saving do not make model requests.

The developer workbench can run the tracked synthetic PDF or an uploaded text-based
PDF. It shows the page text seen by the model, normalized methods and parameters, source evidence,
field-level scores, and an explicit ground-truth-versus-model table for every dimension.
New evaluated runs persist a ground-truth snapshot so later reviews remain reproducible.
Saved-run comparisons and downloadable validated JSON remain available. Provider and
model settings in the sidebar apply only to the next run; the reviewed run's actual
provider and model appear above the results. Comparisons read existing run artifacts and
never make additional model calls. Do not upload documents that are not authorized for
the configured external provider.

## Add another model provider

The extraction runner depends only on the `ModelProvider` contract. To add another
vendor or a local model, implement that contract and register the adapter in
`create_configured_provider()` in `src/scione/workbench.py`. Prompt construction,
schema validation, evaluation, CLI behavior, run storage, and the review UI remain
unchanged. Models available through Groq can already be changed with `GROQ_MODEL`
without adding an adapter.

## Checks

```bash
pytest
ruff check .
```
