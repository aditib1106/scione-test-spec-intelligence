"""Separate source-backed sample/project obligations from machine capability."""

import re
from collections import defaultdict
from decimal import Decimal

from scione.parameters.units import resolve_unit
from scione.source_text import citation_contains
from scione.structured.models import AtomicClaim
from scione.structured.planning import planning_quantities


def compound_sample_plan_proofs(source):
    """Retain complete literal allocation clauses as planning, never capability.

    A model can preserve a three-way sample allocation in one text atom instead
    of three numeric atoms. The existing closed planning grammar proves every
    quantity and destination; the complete source quotation and text must agree.
    Mixed execution clauses and changed quantities cannot use this path.
    """
    result = {}
    for claim in source.normalized.claims:
        if (
            claim.parameter_id not in {"panel_allocation", "sample_allocation"}
            or claim.kind not in {"condition", "instruction", "scope"}
            or claim.operator not in {"text", "required"}
            or claim.normalization_state == "unresolved"
            or claim.configuration is not None
            or claim.sequence is not None
            or claim.unit is not None
            or claim.values
            or any(v is not None for v in (
                claim.value, claim.minimum, claim.maximum, claim.tolerance
            ))
            or not claim.text_value
            or not citation_contains(claim.raw_text, claim.text_value)
            or not citation_contains(claim.text_value, claim.raw_text)
        ):
            continue
        allocations = planning_quantities(claim.raw_text)
        if len(allocations) < 2 or any(
            item["parameter_id"] != "sample_allocation" for item in allocations
        ):
            continue
        pages = {
            e.page for e in source.requirement.evidence
            if citation_contains(e.quote, claim.raw_text)
        }
        if not claim.source_pages or not set(claim.source_pages) <= pages:
            continue
        result[claim.id] = {
            "rule": "source_bound_compound_sample_allocation",
            "source_quote": claim.raw_text,
            "source_pages": list(claim.source_pages),
            "allocations": [
                {**item, "value": str(item["value"])} for item in allocations
            ],
            "establishes_lab_support": False,
        }
    return result


def compound_plan_review_issues(source, proofs):
    """Identify only quantity-audit issues proved by those same full clauses.

    This does not clear a requirement-wide warning, infer an omitted number or
    suppress any other audit. Original source facts and original audits stay in
    the immutable matching snapshot.
    """
    resolved = set()
    for action in source.normalized.normalization_actions:
        if action.get("rule") != "native_source_clause_audit":
            continue
        clause = action.get("source_clause", "").strip().rstrip(".;")
        matching = [
            proof for proof in proofs.values()
            if set(action.get("source_pages", [])) <= set(proof["source_pages"])
            and citation_contains(clause, proof["source_quote"].strip().rstrip(".;"))
            and citation_contains(proof["source_quote"].strip().rstrip(".;"), clause)
        ]
        if len(matching) != 1:
            continue
        proof = matching[0]
        expected = [
            (Decimal(row["value"]), row["source_context"]) for row in proof["allocations"]
        ]
        try:
            proposed = [AtomicClaim.model_validate(row) for row in action["parser_proposals"]]
        except (KeyError, ValueError, TypeError):
            continue
        if (
            len(proposed) != len(expected)
            or any(row.parameter_id != "sample_allocation" or row.operator != "="
                   or row.unit is not None for row in proposed)
            or [(row.value, row.source_context) for row in proposed] != expected
        ):
            continue
        values = {value for value, _scope in expected}
        audit_issues = [
            re.fullmatch(r"Unrepresented source quantity: (\d+(?:\.\d+)?)\.", issue)
            for issue in action.get("issues", [])
        ]
        if any(issue is None or Decimal(issue[1]) not in values for issue in audit_issues):
            continue
        prefix = f"{source.requirement.id} {action.get('audit_id')}: "
        for issue in source.normalized.review_issues:
            if not issue.startswith(prefix):
                continue
            quantity = re.fullmatch(r"Unrepresented source quantity: (\d+(?:\.\d+)?)\.",
                                    issue[len(prefix):])
            if quantity and Decimal(quantity[1]) in values:
                resolved.add(issue)
    return resolved


def planning_role_problem(claim, source):
    from scione.structured.normalization import known_source_fact_problem

    if claim.normalization_state == "unresolved" or claim.kind not in {
        "condition",
        "scope",
        "instruction",
    }:
        return "Only usable specimen-submission/allocation facts can be sample planning."
    text = " ".join(
        (claim.label, claim.parameter_id or "", claim.raw_text, claim.source_context or "")
    )
    unit = resolve_unit(claim.unit)
    count = bool(unit and unit.dimension in {"panels", "specimens"}) or claim.unit in {
        "lots",
        "batches",
    }
    if not count or not re.search(
        r"lot|batch|panel|specimen|sample|coupon|批|试样|样品|试板", text, re.I
    ):
        return "This quantity is not a specimen or lot count."
    values = [
        v for v in (claim.value, claim.minimum, claim.maximum, *claim.values) if v is not None
    ]
    if not values or any(v < 0 or v != v.to_integral_value() for v in values):
        return "Sample planning needs an explicit whole-number source count."
    if not re.search(
        r"submit|suppl|from each|per lot|assign|allocat|reserv|retention|customer|lots?|batches?|"
        r"提交|提供|每批|留样|试件",
        text,
        re.I,
    ):
        return "The source does not identify submission or allocation planning."
    if re.search(
        r"simultaneous|concurrent|at once|chamber load|holder capacity|per cycle|"
        r"同时|容量|单次负载",
        text,
        re.I,
    ):
        return "An execution or simultaneous-load limit must remain a capability comparison."
    pages = {
        e.page for e in source.requirement.evidence if citation_contains(e.quote, claim.raw_text)
    }
    if not claim.source_pages or not set(claim.source_pages) <= pages:
        return "Sample planning needs the complete located customer quotation."
    if known_source_fact_problem(claim, source.requirement):
        return "Sample planning must retain the source quantity, unit and operator."
    return None


def mark_project_obligations(requirements):
    """A repeated complete delivery instruction belongs to the project once.

    No numeric/execution fact is reclassified. All originals stay stored, and
    this marker never establishes that the lab can perform another test.
    """
    groups = defaultdict(list)
    for row in requirements:
        for claim in row.normalized.claims:
            if (
                claim.kind not in {"instruction", "reporting"}
                or claim.operator not in {"text", "required"}
                or claim.normalization_state == "unresolved"
                or re.search(r"\d|[°±≤≥]", claim.raw_text)
                or not re.match(
                    r"\s*(?:report|record|provide|submit|deliver)\b", claim.raw_text, re.I
                )
            ):
                continue
            pages = {
                e.page
                for e in row.requirement.evidence
                if citation_contains(e.quote, claim.raw_text)
            }
            if claim.source_pages and set(claim.source_pages) <= pages:
                groups[" ".join(claim.raw_text.split())].append((row, claim))
    for text, owners in groups.items():
        if len({row.requirement.id for row, _ in owners}) < 2:
            continue
        for row, claim in owners:
            row.normalized.normalization_actions.append(
                {
                    "rule": "shared_project_delivery_instruction",
                    "claim_id": claim.id,
                    "source_quote": text,
                    "source_pages": list(claim.source_pages),
                    "owner_requirement_ids": sorted({r.requirement.id for r, _ in owners}),
                    "establishes_lab_support": False,
                }
            )
