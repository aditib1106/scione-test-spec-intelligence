"""Prevent concurrent paid operations for a document in this server process.

This is deliberately not a background job queue or a remote cancellation API.
The current pilot runs one worker. A multiworker deployment must replace this
process-local gate with a shared database lease before promising global exclusion.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from uuid import uuid4

from scione.inference_progress import STAGE_MESSAGES, progress_snapshot


class InferenceBusyError(RuntimeError):
    """A document already has an explicitly requested inference in flight."""


class InferenceGate:
    def __init__(
        self, *, max_concurrent: int | None = None, max_per_workspace: int | None = None
    ) -> None:
        self._lock = Lock()
        self._active: dict[str, str] = {}
        self._owners: dict[str, str | None] = {}
        self._details: dict[str, dict] = {}
        self.max_concurrent = max_concurrent
        self.max_per_workspace = max_per_workspace

    @contextmanager
    def claim(
        self, document_id: str, operation: str, *, workspace_id: str | None = None
    ) -> Iterator[Callable[[dict], None]]:
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
            prefix = f"{workspace_id}:" if workspace_id is not None else ""
            operation_id = uuid4().hex
            self._details[document_id] = {
                "operation_id": operation_id,
                "document_id": document_id.removeprefix(prefix) if prefix else document_id,
                "kind": "extraction" if operation == "analysis" else operation,
                "started_at": datetime.now(UTC).isoformat(),
                "started_monotonic": monotonic(),
                "progress": progress_snapshot("planning"),
            }

        def report_progress(event):
            # Only this admitted operation can publish to its own tenant/key.
            # Ignore caller prose/percent: the public message is a fixed label.
            try:
                with self._lock:
                    detail = self._details.get(document_id)
                    if (detail is None or detail["operation_id"] != operation_id
                            or self._owners.get(document_id) != workspace_id):
                        return
                    stage = event.get("stage")
                    completed = event.get("completed_steps")
                    total = event.get("total_steps")
                    current = detail["progress"]
                    if (stage not in STAGE_MESSAGES or type(completed) is not int
                            or completed < current["completed_steps"]
                            or (total is not None and (type(total) is not int or total < completed))
                            or (current["total_steps"] is not None
                                and total != current["total_steps"])):
                        return
                    detail["progress"] = progress_snapshot(stage, completed, total)
            except Exception:
                pass
        try:
            yield report_progress
        finally:
            with self._lock:
                detail = self._details.get(document_id)
                if detail is not None and detail["operation_id"] == operation_id:
                    self._active.pop(document_id, None)
                    self._owners.pop(document_id, None)
                    self._details.pop(document_id, None)

    def snapshot(
        self, *, workspace_id: str | None = None, document_id: str | None = None
    ) -> list[dict]:
        """Read active work for exactly one server-selected workspace.

        ``None`` selects legacy local work, never every tenant. Returned copies
        contain no internal namespace, filename, request body or provider data.
        A snapshot is observation only: it cannot queue, cancel or resume work.
        """
        with self._lock:
            now = monotonic()
            result = []
            for key, details in self._details.items():
                if self._owners[key] != workspace_id:
                    continue
                if document_id is not None and details["document_id"] != document_id:
                    continue
                result.append({
                    "operation_id": details["operation_id"],
                    "document_id": details["document_id"],
                    "kind": details["kind"],
                    "started_at": details["started_at"],
                    "elapsed_seconds": round(max(0.0, now - details["started_monotonic"]), 3),
                    "state": "running",
                    "progress": dict(details["progress"]),
                })
            return sorted(result, key=lambda row: (row["started_at"], row["operation_id"]))
