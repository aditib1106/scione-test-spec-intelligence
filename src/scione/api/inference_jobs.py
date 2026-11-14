"""Bounded, process-local transport for explicitly admitted inference jobs.

This is a single-worker pilot transport, not a durable queue. A restart loses
receipts; callers must check saved results and never automatically resubmit.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from time import monotonic
from uuid import UUID

from fastapi import HTTPException

from scione.api.inference_gate import InferenceBusyError, InferenceGate
from scione.inference_progress import progress_snapshot


@dataclass
class _Job:
    operation_id: str
    document_id: str
    kind: str
    workspace_id: str | None
    client_request_id: str | None
    input_digest: str
    started_at: str
    progress: dict
    state: str = "running"
    finished: float | None = None
    result: dict | None = None
    error: dict | None = None


def _request_id(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Use a valid inference request identifier.") from None


def _public_failure(exc: BaseException, status: int | None = None) -> dict:
    # Even HTTPException.detail can include a source fragment or SDK/SQL error.
    # Preserve the HTTP status, never store/return arbitrary exception prose.
    status = status or (exc.status_code if isinstance(exc, HTTPException) else 500)
    detail = {
        400: "The analysis could not complete. Check the saved document before trying again.",
        403: "This account cannot complete the requested action.",
        404: "The saved document is no longer available.",
        409: "Inputs changed. Reload the saved document or report before trying again.",
        422: "The request could not be accepted. Check the saved document and settings.",
        502: "The analysis service could not complete this request.",
        503: "Analysis is temporarily unavailable. Check saved results before trying again.",
    }.get(status, "The analysis could not complete.")
    original = exc.detail if isinstance(exc, HTTPException) else None
    kind = original.get("provider_failure_kind") if isinstance(original, dict) else None
    if not isinstance(kind, str) or kind not in {
        "timeout", "network", "http", "invalid_request", "invalid_response"
    }:
        kind = None
    message = original.get("message") if isinstance(original, dict) else original
    if kind is None and isinstance(message, str) and re.search(
        r"(?:Sail|Fireworks) request timed out; no automatic retry was made\.", message
    ):
        kind = "timeout"
    if kind is not None:
        detail = {
            "timeout": "The AI service timed out before this analysis could finish.",
            "network": "The connection to the AI service failed.",
            "http": "The AI service did not accept or complete the request.",
            "invalid_request": "The AI service could not accept the analysis request.",
            "invalid_response": "The AI response could not be validated.",
        }[kind]
    elif isinstance(message, str) and message.startswith(
        "This document is too long for the current analysis limit."
    ):
        detail = "The document exceeds the current analysis limit. No AI request was sent."
    result = {"status": status, "detail": detail + (
        " Earlier saved results are preserved. No automatic retry was started."
    )}
    if kind is not None:
        result["failure_kind"] = kind
    return result


class InferenceJobs:
    """Reserve before acknowledgement; run at most two jobs without a queue."""

    def __init__(
        self, gate: InferenceGate, *, max_running: int = 2,
        max_finished: int = 128, ttl_seconds: float = 3600,
        failure_status: Callable[[BaseException], int] | None = None,
    ):
        if type(max_running) is not int or not 1 <= max_running <= 2:
            raise ValueError("Choose one or two bounded inference workers.")
        if type(max_finished) is not int or not 1 <= max_finished <= 128 or ttl_seconds <= 0:
            raise ValueError("Choose bounded finished-operation retention.")
        self.gate = gate
        self.max_running = max_running
        self.max_finished = max_finished
        self.ttl_seconds = ttl_seconds
        self.failure_status = failure_status
        self._lock = RLock()
        self._jobs: dict[str, _Job] = {}
        self._closed = False
        self._executor = ThreadPoolExecutor(
            max_workers=max_running, thread_name_prefix="inference-job"
        )

    def _prune(self) -> None:
        finished = sorted(
            (job for job in self._jobs.values() if job.finished is not None),
            key=lambda job: job.finished,
        )
        expired = {
            job.operation_id for job in finished
            if monotonic() - job.finished >= self.ttl_seconds
        }
        expired.update(job.operation_id for job in finished[:max(
            0, len(finished) - self.max_finished
        )])
        for identifier in expired:
            self._jobs.pop(identifier, None)

    @staticmethod
    def _view(job: _Job) -> dict:
        result = {
            "operation_id": job.operation_id, "document_id": job.document_id,
            "kind": job.kind, "state": job.state, "started_at": job.started_at,
            "request_id": job.client_request_id,
        }
        if job.state == "running":
            result["progress"] = deepcopy(job.progress)
        elif job.state == "completed":
            result["result"] = deepcopy(job.result)
            result["result_url"] = (
                f"/api/specifications/{job.document_id}"
                + ("/matching" if job.kind == "matching" else "")
            )
        else:
            result["error"] = deepcopy(job.error)
        return result

    def status(self, operation_id: str, *, workspace_id: str | None) -> dict:
        with self._lock:
            self._prune()
            job = self._jobs.get(operation_id)
            if job is None or job.workspace_id != workspace_id:
                raise HTTPException(404, "This operation was not found. Check saved results.")
            return self._view(job)

    def latest(self, document_id: str, *, workspace_id: str | None) -> dict | None:
        with self._lock:
            self._prune()
            jobs = [job for job in self._jobs.values()
                    if job.workspace_id == workspace_id and job.document_id == document_id]
            return self._view(jobs[-1]) if jobs else None

    @staticmethod
    def _receipt(job: _Job) -> dict:
        return {
            "operation_id": job.operation_id, "document_id": job.document_id,
            "kind": job.kind, "state": job.state,
            "status_url": f"/api/operations/{job.operation_id}",
            "request_id": job.client_request_id,
        }

    def submit(
        self, *, document_id: str, key: str, kind: str, workspace_id: str | None,
        callback: Callable[[Callable[[dict], None]], dict], input_digest: str,
        client_request_id: str | None = None,
    ) -> dict:
        request_id = _request_id(client_request_id)
        context = copy_context()
        with self._lock:
            self._prune()
            if self._closed:
                raise HTTPException(503, "Analysis is shutting down. No work was started.")
            if request_id is not None:
                existing = next((job for job in self._jobs.values()
                                 if job.workspace_id == workspace_id
                                 and job.client_request_id == request_id), None)
                if existing is not None:
                    if (existing.document_id, existing.kind, existing.input_digest) != (
                        document_id, kind, input_digest
                    ):
                        raise HTTPException(409, "This request identifier belongs to other inputs.")
                    return self._receipt(existing)
            running = [job for job in self._jobs.values() if job.state == "running"]
            if len(running) >= self.max_running:
                raise InferenceBusyError("Analysis is busy. No additional work was started.")
            if any(job.workspace_id == workspace_id for job in running):
                raise InferenceBusyError("An analysis is already running for your lab.")
            lease = self.gate.claim(
                key, "analysis" if kind == "extraction" else "matching",
                workspace_id=workspace_id,
            )
            notify = lease.__enter__()
            try:
                active = self.gate.snapshot(
                    workspace_id=workspace_id, document_id=document_id
                )[-1]
                job = _Job(
                    operation_id=active["operation_id"], document_id=document_id,
                    kind=kind, workspace_id=workspace_id, client_request_id=request_id,
                    input_digest=input_digest, started_at=datetime.now(UTC).isoformat(),
                    progress=progress_snapshot("planning"),
                )
                self._jobs[job.operation_id] = job
                receipt = self._receipt(job)

                def publish(event):
                    notify(event)
                    current = self.gate.snapshot(
                        workspace_id=workspace_id, document_id=document_id
                    )
                    if current and current[0]["operation_id"] == job.operation_id:
                        with self._lock:
                            job.progress = current[0]["progress"]

                def execute():
                    try:
                        result = context.run(callback, publish)
                        if not isinstance(result, dict) or result.get("status") in {
                            "failed", "failure"
                        }:
                            raise HTTPException(502, "Analysis did not complete.")
                        run = result.get("run") if isinstance(result, dict) else None
                        run_id = result.get("run_id") if isinstance(result, dict) else None
                        if isinstance(run, dict):
                            run_id = run.get("run_id")
                        if not isinstance(run_id, str) or not re.fullmatch(
                            r"[A-Za-z0-9_-]{1,128}", run_id
                        ):
                            raise HTTPException(500, "The completed result is unavailable.")
                        outcome = {"run_id": run_id, "status": "completed",
                                   "cached": bool(result.get("cached", False))}
                        error = None
                    except BaseException as exc:
                        try:
                            status = self.failure_status(exc) if self.failure_status else None
                        except Exception:
                            status = 500
                        outcome, error = None, _public_failure(exc, status)
                    finally:
                        lease.__exit__(None, None, None)
                    with self._lock:
                        job.result, job.error = outcome, error
                        job.state = "completed" if error is None else "failed"
                        job.finished = monotonic()
                        self._prune()

                self._executor.submit(execute)
                return receipt
            except BaseException:
                if "job" in locals():
                    self._jobs.pop(job.operation_id, None)
                lease.__exit__(None, None, None)
                raise

    def shutdown(self) -> None:
        """Finish admitted jobs before their tenant stores are closed; never retry."""
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)
