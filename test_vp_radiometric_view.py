#!/usr/bin/env python3
"""Tests for the default VP Radiometric view (§spec:view-transform)."""

from typing import cast

import colour
import numpy as np
import numpy.typing as npt
import PyOpenColorIO as OCIO
import pytest

from conftest import (
    GAMMA,
    PEAK_LUMINANCE,
    STUDIO_CONFIG_URI,
    build_reloaded_config,
    make_characterization,
    scene_view_cpu,
)
from OCIODisplayGen import (
    AP0_CHROMATICITIES,
    AP0_TO_XYZ_D65_BUILTIN,
    COLORIMETRIC_VIEW,
    REFERENCE_LUMINANCE,
    SHOULDER_KNEE,
    VP_RADIOMETRIC_VIEW,
    create_display_colorspace_from_characterization,
    create_display_xyz_to_native_matrix,
    create_vp_radiometric_view_transform,
    register_display,
)

NITS_ANCHOR = 300.0

Setup = tuple[OCIO.Config, str]


@pytest.fixture(scope="module")
def clamp_setup(tmp_path_factory: pytest.TempPathFactory) -> Setup:
    return build_reloaded_config(
        tmp_path_factory,
        "wall_clamp.ocio",
        nits_anchor=NITS_ANCHOR,
        overflow_policy="clamp",
    )


@pytest.fixture(scope="module")
def shoulder_setup(tmp_path_factory: pytest.TempPathFactory) -> Setup:
    return build_reloaded_config(
        tmp_path_factory,
        "wall_shoulder.ocio",
        nits_anchor=NITS_ANCHOR,
        overflow_policy="shoulder",
    )


def view_cpu(setup: Setup) -> OCIO.CPUProcessor:
    """Scene reference → VP Radiometric view CPU processor."""
    return scene_view_cpu(setup, VP_RADIOMETRIC_VIEW)


def apply_neutral(cpu: OCIO.CPUProcessor, v: float) -> npt.NDArray[np.float64]:
    return np.array(cpu.applyRGB([v, v, v]))


# --- Registration (§spec:config-structure) ---


def test_vp_radiometric_is_default_view(clamp_setup: Setup) -> None:
    cfg, display_name = clamp_setup
    views = [str(v) for v in cfg.getViews(display_name)]
    assert views[0] == VP_RADIOMETRIC_VIEW
    assert cfg.getDefaultView(display_name) == VP_RADIOMETRIC_VIEW


def test_colorimetric_view_still_listed(clamp_setup: Setup) -> None:
    cfg, display_name = clamp_setup
    assert COLORIMETRIC_VIEW in [str(v) for v in cfg.getViews(display_name)]


def test_reloaded_config_validates(clamp_setup: Setup) -> None:
    cfg, _ = clamp_setup
    cfg.validate()


# --- Neutral ramp: unity system gamma through the nits anchor ---


def test_neutral_ramp_clamp_reproduces_anchored_nits(clamp_setup: Setup) -> None:
    cpu = view_cpu(clamp_setup)
    for v in (0.05, 0.5, 1.0, 2.0, 3.0):
        out = apply_neutral(cpu, v)
        assert np.allclose(out, out[0], atol=1e-5), f"non-neutral output at {v}"
        decoded = out**GAMMA * PEAK_LUMINANCE
        assert np.allclose(decoded, v * NITS_ANCHOR, rtol=1e-3, atol=0.05), f"scene {v}"


def test_neutral_ramp_clamp_flat_lines_at_peak(clamp_setup: Setup) -> None:
    cpu = view_cpu(clamp_setup)
    for v in (3.4, 5.0, 100.0):
        assert np.allclose(apply_neutral(cpu, v), 1.0, atol=1e-5), f"scene {v}"


# --- Shoulder overflow policy ---


def test_shoulder_is_linear_below_knee(shoulder_setup: Setup) -> None:
    cpu = view_cpu(shoulder_setup)
    # Knee at 0.9 full drive: scene 0.9 * peak / anchor = 3.0
    for v in (0.05, 1.0, 2.0, 2.99):
        decoded = apply_neutral(cpu, v) ** GAMMA * PEAK_LUMINANCE
        assert np.allclose(decoded, v * NITS_ANCHOR, rtol=1e-3, atol=0.05), f"scene {v}"


def test_shoulder_flat_lines_above_rolloff(shoulder_setup: Setup) -> None:
    cpu = view_cpu(shoulder_setup)
    # The shoulder reaches full drive at ~1.2 * peak-equivalent input
    for v in (4.1, 10.0, 100.0):
        assert np.allclose(apply_neutral(cpu, v), 1.0, atol=1e-5), f"scene {v}"


def test_shoulder_is_monotone_through_rolloff(shoulder_setup: Setup) -> None:
    cpu = view_cpu(shoulder_setup)
    sweep = np.linspace(2.8, 4.2, 57)
    outs = np.array([apply_neutral(cpu, v)[0] for v in sweep])
    assert np.all(np.diff(outs) >= -1e-7)
    rolling = (sweep[:-1] >= 3.0) & (sweep[1:] <= 3.95)
    assert np.all(np.diff(outs)[rolling] > 0.0)


def test_shoulder_matches_clamp_below_knee(
    clamp_setup: Setup, shoulder_setup: Setup
) -> None:
    clamp_cpu = view_cpu(clamp_setup)
    shoulder_cpu = view_cpu(shoulder_setup)
    for v in (0.3, 1.0, 2.9):
        assert np.allclose(
            apply_neutral(clamp_cpu, v),
            apply_neutral(shoulder_cpu, v),
            atol=1e-6,
        ), f"scene {v}"


# --- In-gamut core passthrough: colorimetric within the wall's volume ---

# AP0 scene values whose native drive stays well inside the wall gamut
# (min/max channel ratio ≥ 0.2) and below peak (max ≤ 0.85).
CORE_SCENE_VALUES = (
    (1.0, 0.9, 0.8),
    (0.6, 0.7, 0.9),
    (1.2, 1.0, 1.1),
    (0.4, 0.5, 0.45),
    (2.0, 1.8, 1.9),
)


def test_in_gamut_core_matches_matrix_only_reference(clamp_setup: Setup) -> None:
    cfg, _ = clamp_setup
    cpu = view_cpu(clamp_setup)
    char = make_characterization()
    ap0_to_xyz = cfg.getProcessor(
        OCIO.BuiltinTransform(AP0_TO_XYZ_D65_BUILTIN)
    ).getDefaultCPUProcessor()
    xyz_to_native = create_display_xyz_to_native_matrix(char)[:3, :3]
    for scene in CORE_SCENE_VALUES:
        xyz = np.array(ap0_to_xyz.applyRGB(list(scene)))
        xyz *= NITS_ANCHOR / REFERENCE_LUMINANCE
        drive = (xyz_to_native @ xyz) * (REFERENCE_LUMINANCE / PEAK_LUMINANCE)
        ratio = drive.min() / drive.max()
        assert ratio >= 0.2 and drive.max() <= 0.85, f"bad test value {scene}"
        expected = drive ** (1.0 / GAMMA)
        out = np.array(cpu.applyRGB(list(scene)))
        assert np.allclose(out, expected, atol=1e-4), f"scene {scene}"


# --- Out-of-gamut hue sweep: untouched core, hue-preserving edge ---
#
# Sweep: Rec.2020 gamut-edge colors (a realistic content envelope,
# strongly outside the wall's gamut) at modest luminance, converted to
# AP0. Hue preservation is pinned at the view transform's output: the
# ACES 2.0 compressor works against an approximated gamut boundary and
# can leave a few-percent residual outside it, which the display
# colorspace's hard clip then skews by up to ~3° — a property of the
# compressor's approximation, not of this wiring.

SAMPLES_PER_EDGE = 48


def hue_sweep_scenes(
    samples_per_edge: int = SAMPLES_PER_EDGE,
) -> npt.NDArray[np.float64]:
    """Rec.2020 gamut-edge colors in AP0, well outside the wall gamut."""
    edges = []
    for t in np.linspace(0.05, 0.95, samples_per_edge):
        edges.append([1.0 - t, t, 0.0])
    for t in np.linspace(0.05, 0.95, samples_per_edge):
        edges.append([0.0, 1.0 - t, t])
    for t in np.linspace(0.05, 0.95, samples_per_edge):
        edges.append([t, 0.0, 1.0 - t])
    rec2020 = np.array(edges) * 0.4
    converted = colour.RGB_to_RGB(
        rec2020,
        colour.RGB_COLOURSPACES["ITU-R BT.2020"],
        colour.RGB_COLOURSPACES["ACES2065-1"],
    )
    return cast(npt.NDArray[np.float64], converted)


def test_sweep_is_beyond_wall_gamut(clamp_setup: Setup) -> None:
    """The sweep premise: nearly all samples need gamut compression."""
    cfg, _ = clamp_setup
    char = make_characterization()
    ap0_to_xyz = cfg.getProcessor(
        OCIO.BuiltinTransform(AP0_TO_XYZ_D65_BUILTIN)
    ).getDefaultCPUProcessor()
    xyz_to_native = create_display_xyz_to_native_matrix(char)[:3, :3]
    outside = 0
    sweep = hue_sweep_scenes()
    for scene in sweep:
        xyz = np.array(ap0_to_xyz.applyRGB(list(scene)))
        xyz *= NITS_ANCHOR / REFERENCE_LUMINANCE
        drive = (xyz_to_native @ xyz) * (REFERENCE_LUMINANCE / PEAK_LUMINANCE)
        if drive.min() < -1e-4:
            outside += 1
    assert outside >= 0.9 * len(sweep)


def jmh_cpu_from_ap0(cfg: OCIO.Config) -> OCIO.CPUProcessor:
    """AP0-linear → JMh processor (the gamut compressor's working space)."""
    transform = OCIO.FixedFunctionTransform(
        OCIO.FIXED_FUNCTION_ACES_RGB_TO_JMH_20, params=AP0_CHROMATICITIES
    )
    return cfg.getProcessor(transform).getDefaultCPUProcessor()


def view_transform_jmh_cpu(cfg: OCIO.Config) -> OCIO.CPUProcessor:
    """Scene → view transform output (display XYZ) → AP0 → JMh."""
    vt = cfg.getViewTransform(VP_RADIOMETRIC_VIEW)
    group = OCIO.GroupTransform()
    group.appendTransform(vt.getTransform(OCIO.VIEWTRANSFORM_DIR_FROM_REFERENCE))
    xyz_to_ap0 = OCIO.BuiltinTransform(AP0_TO_XYZ_D65_BUILTIN)
    xyz_to_ap0.setDirection(OCIO.TRANSFORM_DIR_INVERSE)
    group.appendTransform(xyz_to_ap0)
    group.appendTransform(
        OCIO.FixedFunctionTransform(
            OCIO.FIXED_FUNCTION_ACES_RGB_TO_JMH_20, params=AP0_CHROMATICITIES
        )
    )
    return cfg.getProcessor(group).getDefaultCPUProcessor()


def test_out_of_gamut_sweep_is_finite_and_in_range(clamp_setup: Setup) -> None:
    cpu = view_cpu(clamp_setup)
    outs = np.array([cpu.applyRGB(list(s)) for s in hue_sweep_scenes()])
    assert np.all(np.isfinite(outs))
    assert np.all(outs >= 0.0) and np.all(outs <= 1.0)


def test_out_of_gamut_sweep_preserves_hue(clamp_setup: Setup) -> None:
    cfg, _ = clamp_setup
    to_jmh_in = jmh_cpu_from_ap0(cfg)
    to_jmh_view = view_transform_jmh_cpu(cfg)
    anchor_scale = NITS_ANCHOR / REFERENCE_LUMINANCE
    for scene in hue_sweep_scenes():
        h_in = to_jmh_in.applyRGB(list(scene * anchor_scale))[2]
        h_view = to_jmh_view.applyRGB(list(scene))[2]
        delta = abs(h_view - h_in) % 360.0
        delta = min(delta, 360.0 - delta)
        assert delta < 1.0, f"hue skew {delta:.3f}° at {scene}"


def test_out_of_gamut_sweep_is_smooth(clamp_setup: Setup) -> None:
    """Small input steps → small linear-light steps on the wall. The
    code-value domain is excluded near black: the display EOTF's
    infinite slope at zero amplifies vanishing linear differences. The
    violet edge carries one known ~0.01 linear-light jump internal to
    the ACES 2.0 gamut compressor at deep violet (present in its raw
    output, pre-clip), so it gets a documented looser bound."""
    cpu = view_cpu(clamp_setup)
    sweep = hue_sweep_scenes()
    linear = np.array([cpu.applyRGB(list(s)) for s in sweep]) ** GAMMA
    bounds = (0.008, 0.008, 0.025)  # r-g, g-b, b-r (violet) edges
    for edge, bound in enumerate(bounds):
        span = slice(edge * SAMPLES_PER_EDGE, (edge + 1) * SAMPLES_PER_EDGE)
        steps = np.abs(np.diff(linear[span], axis=0))
        assert np.max(steps) < bound, f"edge {edge}: {np.max(steps):.5f}"


# --- Failure modes ---


def test_non_positive_or_non_finite_nits_anchor_raises() -> None:
    for bad_anchor in (0.0, -300.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="[Nn]its anchor"):
            create_vp_radiometric_view_transform(
                make_characterization(), bad_anchor, "clamp"
            )


def test_unknown_overflow_policy_raises() -> None:
    with pytest.raises(ValueError, match="shoulder"):
        create_vp_radiometric_view_transform(
            make_characterization(), NITS_ANCHOR, "perceptual"
        )


def test_pre_24_base_config_raises() -> None:
    config = OCIO.Config.CreateFromFile(STUDIO_CONFIG_URI)
    char = make_characterization()
    cs = create_display_colorspace_from_characterization(char)
    with pytest.raises(ValueError, match="studio-config-v4.0.0_aces-v2.0_ocio-v2.5"):
        register_display(config, cs, char, nits_anchor=NITS_ANCHOR)


# --- Auditability (§spec:signal-contract) ---


def test_description_records_anchor_and_clamp_policy() -> None:
    vt = create_vp_radiometric_view_transform(
        make_characterization(), NITS_ANCHOR, "clamp"
    )
    desc = vt.getDescription()
    assert "300.0 cd/m²" in desc
    assert "clamp" in desc


def test_description_records_shoulder_knee() -> None:
    vt = create_vp_radiometric_view_transform(
        make_characterization(), NITS_ANCHOR, "shoulder"
    )
    desc = vt.getDescription()
    assert "shoulder" in desc
    assert str(SHOULDER_KNEE) in desc


def test_description_survives_reload(shoulder_setup: Setup) -> None:
    cfg, _ = shoulder_setup
    desc = cfg.getViewTransform(VP_RADIOMETRIC_VIEW).getDescription()
    assert "300.0 cd/m²" in desc
    assert "shoulder" in desc
