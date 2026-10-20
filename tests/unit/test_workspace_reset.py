"""Fresh-demo maintenance preserves exact artifacts and rejects broad targets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scione.workspace_reset import DemoResetError, reset_demo_workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "local_data"
    root.mkdir()
    (root / "lab.json").write_bytes(b'{"existing": "user-edited lab"}\n')
    (root / "specifications" / "example" / "runs").mkdir(parents=True)
    (root / "specifications" / "example" / "original.pdf").write_bytes(b"original pdf")
    (root / "specifications" / "example" / "runs" / "review.json").write_bytes(b"review")
    (root / "reports" / "snapshots").mkdir(parents=True)
    (root / "reports" / "snapshots" / "report.pdf").write_bytes(b"report pdf")
    (root / ".env").write_bytes(b"private credentials stay here")
    (root / "other.txt").write_bytes(b"unknown state is not selected")
    return root


def test_exact_demo_artifacts_are_recoverably_moved_and_manifested(workspace: Path) -> None:
    before = {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    result = reset_demo_workspace(workspace)

    assert result.moved_names == ("lab.json", "specifications", "reports")
    assert result.file_count == 4
    assert result.backup_path is not None
    assert result.backup_path.parent == workspace.parent / ".demo-backups"
    for relative, content in before.items():
        destination = (
            workspace if relative.parts[0] in {".env", "other.txt"} else result.backup_path
        )
        assert (destination / relative).read_bytes() == content
    assert set(path.name for path in workspace.iterdir()) == {".env", "other.txt"}
    manifest = json.loads((result.backup_path / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["workspace"] == str(workspace)
    assert [item["name"] for item in manifest["artifacts"]] == list(result.moved_names)
    assert not list(result.backup_path.rglob("*.lock"))


def test_second_reset_is_noop_and_keeps_existing_backup(workspace: Path) -> None:
    first = reset_demo_workspace(workspace)
    second = reset_demo_workspace(workspace)

    assert second.backup_path is None
    assert second.moved_names == ()
    assert second.file_count == 0
    assert first.backup_path is not None and (first.backup_path / "lab.json").is_file()


def test_absent_workspace_is_not_created(tmp_path: Path) -> None:
    root = tmp_path / "local_data"
    result = reset_demo_workspace(root)

    assert result.backup_path is None
    assert not root.exists()
    assert not (tmp_path / ".demo-backups").exists()


@pytest.mark.parametrize("target", [".lab.lock", ".demo-reset.lock", "specifications/.write.lock"])
def test_active_store_locks_reject_reset_before_moving(workspace: Path, target: str) -> None:
    (workspace / target).write_text("active")
    with pytest.raises(DemoResetError, match="writer may be active"):
        reset_demo_workspace(workspace)

    assert (workspace / "lab.json").is_file()
    assert (workspace / "specifications" / "example" / "original.pdf").is_file()
    assert not (workspace.parent / ".demo-backups").exists()


@pytest.mark.parametrize("root", [Path("/"), Path.home(), Path.cwd(), Path.cwd() / "src"])
def test_home_repository_and_system_targets_are_rejected(root: Path) -> None:
    with pytest.raises(DemoResetError, match="Refusing to reset"):
        reset_demo_workspace(root)


def test_other_repository_root_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(DemoResetError, match="Refusing to reset"):
        reset_demo_workspace(tmp_path)


@pytest.mark.parametrize("relative", ["lab.json", "specifications", "specifications/example/link"])
def test_symlink_artifacts_are_rejected_without_touching_targets(
    workspace: Path, tmp_path: Path, relative: str
) -> None:
    source = workspace / relative
    if source.is_file():
        source.unlink()
    elif source.is_dir():
        source.rename(workspace / "preserved-specifications")
    external = tmp_path / "private.txt"
    external.write_bytes(b"do not touch")
    source.symlink_to(external)

    with pytest.raises(DemoResetError, match="symlink"):
        reset_demo_workspace(workspace)

    assert external.read_bytes() == b"do not touch"
    assert not (workspace.parent / ".demo-backups").exists()


def test_symlink_workspace_and_backup_ancestors_are_rejected(
    workspace: Path, tmp_path: Path
) -> None:
    linked = tmp_path / "linked"
    linked.symlink_to(workspace, target_is_directory=True)
    with pytest.raises(DemoResetError, match="symlink"):
        reset_demo_workspace(linked)
    backup = tmp_path / ".demo-backups"
    backup.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(DemoResetError, match="symlink"):
        reset_demo_workspace(workspace, backup)


@pytest.mark.parametrize("backup_name", ["elsewhere", "local_data/.demo-backups"])
def test_recovery_directory_is_validated_before_writes(workspace: Path, backup_name: str) -> None:
    with pytest.raises(DemoResetError):
        reset_demo_workspace(workspace, workspace.parent / backup_name)

    assert (workspace / "lab.json").is_file()


def test_unexpected_target_type_and_parent_traversal_are_rejected(workspace: Path) -> None:
    with pytest.raises(DemoResetError, match="parent traversal"):
        reset_demo_workspace(workspace / ".." / "local_data")
    (workspace / "lab.json").unlink()
    (workspace / "lab.json").mkdir()
    with pytest.raises(DemoResetError, match="artifact type"):
        reset_demo_workspace(workspace)


def test_move_failure_rolls_back_already_moved_state(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_rename = Path.rename

    def fail_second_move(source: Path, destination: Path) -> Path:
        if source == workspace / "specifications":
            raise OSError("Injected move failure")
        return original_rename(source, destination)

    monkeypatch.setattr(Path, "rename", fail_second_move)
    with pytest.raises(DemoResetError, match="no files were deleted"):
        reset_demo_workspace(workspace)

    assert (workspace / "lab.json").read_bytes() == b'{"existing": "user-edited lab"}\n'
    assert (
        workspace / "specifications" / "example" / "original.pdf"
    ).read_bytes() == b"original pdf"
    assert (workspace / "reports" / "snapshots" / "report.pdf").read_bytes() == b"report pdf"
    assert not list(workspace.rglob("*.lock"))
    manifests = list((workspace.parent / ".demo-backups").glob("*/manifest.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["status"] == "rolled_back"


def test_manifest_finish_failure_rolls_back_all_moves(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scione.workspace_reset as reset_module

    original_write = reset_module._write_manifest

    def fail_complete(path: Path, manifest: dict[str, object]) -> None:
        if manifest["status"] == "complete":
            raise OSError("Injected manifest failure")
        original_write(path, manifest)

    monkeypatch.setattr(reset_module, "_write_manifest", fail_complete)
    with pytest.raises(DemoResetError):
        reset_demo_workspace(workspace)

    assert (workspace / "lab.json").is_file()
    assert (workspace / "specifications" / "example" / "original.pdf").is_file()
    assert (workspace / "reports" / "snapshots" / "report.pdf").is_file()
    assert not list(workspace.rglob("*.lock"))


def test_rollback_never_overwrites_a_newly_recreated_original(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_rename = Path.rename

    def concurrent_recreation(source: Path, destination: Path) -> Path:
        if source == workspace / "specifications":
            (workspace / "lab.json").write_bytes(b"new lab written concurrently")
            raise OSError("Injected concurrent recreation")
        return original_rename(source, destination)

    monkeypatch.setattr(Path, "rename", concurrent_recreation)
    with pytest.raises(DemoResetError, match="Recovery copies are at"):
        reset_demo_workspace(workspace)

    assert (workspace / "lab.json").read_bytes() == b"new lab written concurrently"
    copies = list((workspace.parent / ".demo-backups").glob("*/lab.json"))
    assert len(copies) == 1
    assert copies[0].read_bytes() == b'{"existing": "user-edited lab"}\n'
    manifest = json.loads((copies[0].parent / "manifest.json").read_text())
    assert manifest["status"] == "recovery_required"
    assert (workspace / "specifications" / "example" / "original.pdf").is_file()
    assert not list(workspace.rglob("*.lock"))
