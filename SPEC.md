# ocio-display-gen — Specification

Component spec for the **generate layer** of
[color-wrangler](https://github.com/Fuse-Technical-Group/color-wrangler)
— characterization-based color management for real-time playback on
LED surfaces. The system problem, four-layer architecture, artifact
contracts, measurement sessions, and verification policy live in the
umbrella spec; sections here refine the generate layer only.
Terminology (including "wall" as shorthand for any LED surface)
follows the umbrella.

## Problem §spec:problem

*Status: complete*

Within the color-wrangler system, this component turns a show manifest
and a promoted measurements artifact into a single self-contained OCIO
config — the profile and rendering intents for one measured wall — and
into the predictions file that verification measures against. It needs
OCIO semantics and no hardware: fully testable without a photon.

## Characterization inputs §spec:characterization-model

*Status: in progress*

The generator consumes the two artifact-chain files defined by the
umbrella: the human-authored **show manifest** (naming, policies,
intended signal contract, promotion pointer `{file, sha256}`) and the
machine-written, immutable **measurements artifact**. Validation
splits along the same line — plausibility checks (chromaticity ranges,
CCT/duv of white, contrast ratio, EOTF sanity) target the measurements
artifact; policy checks (enums, anchor bounds) target the manifest —
with strict and warning modes. Missing promotion pointer, unreadable
artifact, or hash mismatch fail loud.

Still open in this component: consuming measured per-channel response
ramps (fitted 1D LUTs in place of the ideal EOTF) and real
session-written artifacts once `color-wrangler characterize` exists
(the shipped sample artifact is a hand-built exemplar, labeled as
such).

## Generated config structure §spec:config-structure

*Status: complete*

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

The scene- and display-reference spaces are derived from the base
config's interchange roles (`aces_interchange`,
`cie_xyz_d65_interchange`), never assumed; generation fails loud when a
role is absent. (The prior implementation assumed ACEScg; the studio
config's scene reference is ACES2065-1 — a silent wrong-matrix bug this
derivation eliminates.)

## Rendering §spec:view-transform

*Status: complete*

Rendering intent is a per-view choice made by the operator in the media
server, not a generation-time decision: the wall is registered with
multiple views and switching intents never requires regenerating the
config. Three views exist:

**VP Radiometric (default).** Colorimetric within the wall's volume,
compression only at its edges. Scene-linear maps to absolute light
through a configurable nits anchor (cd/m² per scene-linear unit,
recorded per show). Out-of-gamut chromaticities are compressed at the
boundary by the ACES 2.0 gamut compressor parameterized with the
wall's measured primaries and peak — untouched core, hue-preserving
edge. Above-peak luminance follows a selectable recorded policy: hard
clamp, or a narrow shoulder confined to the top of the range.
End-to-end system gamma is 1.0: no tone scale, no chroma reshaping, no
surround compensation, and the encoding leg is transparent by
construction (§spec:signal-contract, §spec:config-structure).

**Why radiometric is the default:** in virtual production the wall is
a light source being photographed. The camera shall see the radiometry
the scene data specifies; photographic rendering belongs in the show's
grade, not in the wall.

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

Target tiers, selected in the show manifest:

| Tier | Runtime | Basis | Rendering |
|------|---------|-------|-----------|
| 2.5 | Disguise r32.2+, current DCCs | ACES 2.0 studio config | All three views (§spec:view-transform) |
| 2.4 | Disguise r29.1–r32.1 | ACES 1.3 studio config | VP Radiometric + ACES via 2.0 fixed functions (verify availability/naming per 2.4.x) |
| ≤2.3 | Legacy runtimes | ACES 1.3 studio config | ACES view falls back to nearest ACES 1.3 HDR output (peak-nit variant); VP Radiometric degrades to colorimetric hard clip — no parameterized gamut compressor exists |

**Why not target the lowest common denominator:** the ≤2.3 tier cannot
parameterize primaries, so it is inherently approximate. Building the
correct solution first gives a reference to measure the decimated tiers
against.

## Signal contract recording §spec:signal-contract

*Status: complete*

The signal contract itself — the processor lockdown state a config is
valid for, and the link-encoding guidance — is umbrella policy. This
component records it: the intended state from the show manifest (EOTF,
intensity, processing disabled) is emitted into the generated config's
metadata so operators can restore and audit it, and sessions can diff
live state against it.

## White point policy §spec:white-point

*Status: complete*

When the wall's calibrated white differs from the content white (D65),
the config applies one of two explicit policies, selected in the show
manifest and recorded in the output:

- **adapted** (default): chromatic adaptation maps content white to the
  wall's native white. Preserves full brightness; standard practice.
- **absolute**: reproduces chromaticity exactly within gamut, at the
  cost of peak brightness and possible single-channel clipping.

**Why explicit:** the prior implementation applied a chromatic
adaptation transform silently via library defaults. The choice is
visible on camera and shall be a recorded decision, not a side effect.

## Predictions §spec:verification

*Status: not started*

This component's share of the verification loop is prediction: for a
given generated config, emit probe patch imagery and a documented,
stable predictions file (config hash included per §spec:provenance) —
the handoff contract consumed by color-wrangler sessions and analyzed by
OLE-Toolset against the umbrella's thresholds (ΔE2000 ≤ 2 avg / ≤ 5
max, unity exponent). Round-trip unit tests validate each generated
transform against reference implementations (colour-science) at build
time.

## Artifact provenance §spec:provenance

*Status: complete*

This component implements the generate-layer links of the umbrella's
hash chain: sha256 over file bytes via the standard library. The show
manifest's promotion pointer is verified against the measurements
artifact before generation (mismatch refuses, naming both digests);
the generated config's metadata records both input hashes and the
generator version as greppable `Provenance:` lines; predictions record
the config's hash. Generated output is byte-deterministic (no embedded
timestamps) — hashing and the determinism requirement enforce each
other. Metadata-bound strings reject unprintable characters
(`str.isprintable()`), preventing forged provenance lines via
serialized control characters or Unicode line separators.

## Scope boundaries §spec:non-goals

*Status: complete*

Umbrella non-goals apply. Additionally out of scope for this
component: instrument and signal I/O of any kind (sessions own
hardware), report rendering (OLE-Toolset owns validation reports), and multi-wall
config merging (OCIO 2.5 config-merge is preview-status; revisit when
stable).
