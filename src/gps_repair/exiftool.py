"""The only module that invokes ExifTool.

Every dangerous operation funnels through here so the safety rules are
auditable in one place:

* writes assert the target already exists (spec 4.5 - ExifTool would
  otherwise create it);
* writes carry an `-if` guard so a sidecar with GPS is refused by ExifTool
  itself (spec 4.6);
* success is judged by re-reading, never by exit code (spec 6.0).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

EXIFTOOL = "exiftool"

# Chunk sizes keep argument lists and argfiles to a sane size.
READ_CHUNK = 4000
WRITE_CHUNK = 500

READ_TAGS = [
    "-SubSecDateTimeOriginal",
    "-DateTimeOriginal",
    "-CreateDate",
    "-GPSLatitude",
    "-GPSLongitude",
    "-GPSAltitude",
    "-Model",
    "-SerialNumber",
    "-BodySerialNumber",
]

# Spec 4.6: ExifTool refuses a file that already carries GPS.
GPS_ABSENT_CONDITION = "not $XMP-exif:GPSLatitude and not $XMP-exif:GPSLongitude"


class ExifToolError(RuntimeError):
    pass


def find_exiftool() -> str:
    path = shutil.which(EXIFTOOL)
    if not path:
        raise ExifToolError(
            "exiftool was not found on PATH.\n"
            "Install it with:  brew install exiftool"
        )
    return path


def version() -> str:
    out = subprocess.run([find_exiftool(), "-ver"], capture_output=True, text=True)
    return out.stdout.strip()


def _run(args: list[str], argfile_lines: list[str] | None = None,
         tmpdir: Path | None = None) -> subprocess.CompletedProcess:
    """Invoke ExifTool, passing long file lists via an argfile."""
    exe = find_exiftool()
    if argfile_lines is None:
        return subprocess.run([exe, *args], capture_output=True, text=True)

    target = (tmpdir or Path(os.environ.get("TMPDIR", "/tmp"))) / "exiftool_args.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(argfile_lines) + "\n", encoding="utf-8")
    try:
        return subprocess.run([exe, *args, "-@", str(target)],
                              capture_output=True, text=True)
    finally:
        target.unlink(missing_ok=True)


def read_metadata(paths: list[Path], tmpdir: Path | None = None,
                  all_tags: bool = False) -> dict[Path, dict]:
    """Bulk-read metadata for many files in as few processes as possible.

    Returns a mapping of path -> tag dict. Files ExifTool could not parse come
    back missing or empty; callers must treat that as unreadable rather than
    trusting the exit code (spec 6.0).

    With `all_tags`, every property is read rather than the fixed list, so the
    caller can judge sidecar completeness (spec 4.7). This stays one process
    per chunk either way.
    """
    results: dict[Path, dict] = {}
    if not paths:
        return results

    selection = ["-a"] if all_tags else list(READ_TAGS)

    for start in range(0, len(paths), READ_CHUNK):
        chunk = paths[start:start + READ_CHUNK]
        proc = _run(
            ["-j", "-n", "-q", "-G1", "-charset", "filename=utf8", *selection],
            argfile_lines=[str(p) for p in chunk],
            tmpdir=tmpdir,
        )
        text = proc.stdout.strip()
        if not text:
            continue
        try:
            records = json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ExifToolError(f"could not parse ExifTool output: {exc}") from exc

        for record in records:
            source = record.pop("SourceFile", None)
            if source:
                results[Path(source)] = record

    return results


def tag(record: dict, *names: str) -> object | None:
    """Fetch the first present tag, ignoring the group prefix ExifTool adds."""
    for name in names:
        for key, value in record.items():
            if key.split(":")[-1] == name and value not in (None, ""):
                return value
    return None


# Groups ExifTool synthesises or that describe the file rather than its content.
_NON_CONTENT_GROUPS = ("File", "System", "ExifTool", "Composite")


def count_tags(record: dict) -> int:
    """Count real, non-GPS properties.

    Used both for the preservation check (spec 29) and for sidecar health
    (spec 4.7), so it must ignore filesystem and derived groups.
    """
    total = 0
    for key in record:
        if key.split(":")[0] in _NON_CONTENT_GROUPS:
            continue
        if "GPS" in key.split(":")[-1]:
            continue
        total += 1
    return total


def write_gps(
    updates: list[tuple[Path, float, float, float | None]],
    tmpdir: Path | None = None,
) -> list[tuple[Path, str]]:
    """Write lat/lon (and optional altitude) to sidecars that already exist.

    Returns [(path, reason)] for targets rejected before ExifTool ran. Actual
    success is confirmed by the caller re-reading the files.
    """
    rejected: list[tuple[Path, str]] = []
    writable: list[tuple[Path, float, float, float | None]] = []

    # Spec 4.5: ExifTool creates missing files, so refuse them here.
    for path, lat, lon, alt in updates:
        if not path.is_file():
            rejected.append((path, "target sidecar no longer exists"))
        elif not os.access(path, os.W_OK):
            rejected.append((path, "target sidecar is not writable"))
        else:
            writable.append((path, lat, lon, alt))

    for start in range(0, len(writable), WRITE_CHUNK):
        chunk = writable[start:start + WRITE_CHUNK]
        lines: list[str] = []
        for path, lat, lon, alt in chunk:
            lines += [
                "-n",
                "-overwrite_original",
                "-if",
                GPS_ABSENT_CONDITION,
                f"-XMP-exif:GPSLatitude={lat}",
                f"-XMP-exif:GPSLongitude={lon}",
            ]
            if alt is not None:
                lines.append(f"-XMP-exif:GPSAltitude={alt}")
            lines += [str(path), "-execute"]

        # Exit status is deliberately ignored: a mixed batch returns one
        # aggregate code that says nothing per file (spec 6.0).
        _run(["-q"], argfile_lines=lines, tmpdir=tmpdir)

    return rejected
