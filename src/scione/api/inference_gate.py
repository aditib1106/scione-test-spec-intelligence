"""Prevent concurrent paid operations for a document in this server process.

This is deliberately not a background job queue or a remote cancellation API.
The current pilot runs one worker. A multiworker deployment must replace this
process-local gate with a shared database lease before promising global exclusion.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock


class InferenceBusyError(RuntimeError):
    """A document already has an explicitly requested inference in flight."""


class InferenceGate:
    def __init__(
        self, *, max_concurrent: int | None = None, max_per_workspace: int | None = None
    ) -> None:
        self._lock = Lock()
        self._active: dict[str, str] = {}
        self._owners: dict[str, str | None] = {}
        self.max_concurrent = max_concurrent
        self.max_per_workspace = max_per_workspace

    @contextmanager
    def claim(
        self, document_id: str, operation: str, *, workspace_id: str | None = None
    ) -> Iterator[None]:
        with self._lock:
            active = self._active.get(document_id)
            if active is not None:
                raise InferenceBusyError(
                    f"An AI {active} is already running for this document. "
                    "Wait for it to finish before starting another operation. "
                    "No additional model call was started."
                )
            if self.max_concurrent is not None and len(self._active) >= self.max_concurrent:
                raise InferenceBusyError(
                    "The analysis service is busy. "
                    "Please try again after the current work finishes."
                )
            if (
                workspace_id is not None and self.max_per_workspace is not None
                and sum(owner == workspace_id for owner in self._owners.values())
                >= self.max_per_workspace
            ):
                raise InferenceBusyError(
                    "An analysis is already running for your lab. Wait for it to finish "
                    "before starting another."
                )
            self._active[document_id] = operation
            self._owners[document_id] = workspace_id
        try:
            yield
        finally:
            with self._lock:
                self._active.pop(document_id, None)
                self._owners.pop(document_id, None)
