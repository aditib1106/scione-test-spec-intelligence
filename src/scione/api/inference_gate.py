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
    def __init__(self) -> None:
        self._lock = Lock()
        self._active: dict[str, str] = {}

    @contextmanager
    def claim(self, document_id: str, operation: str) -> Iterator[None]:
        with self._lock:
            active = self._active.get(document_id)
            if active is not None:
                raise InferenceBusyError(
                    f"An AI {active} is already running for this document. "
                    "Wait for it to finish before starting another operation. "
                    "No additional model call was started."
                )
            self._active[document_id] = operation
        try:
            yield
        finally:
            with self._lock:
                self._active.pop(document_id, None)
