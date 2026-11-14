"""Explicit synthetic examples; never infer or overwrite a lab's declarations."""

import json
from importlib.resources import files

from scione.catalogs.models import LabProfile


def typed_csp301_lab() -> LabProfile:
    return _example("csp301_typed_lab_example.json", "csp301_typed")


def detailed_csp301_lab() -> LabProfile:
    return _example("csp301_detailed_lab_example.json", "csp301_detailed")


def demonstration_csp301_lab() -> LabProfile:
    return _example("csp301_demo_lab_example.json", "csp301_demo")


def _example(filename: str, example_id: str) -> LabProfile:
    resource = files("scione.catalogs").joinpath(f"data/{filename}")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("synthetic") is not True or payload.get("example_id") != example_id:
        raise ValueError("The typed capability example must be explicitly synthetic.")
    return LabProfile.model_validate(payload["lab"])
