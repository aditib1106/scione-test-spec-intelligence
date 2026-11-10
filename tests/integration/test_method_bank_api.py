"""Paged synthetic metadata reads do not mutate lab data or invoke a provider."""

import asyncio

import httpx
import pytest

from scione.api import method_bank
from scione.api.app import create_app
from scione.catalogs.bank import MethodBankError, load_bank_manifest
from scione.config import Settings
from scione.providers.gemini import GeminiModelProvider
from scione.providers.groq import GroqModelProvider
from scione.providers.openai_compatible import OpenAICompatibleModelProvider


@pytest.fixture
def app(tmp_path, monkeypatch):
    def reject_provider(*_args, **_kwargs):
        pytest.fail("Reference metadata reads must not invoke a model.")

    for provider in [GeminiModelProvider, GroqModelProvider, OpenAICompatibleModelProvider]:
        monkeypatch.setattr(provider, "generate_structured", reject_provider)
    return create_app(
        Settings(
            _env_file=None,
            scione_workspace_dir=tmp_path / "workspace",
            scione_database_url=None,
            gemini_api_key=None,
            groq_api_key=None,
            sail_api_key=None,
            fireworks_api_key=None,
        ),
        frontend_dir=tmp_path / "no-frontend",
    )


def request(app, path="/api/catalog/method-bank", *, method="GET", **kwargs):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_default_page_is_bounded_and_reports_provenance(app):
    response = request(app)
    assert response.status_code == 200
    data = response.json()
    assert len(data["records"]) == data["limit"] == 25
    assert data["offset"] == 0 and data["has_more"]
    assert data["total_count"] == data["bank_record_count"] == 32_000
    assert data["synthetic"] is True
    assert data["source_sha256"] == load_bank_manifest()["source_csv_sha256"]
    assert data["provenance"]["source_csv_sha256"] == data["source_sha256"]
    assert "do not establish" in data["source_note"]
    assert all(
        row["is_synthetic"] == "TRUE" and row["official_equivalence"] == "FALSE"
        for row in data["records"]
    )
    assert response.headers["cache-control"] == "no-store"


def test_identifier_is_exact_including_edition_and_does_not_populate_a_lab(app):
    before = request(app, "/api/lab").json()
    response = request(app, params={"identifier": " iso 60038 : 2021 "})
    data = response.json()
    assert response.status_code == 200
    assert data["total_count"] == 1 and not data["has_more"]
    assert data["records"][0]["record_id"] == "SC-ISO-00038"
    assert data["records"][0]["test_item_id"] == "COR-02"
    assert request(app, params={"identifier": "ISO 60038"}).json()["total_count"] == 0
    assert request(app, params={"identifier": "ISO 60038:2020"}).json()["total_count"] == 0
    assert request(app, "/api/lab").json() == before
    assert before["capabilities"] == []


def test_family_domain_and_material_filters_intersect_with_identifier_and_paging(app):
    first = request(app, params={"family_id": "COR-02", "limit": 2}).json()
    second = request(app, params={"family_id": "COR-02", "limit": 2, "offset": 2}).json()
    assert first["total_count"] == second["total_count"] == 638
    assert len(first["records"]) == len(second["records"]) == 2
    assert {row["record_id"] for row in first["records"]}.isdisjoint(
        row["record_id"] for row in second["records"]
    )
    row = first["records"][0]
    params = {
        "identifier": row["standard_id"],
        "family_id": row["test_item_id"],
        "domain": row["domain"].swapcase(),
        "material": row["test_material"].swapcase(),
    }
    exact = request(app, params=params).json()
    assert exact["total_count"] == 1 and exact["records"][0] == row
    assert request(app, params=params | {"domain": "Textiles"}).json()["total_count"] == 0
    assert (
        request(app, params={"identifier": row["standard_id"], "family_id": "missing"}).json()[
            "total_count"
        ]
        == 0
    )
    past = request(app, params={"identifier": row["standard_id"], "offset": 1}).json()
    assert past["total_count"] == 1 and past["records"] == [] and not past["has_more"]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"identifier": " "},
        {"family_id": ""},
        {"domain": " "},
        {"material": " "},
    ],
)
def test_invalid_query_is_rejected_without_loading_the_bank(app, monkeypatch, params):
    def reject_load():
        pytest.fail("An invalid query must fail before opening the full bank.")

    monkeypatch.setattr(method_bank, "load_synthetic_method_bank", reject_load)
    assert request(app, params=params).status_code == 422


def test_startup_and_existing_runtime_endpoints_do_not_open_or_emit_the_full_bank(
    tmp_path, monkeypatch
):
    def reject_load():
        pytest.fail("The full method bank must load only for an explicit bank query.")

    monkeypatch.setattr(method_bank, "load_synthetic_method_bank", reject_load)
    app = create_app(
        Settings(
            _env_file=None, scione_workspace_dir=tmp_path / "workspace", scione_database_url=None
        ),
        frontend_dir=tmp_path / "no-frontend",
    )
    active = request(app, "/api/catalog").json()
    workspace = request(app, "/api/workspace").json()
    assert len(active["tests"]) == 99 and len(active["methods"]) == 153
    assert len(workspace["catalog"]["methods"]) == 153


def test_bank_is_read_only_and_corrupt_package_metadata_has_a_safe_error(app, monkeypatch):
    assert request(app, method="POST", json={"support": True}).status_code == 405

    def unavailable():
        raise MethodBankError("Private diagnostic content")

    monkeypatch.setattr(method_bank, "load_synthetic_method_bank", unavailable)
    response = request(app)
    assert response.status_code == 503
    assert "Private diagnostic" not in response.text
