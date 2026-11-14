"""A physically printed scale can support a derived field and an explicit edit."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pymupdf
import pytest
from test_workspace_api import LocalHTTPClient

from scione.api.app import create_app
from scione.api.workspace import Workspace
from scione.config import Settings
from scione.specifications.models import CustomerExtractionRun
from scione.structured.models import AtomicClaim

BODY = "The overall colour difference may not exceed 1.0."
UNIT_ROW = "Master-panel colour drift ΔE*ab <=1.0"


@pytest.fixture
def physical_source(tmp_path):
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_htmlbox(
        pymupdf.Rect(40, 40, 550, 740),
        f"<p>Colour difference: ISO 60570:2025.</p><p>{BODY}</p>"
        f"<p>{UNIT_ROW}</p><p>Synthetic source for checking a physical unit citation. "
        "No other measurement methods, colour scales or acceptance limits apply.</p>",
    )
    data = pdf.tobytes()
    pdf.close()
    settings = Settings(_env_file=None, scione_workspace_dir=tmp_path)
    workspace = Workspace(settings)
    stored = workspace.specs.import_pdf("synthetic-scale.pdf", data)
    atom = AtomicClaim(
        id="native_colour",
        parameter_id="color_difference",
        label="Colour difference",
        kind="criterion",
        operator="<=",
        value=1,
        raw_text=BODY,
        source_pages=[1],
    )
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "started_at": datetime.now(UTC),
            "document_id": stored.document_id,
            "provider": "static",
            "model": "fixture",
            "prompt_version": "offline_fixture",
            "schema_version": "0.1",
            "latency_ms": 0,
            "usage": {},
            "raw_response": "UNCHANGED RAW MODEL RESULT",
            "extraction": {
                "schema_version": "0.1",
                "document": {},
                "requirements": [
                    {
                        "id": "req_colour",
                        "original_test_name": "Colour difference",
                        "requested_methods": [{"code": "ISO 60570:2025"}],
                        "atomic_claims": [atom.model_dump(mode="json")],
                        "evidence": [{"page": 1, "quote": BODY}],
                    }
                ],
            },
        }
    )
    workspace.specs.save_extraction(stored.document_id, run)
    client = LocalHTTPClient(create_app(settings, frontend_dir=tmp_path / "no_frontend"))
    yield client, workspace, stored.document_id, run
    workspace.library.store.close()


def test_physical_unit_view_retains_native_origin_and_raw_archive(physical_source):
    client, workspace, doc, original = physical_source
    data = client.get(f"/api/specifications/{doc}").json()
    claims = data["normalized_requirements"]["req_colour"]["claims"]
    native = next(row for row in claims if row["source_atom_ids"] == ["native_colour"])
    assert native["unit"] == "ΔE*ab"
    assert native["raw_text"] == BODY
    assert native["normalization_state"] == "parsed"
    assert data["run"]["extraction"]["requirements"][0]["atomic_claims"][0]["unit"] is None
    view, normalized = workspace.comparison_source(doc, original)
    assert any(e.quote == UNIT_ROW for e in view.extraction.requirements[0].evidence)
    assert normalized[0].normalization_actions[0]["rule"] == "source_named_scale_summary_unit"
    assert workspace.specs.load_latest_extraction(doc).model_dump() == original.model_dump()
    diagnostic = client.get(f"/api/diagnostics/{doc}")
    assert diagnostic.status_code == 200
    audit = diagnostic.json()["source_review_audits"]
    assert audit["source_run_id"] == original.run_id
    proof = audit["physical_unit_citations"][0]
    assert proof["source_claim_id"] == "native_colour"
    assert proof["source_quote"] == UNIT_ROW
    assert proof["canonical"] == "ΔE*ab"
    assert workspace.specs.load_latest_extraction(doc).model_dump() == original.model_dump()


def test_corrected_derived_unit_can_be_saved_but_invented_unit_cannot(physical_source):
    client, workspace, doc, original = physical_source
    data = client.get(f"/api/specifications/{doc}").json()
    rows = deepcopy(data["run"]["extraction"]["requirements"])
    rows[0]["atomic_claims"][0]["unit"] = "ΔE*ab"
    body = {
        "run_id": original.run_id,
        "expected_version": 0,
        "expected_review_version": data["review"]["version"],
        "requirements": rows,
        "note": "Checked the printed scale.",
    }
    wrong = deepcopy(body)
    wrong["requirements"][0]["atomic_claims"][0]["unit"] = "mm"
    rejected = client.post(f"/api/specifications/{doc}/facts", json=wrong)
    assert rejected.status_code == 400
    saved = client.post(f"/api/specifications/{doc}/facts", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["facts_version"] == 1
    assert workspace.specs.load_latest_extraction(doc).raw_response == original.raw_response
