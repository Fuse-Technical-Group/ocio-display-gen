"""The `ocio-display-gen` command line.

One caller of `ocio_display_gen.generate`. Everything here is about
talking to an operator — progress, formatting, exit codes — and none of
it is about generating a config, which is why it is not in the library.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from ocio_display_gen._core import (
    ACES2_VIEW,
    SHOW_MANIFEST_FILE,
    VP_RADIOMETRIC_VIEW,
    check_predictions,
    describe_processing_state,
    provenance_description,
)
from ocio_display_gen.api import generate


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Command line surface: generate by default, or inspect a
    predictions artifact."""
    parser = argparse.ArgumentParser(
        prog="ocio-display-gen",
        description=(
            f"Generate an OCIO config for a measured display from "
            f"a show manifest (default '{SHOW_MANIFEST_FILE}') and the "
            f"measurements artifact it "
            f"promotes, plus the verification predictions and probe "
            f"imagery a color-wrangler session measures against."
        ),
    )
    parser.add_argument(
        "--check-predictions",
        metavar="FILE",
        help=(
            "Report what a predictions artifact describes and verify it "
            "still matches the config it names, then exit."
        ),
    )
    parser.add_argument(
        "--manifest",
        metavar="PATH",
        help=(
            f"Show manifest to generate from. Defaults to "
            f"'{SHOW_MANIFEST_FILE}' in the working directory."
        ),
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        help=(
            "Directory for the config, predictions and probe imagery. "
            "Defaults to the manifest's own directory."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Generate, or inspect a predictions artifact. Returns an exit code."""
    args = parse_args(argv)
    if args.check_predictions:
        try:
            check_predictions(args.check_predictions)
        except ValueError as e:
            print(f"\u274c Error: {e}")
            return 1
        return 0

    manifest_path = args.manifest or SHOW_MANIFEST_FILE
    print("=== OCIO Display Generator ===")
    print(f"Loading manifest from '{manifest_path}'...")
    try:
        result = generate(manifest_path, output_dir=args.output_dir)
    except (ValueError, RuntimeError) as e:
        print(f"\u274c Error: {e}")
        return 1

    characterization = result.characterization
    print(
        f"\u2713 Loaded measurements artifact "
        f"'{result.provenance.measurements_file}' (promotion hash verified)"
    )
    print(f"\nDisplay: {characterization.name}")
    print(f"Peak luminance: {characterization.peak_luminance} cd/m\u00b2")
    print(f"Black level: {characterization.black_level} cd/m\u00b2")
    print(f"Contrast ratio: {characterization.contrast_ratio:.0f}:1")
    print(f"EOTF: {characterization.eotf_type}")
    intensity = characterization.processor_intensity
    print(
        f"Processor intensity: "
        f"{intensity if intensity is not None else '(not recorded)'}"
    )
    print(
        f"Color processing / dynamic features: "
        f"{describe_processing_state(characterization.processor_processing_disabled)}"
    )
    print(f"White point policy: {characterization.white_point_policy}")
    print(f"Scene reference space: {result.scene_reference}")
    print(f"Display reference space: {result.display_reference}")
    print(f"VP Radiometric nits anchor: {result.nits_anchor} cd/m\u00b2")
    print(f"VP Radiometric overflow policy: {result.overflow_policy}")

    print("\n\u2705 Successfully created OCIO config!")
    print(f"   Output file: {result.config_path}")
    print(f"   Predictions: {result.predictions_path}")
    print(
        f"   Probe imagery: {result.probe_directory}/ "
        f"({len(result.probe_files)} files: PNG+EXR per patch)"
    )
    print("\nProvenance recorded in the config description:")
    for line in provenance_description(result.provenance).splitlines():
        print(f"   {line}")
    print(f"\nRegistered display: {result.display_name}")
    for view in result.views:
        marker = " (default)" if view == result.default_view else ""
        print(f"   View: {view}{marker}")
    print(
        f"   {VP_RADIOMETRIC_VIEW}: anchor {result.nits_anchor} cd/m\u00b2, "
        f"overflow policy {result.overflow_policy}"
    )
    print(
        f"   {ACES2_VIEW}: output transform limited to measured "
        f"peak {characterization.peak_luminance} cd/m\u00b2 and "
        f"measured primaries/white"
    )
    print("\n\U0001f4cb Usage Instructions:")
    print(
        f"1. Set OCIO environment variable: export OCIO="
        f"{os.path.abspath(result.config_path)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
