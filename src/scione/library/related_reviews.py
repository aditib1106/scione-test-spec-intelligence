"""Read-only historical review references; prior decisions are never applied.

Identity deliberately requires an exact test name and complete method/edition
set. This is a bounded navigation aid, not scientific equivalence or a learned
capability rule. The authenticated workspace supplies the only tenant scope.
"""

from __future__ import annotations

from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import func, select

from scione.library.assessment_reviews import reviews
from scione.library.store import comparisons, document_archives, documents
from scione.specifications.store import SpecStoreError

_CANDIDATES = 100
_ENTRIES = 10
# SQLite length(JSON) counts stored characters; MySQL counts stored bytes.
# These conservative stored-size budgets are not measurements of decoded Python
# memory. They bound whole-snapshot reads, never truncate a historical finding.
_SINGLE_MACHINE_LENGTH = 1_000_000
_TOTAL_MACHINE_LENGTH = 4_000_000
_STATUS = {"fully_matched", "partially_matched", "not_matched", "needs_information", "excluded"}
_DECISIONS = {"keep", "clarify_customer", "outsource", "decline"}
_REVIEW_FIELDS = (
    "review_id", "comparison_id", "requirement_id", "version", "review_status",
    "decision", "note", "actor", "created_at", "machine_status",
)
_DOCUMENT_FIELDS = (
    "document_id", "title", "company_name", "specification_id", "specification_date",
    "original_name", "revision",
)
_CHECK_FIELDS = (
    "id", "parameter_id", "label", "kind", "stage", "unit", "requested", "declared",
    "status", "reason", "source_pages",
)


def _identity(value: object) -> str:
    """Only case and whitespace vary; punctuation, numbers and editions do not."""
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def _methods(row: dict) -> frozenset[str]:
    values = row.get("methods")
    if not isinstance(values, list) or not values or any(not _identity(value) for value in values):
        return frozenset()
    return frozenset(_identity(value) for value in values)


def _review_valid(event: object, candidate, machine: dict, requirement: dict) -> bool:
    return bool(
        isinstance(event, dict)
        and event.get("review_id") == candidate["review_id"]
        and event.get("comparison_id") == candidate["comparison_id"]
        and event.get("requirement_id") == candidate["requirement_id"]
        and event.get("version") == candidate["version"]
        and event.get("input_fingerprint") == machine.get("report_input_fingerprint")
        and bool(machine.get("report_input_fingerprint"))
        and event.get("machine_status") == requirement.get("comparison", {}).get("status")
        and event.get("review_status") in {"accepted", "pending"}
        and event.get("decision") in _DECISIONS
    )


def related_review_references(
    workspace, document_id: str, requirement_id: str,
    comparison_id: str, input_fingerprint: str,
) -> dict:
    """List prior same-lab references for this exact current assessment only."""
    try:
        workspace.specs.load_document(document_id)
    except SpecStoreError:
        raise HTTPException(404, "This specification was not found.") from None
    current = workspace.library.comparison(document_id)
    if (
        current["comparison_id"] != comparison_id
        or current["report_input_fingerprint"] != input_fingerprint
    ):
        raise HTTPException(409, "The assessment changed. Refresh before viewing related reviews.")
    target = next(
        (row for row in current["requirements"] if row["requirement_id"] == requirement_id), None
    )
    if target is None:
        raise HTTPException(400, "Select a test from this assessment.")
    response = {
        "document_id": document_id, "comparison_id": comparison_id,
        "requirement_id": requirement_id, "entries": [], "reference_only": True,
    }
    methods, test_name = _methods(target), _identity(target.get("test_name"))
    if not methods or not test_name:
        return response
    store = workspace.library.store
    workspace_id = workspace.labs.workspace_id
    latest = reviews.alias("latest_related_review")
    latest_version = (
        select(func.max(latest.c.version))
        .where(
            latest.c.workspace_id == reviews.c.workspace_id,
            latest.c.comparison_id == reviews.c.comparison_id,
            latest.c.requirement_id == reviews.c.requirement_id,
        )
        .correlate(reviews)
        .scalar_subquery()
    )
    # Sort small scalar metadata only. Never put full JSON in a MySQL filesort.
    query = (
        select(
            reviews.c.review_id, reviews.c.comparison_id, reviews.c.requirement_id,
            reviews.c.version, comparisons.c.document_id,
            func.length(comparisons.c.payload).label("stored_length"),
        )
        .join(comparisons, reviews.c.comparison_id == comparisons.c.comparison_id)
        .join(documents, comparisons.c.document_id == documents.c.document_id)
        .outerjoin(document_archives, documents.c.document_id == document_archives.c.document_id)
        .where(
            reviews.c.workspace_id == workspace_id,
            comparisons.c.document_id != document_id,
            document_archives.c.document_id.is_(None),
            reviews.c.version == latest_version,
        )
        .order_by(reviews.c.created_at.desc(), reviews.c.version.desc(), reviews.c.review_id.desc())
        .limit(_CANDIDATES)
    )
    machines: dict[str, dict | None] = {}
    seen_origins: set[str] = set()
    origin_events: dict[str, dict | None] = {}
    ancestry_reads = 0
    loaded_length = 0

    with store.engine.connect() as connection:
        candidates = connection.execute(query).mappings().all()

        def origin(event: dict) -> str | None:
            nonlocal ancestry_reads
            visited = set()
            while event.get("carried_from_review_id"):
                identifier = event.get("review_id")
                previous_id = event["carried_from_review_id"]
                if identifier in visited or not isinstance(previous_id, str):
                    return None
                visited.add(identifier)
                if previous_id not in origin_events:
                    if ancestry_reads >= _CANDIDATES:
                        return None
                    ancestry_reads += 1
                    original = connection.execute(
                        select(reviews.c.payload).where(
                            reviews.c.workspace_id == workspace_id,
                            reviews.c.review_id == previous_id,
                        )
                    ).scalar_one_or_none()
                    origin_events[previous_id] = original if isinstance(original, dict) else None
                previous = origin_events[previous_id]
                if previous is None or previous.get("review_id") != previous_id:
                    return None
                # A carry-forward is not a new decision. Broken provenance is
                # skipped instead of being shown as an independent endorsement.
                if any(previous.get(key) != event.get(key) for key in (
                    "requirement_id", "review_status", "decision", "note", "actor", "actor_label",
                )):
                    return None
                event = previous
            identifier = event.get("review_id")
            return identifier if isinstance(identifier, str) else None

        for candidate in candidates:
            previous_id = candidate["comparison_id"]
            if previous_id not in machines:
                stored_length = candidate["stored_length"]
                if (
                    not isinstance(stored_length, int)
                    or stored_length < 0
                    or stored_length > _SINGLE_MACHINE_LENGTH
                    or loaded_length + stored_length > _TOTAL_MACHINE_LENGTH
                ):
                    machines[previous_id] = None
                    response["history_limited"] = True
                    continue
                loaded_length += stored_length
                machine = connection.execute(
                    select(comparisons.c.payload).where(comparisons.c.comparison_id == previous_id)
                ).scalar_one_or_none()
                machines[previous_id] = machine if isinstance(machine, dict) else None
            previous = machines[previous_id]
            if (
                previous is None
                or "machine_comparison_id" in previous
                or "_scione_comparison_export_reference" in previous
                or previous.get("comparison_id") != previous_id
                or not isinstance(previous.get("document"), dict)
                or previous["document"].get("document_id") != candidate["document_id"]
                or not isinstance(previous.get("requirements"), list)
            ):
                continue
            requirement = next((
                row for row in previous["requirements"]
                if isinstance(row, dict)
                and row.get("requirement_id") == candidate["requirement_id"]
            ), None)
            if (
                requirement is None
                or _identity(requirement.get("test_name")) != test_name
                or _methods(requirement) != methods
                or not isinstance(requirement.get("comparison"), dict)
                or requirement["comparison"].get("status") not in _STATUS
                or not isinstance(requirement["comparison"].get("checks"), list)
            ):
                continue
            event = connection.execute(
                select(reviews.c.payload).where(
                    reviews.c.workspace_id == workspace_id,
                    reviews.c.review_id == candidate["review_id"],
                )
            ).scalar_one_or_none()
            if not _review_valid(event, candidate, previous, requirement):
                continue
            original_id = origin(event)
            if original_id is None or original_id in seen_origins:
                continue
            seen_origins.add(original_id)
            old_company = _identity(previous["document"].get("company_name"))
            current_company = _identity(current["document"].get("company_name"))
            customer = (
                "unassigned" if not old_company or not current_company
                else "same" if old_company == current_company else "different"
            )
            response["entries"].append({
                "review": {
                    **{key: deepcopy(event.get(key)) for key in _REVIEW_FIELDS},
                    **({"actor_label": event["actor_label"]}
                       if isinstance(event.get("actor_label"), str) else {}),
                },
                "document": {key: deepcopy(previous["document"].get(key))
                             for key in _DOCUMENT_FIELDS},
                "test_name": requirement["test_name"],
                "methods": deepcopy(requirement["methods"]),
                "lab_version": previous.get("lab_version"),
                "lab_changed": previous.get("lab_version") != current.get("lab_version"),
                "same_source": False,
                "different_customer": customer == "different",
                "customer_context": customer,
                "comparison": {
                    "status": requirement["comparison"]["status"],
                    "checks": [
                        {key: deepcopy(check[key]) for key in _CHECK_FIELDS if key in check}
                        for check in requirement["comparison"]["checks"] if isinstance(check, dict)
                    ],
                },
            })
            if len(response["entries"]) >= _ENTRIES:
                break
    return response
