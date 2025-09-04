#!/usr/bin/env python3
"""
Test script for the corrected color pipeline.
Validates the naive gamut mapping and tone mapping functions.
"""

import numpy as np

from OCIODisplayGen import (
    naive_gamut_map_preserve_luminance,
    naive_tone_map_preserve_chromaticity,
)


def test_naive_gamut_mapping():
    """Test naive gamut mapping function."""
    print("=== Testing Naive Gamut Mapping ===")

    # Test case 1: Out-of-gamut color with negative value
    test_rgb = np.array([1.5, -0.2, 2.0])
    result = naive_gamut_map_preserve_luminance(test_rgb)
    print(f"Input:  {test_rgb}")
    print(f"Output: {result}")
    luminance_orig = 0.2126 * test_rgb[0] + 0.7152 * test_rgb[1] + 0.0722 * test_rgb[2]
    luminance_result = 0.2126 * result[0] + 0.7152 * result[1] + 0.0722 * result[2]
    print(f"Luminance preserved: {luminance_orig:.3f} → {luminance_result:.3f}")
    print(f"All values ≥ 0: {np.all(result >= 0)}")
    print()

    # Test case 2: Normal in-gamut color
    test_rgb = np.array([0.8, 0.2, 0.1])
    result = naive_gamut_map_preserve_luminance(test_rgb)
    print(f"Input:  {test_rgb}")
    print(f"Output: {result}")
    print(f"Should be unchanged: {np.allclose(test_rgb, result)}")
    print()

    # Test case 3: Zero/negative luminance
    test_rgb = np.array([0.0, 0.0, 0.0])
    result = naive_gamut_map_preserve_luminance(test_rgb)
    print(f"Input:  {test_rgb}")
    print(f"Output: {result}")
    print()


def test_naive_tone_mapping():
    """Test naive tone mapping function."""
    print("=== Testing Naive Tone Mapping ===")

    # Test case 1: HDR content exceeding 1.0
    test_rgb = np.array([2.5, 2.3, 2.8])
    result = naive_tone_map_preserve_chromaticity(test_rgb, max_luminance=1.0)
    print(f"Input:  {test_rgb}")
    print(f"Output: {result}")
    print(f"Max value ≤ 1.0: {np.max(result) <= 1.0}")
    ratio_orig = test_rgb / np.max(test_rgb)
    ratio_result = result / np.max(result)
    print(f"Chromaticity preserved (ratios): {ratio_orig} → {ratio_result}")
    print()

    # Test case 2: Normal SDR content
    test_rgb = np.array([0.8, 0.2, 0.1])
    result = naive_tone_map_preserve_chromaticity(test_rgb, max_luminance=1.0)
    print(f"Input:  {test_rgb}")
    print(f"Output: {result}")
    print(f"Should be unchanged: {np.allclose(test_rgb, result)}")
    print()

    # Test case 3: Bright white
    test_rgb = np.array([3.0, 3.0, 3.0])
    result = naive_tone_map_preserve_chromaticity(test_rgb, max_luminance=1.0)
    print(f"Input:  {test_rgb}")
    print(f"Output: {result}")
    print(f"Should be [1,1,1]: {np.allclose(result, [1.0, 1.0, 1.0])}")
    print()


def test_full_pipeline():
    """Test the complete pipeline with challenging input."""
    print("=== Testing Full Pipeline ===")

    # Challenging input: bright impossible cyan in ACEScg-like space
    test_rgb = np.array([1.2, -0.1, 2.5])
    print(f"Input (Reference RGB): {test_rgb}")

    # Note: Matrix transform would happen in OCIO, we're testing the post-matrix stages
    print("After matrix transform (simulated impossible display RGB):")
    matrix_output = np.array([1.5, -0.2, 2.0])  # Simulated result
    print(f"Matrix output: {matrix_output}")

    # Stage 2: Gamut mapping
    gamut_mapped = naive_gamut_map_preserve_luminance(matrix_output)
    print(f"After gamut mapping: {gamut_mapped}")

    # Stage 3: Tone mapping
    tone_mapped = naive_tone_map_preserve_chromaticity(gamut_mapped, max_luminance=1.0)
    print(f"After tone mapping: {tone_mapped}")

    # Stage 4: OETF would happen in OCIO (PQ/HLG/Gamma)
    print("Final values ready for OETF application in OCIO")

    # Validate final output
    valid_min = np.all(tone_mapped >= 0)
    valid_max = np.all(tone_mapped <= 1.0)
    print(f"Valid for display: all ≥ 0: {valid_min}, all ≤ 1: {valid_max}")


if __name__ == "__main__":
    test_naive_gamut_mapping()
    test_naive_tone_mapping()
    test_full_pipeline()
