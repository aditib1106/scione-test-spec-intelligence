"""Best-effort instrumentation: metrics must never change business outcomes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from scione.monitoring.store import MonitoringStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperationHandle:
    store: MonitoringStore
    identifier: str
    started: float


def start_operation(workspace, kind: str, document_id: str, *, page_count=None):
    started = time.monotonic()
    try:
        store = MonitoringStore(workspace.library.store)
        identifier = store.start(kind, document_id, page_count=page_count)
        return OperationHandle(store, identifier, started)
    except Exception as exc:
        # No SQL text, paths, user data, model prompts or exception message.
        LOGGER.warning("Operation metrics unavailable (%s).", type(exc).__name__)
        return None


def finish_operation(
    handle: OperationHandle | None,
    status: str,
    *,
    run_id=None,
    provider_latency_ms=None,
    provider_calls=None,
    page_count=None,
    test_count=None,
):
    if handle is None:
        return
    try:
        handle.store.finish(
            handle.identifier,
            status=status,
            duration_ms=max(0, (time.monotonic() - handle.started) * 1000),
            run_id=run_id,
            provider_latency_ms=provider_latency_ms,
            provider_calls=provider_calls,
            page_count=page_count,
            test_count=test_count,
        )
    except Exception as exc:
        LOGGER.warning("Operation metrics completion unavailable (%s).", type(exc).__name__)
