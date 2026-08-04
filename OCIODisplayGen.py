# OCIODisplayGen.py
# This script creates a custom display colorspace for a high dynamic range display with
# non-standard native primaries, and appends it to an existing OCIO config
# It uses the colour-science library to create the colorspace and the PyOpenColorIO
# library to create the OCIO config

import os
import sys
from typing import Any, Dict, Optional, Tuple, cast

import colour
import numpy as np
import numpy.typing as npt
import PyOpenColorIO as OCIO
import yaml  # type: ignore[import]


def derive_reference_spaces(ocio_config: "OCIO.Config") -> Tuple[str, str]:
    """
    Derive the scene and display reference spaces from a config's
    interchange roles.

    Args:
        ocio_config: Loaded OCIO config to query

    Returns:
        Tuple of canonical colorspace names (scene_reference, display_reference)

    Raises:
        ValueError: If either interchange role is missing from the config.
            A silently wrong config is worse than no config.
    """
    names = []
    for role in (OCIO.ROLE_INTERCHANGE_SCENE, OCIO.ROLE_INTERCHANGE_DISPLAY):
        name = ocio_config.getCanonicalName(role)
        if not name:
            raise ValueError(f"Base config does not define interchange role '{role}'")
        names.append(name)
    return names[0], names[1]


# The display reference space the emitted matrix assumes, and its white
# chromaticity: input XYZ must be adapted to this white.
DISPLAY_REFERENCE = "CIE-XYZ-D65"
D65_WHITE_XY: Tuple[float, float] = (0.3127, 0.3290)

# Known names for that same space — CIE XYZ, D65-adapted, 1.0 = 100 cd/m².
# The ACES 1.3 studio config calls it "CIE-XYZ-D65"; the ACES 2.0 / OCIO 2.5
# studio config renames it "CIE XYZ-D65 - Display-referred". The emitted
# matrix's assumption holds for both.
KNOWN_DISPLAY_REFERENCES = (DISPLAY_REFERENCE, "CIE XYZ-D65 - Display-referred")


def validate_display_reference(display_reference: str) -> None:
    """
    Fail loud when the base config's derived display reference is not a
    known CIE-XYZ-D65 space the emitted matrix assumes.

    Raises:
        ValueError: For any name not in KNOWN_DISPLAY_REFERENCES.
    """
    if display_reference not in KNOWN_DISPLAY_REFERENCES:
        raise ValueError(
            f"Base config display reference is '{display_reference}', "
            f"but the emitted XYZ→native matrix assumes one of: "
            f"{', '.join(KNOWN_DISPLAY_REFERENCES)}"
        )


# The colorimetric view: the bare display colorspace with hard clip,
# for measurement and verification work (§spec:view-transform).
COLORIMETRIC_VIEW = "Colorimetric"

# The default view (§spec:view-transform): colorimetric within the
# wall's volume, ACES 2.0 gamut compression at its boundary, unity
# system gamma through a configurable nits anchor.
VP_RADIOMETRIC_VIEW = "VP Radiometric"

# The finished-content view (§spec:view-transform): the full ACES 2.0
# output transform, limited to the wall's measured gamut and peak —
# photographic by design, for IMAG and brand content, not VP plates.
ACES2_VIEW = "ACES 2.0"

# OCIO's display-reference luminance anchor: linear 1.0 = 100 cd/m².
REFERENCE_LUMINANCE = 100.0

# Above-peak overflow policies (§req:constraints): "clamp" is
# radiometric to the ceiling and flat-lines above it; "shoulder" trades
# exactness at the top of the range for a smooth rolloff confined there.
OVERFLOW_POLICIES = ("clamp", "shoulder")
DEFAULT_NITS_ANCHOR = 300.0
DEFAULT_OVERFLOW_POLICY = "clamp"

# Shoulder curve: exact identity below the knee, log rolloff above.
SHOULDER_KNEE = 0.9  # fraction of full drive where the rolloff starts
SHOULDER_END_SLOPE = 0.15  # curve slope where output reaches full drive

# ACES2065-1 (AP0) primaries and white as rx,ry,gx,gy,bx,by,wx,wy — the
# parameterization the ACES 2.0 JMh fixed functions take.
AP0_CHROMATICITIES = [0.7347, 0.2653, 0.0, 1.0, 0.0001, -0.077, 0.32168, 0.33767]

# Scene reference (AP0) → display reference (CIE-XYZ-D65) builtin.
AP0_TO_XYZ_D65_BUILTIN = "UTILITY - ACES-AP0_to_CIE-XYZ-D65_BFD"

# The ACES 2.0 _20 fixed functions require config profile >= 2.4; this
# base config satisfies it (§spec:version-targeting).
MIN_ACES2_PROFILE = (2, 4)
ACES2_BASE_CONFIG_URI = "ocio://studio-config-v4.0.0_aces-v2.0_ocio-v2.5"

# White point policies (§spec:white-point):
# "adapted": chromatic adaptation maps content white (D65) to the wall's
# native white — preserves full brightness. "absolute": no adaptation —
# colorimetrically exact within gamut, at the cost of peak brightness and
# possible single-channel clipping when wall white differs from D65.
WHITE_POINT_POLICIES = ("adapted", "absolute")


class DisplayCharacterization:
    """Class to hold display characterization data"""

    def __init__(self, name: str):
        self.name = name
        self.primaries: Dict[
            str, Tuple[float, float]
        ] = {}  # Measured RGB primaries (xy coordinates)
        self.white_point: Optional[Tuple[float, float]] = (
            None  # Measured white point (xy coordinates)
        )
        self.black_level = 0.0  # Measured black level (cd/m²)
        self.peak_luminance = 1000.0  # Measured peak luminance (cd/m²)
        self.contrast_ratio = 1000.0  # Measured contrast ratio
        self.eotf_type = "PQ"  # Display EOTF type: "PQ", "HLG", "GAMMA"
        self.gamma_value = 2.4  # For gamma-based EOTF (display property)
        self.measured_response: Optional[str] = None  # Custom measured response curve
        self.white_point_policy = "adapted"  # "adapted" or "absolute"
        # Processor state the config is valid for (§spec:signal-contract):
        # locked intensity (free-form: percent or nits as configured) and
        # whether color processing / dynamic features are disabled.
        self.processor_intensity: Optional[str] = None
        self.processor_processing_disabled: Optional[bool] = None


def _matrix_transform(matrix: npt.NDArray[np.floating[Any]]) -> OCIO.MatrixTransform:
    """4x4 row-major matrix as an OCIO MatrixTransform."""
    transform = OCIO.MatrixTransform()
    transform.setMatrix(matrix.flatten().tolist())
    return transform


def _measured_wall_gamut(
    characterization: DisplayCharacterization,
) -> Tuple[
    float,
    Tuple[float, float],
    Tuple[float, float],
    Tuple[float, float],
    Tuple[float, float],
]:
    """
    Measured peak and RGBW chromaticities parameterizing the ACES 2.0
    fixed functions: (peak, red, green, blue, white_point).

    Raises:
        ValueError: For a missing measured white point.
    """
    white_point = characterization.white_point
    if white_point is None:
        raise ValueError("Characterization has no measured white point")
    return (
        characterization.peak_luminance,
        characterization.primaries["red"],
        characterization.primaries["green"],
        characterization.primaries["blue"],
        white_point,
    )


def _describe_gamut(
    red: Tuple[float, float],
    green: Tuple[float, float],
    blue: Tuple[float, float],
    white_point: Tuple[float, float],
) -> str:
    """RGBW chromaticities formatted for recorded descriptions."""
    return (
        f"R({red[0]:.4f}, {red[1]:.4f}) "
        f"G({green[0]:.4f}, {green[1]:.4f}) "
        f"B({blue[0]:.4f}, {blue[1]:.4f}) "
        f"W({white_point[0]:.4f}, {white_point[1]:.4f})"
    )


def describe_processing_state(disabled: Optional[bool]) -> str:
    """Processor color-processing / dynamic-features state for metadata
    and console output (§spec:signal-contract)."""
    if disabled is None:
        return "(not recorded)"
    return "disabled" if disabled else "NOT disabled"


def create_display_xyz_to_native_matrix(
    characterization: DisplayCharacterization,
    chromatic_adaptation_transform: str = "CAT02",
) -> npt.NDArray[np.float64]:
    """
    Build the 4x4 matrix from display-reference CIE XYZ (D65-adapted) to
    the wall's native RGB, per the characterization's white point policy
    (§spec:white-point).

    Policy "adapted": composes a Von Kries chromatic adaptation
    (display-reference D65 → measured wall white) with the wall's derived
    XYZ→RGB matrix, so native RGB (1, 1, 1) is the wall's measured white.
    Policy "absolute": the derived XYZ→RGB matrix alone — no adaptation,
    chromaticity is exact within gamut.

    Args:
        characterization: Measured display data (primaries, white point)
            and white point policy
        chromatic_adaptation_transform: CAT name accepted by colour-science
            (adapted policy only)

    Returns:
        4x4 matrix for an OCIO MatrixTransform (row-major)

    Raises:
        ValueError: For unknown white point policies.
    """
    white_point_policy = characterization.white_point_policy
    if white_point_policy not in WHITE_POINT_POLICIES:
        raise ValueError(
            f"Unknown white point policy '{white_point_policy}'; "
            f"valid values: {', '.join(WHITE_POINT_POLICIES)}"
        )

    primaries = np.array(
        [
            characterization.primaries["red"],
            characterization.primaries["green"],
            characterization.primaries["blue"],
        ],
        dtype=np.float64,
    )
    white_xy = np.asarray(characterization.white_point, dtype=np.float64)

    wall_space = colour.RGB_Colourspace(
        characterization.name, primaries, white_xy, "Wall White"
    )
    wall_space.use_derived_transformation_matrices()

    matrix_3x3 = wall_space.matrix_XYZ_to_RGB
    if white_point_policy == "adapted":
        cat_matrix = colour.adaptation.matrix_chromatic_adaptation_VonKries(
            colour.xy_to_XYZ(np.array(D65_WHITE_XY)),
            colour.xy_to_XYZ(white_xy),
            transform=chromatic_adaptation_transform,
        )
        matrix_3x3 = matrix_3x3 @ cat_matrix

    matrix_4x4 = np.identity(4)
    matrix_4x4[:3, :3] = matrix_3x3
    return matrix_4x4


def create_display_colorspace_from_characterization(
    characterization: DisplayCharacterization,
    chromatic_adaptation_transform: str = "CAT02",
) -> OCIO.ColorSpace:
    """
    Create the wall's OCIO display colorspace from measured data.

    The colorspace is display-referred: its from_display_reference
    transform maps CIE XYZ (D65-adapted, 1.0 = 100 cd/m²) to the wall's
    encoded native RGB. Pipeline: XYZ→native matrix (white point policy
    applied), absolute luminance scale, hard clip, inverse processor
    EOTF. It holds only measured colorimetry — exact within gamut,
    hard-clipped outside. The chosen policy is recorded in the
    colorspace description.

    Args:
        characterization: Measured display data, white point policy, and
            processor state
        chromatic_adaptation_transform: CAT for D65 → wall white
            adaptation (adapted policy only)

    Raises:
        NotImplementedError: For HLG (an inverse EOTF without OOTF
            handling would be silently wrong).
        ValueError: For unknown EOTF types or white point policies.
    """
    eotf_type = characterization.eotf_type
    if eotf_type == "HLG":
        raise NotImplementedError(
            "HLG output is not supported: an HLG inverse EOTF without OOTF "
            "handling would be silently wrong. Configure the processor for "
            "PQ or GAMMA."
        )
    if eotf_type not in ("PQ", "GAMMA"):
        raise ValueError(f"Unknown display EOTF type '{eotf_type}'")

    peak = characterization.peak_luminance

    white_point_policy = characterization.white_point_policy
    if white_point_policy == "absolute":
        policy_note = "absolute (no chromatic adaptation)"
    else:
        policy_note = f"adapted ({chromatic_adaptation_transform}, D65 → wall white)"

    # Signal contract (§spec:signal-contract): record the processor state
    # the config is valid for, so operators can restore and audit it.
    if eotf_type == "PQ":
        contract_clauses = ["EOTF PQ"]
    else:
        contract_clauses = [f"EOTF GAMMA {characterization.gamma_value}"]
    if characterization.processor_intensity is not None:
        contract_clauses.append(f"intensity {characterization.processor_intensity}")
    if characterization.processor_processing_disabled is not None:
        contract_clauses.append(
            "color processing and dynamic features "
            f"{describe_processing_state(characterization.processor_processing_disabled)}"
        )
    signal_contract = (
        "Signal contract: valid only while the processor holds this "
        f"state — {', '.join(contract_clauses)}."
    )

    cs = OCIO.ColorSpace(OCIO.REFERENCE_SPACE_DISPLAY)
    display_name = f"{characterization.name} - Display"
    cs.setName(display_name)
    cs.addAlias(f"{display_name.lower().replace(' ', '_')}_display")
    cs.setFamily("Display")
    cs.setEncoding("hdr-video" if eotf_type == "PQ" else "sdr-video")
    cs.setDescription(
        f"Display colorspace for {characterization.name} "
        f"(Peak: {characterization.peak_luminance} cd/m², "
        f"Black: {characterization.black_level} cd/m², "
        f"EOTF: {eotf_type}) "
        f"CIE-XYZ-D65 → native RGB matrix → luminance scale → "
        f"hard clip → inverse {eotf_type} EOTF. "
        f"White point policy: {policy_note}. "
        f"{signal_contract}"
    )
    cs.setBitDepth(OCIO.BIT_DEPTH_F32)
    cs.addCategory("file-io")
    cs.addCategory("display")

    group = OCIO.GroupTransform()

    # Stage 1: XYZ (D65-adapted) → native RGB, per white point policy.
    # Adapted: native (1,1,1) = wall white at 100 cd/m² (Y = 1.0).
    # Absolute: no adaptation; D65 content white lands off the wall's
    # neutral axis and may single-channel clip at stage 3.
    matrix_4x4 = create_display_xyz_to_native_matrix(
        characterization, chromatic_adaptation_transform
    )
    group.appendTransform(_matrix_transform(matrix_4x4))

    if eotf_type == "GAMMA":
        # Stage 2: absolute luminance scale — RGB 1.0 = measured peak.
        # Kept as a distinct stage for auditability.
        scale = REFERENCE_LUMINANCE / peak
        group.appendTransform(_matrix_transform(np.diag([scale] * 3 + [1.0])))
        clip_max = 1.0
    else:
        # PQ is absolute (encodes nits directly): omit the luminance
        # scale and clip at the measured peak instead, so the encoding
        # stays exact and out-of-range values clip at the wall's peak.
        clip_max = peak / REFERENCE_LUMINANCE

    # Stage 3: hard clip. The display colorspace is exact within gamut
    # and hard-clips outside; gamut handling belongs to view transforms.
    range_transform = OCIO.RangeTransform()
    range_transform.setMinInValue(0.0)
    range_transform.setMaxInValue(clip_max)
    range_transform.setMinOutValue(0.0)
    range_transform.setMaxOutValue(clip_max)
    group.appendTransform(range_transform)

    # Stage 4: inverse processor EOTF (linear → encoded).
    if eotf_type == "PQ":
        pq_transform = OCIO.BuiltinTransform("CURVE - LINEAR_to_ST-2084")
        pq_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
        group.appendTransform(pq_transform)
    else:
        gamma_transform = OCIO.ExponentTransform()
        gamma_transform.setValue([1.0 / characterization.gamma_value] * 3 + [1.0])
        group.appendTransform(gamma_transform)

    cs.setTransform(group, OCIO.COLORSPACE_DIR_FROM_REFERENCE)
    return cs


def _shoulder_overflow_transform() -> OCIO.LogCameraTransform:
    """
    The shoulder rolloff in drive space: y = a*log2(x + o) + b above the
    knee k, exact identity (negatives included) below it.

    Constraints: y(k) = k and y'(k) = 1 (C1 continuity at the knee), and
    y' = m where y reaches 1.0 (slope m at full drive). Solving:
    a = (1 - k) / log2(1/m); u_k = a / ln 2 (log argument at the knee);
    o = u_k - k; b = k - a*log2(u_k). A one-sided range clamp at 1.0
    flat-lines the curve where the log crosses full drive.
    """
    knee = SHOULDER_KNEE
    log_side_slope = (1.0 - knee) / float(np.log2(1.0 / SHOULDER_END_SLOPE))
    knee_arg = log_side_slope / float(np.log(2.0))
    lin_side_offset = knee_arg - knee
    log_side_offset = knee - log_side_slope * float(np.log2(knee_arg))
    return OCIO.LogCameraTransform(
        base=2.0,
        logSideSlope=[log_side_slope] * 3,
        logSideOffset=[log_side_offset] * 3,
        linSideSlope=[1.0] * 3,
        linSideOffset=[lin_side_offset] * 3,
        linSideBreak=[knee] * 3,
        linearSlope=[1.0] * 3,
    )


def create_vp_radiometric_view_transform(
    characterization: DisplayCharacterization,
    nits_anchor: float,
    overflow_policy: str,
    chromatic_adaptation_transform: str = "CAT02",
) -> OCIO.ViewTransform:
    """
    Build the VP Radiometric view transform (§spec:view-transform).

    Scene-referred: maps scene reference (ACES2065-1) to display
    reference (CIE-XYZ-D65). Pipeline: nits anchor scale (scene-linear
    1.0 → anchor cd/m²), ACES 2.0 gamut compression in JMh at the
    wall-gamut boundary (untouched core, hue-preserving edge), AP0 →
    display-reference matrix, then the above-peak overflow policy
    applied per channel in the wall's drive space via the same policy
    matrix the display colorspace uses. End-to-end system gamma is 1.0.
    The anchor, policy, and compressor parameterization are recorded in
    the description (§spec:signal-contract).

    Args:
        characterization: Measured display data (primaries, white
            point, peak) parameterizing the gamut compressor and drive
            space
        nits_anchor: cd/m² emitted for scene-linear 1.0 — the only
            placement knob
        overflow_policy: "clamp" or "shoulder" above-peak handling
        chromatic_adaptation_transform: CAT for the drive-space matrix
            (adapted white point policy only)

    Raises:
        ValueError: For unknown overflow policies or a missing measured
            white point.
    """
    if overflow_policy not in OVERFLOW_POLICIES:
        raise ValueError(
            f"Unknown overflow policy '{overflow_policy}'; "
            f"valid values: {', '.join(OVERFLOW_POLICIES)}"
        )
    # A non-positive or non-finite anchor would generate a validating
    # config that emits black or inverted signal — fail loud instead.
    if not (np.isfinite(nits_anchor) and nits_anchor > 0.0):
        raise ValueError(
            f"Nits anchor must be a positive finite value in cd/m², got {nits_anchor}"
        )
    peak, red, green, blue, white_point = _measured_wall_gamut(characterization)

    if overflow_policy == "shoulder":
        policy_note = (
            f"shoulder (log rolloff from {SHOULDER_KNEE} of full drive, "
            f"hard limit at peak)"
        )
    else:
        policy_note = "clamp (hard clamp at peak, radiometric to the ceiling)"

    vt = OCIO.ViewTransform(OCIO.REFERENCE_SPACE_SCENE)
    vt.setName(VP_RADIOMETRIC_VIEW)
    vt.setDescription(
        f"VP Radiometric rendering for {characterization.name}: "
        f"colorimetric within the wall's volume, ACES 2.0 gamut "
        f"compression at its boundary. "
        f"Nits anchor: scene-linear 1.0 = {nits_anchor} cd/m² "
        f"(unity system gamma). "
        f"Overflow policy: {policy_note}. "
        f"Gamut compressor: measured peak {peak} cd/m², "
        f"primaries {_describe_gamut(red, green, blue, white_point)}."
    )

    group = OCIO.GroupTransform()

    # Stage 1: nits anchor — scene-linear 1.0 → anchor cd/m² in
    # display-linear units (1.0 = REFERENCE_LUMINANCE).
    anchor_matrix = np.diag([nits_anchor / REFERENCE_LUMINANCE] * 3 + [1.0])
    group.appendTransform(_matrix_transform(anchor_matrix))

    # Stage 2: ACES 2.0 gamut compression sandwich in JMh, limited to
    # the wall's measured gamut and peak. Interior colors untouched;
    # compression confined to a smoothing zone at the boundary.
    to_jmh = OCIO.FixedFunctionTransform(
        OCIO.FIXED_FUNCTION_ACES_RGB_TO_JMH_20, params=AP0_CHROMATICITIES
    )
    group.appendTransform(to_jmh)
    gamut_compress = OCIO.FixedFunctionTransform(
        OCIO.FIXED_FUNCTION_ACES_GAMUT_COMPRESS_20,
        params=[peak, *red, *green, *blue, *white_point],
    )
    group.appendTransform(gamut_compress)
    from_jmh = OCIO.FixedFunctionTransform(
        OCIO.FIXED_FUNCTION_ACES_RGB_TO_JMH_20, params=AP0_CHROMATICITIES
    )
    from_jmh.setDirection(OCIO.TRANSFORM_DIR_INVERSE)
    group.appendTransform(from_jmh)

    # Stage 3: AP0 → display reference (CIE-XYZ-D65).
    group.appendTransform(OCIO.BuiltinTransform(AP0_TO_XYZ_D65_BUILTIN))

    # Stage 4: into drive space — wall native RGB where 1.0 = full
    # drive. Reuses the display colorspace's exact policy matrix so the
    # view and colorspace compose transparently.
    drive = np.diag(
        [REFERENCE_LUMINANCE / peak] * 3 + [1.0]
    ) @ create_display_xyz_to_native_matrix(
        characterization, chromatic_adaptation_transform
    )
    group.appendTransform(_matrix_transform(drive))

    # Stage 5: above-peak overflow policy, per channel in drive space.
    # One-sided max clamp only: negatives pass through untouched here —
    # the display colorspace clips low.
    if overflow_policy == "shoulder":
        group.appendTransform(_shoulder_overflow_transform())
    ceiling = OCIO.RangeTransform()
    ceiling.setMaxInValue(1.0)
    ceiling.setMaxOutValue(1.0)
    group.appendTransform(ceiling)

    # Stage 6: back to display reference for the display colorspace.
    group.appendTransform(_matrix_transform(np.linalg.inv(drive)))

    vt.setTransform(group, OCIO.VIEWTRANSFORM_DIR_FROM_REFERENCE)
    return vt


def create_aces2_view_transform(
    characterization: DisplayCharacterization,
    chromatic_adaptation_transform: str = "CAT02",
) -> OCIO.ViewTransform:
    """
    Build the ACES 2.0 view transform (§spec:view-transform).

    Scene-referred: maps scene reference (ACES2065-1) to display
    reference (CIE-XYZ-D65) through the full ACES 2.0 output transform
    parameterized by the wall's measured peak luminance and native
    primaries/white as the limiting gamut. For finished-content
    contexts (IMAG, brand content): its tone scale and chroma
    compression are photographic by design, radiometric nowhere.

    Pipeline: ACES 2.0 output transform (ACES2065-1 → display-linear
    RGB in the limiting primaries, 1.0 = 100 cd/m²), then the exact
    inverse of the display colorspace's policy matrix back to
    display-reference XYZ, so view + display colorspace compose
    transparently (the encoding leg cancels; no luminance rescale —
    the output transform's display-linear shares the display
    reference's 1.0 = 100 cd/m² anchor). The parameterization is
    recorded in the description (§spec:signal-contract).

    Args:
        characterization: Measured display data (peak, primaries,
            white point) parameterizing the output transform's
            limiting gamut and the drive-space matrix
        chromatic_adaptation_transform: CAT for the drive-space matrix
            (adapted white point policy only)

    Raises:
        ValueError: For a missing measured white point.
    """
    peak, red, green, blue, white_point = _measured_wall_gamut(characterization)

    vt = OCIO.ViewTransform(OCIO.REFERENCE_SPACE_SCENE)
    vt.setName(ACES2_VIEW)
    vt.setDescription(
        f"ACES 2.0 output transform for {characterization.name}: "
        f"full tone scale and chroma compression for finished content "
        f"(IMAG, brand content), limited to the wall's measured gamut "
        f"and peak. Parameterization: peak {peak} cd/m², limiting "
        f"gamut {_describe_gamut(red, green, blue, white_point)}."
    )

    group = OCIO.GroupTransform()

    # Stage 1: ACES 2.0 output transform, limited to the wall's
    # measured gamut and peak. Output is display-linear RGB in the
    # limiting primaries, 1.0 = 100 cd/m².
    output_transform = OCIO.FixedFunctionTransform(
        OCIO.FIXED_FUNCTION_ACES_OUTPUT_TRANSFORM_20,
        params=[peak, *red, *green, *blue, *white_point],
    )
    group.appendTransform(output_transform)

    # Stage 2: wall display-linear RGB → display reference XYZ via the
    # exact inverse of the display colorspace's policy matrix, so the
    # encoding leg cancels exactly (same technique as the VP view's
    # drive-space sandwich, without the 100/peak factor: the output
    # transform's display-linear already shares the display
    # reference's 1.0 = 100 cd/m² anchor).
    native_to_xyz = np.linalg.inv(
        create_display_xyz_to_native_matrix(
            characterization, chromatic_adaptation_transform
        )
    )
    group.appendTransform(_matrix_transform(native_to_xyz))

    vt.setTransform(group, OCIO.VIEWTRANSFORM_DIR_FROM_REFERENCE)
    return vt


def register_display(
    config: "OCIO.Config",
    colorspace: OCIO.ColorSpace,
    characterization: DisplayCharacterization,
    nits_anchor: float = DEFAULT_NITS_ANCHOR,
    overflow_policy: str = DEFAULT_OVERFLOW_POLICY,
    chromatic_adaptation_transform: str = "CAT02",
) -> str:
    """
    Register the wall as a named OCIO display with its views.

    Adds the colorspace and the VP Radiometric and ACES 2.0 view
    transforms to the config and registers a display named after the
    colorspace (studio config convention: display name == display
    colorspace name) with "VP Radiometric" first — the OCIO default
    view — then "ACES 2.0" (finished-content rendering), then the
    "Colorimetric" view (bare colorspace). The display is appended to
    the config's active-display list without clobbering the base
    config's existing entries. An empty active list means "all active"
    in OCIO, so it is left empty.

    Args:
        config: Base OCIO config to extend
        colorspace: Display-referred wall colorspace
        characterization: Measured display data parameterizing the VP
            Radiometric view
        nits_anchor: cd/m² emitted for scene-linear 1.0
        overflow_policy: "clamp" or "shoulder" above-peak handling
        chromatic_adaptation_transform: CAT for D65 → wall white
            (adapted policy only)

    Returns:
        The registered display name

    Raises:
        ValueError: When the base config's profile version cannot hold
            the ACES 2.0 fixed functions, for unknown overflow
            policies, or for a missing measured white point.
    """
    version = (config.getMajorVersion(), config.getMinorVersion())
    if version < MIN_ACES2_PROFILE:
        raise ValueError(
            f"Base config profile version {version[0]}.{version[1]} cannot "
            f"hold the ACES 2.0 fixed functions the VP Radiometric and "
            f"ACES 2.0 views use (requires >= "
            f"{MIN_ACES2_PROFILE[0]}.{MIN_ACES2_PROFILE[1]}); "
            f"select the ACES 2.0 / OCIO 2.5 studio base config "
            f"({ACES2_BASE_CONFIG_URI})"
        )

    vp_view_transform = create_vp_radiometric_view_transform(
        characterization,
        nits_anchor,
        overflow_policy,
        chromatic_adaptation_transform,
    )
    aces2_view_transform = create_aces2_view_transform(
        characterization, chromatic_adaptation_transform
    )
    config.addColorSpace(colorspace)
    config.addViewTransform(vp_view_transform)
    config.addViewTransform(aces2_view_transform)
    display_name = colorspace.getName()
    # First view added is the display's default. Keyword arguments are
    # required: positional binds the colorspace-only overload.
    config.addDisplayView(
        display=display_name,
        view=VP_RADIOMETRIC_VIEW,
        viewTransform=VP_RADIOMETRIC_VIEW,
        displayColorSpaceName=display_name,
    )
    config.addDisplayView(
        display=display_name,
        view=ACES2_VIEW,
        viewTransform=ACES2_VIEW,
        displayColorSpaceName=display_name,
    )
    config.addDisplayView(display_name, COLORIMETRIC_VIEW, display_name)

    active_displays = [str(d) for d in config.getActiveDisplays()]
    if active_displays:
        config.setActiveDisplays(", ".join([*active_displays, display_name]))

    return display_name


# YAML Configuration Functions
def load_config_from_yaml(config_path: str) -> Dict[str, Any]:
    """Load display configuration from YAML file."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        print(f"❌ Error: Configuration file '{config_path}' not found.")
        print(
            "   Please create a 'display_config.yaml' file with your display "
            "measurements."
        )
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"❌ Error parsing YAML file: {e}")
        sys.exit(1)


def create_characterization_from_config(
    config: Dict[str, Any],
) -> DisplayCharacterization:
    """Create DisplayCharacterization object from YAML config."""

    display_config = config["display"]

    # Generate display name from LED panel and processor
    led_panel_config = display_config["led_panel"]
    led_panel_name = (
        f"{led_panel_config['manufacturer']} {led_panel_config['model']} "
        f"({led_panel_config['version']})"
    )

    led_processor_config = display_config["led_processor"]
    led_processor_name = (
        f"{led_processor_config['manufacturer']} "
        f"{led_processor_config['model']} ({led_processor_config['version']})"
    )

    display_name = f"{led_panel_name} + {led_processor_name}"

    char = DisplayCharacterization(display_name)

    # Get colorimetry from display.led_panel.colorimetry
    colorimetry_config = led_panel_config["colorimetry"]
    primaries_config = colorimetry_config["primaries"]
    char.primaries = {
        "red": tuple(primaries_config["red"]),
        "green": tuple(primaries_config["green"]),
        "blue": tuple(primaries_config["blue"]),
    }
    char.white_point = tuple(colorimetry_config["white_point"])

    # Get luminance from display.led_panel.luminance
    luminance_config = led_panel_config["luminance"]
    char.black_level = luminance_config["black_level"]
    char.peak_luminance = luminance_config["peak_luminance"]
    # Guard the division: non-strict validation lets a zero black level
    # through with a warning.
    if char.black_level > 0:
        char.contrast_ratio = char.peak_luminance / char.black_level
    else:
        char.contrast_ratio = float("inf")

    # Get EOTF configuration from display.led_processor.configuration.eotf
    if (
        "configuration" not in led_processor_config
        or "eotf" not in led_processor_config["configuration"]
    ):
        raise ValueError(
            "Configuration must contain "
            "'display.led_processor.configuration.eotf' section"
        )

    eotf_config = led_processor_config["configuration"]["eotf"]
    char.eotf_type = eotf_config["type"]
    char.gamma_value = eotf_config.get("gamma_value", 2.4)

    # Processor signal-contract state (§spec:signal-contract). Optional:
    # validate_config_data warns (or fails in strict mode) when absent.
    processor_state = led_processor_config["configuration"]
    intensity = processor_state.get("intensity")
    char.processor_intensity = None if intensity is None else str(intensity)
    char.processor_processing_disabled = processor_state.get("processing_disabled")

    # White point policy is a generation decision, not a measurement, so
    # it lives under ocio:. Validated at matrix-build time.
    char.white_point_policy = config.get("ocio", {}).get(
        "white_point_policy", "adapted"
    )
    return char


def load_validation_settings() -> Dict[str, Any]:
    """Load validation settings from external file."""

    # Default validation settings
    default_validation: Dict[str, Any] = {
        "check_primaries": True,
        "check_white_point": True,
        "check_luminance": True,
        "check_contrast": True,
        "check_processor_state": True,
        "min_contrast_ratio": 100,
        "max_contrast_ratio": 10000,
        "warn_on_validation_failure": True,
        "strict_mode": False,
    }

    # Load external validation settings file
    validation_file = "validation_settings.yaml"
    external_validation: Dict[str, Any] = {}

    if os.path.exists(validation_file):
        try:
            with open(validation_file, "r", encoding="utf-8") as f:
                loaded_data = yaml.safe_load(f)
                if isinstance(loaded_data, dict):
                    external_validation = cast(Dict[str, Any], loaded_data)
                else:
                    print(
                        "⚠️  Warning: Validation settings file contains "
                        "invalid data type"
                    )
                    print("   Using default validation settings")
            print(f"✓ Loaded validation settings from '{validation_file}'")
        except yaml.YAMLError as e:
            print(f"⚠️  Warning: Error parsing validation settings file: {e}")
            print("   Using default validation settings")
    else:
        print(f"⚠️  Warning: Validation settings file '{validation_file}' not found")
        print("   Using default validation settings")

    # Merge settings: external file -> defaults
    validation_settings: Dict[str, Any] = default_validation.copy()
    validation_settings.update(external_validation)

    return validation_settings


def validate_config_data(config: Dict[str, Any]) -> bool:
    """Validate configuration data from YAML file."""

    # Load validation settings
    validation_config = load_validation_settings()

    # Get validation mode from display config (overrides validation settings)
    strict_mode = config.get("validation", {}).get("strict_mode", False)

    # Check primaries
    if validation_config.get("check_primaries", True):
        print(
            "Validating display primaries: basic chromaticity range check "
            "(not a true spectral locus test)..."
        )
        for color, coords in config["display"]["led_panel"]["colorimetry"][
            "primaries"
        ].items():
            x, y = coords
            # Basic range check
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and x + y <= 1.0):
                message = (
                    f"❌ Warning: {color} primary ({x}, {y}) is outside the "
                    f"valid xy chromaticity triangle (0 ≤ x ≤ 1, 0 ≤ y ≤ 1, "
                    f"x + y ≤ 1)"
                )
                if strict_mode:
                    print(message)
                    return False
                else:
                    print(message)
            else:
                print(
                    f"✓ {color} primary ({x:.4f}, {y:.4f}) is within the "
                    f"valid xy chromaticity triangle"
                )

    # Check white point
    if validation_config.get("check_white_point", True):
        x, y = config["display"]["led_panel"]["colorimetry"]["white_point"]

        # Basic range check
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            message = f"❌ Warning: White point ({x}, {y}) outside valid range [0,1]"
            if strict_mode:
                print(message)
                return False
            else:
                print(message)
        else:
            print(f"✓ White point ({x:.4f}, {y:.4f}) is within valid range [0,1]")

        # Check duv deviation from Planckian locus (using CIE 1960 UCS uv)
        try:
            # Convert xy to CIE 1960 UCS uv coordinates
            xy_coords = np.array([x, y])
            uv_coords = colour.xy_to_UCS_uv(xy_coords)

            # Calculate CCT and duv
            CCT, duv = colour.uv_to_CCT(uv_coords, method="Ohno 2013")

            # Check CCT range
            min_cct = validation_config.get("min_white_point_temp", 4000)
            max_cct = validation_config.get("max_white_point_temp", 10000)

            if not (min_cct <= CCT <= max_cct):
                message = (
                    f"❌ Warning: White point CCT ({CCT:.0f}K) outside "
                    f"acceptable range [{min_cct}, {max_cct}]K"
                )
                if strict_mode:
                    print(message)
                    return False
                else:
                    print(message)
            else:
                print(
                    f"✓ White point CCT ({CCT:.0f}K) is within acceptable "
                    f"range [{min_cct}, {max_cct}]K"
                )

            # Check duv deviation
            max_duv_deviation = validation_config.get("max_duv_deviation", 0.15)

            if abs(duv) > max_duv_deviation:
                message = (
                    f"❌ Warning: White point duv deviation ({duv:.4f}) "
                    f"exceeds maximum ({max_duv_deviation:.4f})"
                )
                if strict_mode:
                    print(message)
                    return False
                else:
                    print(message)
            else:
                print(
                    f"✓ White point duv deviation ({duv:.4f}) is within "
                    f"acceptable range (±{max_duv_deviation:.4f})"
                )

        except Exception as e:
            # Fallback if duv calculation fails
            print(f"⚠️  Warning: Could not calculate duv deviation: {e}")
            print("   Skipping duv validation")

    # Check luminance values
    if validation_config.get("check_luminance", True):
        black_level = config["display"]["led_panel"]["luminance"]["black_level"]
        peak_luminance = config["display"]["led_panel"]["luminance"]["peak_luminance"]

        if black_level <= 0:
            # A measured black level is never exactly zero; zero usually
            # means the instrument floored or the field was guessed.
            message = (
                "❌ Warning: Black level must be positive (a measured "
                "black level is never exactly zero)"
            )
            if strict_mode:
                print(message)
                return False
            else:
                print(message)

        if peak_luminance <= 0:
            message = "❌ Warning: Peak luminance must be positive"
            if strict_mode:
                print(message)
                return False
            else:
                print(message)

        if peak_luminance < black_level:
            message = "❌ Warning: Peak luminance must be greater than black level"
            if strict_mode:
                print(message)
                return False
            else:
                print(message)

    # Check contrast ratio (skipped for non-positive black level, which
    # the luminance check above already reported).
    black_level = float(config["display"]["led_panel"]["luminance"]["black_level"])
    if validation_config.get("check_contrast", True) and black_level > 0:
        peak_luminance = float(
            config["display"]["led_panel"]["luminance"]["peak_luminance"]
        )
        contrast_ratio = peak_luminance / black_level
        min_contrast = validation_config.get("min_contrast_ratio", 100)
        max_contrast = validation_config.get("max_contrast_ratio", 10000)

        if not (min_contrast <= contrast_ratio <= max_contrast):
            message = (
                f"❌ Warning: Contrast ratio {contrast_ratio:.0f}:1 outside "
                f"acceptable range [{min_contrast}, {max_contrast}]"
            )
            if strict_mode:
                print(message)
                return False
            else:
                print(message)

    # Check processor signal-contract state (§spec:signal-contract): the
    # config is only valid while the processor holds the recorded state,
    # so a missing record leaves nothing to restore or audit.
    if validation_config.get("check_processor_state", True):
        processor_state = config["display"]["led_processor"].get("configuration", {})
        for field, meaning in (
            ("intensity", "locked processor intensity"),
            ("processing_disabled", "color processing / dynamic features state"),
        ):
            if field in processor_state:
                print(f"✓ Processor {field} recorded: {processor_state[field]}")
                continue
            message = (
                f"❌ Warning: 'display.led_processor.configuration.{field}' "
                f"({meaning}) is missing — the signal contract cannot be "
                f"recorded in the config metadata"
            )
            if strict_mode:
                print(message)
                return False
            else:
                print(message)

    # Advisory: SDR EOTF usage with high brightness displays.
    # Never fatal, even in strict mode: an SDR-gamma-only front end driving
    # a bright wall is the reference use case (§req:problem-statement), and
    # §spec:signal-contract prefers gamma 2.4 on SDR-only links. Strict mode
    # escalates measurement-plausibility failures, not encoding preferences.
    if validation_config.get("warn_on_sdr_eotf", True):
        peak_luminance = config["display"]["led_panel"]["luminance"]["peak_luminance"]
        eotf_type = config["display"]["led_processor"]["configuration"]["eotf"]["type"]
        sdr_threshold = validation_config.get("sdr_warning_threshold", 400.0)

        if peak_luminance > sdr_threshold:
            if eotf_type == "GAMMA":
                print(
                    f"Note: GAMMA EOTF with high brightness "
                    f"({peak_luminance} cd/m²) - PQ carries this range with "
                    f"less quantization where the processor supports it"
                )
            elif eotf_type == "HLG":
                message = (
                    f"⚠️  Warning: HLG EOTF limited to ~1000 cd/m² but "
                    f"display peaks at {peak_luminance} cd/m²"
                )
                if strict_mode:
                    print(message)
                    return False
                else:
                    print(message)
            elif eotf_type == "PQ":
                print(
                    f"✓ Using PQ EOTF with high brightness display "
                    f"({peak_luminance} cd/m²) - appropriate for HDR"
                )

    print("✓ Configuration validation passed")
    return True


def generate_output_filename(
    config: Dict[str, Any], characterization: DisplayCharacterization
) -> str:
    """Generate output filename if not specified in config."""

    ocio_config = config.get("ocio", {})

    # Use specified output config if provided
    if "output_config" in ocio_config:
        return ocio_config["output_config"]

    # Generate filename from display name
    display_name = characterization.name.lower().replace(" ", "_").replace("-", "_")
    return f"{display_name}_config.ocio"


def create_base_ocio_config(config: Dict[str, Any]) -> "OCIO.Config":
    """Create base OCIO configuration using ocio:// scheme."""

    base_config = config.get("ocio", {}).get("base_config", {})
    config_type = base_config.get("type", "studio")
    config_version = base_config.get("config_version", "v2.1.0")
    aces_version = base_config.get("aces_version", "v1.3")
    ocio_version = base_config.get("ocio_version", "v2.3")

    # Construct the ocio:// URL based on configuration
    ocio_url = (
        f"ocio://{config_type}-config-{config_version}_aces-"
        f"{aces_version}_ocio-{ocio_version}"
    )

    print(f"Loading base OCIO config: {ocio_url}")

    try:
        # Load the base configuration using ocio:// scheme
        ocio_config = OCIO.Config.CreateFromFile(ocio_url)
        print("✓ Successfully loaded base configuration")
        return ocio_config

    except Exception as e:
        print(f"❌ Error loading base configuration: {e}")
        print(f"   Attempted URL: {ocio_url}")
        print("   Available configurations:")
        print("     - ocio://studio-config-v4.0.0_aces-v2.0_ocio-v2.5")
        print("     - ocio://studio-config-v2.1.0_aces-v1.3_ocio-v2.3")
        print("     - ocio://aces-config-v2.1.0_aces-v1.3_ocio-v2.3")
        print("   Please check your configuration parameters.")
        raise


def main():
    print("=== OCIO Display Generator ===")
    config_file = "display_config.yaml"
    print(f"Loading configuration from '{config_file}'...")
    config = load_config_from_yaml(config_file)
    print("Validating configuration data...")
    if not validate_config_data(config):
        print("❌ Configuration validation failed. Please check your measurements.")
        sys.exit(1)
    print("Creating display characterization...")
    characterization = create_characterization_from_config(config)
    print(f"\nDisplay: {characterization.name}")
    print(f"Peak luminance: {characterization.peak_luminance} cd/m²")
    print(f"Black level: {characterization.black_level} cd/m²")
    print(f"Contrast ratio: {characterization.contrast_ratio:.0f}:1")
    print(f"EOTF: {characterization.eotf_type}")
    intensity = characterization.processor_intensity
    print(
        f"Processor intensity: "
        f"{intensity if intensity is not None else '(not recorded)'}"
    )
    print(
        f"Color processing / dynamic features: "
        f"{describe_processing_state(characterization.processor_processing_disabled)}"
    )
    print(f"White point policy: {characterization.white_point_policy}")
    # VP Radiometric settings are generation decisions, not measurements,
    # so they live under ocio: (§spec:view-transform).
    vp_settings = config.get("ocio", {}).get("vp_radiometric", {})
    try:
        nits_anchor = float(vp_settings.get("nits_anchor", DEFAULT_NITS_ANCHOR))
    except (TypeError, ValueError):
        print("❌ Error: 'ocio.vp_radiometric.nits_anchor' must be a number")
        sys.exit(1)
    overflow_policy = vp_settings.get("overflow_policy", DEFAULT_OVERFLOW_POLICY)
    print(f"VP Radiometric nits anchor: {nits_anchor} cd/m²")
    print(f"VP Radiometric overflow policy: {overflow_policy}")
    output_config_path = generate_output_filename(config, characterization)
    try:
        print("\nCreating base OCIO config...")
        ocio_config_obj = create_base_ocio_config(config)
        scene_reference, display_reference = derive_reference_spaces(ocio_config_obj)
        print(f"Scene reference space: {scene_reference}")
        print(f"Display reference space: {display_reference}")
        validate_display_reference(display_reference)
        cs = create_display_colorspace_from_characterization(characterization)
        display_name = register_display(
            ocio_config_obj,
            cs,
            characterization,
            nits_anchor=nits_anchor,
            overflow_policy=overflow_policy,
        )
        try:
            ocio_config_obj.validate()
        except Exception as exc:
            raise RuntimeError(
                f"Generated config failed OCIO validation: {exc}"
            ) from exc
        with open(output_config_path, "w", encoding="utf-8") as f:
            f.write(ocio_config_obj.serialize())
        print("\n✅ Successfully created OCIO config!")
        print(f"   Output file: {output_config_path}")
        print(f"\nRegistered display: {display_name}")
        default_view = ocio_config_obj.getDefaultView(display_name)
        for view in ocio_config_obj.getViews(display_name):
            marker = " (default)" if str(view) == default_view else ""
            print(f"   View: {view}{marker}")
        print(
            f"   {VP_RADIOMETRIC_VIEW}: anchor {nits_anchor} cd/m², "
            f"overflow policy {overflow_policy}"
        )
        print(
            f"   {ACES2_VIEW}: output transform limited to measured "
            f"peak {characterization.peak_luminance} cd/m² and "
            f"measured primaries/white"
        )
        print("\n📋 Usage Instructions:")
        print(
            f"1. Set OCIO environment variable: export OCIO="
            f"{os.path.abspath(output_config_path)}"
        )
        print(
            f"2. In your application, select display '{display_name}' "
            f"with view '{VP_RADIOMETRIC_VIEW}'"
        )
    except Exception as e:
        print(f"❌ Error creating OCIO config: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


# Example usage for single display characterization
if __name__ == "__main__":
    main()
