"""Shared fixtures.

Real sidecars live in tests/fixtures/real (checked in). Synthetic sidecars are
generated here so edge cases absent from the sample data are still covered.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gps_repair.model import Photo, RunConfig

FIXTURES = Path(__file__).parent / "fixtures"
REAL = FIXTURES / "real"

TZ = timezone(timedelta(hours=2))
BASE = datetime(2026, 9, 18, 12, 0, 0, tzinfo=TZ)

HAS_EXIFTOOL = shutil.which("exiftool") is not None
requires_exiftool = pytest.mark.skipif(
    not HAS_EXIFTOOL, reason="exiftool binary not available"
)


def at(seconds: float) -> datetime:
    """A capture time `seconds` after the base instant."""
    return BASE + timedelta(seconds=seconds)


def photo(name: str, seconds: float | None, lat=None, lon=None, **kw) -> Photo:
    """Build a Photo without touching the filesystem."""
    return Photo(
        xmp_path=Path(f"/lib/{name}.xmp"),
        capture_time=at(seconds) if seconds is not None else None,
        xmp_lat=lat,
        xmp_lon=lon,
        **kw,
    )


@pytest.fixture
def config(tmp_path) -> RunConfig:
    return RunConfig(root=tmp_path)


# --- Synthetic sidecar construction -------------------------------------------------

XMP_TEMPLATE = """<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
    xmlns:exif="http://ns.adobe.com/exif/1.0/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   xmp:Rating="{rating}"
   tiff:Make="SONY"
   tiff:Model="ILCE-1M2"
{extra}   crs:RawFileName="{stem}.ARW">
   <dc:creator><rdf:Seq><rdf:li>Daniel Schaefer</rdf:li></rdf:Seq></dc:creator>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def write_xmp(path: Path, when: str | None = "2026-09-18T12:00:00.000+02:00",
              lat: str | None = None, lon: str | None = None,
              rating: int = 1, extra_props: str = "") -> Path:
    """Write a synthetic sidecar. `lat`/`lon` use Adobe's deg,decimal-minutes form."""
    lines = []
    if when is not None:
        lines.append(f'   exif:DateTimeOriginal="{when}"')
    if lat is not None:
        lines.append(f'   exif:GPSLatitude="{lat}"')
    if lon is not None:
        lines.append(f'   exif:GPSLongitude="{lon}"')
    extra = "".join(f"{line}\n" for line in lines) + extra_props
    path.write_text(
        XMP_TEMPLATE.format(stem=path.stem, extra=extra, rating=rating),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def library(tmp_path) -> Path:
    """A small on-disk library: two donors around one GPS-less photo."""
    lib = tmp_path / "lib"
    lib.mkdir()
    write_xmp(lib / "A.xmp", "2026-09-18T12:00:00.000+02:00",
              lat="53,0.5266833N", lon="4,45.65015E")
    write_xmp(lib / "B.xmp", "2026-09-18T12:00:03.000+02:00")
    write_xmp(lib / "C.xmp", "2026-09-18T12:00:05.000+02:00",
              lat="53,1.0000000N", lon="4,46.0000000E")
    return lib
