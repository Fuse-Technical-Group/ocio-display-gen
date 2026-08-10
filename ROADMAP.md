# ocio-display-gen — Roadmap

Component roadmap for the generate layer of
[color-wrangler](https://github.com/Fuse-Technical-Group/color-wrangler).
Session and validation work live in the umbrella roadmap. The
verification handoff has shipped (§spec:verification); what remains is
model refinement, then runtime back-compat, then documentation
pruning, then the renderer-path probe artifact.

## Measured response §road:measured-response

### Per-channel measured EOTF §road:measured-eotf-lut

Accept measured per-channel response ramps in the measurements
artifact and emit a fitted 1D LUT in place of the ideal EOTF curve,
including near-black (BT.1886-style) handling for gamma displays.
§spec:characterization-model.

**Verify:** Provide a synthetic measured ramp deviating from pure gamma
2.4; confirm the generated config reproduces the measured response
within tolerance where an ideal-gamma config demonstrably does not,
and that near-black output is finite-sloped.

## Version tiers §road:version-tiers

### Target-runtime tier selection §road:tier-selection

Add a target OCIO runtime option to the show manifest that selects
base config and caps the emitted profile version (2.5 and 2.4 tiers).
§spec:version-targeting.

### Legacy ≤2.3 tier §road:legacy-23-tier

Emit the ≤2.3 fallback: nearest ACES 1.3 HDR output view plus the
display colorspace, with documented gamut-delta clipping.
§spec:version-targeting. Depends on §road:tier-selection.

**Verify:** Generate one config per tier from the same measurements.
Validate each with the matching PyOpenColorIO library version
(`Config.validate()`, profile version check) and confirm the 2.5 and
2.4 outputs render identically for in-gamut test values.

## Documentation truth §road:documentation-truth

### Prune aspirational docs §road:prune-fiction-docs

Rewrite `GAMUT_MAPPING_GUIDE.md`, `COLOR_PIPELINE_DOCUMENTATION.md`,
and `README.md` to describe only implemented behavior, pointing
rendering rationale at SPEC.md and system context at the umbrella.
§spec:view-transform.

### Remove dead pipeline code §road:remove-dead-code

Delete the remaining exploratory scripts (`check_transforms.py`,
`discover_builtins.py`, `test_builtin_transforms.py`) or fold them into
real tests; the naive mapping helpers, legacy colorspace path, and
eotf-variants bug are already removed. §spec:config-structure.

**Verify:** `grep` finds no references to gamut-mapping strategies that
do not exist in generated output; tests and lint pass; README quick
start reproduces a working config verbatim.

## Scene-linear probe imagery §road:scene-linear-probes

The drive-value PNGs verify the wall through a transparent player
(bmd-signal-gen over SDI); nothing yet exercises the deployed
renderer's application of the config. Scene-linear probe imagery is
this component's half of that loop: an artifact the renderer
interprets through the config, judged against the same predictions.
The session half lands in the umbrella roadmap
(`§road:renderer-verification` there).

### Spec the scene-linear probe artifact §road:spec-scene-linear-probes

Amend §spec:verification with the scene-linear probe artifact: one
floating-point EXR per patch in the config's scene reference space,
holding exactly the scene-linear content the predictions file records,
provenance-bound like the PNGs — and why the PNG's no-image-library
rationale does not transfer (this artifact exists to be interpreted by
the renderer, not to bypass interpretation).

### EXR probe writer §road:exr-probe-writer

Write the per-patch scene-linear EXRs beside the PNG probes at
generation time and record them in the predictions artifact and hash
chain (`OCIODisplayGen.py`). §spec:verification. Depends on
§road:spec-scene-linear-probes.

**Verify:** Generate a config; the probe directory holds one EXR per
patch; each EXR reads back the scene-linear values the predictions
file records for its patch; displaying an EXR through the generated
config's default (display, view) in an OCIO application reproduces
that patch's predicted code values.
