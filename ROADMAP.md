# ocio-display-gen — Roadmap

## Rendering views §road:rendering-views

### VP Radiometric view §road:vp-radiometric-view

Add the default VP Radiometric view — configurable nits anchor,
ACES 2.0 gamut compressor at the boundary, selectable clamp/shoulder
overflow policy — wired from `display_config.yaml` through
`OCIODisplayGen.py`. §spec:view-transform. Depends on
§road:colorimetric-view-surface.

### ACES 2.0 base config selection §road:aces2-base-config

Extend `display_config.yaml` base-config selection to the ACES 2.0 /
OCIO 2.5 studio config URI scheme. §spec:version-targeting.

### Parameterized output transform view §road:aces2-view-transform

Add a view using the ACES 2.0 output transform parameterized by
measured peak luminance and native primaries, for finished-content
contexts. §spec:view-transform. Depends on
§road:colorimetric-view-surface and §road:aces2-base-config.

**Verify:** Generate the config and exercise both views. VP
Radiometric: a neutral scene-linear ramp round-trips through
view + simulated display EOTF at exponent 1.0; in-gamut sub-peak
values pass through colorimetrically (matrix-only, within float
tolerance); a hue sweep beyond the wall gamut compresses smoothly with
no hue skew; above-peak values follow the selected clamp or shoulder
policy. ACES 2.0 view: an exposure ramp rolls off rather than clips,
and diffuse white (1.0) lands at the ACES-2.0-predicted level for the
measured peak nits.

## Version tiers §road:version-tiers

### Target-runtime tier selection §road:tier-selection

Add a target OCIO runtime option to `display_config.yaml` that selects
base config and caps the emitted profile version (2.5 and 2.4 tiers).
§spec:version-targeting. Depends on §road:aces2-view-transform.

### Legacy ≤2.3 tier §road:legacy-23-tier

Emit the ≤2.3 fallback: nearest ACES 1.3 HDR output view plus the
display colorspace, with documented gamut-delta clipping.
§spec:version-targeting. Depends on §road:tier-selection.

**Verify:** Generate one config per tier from the same measurements.
Validate each with the matching PyOpenColorIO library version
(`Config.validate()`, profile version check) and confirm the 2.5 and
2.4 outputs render identically for in-gamut test values.

## Measured response §road:measured-response

### Per-channel measured EOTF §road:measured-eotf-lut

Accept measured per-channel response ramps in `display_config.yaml` and
emit a fitted 1D LUT in place of the ideal EOTF curve, including
near-black (BT.1886-style) handling for gamma displays.
§spec:characterization-model. Depends on §road:display-colorspace-emit.

**Verify:** Provide a synthetic measured ramp deviating from pure gamma
2.4; confirm the generated config reproduces the measured response
within tolerance where an ideal-gamma config demonstrably does not,
and that near-black output is finite-sloped.

## Verification harness §road:verification-harness

### Probe patches and predictions §road:probe-patches

Generate probe patch imagery with predicted on-wall XYZ values for a
given generated config. §spec:verification. Depends on
§road:vp-radiometric-view.

### ΔE report §road:delta-e-report

Compare a measured-patch data file against predictions and produce a
ΔE report with pass/fail thresholds. §spec:verification. Depends on
§road:probe-patches.

### Measurement guide §road:measurement-guide

Write a measurement procedure guide (MEASUREMENT.md): processor
state lockdown and warm-up, generation patch set (primaries, white,
black, per-channel ramps, additivity check), validation patch set
through the deployed chain, and escalation criteria (1D shaper LUTs,
corrective 3D LUT) keyed to §spec:characterization-model and
§spec:verification. Depends on §road:delta-e-report.

**Verify:** Feed the harness a measurement file equal to its own
predictions and confirm ΔE ≈ 0 and a passing report against the
ΔE2000 ≤ 2 avg / ≤ 5 max and unity-exponent thresholds; perturb one
patch and confirm the report flags it. Confirm the measurement guide
walks a reader from patch generation to a passing report using only
shipped tooling.

## Closed-loop measurement §road:closed-loop-measurement

### Processor state snapshot §road:processor-state-snapshot

Add a CLI surface that snapshots a Brompton processor's state
read-only via the Tessera HTTP API, diffs it against the yaml's
recorded signal contract, and reports drift (including live input
metadata vs. declared wire format). §spec:measurement-loop.

### Session orchestrator §road:session-orchestrator

Add the one-command measurement session: contract audit gate, patch
drive via bmd-signal-gen's live color-update API on a DeckLink,
instrument reads behind a Colorimetry Research driver contract, and
emission of the measurements file consumed by the ΔE report.
§spec:measurement-loop. Depends on §road:probe-patches,
§road:delta-e-report, §road:processor-state-snapshot. Blocked in part
— sessions on SDI / 10-bit YCbCr links await wire-format validation
upstream in bmd-signal-gen (RGB 12-bit is validated today); unblocked
per format as bmd-signal-gen's spec records validation.

**Verify:** With a mock instrument driver that returns the session's
own predictions: run the session command end-to-end and confirm it
produces a measurements file and a passing ΔE report; perturb the
recorded signal contract (e.g., wrong gamma in yaml vs. live
processor) and confirm the session refuses to measure; declare a wire
format the live input metadata contradicts (e.g., 8-bit negotiated)
and confirm the session aborts with the mismatch named. With real
hardware (wall + CR-300): complete a full session and confirm the
report matches a manually measured spot-check patch.

## Documentation truth §road:documentation-truth

### Prune aspirational docs §road:prune-fiction-docs

Rewrite `GAMUT_MAPPING_GUIDE.md`, `COLOR_PIPELINE_DOCUMENTATION.md`,
and `README.md` to describe only implemented behavior, pointing
rendering rationale at SPEC.md. §spec:view-transform.

### Remove dead pipeline code §road:remove-dead-code

Delete the remaining exploratory scripts (`check_transforms.py`,
`discover_builtins.py`, `test_builtin_transforms.py`) or fold them into
real tests; the naive mapping helpers, legacy colorspace path, and
eotf-variants bug are already removed. §spec:config-structure.

**Verify:** `grep` finds no references to gamut-mapping strategies that
do not exist in generated output; tests and lint pass; README quick
start reproduces a working config verbatim.
