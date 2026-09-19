"""Core data types.

Deliberately free of I/O so the matching logic can be tested without ExifTool
or a filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

# Spec 6.3: basic coordinate validity.
LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 180.0

# Spec 27 (R2): default donor gap tiers, in seconds.
DEFAULT_MAX_GAP = 600.0
DEFAULT_WARN_GAP = 1800.0

# Spec 29 (R2): coordinate comparison tolerance, in degrees.
COORD_TOLERANCE = 1e-6


class GpsState(Enum):
    """GPS state of a sidecar, as found before the run started."""

    PRESENT = "present"   # both lat and lon, in range -> donor
    ABSENT = "absent"     # neither -> candidate
    PARTIAL = "partial"   # exactly one -> skip, never donor (spec 4.3)
    INVALID = "invalid"   # out of range -> skip, never donor (spec 6.3)


class Result(str, Enum):
    """Per-photograph outcome. Values are the report vocabulary (spec 19)."""

    UPDATED = "updated"
    UPDATED_WARN = "updated_warn"
    SKIPPED_MAX_GAP = "skipped_max_gap"
    SKIPPED_NO_DONOR = "skipped_no_donor"
    SKIPPED_HAS_GPS = "skipped_has_gps"
    SKIPPED_PARTIAL = "skipped_partial"
    SKIPPED_RAW_GPS = "skipped_raw_gps"
    SKIPPED_NO_XMP = "skipped_no_xmp"
    SKIPPED_NO_RAW = "skipped_no_raw"
    SKIPPED_NO_TIME = "skipped_no_time"
    FAILED = "failed"


def valid_coords(lat: float | None, lon: float | None) -> bool:
    """True when both coordinates are present and within range (spec 6.3)."""
    if lat is None or lon is None:
        return False
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


@dataclass(frozen=True)
class Photo:
    """One scanned photograph, keyed on its sidecar."""

    xmp_path: Path
    raw_path: Path | None = None
    capture_time: datetime | None = None
    xmp_lat: float | None = None
    xmp_lon: float | None = None
    raw_lat: float | None = None
    raw_lon: float | None = None
    xmp_alt: float | None = None
    camera_model: str | None = None
    camera_serial: str | None = None
    error: str | None = None

    @property
    def gps_state(self) -> GpsState:
        """Classify the sidecar's own GPS (spec 6.1). The single eligibility source."""
        has_lat = self.xmp_lat is not None
        has_lon = self.xmp_lon is not None
        if has_lat != has_lon:
            return GpsState.PARTIAL
        if not has_lat:
            return GpsState.ABSENT
        if not valid_coords(self.xmp_lat, self.xmp_lon):
            return GpsState.INVALID
        return GpsState.PRESENT

    @property
    def raw_has_gps(self) -> bool:
        """True when a RAW is present and carries valid GPS (spec 6.2)."""
        return self.raw_path is not None and valid_coords(self.raw_lat, self.raw_lon)

    @property
    def is_donor(self) -> bool:
        """Only sidecars with valid pre-run GPS may donate (spec 4.4)."""
        return self.gps_state is GpsState.PRESENT and self.capture_time is not None

    @property
    def name(self) -> str:
        return self.xmp_path.name


@dataclass(frozen=True)
class PlanEntry:
    """A decision about one photograph. Produced before any write occurs."""

    photo: Photo
    result: Result
    donor: Photo | None = None
    gap_seconds: float | None = None
    lat: float | None = None
    lon: float | None = None
    altitude: float | None = None
    tie_break_applied: bool = False
    reason: str = ""
    backup_path: Path | None = None
    error: str = ""

    @property
    def will_write(self) -> bool:
        return self.result in (Result.UPDATED, Result.UPDATED_WARN)

    @property
    def is_warn(self) -> bool:
        return self.result is Result.UPDATED_WARN


@dataclass
class RunConfig:
    """Resolved settings for one run."""

    root: Path
    apply: bool = False
    max_gap: float = DEFAULT_MAX_GAP
    warn_gap: float = DEFAULT_WARN_GAP
    backup_dir: Path | None = None
    backup_mode: str = "candidate-only"
    same_folder_only: bool = False
    same_camera_only: bool = False
    require_raw: bool = False
    copy_altitude: bool = False
    report_dir: Path | None = None
    assume_tz: str = "local"
    assume_yes: bool = False
    verbose: bool = False

    def validate(self) -> None:
        """Spec 11.3: the tiers must be ordered."""
        if self.max_gap < 0 or self.warn_gap < 0:
            raise ValueError("gap values must not be negative")
        if self.max_gap > self.warn_gap:
            raise ValueError(
                f"--max-gap ({self.max_gap:g}s) must be <= --warn-gap ({self.warn_gap:g}s)"
            )
