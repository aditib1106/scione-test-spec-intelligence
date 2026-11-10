"""Read-only, bounded access to the team's synthetic reference metadata.

The active runtime catalog remains a separate, curated subset. This router never
creates lab support, imports customer requirements or makes a model request.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from scione.catalogs.bank import (
    MethodBankError,
    load_bank_manifest,
    load_synthetic_method_bank,
)

router = APIRouter(prefix="/api/catalog", tags=["Reference metadata"])


@router.get("/method-bank")
def method_bank(
    identifier: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    family_id: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    domain: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    material: Annotated[str | None, Query(min_length=1, max_length=300)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict:
    """Browse source rows using exact, intersected filters and explicit paging.

    Identifier comparison normalizes presentation only and retains the complete
    edition, standard body and adoption prefix. Domain/material are exact
    case-insensitive metadata filters, not semantic similarity judgments.
    """
    filters = {
        "identifier": identifier,
        "family_id": family_id,
        "domain": domain,
        "material": material,
    }
    if any(value is not None and not value.strip() for value in filters.values()):
        raise HTTPException(422, "Method-bank filters must not contain only whitespace.")
    try:
        bank = load_synthetic_method_bank()
        manifest = load_bank_manifest()
        page = bank.search(**filters, offset=offset, limit=limit)
    except (MethodBankError, OSError) as exc:
        raise HTTPException(
            503, "Synthetic method metadata is unavailable. Check its package data."
        ) from exc
    return {
        "dataset_id": manifest["dataset_id"],
        "synthetic": True,
        "records": [record.as_dict() for record in page.records],
        "total_count": page.total_count,
        "bank_record_count": len(bank),
        "offset": page.offset,
        "limit": page.limit,
        "has_more": page.has_more,
        "filters": filters,
        "source_sha256": bank.source_sha256,
        "provenance": {
            "source_csv_name": manifest["source_csv_name"],
            "source_csv_sha256": manifest["source_csv_sha256"],
            "source_audit_name": manifest["source_audit_name"],
            "source_audit_sha256": manifest["source_audit_sha256"],
            "build_policy": manifest["build_policy"],
        },
        "source_note": (
            "Fictional reference metadata from the capstone data team. These rows do not "
            "establish official standards, inter-method equivalence or laboratory capability. "
            "Browsing this bank does not add methods to a lab or alter the active runtime catalog."
        ),
    }
