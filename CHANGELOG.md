# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

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

- Sample config identity corrected to the bench hardware (Brompton S8,
  ROE Black Pearl 2 (NS)).

### Removed

- Dead `viewing_conditions` plumbing on `DisplayCharacterization`.
