"""Command-line interface and phase orchestration (spec 3, 16, 18, 26)."""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import backup as backup_mod
from . import classify, exiftool, report, scan, writer
from .donors import build_plan
from .model import DEFAULT_MAX_GAP, DEFAULT_WARN_GAP, Result, RunConfig

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_ABORTED = 2
EXIT_USAGE = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lightroom-gps-repair",
        description="Repair missing GPS coordinates in Lightroom XMP sidecars "
                    "by copying them from the temporally nearest geotagged photograph.",
        epilog="Restore a backup with:  lightroom-gps-repair restore <backup-dir>",
    )
    parser.add_argument("root", nargs="?", type=Path, help="library root to scan")
    parser.add_argument("--apply", action="store_true",
                        help="write changes (default is a dry run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="analyse only; the default")
    parser.add_argument("--max-gap", type=float, default=DEFAULT_MAX_GAP,
                        metavar="SECONDS",
                        help=f"apply silently up to this gap (default {DEFAULT_MAX_GAP:g})")
    parser.add_argument("--warn-gap", type=float, default=DEFAULT_WARN_GAP,
                        metavar="SECONDS",
                        help=f"apply with a warning up to this gap, skip beyond "
                             f"(default {DEFAULT_WARN_GAP:g})")
    parser.add_argument("--backup-dir", type=Path, metavar="DIR")
    parser.add_argument("--backup-mode", choices=["candidate-only", "all-xmp"],
                        default="candidate-only")
    parser.add_argument("--report-dir", type=Path, metavar="DIR")
    parser.add_argument("--same-folder-only", action="store_true")
    parser.add_argument("--same-camera-only", action="store_true")
    parser.add_argument("--require-raw", action="store_true",
                        help="skip sidecars with no matching RAW file")
    parser.add_argument("--copy-altitude", action="store_true")
    parser.add_argument("--assume-tz", default="local", metavar="TZ",
                        help="timezone for timestamps without an offset "
                             "(local or utc; default local)")
    parser.add_argument("--yes", "-y", dest="assume_yes", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def build_restore_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lightroom-gps-repair restore",
        description="Restore XMP sidecars from a backup manifest.",
    )
    parser.add_argument("backup_dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", "-y", dest="assume_yes", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def _resolve_tz(name: str) -> timezone | None:
    if name.lower() == "utc":
        return timezone.utc
    return datetime.now().astimezone().tzinfo


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run_restore(args) -> int:
    try:
        outcomes = backup_mod.restore(args.backup_dir, dry_run=args.dry_run)
    except backup_mod.BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ABORTED

    failures = [(p, why) for p, why in outcomes if why not in ("restored", "would restore")]
    if args.verbose:
        for path, why in outcomes:
            print(f"{why}: {path}")
    for path, why in failures:
        print(f"ERROR: {path}: {why}", file=sys.stderr)

    print(f"\n{len(outcomes) - len(failures)} of {len(outcomes)} files "
          f"{'would be restored' if args.dry_run else 'restored'}.")
    return EXIT_PARTIAL if failures else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # `restore` is a distinct mode with its own arguments.
    if argv and argv[0] == "restore":
        return run_restore(build_restore_parser().parse_args(argv[1:]))

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.root is None:
        parser.print_help()
        return EXIT_USAGE

    root = args.root.expanduser()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return EXIT_USAGE

    config = RunConfig(
        root=root,
        apply=args.apply and not args.dry_run,
        max_gap=args.max_gap,
        warn_gap=args.warn_gap,
        backup_dir=args.backup_dir,
        backup_mode=args.backup_mode,
        same_folder_only=args.same_folder_only,
        same_camera_only=args.same_camera_only,
        require_raw=args.require_raw,
        copy_altitude=args.copy_altitude,
        report_dir=args.report_dir,
        assume_tz=args.assume_tz,
        assume_yes=args.assume_yes,
        verbose=args.verbose,
    )
    try:
        config.validate()
        exif_version = exiftool.version()
    except (ValueError, exiftool.ExifToolError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE

    return _execute(config, exif_version)


def _execute(config: RunConfig, exif_version: str) -> int:
    # --- Phase 1-3: scan, extract, classify ---
    print(f"Scanning {config.root} ...")
    scan_result = scan.scan(config.root)
    if not scan_result.sidecars:
        print("No XMP sidecars found.")
        return EXIT_OK
    print(f"Found {len(scan_result.sidecars):,} sidecars; reading metadata ...")

    photos = classify.build_photos(scan_result, _resolve_tz(config.assume_tz))

    # Spec 12 (R2): refuse to pretend a camera restriction is in force.
    if config.same_camera_only and not any(p.camera_serial for p in photos):
        print("ERROR: --same-camera-only requested, but no camera serial number\n"
              "       is available in the scanned metadata.", file=sys.stderr)
        return EXIT_USAGE

    # --- Phase 4: plan (donor set frozen here, before any write) ---
    plan = build_plan(photos, config)
    stats = report.summarize(plan)
    to_write = [e for e in plan if e.will_write]

    for orphan in scan_result.orphan_raws:
        if config.verbose:
            print(f"SKIP {orphan.name}: no XMP sidecar")
    for key in scan_result.ambiguous:
        print(f"WARNING: ambiguous sidecar association near {key}", file=sys.stderr)

    dry_run_text = report.format_dry_run(plan, verbose=config.verbose,
                                         planned=not config.apply)
    if dry_run_text:
        print()
        print(dry_run_text)

    for entry in to_write:
        if entry.is_warn:
            print(f"WARN  {entry.photo.name}  {entry.reason}")

    # Reports never default to the working directory. An apply run puts them
    # beside its backup; anything else uses a temp dir unless told otherwise.
    explicit_report_dir = config.report_dir.expanduser() if config.report_dir else None
    report_dir = explicit_report_dir or Path(
        tempfile.mkdtemp(prefix="gps-repair-report-")
    )
    backup_dir = None

    # --- Dry run stops here (spec 16) ---
    if not config.apply:
        stats = report.write_reports(plan, config, report_dir, None, exif_version)
        print(report.format_summary(stats, config, None, report_dir, applied=False))
        print("\nThis was a dry run. No files were modified.")
        print("Re-run with --apply to write these changes.")
        return EXIT_OK

    if not to_write:
        stats = report.write_reports(plan, config, report_dir, None, exif_version)
        print(report.format_summary(stats, config, None, report_dir, applied=True))
        print("\nNothing to do.")
        return EXIT_OK

    # --- Confirmation (spec 3, 18) ---
    print()
    print(report.LIGHTROOM_WARNING)
    print(f"Photographs scanned:          {stats['scanned']:>8,}")
    print(f"Existing GPS photographs:     {stats['already_geotagged']:>8,}")
    print(f"Eligible for repair:          {len(to_write):>8,}")
    print(f"  of which flagged WARN:      {stats['repaired_warn']:>8,}")
    print(f"\nMaximum donor gap:            {config.max_gap:>8,.0f} s")
    print(f"Warn donor gap:               {config.warn_gap:>8,.0f} s")
    print(f"\n{len(to_write):,} XMP files will be modified.")
    print(f"{len(to_write):,} XMP files will first be backed up.")
    print("\nLightroom Classic must be closed.")

    if not config.assume_yes and not _confirm("\nContinue? [y/N] "):
        print("Aborted. No files were modified.")
        return EXIT_ABORTED

    # --- Phase 5: backup, verified before any modification (spec 5.1) ---
    backup_paths = (
        [e.photo.xmp_path for e in to_write]
        if config.backup_mode == "candidate-only"
        else list(scan_result.sidecars)
    )
    try:
        backup_dir = backup_mod.resolve_backup_dir(config.backup_dir, config.root)
        print(f"\nBacking up {len(backup_paths):,} sidecars to {backup_dir} ...")
        entries = backup_mod.create_backup(backup_paths, config.root, backup_dir)
        print(f"Backup verified ({len(entries):,} files).")
    except backup_mod.BackupError as exc:
        print(f"\nCRITICAL: backup failed: {exc}", file=sys.stderr)
        print("No XMP files were modified.", file=sys.stderr)
        return EXIT_ABORTED

    lookup = {Path(e["original"]): Path(e["backup"]) for e in entries}
    from dataclasses import replace
    plan = [
        replace(e, backup_path=lookup.get(e.photo.xmp_path.resolve()))
        if e.will_write else e
        for e in plan
    ]

    # --- Phase 6-7: write, then verify by re-reading ---
    print(f"Writing {len(to_write):,} sidecars ...")
    plan = writer.apply_updates(plan, copy_altitude=config.copy_altitude)

    # --- Phase 8: report ---
    if explicit_report_dir is None:
        report_dir = backup_dir
    stats = report.write_reports(plan, config, report_dir, backup_dir, exif_version)
    print(report.format_summary(stats, config, backup_dir, report_dir, applied=True))

    failed = [e for e in plan if e.result is Result.FAILED]
    if failed:
        print(f"\n{len(failed)} file(s) failed. Restore them with:")
        print(f"  lightroom-gps-repair restore {backup_dir}")
        for entry in failed[:10]:
            print(f"  {entry.photo.name}: {entry.error}")
        return EXIT_PARTIAL

    print()
    print(report.LIGHTROOM_NEXT_STEPS)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
