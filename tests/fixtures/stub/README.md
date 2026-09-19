# Stub sidecar fixtures

`stub_rating_only.xmp` is a verbatim reproduction of a sidecar observed in
`/Users/daniel/Pictures/2026/2026-09_Texel/2026-09-16_Texel` on 2026-09-19.

514 such files were present across two folders. They are written by
`XMP Core 5.6.0` (not Lightroom Classic) and carry only a star rating: no
capture timestamp, no camera model, no GPS, no develop settings.

They mean Lightroom has not written its catalog metadata to the file yet.
Running *Metadata > Read Metadata from Files* against one would replace rich
catalog metadata with this near-empty file.

The live library was fixed with *Save Metadata to Files* shortly after the
observation, so it can no longer reproduce the condition. This fixture is the
regression surface.
