"""Source-proved comparison copies for redundant phase/subject descriptions.

No source facts are edited. Materials, statistics, codes, extra quantities and
unproved metadata stay literal. A stage alias needs the source's own cycle and
ordered quantities, never model wording alone.
"""

from __future__ import annotations

import re

from scione.matching.metadata_rules import (
    count_basis_pair,
    initial_phase_pair,
    measurement_map_pair,
    report_directive_pair,
)
from scione.matching.parameter_policy import source_proved_humidity_unit
from scione.parameters import normalize_claim, resolve_definition
from scione.parameters.engine import _temperature_subjects
from scione.parameters.registry import normalize_stage
from scione.source_text import citation_text
from scione.specifications.models import CustomerRequirement
from scione.structured.lexical_grounding import grounded_source_numbers, source_operator_hints
from scione.structured.models import AtomicClaim
from scione.structured.normalization import known_source_fact_problem
from scione.structured.source_clauses import ordered_thermal_cycle_proof


def _located_context(claim: AtomicClaim) -> bool:
    return bool(
        claim.source_context
        and citation_text(claim.source_context).casefold()
        in citation_text(claim.raw_text).casefold()
    )


def _topcoat_per_panel_proof(first, second, source, declarations) -> list[str] | None:
    """A known property and an explicit reporting axis can repeat in context.

    Clearing this closed phrase does not remove any independent scope demand:
    the selected capability must separately declare per-panel reporting in the
    same configuration/phase. Missing, conditional or conflicting scope stays
    unknown. The source must own both the quoted limit and its topcoat subject.
    """
    context = " ".join((first.source_context or "").casefold().split()).rstrip(".")
    if not (
        re.fullmatch(
            r"(?:separate )?topcoat thickness\s*;\s*"
            r"(?:on (?:every|each) panel|per[- ]panel)",
            context,
        )
        and (first_definition := resolve_definition(first.parameter_id))
        and first_definition.id == "topcoat_thickness"
        and (second_definition := resolve_definition(second.parameter_id))
        and second_definition.id == "topcoat_thickness"
        and first.kind == "criterion"
        and second.kind == "reporting"
        and first.normalization_state != "unresolved"
        and second.normalization_state != "unresolved"
        and second.availability == "supported"
        and second.source_context is None
        and normalize_stage(first.stage) == normalize_stage(second.stage)
        and first.configuration == second.configuration
        and first.sequence == second.sequence
        and first.source_pages
        and not known_source_fact_problem(first, source)
    ):
        return None
    raw = citation_text(first.raw_text).casefold()
    owned = [
        evidence
        for evidence in source.evidence
        if citation_text(first.raw_text) in citation_text(evidence.quote)
    ]
    if (
        not set(first.source_pages) <= {evidence.page for evidence in owned}
        or not re.search(r"\b(?:on (?:every|each) panel|per[- ]panel)\b", raw)
        or re.search(
            r"\b(?:not|no|never|without|except|unless)\b|\b(?:rather than|instead of)\b|不|非|无",
            raw,
        )
    ):
        return None
    literal_subject = re.search(r"\b(?:separate )?topcoat thickness\b", raw)
    explicit_antecedent = bool(
        re.match(r"(?:the )?latter\b", raw)
        and any(
            re.search(
                r"\bseparate topcoat thickness\s*;\s*" + re.escape(raw),
                citation_text(evidence.quote).casefold(),
            )
            for evidence in owned
        )
    )
    if not (literal_subject or explicit_antecedent):
        return None
    numbers = {
        value
        for value in (first.value, first.minimum, first.maximum, first.tolerance, *first.values)
        if value is not None
    }
    if (
        not first.unit
        or not numbers
        or not numbers <= grounded_source_numbers(first.raw_text, unit=first.unit)
        or source_operator_hints(first.raw_text, unit=first.unit, values=numbers)
        != {first.operator}
    ):
        return None
    scopes = [
        row
        for row in declarations
        if row.parameter_id == "result_scope"
        and normalize_stage(row.stage) == normalize_stage(second.stage)
        and row.configuration == second.configuration
        and row.sequence == second.sequence
    ]
    if not scopes or any(
        row.normalization_state == "unresolved"
        or row.availability != "supported"
        or row.source_context is not None
        or row.kind not in {"scope", "reporting"}
        or normalize_claim(row).text_value != "per_panel"
        or re.search(r"\bnot\s+per[- _]panel\b", row.raw_text, re.I)
        for row in scopes
    ):
        return None
    return sorted(row.id for row in scopes)


def canonical_alignment_pair(
    requested: AtomicClaim,
    declared: AtomicClaim,
    source: CustomerRequirement,
    declarations: list[AtomicClaim],
    *,
    wording_bridge: bool = False,
) -> tuple[AtomicClaim, AtomicClaim, list[dict]]:
    """Project only explicit existing axes, with evidence for every change."""
    first, second = requested.model_copy(deep=True), declared.model_copy(deep=True)
    actions = []
    first, humidity_proof = source_proved_humidity_unit(first, source)
    if humidity_proof:
        actions.append(humidity_proof)
    cycle = ordered_thermal_cycle_proof(first, source)
    if (
        cycle
        and normalize_stage(second.stage) == "thermal_cycle"
        and first.sequence == second.sequence
    ):
        actions.append(
            {
                "field": "stage",
                "source": first.stage,
                "canonical": "thermal_cycle",
                "rule": "literal_ordered_cycle_clause",
                **cycle,
            }
        )
        first.stage = "thermal_cycle"
    if wording_bridge:
        return first, second, actions
    first, second, count_actions = count_basis_pair(first, second, source)
    actions.extend(count_actions)
    first, second, map_actions = measurement_map_pair(first, second, source)
    actions.extend(map_actions)
    first, second, phase_actions = initial_phase_pair(first, second, source)
    actions.extend(phase_actions)
    first, second, report_actions = report_directive_pair(first, second, source)
    actions.extend(report_actions)
    context = " ".join((first.source_context or "").casefold().split()).rstrip(".")
    stage = normalize_stage(first.stage)
    same_stage = stage == normalize_stage(second.stage)
    if not context or not same_stage or first.sequence != second.sequence:
        return first, second, actions
    clear = None
    if (
        stage == "measurement"
        and (definition := resolve_definition(first.parameter_id))
        and definition.id == "measurement_angle"
        and re.fullmatch(r"specular[- ]gloss reading", context)
        and _located_context(first)
        and second.source_context is None
        and first.configuration == second.configuration
        and first.source_pages
        and any(
            evidence.page in first.source_pages
            and citation_text(first.raw_text) in citation_text(evidence.quote)
            for evidence in source.evidence
        )
        and any(
            row.parameter_id == "gloss"
            and row.kind == "reporting"
            and row.configuration == second.configuration
            and row.availability == "supported"
            and row.normalization_state != "unresolved"
            for row in declarations
        )
        and any(
            row.parameter_id == "gloss"
            and row.kind == "criterion"
            and row.source_pages
            and row.normalization_state != "unresolved"
            and row.availability == "supported"
            and all(
                any(
                    e.page == page and citation_text(row.raw_text) in citation_text(e.quote)
                    for e in source.evidence
                )
                for page in row.source_pages
            )
            for row in source.atomic_claims
        )
    ):
        clear = "owned_registered_measurement_property_caption"
    elif (
        re.fullmatch(
            r"(?:actual )?(?:metal|specimen|sample) temperature"
            r"(?:,? not (?:the )?oven (?:setting|temperature|setpoint))?",
            context,
        )
        and _located_context(first)
        and _temperature_subjects(first) == _temperature_subjects(second) == {"specimen"}
        and second.source_context is None
    ):
        clear = "explicit_temperature_subject_already_recorded"
    elif (
        stage == "conditioning"
        and context == "before initial readings"
        and _located_context(first)
        and second.source_context is None
    ):
        clear = "explicit_conditioning_stage_before_initial_readings"
    elif (
        stage == "cycle_recovery"
        and context == "between transfers"
        and _located_context(first)
        and re.search(r"\brecovery\b", first.raw_text, re.I)
        and second.source_context is None
    ):
        clear = "explicit_recovery_stage_already_recorded"
    elif (
        stage == "thermal_cycle"
        and cycle
        and context
        in {
            f"{cycle.get('relative_phase')} step of each cycle",
            f"{cycle.get('relative_phase')} step",
        }
        and second.source_context is None
    ):
        clear = "literal_cycle_and_sequence_already_recorded"
    elif (
        stage == "thermal_cycle"
        and cycle
        and cycle.get("verified_cycle_count")
        and re.fullmatch(r"(?:hot-to-cold\s+)?thermal\s+cycling", context)
        and second.source_context is None
    ):
        clear = "literal_cycle_count_and_phase_scope_already_recorded"
    elif (
        stage == "curing"
        and context in {"cure reference", "cure reference at actual metal temperature"}
        and second.source_context is None
        and (definition := resolve_definition(first.parameter_id))
        and definition.id == "duration"
    ):
        # The object's temperature and the duration are separate constraints of
        # one explicitly selected lab configuration, not values borrowed from
        # unrelated setups. The parent source owns both words and this quote.
        parent = any(
            row.page in first.source_pages
            and citation_text(first.raw_text) in citation_text(row.quote)
            and re.search(
                r"actual metal temperature.*?cure reference", citation_text(row.quote), re.I
            )
            for row in source.evidence
        )
        sibling = next(
            (
                row
                for row in declarations
                if normalize_stage(row.stage) == "curing"
                and row.configuration == second.configuration
                and row.sequence == second.sequence
                and row.normalization_state != "unresolved"
                and row.availability == "supported"
                and _temperature_subjects(row) == {"specimen"}
            ),
            None,
        )
        if parent and sibling:
            clear = "source_cure_object_and_same_configuration_temperature"
            actions.append({"field": "related_lab_claim", "claim_id": sibling.id, "rule": clear})
    elif scope_ids := _topcoat_per_panel_proof(first, second, source, declarations):
        clear = "source_topcoat_property_and_explicit_per_panel_output"
        actions.append({"field": "related_lab_claims", "claim_ids": scope_ids, "rule": clear})
    if clear:
        actions.append(
            {
                "field": "source_context",
                "source": first.source_context,
                "canonical": None,
                "rule": clear,
            }
        )
        first.source_context = None
    return first, second, actions
