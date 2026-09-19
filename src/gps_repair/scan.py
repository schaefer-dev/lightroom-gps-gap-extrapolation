"""Filesystem scanning and sidecar/RAW pairing (spec 2)."""

from __future__ import annotations

from pathlib import Path

# Spec 2: Sony first, but the set is open for future formats.
RAW_EXTENSIONS = {".arw", ".cr2", ".cr3", ".nef", ".orf", ".raf", ".rw2", ".dng"}
XMP_EXTENSION = ".xmp"


class ScanResult:
    def __init__(self) -> None:
        self.sidecars: list[Path] = []
        self.raws: dict[str, Path] = {}
        self.ambiguous: list[str] = []
        self.orphan_raws: list[Path] = []


def _key(path: Path) -> str:
    """Pairing key: parent directory plus case-folded stem."""
    return f"{path.parent}/{path.stem.lower()}"


def scan(root: Path) -> ScanResult:
    """Walk `root` recursively, collecting sidecars and pairing RAW files.

    Extensions are matched case-insensitively (spec 2).
    """
    result = ScanResult()
    seen_sidecars: dict[str, Path] = {}

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()

        if suffix == XMP_EXTENSION:
            key = _key(path)
            if key in seen_sidecars:
                # Spec 28: duplicate/ambiguous sidecar association.
                result.ambiguous.append(key)
            else:
                seen_sidecars[key] = path
                result.sidecars.append(path)
        elif suffix in RAW_EXTENSIONS:
            result.raws.setdefault(_key(path), path)

    for key, raw in result.raws.items():
        if key not in seen_sidecars:
            result.orphan_raws.append(raw)

    return result


def raw_for(sidecar: Path, raws: dict[str, Path]) -> Path | None:
    return raws.get(_key(sidecar))
