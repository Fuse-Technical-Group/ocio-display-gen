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

Throughout these documents, "wall" is industry shorthand for any LED
surface — walls, ceilings, floors, domes, and other geometries. Nothing
in the system is orientation- or geometry-specific.

## Characterization model §spec:characterization-model

*Status: in progress*

The wall (panels + processor, as configured for show) is characterized
as a single black box: known code values in, measured light out.
Characterization input is split along the human/machine line into two
files the generator consumes together:

- A **decisions file** (human-authored, reviewed): show naming, nits
  anchor, overflow policy, white point policy, target OCIO tier,
  validation mode, the *intended* processor lockdown state — and a
  promotion pointer to the measurement artifact of record as
  `{file, sha256}` (§spec:provenance).
- A **measurements artifact** (machine-written by a characterization
  session, immutable, never hand-edited): measured native primaries
  and white point (CIE xy), black level and peak luminance (cd/m²,
  absolute), per-channel response when measured, ambient floor,
  instrument identity and firmware, processor-state snapshot, and
  timestamps.

**Why the split:** humans editing measured values only inject error,
and machine-attestable device state (processor gamma, intensity,
processing flags, panel/instrument identity) is readable from device
APIs — transcription is an error source with no compensating value.
The human act at the seam is *acceptance*: promoting a measurement run
to the characterization of record by recording its hash — a signature,
not an edit. Immutable measurement artifacts also give drift history
for free: two sessions diff as two files.

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

*Status: complete*

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

**Link encoding.** Where the processor offers a true PQ decode, it is
preferred at 10-bit (near-threshold quantization across the full
luminance range; absolute code-to-nits mapping strengthens this
contract) — but only after verification shows it decodes to light with
clipping only, since vendor HDR modes are where unauditable processing
lives (§req:constraints). Where the front end is SDR-only — the
reference fleet's pre-Dynacal panels cannot accept PQ/HLG at all
(§req:problem-statement) — the contract is a pure gamma decode: prefer
gamma 2.4 over 2.2 (better code allocation in the 1–20 cd/m² band when
stretched over 1000 cd/m²) and a 12-bit link over 10-bit, since 10-bit
gamma across that range quantizes visibly on gradients. Chroma
subsampling (4:2:2) is tolerated but 4:4:4 is preferred. **Why no
custom exponent:** a power law's code allocation barely improves with
exponent, and a nonstandard decode state defeats on-site auditability.

## White point policy §spec:white-point

*Status: complete*

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

## Artifact provenance §spec:provenance

*Status: not started*

Every artifact in the pipeline records the sha256 content hashes of
its inputs, binding the chain as it is built:

- The decisions file's promotion pointer is `{file, sha256}` of the
  measurement artifact of record (§spec:characterization-model).
- The generated config's metadata records both input hashes and the
  generator version; generation refuses when the promotion hash does
  not match the measurement artifact on disk.
- Prediction files record the config's hash; verification reports
  record the prediction and measurement hashes and the live processor
  snapshot.

Any config found on a show machine is therefore traceable to the
exact measurements and decisions that produced it, and *checkable*:
if the current files no longer hash to what the config records, the
config is stale and demonstrably so.

**Why hashes and not signatures:** the threat model is error and
drift, not adversaries. sha256 over file bytes via the standard
library — no keys, no PKI, no new dependencies. **Consequence:**
generated artifacts are byte-deterministic (no embedded generation
timestamps), which the determinism requirement
(§req:success-criteria) already demands — hashing and reproducibility
enforce each other.

## Closed-loop measurement §spec:measurement-loop

*Status: not started*

A measurement session is one command run with the wall powered and the
instrument aimed. Sessions come in two modes over one shared core
(contract audit, ambient gate, drive, settle, read, log):

- **`characterize`** — drives a fixed, versioned, *device-referred*
  patch protocol (full-drive R/G/B/W, black, shadow-dense per-channel
  ramps, Y/C/M additivity triad, gray tracking ramp — raw code values,
  no OCIO in the loop, since characterization precedes any config) and
  emits the immutable measurements artifact
  (§spec:characterization-model). The patch protocol is ours: defined
  in MEASUREMENT.md, versioned with the tool.
- **`verify`** — drives config-derived probe patches against the
  generator's predictions and invokes the ΔE report
  (§spec:verification). Sessions are re-runnable in minutes, making
  pre-show drift checks routine (§req:user-stories).

**Ownership split:** session tooling (both modes — everything that
touches hardware) lives in a sibling repository with a
surface-register name, depending on bmd-signal-gen, colour-specio,
and the Tessera read-only client. This repository keeps everything
that needs OCIO semantics and no hardware: generation, prediction,
and the ΔE report. The seam at every stage is a file
(§spec:provenance). **Why:** characterize and verify share the
session machinery (splitting them would force duplication or a
framework), while the generator stays hardware-free and fully
testable without a photon. Session workstreams migrate to the
sibling's governance at its creation.

Session flow, each stage observable in the session log:

- **Contract audit** — snapshot the processor's state read-only
  (Tessera HTTP API for Brompton) and diff against the recorded signal
  contract (§spec:signal-contract); refuse to measure when they
  diverge. Per patch batch, read back the processor's input metadata
  and confirm the wire format matches the session's declared format.
- **Provenance gate** (`verify` mode) — refuse to measure against a
  config whose recorded input hashes do not match the files on disk
  (§spec:provenance): a stale config would produce a report about a
  characterization that no longer exists.
- **Ambient gate** — the session opens and closes with a black-floor
  reading (wall showing black; reflected ambient plus panel leakage)
  and records it with the measurements. Characterization-grade
  sessions refuse when the floor exceeds the budget implied by the
  dark-patch ΔE tolerance; drift checks instead compare the live
  floor against the one recorded at characterization, so ambient
  changes cannot alias into apparent drift. Ambient is recorded and
  gated, never compensated: a display cannot emit negative light to
  cancel reflection, and baking a venue's ambient into the
  characterization poisons the config everywhere else.
- **Patch drive** — display patches via bmd-signal-gen on a DeckLink
  device, using its live color-update surface; probe patch sets
  (§spec:verification) are emitted in a format bmd-signal-gen consumes
  directly. The session explicitly declares pixel format and EOTF
  signaling — bmd-signal-gen defaults to PQ InfoFrames, which would
  fault an SDR-contract wall (§req:constraints), so SDR walls are
  driven with explicit SDR signaling.
- **Instrument read** — settle delay, then a triggered single
  measurement returning XYZ via colour-specio (Colorimetry Research
  family: CR-300 for characterization-grade sessions, CR-120 for
  drift checks — §req:constraints). colour-specio's virtual
  instruments back hardware-free session tests.
- **Report** — the session ends by producing the measurements file and
  invoking the ΔE report; pass/fail per §spec:verification thresholds.

**Why patches ride the show chain, not the processor's generator:**
the internal generator injects downstream of input decode, bypassing
HDMI/SDI receive, YCbCr conversion, range handling, and bit-depth
truncation — it characterizes a system the show signal never
traverses, violating the end-to-end black box
(§spec:characterization-model). Its only role is manual
troubleshooting, documented in the measurement guide.

**Why the processor is read-only:** auditability. The tool observes
and refuses; it never mutates show hardware, so a session can run
against a live rig without risk.

**Why no iteration:** measure-fit-remeasure convergence loops are a
control-systems project, not glue. A session characterizes or
verifies once and reports; escalation (fitted 1D LUTs,
§spec:characterization-model) is a human decision consuming the same
measurements file.

External systems are referenced, not re-specified: bmd-signal-gen owns
patch rendering and wire-format correctness (its spec documents the
validated formats; sessions shall refuse formats not yet validated
there), pydecklink owns device access, and colour-specio owns
instrument communication (consumed at a pinned version — a git SHA
until upstream's current API ships to PyPI — not forked; its
`measure()` surface is the driver contract, and upstream is the venue
for fixes: bench-confirmed asks are a fresh PyPI release and a missing
scipy dependency declaration).

## Scope boundaries §spec:non-goals

*Status: complete*

Out of scope: processor-internal correction, per-panel uniformity,
temporal/PWM artifacts, camera-side ICVFX calibration (moiré, in-camera
metamerism — see OpenVPCal for that problem), multi-wall config
merging (OCIO 2.5 config-merge is preview-status; revisit when stable),
and generalized instrument/playout abstraction frameworks — drivers
stay pinned to the hardware in hand (§spec:measurement-loop).
