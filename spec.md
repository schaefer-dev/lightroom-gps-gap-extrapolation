Below is a single self-contained Markdown specification that can be handed directly to an implementation agent or used as the basis for the program.

# Lightroom GPS Gap Repair Tool - Requirements Specification

## 1. Purpose

The tool shall repair missing GPS coordinates in a Lightroom Classic photo library by copying the GPS coordinates from the temporally nearest photograph that already contains valid GPS information.

The intended use case is a Sony camera receiving location information from a paired phone. Immediately after the camera is switched on or the phone connection is temporarily unavailable, one or more photographs can be created without GPS information. Photographs taken shortly before or shortly afterwards generally contain valid GPS coordinates.

The tool shall operate outside Lightroom Classic and update Lightroom's XMP sidecar files.

The primary goals are:

- Never overwrite existing GPS metadata.
- Never modify RAW image files.
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

### 6.1 Target GPS detection

Whether a sidecar is eligible for modification must be determined from the XMP sidecar itself.

Relevant XMP GPS fields include the effective equivalents of:

```text
XMP-exif:GPSLatitude
XMP-exif:GPSLongitude
```

An XMP is eligible only when both latitude and longitude are absent.

### 6.2 Additional RAW safety check

The associated RAW file should also be inspected.

If the RAW itself already contains valid GPS but the XMP does not, the program should default to:

```text
SKIPPED: source RAW already contains GPS
```

This is safer than creating potentially redundant or conflicting XMP metadata.

This behavior may later become configurable.

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

Timezone information should be used if present.

For photographs from the same camera and shooting session, timestamps without explicit timezone information may be treated as belonging to the same local camera time domain.

The implementation must use a consistent timestamp representation internally.

---

## 8. Candidate Definition

A photograph is a repair candidate only when all of the following conditions are true:

1. A supported RAW file exists.
2. A corresponding XMP sidecar exists.
3. The XMP contains neither GPS latitude nor GPS longitude.
4. The RAW does not already contain valid GPS.
5. A valid capture timestamp can be determined.
6. At least one valid donor exists within the configured maximum time difference.

If any requirement fails, the photograph must be skipped and included in the report.

The initial version should not create new XMP files for photographs that have no sidecar.

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

## 11. Maximum Time Difference

The tool must support a configurable maximum donor time difference.

Recommended default:

```text
120 seconds
```

Example:

```text
--max-gap 120
```

A candidate whose nearest valid donor is farther away than the configured threshold must not be changed.

Example:

```text
15:00:00 GPS
15:14:00 missing
```

with:

```text
max-gap = 120 seconds
```

Result:

```text
SKIPPED: nearest GPS donor is 840 seconds away
```

There must be no unlimited matching mode enabled by default.

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

RAW photographs:                  18,492
Existing GPS photographs:         17,841
Missing GPS candidates:              651
Eligible for automatic repair:       623
Skipped:                              28

Maximum donor gap:                  120 s

623 XMP files will be modified.
623 XMP files will first be backed up.

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

Example CSV:

```csv
target,donor,time_difference_seconds,latitude,longitude,result
DSC01234.ARW,DSC01235.ARW,2.41,53.123456,4.812345,updated
DSC04412.ARW,DSC04411.ARW,8.73,53.124001,4.811932,updated
DSC09201.ARW,,318.22,,,skipped_max_gap
```

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

with:

```text
max gap = 120 seconds
```

Result:

```text
SKIPPED
```

No location should be guessed.

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

with:

```text
max gap = 90 seconds
```

Expected result:

```text
12:01:00 -> GPS A
12:02:00 -> SKIP
12:03:00 -> SKIP
```

The generated GPS at 12:01 must not become a new donor.

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
    and photo.raw_has_no_gps
]

sort original_donors by capture_time

for candidate in candidates:
    previous = nearest donor before candidate
    following = nearest donor after candidate

    donor = donor with smallest absolute time difference

    if no donor:
        skip

    elif time_difference > max_gap:
        skip

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

### Custom maximum gap

```bash
lightroom-gps-repair \
    "/Photos/2026/Texel" \
    --max-gap 60 \
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
Maximum donor gap:        120 seconds
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
3. Latitude matches the intended donor value.
4. Longitude matches the intended donor value.
5. Critical unrelated metadata remains readable.

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