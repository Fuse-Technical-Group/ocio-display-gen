#!/usr/bin/env python3
"""Tests for reference-space derivation from OCIO interchange roles."""

import colour
import numpy as np
import PyOpenColorIO as OCIO
import pytest

from OCIODisplayGen import create_reference_to_display_matrix, derive_reference_spaces

STUDIO_CONFIG_URI = "ocio://studio-config-v2.1.0_aces-v1.3_ocio-v2.3"

# Sample wall measurements from display_config.yaml
WALL_PRIMARIES = np.array([[0.680, 0.320], [0.265, 0.690], [0.150, 0.060]])
WALL_WHITEPOINT = np.array([0.3127, 0.3290])


def test_studio_config_roles_resolve() -> None:
    config = OCIO.Config.CreateFromFile(STUDIO_CONFIG_URI)
    assert derive_reference_spaces(config) == ("ACES2065-1", "CIE-XYZ-D65")


def test_missing_interchange_roles_raise() -> None:
    config = OCIO.Config()
    with pytest.raises(ValueError, match="aces_interchange"):
        derive_reference_spaces(config)


def test_matrix_matches_colour_science_aces2065_1() -> None:
    config = OCIO.Config.CreateFromFile(STUDIO_CONFIG_URI)
    scene_reference, _ = derive_reference_spaces(config)

    reference = colour.RGB_COLOURSPACES[scene_reference]
    matrix_4x4 = create_reference_to_display_matrix(
        WALL_PRIMARIES,
        WALL_WHITEPOINT,
        np.reshape(reference.primaries, (3, 2)),
        np.asarray(reference.whitepoint),
    )

    display = colour.RGB_Colourspace(
        "Display", WALL_PRIMARIES, WALL_WHITEPOINT, "Display Space"
    )
    expected = colour.matrix_RGB_to_RGB(reference, display)

    assert matrix_4x4.shape == (4, 4)
    assert np.allclose(matrix_4x4[:3, :3], expected, atol=1e-5)
    assert np.allclose(matrix_4x4[3], [0.0, 0.0, 0.0, 1.0])
    assert np.allclose(matrix_4x4[:3, 3], [0.0, 0.0, 0.0])

    # Must differ from the old hardcoded ACEScg (AP1) matrix
    acescg = colour.RGB_COLOURSPACES["ACEScg"]
    old_matrix = colour.matrix_RGB_to_RGB(acescg, display)
    assert not np.allclose(matrix_4x4[:3, :3], old_matrix, atol=1e-3)


def test_reference_parameters_are_required() -> None:
    with pytest.raises(TypeError):
        create_reference_to_display_matrix(  # type: ignore[call-arg]
            WALL_PRIMARIES, WALL_WHITEPOINT
        )
