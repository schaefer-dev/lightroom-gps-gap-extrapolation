"""Backup, verification and restore (spec 5).

The backup phase must complete and verify before any modification begins. A
failure here is critical and aborts the whole apply operation (spec 5.1, 28).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1


class BackupError(RuntimeError):
    """Critical: no modification may proceed."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def timestamp_name(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")


def default_backup_root() -> Path:
    return Path.home() / "LightroomGPSBackup"


def resolve_backup_dir(backup_dir: Path | None, root: Path) -> Path:
    """Choose the backup location, refusing to nest it inside the library.

    Spec 5.2: backups belong outside the photo tree.
    """
    base = Path(backup_dir).expanduser() if backup_dir else default_backup_root()
    base = base.resolve()
    library = root.resolve()
    if base == library or library in base.parents:
        raise BackupError(
            f"backup directory {base} is inside the scanned library {library}.\n"
            "Choose a location outside the photo tree."
        )
    return base / timestamp_name()


def create_backup(paths: list[Path], root: Path, destination: Path) -> list[dict]:
    """Copy every file, then verify each copy by hash.

    Returns the manifest entries. Raises BackupError if anything is wrong, so
    the caller can abort before touching an original.
    """
    files_dir = destination / "files"
    try:
        files_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"could not create backup directory {files_dir}: {exc}") from exc
    root = root.resolve()
    entries: list[dict] = []

    for path in paths:
        source = path.resolve()
        try:
            relative = source.relative_to(root)
        except ValueError:
            relative = Path(source.name)

        target = files_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            raise BackupError(f"could not back up {source}: {exc}") from exc

        stat = source.stat()
        entries.append({
            "original": str(source),
            "backup": str(target),
            "relative": str(relative),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha256(source),
        })

    verify_backup(entries)

    manifest = {
        "schema": SCHEMA_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "count": len(entries),
        "files": entries,
    }
    try:
        (destination / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=1), encoding="utf-8"
        )
    except OSError as exc:
        raise BackupError(f"could not write manifest: {exc}") from exc
    return entries


def verify_backup(entries: list[dict]) -> None:
    """Re-hash every copy and compare against the original (spec 5.4)."""
    for entry in entries:
        backup = Path(entry["backup"])
        if not backup.is_file():
            raise BackupError(f"backup missing: {backup}")
        if backup.stat().st_size != entry["size"]:
            raise BackupError(f"backup size mismatch: {backup}")
        if sha256(backup) != entry["sha256"]:
            raise BackupError(f"backup checksum mismatch: {backup}")


def load_manifest(backup_dir: Path) -> dict:
    manifest_path = Path(backup_dir).expanduser() / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupError(f"no {MANIFEST_NAME} in {backup_dir}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def restore(backup_dir: Path, dry_run: bool = False) -> list[tuple[Path, str]]:
    """Restore originals from a manifest (spec 5.5).

    Each backup is hash-checked before it is copied back; a corrupt backup is
    reported and skipped rather than restored.
    """
    manifest = load_manifest(backup_dir)
    outcomes: list[tuple[Path, str]] = []

    for entry in manifest["files"]:
        backup = Path(entry["backup"])
        original = Path(entry["original"])

        if not backup.is_file():
            outcomes.append((original, "backup file missing"))
            continue
        if sha256(backup) != entry["sha256"]:
            outcomes.append((original, "backup checksum mismatch - not restored"))
            continue
        if dry_run:
            outcomes.append((original, "would restore"))
            continue

        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, original)
            outcomes.append((original, "restored"))
        except OSError as exc:
            outcomes.append((original, f"restore failed: {exc}"))

    return outcomes
