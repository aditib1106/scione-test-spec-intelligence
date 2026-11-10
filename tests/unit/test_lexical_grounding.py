"""Multilingual lexical evidence cannot borrow a number, unit, locale or sign."""

from decimal import Decimal

import pytest

from scione.structured.lexical_grounding import (
    grounded_source_numbers,
    source_operator_hints,
    source_quantities,
)


@pytest.mark.parametrize(
    "text,unit,expected",
    [
        ("每批至少六根", "根", {"6"}),
        ("静置七天", "days", {"7"}),
        ("连续十个循环", "cycles", {"10"}),
        ("每板五个固定位置", "locations", {"5"}),
        ("每个配方提供两批样板", "batches", {"2"}),
        ("twenty specimens", "specimens", {"20"}),
        ("mindestens sechs Bleche", "panels", {"6"}),
        ("drei Proben", "specimens", {"3"}),
        ("tres probetas", "specimens", {"3"}),
        ("deux éprouvettes", "specimens", {"2"}),
    ],
)
def test_small_source_number_words_require_an_attached_unit_or_classifier(text, unit, expected):
    assert grounded_source_numbers(text, unit=unit) == {Decimal(value) for value in expected}


def test_distinct_chinese_classifiers_cannot_supply_each_others_numbers():
    text = "从三块有代表性的预制墙板中各取两根圆柱芯样"
    assert grounded_source_numbers(text, unit="panels") == {Decimal(3)}
    assert grounded_source_numbers(text, unit="根") == {Decimal(2)}
    assert Decimal(3) not in grounded_source_numbers(text, unit="specimens")
    for row in source_quantities(text):
        assert text[slice(*row.number_span)] == row.raw_number
        assert text[slice(*row.unit_span)] == row.raw_unit


@pytest.mark.parametrize(
    "text",
    [
        "Step two",
        "Step two cycles",
        "stage three panels",
        "twenty-one panels",
        "one hundred specimens",
        "三十六块",
        "九十天",
        "D65",
        "AX17",
        "D65 K",
        "AX17 mm",
        "ASTM D1012-17",
        "ASTM D1012-17 mm",
        "ISO 60038 mm",
        "ISO 60038:2021 mm",
        "1×10^-3 mm",
        "1*10^3 mm",
        "1e--3 mm",
        "1.2.3 mm",
        "1.0.e3 mm",
        "1-2-3 mm",
        "minus five °C",
        "negative two °C",
        "负二摄氏度",
    ],
)
def test_codes_unsupported_words_and_arithmetic_never_expose_a_numeric_suffix(text):
    assert grounded_source_numbers(text) == set()


@pytest.mark.parametrize(
    "text,expected,language",
    [
        ("Der Mittelwert darf 1,5 mm nicht überschreiten.", "1.5", "de"),
        ("La longitud es de 1,5 mm.", "1.5", "es"),
        ("L'épaisseur doit être de 1,5 mm.", "1.5", "fr"),
        ("La resistencia será de 1.020 MPa.", "1020", "es"),
        ("Der Mittelwert ist 1.020 MPa.", "1020", "de"),
        ("The maximum strength is 1,020 MPa.", "1020", "en"),
        ("0.125 mm", "0.125", None),
        ("1.2e-3 mm", "0.0012", None),
        ("−20 °C", "-20", None),
    ],
)
def test_number_locale_requires_source_language_proof(text, expected, language):
    rows = source_quantities(text)
    assert len(rows) == 1
    assert rows[0].value == Decimal(expected)
    assert rows[0].language == language
    assert rows[0].ambiguity is None


@pytest.mark.parametrize("text", ["1.020 MPa", "1,020 MPa", "1,5 mm", "1.020,5 MPa"])
def test_ambiguous_punctuation_is_not_certified_as_either_convenient_value(text):
    rows = source_quantities(text)
    assert rows and rows[0].ambiguity is not None
    assert rows[0].value is None
    assert grounded_source_numbers(text) == set()


def test_language_context_can_disambiguate_a_token_but_cannot_supply_numbers_or_operators():
    text = "1.020 MPa"
    context = "La resistencia de la probeta deberá mostrar 860 ≤ Rm ≤ 1.020 MPa."
    assert grounded_source_numbers(text, unit="MPa", context=context) == {Decimal(1020)}
    assert source_operator_hints(text, unit="MPa", context=context) == set()
    assert grounded_source_numbers("dato no declarado", unit="MPa", context=context) == set()
    assert grounded_source_numbers(text, context="The maximum strength is 1.020 MPa") == {
        Decimal("1.020")
    }
    mixed = "La resistencia de la probeta: the maximum strength."
    assert grounded_source_numbers(text, context=mixed) == set()


def test_two_sided_variable_inequality_attaches_both_source_values_to_its_stated_unit():
    text = "Cada probeta válida deberá mostrar 860 ≤ Rm ≤ 1.020 MPa"
    rows = source_quantities(text)
    assert [row.value for row in rows] == [Decimal(860), Decimal(1020)]
    assert {row.unit for row in rows} == {"MPa"}
    assert {row.role for row in rows} == {"range_endpoint"}
    assert {row.expression_span for row in rows} == {(text.index("860"), len(text))}
    assert source_operator_hints(text, unit="MPa") == {"range"}
    assert grounded_source_numbers(text, unit="mm") == set()


def test_temperature_tolerance_keeps_separate_roles_and_source_spans():
    text = "试验前在 20 ± 2 °C 环境下密封平衡 24 h"
    rows = source_quantities(text)
    assert [(row.value, row.unit, row.role) for row in rows] == [
        (Decimal(20), "°C", "value"),
        (Decimal(2), "°C", "tolerance"),
        (Decimal(24), "h", "value"),
    ]
    assert grounded_source_numbers(text, unit="°C") == {Decimal(20), Decimal(2)}
    assert grounded_source_numbers(text, unit="h") == {Decimal(24)}


@pytest.mark.parametrize(
    "text,unit,expected",
    [
        ("每批至少六根", "根", ">="),
        ("强度平均值不小于 38.0 MPa", "MPa", ">="),
        ("任何单根芯样不得低于 33.0 MPa", "MPa", ">="),
        ("厚度不大于 5 mm", "mm", "<="),
        ("ΔE*ab 不大于 0.8", "ΔE*ab", "<="),
        ("Der Mittelwert darf 1,5 mm nicht überschreiten.", "mm", "<="),
        ("mindestens 5 mm", "mm", ">="),
        ("nicht über 5 mm", "mm", "<="),
        ("not greater than 5 mm", "mm", "<="),
        ("no menos de 5 mm", "mm", ">="),
        ("pas supérieur à 5 mm", "mm", "<="),
    ],
)
def test_explicit_inequality_words_are_not_reversed(text, unit, expected):
    assert source_operator_hints(text, unit=unit) == {expected}


def test_operators_are_scoped_to_their_own_number_and_unit():
    text = "≥ 5 h then ≤ 20 °C"
    assert source_operator_hints(text, unit="h") == {">="}
    assert source_operator_hints(text, unit="°C") == {"<="}
    assert source_operator_hints("at least 5 h; temperature 20 °C", unit="°C") == set()
    assert source_operator_hints("不小于5 MPa；厚度20 mm", unit="mm") == set()
    assert source_operator_hints("not not less than 5 mm", unit="mm") == set()
    assert source_operator_hints("不得不小于5 mm", unit="mm") == set()


def test_si_case_remains_identity_bearing_and_values_do_not_cross_units():
    text = "5 mPa and 8 MPa; 20 °C and 24 h"
    assert grounded_source_numbers(text, unit="mPa") == {Decimal(5)}
    assert grounded_source_numbers(text, unit="MPa") == {Decimal(8)}
    assert grounded_source_numbers(text, unit="Pa") == set()
    assert grounded_source_numbers(text, unit="°C") == {Decimal(20)}
    assert grounded_source_numbers(text, unit="h") == {Decimal(24)}


@pytest.mark.parametrize("text", ["100 mm²", "100 mm2", "100 mm^2", "100 mm/s", "100 N-m"])
def test_compound_or_power_units_do_not_certify_their_shorter_component(text):
    assert grounded_source_numbers(text) == set()


def test_localized_range_separator_is_used_only_with_its_own_source_language():
    text = "Dureza de 29 a 34 HRC"
    assert grounded_source_numbers(text, unit="HRC") == {Decimal(29), Decimal(34)}
    assert source_operator_hints(text, unit="HRC") == {"range"}
    assert grounded_source_numbers("29 a 34 HRC") == set()


def test_the_original_strict_source_number_grammar_is_unchanged():
    from scione.numeric_literals import source_numbers

    assert source_numbers("D65 AX17; 1.2e-3 mm") == {Decimal("0.0012")}
    assert source_numbers("1.2.3 mm") == set()


@pytest.mark.parametrize(
    "text,unit,expected",
    [
        ("nine marked points", "locations", {9}),
        ("six equally spaced stations", "locations", {6}),
        ("two production paint batches", "batches", {2}),
        ("six 100 × 150 mm panels", "panels", {6}),
        ("720 cumulative hours", "h", {720}),
        ("240, 480 and 720 hours", "h", {240, 480, 720}),
    ],
)
def test_source_owned_count_modifiers_and_shared_unit_lists(text, unit, expected):
    assert grounded_source_numbers(text, unit=unit) == {Decimal(v) for v in expected}
    for quantity in source_quantities(text):
        assert text[slice(*quantity.number_span)] == quantity.raw_number
        assert text[slice(*quantity.unit_span)] == quantity.raw_unit


def test_numeric_lists_do_not_replace_ranges_or_borrow_a_different_unit():
    assert source_operator_hints("between 42 and 55 µm", unit="µm") == {"range"}
    assert source_operator_hints("42 and 55 µm", unit="µm") == {"one_of"}
    assert grounded_source_numbers("240 °C and 480 hours", unit="h") == {Decimal(480)}
    assert source_operator_hints("at least 240 and 480 hours", unit="h") == set()
    assert grounded_source_numbers("Step two production batches") == set()


def test_accented_words_do_not_expose_a_short_si_unit_prefix():
    text = "Une série n’est acceptable que si tous les résultats sont conformes."
    assert source_quantities(text) == ()
    assert source_quantities("Une seule lecture est nécessaire.") == ()
