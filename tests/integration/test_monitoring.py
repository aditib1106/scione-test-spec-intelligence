"""Private monitoring semantics without model calls, servers or secret payloads."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pymupdf
import pytest
from fastapi import FastAPI
from sqlalchemy import event, insert, update

from scione.api.monitoring import create_monitoring_router
from scione.api.workspace import Workspace
from scione.auth.models import AuthPrincipal, _principal
from scione.config import Settings
from scione.library.store import digest
from scione.matching.store import matching_runs, upgrade_matching_schema
from scione.monitoring import collect_metrics, finish_operation, start_operation
from scione.monitoring.store import MonitoringStore, operations, upgrade_monitoring_schema
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.store import ExtractionFailure


@pytest.fixture
def workspace(tmp_path):
    value = Workspace(
        Settings(_env_file=None, scione_workspace_dir=tmp_path, scione_database_url=None)
    )
    yield value
    value.library.store.close()


def seed(workspace):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 50), "Gloss: ISO 2813 at 60 degrees.")
        source = workspace.specs.import_pdf("private-filename.pdf", pdf.tobytes())
    run = CustomerExtractionRun.model_validate(
        {
            "run_id": str(uuid4()),
            "document_id": source.document_id,
            "started_at": datetime.now(UTC) - timedelta(hours=1),
            "schema_version": "0.1",
            "prompt_version": "offline_metrics_fixture",
            "provider": "offline",
            "model": "no_calls",
            "latency_ms": 5000,
            "usage": {},
            "raw_response": "PRIVATE_RAW_RESPONSE_NOT_A_METRIC",
            "extraction": {
                "schema_version": "0.1",
                "document": {"title": "Private source"},
                "requirements": [
                    {
                        "id": "req_gloss",
                        "original_test_name": "Gloss",
                        "requested_methods": [{"code": "ISO 2813"}],
                        "evidence": [{"page": 1, "quote": "Gloss: ISO 2813"}],
                    }
                ],
            },
        }
    )
    workspace.specs.save_extraction(source.document_id, run)
    workspace.library.index(source.document_id)
    return source, run


def metrics(workspace, **kwargs):
    return collect_metrics([("lab-a", "Lab A", workspace)], **kwargs)


def add_matching(workspace, source, run):
    identifier = uuid4().hex
    start = datetime.now(UTC) - timedelta(minutes=2)
    end = start + timedelta(seconds=23)
    upgrade_matching_schema(workspace.library.store)
    with workspace.library.store.engine.begin() as connection:
        connection.execute(
            insert(matching_runs).values(
                workspace_id=workspace.labs.workspace_id,
                run_id=identifier,
                document_id=source.document_id,
                extraction_run_id=run.run_id,
                input_fingerprint="offline",
                created_at=start.isoformat(),
                status="completed",
                provider="offline",
                model="no_calls",
                prompt_version="fixture",
                validation_version="fixture",
                payload={
                    "finished_at": end.isoformat(),
                    "latency_ms": 44000,
                    "planned_count": 8,
                    "raw_response": "PRIVATE_MATCHING_RESPONSE",
                    "input_snapshot": "PRIVATE_PROMPT",
                },
            )
        )
    return identifier


def test_empty_metrics_have_no_fabricated_durations_or_accuracy(workspace):
    result = metrics(workspace)
    assert result["summary"]["extraction"]["count"] == 0
    assert result["summary"]["matching"]["p95_ms"] is None
    assert result["summary"]["source_review"]["review_rate"] is None
    assert result["recent"] == []
    assert result["coverage"]["notices"]
    assert "accuracy" not in result["summary"]


def test_historical_provider_sum_is_not_operation_duration_and_outputs_are_private(workspace):
    source, run = seed(workspace)
    add_matching(workspace, source, run)
    result = metrics(workspace)
    assert result["summary"]["extraction"]["count"] == 1
    assert result["summary"]["extraction"]["median_ms"] is None
    assert result["summary"]["matching"]["median_ms"] == 23000
    assert result["summary"]["volume"] == {"documents": 1, "pages": 1, "tests": 1}
    extraction = next(row for row in result["recent"] if row["kind"] == "extraction")
    matching = next(row for row in result["recent"] if row["kind"] == "matching")
    assert extraction["provider_latency_ms"] == 5000
    assert extraction["duration_ms"] is None
    assert matching["provider_latency_ms"] == 44000
    assert matching["provider_calls"] is None  # planned count is not actual calls
    encoded = json.dumps(result)
    assert "PRIVATE_RAW_RESPONSE" not in encoded
    assert "PRIVATE_MATCHING_RESPONSE" not in encoded and "PRIVATE_PROMPT" not in encoded


def test_cache_reuse_never_erases_original_run_or_skews_latency(workspace):
    source, run = seed(workspace)
    matching_id = add_matching(workspace, source, run)
    store = MonitoringStore(workspace.library.store)
    identifier = store.start("matching", source.document_id)
    store.finish(
        identifier, status="cache_reuse", duration_ms=1, run_id=matching_id, provider_calls=0
    )
    result = metrics(workspace)
    assert result["summary"]["matching"]["count"] == 2
    assert result["summary"]["matching"]["success"] == 1
    assert result["summary"]["matching"]["cache_reuse"] == 1
    assert result["summary"]["matching"]["median_ms"] == 23000
    identifier = store.start("matching", source.document_id)
    store.finish(identifier, status="success", duration_ms=24000, run_id=matching_id)
    result = metrics(workspace)
    assert result["summary"]["matching"]["count"] == 2
    assert result["summary"]["matching"]["median_ms"] == 24000


def test_latest_comparison_per_document_and_review_burden(workspace):
    source, _run = seed(workspace)
    for status, review in (("fully_matched", "ready"), ("partially_matched", "needs_attention")):
        workspace.library.store.save_comparison(
            digest(status),
            {
                "document": {"document_id": source.document_id},
                "summary": {status: 1},
                "requirements": [{"source_review_state": review}],
            },
        )
    result = metrics(workspace)
    assert result["summary"]["outcomes"]["fully_matched"] == 0
    assert result["summary"]["outcomes"]["partially_matched"] == 1
    assert result["summary"]["source_review"] == {
        "needs_review": 1,
        "total_tests": 1,
        "review_rate": 1.0,
    }


def test_durable_failure_deduplicates_latest_failure_pointer(workspace):
    source, _run = seed(workspace)
    store = MonitoringStore(workspace.library.store)
    identifier = store.start("extraction", source.document_id)
    workspace.specs.save_failure(
        ExtractionFailure(
            document_id=source.document_id,
            occurred_at=datetime.now(UTC),
            provider="offline",
            model="no_calls",
            strategy="whole_document",
            output_limit=1000,
            message="PRIVATE_FAILURE_DETAIL",
        )
    )
    store.finish(identifier, status="failure", duration_ms=123)
    result = metrics(workspace)
    assert result["summary"]["extraction"]["count"] == 2
    assert result["summary"]["extraction"]["failure"] == 1
    assert result["summary"]["extraction"]["success"] == 1
    assert "PRIVATE_FAILURE_DETAIL" not in json.dumps(result)


def test_stale_unfinished_operations_are_not_failed_or_successful(workspace):
    store = MonitoringStore(workspace.library.store)
    identifier = store.start("matching", "a" * 16)
    with workspace.library.store.engine.begin() as connection:
        connection.execute(
            update(operations)
            .where(operations.c.id == identifier)
            .values(started_at=(datetime.now(UTC) - timedelta(hours=3)).isoformat())
        )
    result = metrics(workspace)
    assert result["summary"]["matching"]["incomplete"] == 1
    assert result["summary"]["matching"]["success"] == 0
    assert result["summary"]["matching"]["failure"] == 0


def test_small_limit_exposes_truncation_and_best_effort_never_raises(workspace, monkeypatch):
    store = MonitoringStore(workspace.library.store)
    for _ in range(3):
        identifier = store.start("extraction", "a" * 16)
        store.finish(identifier, status="success", duration_ms=10, page_count=2, test_count=4)
    assert metrics(workspace, limit=1)["coverage"]["truncated"] is True

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_ERROR")

    monkeypatch.setattr(MonitoringStore, "start", unavailable)
    assert start_operation(workspace, "extraction", "a" * 16) is None
    finish_operation(None, "failure")


def test_router_is_maintainer_only_even_without_external_middleware(workspace):
    app = FastAPI()
    app.include_router(create_monitoring_router(None, SimpleNamespace(), workspace))

    async def request(role, path):
        principal = AuthPrincipal("tester", "tester", "Tester", role, None, None) if role else None
        token = _principal.set(principal)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                return await client.get(path)
        finally:
            _principal.reset(token)

    assert asyncio.run(request(None, "/api/developer/metrics")).status_code == 401
    assert asyncio.run(request("lab", "/api/developer/metrics")).status_code == 403
    assert asyncio.run(request("maintainer", "/api/developer/metrics?scope=all")).status_code == 200
    assert asyncio.run(request("maintainer", "/api/developer/metrics?days=91")).status_code == 422
    assert asyncio.run(request("maintainer", "/api/developer/metrics?limit=201")).status_code == 422


def test_migration_is_additive_and_idempotent(workspace):
    upgrade_monitoring_schema(workspace.library.store)
    upgrade_monitoring_schema(workspace.library.store)
    identifier = MonitoringStore(workspace.library.store).start("matching", "a" * 16)
    assert identifier


def test_historical_json_is_projected_only_after_small_metadata_sort(workspace):
    source, run = seed(workspace)
    add_matching(workspace, source, run)
    workspace.library.store.save_comparison(
        digest("projection"),
        {
            "document": {"document_id": source.document_id},
            "summary": {"fully_matched": 1},
            "requirements": [],
        },
    )
    queries = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement.upper())

    event.listen(workspace.library.store.engine, "before_cursor_execute", capture)
    try:
        result = metrics(workspace)
    finally:
        event.remove(workspace.library.store.engine, "before_cursor_execute", capture)
    assert result["summary"]["matching"]["count"] == 1
    assert result["summary"]["outcomes"]["fully_matched"] == 1
    assert any("JSON_EXTRACT" in statement for statement in queries)
    # MySQL retains a multi-MB JSON argument during filesort even for a scalar
    # projection. Metadata sorting and JSON projection must remain separate.
    assert not any("ORDER BY" in statement and "PAYLOAD" in statement for statement in queries)


def test_unavailable_history_is_not_a_complete_zero_activity_claim(workspace, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_DATABASE_DETAIL")

    monkeypatch.setattr("scione.monitoring.collector._historical", unavailable)
    result = metrics(workspace)
    assert result["coverage"]["failure_history_complete"] is False
    assert result["coverage"]["unavailable_lab_count"] == 1
    assert "PRIVATE_DATABASE_DETAIL" not in json.dumps(result)
    assert any("temporarily unavailable" in notice for notice in result["coverage"]["notices"])
