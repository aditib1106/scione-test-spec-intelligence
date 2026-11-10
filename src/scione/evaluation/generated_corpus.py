"""Offline, deliberately partial scoring of source-read customer-spec references.

No provider, settings, normalizer or matching policy is instantiated. Method
coverage is exact and edition-preserving; it is not a claim of equivalence.
Selected-atom recall is not precision or whole-document accuracy. Contexts and
unfamiliar scientific wording stay explicit review items rather than becoming
an optimistic semantic judgement by this evaluator.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from scione.specifications.models import CustomerExtractionRun, CustomerRequirement
from scione.structured.models import AtomicClaim, NormalizedRequirement

EVALUATOR_VERSION = "generated_source_reference_v1"

# Written from the reference vocabulary before inspecting any candidate output.
# These are scoring aliases, not additions to the application's parameter bank.
_PARAMETERS = {
    "topcoat_thickness": ("thickness", "film_thickness", "dry_film_thickness", "coating_thickness"),
    "total_film_thickness": (
        "thickness",
        "film_thickness",
        "dry_film_thickness",
        "coating_thickness",
    ),
    "film_thickness": ("thickness", "dry_film_thickness", "coating_thickness"),
    "temperature": (
        "temp",
        "water_temperature",
        "air_temperature",
        "specimen_temperature",
        "chamber_temperature",
        "oven_temperature",
        "test_temperature",
        "温度",
        "水温",
    ),
    "duration": (
        "time",
        "exposure_duration",
        "recovery_time",
        "test_duration",
        "aging_duration",
        "hold_time",
        "conditioning_duration",
        "immersion_duration",
        "时间",
        "时长",
    ),
    "cycle_count": ("cycles", "number_of_cycles", "thermal_cycles", "循环次数"),
    "grid_spacing": ("spacing", "cross_cut_spacing", "cut_spacing", "间距"),
    "adhesion_class": ("adhesion_rating", "cross_cut_class", "rating", "class"),
    "gloss": ("specular_gloss", "gloss_value", "gloss_units", "光泽", "光泽度"),
    "gloss_retention": ("retention", "gloss_retention_percent", "光泽保持率"),
    "gloss_loss": ("gloss_drop", "gloss_change", "absolute_gloss_loss"),
    "colour_difference": ("color_difference", "delta_e", "delta_e_ab", "deltae", "色差"),
    "illuminant": ("illumination", "light_source", "光源"),
    "observer_angle": ("observer", "viewing_observer", "观察者角度"),
    "compression_fraction": ("compression", "compression_ratio", "compression_percent"),
    "compression_set": ("permanent_compression_set", "compression_set_percent"),
    "specimen_diameter": ("diameter", "core_diameter", "sample_diameter"),
    "height_to_diameter_ratio": ("aspect_ratio", "length_diameter_ratio", "height_diameter_ratio"),
    "specimen_age": ("age", "test_age", "curing_age"),
    "compressive_strength": ("compression_strength", "concrete_compressive_strength"),
    "reporting_precision": ("decimal_places", "reporting_decimal_places"),
    "corrosion_creep": ("scribe_creep", "creep_distance", "corrosion_creep_distance"),
    "detached_grid_area": ("detached_area", "detachment_area", "coating_detachment", "area_loss"),
    "exposed_steel_length": ("exposed_metal_length", "bare_metal_length", "exposed_length"),
    "exposed_aluminium_length": ("exposed_metal_length", "bare_metal_length", "exposed_length"),
    "ingress_protection_class": ("ip_rating", "ingress_rating", "ip_class"),
    "inspection_delay": ("inspection_time", "opening_time", "post_test_delay"),
    "water_mass_gain": ("water_gain", "mass_gain", "water_ingress_mass"),
    "balance_resolution": ("resolution", "mass_resolution", "balance_readability"),
    "insulation_resistance": ("resistance", "electrical_insulation_resistance"),
    "resistance_retention": ("insulation_resistance_retention", "resistance_retention_percent"),
    "ultimate_tensile_strength": ("tensile_strength", "ultimate_strength", "rm"),
    "elongation_at_break": ("elongation", "break_elongation"),
    "rockwell_hardness": ("hardness", "hrc", "rockwell_c"),
    "hardness_spread": ("hardness_difference", "hardness_range", "hardness_max_min_difference"),
    "absorbed_energy": ("impact_energy", "charpy_energy", "absorbed_impact_energy"),
}


def _text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def _key(value: Any) -> str:
    return re.sub(r"[\W_]+", " ", _text(value)).strip()


def method_key(value: str) -> str:
    """Fold presentation only; keep method family, part, edition and suffix."""
    value = unicodedata.normalize("NFKC", value)
    value = value.translate(str.maketrans({"−": "-", "–": "-", "‑": "-"}))
    return re.sub(r"\s+", "", value).upper()


def _actual_methods(requirement: CustomerRequirement) -> set[str]:
    values = set()
    for method in requirement.requested_methods:
        code = method_key(method.code)
        if method.revision:
            revision = method_key(method.revision)
            # Separate edition fields are valid. No two-/four-digit year guess.
            if not code.endswith(revision):
                separator = "-" if code.startswith(("ASTM", "GB/T")) else ":"
                values.add(code + separator + revision)
                continue
        values.add(code)
    return values


def _expected_methods(test: dict) -> set[str]:
    return {method_key(row["code"]) for row in test["methods"]}


def _number(value: Any) -> Fraction | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return Fraction(number) if number.is_finite() else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _unit(value: str | None) -> tuple[str, Fraction, Fraction]:
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))
    value = value.replace("µ", "μ")
    # Case matters for SI prefix symbols. Only explicit word aliases are folded.
    names = {
        "Celsius": "°C",
        "degC": "°C",
        "Fahrenheit": "°F",
        "degF": "°F",
        "seconds": "s",
        "second": "s",
        "sec": "s",
        "minutes": "min",
        "minute": "min",
        "hour": "h",
        "hours": "h",
        "hr": "h",
        "day": "days",
        "micrometre": "μm",
        "micrometer": "μm",
        "um": "μm",
        "percent": "%",
        "ISO_class": "class",
        "ISOclass": "class",
        "classes": "class",
        "delta_e_ab": "ΔE*ab",
        "ΔEab": "ΔE*ab",
        "ΔE*ab": "ΔE*ab",
        "cycle": "cycles",
        "ratio": "",
        "decimal_places": "decimal_place",
    }
    value = names.get(value, value)
    exact = {
        "°C": ("temperature", Fraction(1), Fraction(0)),
        "°F": ("temperature", Fraction(5, 9), Fraction(-160, 9)),
        "K": ("temperature", Fraction(1), Fraction(-27315, 100)),
        "s": ("time", Fraction(1), Fraction(0)),
        "min": ("time", Fraction(60), Fraction(0)),
        "h": ("time", Fraction(3600), Fraction(0)),
        "days": ("time", Fraction(86400), Fraction(0)),
        "m": ("length", Fraction(1), Fraction(0)),
        "cm": ("length", Fraction(1, 100), Fraction(0)),
        "mm": ("length", Fraction(1, 1000), Fraction(0)),
        "μm": ("length", Fraction(1, 1000000), Fraction(0)),
        "g": ("mass", Fraction(1), Fraction(0)),
        "mg": ("mass", Fraction(1, 1000), Fraction(0)),
        "Pa": ("pressure", Fraction(1), Fraction(0)),
        "MPa": ("pressure", Fraction(1000000), Fraction(0)),
        "mPa": ("pressure", Fraction(1, 1000), Fraction(0)),
        "kPa": ("pressure", Fraction(1000), Fraction(0)),
    }
    return exact.get(value, ("literal:" + value, Fraction(1), Fraction(0)))


def payload_matches(expected: dict, claim: AtomicClaim) -> bool:
    """Compare a typed payload; this alone does not establish its association."""
    wanted_op = {"allowed_set": "one_of"}.get(expected.get("operator"), expected.get("operator"))
    if "text" in expected:
        return claim.operator in {"text", "required", "one_of"} and _text(
            claim.text_value
        ) == _text(expected["text"])
    if wanted_op != claim.operator:
        return False
    reference_unit = _unit(expected.get("unit"))
    actual_unit = _unit(claim.unit)
    if reference_unit[0] != actual_unit[0]:
        return False

    def convert(value: Any, unit: tuple, tolerance=False):
        parsed = _number(value)
        if parsed is None:
            return None
        return parsed * unit[1] + (0 if tolerance else unit[2])

    if wanted_op == "range":
        return all(
            convert(expected.get(field), reference_unit)
            == convert(getattr(claim, field), actual_unit)
            for field in ["minimum", "maximum"]
        )
    if wanted_op == "one_of":
        return sorted(
            convert(value, reference_unit) for value in expected.get("values", [])
        ) == sorted(convert(value, actual_unit) for value in claim.values)
    if _number(expected.get("value")) is None:
        return False
    if convert(expected["value"], reference_unit) != convert(claim.value, actual_unit):
        return False
    expected_tolerance = expected.get("tolerance")
    if expected_tolerance is None:
        return claim.tolerance in {None, Decimal(0)}
    return convert(expected_tolerance, reference_unit, True) == convert(
        claim.tolerance, actual_unit, True
    )


def _parameter_match(parameter: str, claim: AtomicClaim) -> bool:
    aliases = {_key(parameter), *(_key(value) for value in _PARAMETERS.get(parameter, ()))}
    return bool(aliases & {_key(claim.parameter_id), _key(claim.label)})


def _stage_check(fact: dict, claim: AtomicClaim) -> str:
    expected = _key(fact.get("stage"))
    actual = _key(claim.stage)
    if expected and expected == actual:
        return "same_explicit_label_not_independent_semantic_verification"
    expected_words, actual_words = set(expected.split()), set(actual.split())
    conflicts = [("cold", "hot"), ("immersion", "condensation"), ("dry", "wet")]
    for left, right in conflicts:
        if (left in expected_words and right in actual_words) or (
            right in expected_words and left in actual_words
        ):
            return "explicit_conflicting_stage_words"
    if expected_words & {"aged", "aging"} and actual_words & {"unaged", "unexposed"}:
        return "explicit_conflicting_stage_words"
    return "manual_semantic_review"


def _source_check(fact: dict, claim: AtomicClaim, source_pages: dict[int, str]) -> dict:
    expected_page = fact["evidence"]["page"]
    quote, raw = _text(fact["evidence"]["quote"]), _text(claim.raw_text)
    overlap = bool(min(len(quote), len(raw)) >= 4 and (quote in raw or raw in quote))
    source = any(raw in _text(source_pages.get(page, "")) for page in claim.source_pages)
    return {
        "expected_page_listed": expected_page in claim.source_pages,
        "claim_raw_quote_on_its_recorded_page": source if source_pages else None,
        "literal_overlap_with_reference_quote": overlap,
        "grounded_quote_overlap": bool(expected_page in claim.source_pages and overlap and source),
    }


def _claim_detail(claim: AtomicClaim, requirement_id: str) -> dict:
    return {"requirement_id": requirement_id, **claim.model_dump(mode="json")}


def _auto_fact(fact: dict, tests: dict) -> bool:
    """Eligibility is fixed by the reference, never by candidate performance."""
    return bool(
        any(tests[identifier]["methods"] for identifier in fact["tests"])
        and fact["role"] in {"condition", "criterion", "reporting", "scope", "instruction"}
        and fact["parameter"] in _PARAMETERS
        and ("text" not in fact["expected"] or fact["parameter"] == "illuminant")
    )


def _score_facts(reference: dict, requirements, claims_by_id, source_pages: dict) -> list[dict]:
    tests = {row["reference_id"]: row for row in reference["expected_test_rows"]}
    all_claims = [(row, claim) for row in requirements for claim in claims_by_id.get(row.id, [])]
    outcomes = []
    for fact in reference.get("critical_facts", []):
        method_ids = set().union(
            *(_expected_methods(tests[identifier]) for identifier in fact["tests"])
        )
        associated = [
            (row, claim) for row, claim in all_claims if _actual_methods(row) & method_ids
        ]
        result = {
            "fact_id": fact["fact_id"],
            "reference_tests": fact["tests"],
            "parameter": fact["parameter"],
            "expected": fact["expected"],
            "expected_role": fact["role"],
            "expected_stage": fact["stage"],
            "reference_evidence": fact["evidence"],
            "payload_recovered": False,
            "grounded_payload_recovered": False,
            "role_matches": None,
            "stage_association": "not_assessed",
            "statistic_association": "not_assessed",
            "configuration_association": "not_assessed",
            "candidate": None,
        }
        if not method_ids:
            result["status"] = "manual_review_custom_test_identity"
            outcomes.append(result)
            continue
        if not _auto_fact(fact, tests):
            result["status"] = "manual_review_reference_not_automatically_assessable"
            outcomes.append(result)
            continue
        semantic = [
            (row, claim) for row, claim in associated if _parameter_match(fact["parameter"], claim)
        ]
        matches = [
            (row, claim) for row, claim in semantic if payload_matches(fact["expected"], claim)
        ]
        if matches:
            # Source overlap is preferable to mere equal numbers on the same test.
            matches.sort(
                key=lambda pair: (
                    _source_check(fact, pair[1], source_pages)["grounded_quote_overlap"],
                    _key(fact["stage"]) == _key(pair[1].stage),
                    fact["role"] == pair[1].kind,
                ),
                reverse=True,
            )
            row, claim = matches[0]
            result.update(
                {
                    "status": "typed_payload_recovered_context_still_separately_assessed",
                    "payload_recovered": True,
                    "role_matches": fact["role"] == claim.kind,
                    "stage_association": _stage_check(fact, claim),
                    "statistic_association": "manual_semantic_review"
                    if fact["expected"].get("statistic")
                    else "not_in_selected_reference",
                    "configuration_association": "manual_semantic_review"
                    if fact["expected"].get("configuration")
                    else "not_in_selected_reference",
                    "candidate": _claim_detail(claim, row.id),
                    "source_evidence": _source_check(fact, claim, source_pages),
                }
            )
            result["grounded_payload_recovered"] = result["source_evidence"][
                "grounded_quote_overlap"
            ]
            if not result["role_matches"]:
                result["status"] = "payload_present_role_mismatch"
        else:
            anchored_unknown = [
                (row, claim)
                for row, claim in associated
                if _source_check(fact, claim, source_pages)["literal_overlap_with_reference_quote"]
                and payload_matches(fact["expected"], claim)
            ]
            if anchored_unknown:
                row, claim = anchored_unknown[0]
                result.update(
                    {
                        "status": "manual_review_parameter_identity",
                        "candidate": _claim_detail(claim, row.id),
                        "source_evidence": _source_check(fact, claim, source_pages),
                    }
                )
            elif semantic:
                row, claim = semantic[0]
                result.update(
                    {
                        "status": "selected_payload_not_recovered",
                        "candidate": _claim_detail(claim, row.id),
                    }
                )
            else:
                wrong_test = [
                    _claim_detail(claim, row.id)
                    for row, claim in all_claims
                    if _parameter_match(fact["parameter"], claim)
                    and payload_matches(fact["expected"], claim)
                    and _source_check(fact, claim, source_pages)[
                        "literal_overlap_with_reference_quote"
                    ]
                ]
                result["status"] = (
                    "payload_under_other_test" if wrong_test else "selected_fact_not_recovered"
                )
                if wrong_test:
                    result["wrong_test_candidates"] = wrong_test
        outcomes.append(result)
    return outcomes


def _assignment(candidates: dict[str, list[str]]) -> dict[str, str]:
    """Maximum one-to-one coverage, as a diagnostic rather than a row-count grade."""
    by_actual: dict[str, str] = {}

    def place(reference_id, visited):
        for actual_id in candidates[reference_id]:
            if actual_id in visited:
                continue
            visited.add(actual_id)
            if actual_id not in by_actual or place(by_actual[actual_id], visited):
                by_actual[actual_id] = reference_id
                return True
        return False

    for reference_id in candidates:
        place(reference_id, set())
    return {reference_id: actual_id for actual_id, reference_id in by_actual.items()}


def _duplicate_groups(requirements) -> list[list[str]]:
    signatures: dict[str, list[str]] = {}
    for row in requirements:
        payload = row.model_dump(mode="json", exclude={"id"})
        for claim in payload.get("atomic_claims", []):
            claim.pop("id", None)
        signature = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        signatures.setdefault(signature, []).append(row.id)
    return [group for group in signatures.values() if len(group) > 1]


def score_document(
    reference: dict, run: CustomerExtractionRun, normalized=None, source=None
) -> dict:
    """Score only supplied artifacts, preserving all undecidable associations."""
    requirements = run.extraction.requirements
    expected_tests = reference["expected_test_rows"]
    reference_methods = set().union(*(_expected_methods(test) for test in expected_tests))
    emitted_methods = (
        set().union(*(_actual_methods(row) for row in requirements)) if requirements else set()
    )
    candidates = {
        test["reference_id"]: [
            row.id for row in requirements if _actual_methods(row) & _expected_methods(test)
        ]
        for test in expected_tests
        if test["methods"]
    }
    known_ids = {identifier for options in candidates.values() for identifier in options}
    merges = []
    for row in requirements:
        references = [
            test for test in expected_tests if row.id in candidates.get(test["reference_id"], [])
        ]
        banks = {test.get("bank_test_id") or test["reference_id"] for test in references}
        if len(banks) > 1:
            merges.append(
                {
                    "requirement_id": row.id,
                    "reference_test_ids": [test["reference_id"] for test in references],
                }
            )
    source_pages = {
        page.get("page_number", page.get("page")): page.get("text", "")
        for page in (source or {}).get("pages", [])
    }
    layers = {
        "extraction_atomic_claims": _score_facts(
            reference,
            requirements,
            {row.id: row.atomic_claims for row in requirements},
            source_pages,
        ),
        "normalized_system_claims": None
        if normalized is None
        else _score_facts(
            reference,
            requirements,
            {row.requirement_id: row.claims for row in normalized},
            source_pages,
        ),
    }
    return {
        "document_id": reference["document_id"],
        "split": reference["split"],
        "status": "valid_saved_extraction_contract",
        "factual_correctness_established_by_contract": False,
        "run_metadata": {
            key: getattr(run, key)
            for key in ["run_id", "provider", "model", "prompt_version", "strategy"]
        },
        "method_reference_coverage": {
            "expected_unique_codes": len(reference_methods),
            "recovered_codes": sorted(reference_methods & emitted_methods),
            "missing_codes": sorted(reference_methods - emitted_methods),
            "unreferenced_code_candidates": sorted(emitted_methods - reference_methods),
        },
        "test_coverage": {
            "reference_rows": len(expected_tests),
            "method_backed_reference_rows": len(candidates),
            "method_anchored_covered_rows": sum(bool(options) for options in candidates.values()),
            "method_candidates_by_reference": candidates,
            "maximum_one_to_one_assignment": _assignment(candidates),
            "customer_defined_reference_rows_for_manual_review": [
                test for test in expected_tests if not test["methods"]
            ],
            "unassigned_requirement_candidates": [
                {
                    "id": row.id,
                    "name": row.original_test_name,
                    "methods": [method.code for method in row.requested_methods],
                }
                for row in requirements
                if row.id not in known_ids
            ],
            "exact_duplicate_requirement_groups": _duplicate_groups(requirements),
            "possible_cross_test_merges_for_manual_review": merges,
            "source_exclusion_checks_for_manual_review": reference.get("not_separate_tests", []),
        },
        "selected_critical_facts": layers,
        "manual_review_notes": reference.get("ambiguities", []),
    }


def _read(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    return json.loads(raw, parse_float=Decimal), hashlib.sha256(raw).hexdigest()


def load_reference(path: Path, expected_sha256=None, errata=None, errata_sha256=None):
    reference, sha = _read(path)
    if expected_sha256 and sha != expected_sha256:
        raise ValueError("Reference SHA256 does not match the frozen value.")
    if bool(errata) != bool(errata_sha256):
        raise ValueError("An explicit errata path and SHA256 must be supplied together.")
    metadata = {"reference_path": str(path), "reference_sha256": sha, "errata": None}
    reference = copy.deepcopy(reference)
    if errata:
        changes, actual_sha = _read(errata)
        if actual_sha != errata_sha256 or changes.get("parent_reference_sha256") != sha:
            raise ValueError("Errata hash or parent reference hash does not match.")
        by_document = {doc["document_id"]: doc for doc in reference["documents"]}
        for change in changes["changes"]:
            document = by_document[change["document_id"]]
            if change["field"] == "bank_test_id":
                test = next(
                    row
                    for row in document["expected_test_rows"]
                    if row["reference_id"] == change["reference_id"]
                )
                test["bank_test_id"] = change["corrected"]
            elif change["field"] == "evidence":
                fact = next(
                    row for row in document["critical_facts"] if row["fact_id"] == change["fact_id"]
                )
                fact["evidence"] = change["corrected"]
            else:
                raise ValueError(
                    "Unsupported errata operation; do not silently modify score labels."
                )
        metadata["errata"] = {"path": str(errata), "sha256": actual_sha}
    return reference, metadata


def _normalized(path: Path) -> list[NormalizedRequirement]:
    payload, _ = _read(path)
    if isinstance(payload, dict):
        payload = payload.get("requirements", payload.get("normalized", payload))
    if isinstance(payload, dict):
        payload = list(payload.values())
    if not isinstance(payload, list):
        raise ValueError("Normalized artifact is not a requirement list.")
    rows = [NormalizedRequirement.model_validate(row) for row in payload]
    if len({row.requirement_id for row in rows}) != len(rows):
        raise ValueError("Normalized artifact repeats a requirement ID.")
    return rows


def _fact_summary(rows: list[dict], total: int, auto_total: int) -> dict:
    recovered = sum(row["payload_recovered"] for row in rows)
    grounded = sum(row["grounded_payload_recovered"] for row in rows)
    counts = Counter(row["status"] for row in rows)
    return {
        "selected_reference_facts_including_failed_documents": total,
        "automatically_assessable_reference_facts_including_failed_documents": auto_total,
        "reference_facts_requiring_manual_review": total - auto_total,
        "scored_fact_outcomes": len(rows),
        "typed_payload_recovered": recovered,
        "typed_payload_recall_including_failed_documents": recovered / auto_total
        if auto_total
        else None,
        "payload_recall_interpretation": (
            "Strict automatic recovery lower bound over reference-eligible facts. "
            "Unfamiliar parameter identities remain unconfirmed, not labelled factually wrong. "
            "Reference-ineligible manual facts are excluded from this denominator."
        ),
        "source_grounded_payload_recovered": grounded,
        "source_grounded_payload_recall_including_failed_documents": grounded / auto_total
        if auto_total
        else None,
        "role_mismatch_count": sum(row.get("role_matches") is False for row in rows),
        "explicit_stage_conflict_count": sum(
            row["stage_association"] == "explicit_conflicting_stage_words" for row in rows
        ),
        "stage_semantics_pending_manual_review": sum(
            row["stage_association"] == "manual_semantic_review" for row in rows
        ),
        "statuses": dict(counts),
        "precision": None,
        "whole_document_accuracy": None,
    }


def evaluate_corpus(
    reference_path: Path, runs_path: Path, *, reference_sha256=None, errata=None, errata_sha256=None
) -> dict:
    """Read saved files recursively. Missing/invalid documents remain denominators."""
    reference, provenance = load_reference(reference_path, reference_sha256, errata, errata_sha256)
    by_hash = {doc["sha256"]: doc for doc in reference["documents"]}
    by_name = {Path(doc["file_name"]).stem.strip(): doc for doc in reference["documents"]}
    attempts: dict[str, list[dict]] = {}
    unassigned_artifacts = []
    for path in sorted(runs_path.rglob("extraction_run.json")):
        row: dict = {"run_path": str(path), "artifact_sha256": {}}
        for sibling in ["summary.json", "code_manifest.json"]:
            sibling_path = path.with_name(sibling)
            if sibling_path.exists():
                row["artifact_sha256"][sibling] = hashlib.sha256(
                    sibling_path.read_bytes()
                ).hexdigest()
        document_path = path.with_name("document.json")
        source = None
        identity_status = "path_only_unverified"
        try:
            if document_path.exists():
                source, sha = _read(document_path)
                row["artifact_sha256"]["document.json"] = sha
                declared = source.get("sha256")
                target = by_hash.get(declared)
                if target is None:
                    raise ValueError("Source PDF hash not in frozen reference.")
                identity_status = "source_pdf_hash_matched"
            else:
                target = by_name.get(path.parent.name.strip())
                if target is None:
                    raise ValueError("No reference source identity.")
            raw = path.read_bytes()
            row["artifact_sha256"]["extraction_run.json"] = hashlib.sha256(raw).hexdigest()
            run = CustomerExtractionRun.model_validate_json(raw)
            normalized_path = path.with_name("normalized.json")
            normalized = None
            if normalized_path.exists():
                row["artifact_sha256"]["normalized.json"] = hashlib.sha256(
                    normalized_path.read_bytes()
                ).hexdigest()
                try:
                    normalized = _normalized(normalized_path)
                    if {item.requirement_id for item in normalized} != {
                        item.id for item in run.extraction.requirements
                    }:
                        raise ValueError(
                            "Normalized requirement ID coverage differs from extraction."
                        )
                except (ValueError, TypeError, ValidationError) as exc:
                    row["normalized_artifact_error"] = type(exc).__name__
                    normalized = None
            row.update(score_document(target, run, normalized, source))
            row["source_identity_status"] = identity_status
            attempts.setdefault(target["document_id"], []).append(row)
        except (OSError, ValueError, TypeError, ValidationError) as exc:
            target = by_name.get(path.parent.name.strip())
            row.update(
                {"status": "invalid_artifact_or_source_identity", "error_type": type(exc).__name__}
            )
            if target:
                attempts.setdefault(target["document_id"], []).append(row)
            else:
                unassigned_artifacts.append(row)
    results = []
    for document in reference["documents"]:
        options = attempts.get(document["document_id"], [])
        if len(options) == 1:
            row = options[0]
        elif len(options) > 1:
            # Choosing the best/latest run would leak selection into evaluation.
            row = {"status": "multiple_attempts_require_explicit_selection", "attempts": options}
        else:
            row = {"status": "missing_extraction_run"}
        row.update({"document_id": document["document_id"], "split": document["split"]})
        results.append(row)
    valid = [row for row in results if row["status"] == "valid_saved_extraction_contract"]
    method_total = sum(
        len(set().union(*(_expected_methods(test) for test in doc["expected_test_rows"])))
        for doc in reference["documents"]
    )
    fact_total = sum(len(doc.get("critical_facts", [])) for doc in reference["documents"])
    auto_fact_total = sum(
        _auto_fact(fact, {test["reference_id"]: test for test in doc["expected_test_rows"]})
        for doc in reference["documents"]
        for fact in doc.get("critical_facts", [])
    )
    recovered_methods = sum(
        len(row["method_reference_coverage"]["recovered_codes"]) for row in valid
    )
    layer_summary = {}
    for layer in ["extraction_atomic_claims", "normalized_system_claims"]:
        facts = [fact for row in valid for fact in row["selected_critical_facts"].get(layer) or []]
        layer_summary[layer] = _fact_summary(facts, fact_total, auto_fact_total)
    scorer = Path(__file__)
    return {
        "schema_version": "1.0",
        "evaluator_version": EVALUATOR_VERSION,
        "dataset_id": reference.get("dataset_id"),
        "new_api_calls": 0,
        "mode": "offline_saved_extraction_partial_source_reference",
        "provenance": {
            **provenance,
            "scorer_source_sha256": hashlib.sha256(scorer.read_bytes()).hexdigest(),
            "contract_source_sha256": {
                str(path.relative_to(scorer.parents[1])): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in [
                    scorer.parents[1] / "specifications/models.py",
                    scorer.parents[1] / "structured/models.py",
                    scorer.parents[1] / "numeric_literals.py",
                ]
            },
        },
        "summary": {
            "expected_documents": len(results),
            "valid_saved_contract_documents": len(valid),
            "failed_or_missing_documents": len(results) - len(valid),
            "documents_by_status": dict(Counter(row["status"] for row in results)),
            "expected_method_references": method_total,
            "recovered_method_references": recovered_methods,
            "exact_method_reference_recall_including_failed_documents": recovered_methods
            / method_total
            if method_total
            else None,
            "unreferenced_method_candidates": sum(
                len(row["method_reference_coverage"]["unreferenced_code_candidates"])
                for row in valid
            ),
            "method_anchored_reference_test_rows_covered": sum(
                row["test_coverage"]["method_anchored_covered_rows"] for row in valid
            ),
            "unassigned_requirement_candidates": sum(
                len(row["test_coverage"]["unassigned_requirement_candidates"]) for row in valid
            ),
            "exact_duplicate_requirement_groups": sum(
                len(row["test_coverage"]["exact_duplicate_requirement_groups"]) for row in valid
            ),
            "possible_cross_test_merges_for_manual_review": sum(
                len(row["test_coverage"]["possible_cross_test_merges_for_manual_review"])
                for row in valid
            ),
            "selected_critical_facts": layer_summary,
        },
        "documents": results,
        "unassigned_artifacts": unassigned_artifacts,
        "limitations": [
            *reference.get("limitations", []),
            "Method-anchored coverage is not semantic test-name accuracy. Customer-defined "
            "tests without method IDs require manual review.",
            "Typed payload recovery is separate from role, evidence, stage, statistic "
            "and configuration association.",
            "Stage label agreement is lexical, not independent semantic verification. "
            "Unfamiliar aliases and contexts require manual review.",
            "Duplicates and possible merges are diagnostics. Legitimate variants and "
            "combined UI rows are not automatically graded wrong.",
            "No precision, global accuracy, lab-support accuracy or production reliability "
            "is established by this partial reference.",
            "A valid artifact establishes its application contract, not factual truth "
            "or independent HTTP request success.",
        ],
    }
