"""Turn raw ExifTool output into Photo objects (spec 6, 7)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import exiftool
from .model import Photo
from .scan import ScanResult, raw_for

# ExifTool emits "2026:09:18 14:43:24.485+02:00".
_DT = re.compile(
    r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(\.\d+)?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_timestamp(value: object, assume_tz: timezone | None) -> datetime | None:
    """Parse an ExifTool timestamp into an aware datetime.

    Naive values are placed in `assume_tz` so all comparisons stay within one
    domain (spec 7, R2).
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    match = _DT.match(text)
    if not match:
        return None

    year, month, day, hour, minute, second, frac, offset = match.groups()
    micro = int(round(float(frac or 0) * 1_000_000))
    try:
        stamp = datetime(int(year), int(month), int(day),
                         int(hour), int(minute), int(second), micro)
    except ValueError:
        return None

    if offset in (None, ""):
        return stamp.replace(tzinfo=assume_tz or timezone.utc)
    if offset == "Z":
        return stamp.replace(tzinfo=timezone.utc)

    sign = 1 if offset[0] == "+" else -1
    digits = offset[1:].replace(":", "")
    delta = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
    return stamp.replace(tzinfo=timezone(sign * delta))


def _coord(value: object) -> float | None:
    """ExifTool -n yields signed decimal degrees."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_photos(
    scan_result: ScanResult,
    assume_tz: timezone | None,
    tmpdir: Path | None = None,
) -> list[Photo]:
    """Read metadata for every sidecar (and its RAW) and classify it."""
    sidecars = scan_result.sidecars
    raws = [raw_for(s, scan_result.raws) for s in sidecars]

    to_read = list(sidecars) + [r for r in raws if r is not None]
    records = exiftool.read_metadata(to_read, tmpdir=tmpdir)

    photos: list[Photo] = []
    for sidecar, raw in zip(sidecars, raws):
        record = records.get(sidecar)
        if record is None:
            photos.append(Photo(xmp_path=sidecar, raw_path=raw,
                                error="sidecar could not be read"))
            continue

        when = parse_timestamp(
            exiftool.tag(record, "SubSecDateTimeOriginal", "DateTimeOriginal",
                         "CreateDate"),
            assume_tz,
        )
        # Spec 6.0: a malformed sidecar parses to nothing rather than failing.
        if when is None and not record:
            photos.append(Photo(xmp_path=sidecar, raw_path=raw,
                                error="sidecar is empty or malformed"))
            continue

        raw_record = records.get(raw) if raw is not None else None
        photos.append(Photo(
            xmp_path=sidecar,
            raw_path=raw,
            capture_time=when,
            xmp_lat=_coord(exiftool.tag(record, "GPSLatitude")),
            xmp_lon=_coord(exiftool.tag(record, "GPSLongitude")),
            xmp_alt=_coord(exiftool.tag(record, "GPSAltitude")),
            raw_lat=_coord(exiftool.tag(raw_record, "GPSLatitude")) if raw_record else None,
            raw_lon=_coord(exiftool.tag(raw_record, "GPSLongitude")) if raw_record else None,
            camera_model=exiftool.tag(record, "Model"),
            camera_serial=exiftool.tag(record, "SerialNumber", "BodySerialNumber"),
        ))

    return photos
