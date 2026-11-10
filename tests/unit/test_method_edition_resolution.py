"""A populated bank may hold several editions without making identity ambiguous."""

import pytest

from scione.catalogs.models import ReferenceCatalog, TestMethod
from scione.reports.matching import _method_resolution
from scione.specifications.models import RequestedMethod


def catalog(*, reverse=False, duplicate=False):
    methods = [
        TestMethod(id="method_old", identifier="ISO 12345-2:2019", name="Old", edition="2019"),
        TestMethod(id="method_new", identifier="ISO 12345-2:2024", name="New", edition="2024"),
        TestMethod(id="method_other", identifier="ISO 12345-3:2024", name="Part 3", edition="2024"),
    ]
    if duplicate:
        methods.append(methods[1].model_copy(update={"id": "method_duplicate"}))
    return ReferenceCatalog(
        id="versions", name="Synthetic version examples", version=1,
        source_note="Controlled identity examples; no method equivalences asserted.",
        tests=[], methods=list(reversed(methods)) if reverse else methods,
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("reference", [
    RequestedMethod(code="ISO 12345-2:2024"),
    RequestedMethod(code="ISO 12345-2", revision="2024"),
])
def test_explicit_edition_disambiguates_before_ambiguity(reference, reverse):
    assert _method_resolution(reference, catalog(reverse=reverse)) == ("method_new", None)


@pytest.mark.parametrize("code", ["ISO 12345-2", "ISO 12345-2:2020", "ISO 12345"])
def test_absent_unknown_edition_or_different_part_cannot_borrow_known_edition(code):
    identifier, reason = _method_resolution(RequestedMethod(code=code), catalog())
    assert identifier is None
    assert reason


def test_matching_edition_does_not_strip_unknown_qualifier():
    identifier, reason = _method_resolution(
        RequestedMethod(code="ISO 12345-2", revision="2024"), catalog()
    )
    assert identifier == "method_new" and reason is None
    identifier, reason = _method_resolution(
        RequestedMethod(code="ISO 12345-2 (modified)", revision="2024"), catalog()
    )
    assert identifier == "method_new" and "qualifier" in reason.lower()


def test_conflicting_explicit_edition_and_duplicate_edition_remain_unresolved():
    assert _method_resolution(
        RequestedMethod(code="ISO 12345-2:2024", revision="2019"), catalog()
    )[1].startswith("Conflicting")
    assert _method_resolution(
        RequestedMethod(code="ISO 12345-2:2024"), catalog(duplicate=True)
    )[0] is None
