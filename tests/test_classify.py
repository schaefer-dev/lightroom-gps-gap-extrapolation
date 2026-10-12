"""Timestamp parsing, GPS classification, and scanning (spec 2, 6, 7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gps_repair.classify import parse_timestamp, _coord
from gps_repair.model import GpsState, Photo
from gps_repair.scan import scan

LOCAL = timezone(timedelta(hours=2))


# --- Timestamps ----------------------------------------------------------------------

def test_parses_offset_and_subseconds():
    """The real-world Lightroom form."""
    when = parse_timestamp("2026:09:18 14:43:24.485+02:00", None)
    assert when == datetime(2026, 9, 18, 14, 43, 24, 485000, tzinfo=LOCAL)


def test_parses_utc_z():
    when = parse_timestamp("2026:09:18 12:43:24Z", None)
    assert when.tzinfo == timezone.utc


def test_naive_timestamp_uses_assumed_tz():
    """Spec 7 (R2): naive values land in one consistent domain."""
    when = parse_timestamp("2026:09:18 14:43:24", LOCAL)
    assert when.utcoffset() == timedelta(hours=2)


def test_naive_and_aware_are_comparable():
    """Mixing the two must not raise."""
    a = parse_timestamp("2026:09:18 14:43:24", LOCAL)
    b = parse_timestamp("2026:09:18 14:43:25+02:00", LOCAL)
    assert (b - a).total_seconds() == 1


@pytest.mark.parametrize("value", [None, "", "not a date", "0000:00:00 00:00:00", "garbage"])
def test_unparseable_timestamps_return_none(value):
    assert parse_timestamp(value, None) is None


def test_coord_parsing():
    assert _coord(53.1) == 53.1
    assert _coord("4.8") == 4.8
    assert _coord(None) is None
    assert _coord("") is None
    assert _coord("north") is None


# --- GPS classification ----------------------------------------------------------------

def test_state_present():
    assert Photo(Path("a.xmp"), xmp_lat=53.1, xmp_lon=4.8).gps_state is GpsState.PRESENT


def test_state_absent():
    assert Photo(Path("a.xmp")).gps_state is GpsState.ABSENT


@pytest.mark.parametrize("lat,lon", [(53.1, None), (None, 4.8)])
def test_state_partial(lat, lon):
    """Spec 4.3."""
    assert Photo(Path("a.xmp"), xmp_lat=lat, xmp_lon=lon).gps_state is GpsState.PARTIAL


@pytest.mark.parametrize("lat,lon", [(91.0, 4.8), (53.1, 181.0), (-91.0, 0.0)])
def test_state_invalid(lat, lon):
    """Spec 6.3."""
    assert Photo(Path("a.xmp"), xmp_lat=lat, xmp_lon=lon).gps_state is GpsState.INVALID


def test_boundary_coordinates_are_valid():
    assert Photo(Path("a.xmp"), xmp_lat=90.0, xmp_lon=180.0).gps_state is GpsState.PRESENT
    assert Photo(Path("a.xmp"), xmp_lat=-90.0, xmp_lon=-180.0).gps_state is GpsState.PRESENT


def test_zero_coordinates_count_as_present():
    """0,0 is a real (if unlikely) location - not 'missing'. It must not be overwritten."""
    assert Photo(Path("a.xmp"), xmp_lat=0.0, xmp_lon=0.0).gps_state is GpsState.PRESENT


def test_raw_gps_only_counts_when_raw_present():
    """Spec 6.2 (R2)."""
    no_raw = Photo(Path("a.xmp"), raw_lat=53.1, raw_lon=4.8)
    assert no_raw.raw_has_gps is False
    with_raw = Photo(Path("a.xmp"), raw_path=Path("a.ARW"), raw_lat=53.1, raw_lon=4.8)
    assert with_raw.raw_has_gps is True


# --- Scanning ---------------------------------------------------------------------------

def test_scan_pairs_case_insensitively(tmp_path):
    """Spec 2: .XMP/.ARW in any case."""
    (tmp_path / "A.XMP").write_text("x")
    (tmp_path / "A.arw").write_bytes(b"raw")
    (tmp_path / "B.xmp").write_text("x")
    result = scan(tmp_path)
    assert len(result.sidecars) == 2
    from gps_repair.scan import raw_for
    assert raw_for(tmp_path / "A.XMP", result.raws) is not None
    assert raw_for(tmp_path / "B.xmp", result.raws) is None


def test_scan_is_recursive(tmp_path):
    nested = tmp_path / "2026" / "Coast"
    nested.mkdir(parents=True)
    (nested / "A.xmp").write_text("x")
    assert len(scan(tmp_path).sidecars) == 1


def test_scan_reports_orphan_raws(tmp_path):
    """Spec 8: a RAW with no sidecar is reported, never given one."""
    (tmp_path / "A.ARW").write_bytes(b"raw")
    result = scan(tmp_path)
    assert result.sidecars == []
    assert len(result.orphan_raws) == 1


def test_scan_ignores_unrelated_files(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "photo.jpg").write_bytes(b"x")
    assert scan(tmp_path).sidecars == []
