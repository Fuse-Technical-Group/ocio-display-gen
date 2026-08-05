# OCIODisplayGen.py
# This script creates a custom display colorspace for a high dynamic range display with
# non-standard native primaries, and appends it to an existing OCIO config
# It uses the colour-science library to create the colorspace and the PyOpenColorIO
# library to create the OCIO config

import hashlib
import hmac
import os
import re
import sys
from typing import Any, Dict, NamedTuple, Optional, Tuple, cast

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


def unknown_policy_message(kind: str, value: str, valid: Tuple[str, ...]) -> str:
    """Shared policy-enum failure message, so the validator's warnings
    and the transform builders' errors cannot drift."""
    return f"Unknown {kind} '{value}'; valid values: {', '.join(valid)}"


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
            unknown_policy_message(
                "white point policy", white_point_policy, WHITE_POINT_POLICIES
            )
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
            unknown_policy_message(
                "overflow policy", overflow_policy, OVERFLOW_POLICIES
            )
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


# Input loading (§spec:characterization-model): a human-authored
# decisions file plus the machine-format measurements artifact its
# promotion pointer names, consumed together.
DECISIONS_FILE = "decisions.yaml"

# Generator version recorded in provenance metadata (§spec:provenance).
# A module constant kept equal to pyproject's [project] version — the
# project is run as a script (`uv run ./OCIODisplayGen.py`), not
# installed as a distribution, so importlib.metadata has no package to
# query. test_provenance.py pins this to pyproject.toml.
GENERATOR_VERSION = "0.1.0"

# sha256 hex digest: 64 lowercase hex characters after normalization.
SHA256_HEX_PATTERN = re.compile(r"[0-9a-f]{64}")

def reject_control_characters(value: str, what: str) -> str:
    """
    Refuse values destined for the generated config's description when
    they contain unprintable characters (§spec:provenance). A newline —
    or a Unicode line separator like U+2028, which OCIO's serializer
    emits as a physical newline — could forge or garble the greppable
    Provenance: block. str.isprintable() rejects all C0/C1 controls,
    Unicode line/paragraph separators, and format characters
    (including bidi overrides).

    Raises:
        ValueError: When value contains an unprintable character.
    """
    if not value.isprintable():
        raise ValueError(
            f"{what} must not contain unprintable characters (controls, "
            f"line separators, format characters) — it is recorded "
            f"verbatim in the generated config's description"
        )
    return value


class Provenance(NamedTuple):
    """Input identities recorded in the generated config
    (§spec:provenance). File paths are as written in the decisions
    file / as given to the loader — never absolutized, so recorded
    metadata stays byte-deterministic across checkouts."""

    decisions_file: str
    decisions_sha256: str
    measurements_file: str
    measurements_sha256: str


class PromotionPointer(NamedTuple):
    """The decisions file's validated promotion pointer
    (§spec:provenance): artifact path as written, the recorded digest
    normalized to lowercase, and the path resolved against the
    decisions file's directory."""

    file: str
    sha256: str
    artifact_path: str


def read_file_bytes(path: str, role: str) -> bytes:
    """
    A file's bytes, read once — callers hash and parse the same bytes,
    so the parsed content is exactly what the hash attests
    (§spec:provenance).

    Raises:
        ValueError: For an unreadable file.
    """
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise ValueError(f"{role} '{path}' is not readable: {e}") from e


def enforce_promotion_hash(
    pointer: PromotionPointer, artifact_bytes: bytes, decisions_path: str
) -> str:
    """
    Enforce the promotion pointer's recorded hash over the artifact
    bytes (§spec:provenance). The comparison uses hmac.compare_digest
    as a cheap constant-time habit; the threat model is error and
    drift, not adversaries.

    Returns:
        The artifact's actual sha256 hex digest.

    Raises:
        ValueError: On mismatch — the artifact is not the measurement
            of record and generation must refuse.
    """
    actual = hashlib.sha256(artifact_bytes).hexdigest()
    if not hmac.compare_digest(actual, pointer.sha256):
        raise ValueError(
            f"Measurements artifact '{pointer.file}' does not match the "
            f"promotion pointer in '{decisions_path}': recorded sha256 "
            f"{pointer.sha256}, actual sha256 {actual}. The artifact is "
            f"not the measurement of record — refusing to generate "
            f"(§spec:provenance)"
        )
    return actual


def provenance_description(provenance: Provenance) -> str:
    """Greppable provenance lines for the config's top-level
    description (§spec:provenance)."""
    return (
        f"Provenance: decisions sha256={provenance.decisions_sha256} "
        f"({provenance.decisions_file})\n"
        f"Provenance: measurements sha256={provenance.measurements_sha256} "
        f"({provenance.measurements_file})\n"
        f"Provenance: generator ociodisplaygen {GENERATOR_VERSION}"
    )


def record_provenance(
    config: "OCIO.Config",
    provenance: Provenance,
    show_description: Optional[str] = None,
) -> None:
    """Append the show identity (when decided) and provenance lines to
    the config's top-level description, preserving the base config's
    own description (§spec:provenance)."""
    base = config.getDescription().rstrip()
    lines = provenance_description(provenance)
    if show_description is not None:
        if not isinstance(show_description, str):
            raise ValueError(
                f"Show description must be a string, got "
                f"{type(show_description).__name__}"
            )
        reject_control_characters(show_description, "Show description")
        if show_description:
            lines = f"Show: {show_description}\n{lines}"
    config.setDescription(f"{base}\n\n{lines}" if base else lines)


def parse_yaml_mapping(data: bytes, path: str, role: str) -> Dict[str, Any]:
    """
    Parse YAML bytes that must be a mapping.

    Args:
        data: File bytes (from read_file_bytes)
        path: Source path for error messages
        role: Human-readable role for error messages
            (e.g. "Decisions file")

    Raises:
        ValueError: For invalid YAML or content that is not a mapping.
    """
    try:
        parsed = yaml.safe_load(data)
    except yaml.YAMLError as e:
        raise ValueError(f"{role} '{path}' is not valid YAML: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError(f"{role} '{path}' must be a YAML mapping")
    return cast(Dict[str, Any], parsed)


def resolve_measurements_pointer(
    decisions: Dict[str, Any], decisions_path: str
) -> PromotionPointer:
    """
    Validate the decisions file's promotion pointer and resolve it to
    the measurements artifact of record (§spec:provenance).

    The pointer is `measurements: {file, sha256}`; `file` resolves
    relative to the decisions file's directory. This validates the
    pointer's structure (keys present, well-formed digest); hash
    enforcement is enforce_promotion_hash.

    Raises:
        ValueError: For a missing pointer, missing pointer keys, or a
            malformed recorded digest.
    """
    pointer = decisions.get("measurements")
    if not isinstance(pointer, dict):
        raise ValueError(
            f"Decisions file '{decisions_path}' has no 'measurements' "
            "promotion pointer — expected 'measurements: {file, sha256}' "
            "naming the measurements artifact of record (§spec:provenance)"
        )
    missing = [key for key in ("file", "sha256") if key not in pointer]
    if missing:
        raise ValueError(
            f"Decisions file '{decisions_path}' promotion pointer is "
            f"missing {', '.join(repr(key) for key in missing)} — expected "
            "'measurements: {file, sha256}' (§spec:provenance)"
        )
    pointer_file = str(pointer["file"])
    reject_control_characters(
        pointer_file, f"Decisions file '{decisions_path}' promotion pointer file"
    )
    # The pointer path is recorded verbatim in shipped config metadata:
    # absolute paths and traversal tie the audit trail to one machine's
    # directory layout and leak it into distributed configs.
    if os.path.isabs(pointer_file) or os.pardir in re.split(r"[\\/]", pointer_file):
        raise ValueError(
            f"Decisions file '{decisions_path}' promotion pointer file "
            f"'{pointer_file}' must be a relative path inside the decisions "
            f"file's directory (no absolute paths, no '..') — the pointer "
            f"is recorded in shipped config metadata and must stay "
            f"portable (§spec:provenance)"
        )
    recorded_raw = pointer["sha256"]
    if not isinstance(recorded_raw, str):
        raise ValueError(
            f"Decisions file '{decisions_path}' promotion pointer sha256 "
            f"must be a quoted string, got {type(recorded_raw).__name__} — "
            f"YAML parses an unquoted digit-only digest as a number, "
            f"corrupting it (§spec:provenance)"
        )
    recorded = recorded_raw.lower()
    if not SHA256_HEX_PATTERN.fullmatch(recorded):
        raise ValueError(
            f"Decisions file '{decisions_path}' promotion pointer sha256 "
            f"'{recorded_raw}' is malformed — expected a 64-character "
            f"hex sha256 digest of '{pointer_file}' (§spec:provenance)"
        )
    return PromotionPointer(
        file=pointer_file,
        sha256=recorded,
        artifact_path=os.path.join(
            os.path.dirname(os.path.abspath(decisions_path)), pointer_file
        ),
    )


def load_inputs(
    decisions_path: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], Provenance]:
    """
    Load the decisions file and the measurements artifact it promotes,
    enforcing the promotion hash (§spec:provenance). Each file is read
    once; the bytes that are hashed are the bytes that are parsed.

    Returns:
        (decisions, measurements, provenance).

    Raises:
        ValueError: For an unreadable decisions file, a missing or
            malformed promotion pointer, an unreadable artifact, or a
            promotion-hash mismatch.
    """
    decisions_bytes = read_file_bytes(decisions_path, "Decisions file")
    decisions = parse_yaml_mapping(decisions_bytes, decisions_path, "Decisions file")
    pointer = resolve_measurements_pointer(decisions, decisions_path)
    artifact_bytes = read_file_bytes(pointer.artifact_path, "Measurements artifact")
    measurements_sha256 = enforce_promotion_hash(
        pointer, artifact_bytes, decisions_path
    )
    measurements = parse_yaml_mapping(
        artifact_bytes, pointer.artifact_path, "Measurements artifact"
    )
    provenance = Provenance(
        decisions_file=decisions_path,
        decisions_sha256=hashlib.sha256(decisions_bytes).hexdigest(),
        measurements_file=pointer.file,
        measurements_sha256=measurements_sha256,
    )
    return decisions, measurements, provenance


def create_characterization(
    decisions: Dict[str, Any], measurements: Dict[str, Any]
) -> DisplayCharacterization:
    """
    Build a DisplayCharacterization from the two inputs: measured
    values from the measurements artifact; naming, EOTF intent, white
    point policy, and processor lockdown from the decisions file
    (§spec:characterization-model).
    """
    show = decisions["show"]

    # Display name composes from panel and processor identity
    panel = show["led_panel"]
    panel_name = f"{panel['manufacturer']} {panel['model']} ({panel['version']})"

    processor = show["led_processor"]
    processor_name = (
        f"{processor['manufacturer']} {processor['model']} ({processor['version']})"
    )

    char = DisplayCharacterization(f"{panel_name} + {processor_name}")

    # Measured colorimetry from the artifact
    colorimetry = measurements["colorimetry"]
    primaries = colorimetry["primaries"]
    char.primaries = {
        "red": tuple(primaries["red"]),
        "green": tuple(primaries["green"]),
        "blue": tuple(primaries["blue"]),
    }
    char.white_point = tuple(colorimetry["white_point"])

    # Measured luminance from the artifact
    luminance = measurements["luminance"]
    char.black_level = luminance["black_level"]
    char.peak_luminance = luminance["peak_luminance"]
    # Guard the division: non-strict validation lets a zero black level
    # through with a warning.
    if char.black_level > 0:
        char.contrast_ratio = char.peak_luminance / char.black_level
    else:
        char.contrast_ratio = float("inf")

    # Intended signal contract (§spec:signal-contract) is a decision:
    # the lockdown state the config is valid for, distinct from the
    # artifact's processor_state snapshot (what was read at
    # measurement time).
    contract = decisions.get("signal_contract")
    if not isinstance(contract, dict) or "eotf" not in contract:
        raise ValueError("Decisions file must contain a 'signal_contract.eotf' section")
    eotf = contract["eotf"]
    char.eotf_type = eotf["type"]
    char.gamma_value = eotf.get("gamma_value", 2.4)

    # Processor lockdown state. Optional: validate_inputs warns (or
    # fails in strict mode) when absent.
    intensity = contract.get("intensity")
    char.processor_intensity = None if intensity is None else str(intensity)
    char.processor_processing_disabled = contract.get("processing_disabled")

    # White point policy is a generation decision, not a measurement, so
    # it lives under ocio:. Validated at matrix-build time.
    char.white_point_policy = decisions.get("ocio", {}).get(
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


def validate_decisions_data(
    decisions: Dict[str, Any],
    validation_config: Dict[str, Any],
    strict_mode: bool,
) -> bool:
    """
    Validate the human decisions: policy enums and the intended
    processor lockdown state (§spec:signal-contract). Plausibility of
    measured values belongs to validate_measurements_data.
    """
    ocio_settings = decisions.get("ocio", {})

    # Policy enums (§spec:white-point, §spec:view-transform). Non-strict
    # mode only defers the failure: the transform builders raise on the
    # same values at generation time.
    white_point_policy = ocio_settings.get("white_point_policy", "adapted")
    if white_point_policy not in WHITE_POINT_POLICIES:
        print(
            "❌ Warning: "
            + unknown_policy_message(
                "white point policy", white_point_policy, WHITE_POINT_POLICIES
            )
        )
        if strict_mode:
            return False
    else:
        print(f"✓ White point policy: {white_point_policy}")

    overflow_policy = ocio_settings.get("vp_radiometric", {}).get(
        "overflow_policy", DEFAULT_OVERFLOW_POLICY
    )
    if overflow_policy not in OVERFLOW_POLICIES:
        print(
            "❌ Warning: "
            + unknown_policy_message(
                "overflow policy", overflow_policy, OVERFLOW_POLICIES
            )
        )
        if strict_mode:
            return False
    else:
        print(f"✓ Overflow policy: {overflow_policy}")

    # Check processor signal-contract state (§spec:signal-contract): the
    # config is only valid while the processor holds the recorded state,
    # so a missing record leaves nothing to restore or audit.
    if validation_config.get("check_processor_state", True):
        contract = decisions.get("signal_contract", {})
        for field, meaning in (
            ("intensity", "locked processor intensity"),
            ("processing_disabled", "color processing / dynamic features state"),
        ):
            if field in contract:
                print(f"✓ Processor {field} recorded: {contract[field]}")
                continue
            print(
                f"❌ Warning: 'signal_contract.{field}' "
                f"({meaning}) is missing — the signal contract cannot be "
                f"recorded in the config metadata"
            )
            if strict_mode:
                return False

    return True


def validate_measurements_data(
    measurements: Dict[str, Any],
    validation_config: Dict[str, Any],
    strict_mode: bool,
) -> bool:
    """
    Validate measurement plausibility from the measurements artifact:
    chromaticity ranges, white point CCT/duv, luminance, contrast, and
    the EOTF-vs-brightness advisory (§spec:characterization-model).
    """
    # Check primaries
    if validation_config.get("check_primaries", True):
        print(
            "Validating display primaries: basic chromaticity range check "
            "(not a true spectral locus test)..."
        )
        for color, coords in measurements["colorimetry"]["primaries"].items():
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
        x, y = measurements["colorimetry"]["white_point"]

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
        black_level = measurements["luminance"]["black_level"]
        peak_luminance = measurements["luminance"]["peak_luminance"]

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
    black_level = float(measurements["luminance"]["black_level"])
    if validation_config.get("check_contrast", True) and black_level > 0:
        peak_luminance = float(measurements["luminance"]["peak_luminance"])
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

    # Advisory: SDR EOTF usage with high brightness displays, against
    # the artifact's processor-state snapshot (what was read at
    # measurement time). Never fatal, even in strict mode: an
    # SDR-gamma-only front end driving a bright wall is the reference
    # use case (§req:problem-statement), and §spec:signal-contract
    # prefers gamma 2.4 on SDR-only links. Strict mode escalates
    # measurement-plausibility failures, not encoding preferences.
    if validation_config.get("warn_on_sdr_eotf", True):
        peak_luminance = measurements["luminance"]["peak_luminance"]
        eotf_type = measurements.get("processor_state", {}).get("eotf", {}).get("type")
        sdr_threshold = validation_config.get("sdr_warning_threshold", 400.0)

        if eotf_type is not None and peak_luminance > sdr_threshold:
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

    return True


def validate_inputs(decisions: Dict[str, Any], measurements: Dict[str, Any]) -> bool:
    """
    Validate both inputs along the human/machine line
    (§spec:characterization-model): decisions checks against the
    decisions file, plausibility checks against the measurements
    artifact. Strict mode comes from the decisions file.
    """
    validation_config = load_validation_settings()
    strict_mode = decisions.get("validation", {}).get("strict_mode", False)

    if not validate_decisions_data(decisions, validation_config, strict_mode):
        return False
    if not validate_measurements_data(measurements, validation_config, strict_mode):
        return False

    print("✓ Configuration validation passed")
    return True


def generate_output_filename(
    decisions: Dict[str, Any], characterization: DisplayCharacterization
) -> str:
    """Generate output filename if not specified in the decisions file."""

    ocio_config = decisions.get("ocio", {})

    # Use specified output config if provided
    if "output_config" in ocio_config:
        return ocio_config["output_config"]

    # Generate filename from display name
    display_name = characterization.name.lower().replace(" ", "_").replace("-", "_")
    return f"{display_name}_config.ocio"


def create_base_ocio_config(decisions: Dict[str, Any]) -> "OCIO.Config":
    """Create base OCIO configuration using ocio:// scheme."""

    base_config = decisions.get("ocio", {}).get("base_config", {})
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
    print(f"Loading decisions from '{DECISIONS_FILE}'...")
    try:
        decisions, measurements, provenance = load_inputs(DECISIONS_FILE)
    except ValueError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    print(
        f"✓ Loaded measurements artifact '{provenance.measurements_file}' "
        f"(promotion hash verified)"
    )
    print("Validating configuration data...")
    if not validate_inputs(decisions, measurements):
        print("❌ Configuration validation failed. Please check your inputs.")
        sys.exit(1)
    print("Creating display characterization...")
    characterization = create_characterization(decisions, measurements)
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
    vp_settings = decisions.get("ocio", {}).get("vp_radiometric", {})
    try:
        nits_anchor = float(vp_settings.get("nits_anchor", DEFAULT_NITS_ANCHOR))
    except (TypeError, ValueError):
        print("❌ Error: 'ocio.vp_radiometric.nits_anchor' must be a number")
        sys.exit(1)
    overflow_policy = vp_settings.get("overflow_policy", DEFAULT_OVERFLOW_POLICY)
    print(f"VP Radiometric nits anchor: {nits_anchor} cd/m²")
    print(f"VP Radiometric overflow policy: {overflow_policy}")
    output_config_path = generate_output_filename(decisions, characterization)
    try:
        print("\nCreating base OCIO config...")
        ocio_config_obj = create_base_ocio_config(decisions)
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
        record_provenance(
            ocio_config_obj,
            provenance,
            decisions.get("show", {}).get("description"),
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
        print("\nProvenance recorded in the config description:")
        for line in provenance_description(provenance).splitlines():
            print(f"   {line}")
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
