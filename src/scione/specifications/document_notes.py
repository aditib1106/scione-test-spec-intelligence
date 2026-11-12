"""Persist human acknowledgement of document notes, never scientific approval.

The note content stays derived from immutable source evidence. An acknowledgement
is bound to that PDF, extraction run and reviewed-facts revision; it does not
change a test's review status, normalize a fact, or establish lab support.
"""

from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, insert, select
from sqlalchemy.exc import IntegrityError

from scione.library.store import (
    LibraryConflict,
    LibraryStore,
    LibraryStoreError,
    digest,
    now,
    schema_versions,
)
from scione.specifications.context_coverage import source_context_coverage_audits
from scione.specifications.extraction import coverage_issues
from scione.specifications.review import document_review_issues

_COMPONENT = "document_note_reviews"
metadata = MetaData()
note_reviews = Table(
    "document_note_review_events",
    metadata,
    Column("workspace_id", String(100), primary_key=True),
    Column("document_id", String(16), primary_key=True),
    Column("source_fingerprint", String(64), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("created_at", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
)


def _document_information_note(issue: str) -> dict:
    """Translate known extraction bookkeeping, retaining the untouched audit text.

    Source-control or scientific statements outside these narrow patterns stay
    visible verbatim. This changes presentation only, never warning ownership.
    """
    title, detail = "Document information", issue
    if issue == (
        "Source-window extractions returned differing document metadata; "
        "the first window's metadata was retained and must be checked against the source."
    ):
        title = "Document details"
        detail = (
            "Different parts of the PDF provided different document details. "
            "Check the title, document number, and revision against the source."
        )
    elif (
        re.search(r"\bapproval[- ](?:package|type)\b", issue, re.I)
        and re.search(r"\bsummar(?:y|ies|izes)\b|administrative workflow", issue, re.I)
        and not re.search(r"[°±≤≥<>%]|\b\d+\s*(?:mm|MPa|kg|hours?|days?|min)\b", issue)
    ):
        title = "Approval packages"
        detail = (
            "The PDF groups tests into approval packages. Check which requested "
            "tests each package refers to."
        )
    else:
        replacements = {
            "context pages": "other PDF pages",
            "not supplied as primary here": "described elsewhere in the PDF",
            "no separate test row was emitted": "it was not added as a separate test",
            "no separate test rows were emitted": "they were not added as separate tests",
            "this chunk": "this extracted section",
        }
        for before, after in replacements.items():
            detail = detail.replace(before, after)
    return {
        "kind": "document_info", "title": title, "detail": detail,
        "original_issue": issue,
    }


def upgrade_document_notes_schema(library: LibraryStore) -> None:
    """Add only this component; do not rewrite existing source or review tables."""
    with library.schema_upgrade() as engine:
        with engine.connect() as connection:
            version = connection.execute(select(schema_versions.c.version).where(
                schema_versions.c.component == _COMPONENT
            )).scalar_one_or_none()
        if version not in (None, 1):
            raise LibraryStoreError("The document-note schema needs a compatible application.")
        if version is None:
            metadata.create_all(engine)
            try:
                with engine.begin() as connection:
                    connection.execute(insert(schema_versions).values(
                        component=_COMPONENT, version=1
                    ))
            except IntegrityError:
                with engine.connect() as connection:
                    current = connection.execute(select(schema_versions.c.version).where(
                        schema_versions.c.component == _COMPONENT
                    )).scalar_one()
                if current != 1:
                    raise LibraryStoreError(
                        "The document-note schema changed concurrently."
                    ) from None


def _source_notes(workspace, document_id, run=None):
    document = workspace.current_document(document_id).document
    if run is not None:
        # A comparison may carry transient physical normalizations. They are not
        # reviewed facts and must not create a different acknowledgement target.
        run = workspace.specs.load_extraction(document_id, run.run_id)
    run, _row_reviews, revision = workspace.effective_source(document_id, run)
    notes = []
    if run is not None:
        for cue in source_context_coverage_audits(run.extraction, document):
            notes.append({
                "kind": "context", "title": cue["label"],
                "detail": (
                    "Check whether this description applies to the extracted tests. "
                    "It may already be represented using different wording."
                ),
                "page": cue["page"], "quote": cue["value"],
            })
        for issue in coverage_issues(run.extraction, document):
            if issue.startswith("Check source context:"):
                continue  # Context cues have their own readable, physical-source notes above.
            references = issue.partition("requested_methods: ")[2].partition(". These")[0]
            notes.append({
                "kind": "reference", "title": "Source references",
                "detail": (
                    f"The PDF also mentions {references}. Check whether these are additional "
                    "tests, shared procedures, or background references."
                ) if references else "Check additional method references in the PDF.",
            })
        for issue in document_review_issues(run, document):
            notes.append(_document_information_note(issue))
    # IDs identify the actual note, not a model-generated row or arbitrary user prose.
    unique = {}
    for note in notes:
        identifier = digest(note)
        unique.setdefault(identifier, {"id": identifier, **note})
    notes = list(unique.values())
    signature = digest({
        "pdf_sha256": document.sha256,
        "run_id": run.run_id if run else None,
        "facts_revision": revision.revision_id if revision else None,
        "extraction": run.extraction.model_dump(mode="json") if run else None,
        "notes": notes,
    })
    return signature, notes


def _latest(workspace, document_id, signature):
    store = workspace.library.store
    upgrade_document_notes_schema(store)
    with store.engine.connect() as connection:
        return connection.execute(select(note_reviews.c.payload).where(
            note_reviews.c.workspace_id == workspace.labs.workspace_id,
            note_reviews.c.document_id == document_id,
            note_reviews.c.source_fingerprint == signature,
        ).order_by(note_reviews.c.version.desc()).limit(1)).scalar_one_or_none()


def _present(signature, notes, saved):
    reviewed = saved.get("reviewed_notes", {}) if saved else {}
    presented = [
        {**note, "reviewed": note["id"] in reviewed,
         **({"reviewed_at": reviewed[note["id"]]["created_at"]} if note["id"] in reviewed else {})}
        for note in notes
    ]
    reviewed_count = sum(note["reviewed"] for note in presented)
    return {
        "source_fingerprint": signature,
        "version": saved["version"] if saved else 0,
        "notes": presented,
        "pending_count": len(presented) - reviewed_count,
        "reviewed_count": reviewed_count,
    }


def document_notes_view(workspace, document_id: str, *, run=None) -> dict:
    """Read current note acknowledgements; never require or invoke a model."""
    signature, notes = _source_notes(workspace, document_id, run)
    saved = _latest(workspace, document_id, signature) if notes else None
    return _present(signature, notes, saved)


def save_document_notes_review(
    workspace, document_id: str, source_fingerprint: str, expected_version: int,
    note_ids: list[str], reviewed: bool, actor: str,
) -> dict:
    """Append a checked/unchecked event for exact current notes under a source lock."""
    if (type(expected_version) is not int or expected_version < 0
            or type(reviewed) is not bool or not isinstance(actor, str) or not actor.strip()):
        raise LibraryConflict("Check the document-note review before saving.")
    if not isinstance(note_ids, list) or not note_ids or any(
        not isinstance(identifier, str) for identifier in note_ids
    ):
        raise LibraryConflict("Select the document notes that you checked.")
    with workspace.specs.write_guard():
        signature, notes = _source_notes(workspace, document_id)
        if signature != source_fingerprint:
            raise LibraryConflict("The source notes changed. Reload before saving your check.")
        allowed = {note["id"] for note in notes}
        if not set(note_ids) <= allowed:
            raise LibraryConflict("Select notes from the current source document.")
        previous = _latest(workspace, document_id, signature)
        if (previous["version"] if previous else 0) != expected_version:
            raise LibraryConflict("These note checks changed. Reload before saving.")
        reviewed_notes = dict(previous["reviewed_notes"]) if previous else {}
        selected = set(note_ids)
        already_desired = (
            selected <= reviewed_notes.keys() if reviewed
            else selected.isdisjoint(reviewed_notes)
        )
        if already_desired:
            return _present(signature, notes, previous)
        timestamp = now()
        for identifier in selected:
            if reviewed:
                reviewed_notes[identifier] = {"actor": actor, "created_at": timestamp}
            else:
                reviewed_notes.pop(identifier, None)
        event = {
            "event_id": str(uuid4()), "document_id": document_id,
            "source_fingerprint": signature, "version": expected_version + 1,
            "actor": actor, "created_at": timestamp, "note_ids": list(dict.fromkeys(note_ids)),
            "notes": notes,
            "reviewed": reviewed, "reviewed_notes": reviewed_notes,
            "meaning": "User checked the document note; test and lab assessments are unchanged.",
        }
        try:
            with workspace.library.store.engine.begin() as connection:
                connection.execute(insert(note_reviews).values(
                    workspace_id=workspace.labs.workspace_id,
                    document_id=document_id, source_fingerprint=signature,
                    version=event["version"], created_at=timestamp, payload=event,
                ))
        except IntegrityError:
            raise LibraryConflict("These note checks changed. Reload before saving.") from None
        return _present(signature, notes, event)
