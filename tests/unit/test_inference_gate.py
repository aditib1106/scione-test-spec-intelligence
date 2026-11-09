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
