# Inference policy

The workspace defaults to Sail / GLM-5.3 for specification extraction and semantic
correspondence. Code performs the final unit, numeric range and capability checks.
This is a development baseline validated on a small synthetic sample, not a claim
of optimal model choice or production accuracy.

| Setting | Default |
| --- | --- |
| Provider / model | Sail / `zai-org/GLM-5.3` |
| Temperature | 0; reduces sampling variation without guaranteeing determinism |
| Reasoning / completion window | low / ASAP |
| Completion ceiling | 8192 tokens, including provider-counted reasoning |
| Owned HTTP client deadline | 90 seconds for the complete local HTTP operation |
| Default extraction / semantic matching | One explicitly triggered request each |
| Automatic retries, repair calls or model fallback | None |

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

## Normalization and cache boundaries

A grounded typed fact can replace a weaker parser copy of the same source fact.
Unfamiliar materials, negative restrictions, uncovered quantities, distinct stages
and steps remain represented. Finding one temperature does not establish that an
atmosphere, exclusion or other qualifier in the sentence has been understood.

Matching caches bind the source/lab snapshot, prompt, validation and normalization
policy. Explicit runs also compare effective generation settings. Changing the
reasoning, temperature, completion ceiling or deadline does not reuse an
incompatible cached run. History remains readable, with the provider/model that
actually produced it.

## Request lifecycle and deployment limits

- Startup, idle browsing, model selection, refresh, ordinary form saves and exports
  do not invoke a model.
- Extraction and matching prevent immediate duplicate submissions in the browser.
  The backend blocks concurrent AI operations for one document and releases that
  guard after failure.
- Leaving a page stops browser waiting. Default modes submit one request. The
  advanced focused mode is a previously requested batch of up to eight calls that
  repeats the full source; it is not input chunking and leaving the page does not
  cancel that batch.
- The default client uses a cancellable local deadline and closes its connection.
  It creates no background retry worker or inference job queue.
- Closing a page, timing out or stopping the local server cannot guarantee that a
  provider cancels an already accepted inference or waives its charge. Close the
  server with Ctrl+C; Ctrl+Z suspends a process rather than stopping it.
- The document guard protects one server process only. Multiple workers or
  instances require a shared database lease. Identity isolation, account budgets,
  private storage and deployment-level lifecycle policies remain production work.

Application tests and experiments do not leave cloud model deployments, Sailboxes,
GPUs or inference polling running. Actual balances and billing remain the provider's
record; local token estimates are not invoices.
