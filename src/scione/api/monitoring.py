"""Maintainer-only metrics over server-resolved laboratory workspaces."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from scione.auth.models import require_maintainer
from scione.monitoring import collect_metrics


def create_monitoring_router(registry, auth_manager, legacy_workspace=None):
    router = APIRouter(prefix="/api/developer", tags=["Monitoring"])

    @router.get("/metrics")
    def metrics(
        scope: Literal["current", "all"] = "current",
        days: Annotated[int, Query(ge=1, le=90)] = 30,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ):
        principal = require_maintainer()
        labs = []
        if registry is None:
            if legacy_workspace is not None:
                labs = [("local", "Local workspace", legacy_workspace)]
        elif scope == "all":
            available = auth_manager.list_labs()
            labs = [(lab.id, lab.name, registry.workspace_for(lab.id)) for lab in available[:20]]
            # Expose the omitted count without opening additional tenant pools.
            labs.extend((lab.id, lab.name, None) for lab in available[20:])
        elif principal.lab_id:
            lab = auth_manager.get_lab(principal.lab_id)
            if lab is not None and lab.active:
                labs = [(lab.id, lab.name, registry.workspace_for(lab.id))]
        return collect_metrics(labs, scope=scope, days=days, limit=limit)

    return router
