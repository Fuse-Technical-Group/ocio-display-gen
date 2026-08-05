# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- `display_config.yaml` split into `decisions.yaml` (human decisions:
  show naming, intended signal contract, OCIO targeting, validation
  mode) and a machine-format measurements artifact
  (`measurements/ftg_stage1_20240115.yaml`, shipped as a hand-built
  sample), joined by a `measurements: {file, sha256}` promotion
  pointer. Generated output is byte-identical to the old input path;
  hash enforcement lands with §road:hash-binding.

### Added

- Hash binding (§spec:provenance): generation refuses when the
  measurements artifact on disk does not hash to the promotion
  pointer's recorded sha256 (or when the recorded digest is
  malformed), naming the artifact and both hashes. The generated
  config's top-level description records greppable `Provenance:` lines
  — decisions sha256, measurements sha256, and generator version — and
  the CLI prints them on success. Output stays byte-deterministic.
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
