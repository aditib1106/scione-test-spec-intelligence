# Changelog

Changes are recorded here separately from setup and usage instructions in the README.
An entry under **Unreleased** describes repository changes, not the currently hosted
version. On deployment, move verified changes into a dated release entry and record
the deployed commit and environment. Do not include credentials or private account data.

## Unreleased

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
