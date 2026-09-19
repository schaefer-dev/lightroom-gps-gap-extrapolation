"""CLI behaviour, reports, and runs against real Lightroom sidecars."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from gps_repair.cli import main
from gps_repair.model import DEFAULT_MAX_GAP, DEFAULT_WARN_GAP, RunConfig
from conftest import REAL, requires_exiftool, write_xmp


def run(*args) -> int:
    return main(list(args))


# --- Argument handling (no ExifTool needed) --------------------------------------------

def test_max_gap_above_warn_gap_is_rejected():
    """Spec 11.3."""
    config = RunConfig(root=Path("/x"), max_gap=1000, warn_gap=500)
    with pytest.raises(ValueError, match="must be <="):
        config.validate()


def test_equal_gaps_disable_the_warn_tier():
    RunConfig(root=Path("/x"), max_gap=600, warn_gap=600).validate()


def test_defaults_match_the_spec():
    """Spec 27 (R2)."""
    assert (DEFAULT_MAX_GAP, DEFAULT_WARN_GAP) == (600.0, 1800.0)


def test_missing_root_is_a_usage_error(tmp_path):
    assert run(str(tmp_path / "nope")) == 3


def test_bad_gap_combination_exits_usage(tmp_path):
    assert run(str(tmp_path), "--max-gap", "900", "--warn-gap", "60") == 3


def test_no_arguments_prints_help():
    assert main([]) == 3


# --- Reports ----------------------------------------------------------------------------

@requires_exiftool
def test_reports_are_written(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,6.0000000N", lon="4,48.0000000E")
    write_xmp(root / "B.xmp", "2026-09-18T12:00:02.000+02:00")
    reports = tmp_path / "rep"

    assert run(str(root), "--report-dir", str(reports)) == 0

    rows = list(csv.DictReader((reports / "report.csv").open()))
    assert {r["result"] for r in rows} == {"skipped_has_gps", "updated"}

    payload = json.loads((reports / "report.json").read_text())
    assert payload["mode"] == "dry-run"
    assert payload["settings"]["max_gap_seconds"] == 600.0
    assert payload["settings"]["tie_break_rule"] == "prefer the earlier photograph"
    assert payload["summary"]["repaired"] == 1
    assert (reports / "modified_files.txt").read_text().strip().endswith("B.xmp")


@requires_exiftool
def test_report_records_warn_tier(tmp_path):
    """Spec 19 (R2): the WARN band has its own result value."""
    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,6.0000000N", lon="4,48.0000000E")
    write_xmp(root / "B.xmp", "2026-09-18T12:15:00.000+02:00")   # 900 s -> WARN
    reports = tmp_path / "rep"

    run(str(root), "--report-dir", str(reports))
    rows = {r["target_xmp"].rsplit("/", 1)[-1]: r
            for r in csv.DictReader((reports / "report.csv").open())}

    assert rows["B.xmp"]["result"] == "updated_warn"
    assert float(rows["B.xmp"]["time_difference_seconds"]) == 900.0
    assert rows["B.xmp"]["raw_present"] == "no"


@requires_exiftool
def test_same_camera_only_errors_without_serials(tmp_path):
    """Spec 12 (R2): never silently degrade to unrestricted matching."""
    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,6.0000000N", lon="4,48.0000000E")
    write_xmp(root / "B.xmp", "2026-09-18T12:00:02.000+02:00")

    assert run(str(root), "--same-camera-only") == 3


@requires_exiftool
def test_empty_library_is_handled(tmp_path):
    (tmp_path / "empty").mkdir()
    assert run(str(tmp_path / "empty")) == 0


# --- Real Lightroom sidecars -------------------------------------------------------------

@requires_exiftool
def test_real_fixtures_dry_run(tmp_path):
    """The checked-in sample sidecars, end to end."""
    root = tmp_path / "lib"
    root.mkdir()
    for src in sorted(REAL.glob("*.xmp")):
        shutil.copy2(src, root / src.name)

    reports = tmp_path / "rep"
    assert run(str(root), "--report-dir", str(reports)) == 0

    rows = {r["target_xmp"].rsplit("/", 1)[-1]: r
            for r in csv.DictReader((reports / "report.csv").open())}
    assert len(rows) == 7

    # The tight candidate sits between two donors, seconds away.
    tight = rows["A1_04962.xmp"]
    assert tight["result"] == "updated"
    assert float(tight["time_difference_seconds"]) < 10

    # The WARN-tier candidate is ~18 minutes from its donor.
    warned = rows["A1_09660.xmp"]
    assert warned["result"] == "updated_warn"
    assert 600 < float(warned["time_difference_seconds"]) <= 1800


@requires_exiftool
def test_real_fixtures_apply_and_restore(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    for src in sorted(REAL.glob("*.xmp")):
        shutil.copy2(src, root / src.name)

    originals = {p.name: p.read_bytes() for p in root.glob("*.xmp")}
    tagged_before = {n for n, b in originals.items() if b"GPSLatitude" in b}

    assert run(str(root), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk")) == 0

    # Previously tagged sidecars are untouched, byte for byte.
    for name in tagged_before:
        assert (root / name).read_bytes() == originals[name], name

    # Every sidecar now carries GPS.
    for path in root.glob("*.xmp"):
        assert "GPSLatitude" in path.read_text(encoding="utf-8"), path.name

    # And a restore puts everything back exactly.
    backup = next((tmp_path / "bk").iterdir())
    assert main(["restore", str(backup)]) == 0
    for path in root.glob("*.xmp"):
        assert path.read_bytes() == originals[path.name], path.name


@requires_exiftool
def test_filename_order_does_not_drive_matching(tmp_path):
    """Two real sidecars whose name order contradicts their capture order."""
    root = tmp_path / "lib"
    root.mkdir()
    for name in ("A1_00006.xmp", "A1_09970.xmp"):
        shutil.copy2(REAL / name, root / name)
    # A GPS-less photo timed next to the *later-named* file.
    write_xmp(root / "target.xmp", "2026-09-18T07:44:00.000+02:00")

    reports = tmp_path / "rep"
    run(str(root), "--report-dir", str(reports), "--max-gap", "3600",
        "--warn-gap", "3600")
    rows = {r["target_xmp"].rsplit("/", 1)[-1]: r
            for r in csv.DictReader((reports / "report.csv").open())}

    # A1_09970 is at 07:43:51, far closer in time than A1_00006 at 14:43.
    assert rows["target.xmp"]["donor_xmp"].endswith("A1_09970.xmp")


@requires_exiftool
def test_malformed_sidecar_does_not_crash_the_run(tmp_path):
    """Spec 6.0: exit code 0 on garbage; we must detect it by content."""
    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,6.0000000N", lon="4,48.0000000E")
    write_xmp(root / "B.xmp", "2026-09-18T12:00:02.000+02:00")
    (root / "broken.xmp").write_text("<not valid xml at all", encoding="utf-8")

    reports = tmp_path / "rep"
    assert run(str(root), "--report-dir", str(reports)) == 0

    rows = {r["target_xmp"].rsplit("/", 1)[-1]: r
            for r in csv.DictReader((reports / "report.csv").open())}
    assert rows["broken.xmp"]["result"].startswith(("skipped", "failed"))
    assert rows["B.xmp"]["result"] == "updated"   # unaffected by its neighbour


@requires_exiftool
def test_reports_never_land_in_the_working_directory(tmp_path, monkeypatch):
    """A run must not litter the user's cwd (regression).

    Covers the plain dry run and the 'nothing to do' apply path, which both
    once defaulted their report directory to os.getcwd().
    """
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,6.0000000N", lon="4,48.0000000E")
    write_xmp(root / "B.xmp", "2026-09-18T12:00:02.000+02:00")

    run(str(root))                                    # dry run
    assert list(cwd.iterdir()) == []

    # Every sidecar already has GPS -> nothing to write.
    only_tagged = tmp_path / "tagged"
    only_tagged.mkdir()
    write_xmp(only_tagged / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,6.0000000N", lon="4,48.0000000E")
    run(str(only_tagged), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"))
    assert list(cwd.iterdir()) == []
