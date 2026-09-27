# scione-test-spec-intelligence
CMU MISM Capstone — AI Test Specification Intelligence for SciOne AI

## Current scope

The current vertical slice ingests digitally generated PDFs, runs provider-neutral
structured extraction, validates the result, evaluates it against an optional answer
key, and stores reproducible local run artifacts. OCR/multimodal ingestion, multi-file
or chunked strategies, and the review UI remain separate future pipeline stages.

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

The evaluator reports separate precision/recall/F1 values for document identity,
methods, exposures, parameters, classifications, references, and acceptance criteria.
It deliberately does not hide those dimensions behind one overall score.

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

## Run the synthetic sample with Groq

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

## Open the review workbench

The web interface loads saved runs without making another model call. A new request is
sent only after pressing **Run extraction** in the sidebar.

```bash
python -m streamlit run src/scione/web/app.py
```

The workbench can run the tracked synthetic PDF or an uploaded text-based PDF. It shows
the page text seen by the model, normalized methods and parameters, source evidence,
field-level evaluation differences, and downloadable validated JSON. Do not upload
documents that are not authorized for the configured external provider.

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
