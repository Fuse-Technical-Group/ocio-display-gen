#!/usr/bin/env python3
"""
Check available transform types in OCIO.
"""

import PyOpenColorIO as OCIO


def check_transforms():
    """Check what transform types are available."""
    print("=== Available OCIO Transform Types ===")

    # Check common transform types
    transforms = [
        "ExponentTransform",
        "ExponentWithLinearTransform",
        "GammaTransform",
        "MatrixTransform",
        "BuiltinTransform",
        "ColorSpaceTransform",
        "FileTransform",
    ]

    for transform_name in transforms:
        try:
            transform_class = getattr(OCIO, transform_name)
            print(f"  ✓ {transform_name} - Available")

            # Try to create an instance
            try:
                instance = transform_class()
                print("    └─ Can instantiate")

                # Check methods for gamma-like transforms
                if hasattr(instance, "setValue"):
                    print("    └─ Has setValue() method")
                if hasattr(instance, "setGamma"):
                    print("    └─ Has setGamma() method")

            except Exception as e:
                print(f"    └─ Cannot instantiate: {e}")

        except AttributeError:
            print(f"  ✗ {transform_name} - Not available")


if __name__ == "__main__":
    check_transforms()
