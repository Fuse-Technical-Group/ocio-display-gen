#!/usr/bin/env python3
"""Tests for reference-space derivation from OCIO interchange roles."""

import colour
import numpy as np
import PyOpenColorIO as OCIO
import pytest

import OCIODisplayGen
from conftest import (
    STUDIO_CONFIG_URI,
    WALL_PRIMARIES,
    WALL_WHITEPOINT,
    make_characterization,
)
from OCIODisplayGen import (
    D65_WHITE_XY,
    create_display_xyz_to_native_matrix,
    derive_reference_spaces,
)


def test_studio_config_roles_resolve() -> None:
    config = OCIO.Config.CreateFromFile(STUDIO_CONFIG_URI)
    assert derive_reference_spaces(config) == ("ACES2065-1", "CIE-XYZ-D65")


def test_missing_interchange_roles_raise() -> None:
    config = OCIO.Config()
    with pytest.raises(ValueError, match="aces_interchange"):
        derive_reference_spaces(config)


def test_matrix_matches_colour_science_xyz_to_native() -> None:
    matrix_4x4 = create_display_xyz_to_native_matrix(make_characterization())

    wall = colour.RGB_Colourspace(
        "Wall", WALL_PRIMARIES, np.array(WALL_WHITEPOINT), "Wall White"
    )
    wall.use_derived_transformation_matrices()
    cat = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        colour.xy_to_XYZ(np.array(D65_WHITE_XY)),
        colour.xy_to_XYZ(np.array(WALL_WHITEPOINT)),
        transform="CAT02",
    )
    expected = wall.matrix_XYZ_to_RGB @ cat

    assert matrix_4x4.shape == (4, 4)
    assert np.allclose(matrix_4x4[:3, :3], expected, atol=1e-10)
    assert np.allclose(matrix_4x4[3], [0.0, 0.0, 0.0, 1.0])
    assert np.allclose(matrix_4x4[:3, 3], [0.0, 0.0, 0.0])


def test_no_scene_referred_hardcode_remains() -> None:
    # The scene-referred matrix path (ACEScg/AP1 hardcode, then the
    # RGB-to-RGB helper) is gone: the module derives from XYZ only.
    assert not hasattr(OCIODisplayGen, "get_reference_space_primaries")
    assert not hasattr(OCIODisplayGen, "create_reference_to_display_matrix")

    # The generated matrix is the XYZ-based computation — not an
    # RGB-to-RGB matrix from any scene reference space.
    matrix_4x4 = create_display_xyz_to_native_matrix(make_characterization())
    wall = colour.RGB_Colourspace(
        "Wall", WALL_PRIMARIES, np.array(WALL_WHITEPOINT), "Wall White"
    )
    wall.use_derived_transformation_matrices()
    for scene_space in ("ACEScg", "ACES2065-1"):
        rgb_to_rgb = colour.matrix_RGB_to_RGB(
            colour.RGB_COLOURSPACES[scene_space], wall
        )
        assert not np.allclose(matrix_4x4[:3, :3], rgb_to_rgb, atol=1e-3)
