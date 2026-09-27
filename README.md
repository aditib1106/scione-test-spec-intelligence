# scione-test-spec-intelligence
CMU MISM Capstone — AI Test Specification Intelligence for SciOne AI

## Current scope

The first vertical slice ingests digitally generated PDFs and produces a validated,
page-aware document representation. OCR, model inference, semantic extraction, and the
review UI will be added as separate pipeline stages.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

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

## Checks

```bash
pytest
ruff check .
```
