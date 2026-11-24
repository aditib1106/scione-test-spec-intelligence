# Specification version dates

Saved customer specifications use the date printed on the specification as their
version label. A version belongs to one customer/specification family and retains
its original PDF, extraction runs, reviews and reports.

`specification_versions.specification_date` is a nullable `VARCHAR(10)` containing
a validated ISO calendar date (`YYYY-MM-DD`). Library schema version 2 adds this
column to existing SQLite/MySQL databases. Startup performs the additive migration;
existing rows, IDs, relationships, optimistic edit counters and original revision
labels remain unchanged. Repeated startup does not repeat the alteration.

Existing documents start with a null date. The system does not substitute their
upload/creation timestamp or convert an `Issue A`/`Issue B` label into a date.
The interface displays **Date not provided** until the lab enters a known printed
date through **Edit details**. The old `revision` value remains source provenance.

The Library metadata endpoint accepts:

```json
{
  "company_name": "Example customer",
  "specification_id": "SPEC-100",
  "title": "Finish requirements",
  "revision": "Original printed revision",
  "specification_date": "2026-11-04",
  "family_id": "existing-family-id",
  "expected_version": 3
}
```

The API rejects impossible or non-ISO dates. An omitted `specification_date`
preserves its existing value for older clients; explicit `null` clears it.
`expected_version` is the internal optimistic edit counter, distinct from the
customer's specification date. Stale updates continue to return a conflict.

**Save under** links a newly uploaded document to an existing family; it does not
replace older PDFs or copy their dates. Each version retains its own date. The UI
orders dated versions newest first and keeps undated versions explicitly undated.
Upload timestamps only resolve ordering ties and remain separate metadata.

The library shows one entry per specification family. Its **Versions** control opens
a dated list with each original filename and its existing extraction/review status.
The details page provides the same list for switching the viewed version. Searching
a filename or date from an older version still finds the family. The **Viewing** label
marks the version currently open in the browser; the newest date is not an approval
or an active-version declaration.

Switching versions does not copy confirmations, rerun a model, overwrite a source
file or modify the previous assessment. Removing a version archives that version;
the remaining family members stay accessible. Company boundaries and optimistic
metadata-edit checks still apply. This UI update adds no database schema.

Previously exported report snapshots remain immutable. Changing metadata creates
a new current comparison when needed; it does not silently rewrite an older PDF.
