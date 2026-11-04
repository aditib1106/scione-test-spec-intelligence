"""Coordinate the recoverable local reset with the SQL archive's lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from scione.workspace_reset import DemoResetError, DemoResetResult, reset_demo_workspace

if TYPE_CHECKING:
    from scione.config import Settings
    from scione.library.store import LibraryStore


def reset_library_workspace(
    settings: Settings,
    store: LibraryStore | None = None,
    backup_root: str | Path | None = None,
) -> DemoResetResult:
    """Preserve the embedded SQL archive alongside PDFs, reviews and reports.

    An explicitly configured database can be shared or live elsewhere. The
    prototype does not yet offer an atomic, recoverable reset across that
    database and the file workspace, so it rejects the whole operation before
    touching either resource. Ordinary application reads and edits still work.
    """

    if settings.scione_database_url:
        raise DemoResetError(
            "Reset is unavailable while an external database is configured. "
            "Your database and saved files have not been changed. "
            "For a fresh walkthrough, use a separate demo database and workspace; "
            "keep the existing archive backed up."
        )
    if store is not None:
        store.close()
    return reset_demo_workspace(settings.scione_workspace_dir, backup_root)
