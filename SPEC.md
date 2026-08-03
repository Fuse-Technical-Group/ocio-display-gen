# ocio-display-gen — Specification

## Problem §spec:problem

*Status: complete*

LED video walls deviate from every broadcast standard in three ways at
once: native primaries with oversaturated red and green and desaturated
blue relative to standard gamuts; "diffuse white" luminance of 1000 cd/m²
or more; and a signal path that is often limited to SDR encodings. The
LED processor is the wrong place to correct this — vendor color
processing is opaque, poorly implemented, and hard to control on site.

Media servers and render engines (Disguise, Unreal, compositors) already
speak OpenColorIO. This project generates OCIO configs that carry a
measured characterization of a specific wall, so the renderer transforms
scene-linear imagery to the wall's native response and the processor is
reduced to a fixed, known decode step.

## Characterization model §spec:characterization-model

*Status: in progress*

The wall (panels + processor, as configured for show) is characterized
as a single black box: known code values in, measured light out.
`display_config.yaml` records the measurement:

- Panel and processor identity, firmware, calibration date.
- Measured native primaries and white point (CIE xy).
- Measured black level and peak luminance (cd/m², absolute).
- Processor output EOTF as configured (PQ, HLG, or gamma with value),
  or a measured per-channel response when the ideal curve deviates.
- Metrology: instrument, date, geometry, ambient conditions.

**Why end-to-end:** processor internals (scaling, calibration matrices,
uniformity) are unobservable and vendor-specific. Measuring through the
whole chain captures their combined effect without modeling them.

**Why absolute luminance:** the rendering transform is parameterized by
peak nits, and OCIO's linear conventions are anchored (1.0 = 100 cd/m²
for PQ paths). Relative measurements cannot place diffuse white
correctly on an HDR-bright wall.

The system shall validate measurement plausibility (chromaticity ranges,
CCT/duv of white, contrast ratio, EOTF sanity) before generating output,
with strict and warning modes.

## Generated config structure §spec:config-structure

*Status: not started*

The tool appends to a prebuilt ACES config (loaded via `ocio://` URI)
and writes a single self-contained `.ocio` file, because target runtimes
(Disguise confirmed) load exactly one config and cannot merge at
runtime. Within that config, characterization and rendering are kept in
OCIO's native two-part structure:

- A **display colorspace**, defined relative to the display reference
  (CIE-XYZ-D65), holds only measured colorimetry: XYZ → native-RGB
  matrix, absolute luminance scaling, and the inverse of the processor's
  EOTF. It contains no creative decisions and is exact within gamut.
- **View transforms** hold the rendering — how unbounded scene-linear
  maps into the wall's gamut and luminance range (§spec:view-transform).
- `addDisplayView` entries expose the wall as a named display with
  selectable views in any OCIO-aware application.

**Why the split:** it matches OCIO v2 semantics, lets multiple renderings
target one measured wall (and vice versa), and keeps the measurement
verifiable independently of taste. The prior single-colorspace approach
conflated the two and hard-clipped all out-of-range values.

The system shall derive the scene- and display-reference spaces from the
base config's interchange roles rather than assuming them. (The prior
implementation assumed ACEScg; the studio config's scene reference is
ACES2065-1 — a silent wrong-matrix bug this structure eliminates.)

## Rendering §spec:view-transform

*Status: not started*

Rendering intent is a per-view choice made by the operator in the media
server, not a generation-time decision: the wall is registered with
multiple views and switching intents never requires regenerating the
config (§req:success-criteria). Three views exist:

**VP Radiometric (default).** Colorimetric within the wall's volume,
compression only at its edges (§req:quality-attributes). Scene-linear
maps to absolute light through a configurable nits anchor (cd/m² per
scene-linear unit, recorded per show — §req:constraints). Out-of-gamut
chromaticities are compressed at the boundary by the ACES 2.0 gamut
compressor parameterized with the wall's measured primaries and peak —
untouched core, hue-preserving edge. Above-peak luminance follows a
selectable recorded policy: hard clamp, or a narrow shoulder confined
to the top of the range. End-to-end system gamma is 1.0: no tone
scale, no chroma reshaping, no surround compensation, and the encoding
leg is transparent by construction (§spec:signal-contract,
§spec:config-structure).

**Why radiometric is the default:** in virtual production the wall is
a light source being photographed. The camera must see the radiometry
the scene data specifies; photographic rendering belongs in the show's
grade, not in the wall (§req:user-stories).

**ACES 2.0.** The full ACES 2.0 output transform, parameterized by
measured peak luminance and native primaries as the limiting gamut,
for contexts where the wall shows finished pictures (IMAG, brand
content). **Why it is not the VP default:** its tone scale applies
contrast through the whole range (~1.55 log-log slope at mid-gray;
0.18 → 10 nits at 100 nit peak) and its chroma compression reshapes
the entire gamut interior. It is photographic by design and
radiometric nowhere — correct for pictures, wrong for light sources.

**Colorimetric (no rendering).** Bare display colorspace with hard
clip, for measurement and verification work.

**Why ACES 2.0 components:** the parameterized output transform and
standalone gamut compressor are the only widely deployed,
runtime-supported transforms parameterized by arbitrary primaries and
peak nits. Hand-rolled gamut mapping duplicates that work at lower
quality; OCIO has no other builtin gamut mapping.

Where the target runtime predates these transforms, decimated
fallbacks apply (§spec:version-targeting).

## OCIO version targeting §spec:version-targeting

*Status: not started*

Fidelity first, then decimate: the reference output targets the newest
OCIO (2.5 / ACES 2.0 studio config), and the tool degrades gracefully
for older runtimes. The generated config's profile version shall never
exceed the target runtime's OCIO library version (Disguise documents
this as a hard compatibility requirement).

Target tiers, selected in `display_config.yaml`:

| Tier | Runtime | Basis | Rendering |
|------|---------|-------|-----------|
| 2.5 | Disguise r32.2+, current DCCs | ACES 2.0 studio config | All three views (§spec:view-transform) |
| 2.4 | Disguise r29.1–r32.1 | ACES 1.3 studio config | VP Radiometric + ACES via 2.0 fixed functions (verify availability/naming per 2.4.x) |
| ≤2.3 | Legacy runtimes | ACES 1.3 studio config | ACES view falls back to nearest ACES 1.3 HDR output (peak-nit variant); VP Radiometric degrades to colorimetric hard clip — no parameterized gamut compressor exists |

**Why not target the lowest common denominator:** the ≤2.3 tier cannot
parameterize primaries, so it is inherently approximate. Building the
correct solution first gives a reference to measure the decimated tiers
against.

## Signal contract §spec:signal-contract

*Status: not started*

The generated config is only valid while the processor stays in the
state recorded in the characterization: fixed EOTF, locked intensity,
color processing and dynamic features disabled. The config's metadata
(descriptions) shall record this state so operators can restore and
audit it.

**Why the correction lives upstream of the link:** the inverse EOTF is
the last renderer-side operation, so the 10/12-bit SDR link carries
perceptually uniform code values and large corrections happen in
floating point, avoiding the banding that the same correction applied
in the processor would produce.

## White point policy §spec:white-point

*Status: not started*

When the wall's calibrated white differs from the content white (D65),
the config applies one of two explicit policies, selected in
`display_config.yaml` and recorded in the output:

- **adapted** (default): chromatic adaptation maps content white to the
  wall's native white. Preserves full brightness; standard practice.
- **absolute**: reproduces chromaticity exactly within gamut, at the
  cost of peak brightness and possible single-channel clipping.

**Why explicit:** the prior implementation applied a chromatic
adaptation transform silently via library defaults. The choice is
visible on camera and must be a recorded decision, not a side effect.

## Verification §spec:verification

*Status: not started*

Characterization is a checkable claim, not a hope. The system shall
provide a closed-loop verification path: generate probe patches, play
them through the real chain (media player → processor → wall), measure,
and report against predicted values. A wall passes when in-gamut,
sub-peak patches measure within **ΔE2000 ≤ 2 average, ≤ 5 maximum**,
and a neutral scene-linear ramp measures an end-to-end exponent of 1.0
within measurement tolerance (§req:success-criteria) — the latter
guards the unity-system-gamma property against regression. Round-trip
unit tests validate each generated transform against reference
implementations (colour-science) at build time.

**Why:** the model (xy primaries + ideal EOTF + additivity) is known to
be violated by LED walls in the tail (PWM chromaticity shift at low
drive, near-black response). Only measurement through the deployed
chain bounds the real error.

## Scope boundaries §spec:non-goals

*Status: complete*

Out of scope: processor-internal correction, per-panel uniformity,
temporal/PWM artifacts, camera-side ICVFX calibration (moiré, in-camera
metamerism — see OpenVPCal for that problem), and multi-wall config
merging (OCIO 2.5 config-merge is preview-status; revisit when stable).
