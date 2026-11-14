"""Join explicit matching requests, saved attempts and deterministic comparisons."""

from fastapi import HTTPException

from scione.inference_progress import ProgressReporter
from scione.library.store import digest
from scione.matching import build_matching_input, compile_matching_run
from scione.matching.batching import (
    MATCHING_BATCH_POLICY_VERSION,
    MAX_LONG_DOCUMENT_MATCHING_BATCH_CALLS,
    MAX_MATCHING_BATCH_CALLS,
    run_bounded_matching,
)
from scione.matching.projection import MATCHING_PROJECTION_VERSION
from scione.matching.retrieval import (
    DEFAULT_REQUEST_METHOD_LIMIT,
    RETRIEVAL_POLICY_VERSION,
    RetrievalLimitError,
    retrieve_identity_candidates,
)
from scione.matching.service import SemanticMatchingError, input_fingerprint
from scione.matching.store import SQLMatchingStore
from scione.parameters import REGISTRY_VERSION, normalization_evidence
from scione.reports.matching import (
    _method_resolution,
    _selected_methods,
    _source_permitted_alternative_method,
    lab_fingerprint,
)
from scione.specifications.conditional_notes import conditional_route_proofs


class MatchingWorkspace:
    def __init__(self, workspace):
        self.workspace = workspace
        self.store = SQLMatchingStore(workspace.library.store, workspace.labs.workspace_id)

    def snapshot(self, report):
        return self._snapshot_and_retrieval(report)[0]

    def _snapshot_and_retrieval(self, report):
        try:
            retrieved = [
                retrieve_identity_candidates(
                    line.requirement,
                    report.catalog,
                    preferred_test_ids=list(
                        dict.fromkeys(
                            [
                                *([line.canonical_test_id] if line.canonical_test_id else []),
                                *line.candidate_test_ids,
                            ]
                        )
                    ),
                    pinned_test_id=(
                        line.mapping_choice.canonical_test_id if line.mapping_choice else None
                    ),
                    human_reviewed=line.mapping_choice is not None,
                )
                for line in report.lines
            ]
        except RetrievalLimitError as exc:
            raise HTTPException(422, str(exc)) from exc
        choices = {row.requirement_id: list(row.test_ids) for row in retrieved}
        capability_choices = {}
        for line in report.lines:
            allowed = {
                cap.id
                for cap in report.lab.capabilities
                if cap.canonical_test_id in choices[line.requirement.id]
            }
            if line.mapping_choice is not None:
                allowed &= {line.mapping_choice.capability_id}
            else:
                considered = getattr(line, "considered_method_codes", None)
                methods = [
                    method
                    for method in line.requirement.requested_methods
                    if considered is None or method.code in considered
                ]
                resolved = [_method_resolution(method, report.catalog) for method in methods]
                if resolved and all(identifier and not issue for identifier, issue in resolved):
                    requested_ids = {identifier for identifier, _ in resolved}
                    exact = {
                        cap.id
                        for cap in report.lab.capabilities
                        if cap.id in allowed and requested_ids <= set(cap.method_ids)
                    }
                    # Retain every configuration for the exact method. Only
                    # when none exists do other-method entries remain useful
                    # diagnostics; they never establish equivalence.
                    if exact:
                        allowed = exact
                    else:
                        primary_ids = {
                            identifier
                            for method, (identifier, issue) in zip(methods, resolved, strict=True)
                            if identifier and not issue and method.role in {"primary", "required"}
                        }
                        primary = {
                            cap.id
                            for cap in report.lab.capabilities
                            if cap.id in allowed
                            and primary_ids
                            and primary_ids <= set(cap.method_ids)
                        }
                        relevant = {
                            cap.id
                            for cap in report.lab.capabilities
                            if cap.id in allowed and requested_ids.intersection(cap.method_ids)
                        }
                        # Retrieval does not decide which alternative applies.
                        # The method gate still preserves all original refs and
                        # review issues; unrelated family services add no useful
                        # evidence to a known primary/composite request.
                        if primary or relevant:
                            allowed = primary or relevant
                elif not methods:
                    from scione.catalogs.normalization import is_source_defined_procedure

                    if not is_source_defined_procedure(line.requirement, report.catalog):
                        # An uncoded standard-like request cannot certify method
                        # coverage. Keep a bounded pair of explicit services for
                        # diagnostic comparisons until the user chooses a method.
                        allowed = set(sorted(allowed)[:2])
            capability_choices[line.requirement.id] = allowed
        allowed_capabilities = set().union(*capability_choices.values())
        scoped_lab = report.lab.model_copy(
            update={
                "capabilities": [
                    cap for cap in report.lab.capabilities if cap.id in allowed_capabilities
                ]
            }
        )
        method_ids = {identifier for row in retrieved for identifier in row.method_ids}
        method_ids.update(
            identifier for cap in scoped_lab.capabilities for identifier in cap.method_ids
        )
        if len(method_ids) > DEFAULT_REQUEST_METHOD_LIMIT:
            raise HTTPException(
                422,
                "The selected source and lab identities exceed the method metadata budget. "
                "Review the candidates or split the matching request; no method was dropped.",
            )
        retrieval = {
            "policy_version": RETRIEVAL_POLICY_VERSION,
            "capability_policy": "primary_composite_and_uncoded_candidates_v2",
            "catalog_test_count": len(report.catalog.tests),
            "catalog_method_count": len(report.catalog.methods),
            "transmitted_method_count": len(method_ids),
            "request_method_limit": DEFAULT_REQUEST_METHOD_LIMIT,
            "requirements": [row.diagnostics for row in retrieved],
        }
        workspace = getattr(self, "workspace", None)
        if workspace is None:
            # Pure/offline retrieval probes have no physical-document
            # authority. Preserve source facts and use the ordinary strict
            # normalizer; do not infer an externally printed unit.
            source_view, normalized_source = report.source_run, None
        else:
            source_view, normalized_source = workspace.comparison_source(
                report.document_id, report.source_run, native_first=True
            )
        snapshot = build_matching_input(
            source_view,
            report.catalog,
            scoped_lab,
            capability_revision=lab_fingerprint(report.lab),
            review_revision=digest(
                [
                    REGISTRY_VERSION,
                    MATCHING_PROJECTION_VERSION,
                    MATCHING_BATCH_POLICY_VERSION,
                    retrieval,
                    report.source_facts_revision_id,
                    report.source_review.model_dump(mode="json"),
                    {
                        key: value.model_dump(mode="json")
                        for key, value in report.workspace.mappings.items()
                    },
                ]
            ),
            candidate_test_ids=choices,
            candidate_method_ids=sorted(method_ids),
            normalized_requirements=normalized_source,
        )
        # A global catalog snapshot can be shared across requests, but each
        # row retains its own allowed capability IDs, including human pins.
        for row in snapshot.requirements:
            row.capability_ids = [
                identifier
                for identifier in row.capability_ids
                if identifier in capability_choices[row.requirement.id]
            ]
        # Revalidate the detached projection before it becomes a run artifact.
        return type(snapshot).model_validate(snapshot.model_dump(mode="json")), retrieval

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

    def run(self, document_id: str, body, provider, *, on_progress=None) -> dict:
        activity = ProgressReporter(on_progress)
        activity.emit("planning")
        report = self.workspace.draft(document_id)
        if (
            report.source_run.run_id != body.run_id
            or report.input_fingerprint != body.input_fingerprint
        ):
            raise HTTPException(409, "Inputs changed. Reload the report before AI matching.")
        snapshot, retrieval = self._snapshot_and_retrieval(report)
        reviews = {
            "source_review": report.source_review.model_dump(mode="json"),
            "report_workspace": report.workspace.model_dump(mode="json"),
            "source_facts_revision_id": report.source_facts_revision_id,
        }
        machine_source = self.workspace.specs.load_extraction(document_id, report.source_run.run_id)
        policies = {
            "source_review_audits": self.workspace.source_review_audits(
                document_id, report.source_run
            ),
            "verified_optional_route_proofs": conditional_route_proofs(
                report.source_run,
                self.workspace.current_document(document_id).document,
            ),
            "candidate_retrieval": retrieval,
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
                item.model_dump(mode="json")
                for item in self.workspace.comparison_source(document_id, machine_source)[1]
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
            activity.emit("validating")
            return {"run_id": cached.run_id, "status": cached.status, "cached": True}
        try:
            attempt = run_bounded_matching(
                provider,
                snapshot,
                max_input_characters=self.workspace.settings.scione_matching_input_character_limit,
                max_concurrent_batches=(
                    self.workspace.settings.scione_long_document_parallelism
                    if self.workspace.current_document(document_id).document.page_count > 10
                    else 2
                ),
                max_calls=(
                    MAX_LONG_DOCUMENT_MATCHING_BATCH_CALLS
                    if self.workspace.current_document(document_id).document.page_count > 10
                    else MAX_MATCHING_BATCH_CALLS
                ),
                on_progress=activity.forward if on_progress is not None else None,
            )
        except SemanticMatchingError as exc:
            exc.run.review_snapshot = reviews
            exc.run.policy_snapshot = {**exc.run.policy_snapshot, **policies}
            activity.emit("saving")
            self.store.save(document_id, exc.run)
            raise HTTPException(
                502,
                {
                    "message": str(exc),
                    "run_id": exc.run.run_id,
                    "error_code": exc.run.error_code,
                    "provider_failure_kind": exc.run.provider_failure_kind,
                    "provider_http_status": exc.run.provider_http_status,
                },
            ) from exc
        attempt.review_snapshot = reviews
        attempt.policy_snapshot = {**attempt.policy_snapshot, **policies}
        activity.emit("validating")
        comparisons, _selected = self._compile(report, attempt)
        attempt.validated_comparison = comparisons
        activity.emit("saving")
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
        method_decisions = {}
        for line in report.lines:
            proposal = proposals[line.requirement.id]
            capability = capabilities.get(proposal.capability_id)
            selected[line.requirement.id] = capability
            status = "unknown"
            if (
                proposal.identity_status == "unknown"
                and proposal.canonical_test_id is None
                and capability is None
                and line.status == "test_gap"
                and line.match_basis == "Exact curated name / alias"
                and line.canonical_test_id is not None
                and line.considered_method_codes
                and not line.unknown_methods
                and not any(
                    cap.canonical_test_id == line.canonical_test_id
                    for cap in report.lab.capabilities
                )
            ):
                # An optional semantic abstention cannot erase an exact,
                # unambiguous method/test absence in this lab's declaration.
                # This means "not listed", never "physically impossible".
                status = "gap"
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
                _source_methods, method_issues = _selected_methods(
                    line.requirement, line.mapping_choice
                )
                methods = [
                    method
                    for method in line.requirement.requested_methods
                    if method.code in line.considered_method_codes
                ]
                route = _source_permitted_alternative_method(
                    line.requirement, capability, report.catalog, line.mapping_choice
                )
                if route is not None:
                    methods, method_issues = [route], []
                    method_decisions[line.requirement.id] = {
                        "rule": "source_permitted_registered_alternative",
                        "selected_method": route.code,
                        "retained_original_references": [
                            method.code for method in line.requirement.requested_methods
                        ],
                        "establishes_method_equivalence": False,
                    }
                resolved = [_method_resolution(method, report.catalog) for method in methods]
                if method_issues:
                    # An unresolved OR/composite relationship is not evidence
                    # that this lab lacks every named method. It must not turn
                    # a selected single-service declaration into a false gap.
                    status = "unknown"
                    method_decisions[line.requirement.id] = {
                        "rule": "unresolved_source_method_relationship",
                        "source_issues": method_issues,
                        "retained_original_references": [method.code for method in methods],
                        "establishes_method_equivalence": False,
                    }
                elif methods and not any(reason for _, reason in resolved):
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
        for comparison in comparisons:
            decision = method_decisions.get(comparison.requirement_id)
            if decision is None:
                continue
            method_check = comparison.checks[0]
            method_check.comparison_metadata.append(decision)
            method_check.reason = (
                "The source explicitly permits either reference; the selected lab service "
                f"registers {decision['selected_method']}. Original alternatives remain "
                "recorded and have not been declared equivalent."
                if decision["rule"] == "source_permitted_registered_alternative"
                else "The source contains alternative or combined method references whose "
                "relationship still needs review; a missing method has not been established."
            )
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
                item.model_dump(mode="json")
                for item in self.workspace.comparison_source(document_id, source)[1]
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
