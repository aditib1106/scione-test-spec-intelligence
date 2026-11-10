"""CJK citation layout tolerance must not rewrite source assertions or quantities."""

import pytest

from scione.source_text import citation_contains, citation_text


@pytest.mark.parametrize(
    "source,quote",
    [
        ("每批至少六\n根圆柱芯样", "每批至少六根圆柱芯样"),
        ("抗压强度 平均值 不小于 38.0 MPa", "抗压强度平均值不小于38.0 MPa"),
        ("引用 GB/T 52877-2022 方法", "引用GB/T 52877-2022方法"),
        ("光源 D65 ， 10° 观察者", "光源D65，10°观察者"),
        ("試験\r\n条件：\n温度を記録する。", "試験条件：温度を記録する。"),
        ("１枚 の 試験片 を 準備 する。", "１枚の試験片を準備する。"),
        ("条件： ISO 60038:2021 ； 结果须单列。", "条件：ISO 60038:2021；结果须单列。"),
        ("采用 D65 光源，not supported 时须记录。", "采用D65光源，not supported时须记录。"),
    ],
)
def test_cjk_line_wrapping_and_mixed_latin_text_are_citation_layout_only(source, quote):
    assert citation_contains(source, quote)


@pytest.mark.parametrize(
    "source,quote",
    [
        ("not supported", "notsupported"),
        ("not\n supported", "notsupported"),
        ("not supported", "Not supported"),
        ("12 34", "1234"),
        ("AX-17", "AX17"),
        ("AX-17", "AX–17"),
        ("温度−20 °C", "温度-20 °C"),
        ("温度−20 °C", "温度20 °C"),
        ("环境温度20 °C", "环境温度30 °C"),
        ("条件：湿热后", "条件:湿热后"),
        ("初始，暴露后", "初始暴露后"),
        ("需要测量", "不需要测量"),
        ("不支持 D65 光源", "支持 D75 光源"),
        ("ISO 2409", "ISO2409"),
        ("D 65", "D65"),
        ("中文 D65 光源", "中文 d65 光源"),
        ("中文 温度20 °C", "中文温度20°C"),
    ],
)
def test_case_punctuation_signs_digits_negation_and_latin_word_boundaries_stay_distinct(
    source, quote
):
    assert not citation_contains(source, quote)


@pytest.mark.parametrize(
    "source,quote",
    [
        ("120 mm", "20 mm"),
        ("-20 mm", "20 mm"),
        ("−20 mm", "20 mm"),
        ("1.20 mm", "20 mm"),
        ("AX-17 mm", "17 mm"),
        ("notsupported", "supported"),
        ("50 mm2", "50 mm"),
    ],
)
def test_a_citation_cannot_start_or_end_inside_a_latin_identifier_or_number(source, quote):
    assert not citation_contains(source, quote)


def test_a_later_full_occurrence_can_match_after_an_earlier_partial_number():
    assert citation_contains("120 mm; required value is 20 mm.", "20 mm")


@pytest.mark.parametrize("quote", ["", " ", "\r\n\t", "\u3000"])
def test_empty_citations_are_rejected(quote):
    assert not citation_contains("任何内容 anything 20 °C", quote)


@pytest.mark.parametrize(
    "text",
    [
        " \n中 文： D65 ， 不 支 持 。\t",
        "not\n supported\tAX-17 12\u00a034",
        "段落\n\n第二段 / table\tcell",
        "\u200b不支持\u00adD65\u200c",
        "試験片 \U00020000 条件：１枚。",
        "−20 °C ≥ 30.00 MPa; not supported",
    ],
)
def test_canonicalization_preserves_every_nonwhitespace_codepoint_and_is_idempotent(text):
    result = citation_text(text)
    assert "".join(character for character in text if not character.isspace()) == "".join(
        character for character in result if not character.isspace()
    )
    assert citation_text(result) == result


def test_only_layout_is_normalized_not_cross_cell_semantics():
    assert citation_text("condition\tvalue") == "condition value"
    assert citation_text("支持\t未提供") == "支持未提供"
    # Matching a source snippet is deliberately separate from proving that its
    # clauses or table cells describe the same parameter, object or lab promise.
    assert citation_contains("第一列\t第二列", "第一列第二列")


@pytest.mark.parametrize(
    "word",
    [
        "l’eau",
        "d’analyse",
        "j’attends",
        "t’informe",
        "c’est",
        "n’est",
        "s’écoule",
        "qu’une",
        "jusqu’au",
        "lorsqu’une",
        "puisqu’elle",
        "l’œuvre",
        "d’étalonnage",
        "s’y",
        "n’a",
    ],
)
def test_french_lowercase_elision_apostrophe_typography_is_a_narrow_equivalence(word):
    from scione.source_text import CITATION_TEXT_POLICY_VERSION, canonicalize_citation_apostrophes

    assert CITATION_TEXT_POLICY_VERSION == "v2"
    plain = word.replace("’", "'")
    assert citation_contains(f"Mesure de {word} à 23 °C.", f"Mesure de {plain} à 23 °C.")
    assert citation_contains(f"Mesure de {plain} à 23 °C.", f"Mesure de {word} à 23 °C.")
    result = canonicalize_citation_apostrophes(word)
    assert result == plain and len(result) == len(word)
    assert all(a == b or (a, b) == ("’", "'") for a, b in zip(word, result, strict=True))
    assert citation_text(result) == result


@pytest.mark.parametrize(
    "source,quote",
    [
        ("l’eau à 23 °C", "l'air à 23 °C"),
        ("l’eau à 23 °C", "l'eau à 33 °C"),
        ("l’eau à −23 °C", "l'eau à -23 °C"),
        ("l’eau 6,8", "l'eau 6.8"),
        ("AX’17", "AX'17"),
        ("L’EAU", "L'EAU"),
        ("L’eau", "L'eau"),
        ("d’ISO", "d'ISO"),
        ("l’AX17", "l'AX17"),
        ("l’eau2", "l'eau2"),
        ("l’eauX", "l'eauX"),
        ("1’020 MPa", "1'020 MPa"),
        ("d’µm", "d'µm"),
        ("d’μm", "d'μm"),
        ("d’α", "d'α"),
        ("d’mm", "d'mm"),
        ("d’kpa", "d'kpa"),
        ("l’año", "l'año"),
        ("d’größe", "d'größe"),
        ("l’e\u0301au", "l'e\u0301au"),
        ("m’informe", "m'informe"),
        ("O’Brien", "O'Brien"),
        ("aujourd’hui", "aujourd'hui"),
        ("notsupported", "not supported"),
        ("not supported", "Not supported"),
    ],
)
def test_french_typography_tolerance_does_not_change_words_values_units_codes_or_case(
    source, quote
):
    assert not citation_contains(source, quote)


def test_elision_typography_preserves_all_other_nonwhitespace_characters():
    from scione.source_text import canonicalize_citation_apostrophes

    text = "l’eau d’analyse à −23 ± 2 °C; AX’17; D65; 6,8; l’ISO; d’µm; not supported."
    result = canonicalize_citation_apostrophes(text)
    assert len(text) == len(result)
    changed = [(a, b) for a, b in zip(text, result, strict=True) if a != b]
    assert changed == [("’", "'"), ("’", "'")]
    assert result.endswith("AX’17; D65; 6,8; l’ISO; d’µm; not supported.")
