"""A paid operation owns a document until success or failure releases it."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from scione.api.inference_gate import InferenceBusyError, InferenceGate


def test_document_gate_blocks_other_stages_but_not_other_documents():
    gate = InferenceGate()
    with gate.claim("document_one", "analysis"):
        with pytest.raises(InferenceBusyError, match="already running"):
            with gate.claim("document_one", "matching"):
                pytest.fail("A conflicting paid operation must not start.")
        with gate.claim("document_two", "matching"):
            pass
    with gate.claim("document_one", "matching"):
        pass


def test_failure_releases_document_without_queuing_or_retry():
    gate = InferenceGate()
    with pytest.raises(RuntimeError, match="failed request"):
        with gate.claim("document_one", "analysis"):
            raise RuntimeError("failed request")
    with gate.claim("document_one", "analysis"):
        pass


def test_hosted_limits_are_per_lab_and_global_without_exposing_other_lab_names():
    gate = InferenceGate(max_concurrent=2, max_per_workspace=1)
    with gate.claim("lab_a:one", "analysis", workspace_id="lab_a"):
        with pytest.raises(InferenceBusyError, match="your lab"):
            with gate.claim("lab_a:two", "matching", workspace_id="lab_a"):
                pytest.fail("One lab must not queue another paid operation.")
        with gate.claim("lab_b:one", "analysis", workspace_id="lab_b"):
            with pytest.raises(InferenceBusyError, match="service is busy") as caught:
                with gate.claim("lab_c:one", "matching", workspace_id="lab_c"):
                    pytest.fail("The global service limit must be enforced.")
            assert "lab_a" not in str(caught.value) and "lab_b" not in str(caught.value)
    with gate.claim("lab_a:two", "matching", workspace_id="lab_a"):
        pass


def test_simultaneous_request_is_rejected_instead_of_waiting_for_a_new_call():
    gate = InferenceGate()
    entered, release = Event(), Event()

    def first_request():
        with gate.claim("document_one", "matching"):
            entered.set()
            assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(first_request)
        try:
            assert entered.wait(timeout=5)
            with pytest.raises(InferenceBusyError):
                with gate.claim("document_one", "analysis"):
                    pytest.fail("No queued inference should start.")
        finally:
            release.set()
        first.result(timeout=5)
