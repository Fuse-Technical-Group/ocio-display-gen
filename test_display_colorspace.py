#!/usr/bin/env python3
"""Tests for the display-referred wall colorspace builder."""

import colour
import numpy as np
import numpy.typing as npt
import PyOpenColorIO as OCIO
import pytest

from OCIODisplayGen import (
    D65_WHITE_XY,
    DisplayCharacterization,
    create_display_colorspace_from_characterization,
)

STUDIO_CONFIG_URI = "ocio://studio-config-v2.1.0_aces-v1.3_ocio-v2.3"
DISPLAY_REFERENCE = "CIE-XYZ-D65"

# Sample wall measurements from display_config.yaml
WALL_PRIMARIES = np.array([[0.680, 0.320], [0.265, 0.690], [0.150, 0.060]])
WALL_WHITEPOINT = (0.3127, 0.3290)
PEAK_LUMINANCE = 1000.0

# PQ code values (SMPTE ST 2084), absolute nits
PQ_100_NITS = 0.50808
PQ_1000_NITS = 0.75183


def make_characterization(
    eotf_type: str = "GAMMA",
    white_point: tuple[float, float] = WALL_WHITEPOINT,
) -> DisplayCharacterization:
    char = DisplayCharacterization("Test Wall")
    char.primaries = {
        "red": (0.680, 0.320),
        "green": (0.265, 0.690),
        "blue": (0.150, 0.060),
    }
    char.white_point = white_point
    char.black_level = 0.005
    char.peak_luminance = PEAK_LUMINANCE
    char.eotf_type = eotf_type
    char.gamma_value = 2.4
    return char


def wall_processor(char: DisplayCharacterization) -> OCIO.CPUProcessor:
    """Add the wall colorspace to the studio config and return a
    display-reference → wall CPU processor."""
    config = OCIO.Config.CreateFromFile(STUDIO_CONFIG_URI)
    cs = create_display_colorspace_from_characterization(char)
    config.addColorSpace(cs)
    proc = config.getProcessor(DISPLAY_REFERENCE, cs.getName())
    return proc.getDefaultCPUProcessor()


def d65_xyz(luminance_y: float) -> npt.NDArray[np.float64]:
    """D65 white XYZ at the given Y (units of 100 cd/m²)."""
    return np.asarray(
        colour.xy_to_XYZ(np.array(D65_WHITE_XY)) * luminance_y, dtype=np.float64
    )


def test_colorspace_is_display_referred() -> None:
    cs = create_display_colorspace_from_characterization(make_characterization())
    assert cs.getReferenceSpaceType() == OCIO.REFERENCE_SPACE_DISPLAY
    assert cs.getTransform(OCIO.COLORSPACE_DIR_FROM_REFERENCE) is not None


def test_gamma_peak_white_hits_full_code() -> None:
    cpu = wall_processor(make_characterization("GAMMA"))
    # 1000 nits D65 white = wall peak → code value 1.0 per channel
    out = cpu.applyRGB(list(d65_xyz(10.0)))
    assert np.allclose(out, [1.0, 1.0, 1.0], atol=1e-4)


def test_gamma_100_nits_white() -> None:
    cpu = wall_processor(make_characterization("GAMMA"))
    # 100 nits on a 1000-nit wall: linear 0.1 → 0.1**(1/2.4)
    expected = (100.0 / PEAK_LUMINANCE) ** (1.0 / 2.4)
    out = cpu.applyRGB(list(d65_xyz(1.0)))
    assert np.allclose(out, [expected] * 3, atol=1e-4)


def test_gamma_primaries_match_colour_science() -> None:
    char = make_characterization("GAMMA")
    cpu = wall_processor(char)

    wall_cs = colour.RGB_Colourspace(
        "Wall", WALL_PRIMARIES, np.array(WALL_WHITEPOINT), "Wall White"
    )
    wall_cs.use_derived_transformation_matrices()
    matrix = wall_cs.matrix_XYZ_to_RGB  # wall white is D65 → CAT is identity

    for xy in WALL_PRIMARIES:
        xyz = colour.xyY_to_XYZ(np.array([xy[0], xy[1], 1.0]))
        rgb = matrix @ xyz
        rgb = np.clip(rgb * (100.0 / PEAK_LUMINANCE), 0.0, 1.0)
        expected = rgb ** (1.0 / 2.4)
        out = np.array(cpu.applyRGB(list(xyz)))
        # Code-value comparison: float32 matrix residuals near zero are
        # amplified by the 1/2.4 exponent, so allow 2e-3 there and pin
        # accuracy with a tight linear-domain comparison below.
        assert np.allclose(out, expected, atol=2e-3), f"primary {xy}"
        assert np.allclose(out**2.4, rgb, atol=1e-5), f"primary {xy} (linear)"


def test_pq_absolute_encoding() -> None:
    cpu = wall_processor(make_characterization("PQ"))
    out = cpu.applyRGB(list(d65_xyz(1.0)))
    assert np.allclose(out, [PQ_100_NITS] * 3, atol=1e-4)
    out = cpu.applyRGB(list(d65_xyz(10.0)))
    assert np.allclose(out, [PQ_1000_NITS] * 3, atol=1e-4)


def test_pq_clips_at_measured_peak() -> None:
    cpu = wall_processor(make_characterization("PQ"))
    # 2000 nits input on a 1000-nit wall clips to the peak's code value
    out = cpu.applyRGB(list(d65_xyz(20.0)))
    assert np.allclose(out, [PQ_1000_NITS] * 3, atol=1e-4)


def test_hlg_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="HLG"):
        create_display_colorspace_from_characterization(make_characterization("HLG"))


def test_non_d65_wall_white_uses_cat02() -> None:
    wall_white = (0.3050, 0.3200)
    cs = create_display_colorspace_from_characterization(
        make_characterization("GAMMA", white_point=wall_white)
    )
    group = cs.getTransform(OCIO.COLORSPACE_DIR_FROM_REFERENCE)
    matrix_transform = group[0]
    assert isinstance(matrix_transform, OCIO.MatrixTransform)
    emitted = np.array(matrix_transform.getMatrix()).reshape(4, 4)

    wall_cs = colour.RGB_Colourspace(
        "Wall", WALL_PRIMARIES, np.array(wall_white), "Wall White"
    )
    wall_cs.use_derived_transformation_matrices()
    cat = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        colour.xy_to_XYZ(np.array(D65_WHITE_XY)),
        colour.xy_to_XYZ(np.array(wall_white)),
        transform="CAT02",
    )
    expected = wall_cs.matrix_XYZ_to_RGB @ cat

    assert np.allclose(emitted[:3, :3], expected, atol=1e-6)
    assert np.allclose(emitted[3], [0.0, 0.0, 0.0, 1.0])
    assert np.allclose(emitted[:3, 3], [0.0, 0.0, 0.0])
