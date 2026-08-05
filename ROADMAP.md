# ocio-display-gen — Roadmap

Sections run the walking skeleton to closed-loop measurement first:
the harness makes accuracy checkable, and sessions make it
measurable. Runtime back-compat (version tiers) and model refinement
(measured response) follow once the loop exists to judge them.

## Verification harness §road:verification-harness

### Probe patches and predictions §road:probe-patches

Generate probe patch imagery with predicted on-wall XYZ values for a
given generated config. §spec:verification.

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

Session workstreams in this section migrate to the sibling session
tool's governance at its repo creation (§spec:measurement-loop
ownership split); prediction and report tooling stay here. The
sibling is named **Stilb** (decided 2026-08-04; PyPI name confirmed
available).

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

### Characterize mode §road:characterize-session

Add the `characterize` session mode: fixed device-referred patch
protocol driven raw (no OCIO), emitting the immutable measurements
artifact with embedded processor snapshot, instrument identity, and
ambient floor. §spec:measurement-loop,
§spec:characterization-model. Depends on §road:session-orchestrator.

**Verify:** With a mock instrument driver that returns the session's
own predictions: run the session command end-to-end and confirm it
produces a measurements file and a passing ΔE report; perturb the
recorded signal contract (e.g., wrong gamma in yaml vs. live
processor) and confirm the session refuses to measure; declare a wire
format the live input metadata contradicts (e.g., 8-bit negotiated)
and confirm the session aborts with the mismatch named. Characterize
mode with the virtual instrument: confirm the emitted measurements
artifact carries measurements, processor snapshot, instrument
identity, ambient floor, and timestamps, and that promoting it then
generating a config succeeds end-to-end. With real hardware
(wall + CR-300): complete a full characterize → promote → generate →
verify cycle and confirm the report matches a manually measured
spot-check patch.

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
rendering rationale at SPEC.md. §spec:view-transform.

### Remove dead pipeline code §road:remove-dead-code

Delete the remaining exploratory scripts (`check_transforms.py`,
`discover_builtins.py`, `test_builtin_transforms.py`) or fold them into
real tests; the naive mapping helpers, legacy colorspace path, and
eotf-variants bug are already removed. §spec:config-structure.

**Verify:** `grep` finds no references to gamut-mapping strategies that
do not exist in generated output; tests and lint pass; README quick
start reproduces a working config verbatim.
