"""Unsynced ("stub") sidecar detection - spec 4.7.

A stub is a sidecar Lightroom has never written its catalog metadata to: it
carries a rating and little else. Repairing GPS in a library containing stubs
is unsafe, because the workflow this tool recommends afterwards - Lightroom's
"Read Metadata from Files" - would replace catalog metadata with near-empty
sidecars.

Every case here was observed in a real library of 4,164 sidecars, where 514
stubs sat in two folders.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from gps_repair import exiftool
from gps_repair.cli import main
from gps_repair.model import Photo, Result, SidecarHealth, STUB_TAG_THRESHOLD
from conftest import FIXTURES, REAL, requires_exiftool, write_xmp

STUB_FIXTURE = FIXTURES / "stub" / "stub_rating_only.xmp"

DONOR = dict(lat="53,6.0000000N", lon="4,48.0000000E")


def run(*args) -> int:
    return main(list(args))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digests(root: Path) -> dict[Path, str]:
    return {p: digest(p) for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def stub_library(tmp_path) -> Path:
    """A donor, a repairable photo, and a stub - all in one folder."""
    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00", **DONOR)
    write_xmp(root / "B.xmp", "2026-09-18T12:00:02.000+02:00")
    shutil.copy2(STUB_FIXTURE, root / "C.xmp")
    return root


# --- Detection ---------------------------------------------------------------------

def test_stub_classified_without_io():
    """No timestamp and no camera model is the defining signature."""
    assert Photo(Path("a.xmp")).sidecar_health is SidecarHealth.STUB


def test_sparse_sidecar_classified():
    """A timestamp is not enough if the sidecar is implausibly thin."""
    from datetime import datetime, timezone
    thin = Photo(Path("a.xmp"), capture_time=datetime.now(timezone.utc),
                 camera_model="ILCE-1M2", tag_count=10)
    assert thin.sidecar_health is SidecarHealth.SPARSE


def test_healthy_sidecar_classified():
    from datetime import datetime, timezone
    ok = Photo(Path("a.xmp"), capture_time=datetime.now(timezone.utc),
               camera_model="ILCE-1M2", tag_count=78)
    assert ok.sidecar_health is SidecarHealth.OK
    assert ok.is_healthy


@pytest.mark.parametrize("count,healthy", [
    (STUB_TAG_THRESHOLD - 1, False),
    (STUB_TAG_THRESHOLD, True),
    (78, True),        # the thinnest real sidecar measured in the library
    (6, False),        # the stub count measured in the library
])
def test_threshold_boundary(count, healthy):
    """Locks in the 6-vs-78 gap the threshold was calibrated against."""
    from datetime import datetime, timezone
    p = Photo(Path("a.xmp"), capture_time=datetime.now(timezone.utc),
              camera_model="ILCE-1M2", tag_count=count)
    assert p.is_healthy is healthy


@requires_exiftool
def test_real_stub_fixture_is_detected(tmp_path):
    """The verbatim sidecar observed in the real library."""
    record = exiftool.read_metadata([STUB_FIXTURE], tmpdir=tmp_path,
                                    all_tags=True)[STUB_FIXTURE]
    assert exiftool.count_tags(record) == 6
    assert exiftool.tag(record, "DateTimeOriginal") is None
    assert exiftool.tag(record, "Model") is None


@requires_exiftool
def test_real_lightroom_fixtures_are_healthy(tmp_path):
    """No false positives on genuine sidecars (all 7 checked-in fixtures)."""
    paths = sorted(REAL.glob("*.xmp"))
    assert paths, "real fixtures missing"
    records = exiftool.read_metadata(paths, tmpdir=tmp_path, all_tags=True)
    for path in paths:
        assert exiftool.count_tags(records[path]) >= 78, path.name


# --- The abort gate ------------------------------------------------------------------

@requires_exiftool
def test_stub_aborts_apply_run(stub_library, tmp_path, capsys):
    """Spec 4.7: nothing is written, and no backup is even created."""
    before = tree_digests(stub_library)
    backup = tmp_path / "never"

    assert run(str(stub_library), "--apply", "--yes", "--backup-dir", str(backup)) == 2

    assert tree_digests(stub_library) == before   # not one byte changed
    assert not backup.exists()                    # aborted before backing up


@requires_exiftool
def test_abort_message_names_folder_and_remedy(stub_library, tmp_path, capsys):
    run(str(stub_library), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk"))
    out = capsys.readouterr().out

    assert "missing their Lightroom metadata" in out
    assert "Save Metadata to Files" in out
    assert "Read Metadata from Files" in out     # states the consequence
    assert "No files were modified." in out


@requires_exiftool
def test_dry_run_reports_stubs_without_aborting(stub_library, tmp_path, capsys):
    """A read-only diagnosis must still work on a broken library."""
    code = run(str(stub_library), "--report-dir", str(tmp_path / "rep"))
    out = capsys.readouterr().out

    assert code == 0
    assert "missing their Lightroom metadata" in out
    assert (tmp_path / "rep" / "report.csv").is_file()


@requires_exiftool
def test_allow_stubs_continues_but_skips_them(stub_library, tmp_path):
    """The escape hatch repairs healthy files and still never writes a stub."""
    stub = stub_library / "C.xmp"
    before = digest(stub)

    assert run(str(stub_library), "--apply", "--yes", "--allow-stubs",
               "--backup-dir", str(tmp_path / "bk")) == 0

    assert digest(stub) == before                                  # untouched
    assert "GPSLatitude" in (stub_library / "B.xmp").read_text()   # healthy one repaired


# --- Stubs are inert -------------------------------------------------------------------

@requires_exiftool
def test_stub_never_receives_gps(tmp_path):
    """Even seconds away from a donor, a stub is never written."""
    root = tmp_path / "lib"
    root.mkdir()
    write_xmp(root / "A.xmp", "2026-09-18T12:00:00.000+02:00", **DONOR)
    stub = Path(shutil.copy2(STUB_FIXTURE, root / "B.xmp"))
    before = digest(stub)

    run(str(root), "--apply", "--yes", "--allow-stubs", "--backup-dir", str(tmp_path / "bk"))
    assert digest(stub) == before
    assert "GPSLatitude" not in stub.read_text()


@requires_exiftool
def test_stub_is_never_a_donor(tmp_path):
    """A stub has no coordinates to give, and must not be consulted."""
    root = tmp_path / "lib"
    root.mkdir()
    shutil.copy2(STUB_FIXTURE, root / "A.xmp")
    write_xmp(root / "B.xmp", "2026-09-18T12:00:02.000+02:00")

    reports = tmp_path / "rep"
    run(str(root), "--report-dir", str(reports))
    rows = {r["target_xmp"].rsplit("/", 1)[-1]: r
            for r in csv.DictReader((reports / "report.csv").open())}

    assert rows["B.xmp"]["result"] == "skipped_no_donor"
    assert rows["B.xmp"]["donor_xmp"] == ""


@requires_exiftool
def test_stub_reported_distinctly_not_as_missing_time(stub_library, tmp_path):
    """Regression: v1 reported these as skipped_no_time, which hid the problem."""
    reports = tmp_path / "rep"
    run(str(stub_library), "--report-dir", str(reports))
    rows = {r["target_xmp"].rsplit("/", 1)[-1]: r
            for r in csv.DictReader((reports / "report.csv").open())}

    assert rows["C.xmp"]["result"] == "skipped_stub_sidecar"
    assert rows["C.xmp"]["sidecar_health"] == "stub"
    assert "Save Metadata to Files" in rows["C.xmp"]["reason"]


@requires_exiftool
def test_stub_not_rescued_by_adjacent_raw(tmp_path):
    """The RAW may hold good metadata; the sidecar is still unsynced."""
    root = tmp_path / "lib"
    root.mkdir()
    stub = Path(shutil.copy2(STUB_FIXTURE, root / "B.xmp"))
    (root / "B.ARW").write_bytes(b"\x00not a real raw\xff")

    reports = tmp_path / "rep"
    run(str(root), "--report-dir", str(reports))
    rows = list(csv.DictReader((reports / "report.csv").open()))

    assert rows[0]["result"] == "skipped_stub_sidecar"


# --- Mixed and nested libraries ----------------------------------------------------------

@requires_exiftool
def test_mixed_folder_counts_correctly(tmp_path, capsys):
    """The real case: stubs and healthy sidecars side by side in one folder."""
    root = tmp_path / "2026-09_Texel"
    day = root / "2026-09-16_Texel"
    day.mkdir(parents=True)
    for i in range(5):
        shutil.copy2(STUB_FIXTURE, day / f"stub_{i}.xmp")
    write_xmp(day / "real.xmp", "2026-09-18T12:00:00.000+02:00", **DONOR)

    assert run(str(root), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk")) == 2
    out = capsys.readouterr().out
    assert "5" in out and "2026-09-16_Texel" in out


@requires_exiftool
def test_healthy_nested_library_is_unaffected(tmp_path):
    """Multi-folder libraries with no stubs behave exactly as before."""
    root = tmp_path / "lib"
    (root / "day1").mkdir(parents=True)
    (root / "day2").mkdir(parents=True)
    write_xmp(root / "day1" / "A.xmp", "2026-09-18T12:00:00.000+02:00", **DONOR)
    write_xmp(root / "day1" / "B.xmp", "2026-09-18T12:00:02.000+02:00")
    write_xmp(root / "day2" / "C.xmp", "2026-09-18T13:00:00.000+02:00", **DONOR)

    assert run(str(root), "--apply", "--yes", "--backup-dir", str(tmp_path / "bk")) == 0
    assert "GPSLatitude" in (root / "day1" / "B.xmp").read_text()


@requires_exiftool
def test_json_report_tallies_stubs_by_folder(stub_library, tmp_path):
    reports = tmp_path / "rep"
    run(str(stub_library), "--report-dir", str(reports))
    payload = json.loads((reports / "report.json").read_text())

    assert payload["summary"]["unsynced_sidecars"] == 1
    assert sum(payload["unsynced_by_folder"].values()) == 1
