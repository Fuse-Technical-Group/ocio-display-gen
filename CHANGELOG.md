## [0.2.1](https://github.com/Fuse-Technical-Group/ocio-display-gen/compare/v0.2.0...v0.2.1) (2026-08-11)


### Bug Fixes

* **rendering:** quantize the fixed-function peak; keep the measured value ([#22](https://github.com/Fuse-Technical-Group/ocio-display-gen/issues/22)) ([adb80f9](https://github.com/Fuse-Technical-Group/ocio-display-gen/commit/adb80f9c17f188d2861700d703b771308f70ce0a))

# [0.2.0](https://github.com/Fuse-Technical-Group/ocio-display-gen/compare/v0.1.0...v0.2.0) (2026-08-11)


### Bug Fixes

* **probes:** hold the audited EXR digest to the artifact string invariant ([2723db5](https://github.com/Fuse-Technical-Group/ocio-display-gen/commit/2723db5a4c76d7bf9ef4deb15a80490fb2db5548))


### Features

* **probes:** audit probe EXR config bindings in check-predictions ([d999a03](https://github.com/Fuse-Technical-Group/ocio-display-gen/commit/d999a03e61b697d70221c213235ed60ed5fdcc40))
* **probes:** emit scene-linear EXR probes beside the PNG records ([bde0412](https://github.com/Fuse-Technical-Group/ocio-display-gen/commit/bde0412283e8bfdac3031f934dee4c093d5650ef))

# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Scene-linear EXR probe imagery beside the PNG records: one float32
  EXR per patch in the config's scene reference space, byte
  deterministic, self-describing via `sceneReference`/`configSha256`
  header attributes; `--check-predictions` audits the EXR bindings.

### Changed

- Governance demoted to component scope: system-level requirements,
  architecture, sessions, and verification policy migrated to the
  [color-wrangler umbrella](https://github.com/Fuse-Technical-Group/color-wrangler);
  SPEC/REQUIREMENTS/ROADMAP here now cover the generate layer only.

- `display_config.yaml` split into `decisions.yaml` (human decisions:
  show naming, intended signal contract, OCIO targeting, validation
  mode) and a machine-format measurements artifact
  (`measurements/ftg_stage1_20240115.yaml`, shipped as a hand-built
  sample), joined by a `measurements: {file, sha256}` promotion
  pointer. Emitted transforms are unchanged by the split.

### Added

- Verification handoff (§spec:verification): generation writes a
  predictions artifact and probe patch imagery beside the config. For
  each of 29 probe patches — a neutral ramp plus chromatic axes at full
  drive, half drive, and half saturation — the artifact records the
  scene-linear content that produces it, the code values the config
  emits, and the CIE XYZ the wall is predicted to emit in cd/m².
  Predictions come from the config's own transforms and target the
  default VP Radiometric view. The artifact is bound to its config by
  sha256 and re-emits byte for byte after parsing. Probe imagery is one
  solid-color 16-bit PNG per patch, holding exactly the code values the
  predictions were computed from.

- `--check-predictions FILE` reports what a predictions artifact
  describes and exits nonzero when the config it names is no longer the
  config on disk. A parsed artifact is treated as untrusted input, on
  the same terms as the promotion pointer: the config name and patch
  ids must be bare filenames beside the artifact, the recorded digest
  must be a well-formed sha256, and no field may carry control
  characters.

- Hash binding (§spec:provenance): generation refuses when the
  measurements artifact on disk does not hash to the promotion
  pointer's recorded sha256 (or when the recorded digest is
  malformed), naming the artifact and both hashes. The generated
  config's top-level description records greppable `Provenance:` lines
  — the show description, decisions sha256, measurements sha256, and
  generator version — and the CLI prints them on success. Output stays
  byte-deterministic. Pointer inputs are hardened: relative paths only
  (no absolute paths or `..`), no control characters, and the recorded
  digest must be a quoted string.
- VP Radiometric view (default): configurable nits anchor
  (`ocio.vp_radiometric.nits_anchor`), ACES 2.0 gamut compression at the
  wall-gamut boundary, and a selectable above-peak overflow policy
  (`clamp` or `shoulder`), all recorded in the view transform
  description.
- ACES 2.0 view: the full ACES 2.0 output transform parameterized by
  the wall's measured peak luminance and native primaries, for
  finished-content contexts.
- ACES 2.0 / OCIO 2.5 studio base config selection
  (`ocio://studio-config-v4.0.0_aces-v2.0_ocio-v2.5`), now the sample
  default; the ACES 1.3 base remains selectable.

- Explicit `adapted`/`absolute` white point policy, selected via
  `ocio.white_point_policy` in `display_config.yaml` and recorded in the
  generated colorspace description.
- Signal-contract metadata: processor state (`intensity`,
  `processing_disabled`) recorded in `display_config.yaml` and emitted in
  the generated config's descriptions; strict mode fails when the fields
  are missing.
- SPEC.md and ROADMAP.md governance documents defining the display
  colorspace / view transform architecture and OCIO version tiering.

### Fixed

- Active-display list corrupted by PyOpenColorIO 2.5.1's iterator
  return from `getActiveDisplays()`; the generated config now joins the
  list as strings and validates again.
- Sample config identity corrected to the bench hardware (Brompton S8,
  ROE Black Pearl 2 (NS)).

### Removed

- Dead `viewing_conditions` plumbing on `DisplayCharacterization`.
