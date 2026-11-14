"""Laboratory isolation through concurrent ASGI requests; no servers or models."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from sqlalchemy import insert
from sqlalchemy.exc import OperationalError

from scione.api.tenancy import (
    TenantWorkspaceMiddleware,
    TenantWorkspaceRegistry,
    tenant_schema,
)
from scione.config import ConfigurationError, Settings
from scione.library.store import digest
from scione.matching.store import matching_runs
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.store import FileSpecStore, SpecStoreError

LAB_A = "a" * 32
LAB_B = "b" * 32


def settings(tmp_path, **kwargs):
    return Settings(
        _env_file=None,
        scione_workspace_dir=tmp_path.resolve(),
        scione_database_url=None,
        **kwargs,
    )


def pdf(text="Gloss: ISO 2813. Measure at 60 degrees."):
    with pymupdf.open() as document:
        document.new_page().insert_text((40, 50), text)
        return document.tobytes(no_new_id=True)


def seed_run(workspace, document, raw="laboratory A private response"):
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "document_id": document.document_id,
            "started_at": datetime.now(UTC),
            "schema_version": "0.1",
            "prompt_version": "tenant_test_fixture",
            "provider": "offline",
            "model": "no_network",
            "latency_ms": 0,
            "usage": {},
            "raw_response": raw,
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": "Lab-private specification"},
                "requirements": [
                    {
                        "id": "req_gloss",
                        "original_test_name": "Gloss",
                        "requested_methods": [{"code": "ISO 2813"}],
                        "evidence": [{"page": 1, "quote": "Gloss: ISO 2813."}],
                    }
                ],
            },
        }
    )
    workspace.specs.save_extraction(document.document_id, run)
    return run


def test_proxy_has_no_default_workspace_and_nested_binding_resets(tmp_path):
    registry = TenantWorkspaceRegistry(settings(tmp_path))
    with pytest.raises(Exception, match="Select a laboratory"):
        registry.proxy.active_documents()
    with registry.bind(LAB_A):
        assert registry.current_lab_id() == LAB_A
        with registry.bind(LAB_B):
            assert registry.current_lab_id() == LAB_B
        assert registry.current_lab_id() == LAB_A
        other = TenantWorkspaceRegistry(settings(tmp_path / "other"))
        with pytest.raises(Exception, match="Select a laboratory"):
            other.proxy.active_documents()
    with pytest.raises(Exception, match="Select a laboratory"):
        registry.current()
    registry.close_all()
    with pytest.raises(ConfigurationError, match="stopped"):
        registry.workspace_for(LAB_A)


def test_same_pdf_and_source_runs_are_independent_and_latest_filename_is_view(tmp_path):
    registry = TenantWorkspaceRegistry(settings(tmp_path))
    a, b = registry.workspace_for(LAB_A), registry.workspace_for(LAB_B)
    data = pdf()
    first = a.specs.import_pdf("first-upload.pdf", data)
    run = seed_run(a, first)
    a.library.index(first.document_id)
    original_paths = [first.pdf_path, first.pdf_path.with_name("metadata.json")]
    originals = {path: path.read_bytes() for path in original_paths}
    renamed = a.specs.import_pdf("my-current-filename.pdf", data)
    same_b = b.specs.import_pdf("lab-b-name.pdf", data)
    assert renamed.document_id == same_b.document_id
    assert renamed.original_name == "my-current-filename.pdf"
    assert a.current_document(first.document_id).document.file_name == renamed.original_name
    assert a.library.document(first.document_id)["original_name"] == renamed.original_name
    assert a.library.store.document(first.document_id)["original_name"] == "first-upload.pdf"
    assert a.library.comparison(first.document_id)["document"]["original_name"] == (
        renamed.original_name
    )
    assert a.specs.list_documents()[0].original_name == renamed.original_name
    assert b.library.document(first.document_id)["original_name"] == "lab-b-name.pdf"
    assert b.specs.load_latest_extraction(first.document_id) is None
    assert a.specs.load_extraction(first.document_id, run.run_id).raw_response == run.raw_response
    assert {path: path.read_bytes() for path in original_paths} == originals
    assert FileSpecStore(a.settings.scione_workspace_dir).load_document(
        first.document_id
    ).original_name == "first-upload.pdf"
    assert len(list(first.pdf_path.parent.joinpath("upload_names").glob("*.json"))) == 1
    a.specs.import_pdf("my-current-filename.pdf", data)
    assert len(list(first.pdf_path.parent.joinpath("upload_names").glob("*.json"))) == 1
    registry.close_all()


def test_same_pdf_library_metadata_and_sql_lab_are_isolated(tmp_path):
    registry = TenantWorkspaceRegistry(settings(tmp_path))
    a, b = registry.workspace_for(LAB_A), registry.workspace_for(LAB_B)
    data = pdf()
    doc_a = a.specs.import_pdf("a.pdf", data)
    b.specs.import_pdf("b.pdf", data)
    # Editing SQL metadata in one tenant cannot affect the identical document ID
    # or lab profile in another tenant's independently stored database.
    row_a = a.library.document(doc_a.document_id)
    row_b = b.library.document(doc_a.document_id)
    from scione.api.contracts import LibraryMetadata

    a.library.metadata(
        doc_a.document_id,
        LibraryMetadata(
            expected_version=row_a["version"],
            company_name="Customer A only",
            title="Private title A",
            specification_id="PRIVATE-A",
            revision="A",
        ),
    )
    assert a.library.document(doc_a.document_id)["company_name"] == "Customer A only"
    assert b.library.document(doc_a.document_id)["company_name"] == row_b["company_name"]
    lab_a, lab_b = a.labs.load_or_create(), b.labs.load_or_create()
    lab_a.name = "Laboratory A private profile"
    a.labs.save(lab_a)
    assert b.labs.load_or_create().name == lab_b.name
    assert a.library.store.engine.url.database != b.library.store.engine.url.database
    registry.close_all()


def _test_app(registry):
    app = FastAPI()
    workspace = registry.proxy

    @app.get("/api/specifications/{document_id}/pdf")
    def source(document_id: str):
        return Response(workspace.specs.read_pdf(document_id), media_type="application/pdf")

    @app.get("/api/diagnostics/{document_id}/raw")
    def raw(document_id: str, run_id: str):
        return {"raw": workspace.specs.load_extraction(document_id, run_id).raw_response}

    @app.get("/api/comparison-snapshots/{comparison_id}/json")
    def comparison(comparison_id: str):
        return workspace.library.store.comparison(comparison_id)

    @app.get("/api/specifications/{document_id}/matching/{run_id}/artifacts")
    def artifact(document_id: str, run_id: str):
        return {"document": document_id, "run": run_id}

    @app.get("/api/snapshots/{report_id}/pdf")
    def report(report_id: str):
        return Response(workspace.reports.read_pdf(report_id), media_type="application/pdf")

    @app.get("/api/workspace")
    async def current():
        before = registry.current_lab_id()
        await asyncio.sleep(0.005)
        return {"lab": registry.current_lab_id(), "before": before}

    app.add_middleware(TenantWorkspaceMiddleware, registry=registry)

    class TrustedTestAuthentication:
        """A fixed server-side session map; tenant query/header input is ignored."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            cookie = dict(scope.get("headers", [])).get(b"cookie", b"").decode()
            lab_id = {"session=opaque-session-a": LAB_A, "session=opaque-session-b": LAB_B}.get(
                cookie
            )
            scope.setdefault("state", {})["auth_principal"] = SimpleNamespace(lab_id=lab_id)
            await self.app(scope, receive, send)

    app.add_middleware(TrustedTestAuthentication)
    return app


def test_asgi_concurrent_requests_and_artifact_id_guessing(tmp_path):
    registry = TenantWorkspaceRegistry(settings(tmp_path))
    a, b = registry.workspace_for(LAB_A), registry.workspace_for(LAB_B)
    doc = a.specs.import_pdf("private-a.pdf", pdf())
    # A matching document ID in B must not grant B access to A's raw runs,
    # matching attempts, or saved PDF comparison IDs.
    b.specs.import_pdf("private-b.pdf", a.specs.read_pdf(doc.document_id))
    only_a = a.specs.import_pdf("only-a.pdf", pdf("A private second source"))
    run = seed_run(a, doc)
    a.library.index(doc.document_id)
    saved = a.library.store.save_comparison(digest({"test": "private"}), {
        "document": {"document_id": doc.document_id},
        "report_input_fingerprint": "test-private-input",
        "summary": {},
        "requirements": [],
    })
    matching_id = "matching-private-a"
    with a.matching.store._engine().begin() as connection:
        connection.execute(insert(matching_runs).values(
            workspace_id=a.labs.workspace_id,
            run_id=matching_id,
            document_id=doc.document_id,
            extraction_run_id=run.run_id,
            input_fingerprint="private-input",
            created_at=datetime.now(UTC).isoformat(),
            status="completed", provider="offline", model="no_network",
            prompt_version="fixture", validation_version="fixture", payload={},
        ))
    from scione.reports.pdf import render_report_pdf

    snapshot = a.draft(doc.document_id).model_copy(update={"report_id": str(uuid4())})
    a.reports.save_report(snapshot, render_report_pdf(snapshot))
    app = _test_app(registry)

    async def check():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async def request(lab, path):
                return await client.get(path, headers={"Cookie": f"session=opaque-session-{lab}"})

            responses = await asyncio.gather(*[
                request("a" if i % 2 else "b", "/api/workspace") for i in range(30)
            ])
            assert all(response.status_code == 200 for response in responses)
            for index, response in enumerate(responses):
                expected = LAB_A if index % 2 else LAB_B
                assert response.json() == {"before": expected, "lab": expected}
            raw_path = f"/api/diagnostics/{doc.document_id}/raw?run_id={run.run_id}"
            assert (await request("a", raw_path)).json()["raw"] == run.raw_response
            paths = [
                raw_path,
                f"/api/specifications/{only_a.document_id}/pdf",
                f"/api/comparison-snapshots/{saved['comparison_id']}/json",
                f"/api/specifications/{doc.document_id}/matching/{matching_id}/artifacts",
                f"/api/snapshots/{snapshot.report_id}/pdf",
            ]
            for path in paths:
                response = await request("b", path)
                assert response.status_code == 404, (path, response.text)
                assert "private" not in response.text and str(tmp_path) not in response.text
            assert (await request("a", paths[-2])).status_code == 200
            assert (await request("a", paths[-1])).content.startswith(b"%PDF-")
            spoofed = await client.get(
                f"/api/specifications/{only_a.document_id}/pdf?lab_id={LAB_A}",
                headers={"Cookie": "session=opaque-session-b", "X-Lab-ID": LAB_A},
            )
            assert spoofed.status_code == 404
            missing_context = await client.get("/api/workspace")
            assert missing_context.status_code == 403

    asyncio.run(check())
    with pytest.raises(Exception, match="Select a laboratory"):
        registry.current()
    registry.close_all()


@pytest.mark.parametrize("lab_id", ["", "../escape", "A" * 32, "f" * 31, "x" * 32])
def test_invalid_ids_and_existing_symlinks_are_rejected(tmp_path, lab_id):
    registry = TenantWorkspaceRegistry(settings(tmp_path))
    with pytest.raises(ConfigurationError, match="identifier"):
        registry.workspace_for(lab_id)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "tenants").mkdir(exist_ok=True)
    (tmp_path / "tenants" / LAB_A).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ConfigurationError, match="storage"):
        registry.workspace_for(LAB_A)
    assert list(outside.iterdir()) == []


def test_existing_configured_root_and_cached_tenant_symlink_are_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    configured = tmp_path / "configured"
    configured.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ConfigurationError, match="storage"):
        TenantWorkspaceRegistry(Settings(
            _env_file=None, scione_workspace_dir=configured, scione_database_url=None,
        ))
    root = tmp_path / "safe"
    registry = TenantWorkspaceRegistry(settings(root))
    cached = registry.workspace_for(LAB_A)
    cached.settings.scione_workspace_dir.rmdir()
    cached.settings.scione_workspace_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ConfigurationError, match="storage"):
        registry.workspace_for(LAB_A)
    assert list(outside.iterdir()) == []


def test_mysql_schema_derivation_does_not_touch_central_or_connect_at_runtime(tmp_path):
    configured = Settings(
        _env_file=None,
        scione_workspace_dir=tmp_path,
        scione_database_url="mysql+pymysql://runtime:secret@database:3306/central_auth",
    )
    registry = TenantWorkspaceRegistry(configured)
    a, b = registry.workspace_for(LAB_A), registry.workspace_for(LAB_B)
    assert a.library.store.url.endswith(f"/{tenant_schema(LAB_A)}")
    assert b.library.store.url.endswith(f"/{tenant_schema(LAB_B)}")
    assert a.library.store._engine is None
    assert b.library.store._engine is None
    assert not list(tmp_path.rglob("*.sqlite3"))
    with pytest.raises(ConfigurationError, match="server does not match"):
        registry.provision(
            LAB_A, admin_database_url="mysql+pymysql://admin:secret@different/central_auth"
        )
    registry.close_all()


def test_embedded_provision_and_explicit_sqlite_never_reuse_central_database(tmp_path):
    configured = Settings(
        _env_file=None,
        scione_workspace_dir=tmp_path,
        scione_database_url=f"sqlite:///{tmp_path / 'central_auth.sqlite3'}",
    )
    registry = TenantWorkspaceRegistry(configured)
    result = registry.provision(LAB_A)
    assert result["backend"] == "sqlite"
    assert registry.workspace_for(LAB_A).library.store.engine.url.database == str(
        tmp_path / "tenants" / LAB_A / "library.sqlite3"
    )
    assert not (tmp_path / "central_auth.sqlite3").exists()
    registry.close_all()


@pytest.mark.parametrize("auth_url", [None, "mysql+pymysql://auth:secret@database/scione_" + LAB_A])
def test_configuration_cannot_alias_business_storage_to_account_schema(tmp_path, auth_url):
    base = "accounts" if auth_url else tenant_schema(LAB_A)
    config = Settings(
        _env_file=None, scione_workspace_dir=tmp_path,
        scione_database_url=f"mysql+pymysql://runtime:secret@database/{base}",
        scione_auth_database_url=auth_url,
    )
    registry = TenantWorkspaceRegistry(config)
    with pytest.raises(ConfigurationError, match="separate from account"):
        registry.workspace_for(LAB_A)
    assert registry._workspaces == {}
    assert not list(tmp_path.rglob("*.sqlite3"))


def test_current_upload_name_pointer_cannot_redirect_to_another_source(tmp_path):
    registry = TenantWorkspaceRegistry(settings(tmp_path))
    store = registry.workspace_for(LAB_A).specs
    source = store.import_pdf("original.pdf", pdf())
    store.import_pdf("current.pdf", pdf())
    pointer = source.pdf_path.with_name("current_upload_name.json")
    record = json.loads(pointer.read_text())
    record["document_id"] = "0" * 16
    pointer.write_text(json.dumps(record))
    with pytest.raises(SpecStoreError, match="does not match"):
        store.load_document(source.document_id)
    assert FileSpecStore(store.root.parent).load_document(source.document_id).original_name == (
        "original.pdf"
    )
    registry.close_all()


def test_failed_mysql_provision_does_not_fallback_or_disclose_credentials(tmp_path, monkeypatch):
    configured = Settings(
        _env_file=None,
        scione_workspace_dir=tmp_path,
        scione_database_url="mysql+pymysql://runtime:private-token@database:3306/accounts",
    )
    registry = TenantWorkspaceRegistry(configured)

    def unavailable(*_args, **_kwargs):
        raise OperationalError("connect", {}, RuntimeError("private-token"))

    monkeypatch.setattr("scione.api.tenancy.create_engine", unavailable)
    with pytest.raises(ConfigurationError) as error:
        registry.provision(LAB_A)
    assert "private-token" not in str(error.value)
    assert not list(tmp_path.rglob("*.sqlite3"))
    assert registry._workspaces == {}


def test_mysql_provision_connects_without_the_not_yet_created_schema(tmp_path, monkeypatch):
    configured = Settings(
        _env_file=None, scione_workspace_dir=tmp_path,
        scione_database_url="mysql+pymysql://runtime:private-token@database:3306/accounts",
    )
    registry = TenantWorkspaceRegistry(configured)
    attempted = []

    def stop_after_inspecting_url(url, **_kwargs):
        attempted.append(url)
        raise OperationalError("offline", {}, RuntimeError("No live database in regression"))

    monkeypatch.setattr("scione.api.tenancy.create_engine", stop_after_inspecting_url)
    with pytest.raises(ConfigurationError, match="provisioning failed"):
        registry.provision(LAB_A)
    assert len(attempted) == 1
    assert attempted[0].database is None
    assert attempted[0].host == "database"
    assert attempted[0].port == 3306
    assert attempted[0].username == "runtime"
    assert registry._workspaces == {}


def test_real_auth_sessions_are_isolated_and_normal_views_keep_latest_filename(tmp_path):
    from scione.api.app import create_app
    from scione.auth import AuthManager
    from scione.auth.passwords import hash_password

    config = settings(tmp_path, scione_auth_enabled=True)
    manager = AuthManager(config)
    manager.initialize()
    registry = TenantWorkspaceRegistry(config)
    for lab_id, name in ((LAB_A, "Laboratory A"), (LAB_B, "Laboratory B")):
        manager.store.create_lab(name, lab_id)
        registry.provision(lab_id)
        manager.store.mark_lab_provisioned(lab_id)
        manager.store.create_user(
            "account-" + lab_id[0], name, "lab",
            hash_password("Offline test account passphrase!"), lab_id=lab_id,
        )
    a = registry.workspace_for(LAB_A)
    b = registry.workspace_for(LAB_B)
    source = a.specs.import_pdf("immutable-original.pdf", pdf())
    b.specs.import_pdf("private-b.pdf", a.specs.read_pdf(source.document_id))
    run = seed_run(a, source)
    manager.close()
    registry.close_all()
    app = create_app(config, frontend_dir=tmp_path / "no-frontend")

    async def check():
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://testserver") as client_a,
            httpx.AsyncClient(transport=transport, base_url="http://testserver") as client_b,
        ):
            login_a, login_b = await asyncio.gather(*[
                client.post("/api/auth/login", json={
                    "username": username,
                    "password": "Offline test account passphrase!",
                })
                for client, username in ((client_a, "account-a"), (client_b, "account-b"))
            ])
            assert login_a.status_code == login_b.status_code == 200
            csrf = login_a.json()["csrf_token"]
            renamed = await client_a.post(
                "/api/specifications", headers={"X-CSRF-Token": csrf},
                files={"file": ("current-user-name.pdf", pdf(), "application/pdf")},
            )
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["document_id"] == source.document_id
            path = f"/api/library/{source.document_id}"
            own_a, own_b = await asyncio.gather(client_a.get(path), client_b.get(path))
            assert own_a.status_code == own_b.status_code == 200
            assert own_a.json()["document"]["original_name"] == "current-user-name.pdf"
            assert own_b.json()["document"]["original_name"] == "private-b.pdf"
            assert own_b.json()["document"]["run_id"] is None
            own_run = await client_a.get(
                f"/api/specifications/{source.document_id}?run_id={run.run_id}"
            )
            foreign_run = await client_b.get(
                f"/api/specifications/{source.document_id}?run_id={run.run_id}"
            )
            assert own_run.status_code == 200
            assert foreign_run.status_code == 404
            source_review = await client_a.post(
                f"/api/specifications/{source.document_id}/review",
                headers={"X-CSRF-Token": csrf},
                json={
                    "run_id": run.run_id, "version": 0, "facts_version": 0,
                    "requirement_ids": ["req_gloss"], "decision": "confirmed",
                    "note": "Verified against the offline PDF.",
                },
            )
            assert source_review.status_code == 200, source_review.text
            assert "raw_response" not in source_review.text
            comparison_path = f"/api/comparisons/{source.document_id}"
            comparison_response = await client_a.get(comparison_path)
            assert comparison_response.status_code == 200, comparison_response.text
            report = comparison_response.json()
            machine_status = report["requirements"][0]["comparison"]["status"]
            accepted = await client_a.post(
                comparison_path + "/reviews", headers={"X-CSRF-Token": csrf},
                json={
                    "comparison_id": report["comparison_id"],
                    "input_fingerprint": report["report_input_fingerprint"],
                    "requirement_id": "req_gloss", "expected_version": 0,
                    "decision": "keep", "review_status": "accepted", "note": "",
                },
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["requirements"][0]["comparison"]["status"] == machine_status
            assert accepted.json()["assessment_reviews"]["req_gloss"]["review_status"] == (
                "accepted"
            )
            for excluded in (True, False):
                action = await client_a.post(
                    f"/api/reports/{source.document_id}/exclusions",
                    headers={"X-CSRF-Token": csrf},
                    json={
                        "run_id": run.run_id,
                        "input_fingerprint": report["report_input_fingerprint"],
                        "version": report["report_workspace_version"],
                        "requirement_ids": ["req_gloss"], "excluded": excluded,
                        "note": "Lab quotation scope decision.",
                    },
                )
                assert action.status_code == 200, action.text
                report = action.json()
                assert report["requirements"][0]["comparison"]["status"] == (
                    "excluded" if excluded else machine_status
                )
            snapshot_id = report["comparison_id"]
            for suffix in ("/pdf?download=true", "/json", "/pages/1"):
                export_path = f"/api/comparison-snapshots/{snapshot_id}{suffix}"
                own_export, foreign_export = await asyncio.gather(
                    client_a.get(export_path), client_b.get(export_path),
                )
                assert own_export.status_code == 200, own_export.text[:200]
                assert foreign_export.status_code == 404
                if suffix.startswith("/pdf"):
                    assert own_export.content.startswith(b"%PDF-")
                if suffix == "/json":
                    assert "raw_response" not in own_export.text
                    assert run.raw_response not in own_export.text
            # Lab users cannot elevate themselves with a tenant selector payload.
            switch = await client_b.post(
                "/api/auth/workspace", json={"lab_id": LAB_A},
                headers={"X-CSRF-Token": login_b.json()["csrf_token"]},
            )
            assert switch.status_code == 403
            raw = await client_b.get(
                f"/api/diagnostics/{source.document_id}/raw?run_id={run.run_id}"
            )
            assert raw.status_code == 403
            logged_out = await client_a.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
            assert logged_out.status_code == 200
            assert (await client_a.get(path)).status_code == 401

    try:
        asyncio.run(check())
    finally:
        app.state.tenant_registry.close_all()
        app.state.auth_manager.close()


def test_production_custom_host_secure_cookie_and_csrf_before_business_routes(tmp_path):
    from scione.api.app import create_app
    from scione.auth import AuthManager
    from scione.auth.passwords import hash_password

    config = settings(
        tmp_path, scione_auth_enabled=True, scione_environment="production",
        scione_public_url="https://lab.example.test",
    )
    manager = AuthManager(config)
    manager.initialize()
    manager.store.create_lab("Production-like test lab", LAB_A)
    registry = TenantWorkspaceRegistry(config)
    registry.provision(LAB_A)
    manager.store.mark_lab_provisioned(LAB_A)
    manager.store.create_user(
        "host-test", "Test lab user", "lab",
        hash_password("Production-like offline passphrase!"), lab_id=LAB_A,
    )
    manager.close()
    registry.close_all()
    app = create_app(config, frontend_dir=tmp_path / "no-frontend")

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://lab.example.test",
        ) as client:
            wrong_host = await client.get("/api/health", headers={"Host": "attacker.test"})
            assert wrong_host.status_code == 400
            body = {"username": "host-test", "password": "Production-like offline passphrase!"}
            foreign_login = await client.post(
                "/api/auth/login", json=body, headers={"Origin": "https://attacker.test"},
            )
            assert foreign_login.status_code == 403
            login = await client.post(
                "/api/auth/login", json=body, headers={"Origin": "https://lab.example.test"},
            )
            assert login.status_code == 200, login.text
            cookie = login.headers["set-cookie"]
            assert cookie.startswith("__Host-scione_session=")
            assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
            csrf = login.json()["csrf_token"]
            assert (await client.get("/api/workspace")).status_code == 200
            assert (await client.get("http://lab.example.test/api/workspace")).status_code == 401
            without_token = await client.post("/api/auth/logout")
            assert without_token.status_code == 403
            cross_site = await client.post(
                "/api/auth/logout", headers={
                    "X-CSRF-Token": csrf, "Origin": "https://attacker.test",
                    "Sec-Fetch-Site": "cross-site",
                },
            )
            assert cross_site.status_code == 403
            assert (await client.get("/api/auth/session")).json()["authenticated"] is True
            logout = await client.post(
                "/api/auth/logout", headers={
                    "X-CSRF-Token": csrf, "Origin": "https://lab.example.test",
                },
            )
            assert logout.status_code == 200
            assert (await client.get("/api/workspace")).status_code == 401

    try:
        asyncio.run(check())
    finally:
        app.state.tenant_registry.close_all()
        app.state.auth_manager.close()
