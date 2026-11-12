"""Bounded job receipts survive HTTP disconnects without replaying inference."""

from contextvars import ContextVar
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi import HTTPException

from scione.api.inference_gate import InferenceBusyError, InferenceGate
from scione.api.inference_jobs import InferenceJobs, _public_failure


def wait_result(jobs, identifier, workspace="lab-a"):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        result = jobs.status(identifier, workspace_id=workspace)
        if result["state"] != "running":
            return result
        sleep(0.005)
    pytest.fail("The bounded offline job did not finish.")


def submit(jobs, callback, *, lab="lab-a", doc="doc-a", request_id=None, digest="inputs"):
    return jobs.submit(
        document_id=doc, key=f"{lab}:{doc}", kind="extraction", workspace_id=lab,
        input_digest=digest, client_request_id=request_id, callback=callback,
    )


def test_admission_reserves_lease_and_context_before_return_without_waiting_for_work():
    gate, started, release = InferenceGate(), Event(), Event()
    jobs = InferenceJobs(gate)
    principal = ContextVar("test_job_principal", default=None)
    token = principal.set("original-account")
    calls = []

    def run(progress):
        calls.append(principal.get())
        progress({"stage": "extracting", "completed_steps": 1, "total_steps": 2,
                  "message": "PRIVATE SOURCE", "percent": 999})
        started.set()
        assert release.wait(3)
        calls.append(principal.get())
        return {"run": {"run_id": "original-run", "raw_response": "PRIVATE MODEL"}}

    try:
        request_id = str(uuid4())
        receipt = submit(jobs, run, request_id=request_id)
        assert receipt["state"] == "running" and started.wait(1)
        identifier = receipt["operation_id"]
        assert gate.snapshot(workspace_id="lab-a")[0]["operation_id"] == identifier
        principal.set("other-account")
        active = jobs.status(identifier, workspace_id="lab-a")
        assert active["progress"]["percent"] == 50
        assert active["request_id"] == request_id and "PRIVATE" not in str(active)
        active["progress"]["completed_steps"] = 999
        assert jobs.status(identifier, workspace_id="lab-a")["progress"]["completed_steps"] == 1
        same = submit(jobs, run, request_id=request_id)
        assert same["operation_id"] == identifier and calls == ["original-account"]
        with pytest.raises(HTTPException) as changed:
            submit(jobs, run, request_id=request_id, digest="other-inputs")
        assert changed.value.status_code == 409
        with pytest.raises(InferenceBusyError):
            submit(jobs, run, doc="other-doc")
        release.set()
        final = wait_result(jobs, identifier)
        assert final["result"] == {"run_id": "original-run", "status": "completed",
                                   "cached": False}
        assert final["result_url"] == "/api/specifications/doc-a"
        assert calls == ["original-account", "original-account"]
        assert gate.snapshot(workspace_id="lab-a") == []
        replay = submit(jobs, run, request_id=request_id)
        assert replay["operation_id"] == identifier and len(calls) == 2
    finally:
        release.set()
        jobs.shutdown()
        principal.reset(token)


def test_capacity_has_no_queue_and_foreign_tenant_receipts_are_not_visible():
    gate, release = InferenceGate(), Event()
    jobs = InferenceJobs(gate)

    def run(_progress):
        assert release.wait(3)
        return {"run_id": "saved-run"}

    try:
        a = submit(jobs, run)
        b = submit(jobs, run, lab="lab-b")
        with pytest.raises(InferenceBusyError):
            submit(jobs, run, lab="lab-c")
        with pytest.raises(HTTPException) as forbidden:
            jobs.status(a["operation_id"], workspace_id="lab-b")
        assert forbidden.value.status_code == 404
        assert jobs.latest("doc-a", workspace_id="lab-c") is None
        release.set()
        assert wait_result(jobs, a["operation_id"])["state"] == "completed"
        assert wait_result(jobs, b["operation_id"], "lab-b")["state"] == "completed"
    finally:
        release.set()
        jobs.shutdown()


@pytest.mark.parametrize("failure", [HTTPException(502, {"message": "PRIVATE SOURCE KEY"}),
                                   RuntimeError("PRIVATE DATABASE PASSWORD"), SystemExit()])
def test_failure_is_sanitized_and_cannot_poison_the_next_admitted_job(failure):
    gate = InferenceGate()
    jobs = InferenceJobs(gate)

    def fail(_progress):
        raise failure

    try:
        receipt = submit(jobs, fail)
        result = wait_result(jobs, receipt["operation_id"])
        assert result["state"] == "failed"
        assert result["error"]["status"] == (502 if isinstance(failure, HTTPException) else 500)
        assert "PRIVATE" not in str(result) and "result" not in result
        assert gate.snapshot(workspace_id="lab-a") == []
        next_job = submit(jobs, lambda _: {"run_id": "next-run"})
        assert wait_result(jobs, next_job["operation_id"])["state"] == "completed"
    finally:
        jobs.shutdown()


def test_finished_records_have_bounded_count_ttl_and_closed_admission():
    jobs = InferenceJobs(InferenceGate(), max_finished=2)
    receipts = []
    for _index in range(3):
        receipt = submit(jobs, lambda _: {"run_id": "saved-run"})
        receipts.append(receipt)
        wait_result(jobs, receipt["operation_id"])
    with pytest.raises(HTTPException) as oldest:
        jobs.status(receipts[0]["operation_id"], workspace_id="lab-a")
    assert oldest.value.status_code == 404
    jobs.ttl_seconds = 0
    with pytest.raises(HTTPException) as expired:
        jobs.status(receipts[-1]["operation_id"], workspace_id="lab-a")
    assert expired.value.status_code == 404
    jobs.shutdown()
    with pytest.raises(HTTPException) as closed:
        submit(jobs, lambda _: {"run_id": "not-started"})
    assert closed.value.status_code == 503


def test_submission_failure_and_invalid_request_id_never_leave_a_lease(monkeypatch):
    gate, jobs = InferenceGate(), None
    jobs = InferenceJobs(gate)
    try:
        with pytest.raises(HTTPException) as invalid:
            submit(jobs, lambda _: {}, request_id="not-a-uuid")
        assert invalid.value.status_code == 400

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("executor unavailable")

        monkeypatch.setattr(jobs._executor, "submit", unavailable)
        with pytest.raises(RuntimeError):
            submit(jobs, lambda _: {"run_id": "not-started"})
        assert gate.snapshot(workspace_id="lab-a") == []
        assert jobs.latest("doc-a", workspace_id="lab-a") is None
    finally:
        jobs.shutdown()


@pytest.mark.parametrize("detail", [
    {"provider_failure_kind": "timeout", "message": "PRIVATE SOURCE KEY"},
    "Test inventory failed: Sail request timed out; no automatic retry was made. PRIVATE",
])
def test_known_timeout_cause_uses_fixed_label_without_echoing_original_detail(detail):
    error = _public_failure(HTTPException(502, detail))
    assert error["status"] == 502 and error["failure_kind"] == "timeout"
    assert "AI service timed out" in error["detail"] and "PRIVATE" not in str(error)


def test_unknown_prose_or_provider_kind_cannot_become_a_trusted_failure_cause():
    for detail in ["timeout PRIVATE SQL", {"provider_failure_kind": "PRIVATE SOURCE"}]:
        error = _public_failure(HTTPException(400, detail))
        assert "failure_kind" not in error and "PRIVATE" not in str(error)
