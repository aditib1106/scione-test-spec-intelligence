# Changelog

Changes are recorded here separately from setup and usage instructions in the README.
An entry under **Unreleased** describes repository changes, not the currently hosted
version. On deployment, move verified changes into a dated release entry and record
the deployed commit and environment. Do not include credentials or private account data.

## Unreleased

### Compact report actions and clearer records — 2026-11-24

Local implementation; not deployed. No additional API, schema, model or
comparison-policy change.

- Move report-only removal/restoration to the selected test's **More test actions**
  menu. Open confirmation directly instead of expanding a full-width scope row.
  Source exclusions still require source review; removal does not delete extraction.
- Make the reviewed state compact and keep the decision buttons separate from
  the secondary record links.
- Present review history as a timeline with decisions, notes, authors and times.
  Keep prior events and the downloadable history unchanged.
- Put **Record question** directly inside Customer clarifications. Explicit question
  and reply actions scroll to and focus the form, including long conversations.
  This records email/phone exchanges manually; it sends no messages and does not
  change comparison findings.
- Validated 63 desktop/narrow browser cases; nine viewport-specific cases were
  inapplicable and skipped. Production build and formatting passed. Local fixtures
  prohibit live model calls; hosted accounts remain unchanged.

### Focused review workspaces and related specification versions — 2026-11-24

Local implementation; not deployed. This continues the client-feedback work without
changing model prompts, comparison policy, tenant permissions or database schemas.

- Move report comparison content higher and give it more horizontal space. Keep review
  decisions in a stable action strip while the selected test's detailed comparison scrolls.
- Open **Review history** and **Customer clarifications** in separate, clearly titled
  dialogs. Their content no longer expands into the report. Preserve lazy reads,
  conflict handling, draft retention and idempotent clarification saves.
- Keep document-note counts visible in a compact **Document notes** trigger. Checking
  and reopening notes happens in a scrollable dialog without moving the workbench.
- Fit the PDF review's two main columns to the available laptop viewport, retaining
  independent detail scrolling, source highlighting and reachable confirmation/edit
  actions. Move **Generate report** to the header; keep secondary analysis controls
  available through **Document actions**.
- Compact repeated identity labels and inline source references in PDF review without
  shrinking the text. Short-laptop checks require a readable first parameter before
  scrolling, not merely a larger container. Other reference buttons retain their sizing.
- Make related specification versions discoverable through a **Versions** list ordered
  by the customer specification date. Searching an older filename/date finds its family.
  The list uses the existing family/version records; it does not replace old PDFs,
  extraction, review or report history. Removing one version retains the others.
- Version dates and **Viewing** identify chronology/current navigation only. This does
  not introduce an approved active version, formal sign-off or cross-version gap analysis.
- Verified 82 affected desktop/narrow browser cases, with six viewport-specific cases
  skipped where inapplicable; the final compact source layout passed a further 26
  affected-case reruns. All 73 targeted backend checks, production build and formatting
  checks passed. Tests use disposable data and forbid live model calls; user visual
  acceptance is pending and no new accuracy measurement is claimed.

### Customer clarification records and storage efficiency — 2026-11-24

Local implementation; not deployed. Existing hosted accounts and data are unchanged.

- Add a collapsed **Customer clarifications** section under a test's existing report
  review area. Lab users can record questions and replies, with author, time and optional
  contact. These are manual records; the system sends no messages and does not change
  matching findings or approve source facts when a reply is saved.
- Store clarification events in an additive SQL table with tenant/source/test scope,
  optimistic versions and idempotent save requests. Reassessment of the same extraction
  retains the conversation; re-extraction starts a separate thread. Current export
  JSON/PDF includes the applicable records while older snapshots stay frozen.
- Update only changed current lab entries/claims during a save, keeping complete lab
  revision history and transactional version checks.
- Store new review-only exports as lossless references to the immutable machine
  assessment plus frozen manual overlays. Existing full exports remain readable.
  This reduces future duplicate JSON writes; it does not reclaim existing volume
  space. See [storage lifecycle and rollback boundaries](docs/architecture/storage_lifecycle.md).
- Correct a literal source-quantity audit that could flag already preserved phrases
  such as "three readings" or "five-cycle" after prematurely normalizing number words.
  Frozen CSP301–310 source replay removes five false warnings without changing any of
  the 480 normalized claim payloads. This is not a new end-to-end accuracy score or an
  achieved human-review target; prompts, models, thresholds and capability seeds are
  unchanged. A separate source-audit policy stamp preserves comparison provenance
  without invalidating every semantic input.

### Capability forms and specification actions — 2026-11-24

Local implementation; not deployed.

- Keep **Read PDF & review** and **Gap report** prominent in saved specification
  details. Move metadata editing and removal to **Specification actions**, retaining
  version checks and explicit removal confirmation.
- Remove the duplicate analysis link from an empty specification and replace the
  upload dialog's disabled OCR button with a concise availability note.
- Show saved capability fields as compact value/context summaries, with editing
  available on expansion. New fields open immediately; collapsing retains drafts.
- Reopen invalid fields on every failed save, including repeated identical errors.
  Existing field validation, normalization, saved values and save locking are unchanged.
- Report review history continues to use the existing SQL event store; this update
  adds no backend API, schema, authentication, model or deployment change.
- Validated 56 affected desktop/narrow browser cases across the regression and final
  reruns, plus two existing SQL review-persistence checks. Final production build and
  formatting passed. Screenshots use disposable fixtures; user acceptance is pending.

### Review workspace and decision history — 2026-11-24

Local implementation; not deployed. This is a first acceptance slice of the client
feedback, not a formal approval or customer-communication workflow.

- Keep confirmation and editing prominent in PDF review; move rerun, OCR,
  missing-test, batch-review and remove/restore operations into secondary menus.
- Combine source evidence and review reasons in one compact expandable area.
  Keep the review status visible and retain all recorded reasons.
- Use a compact PDF toolbar in source review. Other PDF viewers keep their existing
  controls and fitting behavior.
- Add direct **Needs attention / All tests** views in the current gap report.
  Detailed status filters and ordering remain available on demand. A reviewed gap
  still belongs in Needs attention; no machine outcome is changed.
- Show existing saved review decisions, notes, authors and timestamps through an
  expandable **Review history** view for the current test/assessment. Reads are lazy,
  stale responses are rejected, and viewing history does not run a model.
- Keep pending review saves locked and preserve drafts against accidental backdrop
  clicks. Consume secondary-action requests so cancelled dialogs do not reopen after
  switching documents.
- Verified 160 desktop/narrow browser checks across the regression and affected-case
  rerun; eight desktop-only layout checks were skipped on narrow screens. Production
  build and changed-file formatting passed. Browser fixtures prohibit live model calls;
  these checks do not measure AI accuracy. User visual acceptance is still pending.

### Client feedback refinements — 2026-11-24

Deployment status: implemented and validated locally; not deployed. The existing
hosted pilot and its lab workspaces have not been changed by this update.

### Changed

- Use **Test specification** for customer requirements and **Measurements & reporting**
  for lab output declarations. Internal JSON/API field names remain compatible.
- Make saved-specification selectors searchable in PDF review and Gap report.
- Add Previous/Next pending navigation beside the test selector, with queue position
  and a completion state when no tests remain to review.
- Show the actual source PDF page in the correction/add-missing-test editor, with
  zoom controls and an original-PDF fallback when a preview cannot be displayed.
- Clarify PDF selection, selected/uploaded state, and Replace/Remove actions before
  upload. Preserve entered customer and specification details when changing a file.
- Group capability form errors, focus the first invalid field, show conditional
  required-field labels, and confirm successful saves with a notification.
- Protect drafts from accidental backdrop clicks and lock controls during saves.

### Fixed

- Pending/All filtering no longer retains a confirmed item in the pending queue or
  silently switches back to All when the queue is empty.
- Cancelled document navigation no longer prevents Pending/All selection after
  returning to the original document with the browser Back button.
- Removing or replacing custom method rows clears obsolete row-number errors.
- Single- and multi-item batch reviews prevent duplicate submissions and preserve
  failed-save drafts for an explicit retry.
- Extraction and matching views avoid duplicate progress for the same task while
  retaining background tracking and connection warnings.

### Validation and scope

- Validated desktop and narrow-screen upload, editing, navigation, source review,
  report/export and authenticated lab-isolation workflows. The final cross-review
  rerun passed 44 affected browser checks; 32 PDF unit tests also passed.
- TypeScript/Vite production build, changed-file formatting and static checks passed.
  Browser fixtures prohibit live model calls; these checks do not measure AI accuracy.
- No extraction prompt, matching policy, database schema, authentication policy or
  deployment configuration change. Historical PDF snapshots are not rewritten.
- Normal **Save under** selection is tested. The originally reported unresponsive
  interaction has not been reproduced, so its root cause is not claimed as fixed.
- Full review-layout redesign, global cross-spec worklists, creation-time sorting,
  method-library expansion, approvals and historical-review reuse remain separate work.
