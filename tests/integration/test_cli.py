"""The supported CLI inspects PDFs locally and never invokes an LLM provider."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pymupdf
import pytest

from scione.cli import build_parser, main


@pytest.fixture
def customer_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "customer-spec.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text(
            (72, 72),
            "Customer specification\nCross-cut adhesion: ISO 2409; spacing 2 mm.\n"
            "Acceptance rating: GT 0 to 1. Confirm the source before matching lab capabilities.",
        )
        document.new_page().insert_text(
            (72, 72),
            "Humidity resistance: ISO 6270-2; exposure 240 hours.\n"
            "Use the customer criterion recorded for this test.",
        )
        document.save(path)
    return path


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("The offline ingest CLI must not access the network.")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)


@pytest.mark.parametrize("reading_order", ["content_stream", "geometric"])
def test_ingest_outputs_page_aware_json_without_changing_pdf(
    customer_pdf: Path,
    reading_order: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_bytes = customer_pdf.read_bytes()

    result = main(["ingest", str(customer_pdf), "--reading-order", reading_order])

    assert result == 0
    document = json.loads(capsys.readouterr().out)
    assert document["page_count"] == 2
    assert document["file_name"] == customer_pdf.name
    assert document["text_reading_order"] == reading_order
    assert document["extraction_status"] == "ready"
    assert "Cross-cut adhesion" in document["pages"][0]["text"]
    assert "ISO 6270-2" in document["pages"][1]["text"]
    assert [page["page_number"] for page in document["pages"]] == [1, 2]
    assert customer_pdf.read_bytes() == original_bytes


def test_ingest_reports_invalid_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(["ingest", str(tmp_path / "missing.pdf")])

    assert result == 1
    assert "Ingestion failed: File does not exist:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "command", ["evaluate", "extract-method-static", "extract-method-groq"]
)
def test_obsolete_standard_extraction_commands_are_rejected(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main([command])

    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_help_only_advertises_supported_offline_command() -> None:
    help_text = build_parser().format_help()

    assert "ingest" in help_text
    assert "evaluate" not in help_text
    assert "extract-method" not in help_text
