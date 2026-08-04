#!/usr/bin/env python3
"""Tests for registering the wall as a named OCIO display."""

import numpy as np
import PyOpenColorIO as OCIO
import pytest

from conftest import (
    GAMMA,
    PEAK_LUMINANCE,
    STUDIO_CONFIG_URI,
    d65_xyz,
    make_characterization,
)
from OCIODisplayGen import (
    COLORIMETRIC_VIEW,
    DISPLAY_REFERENCE,
    create_display_colorspace_from_characterization,
    register_display,
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
