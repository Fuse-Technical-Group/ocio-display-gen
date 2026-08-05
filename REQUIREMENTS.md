# ocio-display-gen — Requirements

System requirements live in the
[color-wrangler umbrella](https://github.com/Fuse-Technical-Group/color-wrangler)
(problem statement, user stories, priorities, and system-wide
constraints). This file scopes them to the generate layer.

## Problem statement §req:problem-statement

This component exists so that a measured characterization becomes a
loadable OCIO config: media servers and render engines already speak
OpenColorIO, and the correction shall run in the renderer's
floating-point pipeline instead of the processor. The reference fleet
context (pre-Dynacal SDR-only front ends, closed vendor math) is
recorded in the umbrella.

## Success criteria §req:success-criteria

- The generated config loads in target OCIO runtimes (Disguise
  confirmed): the wall appears as a named display with selectable
  views, no runtime errors, no version incompatibility.
- Re-running on the same inputs reproduces the same config, byte for
  byte — the foundation of hash-based provenance.
- Every transform between scene-linear content and wire code values is
  readable in the generated config; nothing color-critical is
  delegated to closed processor modes.
- Generated predictions match reference computation (colour-science)
  within float tolerance; the physical loop is validated against the
  umbrella's ΔE thresholds.

## Quality attributes §req:quality-attributes

- **Fail loud:** implausible measurements, unsupported version
  combinations, or provenance mismatches stop generation with a clear
  message and nonzero exit; a silently wrong config is worse than no
  config.
- **Compatibility:** generated configs never exceed the target
  runtime's OCIO library version.
- **Expert-operated:** run by the color/engineering lead; clarity and
  correctness outrank hand-holding.

## Constraints §req:constraints

- Target runtimes load exactly one OCIO config and cannot merge at
  runtime; output is a single self-contained file appended to a
  standard ACES config.
- Inputs are the umbrella's artifact-chain files: human-authored show
  manifest, machine-written measurements artifact, promotion by hash.
  Humans never edit measured values.
- Radiometric claims require the explicit nits anchor recorded in the
  show manifest; above-peak handling is a recorded, selectable policy.
- No hardware I/O in this component — sessions (color-wrangler) own
  instruments and signal devices.

## User stories §req:user-stories

- As a color/engineering lead, I run the generator against a promoted
  measurements artifact and load the resulting config in Disguise — so
  scene-linear content renders correctly on the wall without touching
  processor color settings.
- As a lead handed a config of unknown vintage on a show machine, I
  trace it to the exact measurements and manifest that produced it,
  and prove whether those files are still the ones on disk.

## Priorities §req:priorities

Umbrella priorities govern. Within this component: documentation truth
first, then measured per-channel response, then version tiers —
matching ROADMAP.md order. Predictions (the verification handoff) have
shipped.
