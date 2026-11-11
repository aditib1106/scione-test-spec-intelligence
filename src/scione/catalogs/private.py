"""Compose a lab's private identities with the read-only shared reference catalog."""

from __future__ import annotations

from scione.catalogs.models import CatalogError, LabProfile, ReferenceCatalog


def compose_lab_catalog(base: ReferenceCatalog, lab: LabProfile) -> ReferenceCatalog:
    """Return an isolated identity view; adding a method never asserts equivalence."""
    lab = LabProfile.model_validate(lab.model_dump())
    result = base.model_copy(deep=True)
    private_tests = {item.id: item for item in lab.custom_tests}
    private_methods = {item.id: item for item in lab.custom_methods}
    expected_tests = {
        identifier: item.model_copy(deep=True) for identifier, item in private_tests.items()
    }
    for capability in lab.capabilities:
        if capability.canonical_test_id in expected_tests:
            test = expected_tests[capability.canonical_test_id]
            test.method_ids = list(
                dict.fromkeys(
                    [
                        *test.method_ids,
                        *(
                            identifier
                            for identifier in capability.method_ids
                            if identifier in private_methods
                        ),
                    ]
                )
            )
    for method in base.methods:
        if method.id.startswith("labmethod_") and (
            method.id not in private_methods or method != private_methods[method.id]
        ):
            raise CatalogError("Private lab identities cannot replace shared catalog records.")
    for test in base.tests:
        if test.id.startswith("labtest_") and (
            test.id not in expected_tests or test != expected_tests[test.id]
        ):
            raise CatalogError("Private lab identities cannot replace shared catalog records.")
    if any(
        item.id in private_methods and not item.id.startswith("labmethod_") for item in base.methods
    ) or any(
        item.id in private_tests and not item.id.startswith("labtest_") for item in base.tests
    ):
        raise CatalogError("Private lab identities cannot replace shared catalog records.")
    result.methods = [item for item in result.methods if not item.id.startswith("labmethod_")]
    result.tests = [item for item in result.tests if not item.id.startswith("labtest_")]
    for test in result.tests:
        test.method_ids = [
            identifier for identifier in test.method_ids if identifier not in private_methods
        ]
    result.methods.extend(item.model_copy(deep=True) for item in lab.custom_methods)
    result.tests.extend(item.model_copy(deep=True) for item in lab.custom_tests)
    methods = {item.id for item in result.methods}
    tests = {item.id: item for item in result.tests}
    for test in lab.custom_tests:
        if set(test.method_ids) - methods:
            identifier = next(
                identifier for identifier in test.method_ids if identifier not in methods
            )
            raise CatalogError(f"Unknown method ID: {identifier!r}.")
    for capability in lab.capabilities:
        test = tests.get(capability.canonical_test_id)
        if test is None:
            raise CatalogError(f"Unknown canonical test ID: {capability.canonical_test_id!r}.")
        if set(capability.method_ids) - methods:
            identifier = next(
                identifier for identifier in capability.method_ids if identifier not in methods
            )
            raise CatalogError(f"Unknown method ID: {identifier!r}.")
        for identifier in capability.method_ids:
            if identifier in private_methods and identifier not in test.method_ids:
                test.method_ids.append(identifier)
    return ReferenceCatalog.model_validate(result.model_dump())
