"""Donor selection and the three-tier gap policy (spec 9, 10, 11, 23, 31)."""

from __future__ import annotations

import pytest

from gps_repair.donors import DonorIndex, build_plan, select_donors
from gps_repair.model import GpsState, Result, RunConfig

from conftest import at, photo

GPS = dict(lat=53.1, lon=4.8)
GPS_B = dict(lat=53.2, lon=4.9)


def plan_for(photos, **kw):
    cfg = RunConfig(root="/lib", **kw)
    return {e.photo.name: e for e in build_plan(photos, cfg)}


# --- Nearest-neighbour selection ----------------------------------------------------

def test_nearest_previous_donor():
    """Spec 31: 12:00 GPS, 12:00:04 missing, 12:01 GPS -> takes the previous."""
    p = plan_for([
        photo("A", 0, **GPS),
        photo("B", 4),
        photo("C", 60, **GPS_B),
    ])
    assert p["B.xmp"].result is Result.UPDATED
    assert p["B.xmp"].donor.name == "A.xmp"
    assert p["B.xmp"].gap_seconds == 4


def test_nearest_following_donor():
    """Spec 31: the later donor wins when it is closer."""
    p = plan_for([
        photo("A", 0, **GPS),
        photo("B", 56),
        photo("C", 60, **GPS_B),
    ])
    assert p["B.xmp"].donor.name == "C.xmp"
    assert p["B.xmp"].gap_seconds == 4


def test_scenario_a_closer_following_donor():
    """Spec 23-A: 3 s back vs 2 s forward -> forward."""
    p = plan_for([photo("A", 0, **GPS), photo("B", 3), photo("C", 5, **GPS_B)])
    assert p["B.xmp"].donor.name == "C.xmp"


def test_tie_prefers_previous():
    """Spec 10: equal distance resolves to the earlier photograph."""
    p = plan_for([photo("A", 0, **GPS), photo("B", 5), photo("C", 10, **GPS_B)])
    entry = p["B.xmp"]
    assert entry.donor.name == "A.xmp"
    assert entry.tie_break_applied is True


def test_leading_gap_uses_following_donor():
    """Spec 23-B: the camera-startup case."""
    p = plan_for([photo("A", 0), photo("B", 2), photo("C", 4, **GPS)])
    assert p["A.xmp"].donor.name == "C.xmp"
    assert p["B.xmp"].donor.name == "C.xmp"
    assert p["A.xmp"].gap_seconds == 4
    assert p["B.xmp"].gap_seconds == 2


def test_trailing_gap_uses_previous_donor():
    """Spec 23-C: GPS disappears before shutdown."""
    p = plan_for([photo("A", 0, **GPS), photo("B", 4), photo("C", 7)])
    assert p["B.xmp"].donor.name == "A.xmp"
    assert p["C.xmp"].donor.name == "A.xmp"


def test_distant_earlier_donor_is_irrelevant():
    """Spec 23-D: sorted position does not matter, only time distance."""
    p = plan_for([photo("A", 0, **GPS), photo("B", 7200), photo("C", 7204, **GPS_B)])
    assert p["B.xmp"].donor.name == "C.xmp"


def test_no_donor_at_all():
    p = plan_for([photo("A", 0), photo("B", 5)])
    assert p["A.xmp"].result is Result.SKIPPED_NO_DONOR


# --- Three-tier gap policy (spec 11, R2) --------------------------------------------

def test_tier_apply_silently():
    p = plan_for([photo("A", 0, **GPS), photo("B", 300)])
    assert p["B.xmp"].result is Result.UPDATED
    assert p["B.xmp"].is_warn is False


def test_tier_warn_band_still_applies():
    """600-1800 s: coordinate applied, but flagged for review."""
    p = plan_for([photo("A", 0, **GPS), photo("B", 1095)])
    entry = p["B.xmp"]
    assert entry.result is Result.UPDATED_WARN
    assert entry.will_write is True
    assert entry.lat == 53.1
    assert "review manually" in entry.reason


def test_tier_beyond_warn_gap_is_skipped():
    p = plan_for([photo("A", 0, **GPS), photo("B", 1801)])
    entry = p["B.xmp"]
    assert entry.result is Result.SKIPPED_MAX_GAP
    assert entry.will_write is False
    assert entry.lat is None


@pytest.mark.parametrize("gap,expected", [
    (599.9, Result.UPDATED),
    (600.0, Result.UPDATED),        # boundary: <= max_gap applies silently
    (600.1, Result.UPDATED_WARN),
    (1800.0, Result.UPDATED_WARN),  # boundary: <= warn_gap still applies
    (1800.1, Result.SKIPPED_MAX_GAP),
])
def test_tier_boundaries_are_exact(gap, expected):
    """Spec 31 (R2): boundary behaviour is specified exactly."""
    p = plan_for([photo("A", 0, **GPS), photo("B", gap)])
    assert p["B.xmp"].result is expected


def test_scenario_e_no_nearby_location():
    """Spec 23-E: a 2 h gap is skipped under the defaults."""
    p = plan_for([photo("A", 0, **GPS), photo("B", 7200)])
    assert p["B.xmp"].result is Result.SKIPPED_MAX_GAP


# --- Safety invariants ---------------------------------------------------------------

def test_existing_gps_is_never_a_candidate():
    """Spec 4.2, 23-F."""
    p = plan_for([photo("A", 0, **GPS), photo("B", 2, lat=99.9, lon=99.9)])
    # B has GPS (albeit odd); it must be left alone, not corrected.
    assert p["B.xmp"].result is not Result.UPDATED
    assert p["B.xmp"].will_write is False


def test_partial_gps_skipped_and_not_a_donor():
    """Spec 4.3: lat without lon is skipped, and cannot donate."""
    lat_only = photo("P", 0, lat=53.1)
    lon_only = photo("Q", 1, lon=4.8)
    target = photo("B", 2)
    p = plan_for([lat_only, lon_only, target])

    assert lat_only.gps_state is GpsState.PARTIAL
    assert lon_only.gps_state is GpsState.PARTIAL
    assert p["P.xmp"].result is Result.SKIPPED_PARTIAL
    assert p["Q.xmp"].result is Result.SKIPPED_PARTIAL
    # With no complete donor anywhere, the target cannot be repaired.
    assert p["B.xmp"].result is Result.SKIPPED_NO_DONOR


def test_out_of_range_coordinates_rejected_as_donor():
    """Spec 6.3: implausible coordinates never donate."""
    bad = photo("A", 0, lat=91.0, lon=181.0)
    assert bad.gps_state is GpsState.INVALID
    p = plan_for([bad, photo("B", 2)])
    assert p["B.xmp"].result is Result.SKIPPED_NO_DONOR


def test_no_propagation_within_a_run():
    """Spec 4.4 / 23-G: repaired coordinates never become donors.

    With narrowed gaps only the first gap-photo is in range; the later ones
    must stay skipped rather than chaining off it.
    """
    photos = [photo("A", 0, **GPS), photo("B", 60), photo("C", 120), photo("D", 180)]
    p = plan_for(photos, max_gap=90, warn_gap=90)

    assert p["B.xmp"].result is Result.UPDATED
    assert p["C.xmp"].result is Result.SKIPPED_MAX_GAP
    assert p["D.xmp"].result is Result.SKIPPED_MAX_GAP
    # Every gap is measured against the original donor, never a repaired file.
    assert p["C.xmp"].donor.name == "A.xmp"


def test_donor_set_excludes_candidates():
    """The frozen donor set structurally cannot contain a repair target."""
    photos = [photo("A", 0, **GPS), photo("B", 60), photo("C", 120)]
    donors = select_donors(photos, RunConfig(root="/lib"))
    assert [d.name for d in donors] == ["A.xmp"]


def test_missing_timestamp_is_skipped():
    """A synced sidecar that merely lacks a usable timestamp.

    It still carries camera metadata, so it is a timestamp problem rather than
    an unsynced sidecar (spec 4.7).
    """
    orphan_time = photo("B", None, camera_model="ILCE-1M2", tag_count=80)
    p = plan_for([photo("A", 0, **GPS), orphan_time])
    assert p["B.xmp"].result is Result.SKIPPED_NO_TIME


def test_sidecar_without_time_or_camera_is_a_stub():
    """No timestamp AND no camera model means Lightroom never wrote it."""
    p = plan_for([photo("A", 0, **GPS), photo("B", None)])
    assert p["B.xmp"].result is Result.SKIPPED_STUB_SIDECAR


def test_raw_with_gps_is_skipped():
    """Spec 6.2: a RAW that already has GPS blocks the repair."""
    from pathlib import Path
    target = photo("B", 2, raw_path=Path("/lib/B.ARW"), raw_lat=53.0, raw_lon=4.0)
    p = plan_for([photo("A", 0, **GPS), target])
    assert p["B.xmp"].result is Result.SKIPPED_RAW_GPS


def test_raw_absent_is_fine_by_default():
    """Spec 2.1 (R2): sidecar-only libraries are supported."""
    p = plan_for([photo("A", 0, **GPS), photo("B", 2)])
    assert p["B.xmp"].result is Result.UPDATED


def test_require_raw_skips_when_absent():
    p = plan_for([photo("A", 0, **GPS), photo("B", 2)], require_raw=True)
    assert p["B.xmp"].result is Result.SKIPPED_NO_RAW


# --- Index behaviour -----------------------------------------------------------------

def test_donor_index_sorts_by_time_not_name():
    """Filename order must never drive matching."""
    index = DonorIndex([photo("Z", 0, **GPS), photo("A", 100, **GPS)])
    donor, gap, _ = index.nearest(at(1))
    assert donor.name == "Z.xmp"
    assert gap == 1


def test_empty_index_returns_nothing():
    donor, gap, tie = DonorIndex([]).nearest(at(0))
    assert (donor, gap, tie) == (None, None, False)
