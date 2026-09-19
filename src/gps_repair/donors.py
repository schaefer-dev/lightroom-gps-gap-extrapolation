"""Donor selection: nearest-in-time photograph with pre-existing valid GPS.

Pure logic, no I/O. Two guarantees matter here:

* The donor set is frozen by the caller before any write, so a coordinate
  written during a run can never become a donor in that same run (spec 4.4).
* Equal distances resolve to the earlier photograph (spec 10).
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime

from .model import (
    GpsState,
    SidecarHealth,
    Photo,
    PlanEntry,
    Result,
    RunConfig,
)


class DonorIndex:
    """Sorted donors supporting O(log n) nearest-neighbour lookup (spec 25)."""

    def __init__(self, donors: list[Photo]) -> None:
        # (time, path) keeps ordering total and deterministic even if two
        # photographs share a timestamp.
        self._donors = sorted(donors, key=lambda p: (p.capture_time, str(p.xmp_path)))
        self._times: list[datetime] = [p.capture_time for p in self._donors]

    def __len__(self) -> int:
        return len(self._donors)

    @property
    def donors(self) -> list[Photo]:
        return list(self._donors)

    def nearest(self, when: datetime) -> tuple[Photo | None, float | None, bool]:
        """Return (donor, gap_seconds, tie_break_applied) for a capture time.

        Ties prefer the earlier photograph, so `next` wins only on a strictly
        smaller gap.
        """
        if not self._donors:
            return None, None, False

        i = bisect_left(self._times, when)

        prev_idx = i - 1 if i > 0 else None
        next_idx = i if i < len(self._donors) else None

        prev_gap = (
            abs((when - self._times[prev_idx]).total_seconds())
            if prev_idx is not None
            else None
        )
        next_gap = (
            abs((self._times[next_idx] - when).total_seconds())
            if next_idx is not None
            else None
        )

        if prev_gap is None and next_gap is None:
            return None, None, False
        if next_gap is None:
            return self._donors[prev_idx], prev_gap, False
        if prev_gap is None:
            return self._donors[next_idx], next_gap, False

        tie = prev_gap == next_gap
        if next_gap < prev_gap:  # strict: ties keep the previous photograph
            return self._donors[next_idx], next_gap, False
        return self._donors[prev_idx], prev_gap, tie


def select_donors(photos: list[Photo], config: RunConfig) -> list[Photo]:
    """Freeze the donor set from pre-run state (spec 4.4, 24)."""
    return [p for p in photos if p.is_donor]


def _index_for(
    candidate: Photo, photos: list[Photo], config: RunConfig, index: DonorIndex
) -> DonorIndex:
    """Narrow the donor pool when a restriction flag is active (spec 12)."""
    if not (config.same_folder_only or config.same_camera_only):
        return index

    pool = index.donors
    if config.same_folder_only:
        pool = [d for d in pool if d.xmp_path.parent == candidate.xmp_path.parent]
    if config.same_camera_only:
        pool = [d for d in pool if d.camera_serial == candidate.camera_serial]
    return DonorIndex(pool)


def build_plan(photos: list[Photo], config: RunConfig) -> list[PlanEntry]:
    """Decide the outcome for every scanned photograph.

    Runs to completion before any backup or write begins (spec 24).
    """
    donor_index = DonorIndex(select_donors(photos, config))
    plan: list[PlanEntry] = []

    for photo in photos:
        plan.append(_classify(photo, photos, config, donor_index))

    return plan


def _classify(
    photo: Photo, photos: list[Photo], config: RunConfig, index: DonorIndex
) -> PlanEntry:
    """Resolve one photograph to a single outcome."""
    if photo.error:
        return PlanEntry(photo, Result.FAILED, error=photo.error,
                         reason=f"could not read metadata: {photo.error}")

    # Spec 4.7: an unsynced sidecar is reported as such, never as a plain
    # missing-timestamp skip, and is never written to.
    if not photo.is_healthy:
        health = photo.sidecar_health.value
        return PlanEntry(
            photo, Result.SKIPPED_STUB_SIDECAR,
            reason=f"sidecar is missing Lightroom metadata ({health}); "
                   "use Metadata > Save Metadata to Files first",
        )

    state = photo.gps_state

    # Spec 4.2 / 4.3 / 6.3: anything already carrying GPS is left alone.
    if state is GpsState.PRESENT:
        return PlanEntry(photo, Result.SKIPPED_HAS_GPS,
                         reason="sidecar already contains GPS")
    if state is GpsState.PARTIAL:
        return PlanEntry(photo, Result.SKIPPED_PARTIAL,
                         reason="inconsistent existing GPS metadata")
    if state is GpsState.INVALID:
        return PlanEntry(photo, Result.SKIPPED_PARTIAL,
                         reason="existing GPS coordinates are out of range")

    # Spec 8 (R2): a RAW is optional, but if present it must not already have GPS.
    if config.require_raw and photo.raw_path is None:
        return PlanEntry(photo, Result.SKIPPED_NO_RAW, reason="no RAW file")
    if photo.raw_has_gps:
        return PlanEntry(photo, Result.SKIPPED_RAW_GPS,
                         reason="source RAW already contains GPS")

    if photo.capture_time is None:
        return PlanEntry(photo, Result.SKIPPED_NO_TIME,
                         reason="no usable capture timestamp")

    scoped = _index_for(photo, photos, config, index)
    donor, gap, tie = scoped.nearest(photo.capture_time)

    if donor is None:
        return PlanEntry(photo, Result.SKIPPED_NO_DONOR,
                         reason="no photograph with valid GPS in scope")

    # Spec 11 (R2): three tiers.
    if gap > config.warn_gap:
        return PlanEntry(photo, Result.SKIPPED_MAX_GAP, donor=donor, gap_seconds=gap,
                         tie_break_applied=tie,
                         reason=f"nearest GPS donor is {gap:.2f} seconds away")

    result = Result.UPDATED_WARN if gap > config.max_gap else Result.UPDATED
    reason = ""
    if result is Result.UPDATED_WARN:
        reason = (f"donor is {gap:.0f} s ({gap / 60:.1f} min) away - "
                  "please review manually")

    return PlanEntry(photo, result, donor=donor, gap_seconds=gap,
                     lat=donor.xmp_lat, lon=donor.xmp_lon, altitude=donor.xmp_alt,
                     tie_break_applied=tie, reason=reason)
