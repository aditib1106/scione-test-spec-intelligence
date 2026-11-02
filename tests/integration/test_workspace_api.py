"""Offline contract tests for the local workspace HTTP API."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pymupdf
import pytest

from scione.api.app import create_app
from scione.catalogs import FileLabStore, LabCapability, load_reference_catalog
from scione.catalogs.models import LabOperatingLimit, LabProfile
from scione.config import Settings
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.providers.static import StaticModelProvider
from scione.reports.store import FileReportStore
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.store import FileSpecStore

PENCIL = "Pencil hardness: ISO 15184, 750 g load; at least F."


class LocalHTTPClient:
    """Synchronous facade for in-process ASGI requests without local TCP sockets."""

    def __init__(self, app):
        self.app = app

    def request(self, method: str, url: str, **kwargs):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://testserver",
            ) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(send())

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs):
        return self.request("PUT", url, **kwargs)


def make_pdf(text: str = PENCIL) -> bytes:
    content = "\n".join(
        [
            text,
            "Synthetic offline source fixture for testing the local specification workflow.",
            "No additional requirements or laboratory claims are made in this document.",
        ]
    )
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(35, 35, 560, 700), content, fontsize=10)
        return document.tobytes()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "workspace"
    settings = Settings(
        _env_file=None,
        scione_workspace_dir=root,
        groq_api_key=None,
        gemini_api_key=None,
    )

    def reject_model_call(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Local workspace reads and reviews must never call a model.")

    monkeypatch.setattr(GroqModelProvider, "generate_structured", reject_model_call)
    monkeypatch.setattr(GeminiModelProvider, "generate_structured", reject_model_call)
    app = create_app(settings, frontend_dir=tmp_path / "missing-frontend")
    yield LocalHTTPClient(app), root


def seed_source(
    root: Path,
    *,
    method: str = "ISO 15184",
    test_name: str = "Pencil hardness",
    condition: str = "750",
    run_id: str | None = None,
    source_flag: bool = False,
):
    """Create an isolated source, saved extraction and lab declaration for API checks."""
    catalog = load_reference_catalog()
    specs = FileSpecStore(root)
    lab_store = FileLabStore(root, catalog)
    source = specs.import_pdf("customer.pdf", make_pdf())
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": run_id or str(uuid4()),
            "started_at": datetime.now(UTC),
            "document_id": source.document_id,
            "prompt_version": "offline_api_fixture",
            "schema_version": "0.1",
            "provider": "offline_test",
            "model": "no_network",
            "latency_ms": 1,
            "usage": {},
            "raw_response": " ORIGINAL PROVIDER TEXT\n",
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": "Offline customer spec"},
                "requirements": [
                    {
                        "id": "req_pencil",
                        "original_test_name": test_name,
                        "requested_methods": [{"code": method}],
                        "conditions": [{"name": "Load", "raw_value": condition, "unit": "g"}],
                        "acceptance_criteria": [{"name": "Hardness", "raw_value": "At least F"}],
                        "evidence": [{"page": 1, "quote": PENCIL}],
                    }
                ],
            },
        }
    )
    if source_flag:
        run.extraction.review_issues = ["req_pencil: verify this source condition."]
    specs.save_extraction(source.document_id, run)
    lab = lab_store.load_or_create()
    lab.capabilities = [
        LabCapability(
            id="cap_pencil",
            canonical_test_id="pencil_hardness",
            method_ids=["iso_15184"],
            declared_constraints="Coated flat panels.",
            structured_limits=[
                LabOperatingLimit(parameter="load", minimum="700", maximum="800", unit="g")
            ],
            price=Decimal("100.00"),
            currency="USD",
            pricing_basis="per panel",
        )
    ]
    lab_store.save(lab)
    return source, run, lab_store


def edit_body(response, **changes):
    payload = response.json() if hasattr(response, "json") else response
    report = payload["report"]
    return {
        "run_id": report["source_run"]["run_id"],
        "input_fingerprint": report["input_fingerprint"],
        "version": report["workspace"]["version"],
        **changes,
    }


def detailed_analysis_payload() -> dict:
    return {
        "schema_version": "0.1",
        "document": {"title": "Offline customer specification"},
        "requirements": [
            {
                "id": "req_analysis",
                "original_test_name": "Pencil hardness",
                "requested_methods": [{"code": "ISO 15184"}],
                "conditions": [{"name": "Load", "raw_value": "750", "unit": "g"}],
                "acceptance_criteria": [{"name": "Hardness", "raw_value": "at least F"}],
                "applicability": ["flat coated panels"],
                "special_instructions": [],
                "evidence": [{"page": 1, "quote": PENCIL}],
            }
        ],
        "review_issues": [],
    }


def test_empty_workspace_is_local_and_does_not_seed_demo_or_analyze(client):
    http, root = client
    overview = http.get("/api/workspace")
    assert overview.status_code == 200
    assert overview.json()["lab"]["capabilities"] == []
    assert overview.json()["lab"]["name"] == "My Lab"
    assert overview.json()["providers"] == [
        {
            "id": "groq",
            "label": "Groq",
            "model": "qwen/qwen3.8-27b",
            "available": False,
            "output_limit": overview.json()["providers"][0]["output_limit"],
        },
        {
            "id": "gemini",
            "label": "Gemini",
            "model": "gemini-3.5-flash-lite",
            "available": False,
            "output_limit": overview.json()["providers"][1]["output_limit"],
        },
    ]
    lab = http.get("/api/lab").json()
    assert lab["capabilities"] == []
    assert http.get("/api/specifications").json() == []
    assert list((root / "specifications").glob("*")) == []
    assert http.get("/api/health").json() == {"status": "ok"}


def test_legacy_lab_and_spec_store_data_remain_visible_and_unmodified(client):
    http, root = client
    source, run, lab_store = seed_source(root)
    before_lab = lab_store.load_or_create()
    before_source = FileSpecStore(root).load_document(source.document_id)
    before_pdf = FileSpecStore(root).read_pdf(source.document_id)
    before_run = FileSpecStore(root).load_extraction(source.document_id, run.run_id)

    overview = http.get("/api/workspace").json()
    assert overview["lab"]["version"] == before_lab.version
    assert overview["lab"]["capabilities"][0]["price"] == "100.00"
    assert overview["documents"][0]["document_id"] == source.document_id
    visible = http.get(f"/api/specifications/{source.document_id}").json()
    assert visible["run"]["run_id"] == run.run_id
    assert visible["run"]["raw_response"] == " ORIGINAL PROVIDER TEXT\n"
    assert http.get(f"/api/specifications/{source.document_id}/pdf").content == before_pdf

    assert lab_store.load_or_create() == before_lab
    store = FileSpecStore(root)
    assert store.load_document(source.document_id) == before_source
    assert store.read_pdf(source.document_id) == before_pdf
    assert store.load_extraction(source.document_id, run.run_id) == before_run
    assert not store.load_review(source.document_id, run.run_id).decisions


def test_lab_decimal_limits_roundtrip_stale_write_and_example_safety(client):
    http, root = client
    empty = http.get("/api/lab").json()
    assert empty["capabilities"] == []
    lab = LabProfile.model_validate(
        {
            **empty,
            "description": "Exact lab profile.",
            "capabilities": [
                {
                    "id": "cap_pencil",
                    "canonical_test_id": "pencil_hardness",
                    "method_ids": ["iso_15184"],
                    "declared_constraints": "exact strings",
                    "structured_limits": [
                        {
                            "parameter": "load",
                            "minimum": "000.7500",
                            "maximum": "1.2500",
                            "values": [],
                            "unit": "g",
                            "supported_values": [],
                        },
                        {
                            "parameter": "load",
                            "minimum": None,
                            "maximum": None,
                            "values": ["750.000", "800.5000"],
                            "unit": "g",
                            "supported_values": [],
                        },
                    ],
                    "notes": None,
                    "price": "00100.2500",
                    "currency": "USD",
                    "pricing_basis": "per panel",
                }
            ],
        }
    )
    saved = http.put("/api/lab", json=lab.model_dump(mode="json"))
    assert saved.status_code == 200
    saved_body = saved.json()
    assert saved_body["version"] == 2
    cap = saved_body["capabilities"][0]
    assert cap["price"] == "100.2500"  # Decimal canonicalizes leading zeros, retaining scale.
    assert [limit["minimum"] for limit in cap["structured_limits"]] == ["0.7500", None]
    assert cap["structured_limits"][1]["values"] == ["750.000", "800.5000"]

    stale = http.put("/api/lab", json=lab.model_dump(mode="json"))
    assert stale.status_code == 409
    assert http.get("/api/lab").json() == saved_body
    blocked_example = http.post("/api/lab/example", json={"version": 2})
    assert blocked_example.status_code == 409
    assert http.get("/api/lab").json() == saved_body

    # Use an independent application root to prove example loading is explicit
    # and retains the chosen profile name/description.
    other_root = root.parent / "example-workspace"
    other = LocalHTTPClient(
        create_app(
            Settings(_env_file=None, scione_workspace_dir=other_root),
            frontend_dir=root.parent / "missing",
        )
    )
    initial = other.get("/api/lab").json()
    changed = {**initial, "name": "Precision Bench", "description": "Local lab"}
    profile = other.put("/api/lab", json=changed).json()
    example = other.post("/api/lab/example", json={"version": profile["version"]})
    assert example.status_code == 200
    assert example.json()["name"] == "Precision Bench"
    assert example.json()["description"] == "Local lab"
    assert example.json()["capabilities"]
    assert (
        other.post("/api/lab/example", json={"version": example.json()["version"]}).status_code
        == 409
    )


def test_upload_and_demo_import_create_sources_without_inference(client):
    http, _ = client
    uploaded = http.post(
        "/api/specifications", files={"file": ("upload.pdf", make_pdf(), "application/pdf")}
    )
    assert uploaded.status_code == 200
    document_id = uploaded.json()["document_id"]
    source = http.get(f"/api/specifications/{document_id}").json()
    assert source["run"] is None
    assert source["review"] is None
    assert source["document"]["extraction_status"]
    assert http.get("/api/reports/" + document_id).status_code == 400

    demo = http.post("/api/specifications/demo")
    assert demo.status_code == 200
    demo_source = http.get(f"/api/specifications/{demo.json()['document_id']}").json()
    assert demo_source["run"] is None
    assert len(http.get("/api/specifications").json()) == 2


def test_review_is_separate_from_source_requires_reason_ack_and_latest_version(client):
    http, root = client
    source, run, _ = seed_source(root)
    original_run = FileSpecStore(root).load_extraction(source.document_id, run.run_id)
    body = {
        "run_id": run.run_id,
        "version": 0,
        "requirement_ids": ["req_pencil"],
        "decision": "confirmed",
    }
    confirmed = http.post(f"/api/specifications/{source.document_id}/review", json=body)
    assert confirmed.status_code == 200
    assert confirmed.json()["review"]["version"] == 1
    assert confirmed.json()["review"]["decisions"]["req_pencil"]["status"] == "confirmed"
    assert FileSpecStore(root).load_extraction(source.document_id, run.run_id) == original_run

    stale = http.post(f"/api/specifications/{source.document_id}/review", json=body)
    assert stale.status_code == 409
    bad_exclusion = http.post(
        f"/api/specifications/{source.document_id}/review",
        json={
            **body,
            "version": 1,
            "decision": "excluded",
            "note": "",
        },
    )
    assert bad_exclusion.status_code == 400
    bad_batch = http.post(
        f"/api/specifications/{source.document_id}/review",
        json={
            **body,
            "version": 1,
            "requirement_ids": ["req_pencil", "req_missing"],
            "acknowledged": False,
        },
    )
    assert bad_batch.status_code == 400

    next_run = run.model_copy(deep=True)
    next_run.run_id = str(uuid4())
    FileSpecStore(root).save_extraction(source.document_id, next_run)
    stale_run = http.post(
        f"/api/specifications/{source.document_id}/review",
        json={
            **body,
            "version": 1,
        },
    )
    assert stale_run.status_code == 409
    assert (
        http.get(f"/api/specifications/{source.document_id}?run_id={run.run_id}").json()["run"][
            "run_id"
        ]
        == run.run_id
    )


def test_report_rejects_wrong_method_missing_identity_condition_gap_and_unreviewed_quote(client):
    http, root = client
    source, _run, _lab = seed_source(root, method="ISO 2409")
    report = http.get(f"/api/reports/{source.document_id}").json()
    line = report["report"]["lines"][0]
    assert line["status"] in {"method_gap", "needs_review"}
    input_body = edit_body(
        report,
        requirement_id="req_pencil",
        mapping={
            "canonical_test_id": "pencil_hardness",
            "selected_method_codes": ["ISO 2409"],
            "capability_id": "cap_pencil",
            "capability_verdict": "supported",
            "note": "Try promotion.",
        },
    )
    assert (
        http.post(f"/api/reports/{source.document_id}/mapping", json=input_body).status_code == 400
    )

    invalid_identity = edit_body(
        report,
        requirement_id="req_pencil",
        mapping={
            "canonical_test_id": "missing_test",
            "selected_method_codes": ["ISO 15184"],
            "capability_id": None,
            "capability_verdict": "unreviewed",
            "note": "",
        },
    )
    assert (
        http.post(f"/api/reports/{source.document_id}/mapping", json=invalid_identity).status_code
        == 400
    )

    unsupported = http.post(
        f"/api/reports/{source.document_id}/quantity",
        json=edit_body(
            report,
            requirement_id="req_pencil",
            quantity="2",
            scope_and_basis_confirmed=True,
            note="2 panels",
        ),
    )
    assert unsupported.status_code == 400
    assert FileReportStore(root).list_reports(source.document_id) == []

    # Exact method but a declared load outside the lab's range cannot be marked supported.
    source2, _run2, _ = seed_source(root, condition="900")
    gap_report = http.get(f"/api/reports/{source2.document_id}").json()
    assert gap_report["report"]["lines"][0]["operation_assessment"] == "gap"
    gap_body = edit_body(
        gap_report,
        requirement_id="req_pencil",
        mapping={
            "canonical_test_id": "pencil_hardness",
            "selected_method_codes": ["ISO 15184"],
            "capability_id": "cap_pencil",
            "capability_verdict": "supported",
            "note": "Ignore the gap.",
        },
    )
    assert (
        http.post(f"/api/reports/{source2.document_id}/mapping", json=gap_body).status_code == 400
    )
    scope_body = edit_body(
        gap_report, requirement_ids=["req_pencil"], supported=True, note="", acknowledged=False
    )
    assert (
        http.post(f"/api/reports/{source2.document_id}/scope", json=scope_body).status_code == 400
    )


def test_source_review_and_lab_support_are_independent_quote_gates(client):
    http, root = client
    source, run, labs = seed_source(root, source_flag=True)
    lab = labs.load_or_create()
    lab.capabilities[0].structured_limits = []
    labs.save(lab)
    original = FileSpecStore(root).load_extraction(source.document_id, run.run_id)
    report = http.get(f"/api/reports/{source.document_id}").json()
    assert report["line_states"]["req_pencil"]["needs_review"]
    assert not report["line_states"]["req_pencil"]["quote_eligible"]
    quote_body = edit_body(
        report,
        requirement_id="req_pencil",
        quantity="1",
        scope_and_basis_confirmed=True,
        note="One panel.",
    )
    assert (
        http.post(f"/api/reports/{source.document_id}/quantity", json=quote_body).status_code == 400
    )

    confirmed = http.post(
        f"/api/specifications/{source.document_id}/review",
        json={
            "run_id": run.run_id,
            "version": 0,
            "requirement_ids": ["req_pencil"],
            "decision": "confirmed",
            "note": "Checked the source PDF.",
        },
    )
    assert confirmed.status_code == 200
    after_source_review = http.get(f"/api/reports/{source.document_id}").json()
    assert after_source_review["report"]["lines"][0]["extraction_review_state"] == "confirmed"
    assert after_source_review["line_states"]["req_pencil"]["needs_review"]
    assert not after_source_review["line_states"]["req_pencil"]["quote_eligible"]
    assert (
        http.post(
            f"/api/reports/{source.document_id}/quantity",
            json=edit_body(
                after_source_review,
                requirement_id="req_pencil",
                quantity="1",
                scope_and_basis_confirmed=True,
                note="One panel.",
            ),
        ).status_code
        == 400
    )

    mapped = http.post(
        f"/api/reports/{source.document_id}/mapping",
        json=edit_body(
            after_source_review,
            requirement_id="req_pencil",
            mapping={
                "canonical_test_id": "pencil_hardness",
                "selected_method_codes": ["ISO 15184"],
                "capability_id": "cap_pencil",
                "capability_verdict": "supported",
                "note": "Lab scope reviewed.",
            },
        ),
    )
    assert mapped.status_code == 200
    assert mapped.json()["line_states"]["req_pencil"]["quote_eligible"]
    assert FileSpecStore(root).load_extraction(source.document_id, run.run_id) == original


def test_supported_quantity_snapshot_pdf_json_and_history_survive_lab_edit(client):
    http, root = client
    source, run, lab_store = seed_source(root)
    report = http.get(f"/api/reports/{source.document_id}").json()
    mapping = edit_body(
        report,
        requirement_id="req_pencil",
        mapping={
            "canonical_test_id": "pencil_hardness",
            "selected_method_codes": ["ISO 15184"],
            "capability_id": "cap_pencil",
            "capability_verdict": "supported",
            "note": "Checked the 700 to 800 g scope.",
        },
    )
    reviewed = http.post(f"/api/reports/{source.document_id}/mapping", json=mapping)
    assert reviewed.status_code == 200
    assert reviewed.json()["line_states"]["req_pencil"]["quote_eligible"]
    quantity = edit_body(
        reviewed.json(),
        requirement_id="req_pencil",
        quantity="2.50",
        scope_and_basis_confirmed=True,
        note="Two and a half panels.",
    )
    priced = http.post(f"/api/reports/{source.document_id}/quantity", json=quantity)
    assert priced.status_code == 200
    assert priced.json()["report"]["lines"][0]["quote"]["subtotal"] == "250.0000"

    generated = http.post(
        f"/api/reports/{source.document_id}/snapshots",
        json={
            "run_id": run.run_id,
            "input_fingerprint": priced.json()["report"]["input_fingerprint"],
        },
    )
    assert generated.status_code == 200
    report_id = generated.json()["report_id"]
    snapshot = http.get(f"/api/snapshots/{report_id}").json()
    assert snapshot["report"]["lines"][0]["quote"]["quantity"] == "2.50"
    pdf_before = http.get(f"/api/snapshots/{report_id}/pdf").content
    json_before = http.get(f"/api/snapshots/{report_id}/json").content
    assert pdf_before.startswith(b"%PDF-")
    assert b'"quantity": "2.50"' in json_before

    lab = lab_store.load_or_create()
    lab.capabilities[0].price = Decimal("999.125")
    lab_store.save(lab)
    current = http.get(f"/api/reports/{source.document_id}").json()
    assert current["report"]["lines"][0]["quote"] is None
    assert http.get(f"/api/snapshots/{report_id}").json() == snapshot
    assert http.get(f"/api/snapshots/{report_id}/pdf").content == pdf_before
    assert http.get(f"/api/snapshots/{report_id}/json").content == json_before


def test_local_origin_and_invalid_lab_write_do_not_overwrite_saved_data(client):
    http, root = client
    lab = http.get("/api/lab").json()
    candidate = {**lab, "description": "Saved profile"}
    saved = http.put("/api/lab", json=candidate)
    assert saved.status_code == 200
    before = saved.json()
    foreign = http.put(
        "/api/lab", json={**before, "name": "Hijack"}, headers={"Origin": "https://evil.example"}
    )
    assert foreign.status_code == 403
    invalid = http.put("/api/lab", json={**before, "capabilities": [{"id": "invalid id"}]})
    assert invalid.status_code == 422
    assert http.get("/api/lab").json() == before
    assert FileLabStore(
        root, load_reference_catalog()
    ).load_or_create() == LabProfile.model_validate(before)


def test_diagnostics_can_select_old_run_without_moving_latest_or_changing_review(client):
    http, root = client
    source, first, _ = seed_source(root)
    specs = FileSpecStore(root)
    second = first.model_copy(deep=True)
    second.run_id = str(uuid4())
    second.raw_response = "SECOND IMMUTABLE RESPONSE"
    specs.save_extraction(source.document_id, second)
    before = specs.load_review(source.document_id, first.run_id)
    selected = http.get(f"/api/diagnostics/{source.document_id}?run_id={first.run_id}")
    assert selected.status_code == 200
    assert selected.json()["selected"]["run"]["run_id"] == first.run_id
    assert selected.json()["runs"]
    latest = http.get(f"/api/specifications/{source.document_id}").json()
    assert latest["run"]["run_id"] == second.run_id
    assert specs.load_review(source.document_id, first.run_id) == before
    assert not specs.load_review(source.document_id, second.run_id).decisions


@pytest.mark.parametrize(
    "permission",
    [
        {"provider": "groq", "permission": False, "free_tier_confirmed": False},
        {"provider": "gemini", "permission": True, "free_tier_confirmed": False},
    ],
)
def test_analysis_checks_permission_and_gemini_tier_before_provider_creation(
    client, monkeypatch: pytest.MonkeyPatch, permission: dict
):
    import scione.api.app as api_module

    http, root = client
    source, run, _ = seed_source(root)
    source_before = FileSpecStore(root).load_document(source.document_id)

    def forbidden_factory(*_args, **_kwargs):
        pytest.fail("Permission and Gemini tier checks must run before provider creation.")

    monkeypatch.setattr(api_module, "create_configured_provider", forbidden_factory)
    response = http.post(
        f"/api/specifications/{source.document_id}/analysis",
        json={
            **permission,
            "model": "offline-model",
            "strategy": "whole_document",
            "output_limit": 2048,
        },
    )
    assert response.status_code == 400
    specs = FileSpecStore(root)
    assert specs.load_latest_extraction(source.document_id).run_id == run.run_id
    assert specs.load_failure(source.document_id) is None
    assert specs.load_document(source.document_id) == source_before


def test_offline_analysis_and_failed_reanalysis_preserve_latest_run_and_review(
    client, monkeypatch: pytest.MonkeyPatch
):
    import scione.api.app as api_module

    http, root = client
    source, _old_run, _ = seed_source(root)
    specs = FileSpecStore(root)
    source_before = specs.load_document(source.document_id)
    pdf_before = specs.read_pdf(source.document_id)
    endpoint = f"/api/specifications/{source.document_id}/analysis"
    request_body = {
        "provider": "groq",
        "model": "offline-model",
        "strategy": "whole_document",
        "output_limit": 4096,
        "permission": True,
        "free_tier_confirmed": False,
    }
    monkeypatch.setattr(
        api_module,
        "create_configured_provider",
        lambda *_args, **_kwargs: StaticModelProvider(
            detailed_analysis_payload(), model_name="offline-detailed"
        ),
    )
    analyzed = http.post(endpoint, json=request_body)
    assert analyzed.status_code == 200, analyzed.text
    run = analyzed.json()["run"]
    assert run["provider"] == "static"
    assert run["model"] == "offline-detailed"
    assert run["strategy"] == "whole_document"
    assert run["extraction"]["requirements"][0]["id"] == "req_analysis"
    assert run["extraction"]["requirements"][0]["applicability"] == ["flat coated panels"]
    latest_json = specs.load_latest_extraction(source.document_id).model_dump_json()

    confirmed = http.post(
        f"/api/specifications/{source.document_id}/review",
        json={
            "run_id": run["run_id"],
            "version": 0,
            "requirement_ids": ["req_analysis"],
            "decision": "confirmed",
            "note": "Checked against the offline source fixture.",
        },
    )
    assert confirmed.status_code == 200
    review_before = specs.load_review(source.document_id, run["run_id"])
    monkeypatch.setattr(
        api_module,
        "create_configured_provider",
        lambda *_args, **_kwargs: StaticModelProvider({"schema_version": "0.1"}),
    )
    failed = http.post(endpoint, json=request_body)
    assert failed.status_code == 400
    assert "did not conform to CustomerExtraction" in failed.json()["detail"]
    assert specs.load_latest_extraction(source.document_id).model_dump_json() == latest_json
    assert specs.load_review(source.document_id, run["run_id"]) == review_before
    failure = specs.load_failure(source.document_id)
    assert failure is not None
    assert failure.previous_run_id == run["run_id"]
    assert specs.load_document(source.document_id) == source_before
    assert specs.read_pdf(source.document_id) == pdf_before


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("supported", [False, True])
def test_operating_gap_can_be_declined_but_never_confirmed_as_supported(
    client, batch: bool, supported: bool
):
    http, root = client
    source, run, _ = seed_source(root, condition="900")
    requirement_ids = ["req_pencil"]
    if batch:
        changed = run.model_copy(deep=True)
        changed.run_id = str(uuid4())
        second = changed.extraction.requirements[0].model_copy(deep=True)
        second.id = "req_pencil_2"
        changed.extraction.requirements.append(second)
        FileSpecStore(root).save_extraction(source.document_id, changed)
        requirement_ids.append("req_pencil_2")

    report = http.get(f"/api/reports/{source.document_id}").json()
    assert all(
        report["report"]["lines"][index]["operation_assessment"] == "gap"
        for index in range(len(requirement_ids))
    )
    assert all(
        report["line_states"][identifier]["batch_eligible"] for identifier in requirement_ids
    )
    body = edit_body(
        report,
        requirement_ids=requirement_ids,
        supported=supported,
        note="" if supported else "The lab cannot meet the requested load.",
        acknowledged=batch,
    )
    response = http.post(f"/api/reports/{source.document_id}/scope", json=body)
    if supported:
        assert response.status_code == 400
        assert (
            FileReportStore(root)
            .load_workspace(source.document_id, report["report"]["source_run"]["run_id"])
            .version
            == 0
        )
    else:
        assert response.status_code == 200
        mappings = response.json()["report"]["workspace"]["mappings"]
        assert set(mappings) == set(requirement_ids)
        assert all(choice["capability_verdict"] == "unsupported" for choice in mappings.values())


def test_mapping_rejects_duplicate_methods_and_normalizes_empty_source_method_selection(client):
    http, root = client
    source, run, _ = seed_source(root)
    report = http.get(f"/api/reports/{source.document_id}").json()
    duplicate = edit_body(
        report,
        requirement_id="req_pencil",
        mapping={
            "canonical_test_id": "pencil_hardness",
            "selected_method_codes": ["ISO 15184", "ISO 15184"],
            "capability_id": "cap_pencil",
            "capability_verdict": "unreviewed",
            "note": "Duplicate source method input.",
        },
    )
    assert (
        http.post(f"/api/reports/{source.document_id}/mapping", json=duplicate).status_code == 422
    )
    assert FileReportStore(root).load_workspace(source.document_id, run.run_id).version == 0

    methodless = run.model_copy(deep=True)
    methodless.run_id = str(uuid4())
    methodless.extraction.requirements[0].requested_methods = []
    FileSpecStore(root).save_extraction(source.document_id, methodless)
    current = http.get(f"/api/reports/{source.document_id}").json()
    response = http.post(
        f"/api/reports/{source.document_id}/mapping",
        json=edit_body(
            current,
            requirement_id="req_pencil",
            mapping={
                "canonical_test_id": "pencil_hardness",
                "selected_method_codes": [],
                "capability_id": None,
                "capability_verdict": "unreviewed",
                "note": "No source methods were extracted.",
            },
        ),
    )
    assert response.status_code == 200
    choice = (
        FileReportStore(root)
        .load_workspace(source.document_id, methodless.run_id)
        .mappings["req_pencil"]
    )
    assert choice.selected_method_codes is None
