# Capability comparison development cases

`capability_gold_v1.json` is a versioned set of 29 hand-authored synthetic
development scenarios, containing 30 parameter decisions and 3 overall gate
decisions. Despite the historical filename, these are **not independently
validated gold**. Their labels were written from the stated scenario, not copied
from comparator predictions. Domain review is still needed.

The cases isolate the comparator by supplying typed facts. They exercise numeric
range coverage, missing evidence, explicit unavailability, parameter aliases,
known spelling variants, language equivalents, unit conversion, context/stage,
sequence, configuration, reportability and a supplied method gate. The challenge
split is author-designed; it is not blinded or statistically independent.

`capability_adversarial_v1.json` adds 20 post-baseline scenarios for source
subjects, missing units/semantics, affine tolerances, resolution conversion,
conflicting declarations, route selection, discrete settings and unresolved
source interpretations. These new cases have no historical baseline. Keep their
denominator separate from the original before/after comparison:

```sh
python scripts/evaluate_capability.py --cases tests/fixtures/capability_adversarial_v1.json
```

One material case deliberately remains unknown: the raw source says "steel only"
but the supplied typed claim lacks an explicit closed-set field. The comparator
must not manufacture that structured fact. A future normalization/schema change
can address it in a new fixture version.

Run from the repository root, using the existing virtual environment:

```sh
python scripts/evaluate_capability.py
```

Results go to ignored `local_data/experiments/` as a reproducible decision
snapshot plus JSON and Markdown summaries. No API keys, `.env`, app settings,
providers, database or server are loaded. Provider imports and network connection
attempts are blocked while running the comparator.

Before changing comparison policy, capture the real baseline:

```sh
python scripts/evaluate_capability.py --capture-baseline-only --strategy before_change --output local_data/experiments/before_change
```

After the change, compare the same fixture bytes:

```sh
python scripts/evaluate_capability.py --baseline-snapshot local_data/experiments/before_change/baseline_snapshot.json --output local_data/experiments/after_change
```

Do not label a snapshot captured after a code change as the old baseline. Dataset
hashes must match; changed labels/inputs require a new dataset version and a
valid corresponding baseline. The JSON report retains comparator and policy
source hashes, version, timestamps, source scenarios, labels, reasons, errors,
confusion matrix and results by family and split.

## Metrics

Accuracy includes supported, gap and unknown labels. Automation coverage is
predicted supported or gap divided by all labelled parameter decisions. A system
that defers all decisions therefore has zero automation coverage, even if it
correctly identifies missing information.

False support count means a predicted supported decision whose expected label
is gap, unknown or informational. It is a conservative evidence check; an
unknown label does not prove physical incapability. The rate denominator is
**predicted supported decisions**, not the size of the dataset. A zero denominator
is `null`/n/a rather than a misleading 0%. Missing outputs and runtime errors stay
in the denominator as errors, rather than becoming legitimate unknown decisions.

Method gate and overall results are separate from parameter accuracy. In the
bundled method cases, the method status is supplied as an input; the score does
not evaluate actual method identity matching or alternative-method approval.

## Replay independently labelled matching outputs

The evaluator can replay an actual API export, without running AI. Download
`GET /api/specifications/{document_id}/matching/{run_id}/artifacts` using the
Developer Tools export. Keep the original JSON unchanged. With independently
prepared labels:

```sh
python scripts/evaluate_capability.py --cases independently_reviewed_cases.json --artifacts scione-matching-artifacts.json --output local_data/experiments/replay
```

This adapter reads `comparison_validated` only. It does not recompile proposals,
re-run normalization, trust `raw_response`, or substitute report approvals as
machine decisions. The artifact hash and extraction/matching run IDs,
model/provider, prompt/validation version, source/catalog/capability/review
revisions are retained when present. The exported matching inputs can already
include human corrections to extraction: check `human_corrections_included` and
`review_revision`. Such a run measures assisted matching, not unassisted
end-to-end AI performance.

In the label file, `case.id` defaults to the exported
`comparison_validated[].requirement_id`. Each `expected_checks` key defaults to
that comparison's `checks[].id` (the requirement claim ID). Label the source
documents and lab declarations independently; use IDs only to align the
annotation with the saved run. Do not copy the system's status as the label.

If the reviewer uses stable annotation IDs instead of generated system IDs,
provide an explicit mapping, for example this single case:

```json
{
  "id": "water_jet_temperature",
  "family": "execution_conditions",
  "split": "independent_review",
  "requirement_statement": "Water must be 60 °C.",
  "lab_statement": "Water temperature is selectable from 20 to 50 °C.",
  "rationale": "The specified water temperature exceeds the declared maximum.",
  "expected_checks": {"water_temperature": "gap"},
  "expected_overall": "partially_matched",
  "artifact_requirement_id": "req_actual_exported_id",
  "artifact_claim_ids": {"water_temperature": "claim_actual_exported_id"}
}
```

Use that object inside the dataset's `cases` list with the same top-level schema
as the bundled fixture. `normalized` and `lab_claims` are unnecessary for
replay-only datasets. The example assumes the saved requirement has one labelled
parameter plus its method check. For a real item with several conditions and
criteria, label all its exported parameter checks and include all exported test
items. Extra unlabelled requirements/checks, duplicate IDs and many-to-one
mappings are rejected. Missing labelled outputs and failed runs count as errors
in the original denominator. The method check may be labelled explicitly, or
left to a separately labelled `expected_overall` assertion.

For other pre-existing formats, a small adapter can create the saved-output
snapshot below. The evaluator also accepts this directly:

```sh
python scripts/evaluate_capability.py --cases independently_reviewed_cases.json --predictions saved_matching_outputs.json --output local_data/experiments/replay
```

Use the same dataset schema as the fixture, with labels and rationale prepared
by a separate reviewer from the original source documents. Set
`annotation_status` to `independently_reviewed` only after that review. Typed
`normalized` facts can be omitted in replay-only datasets.

A saved-output snapshot has this minimal protocol:

```json
{
  "schema_version": "1.0",
  "strategy": "saved_application_run",
  "input_kind": "saved_output_replay",
  "captured_at": "2026-11-09T00:00:00Z",
  "dataset_id": "independent_matching_cases_v1",
  "dataset_sha256": "SHA256_OF_THE_EXACT_LABEL_FILE_BYTES",
  "metadata": {
    "artifact_sha256": "HASH_OF_THE_ORIGINAL_SAVED_RUN",
    "extraction_run_id": "actual_run_id",
    "matching_run_id": "actual_run_id"
  },
  "predictions": [
    {
      "case_id": "reviewer_aligned_case_id",
      "checks": {"reviewer_aligned_claim_id": "supported"},
      "overall": "fully_matched",
      "reasons": {"reviewer_aligned_claim_id": "The actual saved decision reason."}
    }
  ]
}
```

Alternatively, each prediction can contain `case_id` and the actual saved
`StructuredComparison` as `comparison`. Stable case/claim IDs must be aligned
explicitly; this runner does not use an LLM or fuzzy matching to invent alignment
with the gold. Missing cases/claims count as errors, duplicate IDs and unknown
case IDs are rejected. Keep original run artifacts unchanged. Include model,
prompt, policy and document version identifiers in metadata, never credentials.

## Replaying the historical development baseline

`capability_legacy_baseline_v1.json` is the synthetic output captured before the
typed parameter engine integration, not a prediction generated by a live model.
It records the exact fixture hash, comparator hash and a transparent label
correction made before integration. It cannot reconstruct missing historical
transitive dependencies; the saved decisions themselves are the replay input.

```bash
python scripts/evaluate_capability.py \
  --baseline-snapshot tests/fixtures/capability_legacy_baseline_v1.json \
  --output local_data/experiments/before-after
```

## Keep extraction and comparison evaluation separate

The supplied-facts mode measures comparison policy after extraction. Saved
matching-output replay measures the decisions in that saved run, including any
upstream mistakes reflected in those aligned decisions. It is not an extraction
precision/recall implementation.

The next independent evaluation dataset should separately label test-item
presence, method, condition/criterion fields, number/operator/unit, source page,
context and sequence from actual PDFs. Evaluate extraction recall and field
correctness separately, then run comparison once with those reviewed facts and
once with the real extracted facts. That isolates extraction errors from
matching errors; do not combine these denominators into a single accuracy claim.

## Frozen source-text challenge for a bounded live experiment

`live_capability_challenge_v1.json` contains four new synthetic tests and 20
explicit source facts. Its SHA256 is
`cc70563c579364d3a9ee18987a9272f52d7aaf895446287006e8cf2151d9152d`.
The cases, allowed parameter aliases and expected capability statuses were
written and frozen before live challenge responses. They remain author-designed
cases, not independently validated or statistically blinded gold.

The challenge covers bilingual text/a spelling error, ordered compound thermal
stages, a novel holder-force correspondence, genuinely missing fictional
material/accessory declarations, and an explicit temperature range gap alongside
reportable acceptance criteria. No standard content is read or inferred. Inputs
start from two supplied text pages, so these scores do not measure PDF or OCR
quality.

`scripts/live_capability_experiment.py` prepares/replays the experiment and never
loads keys, calls a provider, retries, starts servers or uses a database. The
separate experiment driver owns the approved global call budget and artifacts;
SDK retries must also be disabled. Suggested comparisons reuse the same frozen
input: actual extraction versus manual oracle facts, each followed by code-only
matching and accepted AI correspondences plus deterministic comparison.

The helper exposes `build_challenge()`, `evaluate_extraction(run)`,
`evaluate_code_only(run, normalized_requirements=...)` and
`evaluate_matching(attempt)`. Pass `bundle['oracle_normalized']` explicitly when
building oracle matching input; otherwise the application normalizer is also
being evaluated. The oracle is labelled `manual_oracle`, never an AI result.

Strict extraction scores require the correct property, role, stage, sequence,
operator, source quantity/unit, context and physical page. Raw model atoms and
effective normalized facts are reported separately. Retained unresolved source
annotations are counted separately from asserted typed facts. Correct novel
wording outside the frozen aliases can need human adjudication; do not silently
revise the labels after observing model responses.

Matching scores use the complete 20-fact gold denominator. A missing/wrong fact
cannot pass an expected-unknown capability label. Unresolved/wrong test identity
cannot pass its parameter checks. Claim correspondence labels, final capability
statuses and whole-test results are reported separately, and supported outputs
for extra/wrong facts count toward unsafe support. Comparing reportability with
an acceptance threshold does not predict a passing specimen.

Replay saved runs without any new calls:

```sh
python scripts/live_capability_experiment.py --extraction saved_extraction_run.json --matching saved_semantic_matching_run.json --output local_data/experiments/live-replay
```

Running the script without those inputs only prepares the oracle code baseline.
Generated results include the frozen fixture hash and evaluator source hash and
stay under ignored `local_data/experiments/`. Never present these four cases as a
production reliability percentage.
