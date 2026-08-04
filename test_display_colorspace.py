#!/usr/bin/env python3
"""Tests for the display-referred wall colorspace builder."""

from pathlib import Path

import colour
import numpy as np
import PyOpenColorIO as OCIO
import pytest

from conftest import (
    GAMMA,
    PEAK_LUMINANCE,
    STUDIO_CONFIG_URI,
    WALL_PRIMARIES,
    WALL_WHITEPOINT,
    d65_xyz,
    make_characterization,
)
from OCIODisplayGen import (
    D65_WHITE_XY,
    DISPLAY_REFERENCE,
    DisplayCharacterization,
    create_characterization_from_config,
    create_display_colorspace_from_characterization,
    create_display_xyz_to_native_matrix,
    load_config_from_yaml,
    validate_config_data,
)

# PQ code values (SMPTE ST 2084), absolute nits
PQ_100_NITS = 0.50808
PQ_1000_NITS = 0.75183


def wall_processor(char: DisplayCharacterization) -> OCIO.CPUProcessor:
    """Add the wall colorspace to the studio config and return a
    display-reference → wall CPU processor."""
    config = OCIO.Config.CreateFromFile(STUDIO_CONFIG_URI)
    cs = create_display_colorspace_from_characterization(char)
    config.addColorSpace(cs)
    proc = config.getProcessor(DISPLAY_REFERENCE, cs.getName())
    return proc.getDefaultCPUProcessor()


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
    expected = (100.0 / PEAK_LUMINANCE) ** (1.0 / GAMMA)
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
        expected = rgb ** (1.0 / GAMMA)
        out = np.array(cpu.applyRGB(list(xyz)))
        # Code-value comparison: float32 matrix residuals near zero are
        # amplified by the 1/2.4 exponent, so allow 2e-3 there and pin
        # accuracy with a tight linear-domain comparison below.
        assert np.allclose(out, expected, atol=2e-3), f"primary {xy}"
        assert np.allclose(out**GAMMA, rgb, atol=1e-5), f"primary {xy} (linear)"


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


# White point policy (§spec:white-point). The sample wall white IS D65,
# where adapted and absolute coincide, so these use a non-D65 wall white.
NON_D65_WALL_WHITE = (0.3050, 0.3200)


def non_d65_wall_space() -> colour.RGB_Colourspace:
    wall_cs = colour.RGB_Colourspace(
        "Wall", WALL_PRIMARIES, np.array(NON_D65_WALL_WHITE), "Wall White"
    )
    wall_cs.use_derived_transformation_matrices()
    return wall_cs


def test_non_d65_wall_white_uses_cat02() -> None:
    cs = create_display_colorspace_from_characterization(
        make_characterization("GAMMA", white_point=NON_D65_WALL_WHITE)
    )
    group = cs.getTransform(OCIO.COLORSPACE_DIR_FROM_REFERENCE)
    matrix_transform = group[0]
    assert isinstance(matrix_transform, OCIO.MatrixTransform)
    emitted = np.array(matrix_transform.getMatrix()).reshape(4, 4)

    cat = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        colour.xy_to_XYZ(np.array(D65_WHITE_XY)),
        colour.xy_to_XYZ(np.array(NON_D65_WALL_WHITE)),
        transform="CAT02",
    )
    expected = non_d65_wall_space().matrix_XYZ_to_RGB @ cat

    assert np.allclose(emitted[:3, :3], expected, atol=1e-6)
    assert np.allclose(emitted[3], [0.0, 0.0, 0.0, 1.0])
    assert np.allclose(emitted[:3, 3], [0.0, 0.0, 0.0])


def test_absolute_policy_matrix_has_no_cat() -> None:
    matrix = create_display_xyz_to_native_matrix(
        make_characterization(
            "GAMMA", white_point=NON_D65_WALL_WHITE, white_point_policy="absolute"
        )
    )
    expected = non_d65_wall_space().matrix_XYZ_to_RGB
    assert np.allclose(matrix[:3, :3], expected, atol=1e-6)


def test_absolute_policy_reproduces_d65_chromaticity() -> None:
    matrix = create_display_xyz_to_native_matrix(
        make_characterization(
            "GAMMA", white_point=NON_D65_WALL_WHITE, white_point_policy="absolute"
        )
    )
    rgb = matrix[:3, :3] @ colour.xy_to_XYZ(np.array(D65_WHITE_XY))
    # No adaptation: D65 white lands off the wall's neutral axis.
    assert not np.allclose(rgb, [rgb[0]] * 3, atol=1e-4)
    # Colorimetrically exact: back through the wall's RGB→XYZ, the
    # chromaticity is still D65.
    xy = colour.XYZ_to_xy(non_d65_wall_space().matrix_RGB_to_XYZ @ rgb)
    assert np.allclose(xy, D65_WHITE_XY, atol=1e-6)


def test_invalid_white_point_policy_raises() -> None:
    with pytest.raises(ValueError, match="adapted"):
        create_display_xyz_to_native_matrix(
            make_characterization(white_point_policy="perceptual")
        )


def test_description_records_adapted_policy() -> None:
    cs = create_display_colorspace_from_characterization(make_characterization())
    assert "White point policy: adapted" in cs.getDescription()


def test_description_records_absolute_policy() -> None:
    cs = create_display_colorspace_from_characterization(
        make_characterization(white_point_policy="absolute")
    )
    assert "White point policy: absolute (no chromatic adaptation)" in (
        cs.getDescription()
    )


def sample_yaml_config() -> dict:
    """Fresh parse of the sample yaml — each call returns a new object."""
    return load_config_from_yaml(str(Path(__file__).parent / "display_config.yaml"))


def test_yaml_white_point_policy_parsed() -> None:
    config = sample_yaml_config()
    config["ocio"]["white_point_policy"] = "absolute"
    char = create_characterization_from_config(config)
    assert char.white_point_policy == "absolute"


def test_yaml_white_point_policy_defaults_to_adapted() -> None:
    config = sample_yaml_config()
    config["ocio"].pop("white_point_policy", None)
    char = create_characterization_from_config(config)
    assert char.white_point_policy == "adapted"


# Signal contract (§spec:signal-contract): the description records the
# processor state the config is valid for — EOTF, locked intensity,
# processing/dynamic features disabled.


def test_description_records_signal_contract_gamma() -> None:
    cs = create_display_colorspace_from_characterization(
        make_characterization(intensity="100%", processing_disabled=True)
    )
    desc = cs.getDescription()
    assert "Signal contract" in desc
    assert "EOTF GAMMA 2.4" in desc
    assert "intensity 100%" in desc
    assert "color processing and dynamic features disabled" in desc


def test_description_signal_contract_pq_has_no_gamma_value() -> None:
    cs = create_display_colorspace_from_characterization(
        make_characterization("PQ", intensity="100%", processing_disabled=True)
    )
    desc = cs.getDescription()
    assert "EOTF PQ" in desc
    assert "GAMMA" not in desc
    assert "2.4" not in desc


def test_description_records_processing_not_disabled() -> None:
    cs = create_display_colorspace_from_characterization(
        make_characterization(intensity="100%", processing_disabled=False)
    )
    assert "color processing and dynamic features NOT disabled" in cs.getDescription()


def test_description_omits_intensity_when_absent() -> None:
    cs = create_display_colorspace_from_characterization(
        make_characterization(processing_disabled=True)
    )
    desc = cs.getDescription()
    assert "Signal contract" in desc
    assert "intensity" not in desc


def test_yaml_processor_state_parsed() -> None:
    char = create_characterization_from_config(sample_yaml_config())
    assert char.processor_intensity == "100%"
    assert char.processor_processing_disabled is True


def test_yaml_processor_state_absent_is_none() -> None:
    config = sample_yaml_config()
    processor_conf = config["display"]["led_processor"]["configuration"]
    processor_conf.pop("intensity", None)
    processor_conf.pop("processing_disabled", None)
    char = create_characterization_from_config(config)
    assert char.processor_intensity is None
    assert char.processor_processing_disabled is None


# Validation (§spec:signal-contract): missing processor-state fields fail
# loud in strict mode, warn otherwise.


def config_without_processor_state(strict_mode: bool) -> dict:
    config = sample_yaml_config()
    config["validation"]["strict_mode"] = strict_mode
    processor_conf = config["display"]["led_processor"]["configuration"]
    processor_conf.pop("intensity", None)
    processor_conf.pop("processing_disabled", None)
    return config


def test_validation_missing_processor_state_strict_fails() -> None:
    assert validate_config_data(config_without_processor_state(True)) is False


def test_validation_missing_processor_state_non_strict_warns() -> None:
    assert validate_config_data(config_without_processor_state(False)) is True
