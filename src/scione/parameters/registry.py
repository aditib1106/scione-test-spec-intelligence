"""Small, explicit parameter definitions for a controlled pilot.

Definitions describe comparison semantics, not which tests a lab can perform.
The registry is extensible; unknown parameters remain stored in source records
and need an explicit definition or reviewed semantic correspondence.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict

REGISTRY_VERSION = "pilot_parameters_v2"
ValueKind = Literal["number", "category", "set", "boolean"]
ComparisonRule = Literal[
    "operating_range",
    "reporting_range",
    "lower_is_better",
    "category",
    "required_set",
    "boolean_presence",
]


class ParameterDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    description: str
    aliases: tuple[str, ...] = ()
    value_kind: ValueKind = "number"
    dimension: str | None = None
    rule: ComparisonRule = "operating_range"
    default_unit: str | None = None
    allowed_roles: tuple[Literal["condition", "reporting", "scope"], ...] = ("condition",)
    subject: str | None = None
    default_stage: str = "general"


def fold(value: str | None) -> str:
    """Normalize spelling presentation, never infer scientific meaning."""
    return re.sub(
        r"[\s_\-‐‑‒–—]+", " ", unicodedata.normalize("NFKC", value or "").casefold()
    ).strip()


def _number(
    identifier,
    label,
    description,
    aliases=(),
    *,
    unit=None,
    dimension=None,
    rule="operating_range",
    roles=("condition",),
    subject=None,
    stage="general",
):
    return ParameterDefinition(
        id=identifier,
        label=label,
        description=description,
        aliases=tuple(aliases),
        dimension=dimension,
        rule=rule,
        default_unit=unit,
        allowed_roles=roles,
        subject=subject,
        default_stage=stage,
    )


_DEFINITIONS = (
    _number(
        "temperature",
        "Temperature",
        "Operating temperature; specify the medium or object.",
        ("temp", "temperature", "temprature", "温度", "度温"),
        unit="°C",
        dimension="temperature",
    ),
    _number(
        "water_temperature",
        "Water temperature",
        "Temperature of the test water, not ambient air.",
        ("water temperature", "water temp", "水温", "水温度"),
        unit="°C",
        dimension="temperature",
        subject="water",
    ),
    _number(
        "air_temperature",
        "Air temperature",
        "Temperature of air, not test water or specimen metal.",
        ("air temperature", "ambient air temperature", "空气温度", "气温"),
        unit="°C",
        dimension="temperature",
        subject="air",
    ),
    _number(
        "temperature_difference",
        "Temperature difference",
        "A temperature interval, not an absolute reading.",
        ("temperature difference", "temperature delta", "温差"),
        unit="Δ°C",
        dimension="temperature_difference",
    ),
    _number(
        "duration",
        "Duration",
        "Execution time in the same stage and sequence step.",
        ("time duration", "test duration", "时间", "时长", "持续时间"),
        unit="s",
        dimension="time",
    ),
    _number(
        "distance",
        "Distance",
        "Distance between the specified objects; retain its context.",
        ("distance", "standoff distance", "距离"),
        unit="cm",
        dimension="length",
    ),
    _number(
        "spacing",
        "Spacing",
        "Spacing for the stated pattern or method; not arbitrary specimen length.",
        ("spacing", "grid spacing", "间距", "划格间距"),
        unit="mm",
        dimension="length",
    ),
    _number(
        "specimen_length",
        "Specimen length",
        "Length of the specimen, not nozzle distance or grid spacing.",
        ("specimen length", "sample length", "试样长度", "样品长度"),
        unit="mm",
        dimension="length",
        subject="specimen",
    ),
    _number(
        "load",
        "Load / applied mass",
        "Applied mass. Force in N is a separate parameter.",
        ("load mass", "applied mass", "加载质量", "载荷质量"),
        unit="g",
        dimension="mass",
    ),
    _number(
        "force",
        "Force",
        "Applied force. Do not infer equivalence to a mass in g.",
        ("force", "applied force", "作用力", "施加力"),
        unit="N",
        dimension="force",
    ),
    _number(
        "pressure",
        "Pressure",
        "Pressure within the stated operation and configuration.",
        ("pressure", "压力", "压强"),
        unit="bar",
        dimension="pressure",
    ),
    _number(
        "cycles",
        "Cycle count",
        "An explicit cycle count; retain step definitions separately.",
        ("cycle count", "number of cycles", "循环次数", "周期数"),
        unit="cycles",
        dimension="cycles",
    ),
    _number(
        "measurement_angle",
        "Measurement angle",
        "Measurement angle for the same output.",
        ("measurement angle", "measuring angle", "测量角度"),
        unit="°",
        dimension="angle",
        stage="measurement",
    ),
    _number(
        "relative_humidity",
        "Relative humidity",
        "Relative humidity in the stated exposure stage.",
        ("relative humidity", "humidity rh", "相对湿度"),
        unit="% RH",
        dimension="humidity",
    ),
    _number(
        "film_thickness",
        "Film thickness",
        "Reportable film thickness; keep layer and result scope.",
        ("film thickness", "dry film thickness", "膜厚", "干膜厚度"),
        unit="µm",
        dimension="length",
        rule="reporting_range",
        roles=("reporting",),
    ),
    _number(
        "topcoat_thickness",
        "Topcoat thickness",
        "Separate topcoat thickness, not total film build.",
        ("topcoat thickness", "separate topcoat thickness", "面漆厚度"),
        unit="µm",
        dimension="length",
        rule="reporting_range",
        roles=("reporting",),
        subject="topcoat",
    ),
    _number(
        "gloss",
        "Specular gloss",
        "Reportable gloss scale in the declared measurement stage.",
        ("gloss", "specular gloss", "光泽", "光泽度"),
        unit="GU",
        dimension="gloss",
        rule="reporting_range",
        roles=("reporting",),
    ),
    _number(
        "gloss_retention",
        "Gloss retention",
        "Reportable retention relative to the corresponding baseline.",
        ("gloss retention", "retained gloss", "光泽保持率", "光泽保留率"),
        unit="%",
        dimension="fraction",
        rule="reporting_range",
        roles=("reporting",),
    ),
    _number(
        "color_difference",
        "Colour difference",
        "Reportable colour-difference scale; ΔE formulas are distinct.",
        ("colour difference", "color difference", "色差"),
        unit="ΔE*ab",
        dimension="color_ab",
        rule="reporting_range",
        roles=("reporting",),
    ),
    _number(
        "adhesion_class",
        "Adhesion class",
        "Reportable native rating scale; does not predict specimen success.",
        ("adhesion class", "cross cut class", "附着力等级"),
        unit="class",
        dimension="adhesion_class",
        rule="reporting_range",
        roles=("reporting",),
    ),
    _number(
        "measurement_resolution",
        "Measurement resolution",
        "Smallest resolved increment. Smaller is better.",
        ("resolution", "measurement resolution", "分辨率", "测量分辨率"),
        unit="mm",
        rule="lower_is_better",
        roles=("condition", "reporting"),
        stage="measurement",
    ),
    _number(
        "measurement_uncertainty",
        "Measurement uncertainty",
        "An uncertainty magnitude with an explicit unit.",
        ("measurement uncertainty", "测量不确定度"),
        unit="mm",
        rule="lower_is_better",
        roles=("condition", "reporting"),
        stage="measurement",
    ),
    _number(
        "measurement_locations",
        "Measurement locations",
        "Number of locations; retain reporting scope.",
        ("measurement locations", "measurement positions", "测量位置数", "测量点数"),
        unit="locations",
        dimension="locations",
        stage="measurement",
    ),
    ParameterDefinition(
        id="equipment",
        label="Equipment",
        description="Explicitly declared instruments or tools.",
        aliases=("equipment", "instrument", "instruments", "设备", "仪器"),
        value_kind="set",
        rule="required_set",
    ),
    ParameterDefinition(
        id="panel_shape",
        label="Panel shape",
        description="Shape of the specimen panel.",
        aliases=("panel shape", "specimen shape", "试板形状", "试样形状"),
        value_kind="category",
        rule="category",
        allowed_roles=("condition", "scope"),
    ),
    ParameterDefinition(
        id="specimen_material",
        label="Specimen material",
        description="Explicit material or panel category.",
        aliases=("specimen material", "sample material", "试样材料", "样品材料"),
        value_kind="category",
        rule="category",
        allowed_roles=("condition", "scope"),
    ),
    ParameterDefinition(
        id="illuminant",
        label="Lighting / illuminant",
        description="Declared illuminant, not an inferred light source.",
        aliases=("illuminant", "lighting", "光源", "照明光源"),
        value_kind="category",
        rule="category",
        default_stage="measurement",
    ),
    ParameterDefinition(
        id="reference_panel",
        label="Reference / master panel",
        description="Presence of the specified reference panel.",
        aliases=("reference panel", "master panel", "参考板", "标准样板"),
        value_kind="boolean",
        rule="boolean_presence",
        default_stage="measurement",
    ),
)

_INDEX: dict[str, ParameterDefinition] = {}
for _definition in _DEFINITIONS:
    for _alias in (_definition.id, _definition.label, *_definition.aliases):
        key = fold(_alias)
        if key in _INDEX and _INDEX[key].id != _definition.id:
            raise RuntimeError("A parameter alias must not silently identify multiple concepts.")
        _INDEX[key] = _definition


def definitions() -> list[ParameterDefinition]:
    return list(_DEFINITIONS)


def resolve_definition(identifier: str | None) -> ParameterDefinition | None:
    return _INDEX.get(fold(identifier))


_STAGES = {
    "general": ("general", "一般", "通用"),
    "measurement": ("measurement", "measuring", "measurement setup", "测量", "测量阶段"),
    "conditioning": ("conditioning", "preconditioning", "pre conditioning", "预处理", "调节"),
    "curing": ("curing", "cure", "固化", "烘烤固化"),
    "initial": ("initial", "baseline", "初始", "基线"),
    "after_cycle": ("after cycle", "after cycles", "after cycling", "post cycle", "循环后"),
    "thermal_cycle": ("thermal cycle", "thermal cycling", "热循环"),
    "cycle_recovery": ("cycle recovery", "between transfers recovery", "循环恢复", "转移间恢复"),
}
_STAGE_INDEX = {fold(alias): key for key, aliases in _STAGES.items() for alias in aliases}


def normalize_stage(value: str | None) -> str:
    return _STAGE_INDEX.get(fold(value), value or "general")


_CATEGORIES = {
    "panel_shape": {
        "flat_panel": ("flat", "flat panel", "flat panels", "平板", "平面试板"),
        "curved_panel": ("curved", "curved panel", "曲面板", "曲面试板"),
    },
    "specimen_material": {
        "steel": ("steel", "钢"),
        "steel_panel": ("steel panel", "steel panels", "钢板"),
        "aluminium": ("aluminium", "aluminum", "铝"),
        "aluminium_panel": ("aluminium panel", "aluminum panel", "铝板"),
    },
    "equipment": {
        "temperature_controlled_chamber": ("temperature controlled chamber", "恒温箱"),
        "uv_lamp": ("uv lamp", "ultraviolet lamp", "紫外灯"),
        "spectrophotometer": ("spectrophotometer", "分光光度计"),
    },
    "illuminant": {"D65": ("d65", "d 65"), "D50": ("d50", "d 50")},
}


def normalize_category(parameter_id: str, value: str) -> str:
    folded = fold(value)
    for canonical, aliases in _CATEGORIES.get(parameter_id, {}).items():
        if folded in {fold(canonical), *(fold(alias) for alias in aliases)}:
            return canonical
    # An unfamiliar category remains its own value; it is not assigned a known
    # equivalence or broad superclass based on an approximate string score.
    return value.strip()


def known_category(parameter_id: str, value: str) -> bool:
    return normalize_category(parameter_id, value) in _CATEGORIES.get(parameter_id, {})
