"""Structured lab-owned capability edits; shared reference metadata stays read-only."""

from uuid import uuid4

from fastapi import HTTPException

from scione.catalogs.lab_store import LabVersionConflict
from scione.catalogs.models import Alias, CanonicalTest, TestMethod
from scione.catalogs.private import compose_lab_catalog
from scione.reports.matching import _method_resolution, _parse_method
from scione.specifications.models import RequestedMethod


def _aliases(values):
    result = []
    for text in dict.fromkeys(value.strip() for value in values):
        if not text or len(text) > 300:
            raise HTTPException(400, "Aliases must contain between 1 and 300 characters.")
        result.append(Alias(text=text, language="und"))
    return result


def _current(workspace, version):
    lab = workspace.labs.load_or_create()
    if lab.version != version:
        raise LabVersionConflict("Your lab changed. Reload before saving.")
    return lab


def _prune_private(lab):
    """Remove unused current identities; earlier versioned SQL snapshots remain saved."""
    used_tests = {entry.canonical_test_id for entry in lab.capabilities}
    used_methods = {identifier for entry in lab.capabilities for identifier in entry.method_ids}
    lab.custom_tests = [
        test.model_copy(
            update={"method_ids": [mid for mid in test.method_ids if mid in used_methods]}
        )
        for test in lab.custom_tests if test.id in used_tests
    ]
    lab.custom_methods = [method for method in lab.custom_methods if method.id in used_methods]
    return lab


def _result(workspace):
    saved = workspace.labs.load_or_create()
    return {"lab": saved, "catalog": workspace.catalog_for_lab(saved)}


def save_capability(workspace, body, *, capability_id=None):
    lab = _current(workspace, body.version).model_copy(deep=True)
    initial = next((entry for entry in lab.capabilities if entry.id == capability_id), None)
    if capability_id is not None and initial is None:
        raise HTTPException(404, "This lab capability was not found.")
    capability = body.capability.model_copy(deep=True)
    capability.id = initial.id if initial else "cap_" + uuid4().hex
    test_id = capability.canonical_test_id
    tests = {entry.id: entry for entry in lab.custom_tests}
    methods = {entry.id: entry for entry in lab.custom_methods}
    if body.custom_test is not None:
        item = body.custom_test
        if item.id:
            if initial is None or item.id != initial.canonical_test_id or item.id not in tests:
                raise HTTPException(400, "Select a private test belonging to this capability.")
            test_id = item.id
        else:
            test_id = "labtest_" + uuid4().hex
        previous = tests.get(test_id)
        tests[test_id] = CanonicalTest(
            id=test_id, name=item.name,
            description=item.description or f"Laboratory-defined test: {item.name}",
            aliases=(
                _aliases(item.aliases)
                if "aliases" in item.model_fields_set or previous is None else previous.aliases
            ),
            method_ids=previous.method_ids if previous else [],
        )
    capability.canonical_test_id = test_id
    lab.custom_tests = list(tests.values())
    for item in body.custom_methods:
        identifier = item.identifier or item.name
        if item.id:
            if initial is None or item.id not in initial.method_ids or item.id not in methods:
                raise HTTPException(400, "Select a private method belonging to this capability.")
            method_id = item.id
            resolved, issue = _method_resolution(
                RequestedMethod(code=identifier), compose_lab_catalog(workspace.catalog, lab)
            )
            if resolved is not None and resolved != method_id and issue is None:
                raise HTTPException(
                    400, "This method code already exists. Select that method instead."
                )
            if issue and "Ambiguous" in issue:
                raise HTTPException(400, "This method code is ambiguous. Use a distinct code.")
        else:
            effective = compose_lab_catalog(workspace.catalog, lab)
            method_id, issue = _method_resolution(RequestedMethod(code=identifier), effective)
            if issue and "Ambiguous" in issue:
                raise HTTPException(
                    400, "More than one method has this code. Select an existing method."
                )
            if method_id and issue is None:
                if method_id not in capability.method_ids:
                    capability.method_ids.append(method_id)
                continue
            method_id = "labmethod_" + uuid4().hex
        parsed = _parse_method(identifier)
        previous_method = methods.get(method_id)
        methods[method_id] = TestMethod(
            id=method_id, name=item.name, identifier=identifier,
            edition=parsed.revision if parsed else None,
            aliases=(
                _aliases(item.aliases)
                if "aliases" in item.model_fields_set or previous_method is None
                else previous_method.aliases
            ),
        )
        lab.custom_methods = list(methods.values())
        if method_id not in capability.method_ids:
            capability.method_ids.append(method_id)
    if test_id in tests:
        tests[test_id] = tests[test_id].model_copy(update={
            "method_ids": list(dict.fromkeys([*tests[test_id].method_ids, *capability.method_ids]))
        })
    lab.custom_tests = list(tests.values())
    lab.custom_methods = list(methods.values())
    lab.capabilities = (
        [capability if entry.id == initial.id else entry for entry in lab.capabilities]
        if initial else [*lab.capabilities, capability]
    )
    workspace.labs.save(_prune_private(lab))
    return _result(workspace)


def remove_capability(workspace, capability_id, version):
    lab = _current(workspace, version).model_copy(deep=True)
    if not any(entry.id == capability_id for entry in lab.capabilities):
        raise HTTPException(404, "This lab capability was not found.")
    lab.capabilities = [entry for entry in lab.capabilities if entry.id != capability_id]
    workspace.labs.save(_prune_private(lab))
    return _result(workspace)
