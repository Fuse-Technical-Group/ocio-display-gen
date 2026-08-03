#!/usr/bin/env python3
"""Tests for registering the wall as a named OCIO display."""

import colour
import numpy as np
import numpy.typing as npt
import PyOpenColorIO as OCIO
import pytest

from OCIODisplayGen import (
    COLORIMETRIC_VIEW,
    D65_WHITE_XY,
    DisplayCharacterization,
    create_display_colorspace_from_characterization,
    register_display,
)

STUDIO_CONFIG_URI = "ocio://studio-config-v2.1.0_aces-v1.3_ocio-v2.3"
DISPLAY_REFERENCE = "CIE-XYZ-D65"
PEAK_LUMINANCE = 1000.0
GAMMA = 2.4


def make_characterization() -> DisplayCharacterization:
    """Sample-yaml characterization values (display_config.yaml)."""
    char = DisplayCharacterization("Test Wall")
    char.primaries = {
        "red": (0.680, 0.320),
        "green": (0.265, 0.690),
        "blue": (0.150, 0.060),
    }
    char.white_point = (0.3127, 0.3290)
    char.black_level = 0.005
    char.peak_luminance = PEAK_LUMINANCE
    char.eotf_type = "GAMMA"
    char.gamma_value = GAMMA
    return char


def d65_xyz(luminance_y: float) -> npt.NDArray[np.float64]:
    """D65 white XYZ at the given Y (units of 100 cd/m²)."""
    return np.asarray(
        colour.xy_to_XYZ(np.array(D65_WHITE_XY)) * luminance_y, dtype=np.float64
    )


@pytest.fixture(scope="module")
def base_displays() -> list[str]:
    base = OCIO.Config.CreateFromFile(STUDIO_CONFIG_URI)
    return list(base.getDisplays())


@pytest.fixture(scope="module")
def reloaded(tmp_path_factory: pytest.TempPathFactory) -> tuple[OCIO.Config, str]:
    """Register the wall via the real generation path, serialize, reload."""
    config = OCIO.Config.CreateFromFile(STUDIO_CONFIG_URI)
    cs = create_display_colorspace_from_characterization(make_characterization())
    display_name = register_display(config, cs)
    path = tmp_path_factory.mktemp("ocio") / "wall_config.ocio"
    path.write_text(config.serialize(), encoding="utf-8")
    return OCIO.Config.CreateFromFile(str(path)), display_name


def test_wall_display_is_listed(reloaded: tuple[OCIO.Config, str]) -> None:
    cfg, display_name = reloaded
    assert display_name in list(cfg.getDisplays())


def test_colorimetric_view_is_listed(reloaded: tuple[OCIO.Config, str]) -> None:
    cfg, display_name = reloaded
    assert COLORIMETRIC_VIEW in list(cfg.getViews(display_name))


def test_base_displays_preserved(
    reloaded: tuple[OCIO.Config, str], base_displays: list[str]
) -> None:
    cfg, _ = reloaded
    displays = list(cfg.getDisplays())
    for display in base_displays:
        assert display in displays


def test_reloaded_config_validates(reloaded: tuple[OCIO.Config, str]) -> None:
    cfg, _ = reloaded
    cfg.validate()


def test_colorimetric_view_reproduces_display_code_values(
    reloaded: tuple[OCIO.Config, str],
) -> None:
    cfg, display_name = reloaded
    proc = cfg.getProcessor(
        DISPLAY_REFERENCE, display_name, COLORIMETRIC_VIEW, OCIO.TRANSFORM_DIR_FORWARD
    )
    cpu = proc.getDefaultCPUProcessor()
    # 1000 nits D65 white = wall peak → code value 1.0 per channel
    out = cpu.applyRGB(list(d65_xyz(10.0)))
    assert np.allclose(out, [1.0, 1.0, 1.0], atol=1e-4)
    # 100 nits on a 1000-nit wall: linear 0.1 → 0.1**(1/2.4)
    expected = (100.0 / PEAK_LUMINANCE) ** (1.0 / GAMMA)
    out = cpu.applyRGB(list(d65_xyz(1.0)))
    assert np.allclose(out, [expected] * 3, atol=1e-4)
