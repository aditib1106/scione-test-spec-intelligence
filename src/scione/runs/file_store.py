"""Filesystem-backed run artifacts for local experiments."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from scione.evaluation import EvaluationReport
from scione.extraction import MethodExtractionRun
from scione.schemas import IngestedDocument


class RunStoreError(RuntimeError):
    """Raised when local run artifacts cannot be persisted safely."""


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
        except OSError as exc:
            raise RunStoreError(f"Unable to save run {run.run_id}: {exc}") from exc
        return run_directory

    @staticmethod
    def _write_model(path: Path, value: BaseModel) -> None:
        payload = value.model_dump(mode="json")
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
