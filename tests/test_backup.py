"""Backup integrity, verification and restore (spec 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gps_repair.backup import (
    BackupError, create_backup, load_manifest, resolve_backup_dir, restore, sha256,
    verify_backup,
)


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib" / "2026" / "Coast"
    lib.mkdir(parents=True)
    for name in ("A.xmp", "B.xmp"):
        (lib / name).write_text(f"contents of {name}", encoding="utf-8")
    return tmp_path / "lib"


def test_backup_copies_and_verifies(library, tmp_path):
    files = sorted(library.rglob("*.xmp"))
    entries = create_backup(files, library, tmp_path / "bk")

    assert len(entries) == 2
    for entry in entries:
        assert Path(entry["backup"]).is_file()
        assert entry["sha256"] == sha256(Path(entry["original"]))


def test_backup_preserves_relative_structure(library, tmp_path):
    """Spec 5.2."""
    create_backup(sorted(library.rglob("*.xmp")), library, tmp_path / "bk")
    assert (tmp_path / "bk" / "files" / "2026" / "Coast" / "A.xmp").is_file()


def test_manifest_is_written_and_loadable(library, tmp_path):
    """Spec 5.4."""
    create_backup(sorted(library.rglob("*.xmp")), library, tmp_path / "bk")
    manifest = load_manifest(tmp_path / "bk")
    assert manifest["count"] == 2
    assert {"original", "backup", "size", "sha256"} <= set(manifest["files"][0])


def test_verification_detects_corruption(library, tmp_path):
    """A tampered copy must be caught before any modification."""
    entries = create_backup(sorted(library.rglob("*.xmp")), library, tmp_path / "bk")
    backup = Path(entries[0]["backup"])
    # Same length, different bytes: this must be caught by the hash, not the size.
    backup.write_text("X" * entries[0]["size"], encoding="utf-8")
    assert backup.stat().st_size == entries[0]["size"]
    with pytest.raises(BackupError, match="checksum mismatch"):
        verify_backup(entries)


def test_verification_detects_missing_backup(library, tmp_path):
    entries = create_backup(sorted(library.rglob("*.xmp")), library, tmp_path / "bk")
    Path(entries[0]["backup"]).unlink()
    with pytest.raises(BackupError, match="missing"):
        verify_backup(entries)


def test_backup_inside_library_is_refused(library):
    """Spec 5.2: backups belong outside the photo tree."""
    with pytest.raises(BackupError, match="inside the scanned library"):
        resolve_backup_dir(library / "backups", library)


def test_backup_dir_is_timestamped(library, tmp_path):
    target = resolve_backup_dir(tmp_path / "bk", library)
    assert target.parent == (tmp_path / "bk").resolve()
    assert len(target.name) == len("2026-09-19_193500")


def test_restore_round_trip(library, tmp_path):
    """Spec 5.5."""
    files = sorted(library.rglob("*.xmp"))
    originals = {f: f.read_text() for f in files}
    create_backup(files, library, tmp_path / "bk")

    for f in files:
        f.write_text("modified", encoding="utf-8")

    outcomes = restore(tmp_path / "bk")
    assert all(why == "restored" for _, why in outcomes)
    for f, text in originals.items():
        assert f.read_text() == text


def test_restore_dry_run_changes_nothing(library, tmp_path):
    files = sorted(library.rglob("*.xmp"))
    create_backup(files, library, tmp_path / "bk")
    files[0].write_text("modified", encoding="utf-8")

    outcomes = restore(tmp_path / "bk", dry_run=True)
    assert all(why == "would restore" for _, why in outcomes)
    assert files[0].read_text() == "modified"


def test_restore_refuses_corrupt_backup(library, tmp_path):
    """A corrupt backup is reported, never written over a good original."""
    files = sorted(library.rglob("*.xmp"))
    entries = create_backup(files, library, tmp_path / "bk")
    Path(entries[0]["backup"]).write_text("corrupt", encoding="utf-8")

    outcomes = dict(restore(tmp_path / "bk"))
    assert "checksum mismatch" in outcomes[Path(entries[0]["original"])]
    assert files[0].read_text() != "corrupt"


def test_restore_without_manifest_fails(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(BackupError, match="manifest"):
        restore(tmp_path / "empty")


def test_unwritable_destination_raises(library, tmp_path):
    """Spec 5.1/28: a backup failure is critical."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        with pytest.raises(BackupError):
            create_backup(sorted(library.rglob("*.xmp")), library, blocked / "bk")
    finally:
        blocked.chmod(0o700)
