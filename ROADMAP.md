# ocio-display-gen — Roadmap

## Colorimetric foundation §road:colorimetric-foundation

### Derive reference spaces from base config §road:derive-reference-spaces

Replace the hardcoded ACEScg assumption in `OCIODisplayGen.py` with
scene/display reference spaces derived from the base config's
interchange roles. §spec:config-structure

### Emit display colorspace §road:display-colorspace-emit

Generate the wall as a display colorspace (`from_display_reference`:
XYZ→native matrix, absolute luminance scale, inverse processor EOTF) in
`OCIODisplayGen.py`, replacing the scene-referred colorspace path.
§spec:config-structure. Depends on §road:derive-reference-spaces.

### White point policy option §road:white-point-policy

Add an explicit `adapted`/`absolute` white point policy to
`display_config.yaml` and wire it through matrix generation.
§spec:white-point. Depends on §road:display-colorspace-emit.

### Signal contract metadata §road:signal-contract-metadata

Record the required processor state (EOTF, intensity, disabled
processing) in the generated config's descriptions from
`display_config.yaml` fields. §spec:signal-contract.

### Register display and colorimetric view §road:colorimetric-view-surface

Register the wall as a named display with a colorimetric
(no-rendering) view via `addDisplayView` and write the output config.
§spec:config-structure. Depends on §road:display-colorspace-emit.

**Verify:** Run `uv run ./OCIODisplayGen.py` with the sample yaml. Load
the output config in Python (`ocio://`-free), list displays/views, and
confirm the wall appears with its colorimetric view. Push D65 white and
the measured primaries through the processor for that view and confirm
code values match colour-science reference computation (matrix, CAT
policy, inverse EOTF) within float tolerance; confirm the old
ACEScg-matrix path is gone.

## ACES 2.0 rendering §road:aces2-rendering

### ACES 2.0 base config selection §road:aces2-base-config

Extend `display_config.yaml` base-config selection to the ACES 2.0 /
OCIO 2.5 studio config URI scheme. §spec:version-targeting.

### Parameterized output transform view §road:aces2-view-transform

Add a view transform using the ACES 2.0 output transform parameterized
by measured peak luminance and native primaries, registered as the
wall's default view. §spec:view-transform. Depends on
§road:colorimetric-view-surface and §road:aces2-base-config.

**Verify:** Generate the config, apply the default view to a
scene-linear exposure ramp and a hue sweep exceeding the wall gamut.
Confirm highlights roll off rather than clip, out-of-gamut hues
compress without hue skew, and diffuse white (1.0) lands at the
ACES-2.0-predicted output level for the measured peak nits.

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
§road:aces2-view-transform.

### ΔE report §road:delta-e-report

Compare a measured-patch data file against predictions and produce a
ΔE report with pass/fail thresholds. §spec:verification. Depends on
§road:probe-patches.

**Verify:** Feed the harness a measurement file equal to its own
predictions and confirm ΔE ≈ 0 and a passing report; perturb one patch
and confirm the report flags it.

## Documentation truth §road:documentation-truth

### Prune aspirational docs §road:prune-fiction-docs

Rewrite `GAMUT_MAPPING_GUIDE.md`, `COLOR_PIPELINE_DOCUMENTATION.md`,
and `README.md` to describe only implemented behavior, pointing
rendering rationale at SPEC.md. §spec:view-transform.

### Remove dead pipeline code §road:remove-dead-code

Delete the unused naive gamut/tone mapping functions, the legacy
colorspace path, and the eotf-variants bug in `OCIODisplayGen.py`.
§spec:config-structure.

**Verify:** `grep` finds no references to gamut-mapping strategies that
do not exist in generated output; tests and lint pass; README quick
start reproduces a working config verbatim.
