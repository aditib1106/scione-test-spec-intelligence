import pytest
from test_workspace_api import LocalHTTPClient

from scione.api.app import create_app
from scione.catalogs import load_reference_catalog
from scione.catalogs.examples import (
    demonstration_csp301_lab,
    detailed_csp301_lab,
    typed_csp301_lab,
)
from scione.config import Settings


@pytest.mark.parametrize(
    ("example_id", "loader"),
    [
        ("csp301_typed", typed_csp301_lab),
        ("csp301_detailed", detailed_csp301_lab),
        ("csp301_demo", demonstration_csp301_lab),
    ],
)
def test_explicit_typed_example_uses_catalog_ids_and_never_replaces_existing_lab(
    tmp_path, example_id, loader
):
    lab = loader()
    catalog = load_reference_catalog()
    assert len(lab.capabilities) == 4
    for capability in lab.capabilities:
        assert catalog.test_by_id(capability.canonical_test_id)
        assert capability.atomic_claims
        for method_id in capability.method_ids:
            assert catalog.method_by_id(method_id)
    settings = Settings(_env_file=None, scione_workspace_dir=tmp_path)
    client = LocalHTTPClient(create_app(settings))
    initial = client.get("/api/lab").json()
    # Examples are deliberately opt-in and only valid in an empty lab.
    assert initial["capabilities"] == []
    response = client.post(
        "/api/lab/example", json={"version": initial["version"], "example": example_id}
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["name"] == initial["name"]
    assert len(saved["capabilities"]) == 4
    assert any(
        claim["availability"] == "unsupported"
        for capability in saved["capabilities"]
        for claim in capability["atomic_claims"]
    )
    assert (
        client.post(
            "/api/lab/example", json={"version": saved["version"], "example": example_id}
        ).status_code
        == 409
    )
    assert client.get("/api/lab").json() == saved


def test_developer_example_replacement_requires_current_version_and_acknowledgement(tmp_path):
    client = LocalHTTPClient(create_app(Settings(_env_file=None, scione_workspace_dir=tmp_path)))
    initial = client.get("/api/lab").json()
    original = client.post(
        "/api/lab/example", json={"version": initial["version"], "example": "csp301"}
    ).json()
    body = {
        "version": original["version"],
        "example": "csp301_detailed",
        "replace_existing": True,
        "acknowledged": False,
    }
    assert client.post("/api/lab/example", json=body).status_code == 400
    assert client.get("/api/lab").json() == original
    body["acknowledged"] = True
    assert (
        client.post("/api/lab/example", json={**body, "version": initial["version"]}).status_code
        == 409
    )
    result = client.post("/api/lab/example", json=body)
    assert result.status_code == 200, result.text
    replacement = result.json()
    assert replacement["name"] == original["name"]
    assert replacement["version"] == original["version"] + 1
    assert sum(len(cap["atomic_claims"]) for cap in replacement["capabilities"]) == 76
    records = client.get("/api/lab/records").json()
    assert {row["version"] for row in records["revisions"]} >= {
        original["version"],
        replacement["version"],
    }


def test_updated_demo_declares_dimensions_preparation_reporting_and_a_real_gloss_gap():
    lab = demonstration_csp301_lab()
    for capability in lab.capabilities:
        parameters = {claim.parameter_id for claim in capability.atomic_claims}
        assert {"specimen_length", "specimen_width", "panel_preparation", "reporting"} <= parameters
        assert all(
            "application of the coating stack is not included" not in claim.raw_text
            for claim in capability.atomic_claims
        ), "The updated preparation service must not retain contradictory old boilerplate."
        count = len(capability.atomic_claims)
        assert len({claim.id for claim in capability.atomic_claims}) == count
    thickness = next(
        cap for cap in lab.capabilities if cap.canonical_test_id == "dry_film_thickness"
    )
    assert any(claim.parameter_id == "topcoat_thickness" and claim.kind == "reporting"
               for claim in thickness.atomic_claims)
    gloss = next(cap for cap in lab.capabilities if cap.canonical_test_id == "gloss")
    assert any(claim.parameter_id == "gloss_retention" and claim.availability == "unsupported"
               for claim in gloss.atomic_claims)
