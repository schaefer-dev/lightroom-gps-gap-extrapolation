"""Reports and console output (spec 17, 18, 19, 20, 21)."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from statistics import median

from .model import PlanEntry, Result, RunConfig

CSV_NAME = "report.csv"
JSON_NAME = "report.json"
LOG_NAME = "run.log"
MODIFIED_NAME = "modified_files.txt"

CSV_COLUMNS = [
    "target_xmp", "target_raw", "target_time", "original_gps_state",
    "result", "donor_xmp", "donor_time", "time_difference_seconds",
    "latitude", "longitude", "tie_break_applied", "raw_present",
    "backup_path", "reason", "error",
]

LIGHTROOM_WARNING = """IMPORTANT

Before continuing:

1. Ensure Lightroom has finished writing metadata to XMP.
2. Close Lightroom Classic completely.
3. Do not reopen Lightroom until this operation has finished.
"""

LIGHTROOM_NEXT_STEPS = """The XMP files have been updated successfully.

Next:

1. Start Lightroom Classic.
2. Select the affected photographs.
3. Choose Metadata > Read Metadata from Files.

The new GPS coordinates should then appear in Lightroom."""


def _iso(value: datetime | None) -> str:
    return value.isoformat(timespec="milliseconds") if value else ""


def _row(entry: PlanEntry) -> dict:
    photo = entry.photo
    return {
        "target_xmp": str(photo.xmp_path),
        "target_raw": str(photo.raw_path) if photo.raw_path else "",
        "target_time": _iso(photo.capture_time),
        "original_gps_state": photo.gps_state.value,
        "result": entry.result.value,
        "donor_xmp": str(entry.donor.xmp_path) if entry.donor else "",
        "donor_time": _iso(entry.donor.capture_time) if entry.donor else "",
        "time_difference_seconds": (
            f"{entry.gap_seconds:.2f}" if entry.gap_seconds is not None else ""
        ),
        "latitude": f"{entry.lat:.8f}" if entry.lat is not None else "",
        "longitude": f"{entry.lon:.8f}" if entry.lon is not None else "",
        "tie_break_applied": "yes" if entry.tie_break_applied else "",
        "raw_present": "yes" if photo.raw_path else "no",
        "backup_path": str(entry.backup_path) if entry.backup_path else "",
        "reason": entry.reason,
        "error": entry.error,
    }


def summarize(entries: list[PlanEntry]) -> dict:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.result.value] = counts.get(entry.result.value, 0) + 1

    gaps = [e.gap_seconds for e in entries if e.will_write and e.gap_seconds is not None]
    return {
        "scanned": len(entries),
        "already_geotagged": counts.get(Result.SKIPPED_HAS_GPS.value, 0),
        "repaired": counts.get(Result.UPDATED.value, 0),
        "repaired_warn": counts.get(Result.UPDATED_WARN.value, 0),
        "failed": counts.get(Result.FAILED.value, 0),
        "counts": counts,
        "median_gap": round(median(gaps), 2) if gaps else None,
        "max_gap_used": round(max(gaps), 2) if gaps else None,
    }


def write_reports(entries: list[PlanEntry], config: RunConfig, destination: Path,
                  backup_dir: Path | None, exiftool_version: str) -> dict:
    """Emit report.csv, report.json, run.log and the modified-file list."""
    destination.mkdir(parents=True, exist_ok=True)
    rows = [_row(e) for e in entries]

    with (destination / CSV_NAME).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    stats = summarize(entries)
    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply" if config.apply else "dry-run",
        "settings": {
            "root": str(config.root),
            "max_gap_seconds": config.max_gap,
            "warn_gap_seconds": config.warn_gap,
            "tie_break_rule": "prefer the earlier photograph",
            "same_folder_only": config.same_folder_only,
            "same_camera_only": config.same_camera_only,
            "require_raw": config.require_raw,
            "copy_altitude": config.copy_altitude,
            "assume_tz": config.assume_tz,
            "backup_dir": str(backup_dir) if backup_dir else None,
            "exiftool_version": exiftool_version,
        },
        "summary": stats,
        "photos": rows,
    }
    (destination / JSON_NAME).write_text(
        json.dumps(payload, indent=1), encoding="utf-8"
    )

    modified = [r["target_xmp"] for r, e in zip(rows, entries) if e.will_write]
    (destination / MODIFIED_NAME).write_text(
        "\n".join(modified) + ("\n" if modified else ""), encoding="utf-8"
    )
    return stats


def format_dry_run(entries: list[PlanEntry], verbose: bool = False,
                   planned: bool = True) -> str:
    """Per-photograph proposal output (spec 17).

    `planned` selects the wording: a dry run reports what *would* happen, an
    apply run reports what is about to happen.
    """
    lines: list[str] = []
    for entry in entries:
        if entry.will_write:
            lines.append(f"{entry.photo.name}")
            lines.append(f"  target time:       {_iso(entry.photo.capture_time)}")
            lines.append(f"  donor:             {entry.donor.name}")
            lines.append(f"  donor time:        {_iso(entry.donor.capture_time)}")
            lines.append(f"  difference:        {entry.gap_seconds:.3f} seconds")
            lines.append(f"  donor latitude:    {entry.lat:.6f}")
            lines.append(f"  donor longitude:   {entry.lon:.6f}")
            verb = "WOULD UPDATE" if planned else "UPDATE"
            action = f"{verb} (WARN)" if entry.is_warn else verb
            lines.append(f"  action:            {action}")
            if entry.is_warn:
                lines.append(f"  warning:           {entry.reason}")
        elif verbose and entry.result is not Result.SKIPPED_HAS_GPS:
            lines.append(f"{entry.photo.name}")
            lines.append("  action: SKIP")
            lines.append(f"  reason: {entry.reason}")
    return "\n".join(lines)


def format_summary(stats: dict, config: RunConfig, backup_dir: Path | None,
                   report_dir: Path, applied: bool) -> str:
    """End-of-run summary (spec 20)."""
    counts = stats["counts"]
    verb = "completed" if applied else "dry run completed"
    lines = [
        "",
        f"GPS repair {verb}.",
        "",
        f"Scanned photographs:          {stats['scanned']:>8,}",
        f"Already geotagged:            {stats['already_geotagged']:>8,}",
        f"Missing GPS:                  {stats['scanned'] - stats['already_geotagged']:>8,}",
        "",
    ]
    repaired_label = "Successfully repaired" if applied else "Would repair"
    lines.append(f"{repaired_label + ':':<30}{stats['repaired']:>8,}")
    lines.append(f"{'  of which flagged WARN:':<30}{stats['repaired_warn']:>8,}")

    labels = [
        (Result.SKIPPED_MAX_GAP, "Skipped - donor too far away"),
        (Result.SKIPPED_NO_DONOR, "Skipped - no valid donor"),
        (Result.SKIPPED_PARTIAL, "Skipped - inconsistent GPS"),
        (Result.SKIPPED_RAW_GPS, "Skipped - RAW already has GPS"),
        (Result.SKIPPED_NO_TIME, "Skipped - no capture time"),
        (Result.SKIPPED_NO_RAW, "Skipped - no RAW file"),
        (Result.FAILED, "Failed"),
    ]
    for result, label in labels:
        count = counts.get(result.value, 0)
        if count:
            lines.append(f"{label + ':':<30}{count:>8,}")

    if stats["median_gap"] is not None:
        lines += [
            "",
            f"Median donor distance:        {stats['median_gap']:>8.1f} s",
            f"Maximum used donor distance:  {stats['max_gap_used']:>8.1f} s",
        ]
    if backup_dir:
        lines += ["", "Backup:", str(backup_dir)]
    lines += ["", "Report:", str(report_dir / CSV_NAME)]
    return "\n".join(lines)
