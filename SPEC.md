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
| ------ | --------- | ------- | ----------- |
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

*Status: complete*

This component's share of the verification loop is prediction. Every
generation writes, beside the config, a predictions artifact and the
probe patch imagery it describes: for each patch, the scene-linear
content that produces it, the code values the config emits, and the CIE
XYZ the wall is predicted to emit in cd/m². Sessions measure the
patches; OLE-Toolset judges the residuals against the umbrella's
thresholds (ΔE2000 ≤ 2 avg / ≤ 5 max, unity exponent). Analysis and
reporting stay out of this component (§spec:non-goals) — a generator
that graded its own output would be marking its own homework.

Predictions come from the generated config's own transforms. Each patch
runs forward twice — scene reference → (display, view) for the code
values, display colorspace → display reference for the colorimetry
those code values produce — and the drive-space patch is inverted back
to the content that produces it through the config's display-reference
→ scene-reference leg and the same drive-space matrix the rendering
uses. **Why not a second implementation:** an independent prediction
path would drift from the config the runtime executes, and a
disagreement between them would be indistinguishable from a wall fault;
it would also have to assume a scene reference the config derives
(§spec:config-structure). Tests hold the arrangement honest by
reproducing the predictions with colour-science.

Predictions target the default rendering (VP Radiometric): it is the
one making a radiometric claim, so it is the one a measurement can
falsify. The photographic view (§spec:view-transform) is verifiable
only against its own tone scale.

The probe set is fixed and documented rather than configurable — the
artifact is a contract between three tools, and per-show patch lists
would make sessions incomparable. Patches are stated as fractions of
the wall's full linear drive, where a probe set is meaningful: the
neutral ramp carries the radiometric claim and is spaced to sample the
bottom of the range, chromatic axes at full drive sit on the measured
gamut boundary and exercise the view's compressor, and half-drive and
half-saturation variants sit in its untouched core.

The artifact is bound to its config by sha256 (§spec:provenance) and
re-emits byte for byte after parsing, so a consumer can rewrite it
without perturbing the contract. Predictions are recorded at the
precision the file states, so the in-memory prediction and the file are
the same numbers. `--check-predictions` reports what a predictions file
describes and refuses when the config it names is no longer the config
on disk — measuring against predictions for a different config compares
the wall to something it is not running.

Probe imagery is two solid-color images per patch, sharing the patch
id as stem in the probe directory beside the config:

- A 16-bit **PNG** holding the predicted code values — a record for
  provenance and instrument-side reference, not a playback stimulus:
  its pixels are drive code values, and no renderer plays drive
  values back untouched. Written against the format from the standard
  library. **Why not an image library:** the stored pixel shall be
  exactly the code value the prediction was computed from, with no
  colorspace tag, gamma chunk, or encoder default able to reinterpret
  it. Predictions are computed from the quantized code value, so
  image and prediction agree exactly.
- A float32 **EXR** in the config's scene reference space, holding
  the nearest float32 to the scene-linear triple the predictions file
  records for the patch — two views of one computation. This is the
  renderer-path stimulus: the SDI loop drives predicted code values
  directly, proving the config math and the wall plus link, but never
  the deployed renderer's application of the config. Displaying the
  EXR through the config's default (display, view) closes that gap —
  the renderer's output is judged against the same predictions. The
  input space is load-bearing: the file has to be interpreted as the
  config's scene reference, and a renderer that assumes another space
  produces plausible but wrong output. The session half lives in the
  umbrella roadmap (`§road:renderer-verification` there).

The PNG's no-image-library rationale does not transfer to the EXR:
this artifact exists to be interpreted by the renderer, not to bypass
interpretation. Byte-determinism (§spec:provenance) still rules out
an image library, whose bytes sit at the mercy of its version, so the
EXR writer is hand-rolled over the standard library — deterministic
by construction, zero runtime dependencies, verified against the
OpenEXR library (a dev-only dependency) rather than self-certified.
Uncompressed scanlines cost ~800 KB per patch and are accepted for
the widest reader compatibility and the simplest byte-exact framing;
float32 rather than half because half's three significant digits
cannot carry the artifact's recorded precision. Each EXR carries
`sceneReference` and `configSha256` string attributes, so it names
its own interpretation and config; `--check-predictions` audits the
sibling probe directory's EXR bindings the same way it audits the
config hash. The standard `chromaticities` attribute is deliberately
absent: the scene reference is derived from each config's interchange
role, and stamping fixed primaries would silently bind the artifact
to one base-config family — the `sceneReference` name is the
authoritative statement of interpretation.

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
