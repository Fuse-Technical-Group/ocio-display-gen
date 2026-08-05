# ocio-display-gen — Roadmap

Component roadmap for the generate layer of
[color-wrangler](https://github.com/Fuse-Technical-Group/color-wrangler).
Session and validation work live in the umbrella roadmap. The
verification handoff has shipped (§spec:verification); what remains is
model refinement, then runtime back-compat, then documentation pruning.

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
