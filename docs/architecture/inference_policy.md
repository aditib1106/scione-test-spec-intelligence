# Inference policy

The workspace defaults to Sail / GLM-5.3 for specification extraction and semantic
correspondence. Code performs the final unit, numeric range and capability checks.
This is a development baseline validated on a small synthetic sample, not a claim
of optimal model choice or production accuracy.

| Setting                                           | Default                                                                                                             |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Provider / model                                  | Sail / `zai-org/GLM-5.3`                                                                                            |
| Temperature                                       | 0; reduces sampling variation without guaranteeing determinism                                                      |
| Reasoning / completion window                     | low / ASAP                                                                                                          |
| Completion ceiling                                | 12,288 tokens, including provider-counted reasoning                                                                 |
| Owned HTTP client deadline                        | 300 seconds per local HTTP operation                                                                                |
| Guided extraction / semantic matching             | Two serial extraction calls for fitting sources; oversized sources use the bounded chunk plan below. Matching defaults to eight calls; workspace PDFs longer than ten pages allow sixteen |
| Long-document parallelism                        | `SCIONE_LONG_DOCUMENT_PARALLELISM=4`, allowed 1–4; applies to Sail/Fireworks chunk/batch waves for workspace PDFs longer than ten pages |
| Automatic retries, repair calls or model fallback | None                                                                                                                |

Other models remain selectable. A new model needs validation of its schema and
reasoning support, token budget and task quality. Changing a model ID does not
establish equivalent behavior or account limits. See the official
[Sail models](https://docs.sailresearch.com/models) and
[API support](https://docs.sailresearch.com/support).

## Validation and recovery

1. Check source text status and input character budgets before inference. Do not
   silently truncate a document or candidate list.
2. Request native JSON Schema output, including cross-field rules for numeric
   operators and value counts, unresolved links and candidate selections.
3. Validate the returned JSON, finite numbers, duplicate keys, completion status,
   schema, exact ID coverage and candidate boundaries locally. Check source pages,
   quotations, quantities, units, operators and context separately.
4. Semantic links cannot change source quantities. Incompatible stages, materials,
   media and configurations cannot be combined into evidence of support. Missing
   information remains unknown.
5. Save unacceptable output as a failed attempt. Do not fabricate missing values,
   accept truncated JSON as complete or call a model again to repair it. Preserve
   the previous accepted extraction and comparison.
6. Users can inspect the source, previous result and failure. Developer details
   provide the failed artifact download. A retry or changed output budget is a new
   explicit action; 429/503 responses do not start a retry loop.

Failed artifacts retain the original generation request/response, available usage,
actual model and generation settings. They exclude API keys, authorization headers
and provider objects. Artifacts can contain customer source text and belong in
private application storage, not Git. Machine outputs and human corrections have
separate revisions.

The expanded `run.extraction` is a validated application result, not verbatim AI
output. Under `source_stage_citation_v1`, source grounding may add a contiguous
source citation containing an immediately preceding recognized `Stage:` header
and the fact's physical line, when an already emitted fragment occurs uniquely
in the source and its recorded stage agrees. A detached header elsewhere on the
page cannot establish another fact's stage.
The runner records `added_stage_evidence` or `stage_conflict` in
`run.grounding_actions`; conflicts remain flagged for review and their quality
state becomes unresolved. This source-only step
does not substitute a ground-truth fact, rewrite a value or infer a missing stage.
The original provider response remains untouched in `run.calls[].raw_response`.

## Source representation and candidate boundaries

Guided extraction first proposes a short test inventory, then reads the source
directly with only source-located inventory guidance. Fitting documents remain whole;
oversized documents use the raw views described below. Unlocated fragments are omitted from
guidance with an explicit audit; they are never repaired or substituted for source
facts. Tests without any locatable evidence cannot guide the second pass. An invalid
individual attachment is unused as a whole, with its original one-based index and
reason audited; independently valid tests survive. Target links are not repaired or
partially accepted. Invalid root/test entries or attachment-list structure still make
the inventory unusable. Otherwise, attachments are retained only when their targets
survive verification. The guide is not a whitelist:
it never replaces the planned source input. Both actual requests, responses,
purposes, prompt versions and usage are saved. This adds latency; it is not a retry or
a claim that two model outputs establish correctness. The original one-call compact
strategy remains selectable for comparison.

Compact extraction currently uses `customer_requirements_compact_v0.18`. A lexical
checklist contains only method designators actually present in the uploaded PDF,
with physical page numbers. It is non-exhaustive and does not decide whether a
mention is required, optional or background material. The model must establish
that relationship from the source; independent measurements keep separate rows.
Shared clauses target explicit test indexes rather than creating an administrative
or reporting test. Exceptions remain reviewable instead of silently applying to all.
Consistent role citations can refer to several pages; contradictory roles remain invalid.
Wire field limits match the expanded atomic contract, including a 40-character unit.

Physical scope hints check a few explicit material/article labels and release-approval
clauses against the extracted item's owned fields. A warning means literal retention
was not verified; a paraphrase or split representation may still be semantically
correct. These hints neither infer missing targets nor establish semantic omission
or document completeness, and unresolved warnings remain visible for review.

The default source input guard is 60,000 characters including prompt, schema, guide
and source, rather than a provider token/context guarantee. Oversized compact/default
extraction uses `shared_source_pages_v1`: every original physical page belongs to one
primary window, with raw shared-condition/annex pages and fitting neighboring pages
supplied as context. Heading detection is a conservative English heuristic, not proof
that a shared clause applies to every test. The model must preserve exceptions and
scope. Original page numbers, document identity and total page count remain unchanged.
Explicit subsection references on primary pages also retrieve exact raw section spans
when the numbered heading is unique. Referenced-only pages include the section's
original character ranges, including its continuation pages, rather than unrelated
sections on the same page. Multiple references use the ordered span union without
summarizing or rewriting text. Such a page remains excerpted even when selected as an
optional neighbor; primary/shared pages remain whole. Every original page is still
supplied whole in its own primary window. This is one hop, not recursive;
unknown/ambiguous references remain unresolved rather than selecting an arbitrary
section, and the complete request is remeasured.
Chunk requests retain every parameter ID and label but omit repeated vocabulary
descriptions; unchunked whole/focused requests retain the full descriptions.
Trusted `PAGE_ROLE` metadata labels primary and context-only pages without changing
their source text. `SOURCE_VIEW` identifies a page containing exact referenced-section
excerpts. Manifest `context_source_spans` entries record `page_number`, `start` and
`end`: zero-based, half-open character offsets into the original `DocumentPage.text`,
not byte offsets or positions in the concatenated view. The prompt requires a substantive
primary test row or procedure
request: a section heading, index or cross-reference does not own separate context
rows. Context can complete the same continued row; an explicitly additional annex
test remains eligible, while a package summary alone is not a new test. These are
model instructions, not proof of correct ownership.
When original PDF bytes are available, physically located section introductions
also travel with a section's continuation pages. Their exact heading-to-introduction
spans are mandatory context, separately listed in `section_context_pages` and the
ordinary context span audit. `SOURCE_SECTION_CONTEXT` labels only introductions
whose complete quote is actually supplied; it cannot restore an unrelated omitted
table. This keeps a continued chemical table's local judging conditions available
without supplying neighboring tests as new primary work.
Primary windows contain at most three pages. A 4,800-character soft cap on non-shared
primary text reduces output pressure on dense multi-page windows; it never splits or
truncates a primary physical page and is not a guarantee against completion-token exhaustion.
All extraction windows are preflighted against the complete-request guard before any
model call. The maximum is eight extraction calls plus one optional full-source
inventory call; an inventory that cannot fit is explicitly skipped in the run manifest.
Per chunk, guidance is budgeted in this order: full source-located inventory guidance,
source-located names including primary/context roles, primary names only, then no
guide. Names-only guidance conveys no attachment or applicability decision and is
not a whitelist. Guide evidence must also occur literally in the supplied view; an
original page number alone cannot authorize text outside its supplied fragment.
Every option retains the same planned raw source view, including audited excerpts.
Source text is never silently truncated. If an indivisible page or required shared
block cannot fit, or complete page coverage exceeds the call cap, no call is made.

Each extraction window emits work supported by its primary pages. Context-only rows
are audited and omitted; any primary-page evidence retains the row, even when it also
cites earlier preparation. Quotes and atomic citations must reference pages actually
supplied to that call. Requirement evidence found in an original context page but only
outside its supplied excerpts fails with `chunk_source_scope_error`; original-page
location cannot authorize text the call did not receive. Compact shared clauses are expanded within their original
response before merging. Exact duplicates ignore only local requirement/claim IDs and
evidence ordering; differing scopes, stages, values and sequences remain separate.
Global IDs and row-scoped review issues are remapped before final source grounding.
An exact, unique test-name prefix can scope an issue to that row. Expected statements
that another window's context-only work was not emitted stay in the chunk audit instead
of creating whole-document uncertainty; row-specific and scientific warnings remain.
All windows must complete before saving a new run. Any provider/schema/source-scope
failure preserves previous results and all completed/failed call artifacts; no partial
extraction is accepted and no automatic retry occurs. The private raw-response manifest
records page coverage, context-only rows, original requests and untouched responses.
Sail/Fireworks long-document chunk extraction uses the configured wave width, default
four and maximum four; operators can lower it to 1 or 2 for provider quotas. All admitted
responses are retained before parsing; a failed wave starts no later wave. Calls and
merged facts stay in source order. Per-call latency/usage remain separate; the parent
chunk run records wall time. Inventory and fitting short-document extraction stay serial.
Shorter chunked inputs retain the existing two-call paid-provider limit; other providers
remain sequential. Parallelism changes neither the call cap nor source coverage and
does not guarantee lower provider latency.
Advanced legacy focused passes still repeat the complete source and only focus output.

New application PDF imports use geometric reading order and record that choice.
Ready native PDFs historically saved in content-stream order can use a derived
geometric view under `native_geometric_source_view_v1`. The cache is keyed by PDF
hash, original document representation, parser version and reading policy; it lives
under `derived_documents/<document_id>/geometric_v1.json`. Current analysis, review
and source location use this verified view. Original PDF bytes, `document.json`,
saved runs and raw provider output remain unchanged. OCR, non-native, sparse and
already-geometric records bypass the legacy reorder path. A stale derived cache
can be regenerated; altered PDF bytes or unsafe paths fail without replacing source.
This is a layout heuristic, not an assurance that every table or column is understood.
Scans and handwriting still require the separate OCR integration.

`source_named_scale_citation_v1` may establish an omitted colour-scale unit from a
unique physical summary row on the same page. The body criterion must be physically
located, covered by that test's owned evidence and agree with the summary's parameter,
operator and value. Competing owners, phases, units or thresholds block the proof.
The original native unit/value remain archived; a detached normalized view receives
the named scale with an audit of the body and summary source spans. Latin typography
ligatures can be expanded with an offset map for literal source location, without
rewriting words or treating paraphrases as exact quotations.

The following source-only derivations also operate on detached views, not on an
archived extraction or a saved human-facts revision:

- `literal_section_scope_v1` can exclude an inherited condition/native atom only
  when a unique physical table row belongs to another numbered section and the
  clause exactly matches a uniquely located local introduction. Same-section and
  descendant rows retain it. Global/exception wording, actual cross-references,
  unknown ownership and manually entered conditions without parsed inheritance
  proof remain unchanged. Reviewed atoms are retained with a scope-conflict audit.
  The action preserves the complete excluded fields/atoms, original section span
  and table-row location; exclusion establishes no lab support.
- `physical_table_cell_citation` adds evidence only for a complete cell whose
  text agrees with an existing atom and whose unique row has the exact test label
  on the cited physical page. PDF hash, row/cell bounds, cell index and quote hash
  are recorded. Ambiguous rows, changed numbers or partial cell text do not qualify.
  This supplies a citation, never a new value or a lab-capability judgment.
- `literal_conditioning_atoms_v1` completes duration, temperature and explicit RH
  from one closed, already attached conditioning sentence. The complete sentence
  must be located in physical source text; scalar values, printed ranges and
  tolerances reuse the lexical quantity parser. New atoms have stable IDs, kind
  `condition` and stage `conditioning`, with exact source text/page/offset provenance.
  Existing equivalent facts are not duplicated. Conflicts, negation, exceptions,
  omitted source fragments and extra unrecognized content prevent completion.
  No quantity or applicability is inferred from the test name.

These actions are retained in derived `normalization_actions` and matching input/
policy artifacts. Original provider requests/responses, machine runs and reviewed
facts are not replaced by the projection. Current-policy source derivations are
therefore distinct from both raw model output and historical frozen verdicts.

A separate source-proof memory cache holds at most eight derivations for PDFs of
at most 1,000,000 bytes. The complete requirement JSON, document JSON and PDF bytes
are its key; it accepts no lab capability, account or session fields. Every cache
hit returns detached copies, and the caller still reads the authorized lab's PDF.
Combined serialized source and PDF inputs over 2,000,000 characters/bytes also
bypass it. This cache writes nothing to disk and ends with the server
process; it is distinct from the legacy geometric-document disk cache above.

Citation policy `v2` permits wrapping whitespace/CJK spacing and a narrow,
length-preserving French apostrophe typography rule. Words, quantities, signs,
code punctuation and all original artifacts remain unchanged. Atom citations may
be added only through unique, parent-owned physical source spans. Known synthetic
bank label conventions can separate a method designation from an exact family
heading/suffix; the full reference, original offsets and action are retained under
`source_method_label_v1`. Modifications, unknown labels and scientific qualifiers
are never stripped to force a familiar method match.

A unique same-source quantity may replace a duplicate parser copy. Unproved
stage/context metadata produces one explicit source-association review check;
it cannot become full lab support through a semantic link. Explicitly labelled
review/rejection triggers and absolute quantities mislabelled as max-minus-min
spreads remain unresolved rather than being presented as trustworthy acceptance
limits. These are conservative checks, not a complete scientific language model.

The full 32,000-row synthetic method bank is separate from the active 99-test /
154-method catalog. `GET /api/catalog/method-bank` provides read-only paginated
lookup with exact identifier/family/domain/material filters and source hashes.
Matching retrieves bounded candidates, retaining complete editions and parts.
The bank never populates lab declarations or asserts equivalence between methods.

Complex matching uses `whole_requirement_batches_v7`: preflight every complete-test
batch before any network call, normally at most eight requests, with no retries.
Workspace PDFs longer than ten pages allow at most sixteen. The matching
input guard defaults to 120,000 characters per request, including schema/prompt.
Sail and Fireworks use keyed correspondence output. For workspace PDFs longer than
ten pages, waves follow `SCIONE_LONG_DOCUMENT_PARALLELISM` (default four, allowed
1–4); shorter matching batches retain their existing two-call wave limit. Other
providers remain sequential. Every in-flight response or
failure is retained, no later wave starts after a known failure, and a partial plan
never becomes an accepted comparison. An indivisible test exceeding the guard fails
before inference. The parent artifact is an
execution manifest, not a provider response; `child_attempts` retain every actual
request/raw response/usage. Any child failure stops the plan and rejects the combined
result. Previous saved success remains available. No source claims are truncated.

Paid Sail/Fireworks matching requests use `matching_keyed_v5`: every source test and
claim ID is a required JSON object key. The lossless adapter restores the application
lists in original input order. Missing/extra IDs fail validation; they are not filled
with invented unknown records. The exact keyed provider object and raw text remain
in each attempt. Other providers retain the array wire contract.

The compact wire omits legacy quotation bridges, requires an explicit aligned
scope relation, and distinguishes text coverage from numeric comparison. Candidate
ID enums are shared through schema references. Repeated long quotations may use
`source_text_table` references; a mandatory round-trip check proves that expansion
recovers every original input value. No source is summarized or truncated.
Lab claims with identical bodies can also use `claim_body_table`: each occurrence
keeps its original claim ID and refers to every other original field without changes.
This serialization reuse never permits mixing capabilities. It is enabled only when
the complete wire payload saves at least 4,000 characters; expansion verifies full
field equality. The immutable snapshot and actual transmitted request remain saved.
The normal matching budget is eight batches. The workspace allows sixteen for source
PDFs with more than ten physical pages, checked from the stored document rather than
model output. Each complete request still fits the 120,000-character guard. The full
plan is validated before inference, concurrency is bounded as above, and failures do not
accept partial output or trigger retries. The single-call and default CLI budgets stay
unchanged. Source submission/allocation quantities and verified shared project delivery clauses can
remain workflow obligations without pretending to establish equipment capability.
Core operating conditions and specimen acceptance facts remain comparable.

The native request-specific schema bounds each test's catalog and capability IDs to
its allowed candidates, with null available for unresolved identity. Linked lab-claim
IDs are limited to claims belonging to that test's candidate capabilities, rather than
claims from other source tests. A row without candidates permits only null identity
IDs and empty unresolved claim links. These enums do not express ownership by the
capability actually selected in the response: runtime guards still verify that
ownership, cross-field identity, complete coverage and scientific compatibility.
An invalid ID rejects the attempt and preserves previous accepted results and the raw
artifact. No automatic repair/retry is introduced, and schema validity does not prove
that a proposed correspondence is factually correct.
If both selected IDs are valid candidates but the capability belongs to a different
test, only that item is downgraded to unknown after its references are checked. Its
lab links and semantic bridges are cleared on the validated copy; original proposals
remain in the artifact. This is an unresolved identity requiring review, not an
unsupported-test verdict, and cannot discard the other valid items in a batch.

Sail/Fireworks execute bounded whole-test waves in an operation-scoped thread pool:
the long-document setting defaults to four calls, while shorter batches retain the
two-call limit. Every request is preflighted before that pool starts. A failed wave
retains all admitted attempts, starts no later wave and accepts no partial result. Workers
finish before that operation completes; no recurring queue or automatic retry is created.
The soft output-planning budget scales with the provider ceiling and is capped at
eighty claims; a larger indivisible test still receives its own guarded request.
Free providers remain sequential. Parallel parent latency is elapsed wall time; child
artifacts retain individual measured latency and usage.

One complete, source-located nonnumeric descriptive clause can use an explicit AI text
correspondence or an identical lab declaration even without a registered parameter name.
This is a comparison-only view: stored unknown identity/raw source remains unchanged.
Invalid quantities, shaved residual fragments, missing lab information, incompatible
scope and qualitative specimen acceptance criteria do not enter this path. Unfamiliar
numeric units can compare only with the exact same literal unit after a validated
attribute link; no spelling correction or unknown-unit conversion is guessed.

Closed, literal submission/allocation clauses retain numeric sample-plan records
separately from scientific capacity checks. An exact source count can correct a generic
count classifier (for example a source-proved count of lots extracted as specimen count),
with the original value, unit, context and quotation preserved and the action recorded.
Mixed operating clauses, wrong quantities and unlocated text cannot use this rule.

An approval-dependent alternative may remain an informational route note while the
explicit primary route is assessed. This requires a saved, document-aware proof of
the original method-role proposals, owned physical quotations, a primary criterion
and a future alternative acceptance class; the proof is bound to the full source-row
hash. Selecting the alternative later makes its unissued class unknown again. The
primary method's acceptance class cannot supply a different method's missing class.

## Explicit semantic wording proposals

An ID link says which property is related; it does not establish that two category
values or free-text contexts mean the same thing. A claim alignment can separately
propose `context_equivalence` or `value_equivalence` for one selected lab claim.
Each proposal must quote both existing fields exactly and retain its reason. Missing
input cannot be supplied by a proposal. Accepted proposals affect comparison copies
only; original source/lab text and the proposal remain separate audit artifacts.

`joint_scope_equivalence` addresses one explicit field-placement case: a positive,
nonnumeric category/resource fact has a context stored separately on one side and
verbatim once inside the counterpart's text value. Both text values must exist,
exactly one context must be absent, and the source quotation must own the fact.
The proposal quotes the exact supplied `joint_scope_texts` for one selected lab claim,
with the ordinary context/value bridges null. Runtime checks retain the remaining
category, numbers, units, materials, codes, phases and setup. Negative, conditional,
numeric, specimen-criterion, two-absent-context and multi-claim cases cannot use
this path. A missing or incomplete counterpart qualifier cannot be supplied by a proposal.

Lossless transport variations can retain those joint views: strictly parsed objects
must contain exactly the original fields, or a safe delimiter-only rendering must
preserve every original field value. Exact selected text values are also accepted
only when code independently proves the complete original context on both sides.
Duplicate/extra keys, omitted qualifiers and changed scientific text fail validation.
The received proposal strings and original source facts remain unchanged.

Validation rejects proposals that change quantities, erase a negative, merge sets
without member evidence, override conflicting known categories or rename unfamiliar
codes. Context proposals cannot bridge incompatible stages, sequence, medium,
material, specimen layer or configuration. Unknown identifiers retain meaningful
case and punctuation. A negative declaration for one material/resource is not a
global negative for another requested value.

These checks bound the proposal; they do not prove every scientific synonym or
translation correct. An accepted wording judgement can still be wrong and needs
independent evaluation and appropriate human review. Numeric thresholds and unit
conversion always remain code responsibilities.

## Normalization and cache boundaries

A grounded typed fact can replace a weaker parser copy of the same source fact.
Unfamiliar materials, negative restrictions, uncovered quantities, distinct stages
and steps remain represented. Finding one temperature does not establish that an
atmosphere, exclusion or other qualifier in the sentence has been understood.

After a valid same-parameter link, comparison uses a known parameter policy from
either side symmetrically. A translated/custom request ID must not discard a lab's
registered resolution, reporting or temperature-conversion rule. Conflicting known
definitions stay conflicting; two unknown IDs retain conservative custom handling.
An explicit registered label that contradicts the chosen policy vetoes the link;
labels cannot silently replace a different known quantity such as resolution with
uncertainty. A generic temperature label may retain an explicit water/air policy.
For registered category/resource restrictions, `scope` and `condition` naming can
be compared as operational demands. This does not bridge numeric execution roles
or turn specimen criteria into a promise of passing results. All policy selection
uses copies and preserves the recorded identities and roles for display and audit.

A registered relative-humidity claim recorded with `%` can use `% RH` on a
comparison copy only when its owned source quotation, page, literal RH expression,
operator and complete values/tolerance prove that unit. An ordinary percentage or
unfamiliar property cannot use this rule. A closed topcoat/per-panel context can
be consumed only when the source owns that subject and the selected capability
separately declares unconditional per-panel reporting in the same configuration.
Conflicting, qualified or missing declarations remain unknown. Both rules retain
source facts and record their proof in comparison metadata; they do not widen a
lab's numeric range or predict specimen acceptance.

Model JSON numbers are parsed without an intermediate binary-float round trip.
Finite decimal and contiguous e/E literals retain their exact value, and saved typed
JSON uses decimal strings. Precision/exponent complexity is bounded as a software
resource limit, not a lab capability. Unsupported arithmetic notation remains
reviewable rather than being converted into a plausible number.

Matching caches bind the source/lab snapshot, prompt, validation and normalization
policy. Normal report generation can reuse the current accepted comparison without
starting a model experiment. Explicit developer model runs also compare effective
generation settings. Changing the
reasoning, temperature, completion ceiling or deadline does not reuse an
incompatible cached run. History remains readable, with the provider/model that
actually produced it.

Saved assessment identity also hashes the complete computed report payload, including
source-review policy and warnings. A warning-policy upgrade creates a separate snapshot
without replacing an earlier PDF/JSON assessment for the same source and lab inputs.

Current policy versions are `atomic_requirements_v0_39`, source review `0.19`,
`pilot_parameters_v7` and `semantic_alignment_guards_v0_26`; matching uses prompt
`semantic_matching_v0_11`.

## Review and presentation

Source review and lab support review are separate. No model-reported probability
automatically approves a source item. Source quotations, known scientific-identity
contradictions, unproved scope and explicit human decisions determine the workflow.
An unregistered field or local parser limitation alone is not proof that the source
extraction is wrong. Known invalid numeric facts remain guarded after confirmation;
confirmation does not rewrite the original provider response or certify lab support.

Source review policy `0.19` can show an unissued alternative-method acceptance class
as a notice rather than blocking the explicitly specified primary route. This needs
verified original role proposals, literal owned physical citations, an explicit
primary class and the source's approval-dependent future-class statement. Numeric,
scope, unknown-role and mixed scientific concerns remain blocking. The warning stays
in the original output. Document-aware route proofs are captured separately in matching
policy artifacts; if the alternative is selected, its missing class stays unknown and
the primary method's class cannot be borrowed.

A closed, source-located request to measure or report a registered numeric property
can remain a reporting requirement without inventing a specimen acceptance limit.
It must retain its role, complete source statement and any stated scope; missing or
unproved properties remain reviewable. An existing typed lab reporting declaration
can establish that output's presence in the same context. Its range is not a promise
that the customer's material meets an unstated threshold.

A narrow compound-reporting guard detects a single known film/topcoat reporting
entry whose required target combines total film and separate layer thickness. It
requires source review and separation into distinct grounded measurement claims,
rather than automatically splitting the entry or allowing one topcoat declaration
to certify both outputs. Two correctly separated native claims can share the same
compound source quotation. The guard preserves the original run and is not a
general detector for every compound measurement request.

Only a closed generic positive summary comment may be classified as
`unused_model_comment`. It remains in the original output and developer audit with
`used_as_evidence=false`; it neither validates source consistency nor establishes a
fact. Numbers, named properties, methods, uncertainty, negation, row references and
action requests do not fit that generic grammar. Independent physical/source and
scientific checks still run.

`physical_acceptance_summary_v1` separately handles one closed four-property
acceptance-summary template. Each listed threshold must agree in a unique physical
body passage, the physical summary and a correctly owned native criterion, including
operator, scale, role, phase and per-panel/per-site scope. The model's repeat-handling comment only selects the
audit; it is never fact evidence. If any property, source owner or comparison differs,
the comment remains reviewable. This is not a general summary interpreter or a
document-completeness certificate. Original comments and the physical proof remain
available separately.

A standalone revision restatement can be displayed as a source note only when its
revision uniquely agrees with saved document metadata, all located physical GU
ranges agree, and one independent owned native initial-gloss criterion agrees too.
An unlocated note, conflicting revision/range, uncertain owner or other scientific
content retains review. The original notice and its source proof remain archived.

Only source-proved, closed phase/object descriptions can be consumed as redundant
comparison metadata. Independent material, configuration, statistic and quantity
restrictions remain constraints. A unique compatible registered field can receive a
deterministic comparison within an already aligned lab/test; unfamiliar identities,
ambiguous setups and unresolved source facts cannot use this recovery path. Metadata
actions and comparison basis remain separate audit fields.

`source_method_and_reviewed_sibling_exposure_mode` is a narrow same-capability
comparison proof. An owned customer quotation must explicitly retain
`ISO 6270-2 (CH)`, and the selected lab capability must explicitly declare that
same method. Its one reviewed, supported CH exposure-mode claim must share the
duration's exposure stage, sequence and sole configuration. Only then can the lab
duration's comparison copy receive this existing setup context. Original duration,
mode and source records remain unchanged; numeric range comparison still decides
the result. Multiple modes, unknown/rejected declarations, different configurations,
other source codes and unowned quotations do not qualify. A plain ISO identifier
does not supply a setup, medium or temperature classification.

When a semantic suggestion names a real lab entry but its scope cannot be accepted,
the web table may show it as **Lab information to review**. These values are read only from
the selected captured capability, are explicitly unconfirmed, and never change the
verdict. A later lab edit invalidates this presentation with the old matching input.
This differs from a genuinely absent declaration.

Source grounding and review interaction were informed by
[LangExtract's source-span approach](https://github.com/google/langextract).
Schema compliance and factual quality are assessed separately, consistent with
[JSONSchemaBench's evaluation dimensions](https://arxiv.org/abs/2501.10868).
Extraction prompts are versioned separately and stored with each attempt.

`GET /api/diagnostics/{document_id}` exposes `source_review_audits` for the selected
source run under current policy: review version, source run ID, PDF hash, physical
summary/revision proofs, unused-comment audits and named-scale citations. New
matching attempts capture the bundle before inference under
`policy_snapshot.source_review_audits`. That captured bundle belongs to the
attempt; a later diagnostics request is a current-policy replay and must not be
presented as the historical review state of an older attempt. Existing raw runs
remain unchanged, and no extra lab-facing diagnostic controls are required.

Offline semantic replay is labelled `CURRENT_POLICY_RECOMPILE` and records the
actually imported compiler/guard/registry source hashes. Its scores use today's
policy, even for an older saved model response. Historical frozen verdicts require
the original `comparison_validated` export and corresponding policy provenance.
Never overwrite an original run/score file or treat recomputed improvement as a
new model result. Synthetic development probes are not production accuracy claims.

## Request lifecycle and deployment limits

- **Generate report** in source review carries a one-use navigation intent to Reports.
  The intent is consumed before comparison starts and is not a persistent URL trigger.
  Normal **Generate/Update report** uses the configured model and reuses a current
  completed comparison. Model selection, forced experiments, history and artifacts
  remain in Developer report tools. The action authorizes sending the saved source
  requirements and lab declarations for this comparison.
- Startup, idle browsing, model selection, refresh, ordinary form saves and exports
  do not invoke a model.
- Extraction and matching prevent immediate duplicate submissions in the browser.
  The backend blocks concurrent AI operations for one document and releases that
  guard after failure.
- Leaving a page stops browser waiting. Complex matching can finish its already
  started bounded plan; Sail/Fireworks can have up to the configured long-document
  width (default four) in flight, or two for shorter batched matching. It does
  not submit retries. Users can keep viewing a saved report and reopen the elapsed
  progress display without starting another operation. The
  advanced focused mode is a previously requested batch of up to eight calls that
  repeats the full source; it is not input chunking and leaving the page does not
  cancel that batch.
- Browser inference uses a short opt-in `Prefer: respond-async` admission request.
  The server reserves the existing document/lab/global gate before acknowledging
  a 202 receipt. An in-process pool runs at most two admitted jobs (one per lab),
  preserving the submitting principal and workspace context. There is no waiting
  queue, provider fallback or automatic retry. Legacy API calls without the header
  keep their synchronous contract.
- Receipt status is lab-scoped and contains only progress, a compact saved result
  identifier or a fixed public error category. Raw source and arbitrary exception
  details are never included. A request UUID is bound to the lab, document, action
  and managed input digest; duplicate acknowledgement recovery cannot create another
  inference. Finished receipts expire after one hour and are capped at 128 entries.
- The browser polls through bounded short GETs, including the response body, and
  reconciles a lost acknowledgement with the exact request UUID. It never repeats
  the submission automatically. Persistently lost observation becomes completion
  unknown; a saved successful run clears older transport errors. Server restarts
  lose process-local receipts; saved results must be checked before an explicit retry.
- Running extraction and matching expose the number of returned AI calls against
  the preflighted plan. The displayed percentage measures completed AI steps,
  not elapsed time or accepted results. Checking and saving remain visible after
  the last call returns. Progress contains fixed stage labels only and is scoped
  to the requesting lab. Timing references use comparable completed workloads;
  without that history the UI gives a broad waiting notice rather than a countdown.
- Closing a page, timing out or stopping the local server cannot guarantee that a
  provider cancels an already accepted inference or waives its charge. Close the
  server with Ctrl+C; Ctrl+Z suspends a process rather than stopping it.
- The document guard protects one server process only. Multiple workers or
  instances require a shared database lease. Identity isolation, account budgets,
  private storage and deployment-level lifecycle policies remain production work.

Application tests and experiments do not leave cloud model deployments, Sailboxes,
GPUs or inference polling running. Actual balances and billing remain the provider's
record; local token estimates are not invoices.

## Research and limits

[JSONSchemaBench](https://arxiv.org/abs/2501.10868) evaluates constrained output
compliance, constraint coverage and output quality separately. We likewise keep
schema success separate from scientific/source correctness; one cannot certify
the other. [Entity Matching using Large Language Models](https://arxiv.org/abs/2310.11244)
finds prompt sensitivity across models/datasets rather than a universal best prompt.
Our choice of bounded candidates, source-driven error analysis and versioned tests
is an engineering response, not a claim that the paper validates this lab workflow.
[DoclingDocument](https://docling-project.github.io/docling/concepts/docling_document/)
provides a reference for richer document hierarchy, tables and provenance. It remains
a future layout-adapter option; this change does not install Docling or solve OCR.

Internal generated-data results are development evidence. Domain validation,
representative independent source/lab pairs, completeness/false-support labels
and real document layouts are still required for a production reliability claim.
