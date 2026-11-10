"""Current upload names as a view over immutable source artifacts.

Uploading identical bytes can reuse their stored source without making a later
uploader see an old filename. Original metadata and inference runs remain intact;
name changes are separately recorded, then applied only to detached read views.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from scione.specifications.store import FileSpecStore, SpecStoreError, StoredSpecification


class UploadNameEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    event_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    document_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    filename: str
    created_at: datetime


class UploadNameSpecStore(FileSpecStore):
    """Use the most recently uploaded name without rewriting original evidence."""

    def import_pdf(self, filename: str, data: bytes) -> StoredSpecification:
        stored = super().import_pdf(filename, data)
        if stored.original_name == filename:
            return stored
        # The source write lock has been released by import_pdf. Name updates
        # share that same guard with review/source writes and are independently
        # atomic. Existing immutable source/model records are never renamed.
        with self._locked():
            current = self.load_document(stored.document_id)
            if current.original_name != filename:
                directory = self._document_directory(stored.document_id)
                old = self._name_event(current)
                event = UploadNameEvent(
                    version=old.version + 1 if old else 1,
                    event_id=uuid4().hex,
                    document_id=current.document_id,
                    source_sha256=current.document.sha256,
                    filename=filename,
                    created_at=datetime.now(UTC),
                )
                history = directory / "upload_names"
                self._check_directory(history)
                history.mkdir(exist_ok=True, mode=0o700)
                self._write_model(history / f"{event.event_id}.json", event)
                self._write_model(directory / "current_upload_name.json", event)
        return self.load_document(stored.document_id)

    def _name_event(self, stored: StoredSpecification) -> UploadNameEvent | None:
        directory = self._document_directory(stored.document_id)
        path = directory / "current_upload_name.json"
        if not path.exists() and not path.is_symlink():
            return None
        try:
            event = UploadNameEvent.model_validate_json(self._read_bytes(path))
            self._validate_filename(event.filename)
            if (
                event.document_id != stored.document_id
                or event.source_sha256 != stored.document.sha256
            ):
                raise SpecStoreError("The upload name does not match the saved source.")
            historical = UploadNameEvent.model_validate_json(
                self._read_bytes(directory / "upload_names" / f"{event.event_id}.json")
            )
            if historical != event:
                raise SpecStoreError("The current upload name has no matching audit record.")
            return event
        except (OSError, ValueError) as exc:
            raise SpecStoreError("The saved upload name could not be read safely.") from exc

    def load_document(self, document_id: str) -> StoredSpecification:
        stored = super().load_document(document_id)
        event = self._name_event(stored)
        if event is None:
            return stored
        return replace(
            stored,
            original_name=event.filename,
            document=stored.document.model_copy(update={"file_name": event.filename}),
        )
