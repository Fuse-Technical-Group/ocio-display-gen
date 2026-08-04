# Requirements

## Problem statement §req:problem-statement

LED video walls used in virtual production and live events have
nonstandard native gamuts (oversaturated red/green, desaturated blue),
diffuse-white luminance of 1000 cd/m² and up, and often an SDR-only
signal path. Getting scene-linear imagery to display correctly means
correcting for all of this somewhere — and the LED processor is the
worst available place: vendor color processing is opaque, poorly
implemented, and painful to control on site.

The target user is a color/engineering lead who measures walls and
builds show pipelines. Media servers and render engines already support
OpenColorIO; what's missing is a tool that turns wall measurements into
an OCIO config those systems can load directly, so the correction runs
in the renderer's floating-point pipeline instead of the processor.

Two realities make the processor path untenable rather than merely
inconvenient. First, processor HDR modes are often unavailable outright:
Brompton HDR input (PQ/HLG) is hardware-gated on Dynamic Calibration
(R2/R2+ receiver cards plus Hydra measurement), and pre-Dynacal panels
render black if sent HDR video — such walls have an SDR-gamma-only
front end regardless of their optical capability. Second, even where
vendor HDR or gamut modes exist (Brompton, Novastar, and others), their
math is closed, unauditable, and unfixable in the field. The tool's
core value is therefore twofold: reconstruct a wall's full optical
capability ("HDR" relative to scene-linear content in the compositor,
not on the wire) through an SDR-only signal path, and relocate as much
display math as possible into OCIO where it can be inspected, verified,
and corrected.

## Success criteria §req:success-criteria

- The generated config loads in target OCIO runtimes (Disguise
  confirmed) and the wall appears as a named display with selectable
  views — no runtime errors, no version incompatibility.
- **Radiometric accuracy (VP):** in-gamut, sub-peak probe patches
  played through the real chain (media player → processor → wall)
  measure within **ΔE2000 ≤ 2 average, ≤ 5 maximum** of predicted
  values.
- **Unity system gamma (VP):** a neutral scene-linear ramp played
  through the chain measures an end-to-end exponent of 1.0 within
  measurement tolerance — no hidden contrast from encode/decode
  mismatch or viewing-environment compensation.
- Out-of-gamut and above-peak content degrades smoothly on the wall —
  no hue skew, banding, or hard edges at the gamut boundary.
- Switching rendering intent is an operator action in the media server
  (view selection), not a config regeneration.
- Re-running the tool on the same measurements reproduces the same
  config (deterministic output).
- **SDR-front-end capability:** a wall whose processor accepts only
  gamma-encoded SDR signal reaches its full measured luminance and
  gamut range when driven from scene-linear content through the
  generated config, with the processor in plain SDR mode.
- **Auditability:** every transform between scene-linear content and
  wire code values is readable in the generated config; nothing
  color-critical is delegated to closed processor modes.

## User stories §req:user-stories

- As a color/engineering lead, I measure a wall with a
  spectroradiometer, record the results in a YAML file, run the tool,
  and load the resulting config in Disguise — so scene-linear content
  renders correctly on the wall without touching processor color
  settings.
- As a lead on a VP shoot, I select the radiometric view so the wall
  emits the light the scene data specifies — the wall is a light
  source being photographed, and the camera must see plate radiometry,
  not a "pleasing picture" rendering.
- As a lead on a live event, I select an ACES rendering view for
  finished content (brand packages, IMAG) so unbounded HDR imagery
  looks photographic on the wall.
- As a lead, I run a verification pass — probe patches through the
  deployed chain, measured and compared against predictions — so I can
  demonstrate the wall's accuracy to a DP or client with numbers.
- As a lead, I generate a config for an older runtime version and get
  the best rendering that version supports, with the compromises
  documented.
- As a lead with pre-Dynacal panels that cannot accept PQ or HLG, I
  drive the wall's full 1000+ nit, wide-gamut capability from
  scene-linear content over the SDR-only link — the panel hardware is
  not the limit, the processor's decode stage is.
- As a lead who distrusts a processor's color math, I move that math
  into the OCIO config so I can read, verify, and fix it — the
  processor is reduced to a dumb, measurable decode.
- As a lead with the wall powered and the probe aimed, I run one
  command and get a pass/fail characterization or verification report
  — no manual patch stepping, no measurement transcription. Re-running
  before a show takes minutes, so drift is caught before an audience
  sees it.

## Quality attributes §req:quality-attributes

- **Accuracy first:** radiometric fidelity through as much of the
  wall's volume as the hardware allows; compression confined to the
  edges (gamut boundary, luminance ceiling).
- **Fail loud:** implausible measurements or unsupported version
  combinations stop generation with a clear message; a silently wrong
  config is worse than no config.
- **Compatibility:** generated configs never exceed the target
  runtime's OCIO library version.
- **Expert-operated:** the tool is run by the color/engineering lead,
  not on-site ops — clarity and correctness outrank hand-holding.

## Constraints §req:constraints

- Target runtimes load exactly one OCIO config and cannot merge configs
  at runtime; output must be a single self-contained file appended to a
  standard ACES config.
- The signal path to the processor may be SDR-only (gamma-encoded
  10/12-bit); the wall's HDR capability is reached by characterizing
  the processor's fixed decode state, not by an HDR link format. This
  is not always a choice: the reference fleet (pre-Dynacal ROE Black
  Pearl 2 on Brompton SX40) cannot accept PQ/HLG input at all — HDR
  video to non-Dynacal panels renders black.
- Support is processor-agnostic in principle (Brompton, Novastar,
  others): the tool depends only on a measurable, stable decode state,
  not on any vendor feature beyond it.
- Measurement patches are driven through the deployed signal path
  (DeckLink playout via bmd-signal-gen). The processor's internal test
  pattern generator is not used for measurements: it injects downstream
  of input decode, so its patches characterize a different system than
  the show signal traverses. The processor is observed read-only
  (state snapshot, input metadata) — the tool never mutates show
  hardware.
- Measurement sessions require a wire format validated end-to-end
  (bit depth, RGB/YCbCr, quantization range, HDR InfoFrame state).
  RGB 12-bit is validated in bmd-signal-gen today; SDI and 10-bit
  YCbCr validation is in progress there. An unvalidated link bakes its
  own quantization into the "measured" response.
- Instrument support is pinned to the Colorimetry Research family
  (CR-300 spectroradiometer and CR-120 colorimeter on hand) via the
  colour-specio library — no generalized instrument framework.
  Characterization-grade measurements require the spectroradiometer:
  colorimeter filter/observer mismatch is largest on narrow-band LED
  primaries. The colorimeter serves development and drift checks.
- The generated config is valid only while the processor stays in its
  recorded state (EOTF, intensity, processing disabled).
- Radiometric claims require an explicit absolute anchor: the config
  records how many cd/m² one scene-linear unit represents, set per
  show.
- Above-peak luminance handling is a recorded, selectable policy —
  hard clamp (radiometric to the ceiling) or narrow shoulder — because
  different shoots weigh flat-lining vs. near-peak error differently.

## Priorities §req:priorities

1. **Must:** correct colorimetric foundation (display colorspace from
   measurements) — everything else builds on it.
2. **Must:** VP radiometric view with nits anchor, edge-only
   compression, unity system gamma — the primary use case.
3. **Must:** closed-loop verification with the ΔE thresholds above —
   accuracy claims are worthless unmeasured.
4. **Should:** ACES 2.0 photographic view for finished-content
   contexts.
5. **Should:** version tiers for older runtimes.
6. **Should:** closed-loop measurement sessions (automated patch
   drive + instrument read + report) once the verification harness
   exists — automation deletes the largest real error source
   (hundreds of manual patch/measurement transcriptions).
7. **Nice:** measured per-channel EOTF (1D LUT) refinement; hosted
   docs; support for non-Disguise runtimes beyond config-level
   compatibility.
