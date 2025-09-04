#!/usr/bin/env python3
"""
Script to discover all available OCIO builtin transforms.
"""

from typing import List

import PyOpenColorIO as OCIO


def discover_builtin_transforms() -> List[str]:
    """Try to find what builtin transforms are actually available."""
    print("=== Discovering Available OCIO Builtin Transforms ===")
    print(f"OCIO Version: {OCIO.GetVersion()}")

    # Try to access the builtin transform registry if it exists
    try:
        # This might be available in newer OCIO versions
        registry = OCIO.BuiltinTransformRegistry()
        print("Found BuiltinTransformRegistry!")

        # Try to get the list of available transforms
        transforms = registry.getBuiltins()
        print(f"Available builtin transforms ({len(transforms)}):")
        for transform in transforms:
            print(f"  - {transform}")

    except AttributeError:
        print("BuiltinTransformRegistry not available in this OCIO version")

    # Try alternative methods
    try:
        # Check if OCIO has a method to list builtin transforms
        if hasattr(OCIO, "GetBuiltinTransforms"):
            builtin_transforms = OCIO.GetBuiltinTransforms()
            print(f"Available builtin transforms: {builtin_transforms}")
        else:
            print("No GetBuiltinTransforms method found")

    except Exception as e:
        print(f"Error getting builtin transforms: {e}")

    # Try some educated guesses based on OCIO documentation
    print("\nTesting educated guesses based on OCIO patterns:")

    # ACES builtin patterns
    aces_patterns = [
        "ACES-LMT - blue_light_artifact_fix",
        "UTILITY - ACES-AP0_to_CIE-XYZ-D65_BFD",
        "UTILITY - CIE-XYZ-D65_to_ACES-AP0_BFD",
        "UTILITY - ACES-AP1_to_CIE-XYZ-D65_BFD",
        "UTILITY - CIE-XYZ-D65_to_ACES-AP1_BFD",
        "CURVE - ACES-AP1_to_LINEAR",
        "CURVE - LINEAR_to_ACES-AP1",
    ]

    # Curve patterns
    curve_patterns = [
        "CURVE - LINEAR_to_ST-2084",
        "CURVE - ST-2084_to_LINEAR",
        "CURVE - LINEAR_to_BT-1886",
        "CURVE - BT-1886_to_LINEAR",
        "CURVE - LINEAR_to_sRGB",
        "CURVE - sRGB_to_LINEAR",
        "CURVE - LINEAR_to_REC709",
        "CURVE - REC709_to_LINEAR",
    ]

    all_patterns = aces_patterns + curve_patterns

    working_transforms: List[str] = []
    for pattern in all_patterns:
        try:
            _ = OCIO.BuiltinTransform(pattern)
            print(f"  ✓ {pattern}")
            working_transforms.append(pattern)
        except Exception:
            print(f"  ✗ {pattern}")

    print(f"\nSUMMARY: Found {len(working_transforms)} working builtin transforms:")
    for pattern in working_transforms:
        print(f"  - {pattern}")

    return working_transforms


if __name__ == "__main__":
    discover_builtin_transforms()
