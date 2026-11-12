"""Versioned development declarations derived from the team's supplied lab data.

Loading this value has no side effect. A setup operation must explicitly save it;
normal application startup never replaces an existing lab.
"""

import json
from importlib.resources import files

from scione.catalogs.models import LabProfile


def development_lab_baseline() -> LabProfile:
    payload = json.loads(
        files("scione.catalogs").joinpath("data/development_lab_baseline_v1.json").read_text()
    )
    if payload.get("synthetic") is not True:
        raise ValueError("Development declarations must be explicitly synthetic.")
    return LabProfile.model_validate(payload["lab"])


def ms_pnt_410_lab_baseline() -> LabProfile:
    """Load the fixed fictional coatings lab without writing or replacing any lab.

    Source-cited comparison expectations are fixture metadata, not laboratory
    declarations. Loading this separate profile never changes the default setup
    baseline or implies that a client lab owns these capabilities.
    """
    payload = json.loads(
        files("scione.catalogs").joinpath("data/ms_pnt_410_lab_baseline_v1.json").read_text()
    )
    if payload.get("synthetic") is not True:
        raise ValueError("MS-PNT-410 test declarations must be explicitly synthetic.")
    return LabProfile.model_validate(payload["lab"])
