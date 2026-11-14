"""Apply only physically proved synthetic-bank label conventions, with an audit."""

import re

from scione.schemas import IngestedDocument
from scione.specifications.method_labels import canonicalize_method_label
from scione.specifications.models import CustomerExtraction, CustomerMethodLabelAction


def enrich_method_labels(
    extraction: CustomerExtraction, document: IngestedDocument
) -> tuple[CustomerExtraction, list[CustomerMethodLabelAction]]:
    result = extraction.model_copy(deep=True)
    actions = []
    for requirement in result.requirements:
        for method in requirement.requested_methods:
            # Avoid loading the full bank for ordinary bare designators. This
            # is a format prefilter only; the proof helper enforces ownership,
            # exact bank identity/edition and absence of scientific qualifiers.
            if not re.search(
                r"(?::\s*| - )(?:ISO|ASTM|DIN|EN|GB|SAE|VDA)|"
                r"\([A-Z]{2,5}-\d+\)\s*$", method.code,
            ):
                continue
            proof = canonicalize_method_label(method.code, requirement.evidence, document)
            if proof is None:
                continue
            actions.append(CustomerMethodLabelAction(
                requirement_id=requirement.id, raw_reference=method.code, code=proof.code,
                family_id=proof.family_id, form=proof.form, page=proof.page,
                source_start=proof.source_span[0], source_end=proof.source_span[1],
                code_start=proof.source_code_span[0], code_end=proof.source_code_span[1],
                parent_evidence_index=proof.parent_evidence_index,
            ))
            method.code = proof.code
    return result, actions
