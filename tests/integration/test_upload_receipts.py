"""Truthful upload receipts; importing a file never performs inference."""

from test_authenticated_inference import (
    PASSWORD,
    login_and_upload,
)
from test_authenticated_inference import (
    authenticated_api as authenticated_api,
)
from test_semantic_matching_api import setup as setup
from test_workspace_api import make_pdf

from scione.auth.passwords import hash_password


def upload(client, filename, data):
    response = client.post(
        "/api/specifications", files={"file": (filename, data, "application/pdf")}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_new_upload_is_created_without_analysis(setup):
    client, workspace, _document_id, provider, _settings = setup
    receipt = upload(client, "new-request.pdf", make_pdf("New unique customer request."))
    assert receipt == {
        "document_id": receipt["document_id"],
        "created": True,
        "reused": False,
        "restored": False,
        "has_saved_analysis": False,
        "original_name": "new-request.pdf",
    }
    assert workspace.specs.load_latest_extraction(receipt["document_id"]) is None
    assert provider.calls == 0


def test_duplicate_upload_retains_first_name_and_saved_analysis(setup):
    client, workspace, document_id, provider, _settings = setup
    before = workspace.specs.load_latest_extraction(document_id)
    receipt = upload(client, "renamed-copy.pdf", workspace.specs.read_pdf(document_id))
    assert receipt == {
        "document_id": document_id,
        "created": False,
        "reused": True,
        "restored": False,
        "has_saved_analysis": True,
        "original_name": "synthetic.pdf",
    }
    assert workspace.specs.load_latest_extraction(document_id) == before
    assert workspace.specs.load_document(document_id).original_name == "synthetic.pdf"
    assert provider.calls == 0


def test_archived_duplicate_is_explicitly_restored_without_new_analysis(setup):
    client, workspace, document_id, provider, _settings = setup
    client.get("/api/library")  # Bring the source into the structured library first.
    store = workspace.library.store
    version = store.document(document_id)["version"]
    store.set_document_archived(document_id, archived=True, expected_version=version)
    before = workspace.specs.load_latest_extraction(document_id)
    receipt = upload(client, "another-name.pdf", workspace.specs.read_pdf(document_id))
    assert receipt["restored"] is True
    assert receipt["created"] is False
    assert receipt["reused"] is True
    assert receipt["has_saved_analysis"] is True
    assert receipt["original_name"] == "synthetic.pdf"
    assert document_id not in store.archived_document_ids()
    assert workspace.specs.load_latest_extraction(document_id) == before
    assert provider.calls == 0


def test_same_bytes_in_another_lab_are_a_new_upload(authenticated_api):
    client, workspace, captured = authenticated_api
    document_id, _headers, _user_id = login_and_upload(client)
    original = workspace.specs.read_pdf(document_id)
    manager = client.app.state.auth_manager
    registry = client.app.state.tenant_registry
    other = manager.store.create_lab("Independent upload lab")
    registry.provision(other.id)
    manager.store.mark_lab_provisioned(other.id)
    manager.store.create_user(
        "second.lab", "Second lab", "lab", hash_password(PASSWORD), lab_id=other.id
    )
    signed_in = client.post(
        "/api/auth/login", json={"username": "second.lab", "password": PASSWORD}
    )
    assert signed_in.status_code == 200
    response = client.post(
        "/api/specifications",
        headers={"X-CSRF-Token": signed_in.json()["csrf_token"]},
        files={"file": ("second-lab-name.pdf", original, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    assert response.json()["reused"] is False
    assert response.json()["has_saved_analysis"] is False
    assert response.json()["original_name"] == "second-lab-name.pdf"
    assert workspace.specs.load_document(document_id).original_name == "customer.pdf"
    assert captured["factory"] == []


def test_authenticated_duplicate_preserves_current_upload_name_and_immutable_source(
    authenticated_api,
):
    client, workspace, captured = authenticated_api
    document_id, headers, _user_id = login_and_upload(client)
    response = client.post(
        "/api/specifications",
        headers=headers,
        files={
            "file": (
                "renamed-duplicate.pdf",
                workspace.specs.read_pdf(document_id),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["reused"] is True
    assert response.json()["original_name"] == "renamed-duplicate.pdf"
    stored = workspace.specs.load_document(document_id)
    assert stored.original_name == "renamed-duplicate.pdf"
    assert (stored.pdf_path.parent / "current_upload_name.json").exists()
    from scione.specifications.store import FileSpecStore

    immutable = FileSpecStore.load_document(workspace.specs, document_id)
    assert immutable.original_name == "customer.pdf"
    assert client.get(f"/api/specifications/{document_id}").json()["original_name"] == (
        response.json()["original_name"]
    )
    assert client.get(f"/api/library/{document_id}").json()["document"]["original_name"] == (
        response.json()["original_name"]
    )
    assert captured["factory"] == []
