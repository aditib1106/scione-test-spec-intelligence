"""Frozen offered services and negative controls stay independent of inference."""

import hashlib
import json
import re
import runpy
import unicodedata
from pathlib import Path

import pymupdf

from scione.catalogs.baseline import development_lab_baseline
from scione.catalogs.seed import load_reference_catalog

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "src/scione/catalogs/data/development_lab_policy_v2.json"
BASELINE = ROOT / "src/scione/catalogs/data/development_lab_baseline_v1.json"
REFERENCE = ROOT / "tests/fixtures/corpus_lab_reference_v2.json"


def test_lab_rebuild_uses_frozen_policy_and_is_identical_without_model_results():
    builder = runpy.run_path(str(ROOT / "scripts/build_development_lab.py"))
    rebuilt = builder["build_development_lab"](ROOT)
    assert rebuilt == json.loads(BASELINE.read_text())
    assert rebuilt["policy_sha256"] == hashlib.sha256(POLICY.read_bytes()).hexdigest()


def test_explicit_unoffered_methods_cannot_borrow_a_related_registered_service():
    catalog = load_reference_catalog()
    offered = {
        catalog.method_by_id(method).identifier
        for cap in development_lab_baseline().capabilities for method in cap.method_ids
    }
    assert "ASTM D1025-19" in offered
    assert "ASTM D1065-26" not in offered
    assert "ISO 60465:2019" in offered
    assert "ISO 60967:2026" not in offered
    assert "ASTM E1075-25" not in offered
    assert "ASTM F1151-24" not in offered
    assert "ISO 60212:2019" not in offered


def test_shared_lab_limits_cover_ordinary_requests_and_preserve_real_gaps():
    catalog = load_reference_catalog()
    lab = development_lab_baseline()

    def claims(identifier):
        return next(
            cap.atomic_claims for cap in lab.capabilities
            if identifier in [catalog.method_by_id(m).identifier for m in cap.method_ids]
        )

    salt = next(c for c in claims("ISO 61882:2017") if c.parameter_id == "duration"
                and c.stage == "exposure")
    assert salt.maximum == 600 and salt.maximum < 720
    thermal = next(c for c in claims("ISO 60038:2021") if c.parameter_id == "cycles"
                   and c.stage == "thermal_cycle")
    assert thermal.minimum <= 5 <= thermal.maximum
    lead = next(c for c in claims("DIN EN ISO 60682:2024-08")
                if c.parameter_id == "lead_concentration")
    assert lead.minimum == 10 and lead.minimum > 5
    gloss = claims("ASTM D1627-16")
    assert any(c.parameter_id == "gloss" and c.stage == "initial"
               and c.availability == "supported" for c in gloss)
    assert any(c.parameter_id == "gloss_retention" and c.stage == "after_cycle"
               and c.availability == "unsupported" for c in gloss)


def test_model_authored_reference_is_source_cited_and_frozen_to_the_single_lab():
    data = json.loads(REFERENCE.read_text())
    assert "not human/client-validated gold" in data["authoring_status"]
    assert data["lab_policy_sha256"] == hashlib.sha256(POLICY.read_bytes()).hexdigest()
    assert data["baseline_sha256"] == hashlib.sha256(BASELINE.read_bytes()).hexdigest()
    assert len(data["documents"]) == 22
    assertions = [a for d in data["documents"] for a in d["assertions"]]
    assert len(assertions) == 70
    assert {a["expected"] for a in assertions} == {
        "supported", "gap", "not_offered", "needs_information", "informational"
    }
    assert all(a["source_page"] >= 1 and a["source_quote"] for a in assertions)
    # The full development PDFs are deliberately optional, not packaged test inputs.
    for doc in data["documents"]:
        path = ROOT / "docs/Generated Customer Spec" / doc["file_name"]
        if not path.exists():
            continue
        assert hashlib.sha256(path.read_bytes()).hexdigest() == doc["source_sha256"]
        with pymupdf.open(path) as pdf:
            for row in doc["assertions"]:
                text = unicodedata.normalize("NFKC", pdf[row["source_page"] - 1].get_text())
                text = re.sub(r"\s+", " ", text.replace("\x00", "")).strip()
                assert row["source_quote"] in text
