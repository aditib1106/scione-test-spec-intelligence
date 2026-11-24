# Storage lifecycle and write amplification

This describes the local November 24 update. It is not a record of deployment or
production cleanup. Keep an independent backup of both MySQL and the application
volume before deploying storage changes.

## What is stored

MySQL contains current lab declarations, normalized searchable fields, specification
metadata, immutable extraction/assessment indexes, manual review events and export
snapshots. Each lab has a separate business database. The application volume contains
original PDFs and immutable extraction/matching artifacts. These are separate stores;
the size of a developer's `local_data` directory is not the hosted MySQL size.

Some information deliberately exists in several forms: the original source, a current
queryable view and the historical inputs used by an assessment. Do not remove an old
lab revision or machine assessment just because it is absent from the current UI.

## Display-only capability chronology

The local follow-up adds `lab_capability_creation`, keyed by workspace and capability
ID, under its own `lab_capability_creation=1` schema marker. It does not change
`lab_storage=2`, extend `LabProfile`, reorder its arrays, enter matching fingerprints,
or rewrite full lab revisions. An existing v2 database gains only the empty sidecar
and component marker; initial/imported capabilities receive no guessed dates.

On a successful version-checked save, newly added current IDs receive a server UTC
timestamp in the same transaction as the lab update and immutable revision. A stale
save or later transaction failure leaves neither a declaration update nor a timestamp.
Edits preserve the first timestamp. Deletions keep that timestamp, or a null marker for
an undated legacy ID, so subsequent restoration preserves the original known/unknown
date. The public display map excludes null markers and removed IDs. These records are
small chronology hints, not a replacement for revision history or historical-date
reconstruction.

An overview's display hint is bound to the profile ID/version observed with current
entries in one SQL read. A race yields no hint; the UI then preserves stored order.
Only a copied display list is sorted, with known dates first and unknown/equal dates
stable. Embedded reset moves the whole database, including this table and its marker,
into the recovery backup; external-database reset remains unavailable.

The migration is additive and runs through existing lab-upgrade entry points. Older
binaries that otherwise support the current database can ignore this sidecar, but do
not record dates for their own changes. This does not remove the export-reference
rollback boundary below: reverting to an arbitrary older image is still unsafe.

## Changes that reduce future writes

1. **Incremental current lab rows.** A save compares current entry/claim columns and
   inserts, updates or removes only changed rows within the existing version-checked
   transaction. A price-only edit updates one entry and no claim rows. Profile-only
   edits do not rewrite capability rows. Full lab revisions remain intact.
2. **Lossless export references.** A review-only export stores a reference to the
   same-document immutable machine assessment plus a frozen copy of review decisions,
   customer clarifications and export metadata. It includes small summary projections
   for existing monitoring queries. The public JSON remains complete.
3. **Lazy PDF creation.** The existing first-rendered PDF cache remains unchanged.
   Saving a review or question does not create PDF bytes for every intermediate state.

An export is compacted only when the base exists and its entire nonmanual body is
identical. Unknown differences keep the full snapshot. Reads validate the base and
reconstructed snapshot digests; missing, foreign, chained or corrupted references fail
closed. The public API never exposes an internal reference as an assessment.

A regression fixture with 25 evidence-rich tests and 12 successive review snapshots
stores less than one tenth of the JSON bytes that full copies would require. This is
an export-JSON measurement, not a claim that the whole MySQL volume shrinks by 90%.

## History, removal and rollback

- Specification removal remains archival. Reuploading identical bytes can restore
  the prior record; removal is not a physical erasure or fresh-analysis command.
- Removing a current capability removes its current entry/claims. Earlier assessment
  inputs and lab revisions remain available.
- Questions and replies are append-only records bound to the lab, document, extraction
  run and test. A reply cannot silently turn a gap into a match. Concurrent edits use
  thread versions; retrying the same save request does not insert another event.
- New assessment reviews capture a server-owned readable author label beside the
  stable actor ID. Existing review JSON is not backfilled, and carried-forward events
  retain their original label or absence. Renaming an account does not rewrite old
  review records or export snapshots. This adds no SQL column or schema version.
- The UI checks delayed review/reply save responses against the current assessment
  and saved manual versions. It reloads a complete saved report when needed, rather
  than combining manual overlays with an export ID from a different snapshot. This
  guard does not modify stored events or resolve scientific findings.
- The clarification table is an additive, idempotent schema component. Existing rows
  are not transformed during its initialization.
- Existing full assessment/export rows remain readable. Old PDF snapshots are stable.
  **A binary predating the export-reference reader cannot read newly compacted exports.**
  A rollback must retain the reader, or restore the matching database/application
  backup. Do not roll back only the image to an arbitrary old version.
- No automated history purge, `OPTIMIZE TABLE`, database/volume deletion, redo/undo
  cleanup or binary-log cleanup is introduced. A future retention policy must protect
  all referenced source runs, capability revisions, machine bases and delivered exports.

## Understanding a growing MySQL volume

The read-only Railway volume measurement on November 24 was approximately 1.01 GB
used out of 5 GB provisioned. This alone does not distinguish business data from
InnoDB allocation, indexes or engine/log files. Table-level attribution was not obtained
in that read-only check; do not label the difference as application JSON or binary logs
without inspecting it.

These changes reduce new write amplification. They do not rewrite old snapshots or
promise immediate recovery of already allocated disk space.

For an authorized operator session, collect metadata only before proposing cleanup:

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, DATA_FREE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
ORDER BY DATA_LENGTH + INDEX_LENGTH DESC;

SHOW VARIABLES WHERE Variable_name IN
('log_bin', 'binlog_expire_logs_seconds', 'innodb_file_per_table',
 'innodb_redo_log_capacity', 'innodb_undo_log_truncate');
```

Also inspect the sizes of the database directory and engine files through the existing
private operator connection. Avoid printing row contents, database URLs or credentials.
MySQL's size columns describe allocated/estimated storage, and shared tablespaces make
`DATA_FREE` unsuitable for blindly summing across all tables. See the
[MySQL table metadata documentation](https://dev.mysql.com/doc/refman/8.4/en/information-schema-tables-table.html)
and [InnoDB disk management](https://dev.mysql.com/doc/refman/8.4/en/innodb-disk-management.html).

If history retention is later approved, first inventory dependencies and retained
exports, take a restorable backup, and test cleanup on a restored copy. Retention and
physical space reclamation are separate operational decisions.
