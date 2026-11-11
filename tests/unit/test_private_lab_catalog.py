"""Private identities remain tenant-scoped and preserve historical SQL revisions."""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

from scione.catalogs import (
    CanonicalTest,
    CatalogError,
    LabCapability,
    LabProfile,
    LabStoreError,
    LabVersionConflict,
    TestMethod,
    load_reference_catalog,
    validate_lab_references,
)
from scione.catalogs.private import compose_lab_catalog
from scione.catalogs.sql_lab_store import (
    SQLLabStore,
    lab_custom_methods,
    lab_custom_tests,
    lab_revisions,
    upgrade_lab_schema,
)
from scione.library.store import LibraryStore, digest, schema_versions


def identities():
    method = TestMethod(
        id="labmethod_" + uuid4().hex,
        identifier="LAB-SOP-17",
        name="Custom peel test on finished panels",
        edition="2026-11",
    )
    test = CanonicalTest(
        id="labtest_" + uuid4().hex,
        name="Custom peel strength",
        description="Customer-specific peel strength at room temperature.",
        method_ids=[method.id],
    )
    capability = LabCapability(
        id="cap_private_peel",
        canonical_test_id=test.id,
        method_ids=[method.id],
        declared_constraints="Steel panels at 23 °C",
    )
    return test, method, capability


def private_profile():
    test, method, capability = identities()
    return LabProfile(
        id="private_lab",
        name="Private lab",
        custom_tests=[test],
        custom_methods=[method],
        capabilities=[capability],
    )


def test_empty_private_lists_preserve_existing_profile_serialization_and_fingerprint():
    legacy = {
        "id": "legacy_lab",
        "name": "Existing lab",
        "description": "",
        "version": 1,
        "capabilities": [],
    }
    lab = LabProfile.model_validate(legacy)
    assert lab.custom_tests == lab.custom_methods == []
    assert lab.model_dump(mode="json") == legacy
    assert digest(lab.model_dump(mode="json")) == digest(legacy)


def test_composition_is_detached_and_does_not_publish_private_identities_to_another_lab():
    base = load_reference_catalog()
    before = deepcopy(base.model_dump(mode="json"))
    private = private_profile()
    composed = compose_lab_catalog(base, private)
    assert composed.test_by_id(private.custom_tests[0].id).name == "Custom peel strength"
    assert composed.method_by_id(private.custom_methods[0].id).identifier == "LAB-SOP-17"
    validate_lab_references(private, base)
    assert base.model_dump(mode="json") == before
    assert compose_lab_catalog(composed, private) == composed
    validate_lab_references(private, composed)
    other = compose_lab_catalog(base, LabProfile(id="other_lab", name="Other lab"))
    with pytest.raises(CatalogError):
        other.test_by_id(private.custom_tests[0].id)
    composed.tests[0].name = "Edited outgoing view"
    assert base.model_dump(mode="json") == before


def test_private_method_can_be_explicitly_associated_with_a_shared_test_without_mutating_it():
    base = load_reference_catalog()
    shared = base.tests[0]
    _, method, capability = identities()
    capability.canonical_test_id = shared.id
    lab = LabProfile(id="lab", name="Lab", custom_methods=[method], capabilities=[capability])
    before = shared.method_ids[:]
    composed = compose_lab_catalog(base, lab)
    assert composed.test_by_id(shared.id).method_ids == [*before, method.id]
    assert shared.method_ids == before
    validate_lab_references(lab, base)


def test_private_composition_cannot_associate_arbitrary_shared_methods_or_override_shared_ids():
    base = load_reference_catalog()
    shared = next(test for test in base.tests if test.method_ids)
    other_method = next(method for method in base.methods if method.id not in shared.method_ids)
    lab = LabProfile(
        id="lab",
        name="Lab",
        capabilities=[
            LabCapability(id="cap", canonical_test_id=shared.id, method_ids=[other_method.id])
        ],
    )
    with pytest.raises(CatalogError, match="not associated"):
        validate_lab_references(lab, base)
    with pytest.raises(ValidationError, match="server-generated"):
        LabProfile(id="lab", name="Lab", custom_tests=[shared])


def test_duplicate_private_ids_and_foreign_private_references_are_rejected():
    lab = private_profile()
    with pytest.raises(ValidationError, match="must be unique"):
        LabProfile(id="lab", name="Lab", custom_tests=lab.custom_tests * 2)
    foreign = LabProfile(id="other_lab", name="Other lab", capabilities=lab.capabilities)
    with pytest.raises(CatalogError, match="Unknown canonical test"):
        validate_lab_references(foreign, load_reference_catalog())
    with pytest.raises(CatalogError, match="cannot replace"):
        compose_lab_catalog(compose_lab_catalog(load_reference_catalog(), lab), foreign)


def test_sql_round_trip_keeps_private_metadata_and_preserves_deleted_identity_history(tmp_path):
    library = LibraryStore(tmp_path)
    store = SQLLabStore(library, load_reference_catalog(), workspace_id="lab_a")
    lab = store.load_or_create()
    private = private_profile()
    lab.custom_tests, lab.custom_methods = private.custom_tests, private.custom_methods
    lab.capabilities = private.capabilities
    store.save(lab)
    loaded = store.load_or_create()
    assert loaded.custom_tests == private.custom_tests
    assert loaded.custom_methods == private.custom_methods
    assert loaded.capabilities == private.capabilities
    retained_version = loaded.version
    loaded.custom_tests[0].name = "Renamed private peel test"
    loaded.custom_methods[0].identifier = "LAB-SOP-18"
    store.save(loaded)
    loaded = store.load_or_create()
    loaded.capabilities, loaded.custom_tests, loaded.custom_methods = [], [], []
    store.save(loaded)
    assert store.load_or_create().custom_tests == []
    assert store.load_or_create().custom_methods == []
    with library.engine.connect() as connection:
        original = connection.execute(
            select(lab_revisions.c.payload).where(
                lab_revisions.c.workspace_id == "lab_a", lab_revisions.c.version == retained_version
            )
        ).scalar_one()
        assert original["custom_tests"][0]["name"] == "Custom peel strength"
        assert original["custom_methods"][0]["identifier"] == "LAB-SOP-17"
        assert (
            connection.execute(
                select(lab_custom_tests.c.name).where(
                    lab_custom_tests.c.workspace_id == "lab_a",
                    lab_custom_tests.c.lab_version == retained_version,
                )
            ).scalar_one()
            == "Custom peel strength"
        )
        assert (
            connection.execute(
                select(lab_custom_methods.c.identifier).where(
                    lab_custom_methods.c.workspace_id == "lab_a",
                    lab_custom_methods.c.lab_version == retained_version,
                )
            ).scalar_one()
            == "LAB-SOP-17"
        )
    library.close()
    reloaded = SQLLabStore(LibraryStore(tmp_path), load_reference_catalog(), workspace_id="lab_a")
    assert reloaded.load_or_create().capabilities == []


def test_sql_custom_identity_updates_are_isolated_and_obey_profile_compare_and_swap(tmp_path):
    library = LibraryStore(tmp_path)
    first = SQLLabStore(library, load_reference_catalog(), workspace_id="lab_a")
    second = SQLLabStore(library, load_reference_catalog(), workspace_id="lab_b")
    lab, other = first.load_or_create(), second.load_or_create()
    stale = lab.model_copy(deep=True)
    private = private_profile()
    lab.custom_tests, lab.custom_methods, lab.capabilities = (
        private.custom_tests,
        private.custom_methods,
        private.capabilities,
    )
    first.save(lab)
    stale.custom_tests = private.custom_tests
    stale.custom_methods = private.custom_methods
    stale.capabilities = private.capabilities
    with pytest.raises(LabVersionConflict):
        first.save(stale)
    assert second.load_or_create() == other
    assert second.load_or_create().custom_tests == []
    assert first.load_or_create().custom_tests == private.custom_tests


def test_invalid_private_references_do_not_partially_replace_sql_profile(tmp_path):
    store = SQLLabStore(LibraryStore(tmp_path), load_reference_catalog())
    original = store.load_or_create()
    changed = original.model_copy(deep=True)
    changed.capabilities = private_profile().capabilities
    with pytest.raises(LabStoreError):
        store.save(changed)
    assert store.load_or_create() == original


def test_v1_lab_storage_upgrades_additively_without_changing_old_revision_payloads(tmp_path):
    library = LibraryStore(tmp_path)
    store = SQLLabStore(library, load_reference_catalog())
    original = store.load_or_create()
    with library.engine.begin() as connection:
        before = connection.execute(select(lab_revisions.c.payload)).scalar_one()
        connection.execute(
            update(schema_versions)
            .where(schema_versions.c.component == "lab_storage")
            .values(version=1)
        )
    lab_custom_tests.drop(library.engine)
    lab_custom_methods.drop(library.engine)
    upgrade_lab_schema(library)
    assert store.load_or_create() == original
    with library.engine.connect() as connection:
        assert connection.execute(select(lab_revisions.c.payload)).scalar_one() == before
        assert (
            connection.execute(
                select(schema_versions.c.version).where(
                    schema_versions.c.component == "lab_storage"
                )
            ).scalar_one()
            == 2
        )
