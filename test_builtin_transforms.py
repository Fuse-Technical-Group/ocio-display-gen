#!/usr/bin/env python3
"""
Test script to discover what builtin transforms are actually available in OCIO.
"""

import PyOpenColorIO as OCIO


def test_builtin_transforms():
    """Test what builtin transforms actually exist in OCIO."""
    print("=== Testing OCIO Builtin Transforms ===")

    # List of transform names we're currently trying to use
    test_transforms = [
        "GAMUT-MAP - PERCEPTUAL",
        "GAMUT-MAP - SATURATION",
        "GAMUT-MAP - RELATIVE",
        "GAMUT-MAP - ABSOLUTE",
        "GAMUT-MAP - SOFT-CLIP",
        "GAMUT-MAP - ADAPTIVE",
        "GAMUT-MAP - HUE-PRESERVING",
        "CURVE - LINEAR_to_ST-2084",
        "CURVE - LINEAR_to_HLG",
        "CURVE - LINEAR_to_GAMMA2.4",
    ]

    print("Testing transforms we're currently trying to use:")
    for transform_name in test_transforms:
        try:
            OCIO.BuiltinTransform(transform_name)
            print(f"  ✓ {transform_name} - EXISTS")
        except Exception:
            print(f"  ✗ {transform_name} - DOES NOT EXIST")

    print("\nTrying to discover available builtin transforms...")

    # Try some common builtin transform patterns
    common_patterns = [
        "ACES-OUTPUT - sRGB",
        "ACES-OUTPUT - Rec.709",
        "ACES-LMT - blue_light_artifact_fix",
        "DISPLAY - sRGB",
        "UTILITY - ACES-AP0_to_CIE-XYZ-D65_BFD",
        "RRT",
        "ODT.Academy.sRGB_100nits_dim.a1.0.3",
        "LMT.Academy.ACES_0_1_1.a1.0.3",
    ]

    print("\nTesting some common ACES builtin transforms:")
    for transform_name in common_patterns:
        try:
            OCIO.BuiltinTransform(transform_name)
            print(f"  ✓ {transform_name} - EXISTS")
        except Exception:
            print(f"  ✗ {transform_name} - DOES NOT EXIST")

    # Try to get list of available builtin transforms
    try:
        # Check if there's a way to list available builtin transforms
        print(f"\nOCIO Version: {OCIO.GetVersion()}")

        # Try to get registry information
        print("\nAttempting to discover builtin transform registry...")
        # This might not work in all OCIO versions

    except Exception as e:
        print(f"Could not get builtin transform info: {e}")


if __name__ == "__main__":
    test_builtin_transforms()
