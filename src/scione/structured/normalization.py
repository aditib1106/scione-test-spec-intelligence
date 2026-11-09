"""Grounded atomic normalization; no provider call occurs in these functions.

New model runs may provide typed atoms in the same extraction request. Older
runs and manually entered lab prose use a deliberately limited offline parser.
Unrecognized clauses remain visible, with their original text and provenance.
"""

from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal

from scione.catalogs.models import LabCapability
from scione.specifications.models import CustomerRequirement
from scione.structured.models import AtomicClaim, ClaimKind, NormalizedRequirement

_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_QUANTITY = re.compile(
    rf"(?P<op>>=|<=|≥|≤|>|<)?\s*(?P<first>{_NUMBER})"
    rf"(?:\s*(?:(?P<pm>±)\s*(?P<tolerance>{_NUMBER})|(?:-|\bto\b)\s*(?P<second>{_NUMBER})))?"
    r"\s*(?P<unit>°\s*C|degrees?\s*C|µm|μm|um|mm|cm|GU|%\s*RH|%|"
    r"days?|hours?|hrs?|h|minutes?|mins?|seconds?|sec|s|cycles?|°)(?![a-z])",
    re.I,
)
_WORD_NUMBERS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}
_LABELS = {
    "temperature": "Temperature",
    "duration": "Duration",
    "relative_humidity": "Relative humidity",
    "cycles": "Cycle count",
    "gloss": "Specular gloss",
    "gloss_retention": "Gloss retention",
    "measurement_angle": "Measurement angle",
    "color_difference": "Colour difference (ΔE*ab)",
    "adhesion_class": "Adhesion class",
    "film_thickness": "Film thickness",
    "topcoat_thickness": "Separate topcoat thickness",
    "illuminant": "Lighting / illuminant",
    "reference_panel": "Master-panel comparison",
    "measurement_locations": "Measurement locations",
    "result_scope": "Result reporting scope",
    "test_checkpoints": "Before / after-cycle checks",
}
_MEASUREMENT_SETUP = {
    "measurement_angle",
    "measurement_locations",
    "illuminant",
    "reference_panel",
}


def _intrinsic_stage(claim: AtomicClaim) -> AtomicClaim:
    """Measurement identities with no exposure phase use one named stage."""
    count_unit = {"cycles": "cycles", "measurement_locations": "locations"}.get(claim.parameter_id)
    if count_unit and claim.unit is None:
        claim = claim.model_copy(update={"unit": count_unit})
    if claim.stage == "general" and claim.parameter_id in _MEASUREMENT_SETUP:
        return claim.model_copy(update={"stage": "measurement"})
    return claim


def _text(value: str) -> str:
    value = (
        unicodedata.normalize("NFKC", value).replace("−", "-").replace("–", "-").replace("—", "-")
    )
    value = re.sub(r"(?<=\d)-minute\b", " min", value, flags=re.I)
    for word, number in _WORD_NUMBERS.items():
        value = re.sub(rf"\b{word}\b", number, value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def _stage(text: str, kind: str) -> str:
    text = text.casefold()
    if re.search(r"room.temperature recovery|recovery.*between transfer", text):
        return "cycle_recovery"
    if re.search(r"after cycling|post[- ]cycle|after (?:the )?(?:thermal )?cycl", text):
        return "after_cycle"
    if re.search(r"conditioning|condition for|allow.*days|rh.*days|days.*rh", text):
        return "conditioning"
    if re.search(r"as delivered|initial|baseline|before (?:the )?cycl|original value", text):
        return "initial"
    if re.search(r"cure|curing|metal temperature|oven setting", text):
        return "curing"
    if "measurement temperature" in text:
        return "measurement"
    if kind == "condition" and re.search(r"cycl|between transfers", text):
        return "thermal_cycle"
    return "general"


def _parameter(unit: str, text: str, name: str) -> tuple[str | None, str, Decimal]:
    unit = unit.casefold().replace(" ", "")
    context = f"{text} {name}".casefold()
    if unit in {"°c", "degreec", "degreesc"}:
        return "temperature", "°C", Decimal(1)
    if unit in {"day", "days"}:
        return "duration", "days", Decimal(1)
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return "duration", "h", Decimal(1)
    if unit in {"min", "mins", "minute", "minutes"}:
        return "duration", "min", Decimal(1)
    if unit in {"s", "sec", "second", "seconds"}:
        return "duration", "s", Decimal(1)
    if unit in {"cycle", "cycles"}:
        return "cycles", "cycles", Decimal(1)
    if unit in {"%rh", "%"} and re.search(r"humidity|\brh\b|湿度", context):
        return "relative_humidity", "% RH", Decimal(1)
    if (
        unit == "%"
        and re.search(r"retain|retention|保留", context)
        and not re.search(r"adhesion|cross.cut", name, re.I)
    ):
        return "gloss_retention", "%", Decimal(1)
    if unit == "gu":
        return "gloss", "GU", Decimal(1)
    if unit == "°":
        return "measurement_angle", "°", Decimal(1)
    if unit in {"μm", "µm", "um"} and re.search(r"thickness|topcoat|厚度", context):
        parameter = (
            "topcoat_thickness"
            if re.search(r"topcoat|separate.*layer|面漆", context)
            else "film_thickness"
        )
        return parameter, "µm", Decimal(1)
    return None, unit, Decimal(1)


def _availability(text: str) -> str:
    if re.search(
        r"not (?:reportable|supported|available)|cannot|can't|unsupported|unavailable", text, re.I
    ):
        return "unsupported"
    if re.search(r"not specified|unspecified|not recorded|to be agreed|not assessed", text, re.I):
        return "unknown"
    return "supported"


def _unparsed_qualification(
    text: str, spans: list[tuple[int, int]], results: list[AtomicClaim]
) -> str:
    """Keep meaningful words not explained by a numeric/setup parser.

    Finding one temperature is not proof that an atmosphere, material, tool or
    other qualifier in the same sentence was understood. This vocabulary only
    removes syntax and labels represented by the claims already produced.
    """
    remainder = list(text)
    for start, end in spans:
        remainder[start:end] = " " * (end - start)
    residual = "".join(remainder).casefold()
    parameters = {item.parameter_id for item in results}
    labels = {
        "temperature": r"temperature",
        "duration": r"duration|time",
        "relative_humidity": r"relative\s+humidity|humidity|rh",
        "cycles": r"thermal|temperature|cycling|cycles?|before|after|between\s+transfers",
        "gloss": r"specular|gloss",
        "gloss_retention": r"gloss|retention|retain|retained|its|own|original|value",
        "measurement_angle": r"measurement|angle",
        "measurement_locations": r"measurement|marked|locations?|positions?",
        "adhesion_class": r"adhesion|native|iso|class(?:es)?|only|acceptable",
        "color_difference": r"colou?r\s+difference",
        "film_thickness": r"dry[- ]film|film|thickness|build",
        "topcoat_thickness": r"separate|topcoat|thickness",
        "illuminant": r"lighting|illuminant",
        "reference_panel": r"comparison|compare|untouched|reference|master|panel",
        "result_scope": r"each|every|marked|location|site|panel",
        "test_checkpoints": r"repeat|check|both|before|after|cycles?",
    }
    removable = [labels[item] for item in parameters if item in labels]
    stages = {item.stage for item in results}
    if "conditioning" in stages:
        removable.append(r"conditioning|condition|allow|before|initial|readings?")
    if "initial" in stages:
        removable.append(r"initial|baseline|readings?")
    if "after_cycle" in stages:
        removable.append(r"after|post[- ]cycle|cycling|readings?")
    if "cycle_recovery" in stages:
        removable.append(r"room[- ]temperature|recovery|between|transfers?")
    cycle_steps = {
        item.sequence
        for item in results
        if item.parameter_id == "temperature"
        and item.stage == "thermal_cycle"
        and item.sequence is not None
    }
    if len(cycle_steps) >= 2:
        # "Between" connects the already represented ordered temperatures;
        # it is not a separate requirement. Other qualifiers remain visible.
        removable.append(r"between")
    if "curing" in stages:
        removable.append(r"curing|cure")
    if any(item.operator == ">=" for item in results):
        removable.append(r"at\s+least")
    if any(item.operator == "<=" for item in results):
        removable.append(r"at\s+most")
    # Negation, exclusions and unbounded-language qualifiers are intentionally
    # absent: 'except nitrogen' or 'up to' must not turn into an exact value.
    removable.append(
        r"at|and|for|or|with|the|a|an|is|of|to|as|be|shall|must|test|testing|"
        r"perform|performed|use|using|measured|measure|report|reported|can|"
        r"support|supported|require|required|under|from|in"
    )
    for pattern in removable:
        residual = re.sub(rf"\b(?:{pattern})\b", " ", residual, flags=re.I)
    return " ".join(re.findall(r"[\w]+(?:[-'][\w]+)*", residual, flags=re.UNICODE))


def _atoms(
    raw: str, *, kind: ClaimKind, pages: list[int], prefix: str, name: str = "", lab: bool = False
) -> list[AtomicClaim]:
    """Parse a single source clause, keeping ordered cycle steps distinct."""
    text = _text(raw)
    text = re.sub(
        rf"\bbetween\s+({_NUMBER})\s+and\s+({_NUMBER})\b",
        r"\1 to \2",
        text,
        flags=re.I,
    )
    stage = _stage(text, kind)
    availability = _availability(text) if lab else "supported"
    results: list[AtomicClaim] = []
    consumed: list[tuple[int, int]] = []

    def consume(match) -> None:
        if match is not None:
            consumed.append(match.span())

    def add(parameter: str | None, **fields) -> None:
        results.append(
            AtomicClaim(
                id=f"{prefix}_{len(results) + 1}",
                parameter_id=parameter,
                label=_LABELS.get(parameter or "", name or "Unstructured requirement"),
                kind=kind,
                stage=stage,
                availability=availability,
                raw_text=raw,
                source_pages=pages,
                normalization_state="parsed" if parameter else "unresolved",
                **fields,
            )
        )

    # Output availability is explicit negative evidence, not an absent value.
    if lab and availability == "unsupported":
        if re.search(r"gloss|光泽", text, re.I):
            add("gloss", operator="required", text_value="Not reportable")
        if re.search(r"retention|retain", text, re.I):
            add("gloss_retention", operator="required", text_value="Not reportable")
        if results:
            return results
    d65 = re.search(r"\bD65\b", text, re.I)
    if d65:
        add("illuminant", operator="text", text_value="D65")
        consume(d65)
    total_film = re.search(r"record total dry film build", text, re.I)
    if total_film:
        add("film_thickness", operator="required", text_value="Report total film thickness")
        results[-1].kind = "reporting"
        consume(total_film)
    topcoat = re.search(r"record.*separate topcoat thickness", text, re.I)
    if topcoat:
        add(
            "topcoat_thickness", operator="required", text_value="Report separate topcoat thickness"
        )
        results[-1].kind = "reporting"
        consume(topcoat)
    master = re.search(
        r"(?:untouched|reference|same|with an?)\s+master|master[- ]panel|master comparison",
        text,
        re.I,
    )
    if master:
        add("reference_panel", operator="required", text_value="Master panel")
        consume(master)
    locations = re.search(
        r"(\d+)[- ]+(?:(?:marked|measurement|repeatable|test) )?(?:locations|positions)",
        text,
        re.I,
    )
    if locations:
        add("measurement_locations", operator="=", value=Decimal(locations[1]), unit="locations")
        consume(locations)
    scope_match = re.search(r"(?:each|every) (?:marked )?(?:location|site|panel)", text, re.I)
    if scope_match:
        scope = "per_panel" if re.search(r"(?:each|every) panel", text, re.I) else "per_location"
        add("result_scope", operator="text", text_value=scope)
        if kind != "instruction":
            results[-1].kind = "scope"
        consume(scope_match)
    checkpoints = re.search(r"both before and after|before/after", text, re.I)
    if checkpoints and re.search(r"cycl", text, re.I):
        consume(checkpoints)
        add("test_checkpoints", operator="text", text_value="before_and_after_cycles")
        results[-1].kind = "scope"
        results[-1].stage = "general"
        if kind == "scope":
            return results

    thermal = kind == "condition" and bool(re.search(r"cycl", text, re.I))
    mixed_percentages = (
        text.count("%") > 1
        and re.search(r"humidity|\brh\b|湿度", text, re.I)
        and re.search(r"retain|retention|保留", text, re.I)
    )
    temperature_index = 0
    latest_temperature_sequence: int | None = None
    for match in _QUANTITY.finditer(text):
        if mixed_percentages and "%" in match["unit"]:
            # Clause-wide context cannot identify which percentage belongs to
            # humidity versus a retained output. Keep that part unresolved.
            continue
        parameter, unit, factor = _parameter(match["unit"], text, name)
        if parameter is None:
            continue
        consume(match)
        # A film dimension or a temperature's ± tolerance must not become an unrelated quantity.
        fields = {"operator": "=", "unit": unit, "value": Decimal(match["first"]) * factor}
        if match["pm"]:
            fields["tolerance"] = Decimal(match["tolerance"]) * factor
        elif match["second"] is not None:
            fields.update(
                operator="range",
                value=None,
                minimum=Decimal(match["first"]) * factor,
                maximum=Decimal(match["second"]) * factor,
            )
        elif match["op"]:
            fields["operator"] = {"≥": ">=", "≤": "<="}.get(match["op"], match["op"])
        if (
            parameter == "gloss_retention"
            and fields["operator"] == "="
            and re.search(r"at least", text, re.I)
        ):
            fields["operator"] = ">="
        if thermal and parameter == "temperature":
            temperature_index += 1
            latest_temperature_sequence = temperature_index
            fields["sequence"] = temperature_index
        elif thermal and parameter == "duration" and latest_temperature_sequence:
            fields["sequence"] = latest_temperature_sequence
        add(parameter, **fields)
        if thermal and parameter in {"temperature", "duration", "cycles"}:
            results[-1].stage = "thermal_cycle"

    # Dimensionless outputs need their named scale, not a generic numeric field.
    if re.search(r"adhesion class|\bclass(?:es)?\s*[: ]*[0-9]", text, re.I):
        match = re.search(
            rf"class(?:es)?\s*:?\s*({_NUMBER})\s*(?:(-|to|or)\s*({_NUMBER}))?", text, re.I
        )
        if match:
            consume(match)
            first, relation, second = match.groups()
            if relation == "or":
                add("adhesion_class", operator="one_of", values=[Decimal(first), Decimal(second)])
            elif second is not None:
                add(
                    "adhesion_class",
                    operator="range",
                    minimum=Decimal(first),
                    maximum=Decimal(second),
                )
            else:
                add("adhesion_class", operator="=", value=Decimal(first))
    if re.search(
        r"Δ\s*E|delta\s*e|colour difference|color difference|色差", text + " " + name, re.I
    ):
        # Restrict the match to a numeric expression following the scale/name.
        match = re.search(
            r"(?:Δ\s*E\*?ab|Δ\s*E|delta\s*e|colou?r difference)\s*:?\s*"
            rf"(<=|≤|>=|≥|<|>)?\s*({_NUMBER})(?:\s*(?:-|to)\s*({_NUMBER}))?",
            text,
            re.I,
        )
        if not match and kind == "criterion":
            match = re.search(rf"(<=|≤|>=|≥|<|>)\s*({_NUMBER})(?:\s*(?:-|to)\s*({_NUMBER}))?", text)
        if not match and kind == "criterion":
            # Explicit verbal inequalities retain the original source clause;
            # no rewritten model quotation is accepted as verbatim evidence.
            verbal = re.search(
                r"\b(?:may not exceed|must not exceed|shall not exceed|not more than)"
                rf"\s+({_NUMBER})",
                text,
                re.I,
            )
            if verbal:
                consume(verbal)
                add("color_difference", operator="<=", value=Decimal(verbal[1]), unit="ΔE*ab")
        if match:
            consume(match)
            op, first, second = match.groups()
            if second is not None:
                add(
                    "color_difference",
                    operator="range",
                    minimum=Decimal(first),
                    maximum=Decimal(second),
                    unit="ΔE*ab",
                )
            else:
                add(
                    "color_difference",
                    operator={"≤": "<=", "≥": ">="}.get(op, op) or "=",
                    value=Decimal(first),
                    unit="ΔE*ab",
                )

    if not results:
        parameter = "temperature" if "measurement temperature" in text.casefold() else None
        add(parameter, operator="text", text_value=text)
    else:
        remainder = _unparsed_qualification(text, consumed, results)
        if remainder:
            if lab:
                # The qualifier can restrict the parsed limit. It must not be
                # silently discarded while the numeric limit certifies support.
                results = [
                    item.model_copy(update={"normalization_state": "unresolved"})
                    for item in results
                ]
            add(None, operator="text", text_value=remainder)
            results[-1].label = "Additional source qualification"
        if kind == "instruction" and stage in {"curing", "conditioning"}:
            for item in results:
                if item.parameter_id in {"temperature", "duration", "relative_humidity"}:
                    item.kind = "condition"
    return results


def _clauses(raw: str) -> list[str]:
    pattern = (
        r"[;\n]+|,\s*(?=(?:with\s+a\s+)?(?:\d+[- ]minute\s+)?"
        r"room.temperature\s+recovery)"
    )
    return [part.strip() for part in re.split(pattern, raw, flags=re.I) if part.strip()]


def normalize_requirement(requirement: CustomerRequirement) -> NormalizedRequirement:
    """Use grounded model atoms when present; safely structure older text runs."""
    pages = sorted({item.page for item in requirement.evidence})

    def located_pages(raw: str) -> list[int]:
        needle = _text(raw).casefold()
        return sorted(
            {
                evidence.page
                for evidence in requirement.evidence
                if needle in _text(evidence.quote).casefold()
                or _text(evidence.quote).casefold() in needle
            }
        )

    field_values: list[tuple[ClaimKind, str, str]] = []
    seen_fields: set[tuple] = set()

    def field(kind: ClaimKind, raw: str, name: str, context=None, unit=None) -> None:
        identity = (kind, _text(raw), name, context, unit)
        if identity not in seen_fields:
            seen_fields.add(identity)
            field_values.append((kind, raw, name))

    for value in requirement.conditions:
        field("condition", value.raw_value, value.name, value.context, value.unit)
    for value in requirement.acceptance_criteria:
        field("criterion", value.raw_value, value.name, value.context, value.unit)
    for value in requirement.applicability:
        field("scope", value, "Scope")
    for value in requirement.special_instructions:
        field("instruction", value, "Instruction")
    raw_sources = [value for _, value, _ in field_values] + [
        item.quote for item in requirement.evidence
    ]
    atoms = []
    model_identities: dict[str, int] = {}
    supplied = getattr(requirement, "atomic_claims", [])
    for item in supplied:
        claim = AtomicClaim.model_validate(
            item.model_dump() if isinstance(item, AtomicClaim) else item
        )
        grounded = any(
            _text(claim.raw_text).casefold() in _text(raw).casefold() for raw in raw_sources
        )
        # A hyphen after a number separates a range; it does not negate the
        # second endpoint. Leading signs, including negative temperatures, stay signed.
        numbers = {
            Decimal(value) for value in re.findall(rf"(?<![\d.]){_NUMBER}", _text(claim.raw_text))
        }
        numeric_values = [
            value
            for value in [claim.value, claim.tolerance, claim.minimum, claim.maximum]
            if value is not None
        ] + claim.values
        numeric_grounding = all(value in numbers for value in numeric_values)
        grounded_pages = {
            evidence.page
            for evidence in requirement.evidence
            if _text(claim.raw_text).casefold() in _text(evidence.quote).casefold()
        }
        # Numeric shape alone cannot turn "23 °C" into "23 hours".
        physical_units = {"°C", "h", "min", "s", "days", "µm", "μm", "GU", "% RH", "%"}
        source_units = {
            _parameter(match["unit"], _text(claim.raw_text), claim.label)[1]
            for match in _QUANTITY.finditer(_text(claim.raw_text))
        }
        unit_grounding = (
            not numeric_values or claim.unit not in physical_units or (claim.unit in source_units)
        )
        if (
            not grounded
            or not claim.source_pages
            or not set(claim.source_pages).issubset(grounded_pages)
            or not numeric_grounding
            or not unit_grounding
        ):
            # Preserve the original claim visibly; do not let fabricated values support a match.
            claim = claim.model_copy(
                update={
                    "parameter_id": None,
                    "normalization_state": "unresolved",
                    "availability": "unknown",
                }
            )
        claim = _intrinsic_stage(claim)
        identity_fields = claim.model_dump(mode="json", exclude={"id"})
        identity_fields["raw_text"] = _text(claim.raw_text)
        identity_fields["source_pages"] = sorted(set(claim.source_pages))
        identity = json.dumps(identity_fields, sort_keys=True, ensure_ascii=False)
        if identity in model_identities:
            index = model_identities[identity]
            atoms[index] = atoms[index].model_copy(
                update={"source_pages": sorted(set(atoms[index].source_pages + claim.source_pages))}
            )
            continue
        model_identities[identity] = len(atoms)
        atoms.append(claim.model_copy(update={"id": f"{requirement.id}_claim_{len(atoms) + 1}"}))
    model_atoms = list(atoms)
    for kind, raw, name in field_values:
        for clause in _clauses(raw):
            clause_pages = located_pages(clause)
            parsed = _atoms(
                clause,
                kind=kind,
                pages=clause_pages or pages,
                prefix=f"{requirement.id}_field_{len(atoms) + 1}",
                name=f"{name} {requirement.original_test_name}",
            )
            parsed_parameters = {item.parameter_id for item in parsed if item.parameter_id}
            for fallback in parsed:
                if (
                    fallback.parameter_id is None
                    and fallback.label == "Additional source qualification"
                ):
                    # A grounded, separate model parameter may explain the
                    # qualifier the numeric fallback could not understand. A
                    # temperature atom quoting the whole sentence cannot do so.
                    unexplained = set(re.findall(r"\w+", fallback.text_value or ""))
                    explained = set()
                    for claim in model_atoms:
                        if (
                            claim.normalization_state != "unresolved"
                            and claim.parameter_id not in parsed_parameters
                            and claim.kind == kind
                        ):
                            explained.update(re.findall(r"\w+", _text(claim.raw_text).casefold()))
                    if unexplained and unexplained <= explained:
                        continue
                    atoms.append(fallback)
                    continue
                represented = any(
                    claim.kind == kind
                    and claim.normalization_state != "unresolved"
                    and (
                        (
                            claim.parameter_id == fallback.parameter_id
                            and (claim.stage == fallback.stage or fallback.stage == "general")
                            and claim.sequence == fallback.sequence
                            and claim.operator == fallback.operator
                            and claim.value == fallback.value
                            and claim.minimum == fallback.minimum
                            and claim.maximum == fallback.maximum
                            and claim.tolerance == fallback.tolerance
                            and claim.values == fallback.values
                            and claim.unit == fallback.unit
                            and claim.text_value == fallback.text_value
                        )
                        if fallback.parameter_id
                        else _text(clause).casefold() in _text(claim.raw_text).casefold()
                    )
                    and (
                        _text(claim.raw_text).casefold() in _text(clause).casefold()
                        or _text(clause).casefold() in _text(claim.raw_text).casefold()
                    )
                    for claim in model_atoms
                )
                if not represented:
                    atoms.append(fallback)
    return NormalizedRequirement(
        requirement_id=requirement.id,
        test_name=requirement.original_test_name,
        claims=[_intrinsic_stage(claim) for claim in atoms],
    )


def normalize_capability(capability: LabCapability) -> list[AtomicClaim]:
    """Structure explicit declarations without assuming unstated test knowledge."""
    atoms = []
    for raw in _clauses(capability.declared_constraints or ""):
        atoms.extend(
            _atoms(
                raw,
                kind="condition",
                pages=[],
                prefix=f"{capability.id}_condition_{len(atoms) + 1}",
                lab=True,
            )
        )
    notes = capability.notes or ""
    match = re.search(r"Reportable outputs:\s*(.*?)(?:\nSource:|$)", notes, re.I | re.S)
    output_details = match[1] if match else re.split(r"\nSource:", notes, maxsplit=1, flags=re.I)[0]
    if output_details:
        for raw in _clauses(output_details):
            atoms.extend(
                _atoms(
                    raw,
                    kind="reporting",
                    pages=[],
                    prefix=f"{capability.id}_output_{len(atoms) + 1}",
                    lab=True,
                )
            )
    for index, limit in enumerate(capability.structured_limits):
        if limit.parameter in {"condition", "scope", "instruction"}:
            for raw in limit.supported_values:
                atoms.extend(
                    _atoms(
                        raw,
                        kind="condition",
                        pages=[],
                        prefix=f"{capability.id}_limit_{index}_{len(atoms) + 1}",
                        lab=True,
                    )
                )
            continue
        parameter = {"angle": "measurement_angle"}.get(limit.parameter, limit.parameter)
        fields = (
            {"operator": "one_of", "values": limit.values}
            if limit.values
            else {
                "operator": "range",
                "minimum": limit.minimum,
                "maximum": limit.maximum,
            }
        )
        atoms.append(
            AtomicClaim(
                id=f"{capability.id}_limit_{index}",
                parameter_id=parameter,
                label=_LABELS.get(parameter, parameter.replace("_", " ").title()),
                kind="condition",
                unit=limit.unit,
                raw_text=f"Lab entered {limit.parameter} limit",
                normalization_state="reviewed",
                **fields,
            )
        )
    result = []
    for claim in atoms:
        claim = _intrinsic_stage(claim)
        # Measurement setup entered in the outputs/details text remains setup,
        # not a reportable specimen quantity. Keep other roles unchanged.
        if claim.parameter_id in _MEASUREMENT_SETUP and claim.kind == "reporting":
            claim = claim.model_copy(update={"kind": "condition"})
        result.append(claim)
    return result
