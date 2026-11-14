"""Conservative, deterministic aggregation of source-window extraction outputs.

Only local identifiers and citation ordering are ignored for duplicate detection.
No test-name matching, condition propagation or semantic inference happens here.
The original per-call requests and responses remain the runner's responsibility.
"""

from __future__ import annotations

import json
import re

from scione.specifications.models import CustomerExtraction, CustomerRequirement


def _requirement_key(requirement: CustomerRequirement) -> str:
    payload = requirement.model_dump(mode="json", exclude={"id"})
    payload["atomic_claims"] = [
        {key: value for key, value in claim.items() if key != "id"}
        for claim in payload["atomic_claims"]
    ]
    payload["evidence"] = sorted(
        payload["evidence"],
        key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
    )
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _remap_issue(issue: str, identifiers: dict[str, str]) -> str:
    if not identifiers:
        return issue
    # A single substitution avoids cascading req_001 -> req_002 -> req_003.
    # Do not rewrite an identifier embedded in a longer requirement/claim ID.
    pattern = re.compile(
        r"(?<![A-Za-z0-9_-])(?:"
        + "|".join(re.escape(identifier) for identifier in identifiers)
        + r")(?![A-Za-z0-9_-])"
    )
    return pattern.sub(lambda match: identifiers[match.group()], issue)


def _scope_named_issue(issue: str, names: dict[str, list[str]]) -> str:
    """Route only an exact, unambiguous test-name prefix, never inferred prose."""
    if re.search(r"(?<![A-Za-z0-9_-])req_[A-Za-z0-9][A-Za-z0-9_-]*", issue):
        return issue
    candidates = [
        identifiers[0]
        for name, identifiers in names.items()
        if len(identifiers) == 1 and issue.casefold().startswith(name + ":")
    ]
    return f"{candidates[0]}: {issue}" if len(candidates) == 1 else issue


def merge_chunk_extractions(parts: list[CustomerExtraction]) -> CustomerExtraction:
    """Merge complete chunk outputs without combining differently scoped tests.

    The first occurrence owns row order and citation order. Exact duplicate rows
    map to that same global requirement. Stage, sequence, material/context,
    method edition and every source value remain part of the duplicate key.
    """
    if not parts:
        raise ValueError("Chunk aggregation requires at least one extraction.")

    first = parts[0]
    requirements: list[CustomerRequirement] = []
    owners: dict[str, CustomerRequirement] = {}
    issues: list[str] = []
    notices = []
    notice_keys: set[str] = set()
    metadata_differs = False
    name_variants: dict[str, set[str]] = {}
    for part in parts:
        for requirement in part.requirements:
            name_variants.setdefault(requirement.original_test_name.casefold(), set()).add(
                _requirement_key(requirement)
            )

    for part in parts:
        identifiers: dict[str, str] = {}
        names: dict[str, list[str]] = {}
        for requirement in part.requirements:
            key = _requirement_key(requirement)
            owner = owners.get(key)
            if owner is None:
                owner = requirement.model_copy(deep=True)
                owner.id = f"req_{len(requirements) + 1:03d}"
                for index, claim in enumerate(owner.atomic_claims, start=1):
                    claim.id = f"{owner.id}_claim_{index}"
                requirements.append(owner)
                owners[key] = owner
            identifiers[requirement.id] = owner.id
            name = requirement.original_test_name.casefold()
            if len(name_variants[name]) == 1:
                names.setdefault(name, []).append(requirement.id)

        for issue in part.review_issues:
            mapped = _remap_issue(_scope_named_issue(issue, names), identifiers)
            if mapped not in issues:
                issues.append(mapped)
        for notice in part.document_notices:
            key = notice.model_dump_json()
            if key not in notice_keys:
                notices.append(notice.model_copy(deep=True))
                notice_keys.add(key)
        metadata_differs |= part.document != first.document

    if metadata_differs:
        issues.append(
            "Source-window extractions returned differing document metadata; "
            "the first window's metadata was retained and must be checked against the source."
        )
    return CustomerExtraction(
        schema_version=first.schema_version,
        document=first.document.model_copy(deep=True),
        requirements=requirements,
        review_issues=issues,
        document_notices=notices,
    )
