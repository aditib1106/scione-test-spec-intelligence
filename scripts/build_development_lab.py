"""Compile frozen synthetic laboratory services; never run during app startup.

All original team CSV rows remain provenance. The independently authored policy
sets bounded services shared by every customer, with explicit offered and missing
methods and reporting phases. No model predictions or expected reports are read.
"""

import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from scione.catalogs.bank import load_synthetic_method_bank
from scione.catalogs.generated import canonical_test_id_for_family, method_id_for_record
from scione.catalogs.models import LabCapability, LabProfile, validate_lab_references
from scione.catalogs.seed import load_reference_catalog
from scione.structured.models import AtomicClaim
from scione.structured.normalization import normalize_capability

_EXPLICIT_RANGE = re.compile(
    r"(?P<low>[+-]?\d+(?:\.\d+)?)\s*[–−-]\s*"
    r"(?P<high>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>[^\d\s;:,]+)?\s*\.?"
)


def _source_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.replace("–", "-").replace("−", "-").casefold().split()).rstrip(".")


def explicit_reporting_ranges(cap: LabCapability, criteria: str) -> list[AtomicClaim]:
    """Type exact one-property lab ranges without guessing compound field meaning.

    A flexible lab form can declare an unfamiliar property. Its literal label,
    numeric range and unit are useful even when the pilot parser has no predefined
    parameter. This does not infer phase, unit, accreditation or specimen success.
    Compound subjects, half-grade scales and other qualifiers stay unresolved.
    """
    rows = {}
    for clause in criteria.split(";"):
        if ":" not in clause:
            continue
        label, value = (part.strip() for part in clause.split(":", 1))
        if not label or re.search(r",|\band\b|\beach\b|/|\bafter\b|\bbefore\b", label, re.I):
            continue
        found = _EXPLICIT_RANGE.fullmatch(value)
        if not found:
            continue
        identifier = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
        if not identifier or not identifier[0].isalpha() or len(identifier) > 100:
            continue
        rows[_source_key(clause)] = (label, identifier, found)
    result = []
    for atom in normalize_capability(cap):
        row = rows.get(_source_key(atom.raw_text))
        if (
            row is None or atom.kind != "reporting"
            or atom.normalization_state != "unresolved"
            or atom.availability != "supported"
        ):
            result.append(atom)
            continue
        label, identifier, found = row
        result.append(AtomicClaim(
            id=atom.id, parameter_id=identifier, label=label, kind="reporting",
            stage=atom.stage, source_context=atom.source_context,
            operator="range", minimum=found["low"], maximum=found["high"],
            unit=(found["unit"] or "").rstrip(".") or None,
            raw_text=atom.raw_text, normalization_state="reviewed",
        ))
    return result


def build_development_lab(root: Path) -> dict:
    """Compile the frozen lab policy without inspecting any extraction or report.

    The supplied CSV is retained verbatim for provenance. New declarations are
    independently authored laboratory service limits, not inferred CSV parsing or
    a copy of customer acceptance thresholds.
    """
    source = root / "docs/Lap Capability & Gap report/lab_capability.csv"
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    policy_path = root / "src/scione/catalogs/data/development_lab_policy_v2.json"
    policy = json.loads(policy_path.read_text())
    catalog, bank = load_reference_catalog(), load_synthetic_method_bank()
    original_rows = {}
    for index, row in enumerate(rows, 1):
        original_rows.setdefault(row["test_method"], []).append(index)
    caps, provenance = [], []
    for method in policy["methods"]:
        if not method["offered"]:
            continue
        identifier, family = method["identifier"], method["family"]
        record = bank.lookup_identifier(identifier)
        if record is None or record.family_id != family:
            raise ValueError(f"Invalid frozen method declaration: {identifier}")
        method_id = method_id_for_record(record, catalog)
        test_id = canonical_test_id_for_family(family, catalog)
        if method_id is None or test_id is None:
            raise ValueError("Import the exact source method into the identity catalog first.")
        cap_id = "service_" + record.record_id.lower().replace("-", "_")
        claims = []
        for index, spec in enumerate(policy["family_modules"][family], 1):
            value = spec.get("text_value") or (
                f"{spec.get('minimum')}–{spec.get('maximum')} {spec.get('unit') or ''}"
            ).strip()
            claims.append(AtomicClaim(
                id=f"{cap_id}_field_{index}", **spec,
                raw_text=f"{spec['label']}: {value}. Offered phase: {spec['stage']}.",
                normalization_state="reviewed",
            ))
        cap = LabCapability(
            id=cap_id, canonical_test_id=test_id, method_ids=[method_id],
            atomic_claims=claims,
            declared_constraints=(
                "Frozen independent synthetic service declaration; see detailed parameters. "
                "Only explicitly named stages and services are offered."
            ),
            notes=(
                f"Department service module {family}; method {identifier} is separately declared. "
                "Specimen acceptance is assessed from measurements, never guaranteed. "
                "Different method IDs and national rating scales are not interchangeable."
            ),
        )
        caps.append(cap)
        provenance.append({
            "capability_id": cap.id, "method": identifier, "family": family,
            "source_rows": original_rows.get(identifier, []),
            "origin": "independent_frozen_laboratory_service_policy_v2",
        })
    lab = LabProfile(
        id="development_lab", name="Materials Testing Laboratory",
        description=(
            "Synthetic multidisciplinary contract laboratory. Fixed method registrations, "
            "instrument ranges, explicit reporting phases and deliberate service gaps apply "
            "to every customer. This is not an assertion about any real lab."
        ), capabilities=caps,
    )
    validate_lab_references(lab, catalog)
    return {
        "baseline_id": "team_lab_development_v2", "synthetic": True,
        "policy_id": policy["policy_id"],
        "policy_file": str(policy_path.relative_to(root)),
        "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "source_csv": str(source.relative_to(root)),
        "source_csv_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_row_count": len(rows),
        "original_source_method_count": len(original_rows),
        "selected_method_count": len(caps),
        "selection_policy": (
            "One explicit service declaration per offered exact method. Fixed family modules "
            "are authored before corpus inference; customer pass/fail limits are not lab limits. "
            "Omitted methods are intentionally not offered. No cross-method equivalence."
        ),
        "source_rows": rows, "selected_declarations": provenance,
        "not_offered_methods": [m for m in policy["methods"] if not m["offered"]],
        "intentional_missing_services": policy["intentional_missing_services"],
        "lab": lab.model_dump(mode="json"),
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    payload = build_development_lab(root)
    destination = root / "src/scione/catalogs/data/development_lab_baseline_v1.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"Built {len(payload['lab']['capabilities'])} explicit synthetic lab declarations.")
