Below is a single self-contained Markdown specification that can be handed directly to an implementation agent or used as the basis for the program.

# Lightroom GPS Gap Repair Tool - Requirements Specification

> **Revision history**
>
> **2026-09-19 - Revision 3.** Added section 4.7 (unsynced "stub" sidecars) after finding 514
> such files across two folders of a real 4,164-sidecar library. Also updated sections 19, 26,
> 27 and 31. Changes are marked **[R3]**.
>
> **2026-09-19 - Revision 2.** Revised after a data-driven review of the specification against a
> real 804-sidecar sample library and the installed ExifTool 13.55. The review found several
> contradictions and two safety hazards. Changed sections: 1, 2, 4 (new 4.5 / 4.6), 6, 6.2, 7, 8,
> 11, 12, 14, 19, 27, 29, 31. All other sections are unchanged from Revision 1 and were confirmed
> sound. Changes are marked **[R2]** throughout.
>
> **Revision 1.** Original specification.

## 1. Purpose

The tool shall repair missing GPS coordinates in a Lightroom Classic photo library by copying the GPS coordinates from the temporally nearest photograph that already contains valid GPS information.

The intended use case is a Sony camera receiving location information from a paired phone. Immediately after the camera is switched on or the phone connection is temporarily unavailable, one or more photographs can be created without GPS information. Photographs taken shortly before or shortly afterwards generally contain valid GPS coordinates.

The tool shall operate outside Lightroom Classic and update Lightroom's XMP sidecar files.

The primary goals are:

- Never overwrite existing GPS metadata.
- Never modify RAW image files.
- Never create a new XMP sidecar; only modify sidecars that already exist. **[R2]**
- Operate on XMP sidecars as the unit of work, with RAW files optional. **[R2]**
- Back up XMP sidecars before making any change.
- Use only pre-existing valid GPS coordinates as donor locations.
- Select the donor photograph based on the smallest capture-time difference.
- Preserve all unrelated XMP metadata.
- Provide a safe dry-run mode before any modification.
- Produce a complete audit log of all changes.

---

## 2. Supported Library Structure

The initial implementation shall target the following structure:

```text
Photo Library/
├── 2026/
│   ├── Texel/
│   │   ├── DSC00001.ARW
│   │   ├── DSC00001.xmp
│   │   ├── DSC00002.ARW
│   │   ├── DSC00002.xmp
│   │   └── ...
│   └── ...
└── ...
```

The tool shall recursively scan a user-selected root directory.

Version 1 must support:

- Sony `.ARW` RAW files
- corresponding `.xmp` sidecars
- case-insensitive file extensions such as `.ARW`, `.arw`, `.XMP`, and `.xmp`

The design should make it possible to support additional RAW formats later.

### 2.1 The XMP sidecar is the unit of work **[R2]**

The tool is sidecar-driven. The `.xmp` file is what is scanned, evaluated, and modified.

The presence of a RAW file is **optional**. A library consisting only of XMP sidecars, with no
RAW files present at all, is explicitly supported and must be processed normally. This reflects
real usage, where sidecars are frequently reviewed or archived separately from the RAW originals.

When a matching RAW file *is* present, it is used only for the additional safety check in
section 6.2, and is never written to.

A strict mode may be offered that requires a RAW to be present:

```text
--require-raw
```

This is **off** by default.

---

## 3. Lightroom Preconditions

The program shall assume that Lightroom Classic is the primary metadata manager.

Before an apply operation:

1. Lightroom should have completed writing metadata to XMP.
2. If automatic XMP writing is not enabled, metadata should first be saved using Lightroom's `Metadata > Save Metadata to File`.
3. Lightroom Classic must then be closed.
4. The GPS repair program may then modify the sidecars.

The tool shall clearly display this requirement before an apply operation.

Example:

```text
IMPORTANT

Before continuing:

1. Ensure Lightroom has finished writing metadata to XMP.
2. Close Lightroom Classic completely.
3. Do not reopen Lightroom until this operation has finished.
```

A dry run does not modify files and therefore does not require Lightroom to be closed.

---

## 4. Fundamental Safety Rules

The following rules are mandatory.

### 4.1 RAW files must never be modified

Files such as:

```text
DSC01234.ARW
```

must remain byte-for-byte unchanged.

The tool may read metadata from RAW files but must never write to them.

### 4.2 Existing GPS metadata must never be overwritten

An XMP sidecar that already contains GPS metadata must never have its GPS information changed.

This rule applies regardless of whether another photograph has:

- a closer timestamp
- more precise coordinates
- different coordinates
- newer coordinates

Existing GPS is authoritative.

### 4.3 Partial GPS metadata must not be repaired automatically

If an XMP contains only part of a GPS coordinate, for example latitude but no longitude, the tool must not modify it automatically.

Example:

```text
GPSLatitude: present
GPSLongitude: missing
```

Result:

```text
SKIPPED: inconsistent existing GPS metadata
```

This prevents an unusual or corrupted metadata state from being silently altered.

### 4.4 Generated GPS data must never become donor data during the same run

This requirement is particularly important.

The donor set must be determined before any modifications are made.

Consider:

```text
12:00:00 GPS
12:01:00 missing
12:02:00 missing
12:03:00 missing
```

If the maximum donor distance is 90 seconds, only the 12:01 photograph should be eligible.

The program must NOT perform:

```text
12:00 GPS
    ↓
12:01 gets GPS
    ↓
12:02 uses 12:01
    ↓
12:03 uses 12:02
```

That would allow GPS data to propagate indefinitely.

Only photographs containing valid GPS **before the repair operation started** may be donors.

### 4.5 XMP sidecars must never be created **[R2]**

The tool must only ever modify sidecar files that already exist. It must never bring a new
sidecar into existence, for any reason.

This rule requires active enforcement, because the underlying metadata tool does not enforce it.
ExifTool, when given a path that does not exist, will **create** that file rather than fail:

```text
exiftool -GPSLatitude=53.0 /path/DSC01234.xmp
    1 image files created
```

Guarding the write with a condition such as
`-if 'not $XMP-exif:GPSLatitude and not $XMP-exif:GPSLongitude'` does **not** prevent this: a
file that does not exist trivially satisfies "has no GPS", and is still created.

Therefore, immediately before each write, the program must assert that the target path exists and
is a writable regular file. If the target has disappeared between planning and writing, the
photograph must be reported as failed and no file may be created.

### 4.6 Independent safety locks **[R2]**

Protection against overwriting existing GPS must not depend on a single check. Three independent
mechanisms are required:

1. **Classification.** Only photographs whose sidecar contains neither latitude nor longitude are
   admitted to the modification plan.
2. **Metadata-tool condition.** Each write is additionally guarded by
   `-if 'not $XMP-exif:GPSLatitude and not $XMP-exif:GPSLongitude'`, so a sidecar that already
   has GPS is refused by ExifTool itself and left byte-for-byte unchanged, even if the plan were
   incorrect.
3. **Post-write verification.** Every modified sidecar is re-read and compared against the
   intended donor coordinates.

The separate existence assertion of section 4.5 complements these and is not optional.

### 4.7 Unsynced sidecars must never be modified **[R3]**

A sidecar to which Lightroom has never written its catalog metadata is called a **stub**. It
carries a rating and little else:

```xml
<x:xmpmeta x:xmptk="XMP Core 5.6.0">
   <xmp:Rating>1</xmp:Rating>
   <photoshop:SidecarForExtension>ARW</photoshop:SidecarForExtension>
```

No capture time, no camera model, no GPS, no develop settings.

#### Why this matters

The danger is not that this tool would damage a stub - it has no timestamp, so it can never be
matched to a donor. The danger is the workflow this tool **recommends afterwards**. Section 21
instructs the user to run *Metadata > Read Metadata from Files*. Performed against a stub, that
replaces rich Lightroom catalog metadata - develop settings, keywords, crops, ratings - with a
near-empty file. The loss is caused by following the tool's own advice.

A tool that silently skips stubs therefore leads the user into data loss. It must refuse.

#### Detection

Two independent signals, both judged from the sidecar's own parsed content:

1. **Stub** - neither a capture timestamp nor a camera model is present.
2. **Sparse** - fewer than 25 non-GPS properties.

Calibration against a real library of 4,164 sidecars: genuine Lightroom sidecars carried **78
properties at minimum** (median 86, maximum 501); stubs carried **6**. Of those files, 514
classified as stubs and 3,650 as healthy, with **zero ambiguous cases**. The threshold of 25
sits inside that empty gap.

The check must not consult an adjacent RAW file. A RAW frequently holds a valid timestamp and
GPS while its sidecar is still an unwritten stub; trusting the RAW would mask exactly the
condition being detected.

#### Required behaviour

- An apply operation that encounters any unsynced sidecar must **abort before the backup phase**
  and modify nothing.
- The message must state the consequence, not merely the condition, and must name the affected
  folders and the remedy (*Metadata > Save Metadata to Files*).
- A dry run must **report** the condition but must not abort, so the library can still be
  diagnosed in full.
- An unsynced sidecar is never a donor and is never written to, under any flag.
- An explicit `--allow-stubs` option may permit an apply run to continue, skipping the affected
  sidecars. It must never cause one to be written.

Reported as:

```text
SKIPPED: sidecar is missing Lightroom metadata
```

---

## 5. Backup Requirements

Backups are mandatory before any modification.

### 5.1 Backup phase must finish before modification phase begins

The operation shall be divided into distinct phases:

```text
1. Scan
2. Analyze
3. Determine candidates and donors
4. Back up XMP files
5. Verify backup
6. Modify XMP files
7. Verify modifications
8. Generate report
```

No XMP may be modified until the backup phase has completed successfully.

If backup creation fails, the apply operation must abort before modifying any XMP.

### 5.2 Backup location

Backups should be stored outside the normal Lightroom photo directory tree where possible.

Example:

```text
LightroomGPSBackup/
└── 2026-09-19_193500/
    ├── manifest.json
    └── files/
        └── 2026/
            └── Texel/
                ├── DSC01234.xmp
                ├── DSC01592.xmp
                └── ...
```

The original relative directory structure should be preserved.

### 5.3 Which XMP files must be backed up

At minimum, every XMP file that could be modified during the operation must be backed up.

For maximum safety, the program may offer two modes:

```text
candidate-only
all-xmp
```

Recommended default:

```text
candidate-only
```

Every XMP that the program intends to modify must have a verified backup before any XMP modification starts.

### 5.4 Backup integrity

The program should record for every backed-up file:

- original path
- backup path
- file size
- modification timestamp
- SHA-256 hash

Example manifest entry:

```json
{
  "original": "/Photos/2026/Texel/DSC01234.xmp",
  "backup": "/Backup/2026-09-19_193500/files/2026/Texel/DSC01234.xmp",
  "size": 18342,
  "sha256": "..."
}
```

The copied file should be verified against the original before the modification phase begins.

### 5.5 Restore capability

The tool should provide a restore operation such as:

```text
gps-repair restore /path/to/backup/2026-09-19_193500
```

Restore must use the backup manifest rather than attempting to infer original locations.

---

## 6. Metadata Sources

The program should preferably use ExifTool for metadata reading and writing rather than implementing XMP parsing itself.

Reasons include:

- robust EXIF parsing
- Sony RAW support
- XMP namespace handling
- timestamp handling
- safe metadata serialization
- preservation of unrelated XMP metadata

A Python program may communicate with ExifTool.

### 6.0 Determining per-file success **[R2]**

Per-file success must be determined by **re-reading the metadata**, never from the exit code of
the metadata tool. Two verified behaviours make exit codes unreliable:

- A batched invocation writing many files returns a single aggregate exit code. A batch in which
  one file was written and another was refused by its `-if` condition returns exit 2 overall,
  which says nothing about any individual file.
- A malformed or truncated XMP is read with **exit code 0** and simply yields no metadata.

Validity and success must therefore always be judged from parsed metadata content.

### 6.1 Target GPS detection

Whether a sidecar is eligible for modification must be determined from the XMP sidecar itself.

Relevant XMP GPS fields include the effective equivalents of:

```text
XMP-exif:GPSLatitude
XMP-exif:GPSLongitude
```

An XMP is eligible only when both latitude and longitude are absent.

### 6.2 Additional RAW safety check

**[R2]** This check applies **only when a matching RAW file is actually found.** If no RAW is
present, the check does not apply and the photograph is processed normally on the basis of its
sidecar alone (see section 2.1).

When a RAW file is present, it should also be inspected.

If the RAW itself already contains valid GPS but the XMP does not, the program should default to:

```text
SKIPPED: source RAW already contains GPS
```

This is safer than creating potentially redundant or conflicting XMP metadata.

The RAW is opened strictly read-only. This behavior may later become configurable.

### 6.3 Donor GPS source

Valid donor coordinates may originate from:

- RAW EXIF metadata
- Lightroom XMP metadata

The effective photograph metadata should therefore be considered when identifying donors.

A valid donor requires at least:

```text
latitude
longitude
```

Coordinates must also pass basic validity checks:

```text
-90 <= latitude <= 90
-180 <= longitude <= 180
```

---

## 7. Capture Time

The nearest photograph shall be determined using original image capture time, not:

- filesystem creation time
- filesystem modification time
- XMP modification time
- Lightroom import time

Preferred timestamp source:

```text
DateTimeOriginal
```

Subsecond precision should be used where available.

For example:

```text
SubSecDateTimeOriginal
```

**[R2]** In Lightroom-written XMP sidecars, `SubSecDateTimeOriginal` is typically **absent**,
because `exif:DateTimeOriginal` already carries both subseconds and the UTC offset:

```text
exif:DateTimeOriginal="2026-09-18T14:43:24.485+02:00"
```

The resolution order shall therefore be:

1. `SubSecDateTimeOriginal` if present
2. `DateTimeOriginal` (the normal case)
3. `xmp:CreateDate` as a fallback

A photograph for which none of these yields a usable timestamp must be skipped and reported.

Timezone information should be used if present.

For photographs from the same camera and shooting session, timestamps without explicit timezone information may be treated as belonging to the same local camera time domain.

**[R2]** Timestamps lacking an explicit offset shall be interpreted in a single configurable
timezone:

```text
--assume-tz
```

defaulting to the system local timezone. The assumption in force must be recorded in the report.
Timestamps must never be compared across mixed naive and aware representations.

The implementation must use a consistent timestamp representation internally.

---

## 8. Candidate Definition

A photograph is a repair candidate only when all of the following conditions are true:

1. An XMP sidecar exists. **[R2]** This is the unit of work.
2. The XMP contains neither GPS latitude nor GPS longitude.
3. **[R2]** A corresponding RAW file *may* exist. If one is present, it must not already contain
   valid GPS (section 6.2). If none is present, this condition is satisfied automatically.
4. A valid capture timestamp can be determined.
5. At least one valid donor exists within the configured maximum donor gap (section 11).

**[R2]** Under `--require-raw`, an additional condition applies: a supported RAW file must exist,
and a sidecar without one is skipped with `SKIPPED: no RAW file`. This flag is off by default.

If any requirement fails, the photograph must be skipped and included in the report.

The initial version must never create new XMP files for photographs that have no sidecar
(section 4.5).

Example:

```text
DSC01234.ARW
no DSC01234.xmp
```

Result:

```text
SKIPPED: no XMP sidecar
```

---

## 9. Donor Selection Algorithm

For each candidate photograph:

1. Find the nearest earlier photograph with original valid GPS.
2. Find the nearest later photograph with original valid GPS.
3. Calculate the absolute capture-time difference to each.
4. Select the donor with the smaller time difference.
5. Verify that the difference does not exceed the configured maximum.
6. Copy the donor's latitude and longitude to the candidate XMP.

Example:

```text
11:32:00.000    GPS A
11:32:04.200    missing
11:32:07.800    GPS B
```

Differences:

```text
previous donor: 4.2 seconds
next donor:     3.6 seconds
```

Result:

```text
GPS B is used.
```

No coordinate interpolation shall be performed.

---

## 10. Equal-Distance Tie Breaking

If previous and next donors are exactly equally distant in time, the result must be deterministic.

Recommended rule:

```text
Prefer the previous photograph.
```

Example:

```text
12:00:00 GPS A
12:00:05 missing
12:00:10 GPS B
```

Result:

```text
GPS A
```

The selected tie-breaking rule must be documented in the report.

---

## 11. Maximum Time Difference **[R2 - rewritten]**

The tool must support a configurable maximum donor time difference.

**[R2]** Revision 1 specified a single 120-second threshold. Measured against the reference
library of 804 sidecars, that default repaired only 48 of 103 gaps (47 %), because the median gap
to the nearest donor was 152 seconds. A single hard threshold also treats a 3-minute gap and a
3-hour gap identically, discarding both without comment.

Revision 2 therefore defines **three tiers**:

| Gap to nearest valid donor | Action |
|---|---|
| less than or equal to `--max-gap` (default 600 s / 10 min) | Apply silently |
| between `--max-gap` and `--warn-gap` (default 1800 s / 30 min) | Apply, and emit a **WARN** |
| greater than `--warn-gap` (default 1800 s / 30 min) | **Skip.** Never used as a donor |

```text
--max-gap 600
--warn-gap 1800
```

### 11.1 The WARN tier

A coordinate in the WARN tier is still applied, but the photograph must be clearly flagged so a
human can review it. At these distances the donor may represent a genuinely different location.

Console output:

```text
WARN  DSC09660.ARW  donor is 1095 s (18.2 min) away - please review manually
```

Every WARN photograph must be:

- printed to the console during the run
- marked in `report.csv` and `report.json` with result `updated_warn`
- counted separately in the end-of-run summary
- listed in the generated file of modified images

### 11.2 The skip tier

A candidate whose nearest valid donor is farther away than `--warn-gap` must not be changed.

```text
15:00:00 GPS
17:30:00 missing
```

Result:

```text
SKIPPED: nearest GPS donor is 9000 seconds away
```

There must be no unlimited matching mode enabled by default. Setting `--warn-gap` to a very large
value is possible but must be an explicit, deliberate act by the user.

### 11.3 Constraints

`--max-gap` must be less than or equal to `--warn-gap`; otherwise the program exits with a usage
error. Setting them equal disables the WARN tier and restores strict two-tier behaviour.

---

## 12. Search Scope

The donor search shall be limited to photographs within the selected scan root.

Example:

```text
gps-repair scan "/Photos/2026/Texel"
```

Photographs outside that root shall not participate.

The implementation may optionally support additional restrictions such as:

```text
--same-folder-only
--same-camera-only
```

These are useful safeguards but are not mandatory for the first implementation.

### Same-camera matching

If camera serial information is available, an optional mode should restrict donors to photographs from the same physical camera.

Example:

```text
--same-camera-only
```

This can be useful when several cameras are used simultaneously.

**[R2]** Lightroom-written sidecars do not necessarily carry a serial number at all. In the
reference library of 804 sidecars, no `SerialNumber` tag was present in any file, so this option
would have silently grouped every photograph into a single anonymous bucket - appearing to apply
a restriction while applying none.

Therefore, if `--same-camera-only` is requested and serial information cannot be determined, the
program must **exit with a clear error** rather than proceed:

```text
ERROR: --same-camera-only requested, but no camera serial number
       is available in the scanned metadata.
```

Silently degrading to unrestricted matching is forbidden.

---

## 13. Metadata to Copy

By default, only geographical coordinates shall be copied:

```text
GPSLatitude
GPSLongitude
```

The following metadata must NOT be copied automatically:

- GPS timestamp
- GPS date
- GPS processing method
- camera metadata
- capture timestamp
- direction
- speed
- destination coordinates
- Lightroom location names
- keywords
- IPTC location fields

GPS altitude should not be copied by default.

It may be supported through an explicit option:

```text
--copy-altitude
```

The reasoning is that latitude and longitude describe the desired inferred location, while donor GPS timestamps or similar values would incorrectly describe the target image.

---

## 14. XMP Preservation

Modification of GPS metadata must preserve all unrelated XMP information.

This includes, but is not limited to:

- Lightroom develop settings
- ratings
- labels
- keywords
- copyright information
- captions
- titles
- crop data
- camera calibration
- masks
- processing settings
- custom metadata

The tool must not rebuild an XMP from only a selected subset of fields.

It should update the existing XMP packet using a metadata-aware tool such as ExifTool.

Semantic metadata preservation is required.

Byte-for-byte preservation of the XML serialization is not required.

### 14.1 Observed serialization changes **[R2]**

This subsection records verified behaviour so it is not mistaken for corruption later.

When ExifTool writes a Lightroom sidecar, it rewrites the XMP packet:

- attribute-form properties (`exif:FNumber="56/10"`) become element-form
  (`<exif:FNumber>56/10</exif:FNumber>`)
- the file grows by roughly 50 %
- properties are re-ordered, grouped by namespace
- `x:xmptk` changes from `Adobe XMP Core ...` to `Image::ExifTool ...`

A tag-by-tag comparison of a representative sidecar before and after writing confirmed that all
91 unrelated properties were preserved with identical values - ratings, crop, develop settings,
keywords, copyright, and the `xmpMM:History` block included. The only differences were the added
GPS properties and the toolkit identifier.

This satisfies the semantic-preservation requirement above and is explicitly accepted. Lightroom
reads the resulting sidecars normally.

Coordinate round-trip precision was measured at better than one millimetre and is not a concern.

---

## 15. Atomic and Failure-Safe Writing

Each XMP update should be failure-safe.

Preferred process:

```text
existing XMP
    ↓
write modified temporary file
    ↓
validate temporary file
    ↓
atomic replacement of original XMP
```

If writing or validation fails:

- the original XMP should remain intact where possible
- the photograph must be marked as failed
- the error must be reported
- processing may continue for unrelated photographs unless a critical system error occurs

The verified external backup remains the ultimate recovery mechanism.

---

## 16. Dry-Run Mode

Dry-run mode is mandatory.

Dry run should be the default behavior unless an explicit apply argument is supplied.

Example:

```bash
gps-repair "/Photos/2026/Texel"
```

should perform analysis only.

An actual modification should require an explicit command such as:

```bash
gps-repair "/Photos/2026/Texel" --apply
```

Dry-run mode must not:

- alter XMP files
- alter RAW files
- create metadata
- update timestamps of existing photo files

It may create reports.

---

## 17. Dry-Run Output

Dry run should report every proposed modification.

Example:

```text
DSC08123.ARW
  target time:       2026-09-18 07:41:23.240
  donor:             DSC08124.ARW
  donor time:        2026-09-18 07:41:25.710
  difference:        2.470 seconds
  donor latitude:    53.123456
  donor longitude:   4.812345
  action:            WOULD UPDATE
```

Skipped images should also be explained:

```text
DSC09342.ARW
  action: SKIP
  reason: nearest GPS donor is 318 seconds away
```

---

## 18. Apply Confirmation

Before modifying XMPs, the tool should display a summary such as:

```text
Scan complete.

Photographs scanned:              18,492
Existing GPS photographs:         17,841
Missing GPS candidates:              651
Eligible for automatic repair:       623
  of which flagged WARN:              17
Skipped:                              28

Maximum donor gap:                  600 s
Warn donor gap:                    1800 s

623 XMP files will be modified.
623 XMP files will first be backed up.

17 of these have a donor in the WARN band and should be reviewed afterwards.

Lightroom Classic must be closed.

Continue? [y/N]
```

Non-interactive operation may be supported through an explicit option such as:

```text
--yes
```

---

## 19. Reporting

Every run should produce machine-readable and human-readable reports.

Recommended files:

```text
report.csv
report.json
run.log
```

Each processed photograph should include:

- target RAW path
- target XMP path
- target capture time
- original GPS state
- action
- donor RAW path
- donor capture time
- donor latitude
- donor longitude
- absolute time difference
- backup path
- modification result
- reason if skipped
- error message if failed
- **[R2]** whether a RAW file was present (`raw_present`)
- **[R2]** whether the equal-distance tie-break was applied (`tie_break_applied`)
- **[R2]** the donor gap tier (`ok` / `warn`)
- **[R3]** sidecar health (`ok` / `stub` / `sparse`)

**[R2]** The `result` field must distinguish the WARN tier. Permitted values:

```text
updated            coordinate applied, donor within --max-gap
updated_warn       coordinate applied, donor in the WARN band - review manually
skipped_max_gap    nearest donor beyond --warn-gap
skipped_no_donor   no valid donor at all
skipped_has_gps    sidecar already had GPS
skipped_partial    inconsistent existing GPS metadata
skipped_raw_gps    RAW already contains GPS
skipped_no_xmp     no sidecar
skipped_stub_sidecar  sidecar missing Lightroom metadata (4.7)  [R3]
failed             write or verification error
```

Example CSV:

```csv
target,donor,time_difference_seconds,latitude,longitude,result
DSC01234.ARW,DSC01235.ARW,2.41,53.123456,4.812345,updated
DSC04412.ARW,DSC04411.ARW,8.73,53.124001,4.811932,updated
DSC09660.ARW,DSC09659.ARW,1094.98,53.021110,4.762030,updated_warn
DSC09201.ARW,,9000.00,,,skipped_max_gap
```

**[R2]** The report header must also record the tie-breaking rule in force (section 10), the
configured `--max-gap` and `--warn-gap`, the naive-timestamp assumption (section 7), and the
ExifTool version used.

---

## 20. End-of-Run Summary

The final output should provide a concise summary.

Example:

```text
GPS repair completed.

Scanned photographs:             18,492
Already geotagged:               17,841
Missing GPS:                        651

Successfully repaired:              623
  of which flagged WARN:              17
Skipped - donor too far away:        19
Skipped - no valid donor:              4
Skipped - inconsistent GPS:           2
Skipped - RAW already has GPS:         1
Failed:                               2

Median donor distance:             2.8 s
Maximum used donor distance:      47.2 s

Backup:
/Backups/LightroomGPS/2026-09-19_193500

Report:
/Backups/LightroomGPS/2026-09-19_193500/report.csv
```

---

## 21. Post-Processing Lightroom Workflow

After the repair process completes successfully:

1. Start Lightroom Classic.
2. Select the affected photographs or folders.
3. Use:

```text
Metadata > Read Metadata from Files
```

4. Lightroom should then load the newly added GPS coordinates from the modified XMP sidecars.

The tool should display these instructions after a successful run.

Example:

```text
The XMP files have been updated successfully.

Next:

1. Start Lightroom Classic.
2. Select the affected photographs.
3. Choose Metadata > Read Metadata from Files.

The new GPS coordinates should then appear in Lightroom.
```

A list of modified image paths should be generated so the affected photographs can be identified easily.

---

## 22. Important Lightroom Safety Consideration

Reading metadata from XMP into Lightroom can replace catalog metadata with values stored in the sidecar.

For that reason, the intended sequence is:

```text
Lightroom
    ↓
finish writing current metadata to XMP
    ↓
close Lightroom
    ↓
run GPS repair
    ↓
reopen Lightroom
    ↓
read metadata from files
```

The program must never encourage simultaneous Lightroom and external XMP modification.

---

## 23. Example Matching Scenarios

### Scenario A - GPS missing between two tagged photographs

```text
10:00:00 GPS A
10:00:03 missing
10:00:05 GPS B
```

Differences:

```text
A = 3 seconds
B = 2 seconds
```

Result:

```text
Use GPS B.
```

---

### Scenario B - GPS missing immediately after camera startup

```text
10:00:00 missing
10:00:02 missing
10:00:04 GPS A
10:00:06 GPS A
```

Results:

```text
10:00:00 -> donor 10:00:04, difference 4 s
10:00:02 -> donor 10:00:04, difference 2 s
```

Both receive the coordinates from the 10:00:04 photograph.

This solves the main Sony phone-connection use case.

---

### Scenario C - GPS disappears before camera shutdown

```text
18:10:00 GPS A
18:10:04 missing
18:10:07 missing
```

Results:

```text
18:10:04 -> donor 18:10:00
18:10:07 -> donor 18:10:00
```

provided both fall within the maximum allowed gap.

---

### Scenario D - Large time gap

```text
12:00:00 GPS A
14:00:00 missing
14:00:04 GPS B
```

The missing photograph uses GPS B because it is only four seconds away.

GPS A is irrelevant despite appearing earlier in the sorted library.

---

### Scenario E - No nearby location

```text
12:00:00 GPS
14:00:00 missing
```

with the default gaps:

```text
--max-gap 600     (10 min)
--warn-gap 1800   (30 min)
```

The nearest donor is 7200 seconds away, beyond `--warn-gap`.

Result:

```text
SKIPPED
```

No location should be guessed. **[R2]**

---

### Scenario F - Existing GPS

```text
12:00:00 GPS A
12:00:02 GPS B
```

No modification occurs.

Even if GPS A and GPS B appear suspiciously different, existing metadata is never corrected by this tool.

---

### Scenario G - Generated coordinates must not propagate

Original state:

```text
12:00:00 GPS A
12:01:00 missing
12:02:00 missing
12:03:00 missing
```

with explicitly narrowed gaps, chosen to isolate the propagation question:

```text
--max-gap 90 --warn-gap 90
```

Expected result:

```text
12:01:00 -> GPS A     (60 s from the original donor)
12:02:00 -> SKIP      (120 s from the original donor)
12:03:00 -> SKIP      (180 s from the original donor)
```

The generated GPS at 12:01 must not become a new donor.

**[R2]** This scenario deliberately overrides the default gaps. Under the defaults
(`--max-gap 600`) all three photographs would legitimately be repaired **directly from GPS A**,
which is correct and is not propagation: every donor distance is still measured against the
original, pre-run donor set.

The invariant being tested is independent of the thresholds: the donor set is frozen before any
write, so a coordinate written during the run can never serve as a donor within that same run.
A correct implementation must never produce the chain `12:01 -> 12:02 -> 12:03`.

---

## 24. Recommended Internal Algorithm

The complete dataset should first be scanned before any changes are performed.

Conceptually:

```python
photos = scan_library()

for photo in photos:
    read:
        capture_time
        raw_gps
        xmp_gps
        camera_serial
        paths

original_donors = [
    photo
    for photo in photos
    if photo had valid GPS before the run
]

candidates = [
    photo
    for photo in photos
    if photo.xmp_has_no_gps
    and (photo.raw is None or photo.raw_has_no_gps)   # [R2] RAW optional
]

sort original_donors by capture_time

for candidate in candidates:
    previous = nearest donor before candidate
    following = nearest donor after candidate

    donor = donor with smallest absolute time difference

    if no donor:
        skip

    elif time_difference > warn_gap:      # [R2] beyond the outer tier
        skip

    elif time_difference > max_gap:       # [R2] WARN band
        schedule GPS copy, flagged WARN

    else:
        schedule GPS copy
```

Only after the complete modification plan has been created shall the backup and write phases begin.

---

## 25. Performance Requirements

The program should avoid launching a new ExifTool process for every photograph.

Preferred implementations include:

- one bulk metadata extraction call
- ExifTool `-stay_open`
- batched metadata operations

For donor lookup, photographs should be sorted by capture time.

Nearest-neighbor lookup should use an efficient algorithm such as:

- binary search
- two-pointer traversal

There is no need to compare every photograph against every other photograph.

Expected complexity should be approximately:

```text
O(n log n)
```

or better after metadata extraction.

Libraries containing tens or hundreds of thousands of photographs should therefore be practical.

---

## 26. Recommended Command-Line Interface

### Dry run

```bash
lightroom-gps-repair \
    "/Photos/2026/Texel"
```

Equivalent explicit form:

```bash
lightroom-gps-repair \
    "/Photos/2026/Texel" \
    --dry-run
```

### Apply changes

```bash
lightroom-gps-repair \
    "/Photos/2026/Texel" \
    --apply
```

### Custom donor gaps **[R2]**

```bash
lightroom-gps-repair \
    "/Photos/2026/Texel" \
    --max-gap 300 \
    --warn-gap 900 \
    --apply
```

Apply silently up to 5 minutes, apply with a warning up to 15 minutes, skip beyond that.

### Require a RAW file to be present **[R2]**

```bash
lightroom-gps-repair \
    "/Photos/2026/Texel" \
    --require-raw \
    --apply
```

### Explicit backup directory

```bash
lightroom-gps-repair \
    "/Photos/2026/Texel" \
    --backup-dir "/Backups/LightroomGPS" \
    --apply
```

### Same-camera restriction

```bash
lightroom-gps-repair \
    "/Photos/2026/Texel" \
    --same-camera-only \
    --apply
```

### Continue despite unsynced sidecars **[R3]**

```bash
lightroom-gps-repair \
    "/Photos/2026/Texel" \
    --allow-stubs \
    --apply
```

Skips sidecars that are missing their Lightroom metadata instead of aborting. They are still
never written to.

### Restore

```bash
lightroom-gps-repair restore \
    "/Backups/LightroomGPS/2026-09-19_193500"
```

---

## 27. Suggested Defaults

Recommended defaults:

```text
Mode:                     dry-run
Maximum donor gap:        600 seconds     [R2]
Warn donor gap:           1800 seconds    [R2]
Require RAW file:         no              [R2]
Donor scope:              selected root recursively
Same-folder restriction:  off
Same-camera restriction:  off
Copy latitude:            yes
Copy longitude:           yes
Copy altitude:            no
Overwrite existing GPS:   never
Create missing XMP:       no
Modify RAW files:         never
Backup:                   mandatory
Backup verification:      enabled
Report generation:        enabled
Create missing sidecar:   never           [R2]
Modify unsynced sidecar:  never           [R3]
Abort on unsynced:        yes (apply)    [R3]
```

**[R2]** Measured behaviour of these defaults against the 804-sidecar reference library:

```text
Scanned:                     804
Already geotagged:           701
Missing GPS:                 103

Repaired (within 600 s):      99
Repaired with WARN:            4
Skipped:                       0
```

---

## 28. Error Handling

The program must detect and report at least:

- unreadable RAW
- unreadable XMP
- malformed XMP
- unsupported timestamp
- missing capture timestamp
- invalid GPS coordinate
- incomplete GPS coordinate
- missing XMP
- backup failure
- insufficient disk space where detectable
- write failure
- verification failure
- duplicate or ambiguous sidecar association
- filesystem permission failure

A recoverable error concerning one photograph should normally not terminate processing of unrelated photographs.

A backup-system failure before writing is a critical error and must abort the entire apply operation.

---

## 29. Verification After Writing

After updating an XMP, the tool should read the metadata again and verify:

1. Latitude exists.
2. Longitude exists.
3. Latitude matches the intended donor value, within a tolerance of `1e-6` degrees. **[R2]**
4. Longitude matches the intended donor value, within a tolerance of `1e-6` degrees. **[R2]**
5. Critical unrelated metadata remains readable. **[R2]** This is defined concretely as:
   - the count of non-GPS properties has not decreased relative to the pre-write reading
   - `DateTimeOriginal` is still readable and unchanged
   - `tiff:Model` is still readable and unchanged
   - the file parses as well-formed XMP and is not empty

**[R2]** Verification must be performed by re-reading the written files, never by inspecting the
exit code of the write command (section 6.0). A single bulk re-read of all modified files is the
preferred implementation.

If verification fails, the file must be marked as failed.

Automatic restoration from backup may be considered, but an initial implementation may instead stop and report the exact backup required for restoration.

---

## 30. Non-Goals

The initial version shall not attempt to:

- interpolate locations between photographs
- infer routes
- use online reverse geocoding
- add city, state, country, or location names
- correct existing GPS
- evaluate GPS accuracy
- modify RAW files
- modify Lightroom's catalog database directly
- run while Lightroom is editing the same files
- automatically resolve corrupted GPS metadata
- create locations based on filesystem dates
- copy newly generated GPS to further photographs during the same run

---

## 31. Acceptance Criteria

The implementation can be considered complete when the following tests pass.

### Existing GPS preservation

Given an XMP with GPS coordinates:

```text
latitude = 53.1
longitude = 4.8
```

after execution:

```text
latitude = 53.1
longitude = 4.8
```

and the photograph is reported as untouched.

### Nearest previous donor

```text
12:00:00 GPS A
12:00:04 missing
12:01:00 GPS B
```

The missing image receives GPS A.

### Nearest following donor

```text
12:00:00 GPS A
12:00:56 missing
12:01:00 GPS B
```

The missing image receives GPS B.

### Leading gap

```text
missing
missing
GPS
```

Both missing photographs may be repaired from the following tagged photograph if they fall within the configured threshold.

### Trailing gap

```text
GPS
missing
missing
```

Both missing photographs may be repaired from the preceding tagged photograph if they fall within the configured threshold.

### Threshold enforcement

No coordinate is copied when the closest donor exceeds the configured maximum time difference.

### No donor propagation

Coordinates created during the current run are never subsequently used as donor coordinates.

### Backup guarantee

No original XMP is modified unless its verified backup already exists.

### RAW integrity

A checksum of every RAW file is unchanged before and after an apply operation.

### XMP metadata preservation

Unrelated Lightroom metadata remains unchanged after GPS insertion.

### Dry run

A dry run produces the same planned donor decisions as an apply run but modifies zero existing files.

### WARN tier **[R2]**

A candidate whose nearest donor lies between `--max-gap` and `--warn-gap` is repaired, reported
as `updated_warn`, printed as a WARN line, and counted separately in the summary.

A candidate whose nearest donor lies beyond `--warn-gap` is not modified.

Boundary behaviour is exact: a gap of precisely `--max-gap` applies silently, and a gap of
precisely `--warn-gap` applies with a warning.

### Sidecars are never created **[R2]**

If a planned sidecar is removed between the planning phase and the write phase, the run reports
it as failed and **no file is created** at that path.

Running the tool against a directory containing no sidecars creates nothing.

### Only XMP files are touched **[R2]**

After an apply run, a checksum sweep of the entire scan root shows that no file other than the
planned `.xmp` sidecars has changed. This includes RAW files, JPEGs, and unrelated sidecars.

### Existing GPS survives an intentionally wrong plan **[R2]**

If a sidecar that already contains GPS is forced into the modification plan, the ExifTool `-if`
guard refuses it and the file remains byte-for-byte unchanged (section 4.6).

### Unsynced sidecars **[R3]**

An apply run against a library containing a stub aborts with exit code 2, creates no backup, and
modifies no file. A dry run against the same library exits 0 and reports each stub as
`skipped_stub_sidecar`. A stub adjacent in time to a valid donor is never written, and never
acts as a donor itself. A genuine Lightroom sidecar is never misclassified.

### Reference library baseline **[R2]**

Against the 804-sidecar reference library, with default settings:

```text
804 scanned, 701 already geotagged, 103 missing
-> 99 repaired, 4 repaired with WARN, 0 skipped
```

and all 701 previously geotagged sidecars are byte-for-byte identical afterwards.

---

## 32. Recommended Implementation Stack

A practical first implementation would use:

```text
Language:       Python 3
Metadata:       ExifTool
Interface:      command line
Reports:        CSV + JSON + text log
Backup hashes:  SHA-256
```

The program itself should be relatively small.

The core GPS matching logic is simple. Most implementation effort should instead be invested in:

- safe metadata handling
- backup integrity
- deterministic behavior
- reporting
- validation
- failure recovery

The safety model is more important than minimizing the number of lines of code.