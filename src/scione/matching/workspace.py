"""Join explicit matching requests, saved attempts and deterministic comparisons."""

from fastapi import HTTPException

from scione.library.store import digest
from scione.matching import SemanticMatchingRunner, build_matching_input, compile_matching_run
from scione.matching.projection import MATCHING_PROJECTION_VERSION
from scione.matching.service import SemanticMatchingError, input_fingerprint
from scione.matching.store import SQLMatchingStore
from scione.parameters import REGISTRY_VERSION, normalization_evidence
from scione.reports.matching import _method_resolution, lab_fingerprint
from scione.structured import normalize_requirement


class MatchingWorkspace:
    def __init__(self, workspace):
        self.workspace = workspace
        self.store = SQLMatchingStore(workspace.library.store, workspace.labs.workspace_id)

    def snapshot(self, report):
        choices = {
            line.requirement.id: (
                [line.mapping_choice.canonical_test_id]
                if line.mapping_choice and line.mapping_choice.canonical_test_id
                else list(
                    dict.fromkeys(
                        [
                            *([line.canonical_test_id] if line.canonical_test_id else []),
                            *line.candidate_test_ids,
                        ]
                    )
                )
                + [
                    test.id
                    for test in self.workspace.catalog.tests
                    if test.id not in {line.canonical_test_id, *line.candidate_test_ids}
                ]
            )
            for line in report.lines
        }
        # This pilot bank is small: rank rule candidates first, then expose the
        # remaining compact metadata. A weak shortlist must not permanently
        # hide the correct test from semantic matching. The request budget is
        # still checked before inference, never silently truncate candidates.
        return build_matching_input(
            report.source_run,
            report.catalog,
            report.lab,
            capability_revision=lab_fingerprint(report.lab),
            review_revision=digest(
                [
                    REGISTRY_VERSION,
                    MATCHING_PROJECTION_VERSION,
                    report.source_facts_revision_id,
                    report.source_review.model_dump(mode="json"),
                    {
                        key: value.model_dump(mode="json")
                        for key, value in report.workspace.mappings.items()
                    },
                ]
            ),
            candidate_test_ids=choices,
        )

    def overview(self, document_id: str) -> dict:
        report = self.workspace.draft(document_id)
        fingerprint = input_fingerprint(self.snapshot(report))
        current = self.store.latest_completed(document_id, fingerprint)
        return {
            "source_run_id": report.source_run.run_id,
            "report_input_fingerprint": report.input_fingerprint,
            "input_fingerprint": fingerprint,
            "current_run_id": current.run_id if current else None,
            "runs": [
                {**row, "current_inputs": row["input_fingerprint"] == fingerprint}
                for row in self.store.listing(document_id)
            ],
        }

    def run(self, document_id: str, body, provider) -> dict:
        report = self.workspace.draft(document_id)
        if (
            report.source_run.run_id != body.run_id
            or report.input_fingerprint != body.input_fingerprint
        ):
            raise HTTPException(409, "Inputs changed. Reload the report before AI matching.")
        snapshot = self.snapshot(report)
        reviews = {
            "source_review": report.source_review.model_dump(mode="json"),
            "report_workspace": report.workspace.model_dump(mode="json"),
            "source_facts_revision_id": report.source_facts_revision_id,
        }
        machine_source = self.workspace.specs.load_extraction(document_id, report.source_run.run_id)
        policies = {
            "parameter_policy_version": REGISTRY_VERSION,
            "request_canonicalization": [
                normalization_evidence(claim)
                for row in snapshot.requirements
                for claim in row.normalized.claims
            ],
            "lab_canonicalization": [
                {"capability_id": row.capability.id, **normalization_evidence(claim)}
                for row in snapshot.capabilities
                for claim in row.claims
            ],
            "machine_normalized_source": [
                normalize_requirement(item).model_dump(mode="json")
                for item in machine_source.extraction.requirements
            ],
        }
        cached = self.store.latest_completed(
            document_id,
            input_fingerprint(snapshot),
            provider=provider.provider_name,
            model=provider.model_name,
            generation_settings=provider.generation_settings,
        )
        if cached is not None and not body.rerun:
            return {"run_id": cached.run_id, "status": cached.status, "cached": True}
        try:
            attempt = SemanticMatchingRunner(
                provider,
                max_input_characters=self.workspace.settings.scione_matching_input_character_limit,
            ).run(snapshot)
        except SemanticMatchingError as exc:
            exc.run.review_snapshot = reviews
            exc.run.policy_snapshot = policies
            self.store.save(document_id, exc.run)
            raise HTTPException(
                502,
                {
                    "message": str(exc),
                    "run_id": exc.run.run_id,
                    "error_code": exc.run.error_code,
                },
            ) from exc
        comparisons, _selected = self._compile(report, attempt)
        attempt.review_snapshot = reviews
        attempt.policy_snapshot = policies
        attempt.validated_comparison = comparisons
        self.store.save(document_id, attempt)
        if self.workspace.draft(document_id).input_fingerprint != report.input_fingerprint:
            raise HTTPException(
                409,
                "Inputs changed during AI matching. The attempt is saved in history; "
                "run matching for the latest inputs before applying it.",
            )
        return {"run_id": attempt.run_id, "status": attempt.status, "cached": False}

    def compiled(self, report):
        snapshot = self.snapshot(report)
        attempt = self.store.latest_completed(report.document_id, input_fingerprint(snapshot))
        if attempt is None:
            return None, {}, {}
        comparisons, selected = self._compile(report, attempt)
        return attempt, {row.requirement_id: row for row in comparisons}, selected

    def _compile(self, report, attempt):
        capabilities = {item.id: item for item in report.lab.capabilities}
        proposals = {item.requirement_id: item for item in attempt.output.items}
        statuses = {}
        selected = {}
        for line in report.lines:
            proposal = proposals[line.requirement.id]
            capability = capabilities.get(proposal.capability_id)
            selected[line.requirement.id] = capability
            status = "unknown"
            if (
                capability is None
                and proposal.identity_status == "aligned"
                and proposal.canonical_test_id == line.canonical_test_id
                and line.status in {"test_gap", "method_gap"}
            ):
                # Semantic deferral must not erase a known absent catalog
                # method/test declaration for this same confirmed identity.
                status = "gap"
            if proposal.identity_status == "aligned" and capability:
                methods = [
                    method
                    for method in line.requirement.requested_methods
                    if method.code in line.considered_method_codes
                ]
                resolved = [_method_resolution(method, report.catalog) for method in methods]
                if methods and not any(reason for _, reason in resolved):
                    identifiers = {identifier for identifier, _ in resolved}
                    status = "supported" if identifiers <= set(capability.method_ids) else "gap"
                elif (
                    not methods
                    and capability.id == (line.capability.id if line.capability else None)
                    and not line.unknown_methods
                ):
                    status = "supported" if line.status != "needs_review" else "unknown"
            statuses[line.requirement.id] = status
        comparisons = compile_matching_run(attempt, statuses)
        return comparisons, selected

    def artifacts(self, document_id: str, run_id: str) -> dict:
        document = self.workspace.specs.load_document(document_id)
        attempt = self.store.load(document_id, run_id)
        source = self.workspace.specs.load_extraction(
            document_id, attempt.input_snapshot.extraction_run_id
        )
        return {
            "manifest": {
                "schema_version": "0.1",
                "document_id": document_id,
                "source_sha256": document.document.sha256,
                "extraction_run_id": source.run_id,
                "matching_run_id": attempt.run_id,
                "input_fingerprint": attempt.input_fingerprint,
                "capability_revision": attempt.input_snapshot.capability_revision,
                "catalog_revision": attempt.input_snapshot.catalog_revision,
                "review_revision": attempt.input_snapshot.review_revision,
                "status": attempt.status,
                "validation_version": attempt.validation_version,
                "projection_version": attempt.projection_version,
                "parameter_policy_version": attempt.policy_snapshot.get("parameter_policy_version"),
                "machine_normalization_origin": "saved_at_matching"
                if "machine_normalized_source" in attempt.policy_snapshot
                else "derived_at_export",
                "normalizer_versions": sorted(
                    {
                        row.normalized.normalizer_version
                        for row in attempt.input_snapshot.requirements
                    }
                ),
                "human_corrections_included": bool(
                    (attempt.review_snapshot or {}).get("source_facts_revision_id")
                ),
                "notes": "Matching input carries the source-review revision. Machine facts "
                "and accepted semantic links remain separate from human actions.",
            },
            "extraction_machine": source.model_dump(mode="json"),
            "extraction_normalized_machine": attempt.policy_snapshot.get(
                "machine_normalized_source"
            )
            or [
                normalize_requirement(item).model_dump(mode="json")
                for item in source.extraction.requirements
            ],
            "extraction_normalized_effective": [
                row.normalized.model_dump(mode="json")
                for row in attempt.input_snapshot.requirements
            ],
            "effective_requirements_at_matching": [
                row.requirement.model_dump(mode="json")
                for row in attempt.input_snapshot.requirements
            ],
            "matching_machine": attempt.model_dump(mode="json"),
            "comparison_validated": [
                comparison.model_dump(mode="json") for comparison in attempt.validated_comparison
            ],
            "human_actions_at_matching": attempt.review_snapshot,
        }
