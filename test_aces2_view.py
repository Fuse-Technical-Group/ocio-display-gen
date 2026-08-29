#!/usr/bin/env python3
"""Tests for the ACES 2.0 output transform view (§spec:view-transform)."""

import numpy as np
import numpy.typing as npt
import PyOpenColorIO as OCIO
import pytest

from conftest import (
    GAMMA,
    PEAK_LUMINANCE,
    WALL_PRIMARIES,
    WALL_WHITEPOINT,
    build_reloaded_config,
    make_characterization,
    scene_view_cpu,
)
from ocio_display_gen._core import (
    ACES2_VIEW,
    COLORIMETRIC_VIEW,
    REFERENCE_LUMINANCE,
    VP_RADIOMETRIC_VIEW,
    create_aces2_view_transform,
)

Setup = tuple[OCIO.Config, str]

# Neutral scene-linear exposure ramp: 0.18 (diffuse gray) through 50.
EXPOSURE_RAMP = (0.18, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0)


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> Setup:
    return build_reloaded_config(tmp_path_factory, "wall_aces2.ocio")


def view_cpu(setup: Setup) -> OCIO.CPUProcessor:
    """Scene reference → ACES 2.0 view CPU processor."""
    return scene_view_cpu(setup, ACES2_VIEW)


def output_transform_cpu() -> OCIO.CPUProcessor:
    """Raw ACES 2.0 output transform with the wall's parameterization,
    applied through a bare config: ACES2065-1 → display-linear RGB in
    the limiting (wall) primaries, 1.0 = 100 cd/m²."""
    transform = OCIO.FixedFunctionTransform(
        OCIO.FIXED_FUNCTION_ACES_OUTPUT_TRANSFORM_20,
        params=[
            PEAK_LUMINANCE,
            *WALL_PRIMARIES.flatten().tolist(),
            *WALL_WHITEPOINT,
        ],
    )
    return OCIO.Config().getProcessor(transform).getDefaultCPUProcessor()


def decode_nits(out: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Wall code values → emitted nits via the display EOTF."""
    nits = np.asarray(out, dtype=np.float64) ** GAMMA * PEAK_LUMINANCE
    return np.asarray(nits, dtype=np.float64)


# --- Registration (§spec:config-structure) ---


def test_aces2_view_listed_between_vp_and_colorimetric(setup: Setup) -> None:
    cfg, display_name = setup
    views = [str(v) for v in cfg.getViews(display_name)]
    assert ACES2_VIEW in views
    assert views.index(VP_RADIOMETRIC_VIEW) < views.index(ACES2_VIEW)
    assert views.index(ACES2_VIEW) < views.index(COLORIMETRIC_VIEW)


def test_vp_radiometric_remains_default(setup: Setup) -> None:
    cfg, display_name = setup
    assert cfg.getDefaultView(display_name) == VP_RADIOMETRIC_VIEW


def test_reloaded_config_validates(setup: Setup) -> None:
    cfg, _ = setup
    cfg.validate()


# --- Tone scale: rolloff, not clip ---


def test_exposure_ramp_rolls_off(setup: Setup) -> None:
    cpu = view_cpu(setup)
    nits = []
    for v in EXPOSURE_RAMP:
        out = np.array(cpu.applyRGB([v, v, v]))
        nits.append(float(decode_nits(out).max()))
    nits_arr = np.array(nits)
    # Strictly increasing: no flat-line below peak.
    assert np.all(np.diff(nits_arr) > 0.0), f"ramp not increasing: {nits}"
    # Never exceeds the wall's measured peak.
    assert np.all(nits_arr <= PEAK_LUMINANCE), f"ramp exceeds peak: {nits}"
    # Compressive: successive doublings yield diminishing nit ratios,
    # each below linear passthrough (2x) and above flat (1x).
    doublings = ((1.0, 2.0), (5.0, 10.0), (10.0, 20.0))
    ramp = dict(zip(EXPOSURE_RAMP, nits_arr))
    ratios = [ramp[hi] / ramp[lo] for lo, hi in doublings]
    assert all(1.0 < r < 2.0 for r in ratios), f"ratios {ratios}"
    assert all(a > b for a, b in zip(ratios, ratios[1:])), f"ratios {ratios}"


# --- Diffuse white: ACES-2.0-predicted level, transparent encoding leg ---


def test_diffuse_white_matches_output_transform_prediction(setup: Setup) -> None:
    cpu = view_cpu(setup)
    raw = np.array(output_transform_cpu().applyRGB([1.0, 1.0, 1.0]))
    predicted_nits = raw * REFERENCE_LUMINANCE
    composed_nits = decode_nits(np.array(cpu.applyRGB([1.0, 1.0, 1.0])))
    assert np.allclose(composed_nits, predicted_nits, atol=0.5), (
        f"composed {composed_nits} vs predicted {predicted_nits}"
    )


def test_encoding_leg_is_transparent(setup: Setup) -> None:
    """View ∘ display colorspace equals the raw output transform mapped
    through the drive relationship: code = (linear * 100/peak)^(1/2.4)."""
    cpu = view_cpu(setup)
    raw_cpu = output_transform_cpu()
    for v in (0.18, 1.0, 5.0, 20.0):
        raw = np.array(raw_cpu.applyRGB([v, v, v]))
        drive = raw * REFERENCE_LUMINANCE / PEAK_LUMINANCE
        assert np.all(drive >= 0.0) and np.all(drive <= 1.0), f"scene {v}"
        expected = drive ** (1.0 / GAMMA)
        out = np.array(cpu.applyRGB([v, v, v]))
        assert np.allclose(out, expected, atol=1e-4), f"scene {v}"


# --- Auditability (§spec:signal-contract) ---


def test_description_records_peak_and_limiting_gamut() -> None:
    vt = create_aces2_view_transform(make_characterization())
    desc = vt.getDescription()
    assert "1000.0 cd/m²" in desc
    assert "R(0.6800, 0.3200)" in desc
    assert "G(0.2650, 0.6900)" in desc
    assert "B(0.1500, 0.0600)" in desc
    assert "W(0.3127, 0.3290)" in desc


def test_description_survives_reload(setup: Setup) -> None:
    cfg, _ = setup
    desc = cfg.getViewTransform(ACES2_VIEW).getDescription()
    assert "1000.0 cd/m²" in desc
    assert "R(0.6800, 0.3200)" in desc


# --- Failure modes ---


def test_missing_white_point_raises() -> None:
    char = make_characterization()
    char.white_point = None
    with pytest.raises(ValueError, match="white point"):
        create_aces2_view_transform(char)
