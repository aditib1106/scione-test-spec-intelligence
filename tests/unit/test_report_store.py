"""Report history is immutable and independent of mutable lab/specification files."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest

from scione.catalogs.models import LabProfile, ReferenceCatalog
from scione.providers import TokenUsage
from scione.reports.models import (
    GapLine,
    MappingChoice,
    QuoteChoice,
    ReportSnapshot,
    ReportWorkspace,
)
from scione.reports.store import (
    MAX_REPORT_PDF_BYTES,
    FileReportStore,
    ReportStoreError,
    ReportVersionConflict,
)
from scione.specifications.models import CustomerExtraction, CustomerExtractionRun
from scione.specifications.store import RequirementReview, RequirementReviewSet

DOCUMENT_ID = "a" * 16
RUN_ID = "00f5df93-3669-4668-a5c7-6b8bb28d0cd4"


def make_pdf(*, pages: int = 1, encrypted: bool = False) -> bytes:
    with pymupdf.open() as document:
        for _ in range(pages):
            document.new_page().insert_text((50, 50), "Draft lab capability report")
        if encrypted:
            return document.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret"
            )
        return document.tobytes()


def make_snapshot(**changes: object) -> ReportSnapshot:
    document_id = changes.pop("document_id", DOCUMENT_ID)
    run_id = changes.pop("run_id", RUN_ID)
    extraction = CustomerExtraction.model_validate(
        {
            "schema_version": "0.1",
            "document": {"title": "Exterior coating spec"},
            "requirements": [
                {
                    "id": "req_pencil",
                    "original_test_name": "Pencil hardness",
                    "requested_methods": [{"code": "ISO 15184"}],
                    "conditions": [{"name": "Load", "raw_value": "750 g"}],
                    "acceptance_criteria": [{"name": "Hardness", "raw_value": "At least F"}],
                    "evidence": [{"page": 1, "quote": "Pencil hardness ISO 15184"}],
                }
            ],
        }
    )
    run = CustomerExtractionRun(
        run_id=run_id,
        document_id=document_id,
        started_at=datetime.now(UTC),
        prompt_version="customer_requirements_v0.2",
        schema_version="0.1",
        provider="offline_test",
        model="no_network",
        latency_ms=10,
        usage=TokenUsage(),
        raw_response="  Original provider text\n",
        extraction=extraction,
    )
    return ReportSnapshot.model_validate(
        {
            "report_id": str(uuid4()),
            "created_at": datetime.now(UTC),
            "document_id": document_id,
            "original_name": "customer.pdf",
            "document_sha256": str(document_id) + "a" * 48,
            "source_run": run,
            "source_review": RequirementReviewSet(document_id=document_id, run_id=run_id),
            "lab": LabProfile(id="demo_lab", name="Demo lab"),
            "catalog": ReferenceCatalog(
                id="demo_catalog",
                name="Synthetic catalog",
                version=1,
                source_note="Offline fixture",
                tests=[],
                methods=[],
            ),
            "workspace": ReportWorkspace(document_id=document_id, run_id=run_id),
            "input_fingerprint": "f" * 64,
            "lines": [
                GapLine(
                    requirement=extraction.requirements[0],
                    status="test_gap",
                    match_basis="No catalog match",
                    extraction_review_state="ready",
                )
            ],
            **changes,
        }
    )


def test_construct_default_workspace_and_list_are_read_only(tmp_path: Path) -> None:
    directory = tmp_path / "not-created"
    store = FileReportStore(directory)
    assert store.root == directory / "reports"
    assert store.load_workspace(DOCUMENT_ID, RUN_ID) == ReportWorkspace(
        document_id=DOCUMENT_ID, run_id=RUN_ID
    )
    assert store.list_reports() == []
    assert store.list_reports(DOCUMENT_ID) == []
    assert not directory.exists()


def test_workspace_compare_and_swap_increments_copy_and_survives_reload(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    workspace = store.load_workspace(DOCUMENT_ID, RUN_ID)
    workspace.mappings["req_pencil"] = MappingChoice(note="Reviewed manually")
    workspace.quantities["req_pencil"] = QuoteChoice(
        quantity=Decimal("2.5"), scope_and_basis_confirmed=True, note="2.5 batches"
    )
    saved = store.save_workspace(workspace, expected_version=0)
    assert workspace.version == 0
    assert saved.version == 1
    assert FileReportStore(tmp_path).load_workspace(DOCUMENT_ID, RUN_ID) == saved
    with pytest.raises(ReportVersionConflict):
        store.save_workspace(workspace, expected_version=0)
    assert store.load_workspace(DOCUMENT_ID, RUN_ID) == saved
    saved.mappings["req_pencil"].note = "Amended choice"
    again = store.save_workspace(saved, expected_version=1)
    assert again.version == 2
    assert saved.version == 1
    assert store.load_workspace(DOCUMENT_ID, RUN_ID).mappings["req_pencil"].note == "Amended choice"


@pytest.mark.parametrize("expected", [-1, True, 0.0, "0"])
def test_invalid_expected_version_rejected_without_write(tmp_path: Path, expected: object) -> None:
    store = FileReportStore(tmp_path / "data")
    with pytest.raises(ReportStoreError):
        store.save_workspace(
            ReportWorkspace(document_id=DOCUMENT_ID, run_id=RUN_ID), expected_version=expected
        )
    assert not store.root.exists()


def test_workspace_version_argument_must_match_editor_version(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path / "data")
    workspace = ReportWorkspace(document_id=DOCUMENT_ID, run_id=RUN_ID, version=2)
    with pytest.raises(ReportVersionConflict):
        store.save_workspace(workspace, expected_version=0)
    assert not store.root.exists()


def test_report_round_trip_keeps_exact_pdf_raw_text_and_detached_snapshot(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    report, pdf = make_snapshot(), make_pdf()
    store.save_report(report, pdf)
    assert store.load_report(report.report_id) == report
    assert store.read_pdf(report.report_id) == pdf
    reloaded = FileReportStore(tmp_path)
    assert reloaded.list_reports() == [report]
    assert reloaded.list_reports(DOCUMENT_ID) == [report]
    assert reloaded.list_reports("b" * 16) == []
    assert (
        reloaded.load_report(report.report_id).source_run.raw_response
        == "  Original provider text\n"
    )
    assert not (tmp_path / "specifications").exists()
    assert not (tmp_path / "lab.json").exists()
    # Later changes to in-memory source objects cannot alter an immutable saved report.
    report.lab.name = "Different lab now"
    report.source_run.extraction.requirements[0].original_test_name = "Edited extraction"
    assert reloaded.load_report(report.report_id).lab.name == "Demo lab"
    assert (
        reloaded.load_report(report.report_id).lines[0].requirement.original_test_name
        == "Pencil hardness"
    )
    assert {path.name for path in (store.root / "snapshots" / report.report_id).iterdir()} == {
        "report.json",
        "report.pdf",
        "manifest.json",
    }


def test_report_ids_are_immutable_even_for_identical_content(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    report, pdf = make_snapshot(), make_pdf()
    store.save_report(report, pdf)
    with pytest.raises(ReportStoreError, match="immutable"):
        store.save_report(report, pdf)
    assert store.load_report(report.report_id) == report
    assert store.read_pdf(report.report_id) == pdf


def test_listing_orders_naive_and_aware_timestamps_as_utc(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    times = [
        datetime(2026, 10, 1, 12),
        datetime(2026, 10, 1, 8, tzinfo=timezone(timedelta(hours=-5))),
        datetime(2026, 10, 1, 11, tzinfo=UTC),
    ]
    reports = [make_snapshot(created_at=value) for value in times]
    for report in reports:
        store.save_report(report, make_pdf())
    assert [item.report_id for item in store.list_reports()] == [
        reports[index].report_id for index in (1, 0, 2)
    ]


@pytest.mark.parametrize("artifact", ["report.json", "report.pdf", "manifest.json"])
def test_changed_bundle_artifact_is_reported_not_hidden_or_repaired(
    tmp_path: Path, artifact: str
) -> None:
    store = FileReportStore(tmp_path)
    report = make_snapshot()
    store.save_report(report, make_pdf())
    path = store.root / "snapshots" / report.report_id / artifact
    path.write_bytes(b"corrupt")
    for action in [
        lambda: store.load_report(report.report_id),
        lambda: store.read_pdf(report.report_id),
        store.list_reports,
        lambda: store.list_reports("b" * 16),
    ]:
        with pytest.raises(ReportStoreError):
            action()
    assert path.read_bytes() == b"corrupt"


@pytest.mark.parametrize("artifact", ["report.json", "report.pdf", "manifest.json"])
def test_missing_bundle_artifact_is_not_silently_skipped(tmp_path: Path, artifact: str) -> None:
    store = FileReportStore(tmp_path)
    report = make_snapshot()
    store.save_report(report, make_pdf())
    (store.root / "snapshots" / report.report_id / artifact).unlink()
    with pytest.raises(ReportStoreError, match="missing"):
        store.list_reports()


def test_unexpected_bundle_artifact_and_directory_report_errors(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    report = make_snapshot()
    store.save_report(report, make_pdf())
    extra = store.root / "snapshots" / report.report_id / "extra.txt"
    extra.write_text("unexpected")
    with pytest.raises(ReportStoreError, match="unexpected"):
        store.load_report(report.report_id)
    extra.unlink()
    (store.root / "snapshots" / "not-a-report").mkdir()
    with pytest.raises(ReportStoreError, match="UUID"):
        store.list_reports()


def test_corrupt_workspace_never_replaced_with_defaults(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    saved = store.save_workspace(
        ReportWorkspace(document_id=DOCUMENT_ID, run_id=RUN_ID), expected_version=0
    )
    path = store.root / "workspaces" / DOCUMENT_ID / f"{RUN_ID}.json"
    path.write_text("{broken")
    with pytest.raises(ReportStoreError):
        store.load_workspace(DOCUMENT_ID, RUN_ID)
    with pytest.raises(ReportStoreError):
        store.save_workspace(saved, expected_version=1)
    assert path.read_text() == "{broken"


def test_wrong_workspace_identity_is_rejected(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    saved = store.save_workspace(
        ReportWorkspace(document_id=DOCUMENT_ID, run_id=RUN_ID), expected_version=0
    )
    path = store.root / "workspaces" / DOCUMENT_ID / f"{RUN_ID}.json"
    saved.run_id = str(uuid4())
    path.write_text(saved.model_dump_json())
    with pytest.raises(ReportStoreError, match="inconsistent source IDs"):
        store.load_workspace(DOCUMENT_ID, RUN_ID)


@pytest.mark.parametrize("document_id", ["../outside", "", "A" * 16, "a" * 64, "x" * 16])
def test_bad_document_ids_rejected_without_io(tmp_path: Path, document_id: str) -> None:
    store = FileReportStore(tmp_path)
    with pytest.raises(ReportStoreError):
        store.load_workspace(document_id, RUN_ID)
    with pytest.raises(ReportStoreError):
        store.list_reports(document_id)
    assert not store.root.exists()


@pytest.mark.parametrize("identifier", ["../outside", "", "run_one", "a/b", "a\\b", RUN_ID.upper()])
def test_non_uuid_or_noncanonical_run_and_report_ids_rejected(
    tmp_path: Path, identifier: str
) -> None:
    store = FileReportStore(tmp_path)
    for action in [
        lambda: store.load_workspace(DOCUMENT_ID, identifier),
        lambda: store.load_report(identifier),
        lambda: store.read_pdf(identifier),
    ]:
        with pytest.raises(ReportStoreError, match="UUID"):
            action()
    assert not store.root.exists()


@pytest.mark.parametrize(
    "data", [b"", b"not a PDF", b"%PDF-malformed", b"a" * (MAX_REPORT_PDF_BYTES + 1)]
)
def test_invalid_report_pdf_rejected_before_store_creation(tmp_path: Path, data: bytes) -> None:
    store = FileReportStore(tmp_path)
    with pytest.raises(ReportStoreError):
        store.save_report(make_snapshot(), data)
    assert not store.root.exists()


def test_encrypted_and_excessive_report_pdf_rejected(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    for pdf in [make_pdf(encrypted=True), make_pdf(pages=501)]:
        with pytest.raises(ReportStoreError):
            store.save_report(make_snapshot(), pdf)
    assert not store.root.exists()


def test_report_source_copy_hash_and_unknown_review_choices_are_checked(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    original = make_snapshot()
    bad_copy = original.model_copy(deep=True)
    bad_copy.lines[0].requirement = bad_copy.lines[0].requirement.model_copy(
        update={"original_test_name": "Not the source name"}
    )
    bad_hash = original.model_copy(deep=True, update={"document_sha256": "b" * 64})
    bad_review = original.model_copy(deep=True)
    bad_review.source_review.decisions["req_unknown"] = RequirementReview(
        requirement_id="req_unknown", status="confirmed", updated_at=datetime.now(UTC)
    )
    bad_mapping = original.model_copy(deep=True)
    bad_mapping.workspace.mappings["req_unknown"] = MappingChoice(note="Unknown source row")
    for report in [bad_copy, bad_hash, bad_review, bad_mapping]:
        with pytest.raises(ReportStoreError):
            store.save_report(report, make_pdf())
    assert not store.root.exists()


def test_rewritten_json_with_updated_hash_still_requires_consistent_identity(
    tmp_path: Path,
) -> None:
    store = FileReportStore(tmp_path)
    report = make_snapshot()
    store.save_report(report, make_pdf())
    directory = store.root / "snapshots" / report.report_id
    path = directory / "report.json"
    data = json.loads(path.read_text())
    data["report_id"] = str(uuid4())
    encoded = json.dumps(data).encode()
    path.write_bytes(encoded)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["json_sha256"] = hashlib.sha256(encoded).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ReportStoreError, match="identity"):
        store.load_report(report.report_id)


def test_parent_traversal_and_symlinked_root_rejected(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path / "nested" / ".." / "data")
    with pytest.raises(ReportStoreError, match="traversal"):
        store.list_reports()
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    store = FileReportStore(linked)
    for action in [
        store.list_reports,
        lambda: store.load_workspace(DOCUMENT_ID, RUN_ID),
        lambda: store.save_report(make_snapshot(), make_pdf()),
    ]:
        with pytest.raises(ReportStoreError, match="symlink"):
            action()
    assert not (actual / "reports").exists()


@pytest.mark.parametrize("artifact", ["report.json", "report.pdf", "manifest.json"])
def test_symlinked_bundle_files_are_rejected(tmp_path: Path, artifact: str) -> None:
    store = FileReportStore(tmp_path)
    report = make_snapshot()
    store.save_report(report, make_pdf())
    path = store.root / "snapshots" / report.report_id / artifact
    external = tmp_path / "outside"
    original = path.read_bytes()
    external.write_bytes(original)
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(ReportStoreError, match="symlink"):
        store.read_pdf(report.report_id)
    assert external.read_bytes() == original


def test_symlinked_workspace_and_write_lock_do_not_touch_target(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    workspace = store.save_workspace(
        ReportWorkspace(document_id=DOCUMENT_ID, run_id=RUN_ID), expected_version=0
    )
    path = store.root / "workspaces" / DOCUMENT_ID / f"{RUN_ID}.json"
    external = tmp_path / "outside"
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(ReportStoreError, match="symlink"):
        store.load_workspace(DOCUMENT_ID, RUN_ID)
    with pytest.raises(ReportStoreError, match="symlink"):
        store.save_workspace(workspace, expected_version=1)
    lock = store.root / ".write.lock"
    lock.symlink_to(external)
    with pytest.raises(ReportStoreError, match="symlink"):
        store.save_report(make_snapshot(), make_pdf())
    assert external.is_file()


def test_symlinked_staging_directory_rejected_but_real_staging_omitted(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    report = make_snapshot()
    store.save_report(report, make_pdf())
    staging = store.root / "snapshots" / ".snapshot-active"
    staging.mkdir()
    assert store.list_reports() == [report]
    staging.rmdir()
    staging.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ReportStoreError, match="symlink"):
        store.list_reports()


def test_locked_store_never_overwrites_active_write_lock(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    store.root.mkdir()
    lock = store.root / ".write.lock"
    lock.write_text("other writer")
    with pytest.raises(ReportStoreError, match="active"):
        store.save_report(make_snapshot(), make_pdf())
    assert lock.read_text() == "other writer"


def test_failed_snapshot_commit_leaves_no_partial_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileReportStore(tmp_path)
    original_write = store._write_atomic

    def fail_pdf(path: Path, data: bytes) -> None:
        if path.name == "report.pdf":
            raise OSError("Simulated disk failure")
        original_write(path, data)

    monkeypatch.setattr(store, "_write_atomic", fail_pdf)
    report = make_snapshot()
    with pytest.raises(ReportStoreError, match="disk failure"):
        store.save_report(report, make_pdf())
    assert store.list_reports() == []
    assert not (store.root / ".write.lock").exists()
    assert list((store.root / "snapshots").iterdir()) == []


def test_regular_file_in_directory_position_is_not_overwritten(tmp_path: Path) -> None:
    (tmp_path / "reports").write_text("preserve")
    store = FileReportStore(tmp_path)
    with pytest.raises(ReportStoreError, match="directory"):
        store.list_reports()
    with pytest.raises(ReportStoreError, match="directory"):
        store.load_workspace(DOCUMENT_ID, RUN_ID)
    assert (tmp_path / "reports").read_text() == "preserve"


def test_nonregular_workspace_artifact_does_not_block_reader(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    store.save_workspace(
        ReportWorkspace(document_id=DOCUMENT_ID, run_id=RUN_ID), expected_version=0
    )
    path = store.root / "workspaces" / DOCUMENT_ID / f"{RUN_ID}.json"
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(ReportStoreError, match="regular file"):
        store.load_workspace(DOCUMENT_ID, RUN_ID)


def test_symlinked_workspace_parent_does_not_create_outside_files(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    store.root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "workspaces").symlink_to(outside, target_is_directory=True)
    workspace = ReportWorkspace(document_id=DOCUMENT_ID, run_id=RUN_ID)
    with pytest.raises(ReportStoreError, match="symlink"):
        store.save_workspace(workspace, expected_version=0)
    assert list(outside.iterdir()) == []


def test_symlinked_snapshot_directory_rejected_when_loading_or_saving(tmp_path: Path) -> None:
    store = FileReportStore(tmp_path)
    report = make_snapshot()
    snapshot_dir = store.root / "snapshots"
    snapshot_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (snapshot_dir / report.report_id).symlink_to(outside, target_is_directory=True)
    for action in [
        lambda: store.save_report(report, make_pdf()),
        lambda: store.load_report(report.report_id),
        store.list_reports,
    ]:
        with pytest.raises(ReportStoreError, match="symlink"):
            action()
    assert list(outside.iterdir()) == []
