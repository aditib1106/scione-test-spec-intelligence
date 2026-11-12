"""Operator seeding targets an explicit lab account and preserves other workspaces."""

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, update

from scione.api.tenancy import TenantWorkspaceRegistry
from scione.api.workspace import Workspace
from scione.auth.store import AuthStore, labs, users
from scione.catalogs.baseline import development_lab_baseline, ms_pnt_410_lab_baseline
from scione.catalogs.sql_lab_store import lab_revisions
from scione.config import Settings


@pytest.fixture
def operator(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/initialize_lab.py"
    spec = importlib.util.spec_from_file_location("initialize_lab_test_subject", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    settings = Settings(
        _env_file=None, scione_workspace_dir=tmp_path / "workspace",
        scione_database_url=None, scione_auth_database_url=None, scione_auth_enabled=True,
    )
    monkeypatch.setattr(script, "Settings", lambda: settings)
    auth = AuthStore(settings)
    auth.initialize()
    first = auth.create_lab("First synthetic lab", active=True)
    second = auth.create_lab("Second synthetic lab", active=True)
    auth.create_user("lab1", "First lab user", "lab", "unused-test-hash", lab_id=first.id)
    auth.create_user("lab2", "Second lab user", "lab", "unused-test-hash", lab_id=second.id)
    auth.create_user("admin", "Maintainer", "maintainer", "unused-test-hash")
    try:
        yield script, settings, auth, first.id, second.id
    finally:
        auth.close()


def test_explicit_account_routes_only_to_its_tenant_and_preserves_profile_and_documents(operator):
    script, settings, _, first_id, second_id = operator
    registry = TenantWorkspaceRegistry(settings)
    try:
        first = registry.workspace_for(first_id)
        existing = first.labs.load_or_create()
        first.labs.save(existing.model_copy(update={
            "name": "Retained laboratory name",
            "description": "Retained details",
        }))
        before = first.labs.load_or_create()
        source = registry.tenant_root(first_id) / "original-client.pdf"
        source.write_bytes(b"unchanged original PDF bytes")
        second = registry.workspace_for(second_id)
        other = second.labs.load_or_create()
        second.labs.save(other.model_copy(update={
            "name": "Other lab", "capabilities": ms_pnt_410_lab_baseline().capabilities[:1],
        }))
        second_before = second.labs.load_or_create().model_dump(mode="json")
    finally:
        registry.close_all()

    script.main(["--profile", "ms-pnt-410", "--lab-user", "LAB1"])
    registry = TenantWorkspaceRegistry(settings)
    try:
        selected = registry.workspace_for(first_id)
        result = selected.labs.load_or_create()
        assert (result.id, result.name, result.description) == (
            before.id, "Retained laboratory name", "Retained details",
        )
        assert result.version == before.version + 1 and len(result.capabilities) == 12
        assert result.capabilities == ms_pnt_410_lab_baseline().capabilities
        assert source.read_bytes() == b"unchanged original PDF bytes"
        with selected.library.store.engine.connect() as connection:
            history = connection.execute(select(lab_revisions.c.version)).scalars().all()
        assert before.version in history and result.version in history
        assert registry.workspace_for(second_id).labs.load_or_create().model_dump(
            mode="json"
        ) == second_before
        assert not (settings.scione_workspace_dir / "library.sqlite3").exists()
        assert len(development_lab_baseline().capabilities) == 70
    finally:
        registry.close_all()


@pytest.mark.parametrize("username", ["missing", "admin", "x"])
def test_unknown_maintainer_or_invalid_account_cannot_select_a_workspace(operator, username):
    script, settings, _, _, _ = operator
    with pytest.raises(SystemExit) as caught:
        script.main(["--profile", "ms-pnt-410", "--lab-user", username])
    assert caught.value.code == 2
    assert not (settings.scione_workspace_dir / "tenants").exists()
    assert not (settings.scione_workspace_dir / "library.sqlite3").exists()


@pytest.mark.parametrize("inactive", ["account", "lab"])
def test_inactive_account_or_lab_is_rejected_without_business_write(operator, inactive):
    script, settings, auth, first_id, _ = operator
    if inactive == "account":
        auth.disable_user(auth.user_by_username("lab1")["id"])
    else:
        with auth.engine.begin() as connection:
            connection.execute(update(labs).where(labs.c.id == first_id).values(active=False))
    with pytest.raises(SystemExit) as caught:
        script.main(["--profile", "ms-pnt-410", "--lab-user", "lab1"])
    assert caught.value.code == 2
    assert not (settings.scione_workspace_dir / "tenants").exists()


def test_accounts_enabled_requires_explicit_lab_selection_before_legacy_write(operator):
    script, settings, _, _, _ = operator
    with pytest.raises(SystemExit) as caught:
        script.main(["--profile", "ms-pnt-410"])
    assert caught.value.code == 2
    assert not (settings.scione_workspace_dir / "tenants").exists()
    assert not (settings.scione_workspace_dir / "library.sqlite3").exists()


def test_existing_declarations_require_replace_and_replacement_keeps_earlier_revision(operator):
    script, settings, _, first_id, _ = operator
    args = ["--profile", "ms-pnt-410", "--lab-user", "lab1"]
    script.main(args)
    registry = TenantWorkspaceRegistry(settings)
    try:
        first = registry.workspace_for(first_id).labs.load_or_create().model_dump(mode="json")
    finally:
        registry.close_all()
    with pytest.raises(SystemExit) as caught:
        script.main(args)
    assert caught.value.code == 2
    script.main([*args, "--replace-existing"])
    registry = TenantWorkspaceRegistry(settings)
    try:
        workspace = registry.workspace_for(first_id)
        saved = workspace.labs.load_or_create()
        assert saved.version == first["version"] + 1
        with workspace.library.store.engine.connect() as connection:
            prior = connection.execute(
                select(lab_revisions.c.payload).where(
                    lab_revisions.c.version == first["version"]
                )
            ).scalar_one()
        assert prior == first
    finally:
        registry.close_all()


def test_accounts_disabled_keeps_original_local_default_and_rejects_lab_user(operator):
    script, settings, _, _, _ = operator
    settings.scione_auth_enabled = False
    with pytest.raises(SystemExit) as caught:
        script.main(["--lab-user", "lab1"])
    assert caught.value.code == 2
    assert not (settings.scione_workspace_dir / "library.sqlite3").exists()
    script.main([])
    workspace = Workspace(settings)
    try:
        assert workspace.labs.load_or_create().capabilities == (
            development_lab_baseline().capabilities
        )
    finally:
        workspace.library.store.close()
    assert not (settings.scione_workspace_dir / "tenants").exists()


def test_lab_account_missing_membership_is_rejected(operator):
    script, settings, auth, _, _ = operator
    with auth.engine.begin() as connection:
        connection.execute(update(users).where(users.c.username == "lab1").values(lab_id=None))
    with pytest.raises(SystemExit) as caught:
        script.main(["--profile", "ms-pnt-410", "--lab-user", "lab1"])
    assert caught.value.code == 2
    assert not (settings.scione_workspace_dir / "tenants").exists()


def test_merge_appends_missing_ids_preserving_user_edits_and_other_lab(operator):
    script, settings, _, first_id, second_id = operator
    baseline = ms_pnt_410_lab_baseline()
    edited = baseline.capabilities[0].model_copy(update={
        "notes": "User-edited declaration; retain this value.", "price": Decimal("999"),
        "atomic_claims": baseline.capabilities[0].atomic_claims[:1],
    })
    earlier = development_lab_baseline().capabilities[0]
    registry = TenantWorkspaceRegistry(settings)
    try:
        workspace = registry.workspace_for(first_id)
        original = workspace.labs.load_or_create()
        workspace.labs.save(original.model_copy(update={"capabilities": [edited, earlier]}))
        other_before = registry.workspace_for(second_id).labs.load_or_create().model_dump(
            mode="json"
        )
    finally:
        registry.close_all()
    script.main([
        "--profile", "ms-pnt-410", "--lab-user", "lab1", "--merge-existing",
    ])
    registry = TenantWorkspaceRegistry(settings)
    try:
        saved = registry.workspace_for(first_id).labs.load_or_create()
        assert len(saved.capabilities) == 13
        assert saved.capabilities[:2] == [edited, earlier]
        assert saved.capabilities[2:] == baseline.capabilities[1:]
        assert len({cap.id for cap in saved.capabilities}) == 13
        assert registry.workspace_for(second_id).labs.load_or_create().model_dump(
            mode="json"
        ) == other_before
    finally:
        registry.close_all()


def test_merge_and_replace_cannot_be_combined_or_write_a_workspace(operator):
    script, settings, _, _, _ = operator
    with pytest.raises(SystemExit) as caught:
        script.main([
            "--profile", "ms-pnt-410", "--lab-user", "lab1",
            "--merge-existing", "--replace-existing",
        ])
    assert caught.value.code == 2
    assert not (settings.scione_workspace_dir / "tenants").exists()
    assert not (settings.scione_workspace_dir / "library.sqlite3").exists()
