"""Exact unit conversions for pilot quantities; no dimensional guessing."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from scione.numeric_literals import NUMBER, NUMBER_START


@dataclass(frozen=True)
class Unit:
    name: str
    dimension: str
    factor: Fraction = Fraction(1)
    offset: Fraction = Fraction(0)


def _key(value: str) -> str:
    # SI prefix case is meaningful: mPa and MPa differ by 10**9, and Mm is
    # not mm. Only full-language names have a case-insensitive alias lookup.
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


_UNITS: dict[str, Unit] = {}
_WORD_UNITS: dict[str, Unit] = {}


def _add(name, dimension, aliases=(), factor=1, offset=0):
    value = Unit(name, dimension, Fraction(factor), Fraction(offset))
    for alias in (name, *aliases):
        _UNITS[_key(alias)] = value
        if len(alias) > 3 and alias.isalpha():
            _WORD_UNITS[_key(alias).casefold()] = value


_add("°C", "temperature", ("Celsius", "degC", "摄氏度"))
_add("°F", "temperature", ("Fahrenheit", "degF", "华氏度"), Fraction(5, 9), Fraction(-160, 9))
_add("K", "temperature", ("kelvin",), 1, Fraction(-27315, 100))
_add("Δ°C", "temperature_difference", ("delta_degC", "delta °C"))
_add("Δ°F", "temperature_difference", ("delta_degF", "delta °F"), Fraction(5, 9))
_add("ΔK", "temperature_difference", ("delta_kelvin",))
_add("s", "time", ("sec", "second", "seconds", "秒"))
_add("min", "time", ("minute", "minutes", "分钟"), 60)
_add("h", "time", ("hr", "hrs", "hour", "hours", "小时"), 3600)
_add("days", "time", ("day", "天"), 86400)
_add("m", "length", ("meter", "metre", "米"))
_add("cm", "length", ("厘米",), Fraction(1, 100))
_add("mm", "length", ("毫米",), Fraction(1, 1000))
_add("µm", "length", ("μm", "um", "micrometer", "micrometre", "微米"), Fraction(1, 1_000_000))
_add("nm", "length", ("纳米",), Fraction(1, 1_000_000_000))
_add(
    "in",
    "length",
    (
        "inch",
        "inches",
    ),
    Fraction(127, 5000),
)
_add("kg", "mass", ("kilogram", "千克"))
_add("g", "mass", ("gram", "grams", "克"), Fraction(1, 1000))
_add("mg", "mass", ("毫克",), Fraction(1, 1_000_000))
_add("N", "force", ("newton", "牛顿"))
_add("kN", "force", (), 1000)
_add("Pa", "pressure", ("pascal", "帕"))
_add("kPa", "pressure", (), 1000)
_add("MPa", "pressure", (), 1_000_000)
_add("GPa", "pressure", (), 1_000_000_000)
_add("bar", "pressure", ("巴",), 100_000)
_add("J", "energy", ("joule", "joules", "焦耳"))
_add("kJ", "energy", (), 1000)
_add("HRC", "hardness_rockwell_c", ("Rockwell C",))
_add("HV", "hardness_vickers", ("Vickers HV",))
_add("°", "angle", ("deg", "degree", "degrees", "度"))
_add("cycles", "cycles", ("cycle", "次循环"))
_add("locations", "locations", ("location", "positions", "position", "点"))
_add(
    "specimens",
    "specimens",
    ("specimen", "sample", "samples", "test specimen", "test specimens", "试样", "试件"),
)
_add("panels", "panels", ("panel", "test panel", "test panels", "试板", "块试板"))
_add("%", "fraction", ("percent", "百分比"), Fraction(1, 100))
_add("% RH", "humidity", ("%RH", "RH%"), Fraction(1, 100))
_add("GU", "gloss", ("glossunit", "glossunits"))
_add("ΔE*ab", "color_ab", ("ΔEab", "deltaeab", "delta_e_ab"))
_add("class", "adhesion_class", ("ISO class", "classes", "等级"))


def _lookup(value: str) -> Unit | None:
    key = _key(value)
    return _UNITS.get(key) or _WORD_UNITS.get(key.casefold())


def resolve_unit(
    value: str | None, *, dimension: str | None = None, difference: bool = False
) -> Unit | None:
    if value is None:
        if dimension in {"cycles", "locations", "adhesion_class", "specimens", "panels"}:
            return Unit(dimension, dimension)
        return None
    unit = _lookup(value)
    if unit is None:
        return None
    if dimension == "humidity" and unit.dimension == "fraction":
        unit = Unit(unit.name, "humidity", unit.factor)
    if dimension == "temperature_difference" and unit.dimension == "temperature":
        unit = Unit(unit.name, "temperature_difference", unit.factor)
    if difference and unit.dimension == "temperature":
        unit = Unit(unit.name, "temperature_difference", unit.factor)
    if dimension is not None and unit.dimension != dimension:
        return None
    return unit


def canonical_unit_name(value: str | None) -> str | None:
    unit = _lookup(value) if value is not None else None
    return unit.name if unit else value


def unit_is_stated(value: str, text: str) -> bool:
    """Find an explicit unit alias without conflating SI prefixes or substrings.

    This checks lexical evidence only, not which number/object the unit belongs
    to. The caller must separately verify source spans, numbers and relations.
    """
    wanted = _lookup(value)
    if wanted is None:
        return False
    source = unicodedata.normalize("NFKC", text)
    for alias, unit in _UNITS.items():
        if unit.name != wanted.name:
            continue
        flags = re.I if alias.isalpha() and len(alias) > 3 else 0
        if re.search(r"(?<![A-Za-zµμΔ°])" + re.escape(alias) + r"(?![A-Za-zµμ])", source, flags):
            return True
    return False


def source_numbers_for_unit(value: str, text: str) -> set[Decimal]:
    """Numbers explicitly attached to this unit, including stated range endpoints.

    No conversions occur: extraction must retain source values/units. This is
    a lexical consistency check, not a proof of stage/object association.
    """
    wanted = _lookup(value)
    if wanted is None:
        return set()
    aliases = []
    for alias, unit in _UNITS.items():
        if unit.name != wanted.name:
            continue
        pattern = re.escape(alias)
        if alias.startswith(("°", "Δ°")):
            pattern = pattern.replace("°", "°\\s*")
        if alias == "%RH":
            pattern = "%\\s*RH"
        if alias.isalpha() and len(alias) > 3:
            pattern = f"(?i:{pattern})"
        aliases.append(pattern)
    if not aliases:
        return set()
    expression = (
        rf"{NUMBER_START}(?P<first>{NUMBER})"
        rf"(?:\s*(?:±\s*(?P<tolerance>{NUMBER})|(?:-|\bto\b)\s*(?P<second>{NUMBER})))?"
        rf"\s*(?:{'|'.join(sorted(aliases, key=len, reverse=True))})(?![A-Za-zµμ])"
    )
    source = unicodedata.normalize("NFKC", text).replace("−", "-").replace("–", "-")
    return {
        Decimal(part)
        for match in re.finditer(expression, source)
        for part in match.groupdict().values()
        if part is not None
    }


def convert_value(value, source: Unit) -> Fraction:
    return Fraction(value) * source.factor + source.offset
