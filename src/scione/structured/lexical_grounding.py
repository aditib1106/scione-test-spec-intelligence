"""Conservative, source-local lexical evidence for multilingual quantities.

This module proposes exact lexical candidates. It does not identify a parameter,
infer a method, convert units, compute a quantity or approve a lab capability.
Language evidence comes from the quoted source/context, never a model label.
The original strict numeric-literal grammar remains unchanged.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Literal

from scione.numeric_literals import decimal_literal
from scione.parameters.units import canonical_unit_name, resolve_unit


@dataclass(frozen=True)
class SourceQuantity:
    value: Decimal | None
    raw_number: str
    number_span: tuple[int, int]
    raw_unit: str
    unit: str
    unit_span: tuple[int, int]
    expression_span: tuple[int, int]
    language: str | None
    role: Literal["value", "range_endpoint", "tolerance"] = "value"
    operator: str | None = None
    ambiguity: str | None = None
    alternatives: tuple[Decimal, ...] = ()
    operator_ambiguity: str | None = None


_NUMERAL_CHARS = "零〇一二两三四五六七八九十百千万亿点"
_ZH_DIGITS = dict(
    zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9], strict=True)
)
_WORD_VALUES = {
    "en": dict(
        zip(
            "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
            "fifteen sixteen seventeen eighteen nineteen twenty".split(),
            range(21),
            strict=True,
        )
    ),
    "de": dict(
        zip(
            "null eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf dreizehn vierzehn "
            "fünfzehn sechzehn siebzehn achtzehn neunzehn zwanzig".split(),
            range(21),
            strict=True,
        )
    )
    | dict.fromkeys(["ein", "eine", "einen", "einem", "einer"], 1),
    "es": dict(
        zip(
            "cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce "
            "quince dieciséis diecisiete dieciocho diecinueve veinte".split(),
            range(21),
            strict=True,
        )
    )
    | {"un": 1, "una": 1, "dieciseis": 16},
    "fr": dict(
        zip(
            "zéro un deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze "
            "quinze seize dix-sept dix-huit dix-neuf vingt".split(),
            range(21),
            strict=True,
        )
    )
    | {"zero": 0, "une": 1},
}

_LANGUAGE_MARKERS = {
    "de": r"\b(?:Prüf\w*|Mittelwert|Messwert\w*|Unterwanderung|Beanspruchung|Bleche[n]?|"
    r"mindestens|höchstens|überschreiten|Zyklen|Proben|Temperaturwechsel\w*)\b",
    "es": r"\b(?:resistencia|tracción|probeta[s]?|colada[s]?|hornada[s]?|longitud|dureza|"
    r"ensayo|deberá|inferior|superar|lectura[s]?)\b|\bal menos\b",
    "fr": r"\b(?:température|éprouvette[s]?|échantillon[s]?|inférieur[e]?|supérieur[e]?|"
    r"longueur|épaisseur|humidité|dépasser|doit)\b|\bau moins\b|\bau plus\b",
    "en": r"\b(?:specimen[s]?|temperature|length|shall|must|maximum|minimum|"
    r"panels?|thickness)\b|\bat least\b|\bno more than\b",
}

# Unit spelling is a source token, not a fuzzy string. SI symbols remain case
# sensitive. Full language names and explicit count classifiers have narrow aliases.
_EXTRA_UNITS = {
    "根": "specimens",
    "块": "panels",
    "批": "batches",
    "组": "groups",
    "个": "items",
    "位置": "locations",
    "测点": "locations",
    "个位置": "locations",
    "个不同位置": "locations",
    "个固定位置": "locations",
    "个测点": "locations",
    "个试样": "specimens",
    "个样品": "specimens",
    "个循环": "cycles",
    "次循环": "cycles",
    "个连续循环": "cycles",
    "个完整循环": "cycles",
    "次": "cycles",
    "specimen": "specimens",
    "specimens": "specimens",
    "core": "specimens",
    "cores": "specimens",
    "panel": "panels",
    "panels": "panels",
    "batch": "batches",
    "batches": "batches",
    "group": "groups",
    "groups": "groups",
    "location": "locations",
    "locations": "locations",
    "position": "locations",
    "positions": "locations",
    "readings": "locations",
    "Probe": "specimens",
    "Proben": "specimens",
    "Blech": "panels",
    "Bleche": "panels",
    "Blechen": "panels",
    "Messstellen": "locations",
    "Messorten": "locations",
    "Positionen": "locations",
    "Zyklen": "cycles",
    "Stunden": "h",
    "Minuten": "min",
    "Tage": "days",
    "Tagen": "days",
    "Sekunden": "s",
    "probeta": "specimens",
    "probetas": "specimens",
    "puntos": "locations",
    "ciclos": "cycles",
    "horas": "h",
    "minutos": "min",
    "segundos": "s",
    "días": "days",
    "éprouvette": "specimens",
    "éprouvettes": "specimens",
    "échantillon": "specimens",
    "échantillons": "specimens",
    "heures": "h",
    "jours": "days",
    "cycles": "cycles",
    "degrees C": "°C",
    "degree C": "°C",
    "mins": "min",
}
_SI_UNITS = (
    "% RH",
    "%RH",
    "RH%",
    "ΔE*ab",
    "ΔEab",
    "Δ°C",
    "Δ°F",
    "ΔK",
    "°C",
    "° C",
    "°F",
    "° F",
    "K",
    "°",
    "µm",
    "μm",
    "um",
    "nm",
    "mm",
    "cm",
    "m",
    "in",
    "MPa",
    "mPa",
    "kPa",
    "Pa",
    "bar",
    "kN",
    "N",
    "kg",
    "mg",
    "g",
    "GU",
    "HRC",
    "HV",
    "J",
    "h",
    "hr",
    "hrs",
    "sec",
    "s",
    "min",
    "%",
    "天",
    "小时",
    "分钟",
    "秒",
    "毫米",
    "厘米",
    "微米",
    "米",
    "摄氏度",
    "华氏度",
    "度",
    "点",
)
_WORD_UNITS = (
    "seconds",
    "second",
    "minutes",
    "minute",
    "hours",
    "hour",
    "days",
    "day",
    "cycles",
    "cycle",
    "degrees",
    "degree",
    "percent",
    "Celsius",
    "Fahrenheit",
    "degrees C",
    "degree C",
    "mins",
    "millimeters",
    "millimetres",
)


def _unit_pattern() -> str:
    literal = [re.escape(value).replace(r"\ ", r"\s*") for value in _SI_UNITS]
    words = [
        "(?i:" + re.escape(value).replace(r"\ ", r"\s+") + ")"
        for value in (*_WORD_UNITS, *_EXTRA_UNITS)
    ]
    return (
        "(?:"
        + "|".join(sorted([*literal, *words], key=len, reverse=True))
        + r")(?![A-Za-z0-9_µμ²³^/·⋅*]|-[A-Za-z0-9])"
    )


_UNIT = _unit_pattern()
_DIGIT = r"[+\-−＋－]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][+\-]?\d+)?"
_WORDS = "|".join(
    sorted({word for values in _WORD_VALUES.values() for word in values}, key=len, reverse=True)
)
_WORD = rf"(?i:(?:{_WORDS}))(?![A-Za-zÀ-ž-])"
_ZH_WORD = rf"[{_NUMERAL_CHARS}]+"
_NUMBER = rf"(?:{_DIGIT}|{_WORD}|{_ZH_WORD})"
_START = rf"(?<![A-Za-z0-9_.^À-ž{_NUMERAL_CHARS}])"
_SEPARATOR = r"(?:[-–—]|\bto\b|\bbis\b|\ba\b|\bà\b|至|到)"
_ATTACHED = re.compile(
    rf"{_START}(?P<first>{_NUMBER})(?:\s*(?:(?P<pm>±)\s*(?P<tolerance>{_NUMBER})|"
    rf"(?P<separator>{_SEPARATOR})\s*(?P<second>{_NUMBER})))?\s*(?P<unit>{_UNIT})"
)

# A count classifier may form an adjective ("five-position map").  A
# hyphenated number word such as twenty-five and code suffixes remain excluded
# by the complete classifier match and the ordinary source-prefix guard.
_COUNT_WORD = rf"(?i:(?:{_WORDS}))(?![A-Za-zÀ-ž])"
_COMPOUND_COUNT = re.compile(
    rf"{_START}(?P<first>{_DIGIT}|{_COUNT_WORD})-"
    r"(?P<unit>(?i:positions?|locations?|cycles?|panels?|specimens?))"
    r"(?![A-Za-z0-9_-])"
)
_MARKED_LOCATIONS = re.compile(
    rf"{_START}(?P<first>{_NUMBER})(?:\s*(?:(?P<pm>±)\s*(?P<tolerance>{_NUMBER})|"
    rf"(?P<separator>{_SEPARATOR})\s*(?P<second>{_NUMBER})))?\s+"
    r"(?i:marked)\s+(?P<unit>(?i:positions?|locations?))(?![A-Za-z0-9_-])"
)
_PANEL_COUNT = re.compile(
    rf"{_START}(?P<first>{_NUMBER})\s+flat\s+"
    rf"{_DIGIT}\s*[×x]\s*{_DIGIT}\s*(?:mm|cm|in)\s+"
    r"(?:[A-Za-z][A-Za-z-]*\s+){0,3}(?P<unit>panels?|witnesses)(?![A-Za-z0-9_])",
    re.I,
)

# Explicit range phrases bind an omitted lower endpoint unit only to the
# same grammatical expression. A separate stated unit remains identity-bearing.
_VERBAL_RANGE = re.compile(
    rf"\b(?P<intro>(?i:between|from|entre|zwischen))\s+"
    rf"(?P<first>{_NUMBER})(?:\s*(?P<first_unit>{_UNIT}))?\s+"
    rf"(?P<link>(?i:and|to|et|y|und))\s+(?P<second>{_NUMBER})\s*"
    rf"(?P<unit>{_UNIT})"
)


_CHAIN = re.compile(
    rf"{_START}(?P<first>{_DIGIT})\s*(?P<left><=|>=|≤|≥|<|>)\s*"
    rf"[A-Za-z][A-Za-z0-9_*]{{0,12}}\s*(?P<right><=|>=|≤|≥|<|>)\s*"
    rf"(?P<second>{_DIGIT})\s*(?P<unit>{_UNIT})"
)
_SCALE_FIRST = re.compile(
    rf"(?P<unit>Δ\s*E(?:\*?ab)?|pH)\s*"
    rf"(?P<op>不大于|不得大于|不超过|不得超过|不小于|不低于|不得低于|至少|至多|"
    rf"<=|>=|≤|≥|<|>)\s*(?P<first>{_DIGIT})"
)
_ORDINAL_CLASS_FIRST = re.compile(
    rf"(?<![\w])(?P<unit>(?i:(?:ISO\s+)?class(?:es)?))\s*"
    rf"(?P<op><=|>=|≤|≥|<|>)?\s*(?P<first>{_DIGIT})"
    rf"(?:\s*(?P<separator>(?i:or|and|to)|/|[-–—])\s*(?P<second>{_DIGIT}))?"
    rf"(?![A-Za-z0-9_.]|\s*(?:(?i:or|and|to)|/|[-–—])\s*{_DIGIT})"
)
_UNSUPPORTED_ARITHMETIC = re.compile(
    rf"{_START}(?P<first>[+\-−]?\d[\d.eE+\-−×*^,]*[×*^][\d.eE+\-−×*^,]*)"
    rf"\s*(?P<unit>{_UNIT})"
)


def _language(text: str, context: str | None) -> str | None:
    source = unicodedata.normalize("NFKC", text + " " + (context or ""))
    found = {key for key, pattern in _LANGUAGE_MARKERS.items() if re.search(pattern, source, re.I)}
    # A Chinese quantity is locally identifiable even inside a bilingual document.
    if re.search(r"[\u3400-\u9fff]", text):
        return "zh"
    return next(iter(found)) if len(found) == 1 else None


def _unit_key(unit: str) -> str:
    compact = " ".join(unicodedata.normalize("NFKC", unit).split())
    result = canonical_unit_name(compact)
    if result != compact:
        return result
    explicit = {key.casefold(): value for key, value in _EXTRA_UNITS.items()}
    return explicit.get(compact.casefold(), result)


def source_unit_name(unit: str) -> str:
    """Canonical spelling for curated source units and distinct count classifiers."""
    return _unit_key(unit)


def _word_value(token: str, language: str | None) -> Decimal | None:
    if re.fullmatch(_ZH_WORD, token):
        if token in _ZH_DIGITS:
            return Decimal(_ZH_DIGITS[token])
        if token == "十":
            return Decimal(10)
        if len(token) == 2 and token.startswith("十") and token[1] in _ZH_DIGITS:
            return Decimal(10 + _ZH_DIGITS[token[1]])
        if token == "二十":
            return Decimal(20)
        return None
    keys = _WORD_VALUES.get(language, {})
    if token.casefold() in keys:
        return Decimal(keys[token.casefold()])
    # A source-stated number word is itself lexical evidence. Shared spellings
    # are accepted only when all supported dictionaries give the same value.
    if token.casefold() in _WORD_VALUES["en"] and language in {None, "en"}:
        return Decimal(_WORD_VALUES["en"][token.casefold()])
    alternatives = {
        values[token.casefold()] for values in _WORD_VALUES.values() if token.casefold() in values
    }
    return Decimal(next(iter(alternatives))) if len(alternatives) == 1 else None


def _number(
    token: str, language: str | None
) -> tuple[Decimal | None, str | None, tuple[Decimal, ...]]:
    raw = unicodedata.normalize("NFKC", token).replace("−", "-")
    if re.search(r"[A-Za-zÀ-ž\u3400-\u9fff]", raw) and not re.fullmatch(_DIGIT, raw):
        value = _word_value(raw, language)
        return value, None if value is not None else "unsupported_or_ambiguous_number_word", ()
    european = language in {"de", "es", "fr"}
    try:
        if "," in raw and "." in raw:
            if language in {"de", "es"} and re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+,\d+", raw):
                return decimal_literal(raw.replace(".", "").replace(",", ".")), None, ()
            if language == "en" and re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+\.\d+", raw):
                return decimal_literal(raw.replace(",", "")), None, ()
            return None, "mixed_or_unproved_number_locale", ()
        if "," in raw:
            if european and re.fullmatch(r"[+-]?\d+,\d+", raw):
                return decimal_literal(raw.replace(",", ".")), None, ()
            if language == "en" and re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+", raw):
                return decimal_literal(raw.replace(",", "")), None, ()
            alternatives = ()
            if re.fullmatch(r"[+-]?\d{1,3},\d{3}", raw):
                alternatives = (
                    decimal_literal(raw.replace(",", ".")),
                    decimal_literal(raw.replace(",", "")),
                )
            return None, "unproved_or_invalid_comma_locale", alternatives
        if re.fullmatch(r"[+-]?[1-9]\d{0,2}(?:\.\d{3})+", raw):
            if language in {"de", "es"}:
                return decimal_literal(raw.replace(".", "")), None, ()
            if language is None:
                alternatives = (decimal_literal(raw), decimal_literal(raw.replace(".", "")))
                return None, "ambiguous_decimal_or_thousands", alternatives
        return decimal_literal(raw), None, ()
    except (InvalidOperation, ValueError):
        return None, "unsupported_numeric_notation", ()


def _blocked_prefix(text: str, start: int) -> bool:
    prefix = text[:start]
    if re.search(r"[A-Za-z0-9_.^×*+\-−]$", prefix):
        return True
    if re.search(r"\b(?:step|stage|phase|section|item|figure|table)\s+$", prefix, re.I):
        return True
    return bool(
        re.search(
            r"\b(?:ISO|ASTM|DIN(?:\s+EN)?(?:\s+ISO)?|GB\s*/\s*T|SAE|VDA)\s*[A-Z]?\s*$"
            r"|\b(?:ISO|ASTM|DIN|GB\s*/\s*T|SAE|VDA)\s*[A-Z]?\s*\d[\d:\-]*:$",
            prefix,
            re.I,
        )
    )


_PREFIX_OPERATORS = [
    (
        ">=",
        r"不小于|不少于|不低于|不得低于|至少|最低(?:值)?(?:为)?|最小值(?:为)?|"
        r"(?i:minimum(?:\s+value)?(?:\s+(?:of|is))?)|"
        r"(?i:at least|not less than|no less than|mindestens|nicht weniger als|"
        r"nicht unter|al menos|no inferior a|no menos de|au moins|pas inférieur à)",
    ),
    (
        "<=",
        r"不大于|不高于|不得大于|不得超过|不超过|至多|最多|最高(?:值)?(?:为)?|最大值(?:为)?|"
        r"(?i:maximum(?:\s+value)?(?:\s+(?:of|is))?)|"
        r"(?i:at most|no more than|not more than|not greater than|no greater than|"
        r"höchstens|nicht mehr als|nicht über|a lo sumo|como máximo|no más de|"
        r"au plus|pas supérieur à)",
    ),
    (">", r"大于|超过|高于|(?i:greater than|more than|über|más de|supérieur à)"),
    ("<", r"小于|低于|少于|(?i:less than|unter|menos de|inférieur à)"),
]


def _operator(prefix: str, suffix: str) -> tuple[str | None, str | None]:
    def negated_before(start: int) -> bool:
        return bool(
            re.search(
                r"(?:不得|不可|并非|不|非|(?i:\bnot|\bno|\bnicht|\bpas|\bcannot(?: be)?))\s*$",
                prefix[:start],
            )
        )

    if match := re.search(r"(<=|>=|≤|≥|<|>)\s*$", prefix):
        if negated_before(match.start()):
            return None, "nested_or_negated_operator"
        return {"≤": "<=", "≥": ">="}.get(match[1], match[1]), None
    for result, pattern in _PREFIX_OPERATORS:
        if match := re.search(r"(?:" + pattern + r")\s*$", prefix):
            if negated_before(match.start()):
                return None, "nested_or_negated_operator"
            return result, None
    if re.match(r"\s*(?:nicht überschreiten|nicht übersteigen)\b", suffix, re.I):
        return "<=", None
    return None, None


def unit_token_is_standalone(text: str, end: int) -> bool:
    """A short unit cannot stand in for a power, quotient or compound unit."""
    suffix = text[end:]
    if re.match(r"[A-Za-z0-9_µμ²³]", suffix):
        return False
    if re.match(r"\s*(?:[/⁄∕^·⋅*]|-\s*[A-Za-z])", suffix):
        return False
    exponent = re.match(r"\s+([A-Za-zµμ°]+)\s*\^?\s*[-−]\s*\d", suffix)
    if exponent and exponent[1].casefold() in {"at", "between", "from", "to", "below", "above"}:
        # A prose connector followed by a negative setting is not an exponent:
        # "five cycles between -20 °C ..." still contains five cycles.
        return True
    return exponent is None


@lru_cache(maxsize=64)
def _additional_patterns(unit: str) -> tuple:
    # The caller's unknown unit is accepted only as an exact source token. It
    # cannot make an absent unit, a shorter unit or a scientific synonym true.
    literal = "(?:" + re.escape(unit).replace(r"\ ", r"\s+") + ")"
    literal += r"(?![A-Za-z0-9_µμ²³])"
    return tuple(
        (re.compile(pattern.pattern.replace(_UNIT, literal)), mode)
        for pattern, mode in [
            (_UNSUPPORTED_ARITHMETIC, "unsupported"),
            (_VERBAL_RANGE, "verbal_range"),
            (_CHAIN, "chain"),
            (_ATTACHED, "attached"),
        ]
    )


def source_quantities(
    text: str, *, context: str | None = None, additional_unit: str | None = None
) -> tuple[SourceQuantity, ...]:
    """Lex directly attached quantities, retaining uncertainty and source spans.

    ``context`` may be a larger verbatim source quotation for language proof. Its
    numbers and operators never become evidence for quantities outside ``text``.
    """
    language = _language(text, context)
    candidates = []
    occupied: list[tuple[int, int]] = []
    patterns = list(_additional_patterns(additional_unit)) if additional_unit else []
    patterns += [
        (_PANEL_COUNT, "panel_count"),
        (_UNSUPPORTED_ARITHMETIC, "unsupported"),
        (_VERBAL_RANGE, "verbal_range"),
        (_CHAIN, "chain"),
        (_COMPOUND_COUNT, "count_classifier"),
        (_MARKED_LOCATIONS, "attached"),
        (_ATTACHED, "attached"),
        (_SCALE_FIRST, "scale"),
        (_ORDINAL_CLASS_FIRST, "ordinal_class"),
    ]
    for pattern, mode in patterns:
        for match in pattern.finditer(text):
            if any(start < match.end() and end > match.start() for start, end in occupied):
                continue
            if _blocked_prefix(text, match.start("first")):
                continue
            if mode != "ordinal_class" and not unit_token_is_standalone(text, match.end("unit")):
                continue
            if mode == "chain" and match["left"] != match["right"]:
                continue
            if mode == "panel_count":
                # This classifier crosses a dimension phrase; it owns only
                # its count and noun. The intervening geometry must still be
                # lexed and retained independently, never consumed as count.
                occupied.extend([match.span("first"), match.span("unit")])
            else:
                occupied.append(match.span())
            unit = "panels" if mode == "panel_count" else _unit_key(match["unit"])
            fields = ["first"]
            if mode in {"chain", "verbal_range"} or (
                mode in {"attached", "ordinal_class"} and match["second"] is not None
            ):
                fields.append("second")
            if mode == "attached" and match["tolerance"] is not None:
                fields.append("tolerance")
            range_ambiguity = None
            if mode == "verbal_range":
                intro, link = match["intro"].casefold(), match["link"].casefold()
                expected = {
                    ("between", "and"): None,
                    ("from", "to"): None,
                    ("entre", "et"): "fr",
                    ("entre", "y"): "es",
                    ("zwischen", "und"): "de",
                }
                if (intro, link) not in expected:
                    range_ambiguity = "unsupported_range_phrase"
                elif expected[intro, link] and language != expected[intro, link]:
                    range_ambiguity = "unproved_range_language"
                elif match["first_unit"] and _unit_key(match["first_unit"]) != unit:
                    range_ambiguity = "mixed_units_in_range"
                elif re.search(
                    r"(?i:\bnot(?:\s+be)?|\bno|\bnicht|\bpas)\s*$",
                    text[: match.start()],
                ):
                    range_ambiguity = "negated_verbal_range"
                elif any(
                    _number(match[field], language)[0] is None
                    and _number(match[field], language)[1] != "ambiguous_decimal_or_thousands"
                    for field in fields
                ):
                    range_ambiguity = "unproved_range_endpoint"
            for field in fields:
                value, ambiguity, alternatives = _number(match[field], language)
                if range_ambiguity:
                    value, ambiguity, alternatives = None, range_ambiguity, ()
                negative_word = re.search(
                    r"(?:负|(?i:\bminus|\bnegative|\bmoins|\bmenos))\s*$",
                    text[: match.start(field)],
                )
                minimum_idiom = re.search(
                    r"\b(?:al menos|au moins)\s*$", text[: match.start(field)], re.I
                )
                if negative_word and not minimum_idiom:
                    value, ambiguity = None, "unsupported_word_sign"
                if mode == "attached" and match["separator"] in {"a", "à", "bis"}:
                    expected = {"a": "es", "à": "fr", "bis": "de"}[match["separator"]]
                    if language != expected:
                        value, ambiguity = None, "unproved_range_language"
                if mode == "unsupported":
                    value, ambiguity, alternatives = None, "unsupported_arithmetic_notation", ()
                ordinal_list = mode == "ordinal_class" and (
                    match["separator"] or ""
                ).casefold() in {"or", "and", "/"}
                role = (
                    "tolerance"
                    if field == "tolerance"
                    else ("range_endpoint" if "second" in fields and not ordinal_list else "value")
                )
                unit_field = (
                    "first_unit"
                    if mode == "verbal_range" and field == "first" and match["first_unit"]
                    else "unit"
                )
                candidate = SourceQuantity(
                    value=value,
                    raw_number=match[field],
                    number_span=match.span(field),
                    raw_unit=match[unit_field],
                    unit="panels" if mode == "panel_count" else _unit_key(match[unit_field]),
                    unit_span=match.span(unit_field),
                    expression_span=match.span(),
                    language=language,
                    role=role,
                    operator="range" if role == "range_endpoint" else None,
                    ambiguity=ambiguity,
                    alternatives=alternatives,
                )
                if mode == "scale":
                    operator, operator_ambiguity = _operator(match["op"], "")
                    candidate = replace(
                        candidate, operator=operator, operator_ambiguity=operator_ambiguity
                    )
                if mode == "ordinal_class":
                    prefix = text[max(0, match.start() - 40) : match.start()]
                    suffix = re.split(r"[;；。\n]", text[match.end() : match.end() + 60])[0]
                    negated = bool(
                        re.search(
                            r"\b(?:not|no|except|excluding|neither|reject(?:ed)?)\s+"
                            r"(?:(?:only|when|if|for|acceptable|accepted|allowed)\s+){0,2}$",
                            prefix,
                            re.I,
                        )
                        or re.match(
                            r"\s*(?:(?:is|are)\s+)?(?:not\s+(?:acceptable|accepted|allowed|permitted)"
                            r"|unacceptable|rejected|prohibited)\b",
                            suffix,
                            re.I,
                        )
                    )
                    mixed = bool(match["op"] and match["second"] is not None)
                    operator, operator_ambiguity = _operator(match["op"] or "", "")
                    candidate = replace(
                        candidate,
                        operator=(
                            "one_of"
                            if ordinal_list
                            else "range"
                            if "second" in fields
                            else operator
                        ),
                        operator_ambiguity=(
                            "negated_or_mixed_ordinal_class"
                            if negated or mixed
                            else operator_ambiguity
                        ),
                    )
                candidates.append(candidate)
    candidates.sort(key=lambda row: row.number_span)
    result = []
    for index, candidate in enumerate(candidates):
        if (
            candidate.operator is None
            and candidate.operator_ambiguity is None
            and candidate.role != "tolerance"
        ):
            earlier = [
                row.unit_span[1]
                for row in candidates[:index]
                if row.expression_span != candidate.expression_span
            ]
            prefix_start = max([candidate.expression_span[0] - 80, 0, *earlier])
            prefix = text[prefix_start : candidate.number_span[0]]
            prefix = re.split(r"[;；。\n]", prefix)[-1]
            later = [
                row.expression_span[0]
                for row in candidates[index + 1 :]
                if row.expression_span != candidate.expression_span
            ]
            suffix_end = min([candidate.unit_span[1] + 50, len(text), *later])
            suffix = text[candidate.unit_span[1] : suffix_end]
            operator, operator_ambiguity = _operator(prefix, suffix)
            candidate = replace(candidate, operator=operator, operator_ambiguity=operator_ambiguity)
        result.append(candidate)
    return tuple(result)


def grounded_source_numbers(
    text: str, *, unit: str | None = None, context: str | None = None
) -> set[Decimal]:
    """Return unambiguous attached lexical values only, with optional unit filtering."""
    wanted = _unit_key(unit) if unit is not None else None
    return {
        row.value
        for row in source_quantities(text, context=context)
        if row.value is not None
        and row.ambiguity is None
        and (wanted is None or row.unit == wanted)
    }


def source_operator_hints(
    text: str,
    *,
    unit: str | None = None,
    values: set[Decimal] | None = None,
    context: str | None = None,
) -> set[str]:
    """Return local explicit operators for the selected source unit/value candidates."""
    wanted = _unit_key(unit) if unit is not None else None
    return {
        row.operator
        for row in source_quantities(
            text, context=context,
            additional_unit=unit if unit is not None and resolve_unit(unit) is None else None,
        )
        if row.value is not None
        and row.ambiguity is None
        and row.operator is not None
        and (wanted is None or row.unit == wanted)
        and (values is None or row.value in values)
    }
