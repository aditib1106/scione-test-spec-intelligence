"""Original PDFs, immutable model runs, and human decisions survive local reloads."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import pytest
from pydantic import ValidationError

from scione.providers import TokenUsage
from scione.schemas import TextExtractionStatus
from scione.specifications.models import CustomerExtraction, CustomerExtractionRun
from scione.specifications.store import (
    MAX_PDF_BYTES,
    ExtractionFailure,
    FileSpecStore,
    SpecStoreError,
    SpecVersionConflict,
)


def make_pdf(*, text: bool = True, pages: int = 1, encrypted: bool = False) -> bytes:
    with pymupdf.open() as pdf:
        for _ in range(pages):
            page = pdf.new_page()
            if text:
                page.insert_textbox(
                    pymupdf.Rect(40, 40, 550, 400),
                    "Pencil hardness ISO 15184. Apply a 750 g load. Acceptance: at least F.\n"
                    "The customer requests testing for the exterior coating on steel panels.\n"
                    "Retain both the requested conditions and source information for review.",
                )
        if encrypted:
            return pdf.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="owner",
                user_pw="secret",
            )
        return pdf.tobytes()


def make_run(document_id: str, run_id: str = "run_one") -> CustomerExtractionRun:
    return CustomerExtractionRun(
        run_id=run_id,
        started_at=datetime.now(UTC),
        document_id=document_id,
        prompt_version="customer_requirements_v0.1",
        schema_version="0.1",
        provider="offline_test",
        model="no_network",
        latency_ms=1,
        usage=TokenUsage(),
        raw_response="original model output remains unchanged",
        extraction=CustomerExtraction.model_validate(
            {
                "schema_version": "0.1",
                "document": {"title": "Customer spec"},
                "requirements": [
                    {
                        "id": "req_pencil",
                        "original_test_name": "Pencil hardness",
                        "requested_methods": [{"code": "ISO 15184"}],
                        "evidence": [{"page": 1, "quote": "Pencil hardness ISO 15184."}],
                    }
                ],
            }
        ),
    )


def make_failure(
    document_id: str, previous_run_id: str | None = None, **changes: object
) -> ExtractionFailure:
    return ExtractionFailure.model_validate(
        {
            "document_id": document_id,
            "occurred_at": datetime.now(UTC),
            "provider": "offline_test",
            "model": "no_network",
            "strategy": "whole_document",
            "output_limit": 6000,
            "message": "Output token limit reached. Try a larger output limit.",
            "previous_run_id": previous_run_id,
            **changes,
        }
    )


def test_constructor_no_io_and_import_preserves_exact_pdf(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    store = FileSpecStore(root)
    assert not root.exists()
    assert store.list_documents() == []
    assert not root.exists()
    data = make_pdf()
    saved = store.import_pdf("customer.pdf", data)
    assert saved.document_id == hashlib.sha256(data).hexdigest()[:16]
    assert saved.original_name == "customer.pdf"
    assert saved.document.file_name == "customer.pdf"
    assert saved.document.extraction_status == TextExtractionStatus.READY
    assert saved.created_at.tzinfo is not None
    reloaded = FileSpecStore(root).load_document(saved.document_id)
    assert reloaded == saved
    assert store.read_pdf(saved.document_id) == data
    assert saved.pdf_path.read_bytes() == data
    summary = store.list_documents()[0]
    assert summary.document_id == saved.document_id
    assert summary.latest_run_id is None
    assert summary.requirement_count == 0
    assert summary.confirmed_count == 0


def test_repeated_pdf_bytes_deduplicate_and_keep_first_filename(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    data = make_pdf()
    first = store.import_pdf("first.pdf", data)
    second = store.import_pdf("second.pdf", data)
    assert first == second
    assert len(store.list_documents()) == 1
    assert store.read_pdf(first.document_id) == data


def test_blank_pdf_and_short_text_remain_saved_with_ingestion_warnings(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    blank = store.import_pdf("scanned.pdf", make_pdf(text=False))
    assert blank.document.extraction_status == TextExtractionStatus.EMPTY
    assert blank.document.warnings
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "A short spec.")
        short = store.import_pdf("short.pdf", pdf.tobytes())
    assert short.document.extraction_status == TextExtractionStatus.POSSIBLE_SCANNED_PDF
    assert "short spec" in short.document.pages[0].text


@pytest.mark.parametrize("name", ["../outside.pdf", "/tmp/outside.pdf", "a\\b.pdf", "a.txt", ""])
def test_unsafe_names_do_not_create_store(tmp_path: Path, name: str) -> None:
    store = FileSpecStore(tmp_path / "data")
    with pytest.raises(SpecStoreError):
        store.import_pdf(name, make_pdf())
    assert not store.root.exists()


@pytest.mark.parametrize("data", [b"", b"not a PDF", b"%PDF-malformed", b"x" * (MAX_PDF_BYTES + 1)])
def test_invalid_or_oversized_pdf_rejected_without_artifacts(tmp_path: Path, data: bytes) -> None:
    store = FileSpecStore(tmp_path)
    with pytest.raises(SpecStoreError):
        store.import_pdf("spec.pdf", data)
    assert not store.root.exists()


def test_encrypted_and_too_many_pages_rejected_before_import(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    with pytest.raises(SpecStoreError, match="Encrypted"):
        store.import_pdf("encrypted.pdf", make_pdf(encrypted=True))
    with pytest.raises(SpecStoreError, match="200 pages"):
        store.import_pdf("too-many.pdf", make_pdf(text=False, pages=201))
    assert not store.root.exists()


@pytest.mark.parametrize("identifier", ["../outside", "", "a" * 64, "../../one"])
def test_invalid_document_ids_cannot_traverse_storage(tmp_path: Path, identifier: str) -> None:
    with pytest.raises(SpecStoreError):
        FileSpecStore(tmp_path).load_document(identifier)


def test_corrupt_metadata_is_reported_not_replaced_or_hidden(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    data = make_pdf()
    saved = store.import_pdf("spec.pdf", data)
    path = saved.pdf_path.parent / "metadata.json"
    path.write_text("{broken JSON")
    for action in [
        lambda: store.load_document(saved.document_id),
        lambda: store.import_pdf("duplicate.pdf", data),
        store.list_documents,
    ]:
        with pytest.raises(SpecStoreError):
            action()
    assert path.read_text() == "{broken JSON"
    assert saved.pdf_path.read_bytes() == data


def test_tampered_original_pdf_is_detected(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    saved.pdf_path.write_bytes(b"changed")
    with pytest.raises(SpecStoreError, match="inconsistent"):
        store.read_pdf(saved.document_id)


def test_symlinked_storage_root_and_artifact_are_rejected(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(SpecStoreError, match="symlink"):
        FileSpecStore(link).import_pdf("spec.pdf", make_pdf())
    store = FileSpecStore(actual)
    saved = store.import_pdf("spec.pdf", make_pdf())
    metadata = saved.pdf_path.parent / "metadata.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(metadata.read_bytes())
    metadata.unlink()
    metadata.symlink_to(outside)
    with pytest.raises(SpecStoreError, match="symlink"):
        store.load_document(saved.document_id)
    assert outside.is_file()


def test_parent_traversal_in_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SpecStoreError, match="traversal"):
        FileSpecStore(tmp_path / "nested" / ".." / "data").import_pdf("spec.pdf", make_pdf())


def test_runs_are_immutable_latest_moves_and_review_is_separate(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    run = make_run(saved.document_id)
    store.save_extraction(saved.document_id, run)
    run_path = saved.pdf_path.parent / "runs" / run.run_id / "run.json"
    before = run_path.read_bytes()
    assert FileSpecStore(tmp_path).load_latest_extraction(saved.document_id) == run
    with pytest.raises(SpecStoreError, match="immutable"):
        store.save_extraction(saved.document_id, run)
    review = store.load_review(saved.document_id, run.run_id)
    assert review.version == 0
    assert not review.decisions
    confirmed = store.save_review(
        saved.document_id,
        run.run_id,
        "req_pencil",
        "confirmed",
        "Checked against page 1",
        expected_version=0,
    )
    assert confirmed.version == 1
    assert confirmed.decisions["req_pencil"].note == "Checked against page 1"
    assert FileSpecStore(tmp_path).load_review(saved.document_id, run.run_id) == confirmed
    assert run_path.read_bytes() == before
    assert store.load_extraction(saved.document_id, run.run_id).raw_response == run.raw_response
    summary = store.list_documents()[0]
    assert summary.confirmed_count == 1
    assert summary.requirement_count == 1
    second = make_run(saved.document_id, "run_two")
    store.save_extraction(saved.document_id, second)
    assert store.load_latest_extraction(saved.document_id) == second
    assert store.load_review(saved.document_id, second.run_id).version == 0
    assert store.load_review(saved.document_id, run.run_id) == confirmed
    assert store.list_documents()[0].confirmed_count == 0


def test_raw_model_whitespace_survives_json_and_text_storage_exactly(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    raw = '\n  {"unmodified": "original model response"} \r\n'
    run = CustomerExtractionRun.model_validate(
        {**make_run(saved.document_id).model_dump(), "raw_response": raw}
    )

    store.save_extraction(saved.document_id, run)

    assert store.load_extraction(saved.document_id, run.run_id).raw_response == raw
    assert store.load_latest_extraction(saved.document_id).raw_response == raw
    raw_file = saved.pdf_path.parent / "runs" / run.run_id / "raw_response.txt"
    assert raw_file.read_bytes() == raw.encode("utf-8")


def test_wrong_doc_traversal_unknown_requirement_and_stale_review_are_rejected(
    tmp_path: Path,
) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    run = make_run(saved.document_id)
    with pytest.raises(SpecStoreError, match="different"):
        store.save_extraction(saved.document_id, run.model_copy(update={"document_id": "other"}))
    with pytest.raises(SpecStoreError, match="run ID"):
        store.save_extraction(saved.document_id, run.model_copy(update={"run_id": "../outside"}))
    store.save_extraction(saved.document_id, run)
    with pytest.raises(SpecStoreError, match="Unknown"):
        store.save_review(saved.document_id, run.run_id, "req_unknown", "confirmed")
    first = store.save_review(saved.document_id, run.run_id, "req_pencil", "needs_review")
    with pytest.raises(SpecVersionConflict, match="Reload"):
        store.save_review(
            saved.document_id, run.run_id, "req_pencil", "confirmed", expected_version=0
        )
    assert store.load_review(saved.document_id, run.run_id) == first


def test_corrupt_latest_pointer_and_review_are_not_reset(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    run = make_run(saved.document_id)
    store.save_extraction(saved.document_id, run)
    review_path = saved.pdf_path.parent / "runs" / run.run_id / "review.json"
    review_path.write_text("not valid JSON")
    with pytest.raises(SpecStoreError):
        store.save_review(saved.document_id, run.run_id, "req_pencil", "confirmed")
    assert review_path.read_text() == "not valid JSON"
    pointer = saved.pdf_path.parent / "latest_run.json"
    pointer.write_text(json.dumps({"run_id": "../outside"}))
    with pytest.raises(SpecStoreError):
        store.save_extraction(saved.document_id, make_run(saved.document_id, "second"))
    assert not (saved.pdf_path.parent / "runs" / "second").exists()


def test_failed_review_atomic_replace_preserves_previous_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    run = make_run(saved.document_id)
    store.save_extraction(saved.document_id, run)
    original = store.save_review(saved.document_id, run.run_id, "req_pencil", "needs_review")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("scione.specifications.store.os.replace", fail_replace)
    with pytest.raises(SpecStoreError, match="simulated"):
        store.save_review(saved.document_id, run.run_id, "req_pencil", "confirmed")
    assert store.load_review(saved.document_id, run.run_id) == original
    assert not (store.root / ".write.lock").exists()


def test_requirement_can_be_excluded_then_restored_without_changing_model_artifacts(
    tmp_path: Path,
) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    run = make_run(saved.document_id)
    store.save_extraction(saved.document_id, run)
    directory = saved.pdf_path.parent / "runs" / run.run_id
    originals = {
        name: (directory / name).read_bytes()
        for name in ("run.json", "prediction.json", "raw_response.txt")
    }

    excluded = store.save_review(
        saved.document_id,
        run.run_id,
        "req_pencil",
        "excluded",
        "This item is outside this order.",
        expected_version=0,
    )

    assert excluded.version == 1
    assert excluded.decisions["req_pencil"].status == "excluded"
    assert excluded.decisions["req_pencil"].note == "This item is outside this order."
    reopened = FileSpecStore(tmp_path)
    assert reopened.load_review(saved.document_id, run.run_id) == excluded
    summary = reopened.list_documents()[0]
    assert summary.requirement_count == 1
    assert summary.confirmed_count == summary.needs_review_count == 0

    restored = reopened.save_review(
        saved.document_id,
        run.run_id,
        "req_pencil",
        "confirmed",
        "Included after checking the customer request.",
        expected_version=excluded.version,
    )

    assert restored.version == 2
    assert restored.decisions["req_pencil"].status == "confirmed"
    assert FileSpecStore(tmp_path).load_review(saved.document_id, run.run_id) == restored
    assert reopened.list_documents()[0].confirmed_count == 1
    for name, original in originals.items():
        assert (directory / name).read_bytes() == original


def test_failure_without_success_survives_fresh_store_without_inventing_result(
    tmp_path: Path,
) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    assert store.load_failure(saved.document_id) is None
    original_pdf = saved.pdf_path.read_bytes()
    failure = make_failure(saved.document_id)

    store.save_failure(failure)

    reopened = FileSpecStore(tmp_path)
    assert reopened.load_failure(saved.document_id) == failure
    assert reopened.load_latest_extraction(saved.document_id) is None
    summary = reopened.list_documents()[0]
    assert summary.latest_run_id is None
    assert summary.requirement_count == summary.confirmed_count == 0
    assert saved.pdf_path.read_bytes() == original_pdf
    assert not (saved.pdf_path.parent / "runs").exists()
    assert not (saved.pdf_path.parent / "latest_run.json").exists()


def test_failure_keeps_success_and_review_and_previous_run_pointer_after_new_success(
    tmp_path: Path,
) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    first = make_run(saved.document_id)
    store.save_extraction(saved.document_id, first)
    review = store.save_review(saved.document_id, first.run_id, "req_pencil", "confirmed")
    directory = saved.pdf_path.parent / "runs" / first.run_id
    originals = {
        name: (directory / name).read_bytes()
        for name in ("run.json", "prediction.json", "raw_response.txt", "review.json")
    }
    pointer = saved.pdf_path.parent / "latest_run.json"
    original_pointer = pointer.read_bytes()
    failure = make_failure(saved.document_id, previous_run_id=first.run_id)

    store.save_failure(failure)

    reopened = FileSpecStore(tmp_path)
    assert reopened.load_failure(saved.document_id) == failure
    assert reopened.load_latest_extraction(saved.document_id) == first
    assert reopened.load_review(saved.document_id, first.run_id) == review
    assert pointer.read_bytes() == original_pointer
    assert reopened.list_documents()[0].confirmed_count == 1

    # A retry replaces the diagnostic, not the previous successful result.
    latest_failure = make_failure(
        saved.document_id,
        previous_run_id=first.run_id,
        message="Model returned incomplete output on retry.",
        output_limit=8000,
    )
    reopened.save_failure(latest_failure)
    assert FileSpecStore(tmp_path).load_failure(saved.document_id) == latest_failure
    assert pointer.read_bytes() == original_pointer

    # A later success does not rewrite the historical failure's previous-result identity.
    second = make_run(saved.document_id, "run_two")
    reopened.save_extraction(saved.document_id, second)
    after_success = FileSpecStore(tmp_path)
    assert after_success.load_latest_extraction(saved.document_id) == second
    retained_failure = after_success.load_failure(saved.document_id)
    assert retained_failure == latest_failure
    assert retained_failure.previous_run_id == first.run_id
    assert retained_failure.previous_run_id != second.run_id
    assert after_success.load_review(saved.document_id, first.run_id) == review
    assert not after_success.load_review(saved.document_id, second.run_id).decisions
    for name, original in originals.items():
        assert (directory / name).read_bytes() == original


def test_failure_for_unknown_document_does_not_create_document_artifacts(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)

    with pytest.raises(SpecStoreError):
        store.save_failure(make_failure("f" * 16))

    assert not (store.root / ("f" * 16)).exists()
    assert not list(store.root.rglob("latest_failure.json"))
    assert not (store.root / ".write.lock").exists()


@pytest.mark.parametrize("invalid", ["../../outside", "", "a" * 64, "absolute"])
def test_invalid_failure_document_id_cannot_write_outside_store(
    tmp_path: Path, invalid: str
) -> None:
    store = FileSpecStore(tmp_path / "workspace")
    outside = tmp_path / "outside"
    identifier = str(outside) if invalid == "absolute" else invalid

    with pytest.raises(SpecStoreError):
        store.load_failure(identifier)
    assert not store.root.exists()

    with pytest.raises(SpecStoreError):
        store.save_failure(make_failure(identifier))

    assert not outside.exists()
    assert not list(store.root.rglob("latest_failure.json"))
    assert not (store.root / ".write.lock").exists()


def test_mismatched_failure_document_is_rejected_without_resetting_file(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    store.save_failure(make_failure(saved.document_id))
    path = saved.pdf_path.parent / "latest_failure.json"
    mismatched = make_failure("f" * 16).model_dump_json()
    path.write_text(mismatched, encoding="utf-8")

    with pytest.raises(SpecStoreError, match="does not match"):
        FileSpecStore(tmp_path).load_failure(saved.document_id)

    assert path.read_text(encoding="utf-8") == mismatched
    assert store.load_latest_extraction(saved.document_id) is None


def test_symlink_failure_file_cannot_be_read_or_overwritten(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path / "workspace")
    saved = store.import_pdf("spec.pdf", make_pdf())
    failure = make_failure(saved.document_id)
    outside = tmp_path / "outside.json"
    outside.write_text(failure.model_dump_json(), encoding="utf-8")
    original = outside.read_bytes()
    path = saved.pdf_path.parent / "latest_failure.json"
    path.symlink_to(outside)

    with pytest.raises(SpecStoreError, match="symlink"):
        store.load_failure(saved.document_id)
    with pytest.raises(SpecStoreError, match="symlink"):
        store.save_failure(failure)

    assert path.is_symlink()
    assert outside.read_bytes() == original
    assert not (store.root / ".write.lock").exists()


def test_failure_operations_reject_symlinked_root_and_document_directory(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    store = FileSpecStore(actual)
    saved = store.import_pdf("spec.pdf", make_pdf())
    failure = make_failure(saved.document_id)
    store.save_failure(failure)
    path = saved.pdf_path.parent / "latest_failure.json"
    original = path.read_bytes()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    linked_store = FileSpecStore(linked)

    for action in (
        lambda: linked_store.load_failure(saved.document_id),
        lambda: linked_store.save_failure(failure),
    ):
        with pytest.raises(SpecStoreError, match="symlink"):
            action()
    assert path.read_bytes() == original

    directory = saved.pdf_path.parent
    moved = tmp_path / "outside_document"
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    for action in (
        lambda: store.load_failure(saved.document_id),
        lambda: store.save_failure(failure),
    ):
        with pytest.raises(SpecStoreError, match="symlink"):
            action()
    assert (moved / "latest_failure.json").read_bytes() == original


def test_corrupt_failure_is_reported_instead_of_hidden_or_reset(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    path = saved.pdf_path.parent / "latest_failure.json"
    path.write_text("{broken JSON", encoding="utf-8")

    with pytest.raises(SpecStoreError, match="Unable to load analysis diagnostic"):
        FileSpecStore(tmp_path).load_failure(saved.document_id)

    assert path.read_text(encoding="utf-8") == "{broken JSON"


def test_failure_message_limit_is_enforced_before_storage(tmp_path: Path) -> None:
    store = FileSpecStore(tmp_path)
    saved = store.import_pdf("spec.pdf", make_pdf())
    failure = make_failure(saved.document_id, message="x" * 4000)
    store.save_failure(failure)
    assert store.load_failure(saved.document_id) == failure
    original = (saved.pdf_path.parent / "latest_failure.json").read_bytes()

    with pytest.raises(ValidationError):
        make_failure(saved.document_id, message="x" * 4001)
    # Revalidation also rejects model_copy bypasses before replacing a saved diagnostic.
    with pytest.raises(SpecStoreError):
        store.save_failure(failure.model_copy(update={"message": "x" * 4001}))

    assert (saved.pdf_path.parent / "latest_failure.json").read_bytes() == original
