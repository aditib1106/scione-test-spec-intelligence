"""Read-only, tenant-scoped observation of explicitly started inference.

The pilot has a single worker and no durable background queue. A stale ledger
entry is therefore completion-unknown, not a promise to resume or retry work.
"""

import logging
import re

from fastapi import APIRouter, HTTPException

from scione.auth.models import require_principal
from scione.monitoring.store import MonitoringStore
from scione.specifications.store import SpecStoreError

logger = logging.getLogger(__name__)
_DOCUMENT_ID = re.compile(r"[0-9a-f]{16}\Z")


def create_inference_activity_router(gate, registry=None, legacy_workspace=None, *, jobs=None):
    router = APIRouter(tags=["Analysis activity"])

    def selected_workspace():
        principal = require_principal()
        if registry is None:
            return None, legacy_workspace
        if principal.lab_id is None:
            return None, None
        return principal.lab_id, registry.workspace_for(principal.lab_id)

    @router.get("/api/operations")
    def active_operations():
        workspace_id, workspace = selected_workspace()
        active = gate.snapshot(workspace_id=workspace_id) if workspace is not None else []
        return {"active": active, "background_resume_supported": False}

    @router.get("/api/specifications/{document_id}/operations")
    def document_operations(document_id: str):
        workspace_id, workspace = selected_workspace()
        if workspace is None:
            raise HTTPException(403, "Select a laboratory workspace first.")
        if not _DOCUMENT_ID.fullmatch(document_id):
            raise HTTPException(404, "This resource was not found.")
        try:
            directory = workspace.specs._document_directory(document_id)
            if not all(
                (directory / name).is_file() and not (directory / name).is_symlink()
                for name in ("original.pdf", "document.json", "metadata.json")
            ):
                raise HTTPException(404, "This resource was not found.")
        except SpecStoreError:
            raise HTTPException(404, "This resource was not found.") from None
        active = gate.snapshot(workspace_id=workspace_id, document_id=document_id)
        latest = None
        notice = None
        try:
            latest = MonitoringStore(workspace.library.store).latest_document(document_id)
        except Exception as exc:
            # Telemetry failure must never affect existing source/results or
            # start paid work. Do not log exception values or customer data.
            logger.warning("Operation status unavailable (%s).", type(exc).__name__)
            notice = "Saved operation status is temporarily unavailable."
        state = "running" if active else "idle"
        if not active and latest is not None and latest["status"] == "running":
            state = "completion_unknown"
            notice = (
                "The previous analysis is no longer active on this server. "
                "Check saved results before starting it again. No automatic retry was started."
            )
        return {
            "document_id": document_id,
            "active": active,
            "state": state,
            "latest": latest,
            "notice": notice,
            "background_resume_supported": False,
            "recent_job": jobs.latest(document_id, workspace_id=workspace_id) if jobs else None,
        }

    return router
