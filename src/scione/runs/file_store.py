"""Filesystem-backed run artifacts for local experiments."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from scione.evaluation import EvaluationReport
from scione.extraction import MethodExtractionRun
from scione.schemas import IngestedDocument, TestMethodExtraction


class RunStoreError(RuntimeError):
    """Raised when local run artifacts cannot be persisted safely."""


@dataclass(frozen=True)
class StoredMethodRun:
    """Validated artifacts loaded from one saved run directory."""

    directory: Path
    document: IngestedDocument
    run: MethodExtractionRun
    evaluation: EvaluationReport | None
    ground_truth: TestMethodExtraction | None = None


class FileRunStore:
    """Write one immutable directory per run using portable JSON files."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save_method_run(
        self,
        *,
        document: IngestedDocument,
        run: MethodExtractionRun,
        evaluation: EvaluationReport | None = None,
        ground_truth: TestMethodExtraction | None = None,
    ) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        run_directory = self.root / run.run_id
        try:
            run_directory.mkdir(exist_ok=False)
            self._write_model(run_directory / "document.json", document)
            self._write_model(run_directory / "run.json", run)
            self._write_model(run_directory / "prediction.json", run.extraction)
            (run_directory / "raw_response.txt").write_text(
                run.raw_response,
                encoding="utf-8",
            )
            if evaluation is not None:
                self._write_model(run_directory / "evaluation.json", evaluation)
            if ground_truth is not None:
                self._write_model(run_directory / "ground_truth.json", ground_truth)
        except OSError as exc:
            raise RunStoreError(f"Unable to save run {run.run_id}: {exc}") from exc
        return run_directory

    def list_method_run_ids(self) -> list[str]:
        """Return newest run directories first without loading their large payloads."""

        if not self.root.exists():
            return []
        try:
            directories = [
                path
                for path in self.root.iterdir()
                if path.is_dir() and (path / "run.json").is_file()
            ]
            directories.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError as exc:
            raise RunStoreError(f"Unable to list runs in {self.root}: {exc}") from exc
        return [path.name for path in directories]

    def load_method_run(self, run_id: str) -> StoredMethodRun:
        """Load one run by directory name while rejecting path traversal."""

        if not run_id or Path(run_id).name != run_id:
            raise RunStoreError(f"Invalid run id: {run_id!r}")
        run_directory = self.root / run_id
        try:
            document_payload = json.loads(
                (run_directory / "document.json").read_text(encoding="utf-8")
            )
            # Runs created before the loader existed included computed display fields.
            document_payload.pop("total_character_count", None)
            document_payload.pop("total_non_whitespace_character_count", None)
            document = IngestedDocument.model_validate(document_payload)
            run = MethodExtractionRun.model_validate_json(
                (run_directory / "run.json").read_text(encoding="utf-8")
            )
            evaluation_path = run_directory / "evaluation.json"
            evaluation = (
                EvaluationReport.model_validate_json(evaluation_path.read_text(encoding="utf-8"))
                if evaluation_path.exists()
                else None
            )
            ground_truth_path = run_directory / "ground_truth.json"
            ground_truth = (
                TestMethodExtraction.model_validate_json(
                    ground_truth_path.read_text(encoding="utf-8")
                )
                if ground_truth_path.exists()
                else None
            )
        except (OSError, TypeError, ValueError) as exc:
            raise RunStoreError(f"Unable to load run {run_id}: {exc}") from exc
        return StoredMethodRun(
            directory=run_directory,
            document=document,
            run=run,
            evaluation=evaluation,
            ground_truth=ground_truth,
        )

    @staticmethod
    def _write_model(path: Path, value: BaseModel) -> None:
        payload = value.model_dump(mode="json", exclude_computed_fields=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
