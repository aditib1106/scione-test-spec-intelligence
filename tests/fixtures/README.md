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

## Frozen semantic-matching challenge v2

`semantic_capability_challenge_v2.json` contains 24 author-designed scenarios and
28 parameter decisions, split into two batches of 12. Its frozen SHA256 is
`54a4e286922e27b13a99e37d1583e0ecc41d53c73395a96f9e5a992794b7bf19`.
It supplies typed source facts to isolate semantic matching and deterministic
comparison from extraction, PDF parsing and OCR. These are development labels,
not independently reviewed production ground truth. Do not revise the labels
after looking at model responses.

The scenarios include bilingual names, unfamiliar parameters, equivalent free
context and material wording, missing facts, unknown tests, method editions,
ordered steps, different media and roles, numeric limits, resolution, nominal
tolerances, distinct configurations and the scope of negative declarations.
Proprietary identifiers retain meaningful case and punctuation. One case
intentionally supplies an incomplete extracted category to test safe handling.

The pure helper in `scione.evaluation.semantic_challenge` builds the candidate
snapshot. Each requirement can select only the capability IDs belonging to its
own scenario, even when another case in the same batch has a similarly named
capability. Otherwise a missing-information case could borrow evidence from a
neighboring test case. No settings, model clients or database are used by this
helper.

Replay one or more saved **SemanticMatchingRun** JSON files without API calls:

```sh
python scripts/evaluate_semantic_matching.py \
  --run saved_batch_a.run.json saved_batch_b.run.json \
  --output local_data/experiments/semantic-replay
```

`--run` may also be repeated. The input is the saved run itself, not a provider's
raw completion or a full application export wrapper; `matching_machine` from an
application artifact export is the corresponding run object. Input files remain
unchanged. The command writes full per-run scores and `summary.json` to the
chosen directory. This is explicitly labelled `CURRENT_POLICY_RECOMPILE`: it
recompiles the saved accepted links using the currently
imported production guards and comparator, recording their source hashes. It does
not reconstruct a historical policy from the saved model's version string.
Historical and current scores are not comparable without identifying the policy
used for each; original historical score/run files must remain unchanged. To
score previously frozen application verdicts without recompilation, use the
`evaluate_capability.py --artifacts` protocol above with independently aligned
labels and the exported `comparison_validated` data.

Invalid artifacts are recorded separately and return a nonzero
exit code; they are not converted into correct unknown capability decisions.

Without `--run`, the command compiles the frozen first reference outcome for each
case and exercises the comparison policy offline:

```sh
python scripts/evaluate_semantic_matching.py
```

This mode is explicitly labelled `manual_reference_policy_probe`. It measures
policy given author-typed facts and correspondences, not model performance.
No mode of this CLI loads credentials, retries inference or runs a provider.

Scores separate test/capability identity, exact claim links, parameter decisions,
whole-test status, false support, false gaps, false rejection of known positives,
unknown decisions and contract errors. Wrong identity cannot receive credit for
an accidentally correct unknown status. Missing or failed output stays in the
error denominator. Where more than one conservative outcome is valid, allowed
outcomes are frozen in advance; all metrics for a case use one complete chosen
outcome rather than favorable fields from several alternatives. Replayed trials
with overlapping cases are explicitly flagged and are not independent samples.

Exact link agreement scores IDs and relation labels. It does not independently
certify a model's proposed context/category wording equivalence. Those proposals
retain both quoted inputs and reasons, and require separate semantic annotation
for broader evaluation; structural acceptance alone is not proof of meaning.

`e2e_source_plan()` derives a separate six-page text probe from six frozen cases.
It uses neutral R01-R06 independent-request boundaries, one request per physical
page and explicit source stage headers, with seven expected source facts. Two
requests deliberately share a method, so method text alone cannot align their
output rows. The plan retains source/page hashes, typed expectations and a strict
scoring protocol. It does not change the 24-case fixture or create a PDF/OCR
benchmark. A real extraction-and-matching test must use actual extracted facts,
retain missing/incorrect facts as end-to-end errors, and report independently
reviewed representation differences separately from strict field agreement.

## Generated customer corpus and matching probes

`generated_matching_reference_v1.json` contains 28 frozen, controlled matching-only
scenarios (29 parameter decisions), using manually typed source facts and independent
synthetic lab declarations. It is an internal development reference, not domain-expert
validated gold. Source and scoring hashes prevent changing labels to match predictions.

```sh
python scripts/evaluate_generated_matching.py --output local_data/experiments/generated-policy.json
python scripts/evaluate_generated_matching.py \
  --run saved_batch_01/matching_run.json \
  --run saved_batch_02/matching_run.json \
  --output local_data/experiments/generated-live-replay.json
```

The first command makes zero model calls and probes software policy. The second
scores saved actual matching runs; repeat once per frozen batch (five batches total).
It reports missing batches, identity/link/parameter/method/overall results and false
support separately. A correct policy probe does not establish model performance.

The generated-PDF scorer requires explicitly supplied source-only partial annotations:

```sh
python scripts/evaluate_generated_corpus.py \
  --reference private_source_reference.json --reference-sha256 REFERENCE_SHA256 \
  --runs local_data/experiments/saved-extractions \
  --output local_data/experiments/extraction-score.json
```

Original PDFs, raw responses, private annotation working files and paid experiment
outputs remain outside tracked fixtures. Scans/handwriting are excluded from the
text-only corpus. Do not combine selected successful attempts into an apparent
single-configuration score or label iteratively inspected data an independent holdout.

`generated_matching_workflow_v2.json` is a separate four-test workflow probe with
35 manually selected, source-quoted CSP-EN-301 facts and an explicitly synthetic
lab profile. Its operating ranges and reporting declarations were authored before
matching inference, independently of extraction model outputs. Frozen outcomes
include full coverage, an explicitly unavailable post-cycle gloss output, and a
missing separate-topcoat declaration. It is deliberately a selected-fact matching
probe, not exhaustive extraction accuracy or a claim about any real lab.
`test_matching_workflow_automation.py` checks the policy, literal source quotes,
and counterfactuals where a field is removed or a lab range becomes insufficient.

The same optional lab input is packaged as
`scione.catalogs/data/csp301_typed_lab_example.json`, with provenance and limitations.
Loading an example must be an explicit user action; it never fills an existing
lab profile automatically or derives capability from catalog metadata. Actual
end-to-end extraction can still require review for additional source obligations
not included among these selected reference facts.
