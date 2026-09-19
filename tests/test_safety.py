"""End-to-end safety invariants - the tests that matter most.

These exercise the real ExifTool binary against real files.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from gps_repair.cli import main
from conftest import REAL, requires_exiftool, write_xmp

pytestmark = requires_exiftool


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def all_tags(path: Path) -> dict:
    """Every tag in a file. read_metadata() fetches only the tags the tool needs,
    so preservation checks must ask ExifTool for the full set."""
    import json
    import subprocess
    out = subprocess.run(
        ["exiftool", "-j", "-a", "-G1", "-n", str(path)],
        capture_output=True, text=True,
    )
    record = json.loads(out.stdout)[0]
    return {k: v for k, v in record.items()
            if not k.startswith(("SourceFile", "System:", "File:", "ExifTool:"))}


def tree_digests(root: Path) -> dict[Path, str]:
    return {p: digest(p) for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def lib(tmp_path) -> Path:
    """A library with two donors, one repairable gap, and one far-away gap."""
    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,6.0000000N", lon="4,48.0000000E")
    write_xmp(root / "B.xmp", "2026-09-18T12:00:03.000+02:00")           # repairable
    write_xmp(root / "C.xmp", "2026-09-18T12:00:05.000+02:00",
              lat="53,7.0000000N", lon="4,49.0000000E")
    write_xmp(root / "D.xmp", "2026-09-18T18:00:00.000+02:00")           # too far
    return root


def run(*args) -> int:
    return main(list(args))


# --- The three user-stated guarantees -------------------------------------------------

def test_only_gps_less_sidecars_are_modified(lib, tmp_path):
    """Files that already have GPS must be byte-identical afterwards."""
    before = tree_digests(lib)
    assert run(str(lib), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk")) == 0
    after = tree_digests(lib)

    assert after[lib / "A.xmp"] == before[lib / "A.xmp"]
    assert after[lib / "C.xmp"] == before[lib / "C.xmp"]
    assert after[lib / "B.xmp"] != before[lib / "B.xmp"]   # the only repair
    assert after[lib / "D.xmp"] == before[lib / "D.xmp"]   # beyond the gap


def test_raw_files_are_never_modified(lib, tmp_path):
    """Spec 4.1 / 31: RAW checksums must be unchanged."""
    raw = lib / "B.ARW"
    raw.write_bytes(b"\x00RAW SENSOR DATA\xff" * 512)
    before = digest(raw)

    run(str(lib), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"))
    assert digest(raw) == before


def test_no_non_xmp_file_is_touched(lib, tmp_path):
    """Only .xmp sidecars may change."""
    (lib / "notes.txt").write_text("keep me")
    (lib / "photo.jpg").write_bytes(b"jpeg")
    before = tree_digests(lib)

    run(str(lib), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"))
    after = tree_digests(lib)

    changed = {p for p in before if before[p] != after.get(p)}
    assert all(p.suffix.lower() == ".xmp" for p in changed), changed


def test_backup_exists_before_any_modification(lib, tmp_path):
    """Spec 5.1: a backup failure aborts before a single file changes."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    before = tree_digests(lib)
    try:
        code = run(str(lib), "--apply", "--yes", "--backup-dir", str(blocked / "bk"))
    finally:
        blocked.chmod(0o700)

    assert code == 2                      # aborted
    assert tree_digests(lib) == before    # nothing modified


def test_every_modified_file_has_a_verified_backup(lib, tmp_path):
    """Spec 31: no original is modified unless its backup already exists."""
    backup_root = tmp_path / "bk"
    run(str(lib), "--apply", "--yes", "--backup-dir", str(backup_root))

    manifest_dirs = list(backup_root.iterdir())
    assert len(manifest_dirs) == 1
    backed_up = list((manifest_dirs[0] / "files").rglob("*.xmp"))
    assert [p.name for p in backed_up] == ["B.xmp"]
    # The backup holds the pre-repair content: no GPS.
    assert "GPSLatitude" not in backed_up[0].read_text()


# --- Dry run ---------------------------------------------------------------------------

def test_dry_run_modifies_nothing(lib, tmp_path):
    """Spec 16."""
    before = tree_digests(lib)
    assert run(str(lib), "--report-dir", str(tmp_path / "rep")) == 0
    assert tree_digests(lib) == before


def test_dry_run_matches_apply_decisions(lib, tmp_path):
    """Spec 31: identical donor decisions in both modes."""
    import csv

    run(str(lib), "--report-dir", str(tmp_path / "dry"))
    dry = {r["target_xmp"]: (r["donor_xmp"], r["time_difference_seconds"])
           for r in csv.DictReader((tmp_path / "dry" / "report.csv").open())}

    run(str(lib), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"),
        "--report-dir", str(tmp_path / "wet"))
    wet = {r["target_xmp"]: (r["donor_xmp"], r["time_difference_seconds"])
           for r in csv.DictReader((tmp_path / "wet" / "report.csv").open())}

    assert dry == wet


# --- Sidecar creation ------------------------------------------------------------------

def test_sidecar_is_never_created_for_orphan_raw(tmp_path):
    """Spec 8: a RAW without a sidecar gets no sidecar."""
    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,6.0000000N", lon="4,48.0000000E")
    (root / "B.ARW").write_bytes(b"raw")

    run(str(root), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"))
    assert not (root / "B.xmp").exists()


def test_vanished_target_is_failed_not_created(lib, tmp_path, monkeypatch):
    """Spec 4.5: ExifTool would create it; we must not.

    The sidecar is deleted after planning but before the write.
    """
    from gps_repair import writer as writer_mod
    real_apply = writer_mod.apply_updates

    def delete_then_write(entries, **kw):
        for entry in entries:
            if entry.will_write:
                entry.photo.xmp_path.unlink()
        return real_apply(entries, **kw)

    monkeypatch.setattr("gps_repair.cli.writer.apply_updates", delete_then_write)
    code = run(str(lib), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"))

    assert code == 1                       # partial failure
    assert not (lib / "B.xmp").exists()    # never resurrected


# --- Existing GPS is authoritative ------------------------------------------------------

def test_partial_gps_is_left_alone(tmp_path):
    """Spec 4.3: latitude without longitude is skipped, not completed."""
    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,6.0000000N", lon="4,48.0000000E")
    partial = write_xmp(root / "B.xmp", "2026-09-18T12:00:02.000+02:00",
                        lat="53,9.0000000N")
    before = digest(partial)

    run(str(root), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"))
    assert digest(partial) == before


def test_exiftool_guard_refuses_a_tagged_file(tmp_path):
    """Spec 4.6: the ExifTool-level lock works independently of our classifier."""
    from gps_repair.exiftool import write_gps

    sidecar = write_xmp(tmp_path / "A.xmp", "2026-09-18T12:00:00.000+02:00",
                        lat="53,6.0000000N", lon="4,48.0000000E")
    before = digest(sidecar)

    # Force a write that our planner would never schedule.
    write_gps([(sidecar, 1.0, 2.0, None)], tmpdir=tmp_path)
    assert digest(sidecar) == before


def test_write_rejects_missing_path(tmp_path):
    """The existence assertion, exercised directly."""
    from gps_repair.exiftool import write_gps

    ghost = tmp_path / "ghost.xmp"
    rejected = write_gps([(ghost, 1.0, 2.0, None)], tmpdir=tmp_path)

    assert not ghost.exists()
    assert rejected and "no longer exists" in rejected[0][1]


# --- Metadata preservation ---------------------------------------------------------------

def test_unrelated_metadata_survives(tmp_path):
    """Spec 14: ratings, crop, creator and the rest must be preserved."""
    from gps_repair import exiftool

    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,6.0000000N", lon="4,48.0000000E")
    target = write_xmp(root / "B.xmp", "2026-09-18T12:00:02.000+02:00", rating=4)

    before = all_tags(target)
    assert before.get("XMP-xmp:Rating") == 4       # real metadata present

    run(str(root), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"))
    after = all_tags(target)

    assert after.get("XMP-exif:GPSLatitude") is not None   # repaired
    for key, value in before.items():
        if "GPS" in key or "XMPToolkit" in key:
            continue
        assert after.get(key) == value, f"{key} changed"


def test_real_lightroom_sidecar_metadata_survives(tmp_path):
    """The same guarantee, against a genuine Lightroom sidecar."""
    from gps_repair import exiftool

    root = tmp_path / "lib"
    root.mkdir()
    donor = shutil.copy2(REAL / "A1_04866.xmp", root / "donor.xmp")
    target = Path(shutil.copy2(REAL / "A1_04962.xmp", root / "target.xmp"))

    before = all_tags(target)
    # A genuine Lightroom sidecar carries develop settings, crop, creator, history.
    assert len(before) > 50, f"expected a rich sidecar, got {len(before)} tags"

    run(str(root), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"))
    after = all_tags(target)

    assert after.get("XMP-exif:GPSLatitude") is not None   # repaired
    lost = {k: v for k, v in before.items()
            if "GPS" not in k and "XMPToolkit" not in k and after.get(k) != v}
    assert not lost, f"metadata lost or altered: {sorted(lost)}"


# --- Restore --------------------------------------------------------------------------

def test_restore_returns_library_to_original_state(lib, tmp_path):
    before = tree_digests(lib)
    run(str(lib), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"))
    assert tree_digests(lib) != before

    backup = next((tmp_path / "bk").iterdir())
    assert main(["restore", str(backup)]) == 0
    assert tree_digests(lib) == before
