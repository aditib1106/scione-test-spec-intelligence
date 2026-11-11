"""Private code formatting never establishes standard or revision equivalence."""

import pytest

from scione.catalogs.identity_keys import private_method_key


@pytest.mark.parametrize(
    "value",
    [
        "test123",
        "TEST123",
        "test-123",
        "Test 123",
        "test_123",
        "  TEST - 123  ",
        "test\t123",
        "test\n123",
        "test–123",
        "test‑123",
        "ＴＥＳＴ－１２３",
    ],
)
def test_private_code_formatting_has_one_key(value):
    assert private_method_key(value) == "test123"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("test12-3", "test123"),
        ("test123-4", "test1234"),
        ("test123-2024", "test123-2025"),
        ("test00123", "test123"),
        ("test123a", "test123b"),
        ("test123-a", "test123a"),
        ("oem-qa123", "oemqa123"),
        ("test12 3", "test123"),
        ("test12_3", "test123"),
    ],
)
def test_identity_bearing_code_parts_are_preserved(left, right):
    assert private_method_key(left) is not None
    assert private_method_key(right) is not None
    assert private_method_key(left) != private_method_key(right)


def test_private_code_prefix_and_numeric_parts_keep_their_own_structure():
    assert private_method_key("OEM-QA - 001-2") == "oem-qa001-2"
    assert private_method_key("test12 - 3") == "test12-3"


@pytest.mark.parametrize(
    "value",
    [
        "ISO 2409",
        "iso2409",
        "ISO-2409",
        "ISO 12345-2:2024",
        "ISO12345-2",
        "ISOA123",
        "ASTM D3359-17",
        "ASTMD3359",
        "ASTMD 3359",
        "ASTM-D3359",
        "DIN EN ISO 2409",
        "DINENISO2409",
        "DINENISO 2409",
        "DINEN1234",
        "ENISO2409",
        "BSENISO2409",
        "GB/T 9286-2021",
        "GB1234",
        "SAE J400",
        "VDMA 24364",
        "IEC60068-2-2",
        "ANSI 123",
        "JIS 123",
        "MIL-STD-810",
        "AS 1234",
        "AS/NZS 1234",
    ],
)
def test_standard_references_are_not_private_formatting_keys(value):
    assert private_method_key(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "test123:2024",
        "test123 rev 2",
        "test123-revision2",
        "test123 version 3",
        "test123 issue A",
        "test123-r2",
        "test123/4",
        "test123 (modified)",
        "test123-modified",
        "test123-adapted",
        "test123 + addendum",
        "customer test 123",
        "test 123 modified",
        "Cross-cut adhesion",
        "test",
        "123",
        "",
        "tеst123",
        "test--123",
    ],
)
def test_qualified_revisions_and_prose_keep_the_existing_exact_path(value):
    assert private_method_key(value) is None
