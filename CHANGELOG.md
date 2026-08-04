# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

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
