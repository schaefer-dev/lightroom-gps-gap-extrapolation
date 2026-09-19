# Lightroom GPS Gap Repair

Fills missing GPS coordinates in a Lightroom Classic library by copying latitude and
longitude from the **temporally nearest photograph that already has GPS**.

Built for the Sony "location from paired phone" case: when the camera is switched on, or
the phone link drops, a handful of frames are written without GPS while the shots seconds
either side are tagged normally.

## Safety guarantees

1. **Only `.xmp` sidecars are ever written.** RAW files are opened read-only, never modified.
2. **Only sidecars with no GPS at all are touched.** Existing GPS is authoritative. A sidecar
   with partial GPS (latitude but no longitude) is skipped, never "completed".
3. **Every file that will change is backed up and SHA-256 verified first.** If any backup
   fails, the run aborts before a single sidecar is modified.
4. **Sidecars are never created.** ExifTool will happily create a file that does not exist;
   the tool asserts the target exists immediately before each write.
5. **Repaired coordinates never propagate.** The donor set is frozen before any write, so a
   coordinate written during a run can never become a donor in that same run.

## Install

Everything stays local to this directory - nothing is installed on your system.

```bash
cd /Users/daniel/dev/gps_gap_metadata_fixer
uv venv
uv pip install -e ".[dev]"
```

Requires [ExifTool](https://exiftool.org) (`brew install exiftool`).

## Use

The command lives inside the project's `.venv`, **not** on your `PATH`. Run it with
the path prefix, from the project directory:

```bash
cd /Users/daniel/dev/gps_gap_metadata_fixer

# Dry run - the default. Analyses and reports, changes nothing.
.venv/bin/lightroom-gps-repair "/Photos/2026/Texel"

# Apply, backing up to an explicit location.
.venv/bin/lightroom-gps-repair "/Photos/2026/Texel" --apply --backup-dir ~/Backups/LightroomGPS

# Undo a run.
.venv/bin/lightroom-gps-repair restore ~/Backups/LightroomGPS/2026-09-19_193500
```

> `zsh: command not found: lightroom-gps-repair` means the `.venv/bin/` prefix is
> missing, or you are not in the project directory.

### Typing it less

Either activate the venv for the session:

```bash
source .venv/bin/activate      # then plain `lightroom-gps-repair ...` works
deactivate                     # when finished
```

Or add a shell alias to `~/.zshrc` (this one *does* change your shell config):

```bash
alias gps-repair="/Users/daniel/dev/gps_gap_metadata_fixer/.venv/bin/lightroom-gps-repair"
```

**Close Lightroom Classic before applying**, and use *Metadata > Read Metadata from Files*
afterwards to pull the new coordinates into the catalog.

## Donor gap tiers

The gap is the time between a photo and its nearest already-geotagged neighbour.

| Gap | Action |
|---|---|
| ≤ `--max-gap` (default 600 s) | Applied silently |
| `--max-gap` … `--warn-gap` (default 1800 s) | Applied, flagged `WARN` for review |
| > `--warn-gap` | Skipped |

The WARN band exists because at ten-plus minutes the donor may be a genuinely different
location — worth a human glance rather than silent acceptance or silent discard.

## Options

| Flag | Meaning |
|---|---|
| `--apply` | Write changes (dry run is the default) |
| `--max-gap` / `--warn-gap` | Tier thresholds, in seconds |
| `--backup-dir DIR` | Backup location (default `~/LightroomGPSBackup`) |
| `--backup-mode` | `candidate-only` (default) or `all-xmp` |
| `--report-dir DIR` | Where reports are written |
| `--require-raw` | Skip sidecars with no matching RAW file |
| `--same-folder-only` / `--same-camera-only` | Narrow the donor pool |
| `--copy-altitude` | Also copy GPS altitude (off by default) |
| `--assume-tz` | Timezone for timestamps without an offset |
| `--yes` | Skip the confirmation prompt |

## Reports

Each run writes `report.csv`, `report.json`, and `modified_files.txt`. Results use a fixed
vocabulary: `updated`, `updated_warn`, `skipped_max_gap`, `skipped_no_donor`,
`skipped_has_gps`, `skipped_partial`, `skipped_raw_gps`, `skipped_no_time`, `failed`.

## Tests

```bash
cd /Users/daniel/dev/gps_gap_metadata_fixer
.venv/bin/pytest -q
```

Fixtures in `tests/fixtures/real/` are real Lightroom sidecars; edge cases absent from real
data (partial GPS, malformed XML, missing timestamps) are generated synthetically.

`spec.md` is the full requirements document.
