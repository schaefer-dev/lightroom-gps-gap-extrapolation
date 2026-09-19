"""Write GPS to sidecars and verify the result (spec 15, 29).

Success is established by re-reading the files, never by ExifTool's exit
code (spec 6.0).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from . import exiftool
from .model import COORD_TOLERANCE, PlanEntry, Result


def _close(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= COORD_TOLERANCE


def apply_updates(
    entries: list[PlanEntry],
    copy_altitude: bool = False,
    tmpdir: Path | None = None,
) -> list[PlanEntry]:
    """Write planned coordinates, then verify every file by re-reading it."""
    targets = [e for e in entries if e.will_write]
    if not targets:
        return entries

    # Pre-write snapshot, for the metadata-preservation check (spec 29.5).
    before = exiftool.read_metadata([e.photo.xmp_path for e in targets], tmpdir=tmpdir)

    # Spec 13: only lat/lon by default; altitude strictly opt-in.
    updates = [
        (e.photo.xmp_path, e.lat, e.lon, e.altitude if copy_altitude else None)
        for e in targets
    ]
    rejected = dict(exiftool.write_gps(updates, tmpdir=tmpdir))

    after = exiftool.read_metadata([e.photo.xmp_path for e in targets], tmpdir=tmpdir)

    verified: list[PlanEntry] = []
    for entry in entries:
        if not entry.will_write:
            verified.append(entry)
            continue
        verified.append(_verify(entry, before, after, rejected))
    return verified


def _verify(entry: PlanEntry, before: dict, after: dict,
            rejected: dict[Path, str]) -> PlanEntry:
    """Confirm one file was written correctly, or mark it failed."""
    path = entry.photo.xmp_path

    if path in rejected:
        return replace(entry, result=Result.FAILED, error=rejected[path],
                       reason=rejected[path])

    record = after.get(path)
    if not record:
        return replace(entry, result=Result.FAILED,
                       error="file could not be re-read after writing",
                       reason="verification failed")

    lat = record and exiftool.tag(record, "GPSLatitude")
    lon = record and exiftool.tag(record, "GPSLongitude")
    try:
        lat = float(lat) if lat is not None else None
        lon = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lat = lon = None

    # Spec 29.1-29.4
    if not _close(lat, entry.lat) or not _close(lon, entry.lon):
        return replace(
            entry, result=Result.FAILED,
            error=f"verification mismatch: wrote ({entry.lat}, {entry.lon}), "
                  f"read back ({lat}, {lon})",
            reason="verification failed",
        )

    # Spec 29.5: unrelated metadata must survive.
    old = before.get(path, {})
    if old and exiftool.count_tags(record) < exiftool.count_tags(old):
        return replace(entry, result=Result.FAILED,
                       error="unrelated metadata was lost during the write",
                       reason="verification failed")

    for name in ("DateTimeOriginal", "Model"):
        if exiftool.tag(old, name) and not exiftool.tag(record, name):
            return replace(entry, result=Result.FAILED,
                           error=f"{name} is no longer readable after the write",
                           reason="verification failed")

    return entry
