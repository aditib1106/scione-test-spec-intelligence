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
from hashlib import sha256

from scione.catalogs.models import LabCapability
from scione.numeric_literals import NUMBER, NUMBER_START, decimal_literal, source_numbers
from scione.parameters.registry import normalize_stage, resolve_definition
from scione.parameters.units import (
    canonical_unit_name,
    resolve_unit,
    source_numbers_for_unit,
    unit_is_stated,
)
from scione.source_text import citation_contains, citation_text
from scione.specifications.models import CustomerRequirement
from scione.structured.lexical_grounding import (
    SourceQuantity,
    source_operator_hints,
    source_quantities,
    source_unit_name,
    unit_token_is_standalone,
)
from scione.structured.models import (
    NORMALIZER_VERSION,
    AtomicClaim,
    ClaimKind,
    NormalizedRequirement,
)
from scione.structured.native_fields import represented_custom_scalar_field
from scione.structured.ordinal_sources import owned_ordinal_enumeration
from scione.structured.planning import (
    canonical_planning_parameter,
    is_planning_instruction,
    planning_quantities,
)
from scione.structured.source_clauses import (
    context_is_source_grounded,
    context_owned_word_spans,
    ordered_thermal_cycle_proof,
)
from scione.structured.source_occurrences import prove_same_numeric_occurrence

_NUMBER = NUMBER
_QUANTITY = re.compile(
    rf"{NUMBER_START}(?P<op>>=|<=|≥|≤|>|<)?\s*(?P<first>{_NUMBER})"
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


def _native_parameter_name(claim: AtomicClaim) -> str | None:
    """Name an otherwise usable numeric field without inventing its meaning.

    A meaningful label may supply a local field name. A generic label receives
    only a neutral source-quantity identifier, leaving scientific identity to
    semantic alignment. No meaning is inferred from units or raw prose. Ordinary
    value, unit, operator and physical-source checks still decide usability.
    """
    if claim.parameter_id is not None or claim.availability != "supported":
        return None
    prose = claim.operator in {"text", "required"}
    if prose:
        value, raw = citation_text(claim.text_value or ""), citation_text(claim.raw_text)
        # A native, explicitly extracted complete service/property statement
        # can retain its source identity without a registered scientific name.
        # Never turn a shaved parser word or a model paraphrase into that fact.
        def complete(text):
            return (
                len(re.findall(r"[\u3400-\u9fff]", text)) >= 6
                or len(re.findall(r"\b[A-Za-zÀ-ž][A-Za-zÀ-ž'-]*\b", text)) >= 3
            )

        if (
            _numeric_values(claim)
            or not value
            or not citation_contains(raw, value)
            or not complete(value)
            or not complete(raw)
        ):
            return None
        payload = json.dumps({
            "raw_text": raw, "text_value": value, "kind": claim.kind,
            "stage": claim.stage, "operator": claim.operator,
        }, sort_keys=True, ensure_ascii=False)
        return "source_fact_" + sha256(payload.encode()).hexdigest()[:16]
    if not _numeric_values(claim):
        return None
    label = " ".join(claim.label.casefold().split())
    generic = bool(re.fullmatch(
        r"(?:unstructured|unknown|unidentified|unspecified|customer|additional|source)?\s*"
        r"(?:requirement|condition|criterion|criteria|parameter|field|qualification|detail)s?"
        r"(?:\s+\d+)?|unknown|other|not specified|未知(?:参数|字段)?|客户(?:条件|要求)",
        label,
    ))
    if generic:
        # This is a local fact identifier, not a guessed scientific parameter.
        # Matching still has to establish its meaning from the owned quotation.
        source = json.dumps({
            "raw_text": citation_text(claim.raw_text),
            "kind": claim.kind,
            "stage": claim.stage,
            "unit": claim.unit,
            "operator": claim.operator,
            "values": [str(value) for value in (
                claim.value, claim.minimum, claim.maximum, claim.tolerance, *claim.values
            ) if value is not None],
        }, sort_keys=True, ensure_ascii=False)
        return "source_quantity_" + sha256(source.encode()).hexdigest()[:16]
    if definition := resolve_definition(label):
        return definition.id
    words = re.findall(r"[^\W\d_]+", label, re.UNICODE)
    if not words or sum(map(len, words)) < 4:
        return None
    slug = "_".join(re.findall(r"\w+", label, re.UNICODE))
    if len(slug) > 80:
        slug = slug[:60] + "_" + sha256(label.encode()).hexdigest()[:12]
    return "custom_" + slug


def _intrinsic_stage(claim: AtomicClaim) -> AtomicClaim:
    """Measurement identities with no exposure phase use one named stage."""
    count_unit = {"cycles": "cycles", "measurement_locations": "locations"}.get(claim.parameter_id)
    if count_unit and claim.unit is None:
        claim = claim.model_copy(update={"unit": count_unit})
    if claim.stage == "general" and claim.parameter_id in _MEASUREMENT_SETUP:
        return claim.model_copy(update={"stage": "measurement"})
    return claim


def _workflow_trigger_as_acceptance(claim: AtomicClaim) -> bool:
    """An explicitly labelled review/rejection trigger is not a typed pass limit.

    Do not invert the inequality or invent an acceptance rule. The original
    machine fact and source wording remain inspectable as unresolved content.
    Ordinary acceptance limits and separately classified instructions are intact.
    """
    return claim.kind == "criterion" and bool(
        re.search(
            r"\b(?:review|rejection|failure)[-_ ]+(?:trigger|threshold|condition|observation)\b|"
            r"\bnot\s+(?:an?\s+|a standalone\s+|a hard\s+)?acceptance\b|"
            r"复核触发|拒收触发|不是验收|非验收",
            claim.source_context or "",
            re.I,
        )
    )


def _absolute_quantity_with_delta_context(claim: AtomicClaim) -> bool:
    """Do not certify a spread/change as an absolute registered measurement.

    This veto uses explicit, claim-local functional wording. It does not guess
    a replacement parameter or search a full paragraph for a different fact.
    """
    definition = resolve_definition(claim.parameter_id)
    if (definition.id if definition else claim.parameter_id) not in {
        "rockwell_hardness",
        "vickers_hardness",
        "gloss",
        "film_thickness",
        "topcoat_thickness",
        "temperature",
        "tensile_strength",
        "tensile_force",
    }:
        return False
    return bool(
        re.search(
            r"\bmax(?:imum)?[\s-]*(?:minus|-)[\s-]*min(?:imum)?\b|"
            r"\bwithin[- ]specimen\s+spread\b|"
            r"\badjacent[- ](?:point[- ])?(?:hardness[- ])?difference\b|"
            r"\b(?:hardness|gloss|thickness|temperature)[- ]+(?:spread|loss|change|delta)\b|"
            r"最大值.*最小值.*差|点间硬度差|硬度变化量",
            claim.source_context or "",
            re.I,
        )
    )


def _text(value: str) -> str:
    value = (
        unicodedata.normalize("NFKC", value).replace("−", "-").replace("–", "-").replace("—", "-")
    )
    value = re.sub(r"(?<=\d)-minute\b", " min", value, flags=re.I)
    for word, number in _WORD_NUMBERS.items():
        value = re.sub(rf"\b{word}\b", number, value, flags=re.I)
    return citation_text(value)


def _stage(text: str, kind: str) -> str:
    text = text.casefold()
    if re.search(r"room.temperature recovery|recovery.*between transfer", text):
        return "cycle_recovery"
    if re.search(r"after cycling|post[- ]cycle|after[- ](?:the )?(?:thermal )?cycl", text):
        return "after_cycle"
    if re.search(
        r"conditioning|condition for|allow.*days|rh.*days|days.*rh|"
        r"(?:试验|测试|测量)前.*(?:平衡|调节|调湿|放置)|密封平衡|预处理|"
        r"konditionier|(?:vor der messung|vor der prüfung).*gelagert",
        text,
    ):
        return "conditioning"
    if re.search(r"as delivered|initial|baseline|before (?:the )?cycl|original value", text):
        return "initial"
    if re.search(r"cure|curing|metal temperature|oven setting", text):
        return "curing"
    if "measurement temperature" in text or re.search(
        r"\bmeasurement\s+(?:stage|setup|acceptance|criteri(?:on|a))\b", text
    ):
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
        and re.search(r"gloss|光泽", context)
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


def _explicit_operator(match, text: str) -> str | None:
    if match["op"]:
        return {"≤": "<=", "≥": ">="}.get(match["op"], match["op"])
    if match["second"]:
        return "range"
    if match["pm"]:
        return "="
    prefix = text[max(0, match.start() - 60) : match.start()]
    if re.search(r"(?:at least|no less than|not less than)\s*$", prefix, re.I):
        return ">="
    if re.search(
        r"(?:at most|no more than|not more than|may not exceed|must not exceed|"
        r"shall not exceed|up to)\s*$",
        prefix,
        re.I,
    ):
        return "<="
    return None


def _unparsed_qualification(
    text: str,
    spans: list[tuple[int, int]],
    results: list[AtomicClaim],
    *,
    source_text: str | None = None,
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
        "duration": r"duration|time|duraci[oó]n|tiempo",
        "relative_humidity": r"relative\s+humidity|humidity|rh",
        "cycles": r"thermal|temperature|cycling|cycles?|before|after|between\s+transfers",
        "gloss": r"specular|gloss|(?:表面)?光泽(?:度)?(?:为|是)?",
        "gloss_retention": r"gloss|retention|retain|retained|its|own|original|value",
        "measurement_angle": r"measurement|observation|angle",
        "measurement_locations": r"measurement|marked|mark|repeatable|map|locations?|positions?",
        "adhesion_class": r"adhesion|native|iso|class(?:es)?|only|acceptable",
        "color_difference": r"colou?r\s+difference|δ\s*e\*?\s*ab",
        "film_thickness": r"dry[- ]film|film|thickness|build",
        "topcoat_thickness": r"separate|topcoat|thickness",
        "illuminant": r"lighting|illuminant",
        "reference_panel": r"comparison|compare|untouched|reference|master|panel",
        "result_scope": r"each|every|marked|location|site|panel|on",
        "test_checkpoints": r"repeat|check|both|before|after|cycles?",
    }
    removable = [labels[item] for item in parameters if item in labels]
    for parameter in parameters:
        definition = resolve_definition(parameter)
        if definition:
            if definition.subject:
                removable.append(re.escape(definition.subject))
            for alias in sorted(
                {definition.id.replace("_", " "), *definition.aliases},
                key=len,
                reverse=True,
            ):
                # A Chinese label and its copula are one orthographic word;
                # Latin word boundaries alone do not separate e.g. 温度为.
                suffix = r"(?:为|是)?" if re.search(r"[\u3400-\u9fff]", alias) else ""
                removable.append(re.escape(alias) + suffix)
    stages = {item.stage for item in results}
    if stages - {"general"}:
        removable.append(r"stages?|setup")
    if results:
        # A request introduction is not an extra physical condition. Its
        # substantive words still need coverage by the retained facts below.
        removable.append(r"we\s+(?:want|require)|please")
        # These are labels for a role/stage already expressed by the facts,
        # not additional specimen requirements. Keep words after the label.
        removable.append(r"(?:acceptance\s+)?criteri(?:on|a)")
        if any(item.kind == "criterion" for item in results):
            removable.append(r"acceptance|(?:测量)?验收")
        for stage, words in {
            "measurement": r"measurement",
            "conditioning": r"conditioning|(?:预处理|调节)(?:阶段)?",
            "curing": r"curing|cure",
            "after_cycle": r"after[- ]cycle|post[- ]cycle",
            "exposure": r"exposure|exposici[oó]n",
        }.items():
            if stage in stages:
                removable.append(rf"(?:{words})(?:\s+(?:stage|setup))?")
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
    if cycle_steps:
        # Step identifiers are syntax only when those exact step numbers are
        # already represented. An unseen step number remains an unresolved fact.
        numbers = "|".join(str(step) for step in sorted(cycle_steps))
        removable.extend(
            [rf"step\s+(?:{numbers})(?!\d)", r"execute|these|thermal[- ]cycle|stages?|order"]
        )
    if "curing" in stages:
        removable.append(r"curing|cure")
    # These restrictions remain on the grounded primary atom. Do not make a
    # second unknown requirement out of an exact, already represented phrase.
    # Never remove a paraphrase or arbitrary words from the primary quotation.
    original = _text(source_text or text)
    context_coverage = []
    for item in results:
        for phrase in (item.source_context, item.configuration):
            phrase_text = _text(phrase or "").casefold()
            source = _text(source_text or text).casefold()
            if phrase_text and item.normalization_state != "unresolved":
                context_coverage.extend(context_owned_word_spans(phrase_text, original, item.stage))
            if phrase_text and re.search(rf"(?<!\w){re.escape(phrase_text)}(?!\w)", source):
                # A first parser pass may already have removed function words
                # from the residual. Match that exact syntactic projection as
                # well, only after proving the full phrase in the source.
                for represented in (phrase_text, _unparsed_qualification(phrase_text, [], [])):
                    if represented:
                        escaped = re.escape(represented)
                        residual = re.sub(rf"(?<!\w){escaped}(?!\w)", " ", residual)
    # A source-grounded native categorical/text value may already own words
    # left orphaned by the legacy numeric parser. Mask only words whose EVERY
    # literal occurrence in this source clause belongs to such a value span.
    # Never mask a broad raw_text quotation or an uncovered token. A numeric
    # descriptor/code stays explicitly retained in the exact native text value.
    covered: list[tuple[int, int]] = list(context_coverage)
    for item in results:
        if (
            item.normalization_state != "unresolved"
            and item.operator in {"text", "required"}
            and item.text_value
            and citation_contains(original, _text(item.text_value))
        ):
            spans = list(re.finditer(re.escape(_text(item.text_value)), original))
            if len(spans) == 1:
                covered.append(spans[0].span())
                if (
                    item.parameter_id == "test_atmosphere"
                    and not _numeric_values(item)
                    and citation_contains(original, _text(item.raw_text))
                    and citation_contains(_text(item.raw_text), _text(item.text_value))
                    and re.search(r"\batmosphere\b", item.raw_text, re.I)
                ):
                    # The separately grounded categorical value owns this
                    # closed category noun too. Its raw quotation still does
                    # not own any fixture/material/exclusion elsewhere in it.
                    removable.append(r"atmosphere")
    if covered:
        tokens: dict[str, list[tuple[int, int]]] = {}
        for word in re.finditer(r"\w+(?:[-']\w+)*", original):
            tokens.setdefault(word[0].casefold(), []).append(word.span())
        for word, locations in tokens.items():
            if all(
                any(start <= first and last <= end for start, end in covered)
                for first, last in locations
            ):
                residual = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", " ", residual)
    if any(item.operator == ">=" for item in results):
        removable.append(r"at\s+least|no\s+less\s+than|not\s+less\s+than")
    if any(item.operator == "<=" for item in results):
        removable.append(
            r"at\s+most|no\s+more\s+than|not\s+more\s+than|may\s+not\s+exceed|"
            r"must\s+not\s+exceed|shall\s+not\s+exceed|up\s+to"
        )
    # Negation, exclusions and unbounded-language qualifiers are intentionally
    # absent: 'except nitrogen' or 'up to' must not turn into an exact value.
    removable.append(
        r"at|and|for|or|with|the|a|an|is|of|to|as|be|shall|must|test|testing|"
        r"perform|performed|use|using|apply|during|maintain|measured|measure|report|reported|can|"
        r"support|supported|require|required|under|from|in|de"
    )
    for pattern in removable:
        residual = re.sub(rf"\b(?:{pattern})\b", " ", residual, flags=re.I)
    return " ".join(re.findall(r"[\w]+(?:[-'][\w]+)*", residual, flags=re.UNICODE))


def _atoms(
    raw: str,
    *,
    kind: ClaimKind,
    pages: list[int],
    prefix: str,
    name: str = "",
    lab: bool = False,
    lexical_context: str | None = None,
    source_field_raw: str | None = None,
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

    planned = planning_quantities(raw) if kind in {"condition", "instruction"} else []
    if planned:
        return [
            AtomicClaim(
                id=f"{prefix}_{index}",
                kind="instruction",
                stage="general",
                operator="=",
                raw_text=raw,
                source_pages=pages,
                normalization_state="parsed",
                **item,
            )
            for index, item in enumerate(planned, 1)
        ]

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

    non_acceptance = bool(
        re.search(
            r"\b(?:process[- ]only|not\s+(?:an?\s+)?acceptance|"
            r"process\s+(?:records?|targets?)\s+only)\b|"
            r"仅(?:为|作|用于).{0,35}(?:工艺|过程|供应商).{0,20}(?:记录|目标)|"
            r"不(?:由|作为).{0,30}(?:分层测量|独立判废)|非(?:独立)?判废",
            source_field_raw or text,
            re.I,
        )
    )
    if (non_acceptance and kind in {"criterion", "scope"}) or (
        kind == "criterion"
        and re.search(
            r"\bunacceptable\b|\bnot\s+(?:acceptable|accepted|permitted)\b|"
            r"\b(?:rejection|failure|review)[-_ ]+(?:threshold|trigger)\b",
            text,
            re.I,
        )
    ):
        # A rejection observation is not a nominal acceptance value. The
        # legacy parser cannot invert it or decide its scientific quantity;
        # preserve the literal clause for the native extractor/user instead.
        add(None, operator="text", text_value=text)
        return results

    # Output availability is explicit negative evidence, not an absent value.
    if lab and availability == "unsupported":
        if re.search(r"gloss|光泽", text, re.I):
            add("gloss", operator="required", text_value="Not reportable")
        if re.search(r"retention|retain", text, re.I) and re.search(r"gloss|光泽", text, re.I):
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
        r"(\d+)[- ]+(?:(?:marked|measurement|repeatable|test) )?(?:locations?|positions?)\b",
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
    lexical_quantities = source_quantities(text, context=lexical_context)
    for match in _QUANTITY.finditer(text):
        parameter, unit, factor = _parameter(match["unit"], text, name)
        if parameter is None or not unit_token_is_standalone(text, match.end("unit")):
            continue
        safe_legacy_quantity = True
        for field in ("first", "second", "tolerance"):
            if match[field] is None:
                continue
            start, end = match.span(field)
            candidates = [
                candidate
                for candidate in lexical_quantities
                if candidate.number_span == (start, end)
                and (
                    canonical_unit_name(candidate.unit) == canonical_unit_name(unit)
                    or parameter == "relative_humidity"
                    and candidate.unit in {"%", "% RH"}
                    and unit in {"%", "% RH"}
                )
                and _candidate_number(candidate) == Decimal(match[field])
                and candidate.operator_ambiguity is None
                and (field != "tolerance" or candidate.role == "tolerance")
                and (field != "first" or candidate.role != "tolerance")
            ]
            if not candidates:
                safe_legacy_quantity = False
        if not safe_legacy_quantity:
            # A limited prose parser must not invent '5 mm' from German
            # '1,5 mm', or reinterpret Spanish '1.020' as 1.02. The retained
            # model atoms can establish the original localized value separately.
            continue
        if mixed_percentages and "%" in match["unit"]:
            # Clause-wide context cannot identify which percentage belongs to
            # humidity versus a retained output. Keep that part unresolved.
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
        elif explicit_operator := _explicit_operator(match, text):
            fields["operator"] = explicit_operator
        if fields["operator"] == "=" and not match["pm"]:
            lexical_operators = {
                candidate.operator
                for candidate in lexical_quantities
                if candidate.number_span == match.span("first")
                and candidate.operator in {"<=", ">=", "<", ">"}
                and candidate.operator_ambiguity is None
            }
            if len(lexical_operators) == 1:
                fields["operator"] = next(iter(lexical_operators))
        if (
            kind == "criterion"
            and fields["operator"] == "="
            and not match["pm"]
            and not re.search(
                r"\b(?:exactly|equals?|nominal(?:ly)?|target(?:\s+value)?|must\s+be|shall\s+be)\b|"
                r"名义|目标值|等于|应为|必须为",
                text,
                re.I,
            )
        ):
            # A bare criterion value does not say whether it is a nominal,
            # minimum or maximum. Preserve the source instead of inventing '='.
            add(None, operator="text", text_value=raw)
            continue
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
            rf"class(?:es)?\s*:?\s*(<=|>=|≤|≥|<|>)?\s*({_NUMBER})"
            rf"\s*(?:(-|to|or)\s*({_NUMBER}))?",
            text,
            re.I,
        )
        if match:
            consume(match)
            operator, first, relation, second = match.groups()
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
                add(
                    "adhesion_class",
                    operator={"≤": "<=", "≥": ">="}.get(operator, operator) or "=",
                    value=Decimal(first),
                )
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
    from scione.structured.source_clauses import source_clauses

    return source_clauses(raw)


def _numeric_values(claim: AtomicClaim) -> set[Decimal]:
    return {
        value
        for value in [claim.value, claim.minimum, claim.maximum, claim.tolerance, *claim.values]
        if value is not None
    }


def _operator_grounded(claim: AtomicClaim, *, context: str | None = None) -> bool:
    """Reject a typed operator contradicting an explicit source quantity.

    Text/numeric presence alone does not prove that >= and <= mean the same
    thing. This check only uses unambiguous source expressions; unfamiliar
    wording is still retained for semantic review rather than rewritten here.
    """
    if claim.operator in {"text", "required"}:
        return True
    text = _text(claim.raw_text)
    expected: set[str] = set()
    numeric = _numeric_values(claim)
    candidates = _claim_source_quantities(claim, context=context)
    if any(
        candidate.operator_ambiguity
        and _candidate_number(candidate) in numeric
        and _source_unit_matches(candidate.unit, claim)
        for candidate in candidates
    ):
        return False
    for match in _QUANTITY.finditer(text):
        _, unit, _ = _parameter(match["unit"], text, claim.label)
        if unit != claim.unit:
            continue
        source_values = {
            Decimal(value)
            for value in [match["first"], match["second"], match["tolerance"]]
            if value is not None
        }
        if not numeric <= source_values:
            continue
        if operator := _explicit_operator(match, text):
            expected.add(operator)
    # The named, dimensionless colour scale has no physical-unit suffix.
    if claim.parameter_id == "color_difference":
        for match in re.finditer(
            rf"(?:Δ\s*E\*?ab|Δ\s*E|delta\s*e|colou?r difference)\s*:?\s*"
            rf"(?P<op><=|>=|≤|≥|<|>)\s*(?P<value>{_NUMBER})",
            text,
            re.I,
        ):
            if numeric <= {Decimal(match["value"])}:
                expected.add({"≤": "<=", "≥": ">="}.get(match["op"], match["op"]))
    definition = resolve_definition(claim.parameter_id)
    source_unit = claim.unit or (
        "class" if definition and definition.id == "adhesion_class" else None
    )
    if source_unit is not None:
        expected.update(
            source_operator_hints(text, unit=source_unit, values=numeric, context=context)
        )
    if (
        claim.kind == "criterion"
        and expected == {">"}
        and claim.operator == "<="
        and (
            re.search(r"\bno\s+(?:specimen|sample|panel)\s+may\b(?!\s+not\b)", text, re.I)
            or any(
                quantity.number_span == match.span("limit")
                and _candidate_number(quantity) == claim.value
                and _source_unit_matches(quantity.unit, claim)
                for match in re.finditer(
                    r"不得有[^。；;\d]{0,80}(?:超过|大于|高于)\s*(?P<limit>" + _NUMBER + ")",
                    text,
                )
                for quantity in candidates
            )
        )
        and not re.search(r"\bexcept\b|\bunless\b|除外|除非|例外", text, re.I)
    ):
        # Explicit prohibition of a value above x permits equality at x. This
        # validates an already extracted <= boundary; it never rewrites one.
        return True
    return not expected or claim.operator in expected


def _candidate_number(candidate: SourceQuantity) -> Decimal | None:
    if candidate.value is not None and candidate.ambiguity is None:
        return candidate.value
    if candidate.ambiguity == "ambiguous_decimal_or_thousands" and candidate.language is None:
        # Preserve the established strict dot-decimal interpretation only.
        return decimal_literal(candidate.raw_number)
    return None


def _claim_source_quantities(
    claim: AtomicClaim, *, context: str | None
) -> tuple[SourceQuantity, ...]:
    unknown_unit = _text(claim.unit) if claim.unit and resolve_unit(claim.unit) is None else None
    return source_quantities(_text(claim.raw_text), context=context, additional_unit=unknown_unit)


def _source_unit_matches(source_unit: str, claim: AtomicClaim) -> bool:
    wanted = source_unit_name(claim.unit) if claim.unit is not None else None
    if source_unit_name(source_unit) == wanted:
        return True
    definition = resolve_definition(claim.parameter_id)
    if definition and definition.id == "adhesion_class" and claim.unit is None:
        return source_unit == "class"
    if definition and definition.id == "relative_humidity":
        return source_unit in {"%", "% RH"} and wanted in {"%", "% RH"}
    return bool(
        claim.unit and resolve_unit(claim.unit) is None and _text(source_unit) == _text(claim.unit)
    )


def _quantity_roles_grounded(claim: AtomicClaim, *, context: str) -> bool:
    """Keep nominal values, tolerances and range endpoints in one source expression."""
    candidates = [
        row
        for row in _claim_source_quantities(claim, context=context)
        if _source_unit_matches(row.unit, claim) and _candidate_number(row) is not None
    ]
    if not candidates:
        return True  # Unit and bare-number checks separately reject missing evidence.
    groups: dict[tuple[int, int], list[SourceQuantity]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.expression_span, []).append(candidate)
    if claim.operator == "range":
        expected = {value for value in [claim.minimum, claim.maximum] if value is not None}
        return any(
            {row.role for row in group} == {"range_endpoint"}
            and {_candidate_number(row) for row in group} == expected
            and claim.minimum is not None
            and claim.maximum is not None
            for group in groups.values()
        )
    if claim.operator == "one_of":
        nominal = {
            _candidate_number(row)
            for group in groups.values()
            if not any(item.role == "tolerance" for item in group)
            for row in group
            if row.role != "tolerance"
        }
        return set(claim.values) <= nominal
    if claim.value is None:
        return True
    definition = resolve_definition(claim.parameter_id)
    if definition and definition.dimension == "temperature_difference":
        if any(
            row.role == "tolerance" and _candidate_number(row) == claim.value for row in candidates
        ):
            return claim.tolerance is None
    for group in groups.values():
        if not any(row.role == "value" and _candidate_number(row) == claim.value for row in group):
            continue
        tolerances = {_candidate_number(row) for row in group if row.role == "tolerance"}
        if tolerances == ({claim.tolerance} if claim.tolerance is not None else set()):
            return True
    return False


def _lexical_numeric_evidence(
    claim: AtomicClaim, *, context: str
) -> tuple[set[Decimal], set[Decimal], bool]:
    """Replace localized numeric spans, never union them with misparsed suffixes.

    Unattached literals keep the established strict grammar. A dot decimal with
    no locale evidence retains that grammar's interpretation only; translated
    or grouped readings need explicit surrounding source-language evidence.
    """
    text = _text(claim.raw_text)
    candidates = _claim_source_quantities(claim, context=context)
    masked = list(text)
    approved: set[Decimal] = set()
    attached = set()
    for candidate in candidates:
        start, end = candidate.number_span
        masked[start:end] = " " * (end - start)
        value = _candidate_number(candidate)
        if value is not None:
            approved.add(value)
            if _source_unit_matches(candidate.unit, claim):
                attached.add(value)
    approved.update(source_numbers(_text("".join(masked))))
    known_unit = resolve_unit(claim.unit) is not None
    return approved, attached, known_unit


def _contextualize(claim: AtomicClaim, context: str | None) -> AtomicClaim:
    """Carry field context without inventing or modifying a source quotation."""
    if not context:
        return claim
    hint = _stage(context, claim.kind)
    if _text(context).casefold() in {
        "general",
        "measurement",
        "conditioning",
        "curing",
        "initial",
        "after_cycle",
        "thermal_cycle",
        "cycle_recovery",
    }:
        hint = _text(context).casefold()
    if re.fullmatch(r"cure", _text(context), re.I):
        hint = "curing"
    updates = {"source_context": context}
    if hint != "general":
        if claim.stage == "general":
            updates["stage"] = hint
        elif claim.stage != hint:
            # An explicit source stage and its enclosing field disagree.
            # Preserve both for review rather than choosing a convenient one.
            updates.update(normalization_state="unresolved", availability="unknown")
    return claim.model_copy(update=updates)


def _numeric_identity(claim: AtomicClaim) -> tuple:
    return (
        claim.parameter_id,
        claim.kind,
        claim.operator,
        claim.value,
        claim.minimum,
        claim.maximum,
        claim.tolerance,
        tuple(claim.values),
        canonical_unit_name(claim.unit),
        claim.text_value,
        claim.availability,
    )


def _same_text_value(first: AtomicClaim, second: AtomicClaim) -> bool:
    if first.text_value == second.text_value:
        return True
    if first.parameter_id not in _MEASUREMENT_SETUP:
        # An unfamiliar code or category is not known to be case insensitive.
        return False
    return _text(first.text_value or "").casefold().rstrip(".;:") == _text(
        second.text_value or ""
    ).casefold().rstrip(".;:")


def _quoted_sequence(raw_text: str, clause: str) -> int | None:
    """Locate a unique short quotation within its explicit source step.

    A duration fragment need not repeat its parent's "step 2" words. It may
    inherit only the nearest preceding explicit step in this same source
    clause, with no other step boundary inside the fragment. Repeated quotes
    are ambiguous without offsets and deliberately remain unassociated.
    """
    quote, source = _text(raw_text).casefold(), _text(clause).casefold()
    matches = list(re.finditer(re.escape(quote), source))
    if len(matches) != 1:
        return None
    start, end = matches[0].span()
    preceding = []
    for match in re.finditer(r"\bstep\s*(\d+)\b", source):
        if start < match.start() < end:
            return None
        if match.start() <= start:
            preceding.append(int(match[1]))
    return preceding[-1] if preceding else None


def _sequence_quantity_grounded(claim: AtomicClaim) -> bool:
    """Check numbers inside the stated step when a quote spans several steps.

    A union of all numbers in a long quotation loses their step association.
    Preserve legitimate global quantities and short fragments; only an explicit
    sequenced atom with separable source step labels is checked here.
    """
    if claim.sequence is None or not _numeric_values(claim):
        return True
    text = _text(claim.raw_text)
    steps = list(re.finditer(r"\bstep\s*(\d+)\b", text, re.I))
    if len(steps) < 2:
        return True
    matching = [index for index, match in enumerate(steps) if int(match[1]) == claim.sequence]
    if len(matching) != 1:
        return False
    index = matching[0]
    end = steps[index + 1].start() if index + 1 < len(steps) else None
    segment = text[steps[index].end() : end]
    numbers = (
        source_numbers_for_unit(claim.unit, segment)
        if claim.unit and resolve_unit(claim.unit)
        else source_numbers(segment)
    )
    return _numeric_values(claim) <= numbers


def _local_source_texts(
    claim: AtomicClaim,
    clause: str,
    requirement: CustomerRequirement,
    context: str | None,
    enclosing_raw: str | None = None,
) -> list[str]:
    """Inspect only the local quotation line, not another test stage on the page."""
    needle = _text(claim.raw_text).casefold()
    result = [clause, context or ""]

    def local_lines(raw: str) -> list[str]:
        lines = raw.splitlines()
        located = [index for index, line in enumerate(lines) if needle in _text(line).casefold()]
        if len(located) != 1:
            return []
        index = located[0]
        fragments = [part for part in _clauses(lines[index]) if needle in _text(part).casefold()]
        local = fragments if len(fragments) == 1 else [lines[index]]
        # A standalone heading on the same page cannot establish this fact's
        # scope. It must immediately precede this fact inside the same literal
        # citation, with no intervening section, blank line or repeated quote.
        names = (
            r"measurement|conditioning|curing|initial|exposure|"
            r"thermal[- ]cycle|after[- ]cycle|cycle[- ]recovery"
        )
        if index > 0 and re.fullmatch(
            rf"(?:stage\s*:\s*(?:{names})|(?:{names})\s+(?:stage|setup))\s*[.;]?",
            _text(lines[index - 1]),
            re.I,
        ):
            local.append(lines[index - 1])
        return local

    if enclosing_raw:
        result.extend(local_lines(enclosing_raw))
    for evidence in requirement.evidence:
        if evidence.page in claim.source_pages:
            result.extend(local_lines(evidence.quote))
    return result


def _represented_numeric_fallback(
    fallback: AtomicClaim,
    supplied: AtomicClaim,
    *,
    clause: str,
    context: str | None,
    requirement: CustomerRequirement,
    occurrences: int,
    enclosing_raw: str | None = None,
) -> bool:
    """A grounded specific atom may replace only a weaker copy of that fact.

    A missing parser stage/step is not contrary evidence. Added specificity
    nevertheless needs local source support; a plausible model label, another
    stage elsewhere on the page, or a quarantined atom cannot hide a fallback.
    Raw unparsed qualifications are handled separately and remain visible.
    """
    if (
        fallback.normalization_state == "unresolved"
        or supplied.normalization_state == "unresolved"
        or not fallback.parameter_id
        or not _numeric_values(fallback)
        or _numeric_identity(supplied) != _numeric_identity(fallback)
        or (supplied.configuration != fallback.configuration and fallback.configuration is not None)
        or not set(fallback.source_pages) <= set(supplied.source_pages)
    ):
        return False
    model_text, clause_text = _text(supplied.raw_text).casefold(), _text(clause).casefold()
    if not (model_text in clause_text or clause_text in model_text):
        return False
    model_stage, fallback_stage = normalize_stage(supplied.stage), normalize_stage(fallback.stage)
    if model_stage != fallback_stage and fallback_stage != "general":
        return False
    if supplied.sequence != fallback.sequence and fallback.sequence is not None:
        return False
    model_context, fallback_context = (
        _text(supplied.source_context or "").casefold(),
        _text(fallback.source_context or "").casefold(),
    )
    if model_context != fallback_context and fallback_context:
        return False
    more_specific = (
        model_stage != fallback_stage
        or supplied.sequence != fallback.sequence
        or model_context != fallback_context
        or supplied.configuration != fallback.configuration
    )
    if not more_specific:
        return True
    # Without source offsets, two equal quantities in one clause cannot be
    # attributed to one narrow model quotation without hiding the other event.
    if occurrences != 1:
        return False
    source_texts = _local_source_texts(supplied, clause, requirement, context, enclosing_raw)
    if supplied.configuration != fallback.configuration and not re.search(
        rf"(?<!\w){re.escape(_text(supplied.configuration or '').casefold())}(?!\w)",
        clause_text,
    ):
        return False
    if supplied.sequence != fallback.sequence:
        steps = set(re.findall(r"\bstep\s*(\d+)\b", model_text))
        sequence = (
            int(next(iter(steps)))
            if len(steps) == 1
            else _quoted_sequence(supplied.raw_text, clause)
            if not steps
            else None
        )
        if supplied.sequence is None or sequence != supplied.sequence:
            return False
    if model_stage != fallback_stage:
        stages = set()
        for text in source_texts:
            stage = normalize_stage(_stage(_text(text), supplied.kind))
            if stage != "general":
                stages.add(stage)
            stages.update(
                normalize_stage(match)
                for match in re.findall(
                    r"\b(measurement|conditioning|curing|initial|exposure|"
                    r"thermal[- ]cycle|after[- ]cycle)"
                    r"\s+(?:stage|setup|acceptance|criterion|criteria)\b",
                    text,
                    re.I,
                )
            )
            header = re.fullmatch(r"stage\s*:\s*([\w -]+?)\s*[.;]?", _text(text), re.I)
            if header:
                stages.add(normalize_stage(header[1]))
        if stages != {model_stage}:
            return False
    if model_context != fallback_context:
        context_proved = any(
            context_is_source_grounded(model_context, _text(text), model_stage)
            for text in source_texts
        )
        cycle = ordered_thermal_cycle_proof(supplied, requirement)
        if cycle:
            if model_context in {
                f"{cycle.get('relative_phase')} step of each cycle",
                f"{cycle.get('relative_phase')} step",
            }:
                context_proved = True
            if cycle.get("verified_cycle_count") and re.fullmatch(
                r"(?:hot-to-cold\s+)?thermal\s+cycling", model_context
            ):
                context_proved = True
        if not context_proved:
            return False
    return True


def _represented_operational_category(
    claim: AtomicClaim, *, clause: str, kind: ClaimKind, pages: list[int]
) -> bool:
    """Deduplicate one fully represented operational category across raw roles.

    A material/resource may be placed in applicability while its atom is an
    operational condition. Only curated nonnumeric categories can cross those
    two roles, and every remaining word must be accounted for. A full quotation
    alone is not proof that a second exception or restriction was extracted.
    """
    definition = resolve_definition(claim.parameter_id)
    if (
        {claim.kind, kind} != {"scope", "condition"}
        or claim.normalization_state == "unresolved"
        or not definition
        or definition.rule not in {"category", "required_set", "boolean_presence"}
        or claim.operator not in {"text", "required"}
        or _numeric_values(claim)
        or not claim.text_value
        or not set(pages) <= set(claim.source_pages)
        or _text(claim.raw_text).casefold().rstrip(".;") != _text(clause).casefold().rstrip(".;")
    ):
        return False
    residual = _text(clause).casefold()
    value = re.escape(_text(claim.text_value).casefold())
    residual, count = re.subn(rf"(?<!\w){value}(?!\w)", " ", residual, count=1)
    if count != 1:
        return False
    for label in (definition.label, *definition.aliases):
        residual = re.sub(rf"(?<!\w){re.escape(label.casefold())}(?!\w)", " ", residual)
    return not _unparsed_qualification(residual, [], [claim], source_text=clause)


def _represented_verbatim_text(
    fallback: AtomicClaim, supplied: AtomicClaim, *, pages: list[int]
) -> bool:
    """Keep one copy of an entire identical textual obligation.

    This is not semantic equivalence or a claim that the obligation is usable.
    In particular an unresolved model text remains unresolved. Numeric, partial
    and differently worded statements are excluded so a broad quotation cannot
    swallow a missing value, qualifier or neighboring requirement.
    """
    return bool(
        fallback.kind == supplied.kind
        and not _numeric_values(fallback)
        and not _numeric_values(supplied)
        and fallback.operator in {"text", "required"}
        and supplied.operator in {"text", "required"}
        and fallback.text_value
        and supplied.text_value
        and _text(fallback.raw_text) == _text(supplied.raw_text)
        and _text(fallback.text_value) == _text(supplied.text_value)
        and _text(fallback.text_value) == _text(fallback.raw_text)
        and set(pages) <= set(supplied.source_pages)
    )


def _scientific_identity_problem(
    claim: AtomicClaim, requirement: CustomerRequirement
) -> str | None:
    """Veto explicit subdimension/layer contradictions without guessing a repair."""
    definition = resolve_definition(claim.parameter_id)
    parameter = definition.id if definition else claim.parameter_id
    local = " ".join((claim.raw_text, claim.source_context or ""))
    if (
        parameter == "specimen_length"
        and re.search(
            r"\b(?:thick|thickness)\b|厚度|板厚|膜厚",
            local,
            re.I,
        )
        and not re.search(r"\b(?:length|long)\b|长度|长边", claim.raw_text, re.I)
    ):
        return "Specimen length conflicts with source-stated thickness."
    if (
        parameter == "specimen_length"
        and claim.operator in {"text", "required"}
        and re.search(
            r"\bregrind\b|回料|再生料",
            claim.raw_text,
            re.I,
        )
    ):
        return "A material/regrind instruction does not identify specimen length."
    if parameter == "topcoat_thickness" and _numeric_values(claim):
        layer = re.compile(r"\btop[ -]?coat\b|面漆|面层", re.I)
        if layer.search(claim.raw_text):
            return None
        # A short source fragment may explicitly refer to its immediate
        # antecedent ('separate topcoat thickness; the latter ...'). Evidence
        # elsewhere in the document or a model-generated label is insufficient.
        needle = _text(claim.raw_text)
        for evidence in requirement.evidence:
            source = _text(evidence.quote)
            if evidence.page not in claim.source_pages:
                continue
            occurrences = list(re.finditer(re.escape(needle), source))
            if len(occurrences) != 1:
                continue
            prefix = source[max(0, occurrences[0].start() - 160) : occurrences[0].start()]
            if re.search(
                r"\btop[ -]?coat\s+(?:film\s+)?thickness\s*[:=]?\s*$|面漆厚度\s*[:：]?\s*$",
                prefix,
                re.I,
            ):
                return None
            if re.search(
                r"\b(?:separate\s+)?top[ -]?coat\s+thickness\s*[;,，；:]\s*"
                r"(?:the\s+)?latter\s*(?:must\s*)?$",
                prefix,
                re.I,
            ):
                return None
            if layer.search(prefix) and re.match(r"(?:the\s+)?latter\b", needle, re.I):
                return None
            anchors = list(layer.finditer(prefix))
            if anchors:
                tail = prefix[anchors[-1].end() :]
                if re.search(r"\blatter\b", tail, re.I) and not re.search(r"\d|[。.!?]", tail):
                    return None
        return "Topcoat/layer specificity is not established by the quoted quantity."
    return None


def _source_codes_grounded(claim: AtomicClaim) -> bool:
    """Require exact source identity for code-like categorical values.

    Plain translated words remain semantic data. Digit-bearing identifiers and
    mixed-case punctuated resource codes cannot be invented or case-folded.
    Presence is necessary evidence, not proof of the broader clause's meaning.
    """
    if claim.operator not in {"text", "required"} or not claim.text_value:
        return True
    tokens = re.findall(
        r"[A-Za-z][A-Za-z0-9_+./:#@-]*", unicodedata.normalize("NFKC", claim.text_value)
    )
    source = unicodedata.normalize("NFKC", claim.raw_text)
    codes = [
        token.rstrip(".,;:")
        for token in tokens
        if re.search(r"\d", token) or (re.search(r"[A-Z]", token) and re.search(r"[-_#@:]", token))
    ]
    for token in codes:
        if not token:
            continue
        spans = list(
            re.finditer(
                r"(?<![A-Za-z0-9_+./:#@-])"
                + re.escape(token)
                + r"(?![A-Za-z0-9_+/:#@-]|\.[A-Za-z0-9])",
                source,
            )
        )
        if not spans or not any(
            not re.search(
                r"(?:\b(?:not|except|excluding|without)\s+|不是|并非|而非|非)\s*$",
                source[max(0, span.start() - 30) : span.start()],
                re.I,
            )
            for span in spans
        ):
            return False
    return True


def known_source_fact_problem(claim: AtomicClaim, requirement: CustomerRequirement) -> str | None:
    """Explain a narrow scientific contradiction, not a generic confidence flag.

    This does not flag unfamiliar/custom units, normal parser fallbacks or
    untranslated prose solely because they need more interpretation. It never
    repairs a source value, chooses a replacement parameter or predicts support.
    """
    from scione.specifications.review_audits import compound_reporting_problem

    if problem := compound_reporting_problem(claim):
        return problem
    if problem := _scientific_identity_problem(claim, requirement):
        return problem
    if not _source_codes_grounded(claim):
        return "A code-like categorical value is not stated in its source quote."
    definition = resolve_definition(claim.parameter_id)
    if _numeric_values(claim) and definition and definition.value_kind == "number":
        unit = resolve_unit(claim.unit)
        if (
            unit
            and definition.dimension
            and resolve_unit(claim.unit, dimension=definition.dimension) is None
        ):
            return "The recorded parameter and source unit describe different physical dimensions."
        if claim.unit and "/" in claim.unit:
            pieces = claim.unit.split("/")
            if len(pieces) == 2:
                numerator = resolve_unit(pieces[0])
                denominator = resolve_unit(re.sub(r"[²³]$|\^[23]$", "", pieces[1]))
                if numerator and denominator and numerator.dimension == definition.dimension:
                    return "A per-unit/rate quantity is not the recorded absolute parameter."
    if _numeric_values(claim) and claim.operator not in {"text", "required"}:
        hints = [
            quantity
            for quantity in _claim_source_quantities(claim, context=claim.raw_text)
            if _candidate_number(quantity) in _numeric_values(claim)
            and _source_unit_matches(quantity.unit, claim)
            and quantity.operator is not None
            and quantity.operator_ambiguity is None
        ]
        if (
            hints
            and not _operator_grounded(claim, context=claim.raw_text)
            and not owned_ordinal_enumeration(claim, requirement)
        ):
            return "The typed comparison boundary needs verification against its quoted source."
    return None


def _named_scale_unit_proof(claim, requirement, proofs):
    """Bind a server-supplied physical proof; this is not a client trust flag."""
    proof = (proofs or {}).get(claim.id)
    if not isinstance(proof, dict):
        return None
    from scione.specifications.named_scale_units import _parse_row

    try:
        valid = (
            proof.get("rule") == "source_named_scale_summary_unit"
            and proof.get("policy_version") == "source_named_scale_citation_v1"
            and proof.get("requirement_id") == requirement.id
            and proof.get("source_claim_id") == claim.id
            and proof.get("field") == "unit"
            and proof.get("source") == claim.unit
            and (claim.unit is None or canonical_unit_name(claim.unit) == "ΔE*ab")
            and proof.get("canonical") == "ΔE*ab"
            and (definition := resolve_definition(claim.parameter_id))
            and definition.id == "color_difference"
            and claim.kind == "criterion"
            and claim.stage in {"general", "measurement"}
            and claim.configuration is None
            and claim.sequence is None
            and claim.value is not None
            and claim.minimum is None
            and claim.maximum is None
            and claim.tolerance is None
            and not claim.values
            and proof.get("operator") == claim.operator
            and Decimal(proof["value"]) == claim.value
            and proof.get("body_quote") == claim.raw_text
            and isinstance(proof.get("source_quote"), str)
            and _parse_row(proof["source_quote"]) == (claim.operator, claim.value)
            and proof.get("source_quote_sha256")
            == sha256(proof["source_quote"].encode()).hexdigest()
            and any(
                evidence.page == proof.get("source_page")
                and citation_contains(evidence.quote, proof["source_quote"])
                for evidence in requirement.evidence
            )
            and any(
                evidence.page in claim.source_pages
                and citation_contains(evidence.quote, claim.raw_text)
                for evidence in requirement.evidence
            )
            and re.fullmatch(r"[a-f0-9]{64}", proof.get("document_sha256", ""))
        )
    except (KeyError, TypeError, ValueError):
        return None
    return dict(proof) if valid else None


def _native_comparison_coverage(
    fallback: AtomicClaim,
    parsed: list[AtomicClaim],
    supplied: list[AtomicClaim],
    *,
    clause: str,
    pages: list[int],
    requirement: CustomerRequirement,
) -> dict | None:
    """Prove a legacy parser row adds no independently missing source fact.

    Native text values/context may account for prose across extraction roles.
    A broad quotation alone never owns its qualifiers. Numeric rows require
    matching typed payloads and enough grounded native occurrences; a missing
    quantity, material, negation or unknown qualifier remains a real audit row.
    This only removes a duplicate demand, never decides lab support.
    """
    source = _text(clause).casefold().rstrip(".;")
    local = [
        claim
        for claim in supplied
        if claim.normalization_state != "unresolved"
        and set(pages) <= set(claim.source_pages)
        and (
            _text(claim.raw_text).casefold().rstrip(".;") in source
            or source in _text(claim.raw_text).casefold().rstrip(".;")
        )
    ]
    if not local:
        return None
    covered = []
    basis = None
    if fallback.parameter_id and _numeric_values(fallback):

        def identity(claim):
            # A numeric text_value is an accompanying label, not another
            # quantity. Preserve it on the native fact but do not require the
            # older parser to manufacture the same presentation text.
            definition = resolve_definition(claim.parameter_id)
            unit = resolve_unit(claim.unit, dimension=definition.dimension if definition else None)
            payload = list(_numeric_identity(claim))
            # The native extractor owns whether a source quantity is an
            # execution condition or an acceptance criterion. A legacy parser
            # reading the same quantity from a summary must not manufacture a
            # second demand merely because its coarse field role differs.
            payload[1] = None
            payload[-2] = None
            if unit:
                payload[-3] = (unit.dimension, unit.factor, unit.offset)
            return tuple(payload)

        candidates = [
            claim
            for claim in local
            if identity(claim) == identity(fallback)
            and (
                not claim.configuration
                or context_is_source_grounded(claim.configuration, clause, claim.stage)
            )
        ]
        # Repeated identical values need an equal number of separately retained
        # native facts. One duration may not erase both legs of a thermal cycle.
        needed = sum(identity(row) == identity(fallback) for row in parsed)
        if len(candidates) >= needed and all(
            normalize_stage(claim.stage) == normalize_stage(fallback.stage)
            or normalize_stage(fallback.stage) == "general"
            or ordered_thermal_cycle_proof(claim, requirement)
            for claim in candidates
        ):
            covered, basis = candidates, "grounded_native_quantity_multiset"
    elif not _numeric_values(fallback):
        represented_spans = []
        for claim in local:
            if _numeric_values(claim):
                proof = prove_same_numeric_occurrence(
                    claim, claim, source_text=clause, source_pages=pages
                )
                if proof:
                    represented_spans.extend([*proof.number_spans, proof.unit_span])
        # The original full clause is used for ownership. Residual parser text
        # such as a preposition is not promoted into a separate lab capability.
        # Native categorical values own their exact words, not their quotation.
        if not _unparsed_qualification(clause, represented_spans, local, source_text=clause):
            covered, basis = local, "grounded_native_text_and_context"
        else:
            for claim in local:
                if (
                    claim.operator in {"text", "required"}
                    and claim.text_value
                    and not _numeric_values(claim)
                    and _text(claim.text_value).casefold().rstrip(".;")
                    == _text(fallback.text_value or "").casefold().rstrip(".;")
                ):
                    covered, basis = [claim], "identical_native_text_obligation"
                    break
    if not covered:
        return None
    return {
        "rule": "native_source_fact_coverage",
        "basis": basis,
        "source_claim_id": fallback.id,
        "represented_by": [claim.id for claim in covered],
        "source_note": fallback.model_dump(mode="json"),
        "source_values_preserved": True,
    }


def _native_clause_audit(
    parsed: list[AtomicClaim],
    supplied: list[AtomicClaim],
    *,
    clause: str,
    pages: list[int],
    requirement: CustomerRequirement,
) -> tuple[dict, list[str]]:
    """Keep the legacy parser as an omission audit, not a second extractor.

    Native source-grounded atoms remain the one comparison vocabulary. The
    old parser's complete output and original clause survive separately, so
    nothing is silently discarded. Independently uncovered quantities and
    explicit material/resource restrictions request source review rather than
    pretend to be lab declarations with guessed scientific identities.
    """
    covered = []
    uncovered = []
    problems = []
    def quantity_present(fallback: AtomicClaim) -> list[AtomicClaim]:
        """A source audit asks whether a quantity survived, not lab coverage.

        A generic parser label such as ``temperature`` may be less specific
        than the native ``air_temperature``. Summary/body quotations can also
        differ while repeating the same value. Preserve their native ownership
        and roles; do not manufacture a second claim to express that difference.
        """
        wanted = _numeric_values(fallback)
        if not wanted:
            return []
        candidates = []
        for claim in supplied:
            if claim.normalization_state == "unresolved" or not _numeric_values(claim):
                continue
            first, second = resolve_unit(fallback.unit), resolve_unit(claim.unit)
            source_definition = resolve_definition(fallback.parameter_id)
            native_definition = resolve_definition(claim.parameter_id)
            if (
                source_definition is not None and native_definition is not None
                and source_definition.id == native_definition.id == "relative_humidity"
            ):
                # '%' and '% RH' express the same retained humidity quantity.
                # Do not apply this bridge to other percentage-valued properties.
                first = resolve_unit(fallback.unit, dimension="humidity")
                second = resolve_unit(claim.unit, dimension="humidity")
            same_unit = canonical_unit_name(fallback.unit) == canonical_unit_name(claim.unit)
            if is_planning_instruction(fallback) and is_planning_instruction(claim):
                same_unit = fallback.unit is None or same_unit
            # A source summary can omit a named ordinal scale already stated
            # by its exact property label and native body criterion.
            if fallback.unit is None and fallback.parameter_id == claim.parameter_id:
                same_unit = True
            if not same_unit and first and second:
                same_unit = (
                    first.dimension == second.dimension
                    and first.factor == second.factor
                    and first.offset == second.offset
                )
            if not same_unit or wanted != _numeric_values(claim):
                continue
            first_stage = normalize_stage(fallback.stage)
            second_stage = normalize_stage(claim.stage)
            if first_stage != "general" and first_stage != second_stage:
                continue
            if not set(pages).intersection(claim.source_pages):
                continue
            candidates.append(claim)
        repetitions = sum(
            _numeric_values(row) == wanted
            and canonical_unit_name(row.unit) == canonical_unit_name(fallback.unit)
            for row in parsed
        )
        return candidates if len(candidates) >= repetitions else []

    for fallback in parsed:
        proof = _native_comparison_coverage(
            fallback, parsed, supplied, clause=clause, pages=pages, requirement=requirement
        )
        if proof:
            covered.append(proof)
            continue
        if fallback.parameter_id and _numeric_values(fallback):
            present = quantity_present(fallback)
            if present:
                covered.append({
                    "represented_by": [claim.id for claim in present],
                    "basis": "native_quantity_presence_audit",
                    "source_values_preserved": True,
                })
                continue
        uncovered.append(fallback)
        if fallback.parameter_id and _numeric_values(fallback):
            problems.append(f"Unrepresented source quantity: {fallback.display_value}.")
    local = [
        claim
        for claim in supplied
        if claim.normalization_state != "unresolved"
        and set(pages) <= set(claim.source_pages)
        and (
            citation_contains(clause, claim.raw_text)
            or citation_contains(claim.raw_text, clause)
        )
    ]
    represented_spans = []
    for claim in local:
        if _numeric_values(claim):
            proof = prove_same_numeric_occurrence(
                claim, claim, source_text=clause, source_pages=pages
            )
            if proof:
                represented_spans.extend([*proof.number_spans, proof.unit_span])
    residual = _unparsed_qualification(clause, represented_spans, local, source_text=clause)
    for quantity in source_quantities(clause):
        if quantity.value is None or quantity.ambiguity:
            continue
        represented = False
        for claim in supplied:
            if claim.normalization_state == "unresolved":
                continue
            if not set(pages).intersection(claim.source_pages):
                continue
            if (
                quantity.value in _numeric_values(claim)
                and _source_unit_matches(quantity.unit, claim)
            ):
                represented = True
                break
            # An explicitly preserved structured categorical/dimensional value
            # may own its literal quantities; its broad source quote may not.
            value = _text(claim.text_value or "")
            if value and citation_contains(clause, value):
                if any(
                    item.value == quantity.value and item.unit == quantity.unit
                    for item in source_quantities(value)
                ):
                    represented = True
                    break
        if not represented:
            problems.append(
                "Unrepresented source quantity: "
                + clause[slice(*quantity.expression_span)].strip().rstrip(".;")
                + "."
            )
    # These are omission signals only. The audit does not interpret an unseen
    # material, negate a requirement, or manufacture a capacity comparison.
    # Full remaining prose is retained even when this narrow signal is absent.
    if residual and re.search(
        r"\b(?:nitrogen|argon|oxygen|helium|alumini?um|copper|stainless|"
        r"fixture|jig|apparatus|reagent|atmosphere|substrate|equipment)\b|"
        r"氮气|氩气|氧气|夹具|试剂|基材|设备|除外|不得使用",
        residual,
        re.I,
    ):
        problems.append("Unrepresented source material or resource restriction: " + residual + ".")
    identifier = sha256((str(pages) + "\n" + clause).encode()).hexdigest()[:16]
    return (
        {
            "rule": "native_source_clause_audit",
            "audit_id": f"{requirement.id}_source_audit_{identifier}",
            "source_clause": clause,
            "source_pages": pages,
            "represented_by": sorted({
                identifier for proof in covered for identifier in proof["represented_by"]
            }),
            "parser_proposals": [claim.model_dump(mode="json") for claim in parsed],
            "unrepresented_parser_ids": [claim.id for claim in uncovered],
            "uninterpreted_source_text": residual or None,
            "requires_source_review": bool(problems),
            "issues": list(dict.fromkeys(problems)),
            "establishes_lab_support": False,
            "comparison_demand_created": False,
        },
        list(dict.fromkeys(problems)),
    )


def _typed_literal_projection(claim: AtomicClaim) -> AtomicClaim | None:
    """Recover a complete numeric literal put in a native text field.

    This is a lossless representation repair, not semantic interpretation:
    require one closed quantity, the same registered numeric parameter and
    compatible explicit units. The original quote, context and text remain.
    All ordinary source/page/operator grounding still runs on the result.
    """
    if (
        claim.operator not in {"text", "required"}
        or not claim.text_value
        or _numeric_values(claim)
        or claim.availability != "supported"
    ):
        return None
    definition = resolve_definition(claim.parameter_id)
    literal = _text(claim.text_value)
    if (
        definition is None
        or definition.value_kind != "number"
        or _QUANTITY.fullmatch(literal) is None
        or not citation_contains(claim.raw_text, literal)
    ):
        return None
    parsed = _atoms(
        literal,
        kind=claim.kind,
        pages=claim.source_pages,
        prefix=claim.id,
        name=claim.label,
    )
    if len(parsed) != 1:
        return None
    atom = parsed[0]
    if (
        atom.parameter_id != definition.id
        or atom.normalization_state == "unresolved"
        or not _numeric_values(atom)
        or (claim.unit and canonical_unit_name(claim.unit) != canonical_unit_name(atom.unit))
    ):
        return None
    return claim.model_copy(update={
        key: getattr(atom, key)
        for key in ("operator", "value", "minimum", "maximum", "tolerance", "values", "unit")
    })


def normalize_requirement(
    requirement: CustomerRequirement,
    *,
    source_unit_proofs: dict | None = None,
    native_first: bool = False,
) -> NormalizedRequirement:
    """Use grounded model atoms when present; safely structure older text runs.

    ``native_first`` is an explicit comparison projection, never an archive
    migration. Native facts stay intact and the text parser audits omissions;
    its weaker copies of already represented facts are retained as audit notes.
    Raw-only extractions keep the legacy behavior.
    """
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

    field_values: list[tuple[ClaimKind, str, str, str | None, str | None]] = []
    seen_fields: set[tuple] = set()

    def field(kind: ClaimKind, raw: str, name: str, context=None, unit=None) -> None:
        identity = (kind, _text(raw), name, context, unit)
        if identity not in seen_fields:
            seen_fields.add(identity)
            field_values.append((kind, raw, name, context, unit))

    for value in requirement.conditions:
        field("condition", value.raw_value, value.name, value.context, value.unit)
    for value in requirement.acceptance_criteria:
        field("criterion", value.raw_value, value.name, value.context, value.unit)
    for value in requirement.applicability:
        field("scope", value, "Scope")
    for value in requirement.special_instructions:
        field("instruction", value, "Instruction")
    raw_sources = [value for _, value, _, _, _ in field_values] + [
        item.quote for item in requirement.evidence
    ]
    atoms = []
    review_issues: list[str] = []
    normalization_actions: list[dict] = list(
        (source_unit_proofs or {}).get("__source_derivations__", {}).get("actions", [])
    )
    model_identities: dict[str, int] = {}
    supplied = getattr(requirement, "atomic_claims", [])
    for item in supplied:
        claim = AtomicClaim.model_validate(
            item.model_dump() if isinstance(item, AtomicClaim) else item
        )
        parameter_name = _native_parameter_name(claim)
        original_label = claim.label
        if parameter_name:
            claim = claim.model_copy(update={
                "parameter_id": parameter_name, "normalization_state": "parsed"
            })
        literal_projection = _typed_literal_projection(claim)
        if literal_projection is not None:
            normalization_actions.append({
                "rule": "source_proved_numeric_text_literal",
                "source_claim_id": claim.id,
                "original_operator": claim.operator,
                "original_text_value": claim.text_value,
                "original_quote_preserved": True,
                "canonical_operator": literal_projection.operator,
                "canonical_unit": literal_projection.unit,
            })
            claim = literal_projection
        unit_proof = _named_scale_unit_proof(claim, requirement, source_unit_proofs)
        if unit_proof:
            normalization_actions.append(unit_proof)
            claim = claim.model_copy(update={"unit": unit_proof["canonical"]})
        ordinal_proof = owned_ordinal_enumeration(claim, requirement)
        if ordinal_proof:
            normalization_actions.append(ordinal_proof)
        # Intrinsic count units must be checked against their own source
        # classifier before they can be used for display or matching.
        count_unit = {"cycles": "cycles", "measurement_locations": "locations"}.get(
            claim.parameter_id
        )
        if count_unit and claim.unit is None:
            claim = claim.model_copy(update={"unit": count_unit})
        grounded = any(
            _text(claim.raw_text).casefold() in _text(raw).casefold() for raw in raw_sources
        )
        # A hyphen after a number separates a range; it does not negate the
        # second endpoint. Leading signs, including negative temperatures, stay signed.
        lexical_context = "\n".join(item.quote for item in requirement.evidence)
        numbers, lexical_attached, _known_unit = _lexical_numeric_evidence(
            claim, context=lexical_context
        )
        numeric_values = [
            value
            for value in [claim.value, claim.tolerance, claim.minimum, claim.maximum]
            if value is not None
        ] + claim.values
        numeric_grounding = all(value in numbers for value in numeric_values) and (
            _sequence_quantity_grounded(claim)
        )
        grounded_pages = {
            evidence.page
            for evidence in requirement.evidence
            if _text(claim.raw_text).casefold() in _text(evidence.quote).casefold()
        }
        planning_parameter = canonical_planning_parameter(claim)
        if (
            planning_parameter
            and grounded
            and claim.source_pages
            and set(claim.source_pages) <= grounded_pages
        ):
            normalization_actions.append(
                {
                    "rule": "source_proved_planning_count_classifier",
                    "source_claim_id": claim.id,
                    "original_parameter_id": claim.parameter_id,
                    "canonical_parameter_id": planning_parameter,
                    "value_unit_context_raw_preserved": True,
                }
            )
            claim = claim.model_copy(update={"parameter_id": planning_parameter})
        # Numeric shape alone cannot turn "23 °C" into "23 hours".
        physical_units = {
            "°C",
            "°F",
            "K",
            "h",
            "min",
            "s",
            "days",
            "µm",
            "μm",
            "nm",
            "mm",
            "cm",
            "m",
            "in",
            "Pa",
            "kPa",
            "MPa",
            "bar",
            "N",
            "kN",
            "g",
            "kg",
            "mg",
            "GU",
            "% RH",
            "%",
        }
        source_units = {
            _parameter(match["unit"], _text(claim.raw_text), claim.label)[1]
            for match in _QUANTITY.finditer(_text(claim.raw_text))
        }
        unit_grounding = (
            not numeric_values
            or claim.unit not in physical_units
            or claim.unit in source_units
            or unit_is_stated(claim.unit, claim.raw_text)
        )
        if numeric_values and claim.unit in physical_units:
            attached = source_numbers_for_unit(claim.unit, _text(claim.raw_text))
            # Preserve the legacy "degrees C" spelling while still requiring
            # each quantity to be attached to the claimed source unit.
            for found in _QUANTITY.finditer(_text(claim.raw_text)):
                source_unit = resolve_unit(found["unit"])
                source_name = source_unit.name if source_unit else None
                if re.fullmatch(r"degrees?\s*C", found["unit"], re.I):
                    source_name = "°C"
                if source_name == canonical_unit_name(claim.unit):
                    attached.update(
                        Decimal(value)
                        for value in (found["first"], found["second"], found["tolerance"])
                        if value is not None
                    )
            unit_grounding = unit_grounding and set(numeric_values) <= attached
        if numeric_values and claim.unit is not None:
            # The stricter lexer rejects shorter-unit matches inside mm²,
            # mm/s, N-m and ambiguous localized-number suffixes.
            unit_grounding = set(numeric_values) <= lexical_attached
        elif numeric_values:
            # A dimensionless custom atom may use an unattached source number;
            # it cannot silently discard a unit attached to its only evidence.
            bare_source = list(_text(claim.raw_text))
            for candidate in _claim_source_quantities(claim, context=lexical_context):
                start, end = candidate.number_span
                bare_source[start:end] = " " * (end - start)
            bare_numbers = source_numbers(_text("".join(bare_source)))
            implicit_definition = resolve_definition(claim.parameter_id)
            if implicit_definition and implicit_definition.id == "adhesion_class":
                # This named ordinal scale may omit a separate display unit.
                # A source prefix such as 'class <=1' still owns that number;
                # it is neither an unrelated physical unit nor a bare guess.
                bare_numbers.update(lexical_attached)
            unit_grounding = set(numeric_values) <= bare_numbers
        if planning_parameter and is_planning_instruction(claim):
            # The complete source-owned planning grammar proves the count and
            # role. It is not a physical capacity or a fabricated unit bridge.
            numeric_grounding = unit_grounding = True
        if unit_proof:
            # The physical, uniquely owned summary supplies only the missing
            # named scale. Values, operator, raw body, scope and all other
            # scientific checks continue to use the original source atom.
            unit_grounding = True
        definition = resolve_definition(claim.parameter_id)
        identity_problem = known_source_fact_problem(claim, requirement)
        codes_grounded = _source_codes_grounded(claim)
        if identity_problem or not codes_grounded:
            review_issues.append(
                f"{requirement.id} {claim.id}: "
                + (
                    identity_problem
                    or "A code-like categorical value is not stated in its source quote."
                )
            )
        dimension_grounding = not (
            numeric_values
            and definition
            and (
                definition.value_kind != "number"
                or resolve_unit(claim.unit, dimension=definition.dimension) is None
            )
        )
        if (
            not grounded
            or not claim.source_pages
            or not set(claim.source_pages).issubset(grounded_pages)
            or not numeric_grounding
            or not unit_grounding
            or not dimension_grounding
            or not _quantity_roles_grounded(claim, context=lexical_context)
            or not (_operator_grounded(claim, context=lexical_context) or ordinal_proof)
            or _workflow_trigger_as_acceptance(claim)
            or _absolute_quantity_with_delta_context(claim)
            or identity_problem is not None
            or not codes_grounded
        ):
            # Preserve the original claim visibly; do not let fabricated values support a match.
            claim = claim.model_copy(
                update={
                    "parameter_id": None,
                    "normalization_state": "unresolved",
                    "availability": "unknown",
                }
            )
        if parameter_name and claim.normalization_state != "unresolved":
            quantity = parameter_name.startswith("source_quantity_")
            prose_fact = parameter_name.startswith("source_fact_")
            neutral = quantity or prose_fact
            normalization_actions.append({
                "rule": (
                    "grounded_native_local_quantity" if quantity
                    else "grounded_native_local_fact" if prose_fact
                    else "native_label_parameter_name"
                ),
                "source_claim_id": claim.id,
                "source_label": original_label,
                "original_parameter_id": None,
                "canonical_parameter_id": parameter_name,
                "naming_only": True,
                "scientific_identity_inferred": False,
                "semantic_identity_requires_alignment": neutral,
                "source_values_preserved": True,
            })
            if neutral:
                claim = claim.model_copy(update={
                    "label": "Source quantity" if quantity else "Source detail"
                })
        contexts = {
            context
            for kind, raw, _, context, _ in field_values
            if context
            and kind == claim.kind
            and _text(claim.raw_text).casefold() in _text(raw).casefold()
        }
        if not claim.source_context and len(contexts) == 1:
            claim = _contextualize(claim, next(iter(contexts)))
        claim = _intrinsic_stage(claim)
        if ordered_thermal_cycle_proof(claim, requirement):
            claim = claim.model_copy(update={"stage": "thermal_cycle"})
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
    native_first = native_first and bool(model_atoms)
    if native_first:
        normalization_actions.append(
            {
                "rule": "native_first_comparison_view",
                "mode": "native_first_v2",
                "native_claim_ids": [claim.id for claim in model_atoms],
                "source_values_preserved": True,
            }
        )
    from scione.structured.summary_coverage import (
        represented_scalar_summary,
        same_named_scalar_row,
    )

    for kind, raw, name, context, field_unit in field_values:
        for clause in _clauses(raw):
            if context is None and (
                summary_proof := represented_scalar_summary(clause, kind, requirement, model_atoms)
            ):
                normalization_actions.append(summary_proof)
                continue
            if (
                kind == "criterion"
                and context is None
                and any(
                    action.get("rule") == "source_named_scale_summary_unit"
                    and same_named_scalar_row(clause, action.get("source_quote", ""))
                    for action in normalization_actions
                )
            ):
                # The fresh physical proof bound this entire strict scalar
                # summary row to the retained native body criterion.
                continue
            if represented_custom_scalar_field(clause, field_unit, kind, context, model_atoms):
                continue
            clause_pages = located_pages(clause)
            parse_clause = clause
            if field_unit and not _QUANTITY.search(_text(clause)):
                # Older extraction responses may separate value and unit. Use
                # that unit only when the source evidence independently states
                # the same physical quantity; never turn 23 hours into 23 °C.
                proposed = _QUANTITY.fullmatch(_text(f"{clause} {field_unit}"))
                if proposed:
                    proposed_unit = _parameter(proposed["unit"], clause, name)[1]
                    grounded = any(
                        _parameter(found["unit"], evidence.quote, name)[1] == proposed_unit
                        and found["first"] == proposed["first"]
                        and found["second"] == proposed["second"]
                        and found["tolerance"] == proposed["tolerance"]
                        and found["op"] == proposed["op"]
                        for evidence in requirement.evidence
                        for found in _QUANTITY.finditer(_text(evidence.quote))
                    )
                    if grounded:
                        parse_clause = f"{clause} {field_unit}"
            parsed = _atoms(
                parse_clause,
                kind=kind,
                pages=clause_pages or pages,
                prefix=f"{requirement.id}_field_{len(atoms) + 1}",
                name=f"{name} {requirement.original_test_name}",
                lexical_context="\n".join(item.quote for item in requirement.evidence),
                source_field_raw=raw,
            )
            if parse_clause != clause:
                parsed = [item.model_copy(update={"raw_text": clause}) for item in parsed]
            parsed = [_intrinsic_stage(_contextualize(item, context)) for item in parsed]
            if native_first:
                audit, problems = _native_clause_audit(
                    parsed,
                    model_atoms,
                    clause=clause,
                    pages=clause_pages or pages,
                    requirement=requirement,
                )
                normalization_actions.append(audit)
                for problem in problems:
                    issue = f"{requirement.id} {audit['audit_id']}: {problem}"
                    if issue not in review_issues:
                        review_issues.append(issue)
                continue
            local_model_atoms = [
                claim
                for claim in model_atoms
                if claim.normalization_state != "unresolved"
                and claim.kind == kind
                and set(clause_pages or pages) <= set(claim.source_pages)
                and (
                    _text(claim.raw_text).casefold() in _text(clause).casefold()
                    or _text(clause).casefold() in _text(claim.raw_text).casefold()
                )
            ]
            for fallback in parsed:
                if is_planning_instruction(fallback) and any(
                    is_planning_instruction(claim)
                    and claim.parameter_id == fallback.parameter_id
                    and claim.value == fallback.value
                    and _text(claim.raw_text).rstrip(".") == _text(clause).rstrip(".")
                    and fallback.source_context
                    and fallback.source_context.casefold()
                    in (claim.source_context or "").casefold()
                    for claim in local_model_atoms
                ):
                    continue
                if any(
                    _represented_verbatim_text(fallback, claim, pages=clause_pages or pages)
                    for claim in model_atoms
                ):
                    continue
                if (
                    fallback.parameter_id is None
                    and fallback.text_value
                    and _text(fallback.text_value) == _text(clause)
                    and local_model_atoms
                ):
                    # A bare criterion can be intentionally untyped by the
                    # legacy parser while a grounded native quantity already
                    # accounts for its numeric expression. Subtract that exact
                    # occurrence, never the native atom's broad quotation: its
                    # neighboring material/exclusion may still be unrecorded.
                    represented_spans = []
                    for claim in local_model_atoms:
                        proof = prove_same_numeric_occurrence(
                            claim,
                            claim,
                            source_text=clause,
                            source_pages=clause_pages or pages,
                        )
                        if proof is not None:
                            represented_spans.extend([*proof.number_spans, proof.unit_span])
                    residual = _unparsed_qualification(
                        clause, represented_spans, local_model_atoms, source_text=clause
                    )
                    if not residual:
                        continue
                    fallback = fallback.model_copy(
                        update={
                            "text_value": residual,
                            "label": "Additional source qualification",
                        }
                    )
                if (
                    fallback.parameter_id is None
                    and fallback.label == "Additional source qualification"
                ):
                    # A grounded, separate model parameter may explain the
                    # qualifier the numeric fallback could not understand. A
                    # temperature atom quoting the whole sentence cannot do so.
                    residual = _unparsed_qualification(
                        fallback.text_value or "", [], local_model_atoms, source_text=clause
                    )
                    if not residual:
                        continue
                    fallback = fallback.model_copy(update={"text_value": residual})
                    atoms.append(fallback)
                    continue
                represented = any(
                    claim.kind == kind
                    and claim.normalization_state != "unresolved"
                    and (
                        (
                            claim.parameter_id == fallback.parameter_id
                            and _intrinsic_stage(claim).stage == _intrinsic_stage(fallback).stage
                            and claim.configuration == fallback.configuration
                            and claim.sequence == fallback.sequence
                            and claim.operator == fallback.operator
                            and claim.value == fallback.value
                            and claim.minimum == fallback.minimum
                            and claim.maximum == fallback.maximum
                            and claim.tolerance == fallback.tolerance
                            and claim.values == fallback.values
                            and claim.unit == fallback.unit
                            and _same_text_value(claim, fallback)
                            and claim.source_context == fallback.source_context
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
                if fallback.parameter_id and _numeric_values(fallback):
                    represented = represented or any(
                        _represented_numeric_fallback(
                            fallback,
                            claim,
                            clause=clause,
                            context=context,
                            requirement=requirement,
                            occurrences=sum(
                                _numeric_identity(candidate) == _numeric_identity(fallback)
                                for candidate in parsed
                            ),
                            enclosing_raw=raw,
                        )
                        for claim in model_atoms
                    )
                if not represented and fallback.parameter_id and _numeric_values(fallback):
                    # Match only a unique quantity in a retained physical-source
                    # citation. A model paraphrase cannot establish scope or
                    # erase residual words; those remain separate obligations.
                    for evidence_index, evidence in enumerate(requirement.evidence):
                        if not citation_contains(evidence.quote, fallback.raw_text):
                            continue
                        for claim in model_atoms:
                            proof = prove_same_numeric_occurrence(
                                fallback,
                                claim,
                                source_text=evidence.quote,
                                source_pages=[evidence.page],
                            )
                            if proof is None:
                                continue
                            represented = True
                            flags = list(proof.metadata_flags)
                            cleared = []
                            if (
                                "supplied_source_context_not_literal" in flags
                                and claim.source_context
                                and context_is_source_grounded(
                                    claim.source_context, evidence.quote, claim.stage
                                )
                            ):
                                flags.remove("supplied_source_context_not_literal")
                                cleared.append("supplied_source_context_not_literal")
                            cycle = ordered_thermal_cycle_proof(claim, requirement)
                            if (
                                "supplied_stage_not_literal" in flags
                                and cycle
                                and cycle.get("evidence_index") == evidence_index + 1
                                and cycle.get("source_page") == evidence.page
                            ):
                                flags.remove("supplied_stage_not_literal")
                                cleared.append("supplied_stage_not_literal")
                            if cleared:
                                normalization_actions.append(
                                    {
                                        "rule": "same_occurrence_metadata_source_proof",
                                        "source_claim_id": claim.id,
                                        "source_page": evidence.page,
                                        "source_quote_sha256": sha256(
                                            evidence.quote.encode()
                                        ).hexdigest(),
                                        "cleared_metadata_flags": cleared,
                                        "remaining_metadata_flags": flags,
                                        "source_values_preserved": True,
                                    }
                                )
                            if flags:
                                note = (
                                    f"Same numeric source occurrence {fallback.id} -> {claim.id}; "
                                    f"evidence {evidence_index + 1}, page {evidence.page}, "
                                    f"span {proof.expression_span}: metadata unconfirmed "
                                    f"({', '.join(flags)})."
                                )
                                if note not in review_issues:
                                    review_issues.append(note)
                            break
                        if represented:
                            break
                elif fallback.parameter_id is None:
                    represented = represented or any(
                        _represented_operational_category(
                            claim, clause=clause, kind=kind, pages=clause_pages or pages
                        )
                        for claim in model_atoms
                    )
                if not represented:
                    atoms.append(fallback)
    return NormalizedRequirement(
        requirement_id=requirement.id,
        test_name=requirement.original_test_name,
        **({"normalizer_version": f"{NORMALIZER_VERSION}_native_first"} if native_first else {}),
        claims=[
            _intrinsic_stage(claim).model_copy(
                update={
                    "parameter_id": definition.id
                    if (definition := resolve_definition(claim.parameter_id))
                    else claim.parameter_id
                }
            )
            for claim in atoms
        ],
        review_issues=review_issues,
        normalization_actions=normalization_actions,
    )


def normalize_capability(capability: LabCapability) -> list[AtomicClaim]:
    """Structure explicit declarations without assuming unstated test knowledge."""
    if capability.atomic_claims:
        # Reviewed structured declarations are the lab's authoritative facts.
        # Re-parsing prose here would reintroduce guessed/duplicate limits.
        return [_intrinsic_stage(claim) for claim in capability.atomic_claims]
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
