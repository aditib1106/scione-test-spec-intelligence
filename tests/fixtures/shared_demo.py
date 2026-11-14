"""Hand-authored, partial review data from the shared PDF; never model output.

Only five rows from pages 6–7 are represented. The original 13-page document is
preserved, and unresolved source checks remain visible in the walkthrough.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from scione.catalogs import FileLabStore, LabCapability
from scione.catalogs.models import LabOperatingLimit
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.store import FileSpecStore


def seed_shared_demo(specs: FileSpecStore, labs: FileLabStore) -> None:
    path = Path(__file__).parents[2] / "docs" / "samples" / "MS-PNT-410_demo_spec.pdf"
    stored = specs.import_pdf(path.name, path.read_bytes())
    pages = {page.page_number: page.text for page in stored.document.pages}

    def evidence(page: int, start: str, end: str) -> list[dict[str, object]]:
        text = pages[page]
        # Geometric extraction keeps a visual row together. Wrapped item names
        # may have intervening method/criteria columns, so use their first
        # physical label line as the known row boundary in this UI fixture.
        begin = text.index(start.splitlines()[0])
        finish = text.index(end.splitlines()[0], begin + len(start.splitlines()[0]))
        return [{"page": page, "quote": text[begin:finish].strip()}]

    requirements = [
        {
            "id": "req_demo_pencil",
            "original_test_name": "Pencil hardness",
            "requested_methods": [{"code": "ISO 15184"}],
            "conditions": [{"name": "Load", "raw_value": "750", "unit": "g"}],
            "acceptance_criteria": [{"name": "Hardness", "raw_value": "≥ F"}],
            "evidence": evidence(6, "Pencil hardness\n", "Buchholz indentation\n"),
        },
        {
            "id": "req_demo_crosscut",
            "original_test_name": "Cross-cut adhesion",
            "requested_methods": [{"code": "ISO 2409"}],
            "conditions": [{"name": "Spacing", "raw_value": "2", "unit": "mm"}],
            "applicability": ["All bake conditions, all substrates"],
            "acceptance_criteria": [{"name": "Rating", "raw_value": "GT 0 to 1"}],
            "evidence": evidence(6, "Cross-cut adhesion\n", "Pull-off adhesion\n"),
        },
        {
            "id": "req_demo_pull",
            "original_test_name": "Pull-off adhesion",
            "requested_methods": [{"code": "ISO 4624"}],
            "conditions": [{"name": "Dolly", "raw_value": "20", "unit": "mm"}],
            "applicability": ["Nominal bake and overbake"],
            "special_instructions": ["2K epoxy adhesive"],
            "acceptance_criteria": [
                {
                    "name": "Strength",
                    "raw_value": "≥ 5 MPa; no separation between e-coat, primer, basecoat or "
                    "clearcoat (adhesive or dolly failure accepted)",
                }
            ],
            "evidence": evidence(6, "Pull-off adhesion\n", "Pressure water jet\n"),
        },
        {
            "id": "req_demo_humidity",
            "original_test_name": "Humidity resistance",
            "requested_methods": [{"code": "ISO 6270-2"}],
            "conditions": [
                {"name": "Duration", "raw_value": "240", "unit": "h"},
                {"name": "Temperature", "raw_value": "40", "unit": "°C"},
            ],
            "special_instructions": ["CH exposure"],
            "acceptance_criteria": [
                {
                    "name": "After exposure",
                    "raw_value": "Blistering 0(S0); GT ≤ 1 at 1 h and 24 h; "
                    "ΔE*ab ≤ 0.5 at 45°; gloss loss ≤ 5 GU",
                }
            ],
            "evidence": evidence(7, "Humidity\n", "Water\nimmersion\n"),
        },
        {
            "id": "req_demo_salt",
            "original_test_name": "Neutral salt spray",
            "requested_methods": [{"code": "ISO 9227"}],
            "conditions": [{"name": "Duration", "raw_value": "1000", "unit": "h"}],
            "acceptance_criteria": [
                {"name": "Creep", "raw_value": "≤ 2.0 mm (CRS), ≤ 1.0 mm (galvanized)"}
            ],
            "evidence": evidence(7, "Neutral salt\nspray\n", "Cyclic corrosion\n"),
        },
    ]
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "started_at": datetime.now(UTC),
            "document_id": stored.document_id,
            "prompt_version": "hand_authored_partial_shared_demo_fixture",
            "schema_version": "0.1",
            "provider": "offline_test",
            "model": "no_network",
            "latency_ms": 0,
            "usage": {},
            "raw_response": "Hand-authored UI fixture: five selected rows from pages 6–7. "
            "Not a complete extraction or a model evaluation.",
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": "MS-PNT-410 · partial offline UI fixture"},
                "requirements": requirements,
                "review_issues": [
                    "req_demo_pencil: Check this fixture row against page 6 before confirming.",
                    "req_demo_crosscut: Check bake and substrate scope against page 6.",
                ],
            },
        }
    )
    specs.save_extraction(stored.document_id, run)
    lab = labs.load_or_create()
    lab.capabilities.append(
        LabCapability(
            id="cap_demo_humidity",
            canonical_test_id="humidity_resistance",
            method_ids=["iso_6270_2"],
            declared_constraints="40 °C; maximum 120 h continuous exposure.",
            structured_limits=[
                LabOperatingLimit(parameter="duration", maximum=Decimal("120"), unit="h"),
                LabOperatingLimit(parameter="temperature", values=[Decimal("40")], unit="°C"),
            ],
        )
    )
    labs.save(lab)
