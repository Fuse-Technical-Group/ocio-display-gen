# OCIODisplayGen.py
# This script creates a custom display colorspace for a high dynamic range display with
# non-standard native primaries, and appends it to an existing OCIO config
# It uses the colour-science library to create the colorspace and the PyOpenColorIO
# library to create the OCIO config

import hashlib
import hmac
import ntpath
import os
import posixpath
import re
import struct
import zlib
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, cast

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
    # ACES 2.0 fixed functions accept only integral peak_luminance; a
    # measured peak is never integral. Quantize here — the one source of
    # fixed-function parameterization — so the tone scale rounds to the
    # nearest nit (far below instrument repeatability) while the
    # radiometric anchor elsewhere keeps the measured value exactly.
    return (
        float(round(characterization.peak_luminance)),
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


# Processor features that adapt to surrounding frames. These break the
# memoryless code-to-light assumption a characterization rests on, so
# the config is valid only while they are off. Static per-pixel
# processing (Brompton's dark-magic, puretone, extended-bit-depth) is
# part of the display's transfer function and is measured, not excluded
# — see the signal_contract.processing block in the show manifest.
CONTENT_DEPENDENT_FEATURES = ("overdrive",)


def _processing_disabled(contract: dict) -> Optional[bool]:
    """Whether no content-dependent processing is declared enabled.

    Reads the per-feature `signal_contract.processing` block. Falls back
    to the superseded `processing_disabled` boolean so manifests written
    before the split still load.
    """
    processing = contract.get("processing")
    if isinstance(processing, dict):
        return not any(
            bool(processing.get(feature)) for feature in CONTENT_DEPENDENT_FEATURES
        )
    return contract.get("processing_disabled")


def describe_processing_state(disabled: Optional[bool]) -> str:
    """Processor content-dependent-processing state for metadata and
    console output (§spec:signal-contract).

    Static per-pixel processing may be on and is measured; what this
    reports is whether anything in the path adapts to the content."""
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


def _drive_space_matrix(
    characterization: DisplayCharacterization,
    chromatic_adaptation_transform: str,
) -> npt.NDArray[np.float64]:
    """Display-reference XYZ → wall drive space, where 1.0 is full
    drive.

    The VP Radiometric view and the probe predictor's inverse are the
    same matrix by construction: predictions describe the rendering the
    config performs only while both sides agree, and nothing else would
    catch them diverging — the full-drive patches would quietly stop
    landing on the measured gamut boundary.
    """
    return np.asarray(
        np.diag([REFERENCE_LUMINANCE / characterization.peak_luminance] * 3 + [1.0])
        @ create_display_xyz_to_native_matrix(
            characterization, chromatic_adaptation_transform
        ),
        dtype=np.float64,
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
    drive = _drive_space_matrix(characterization, chromatic_adaptation_transform)
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
# show manifest plus the machine-format measurements artifact its
# promotion pointer names, consumed together.
SHOW_MANIFEST_FILE = "show_manifest.yaml"
VALIDATION_SETTINGS_FILE = "validation_settings.yaml"

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
    (§spec:provenance). File paths are as written in the show manifest's
    file / as given to the loader — never absolutized, so recorded
    metadata stays byte-deterministic across checkouts."""

    show_manifest_file: str
    show_manifest_sha256: str
    measurements_file: str
    measurements_sha256: str
    # The link the display was measured over, as the artifact states it.
    # A config describes the display *as driven*, and the same display
    # measured over a 10-bit narrow YCbCr link and over a 12-bit full
    # RGB one is not the same measurement -- a matrix or range the
    # processor reads differently moves the primaries by percent, not
    # by rounding. None for a `measurements/1` artifact, which predates
    # the block and cannot say.
    measurements_wire: Optional[str] = None


class PromotionPointer(NamedTuple):
    """The show manifest's validated promotion pointer
    (§spec:provenance): artifact path as written, the recorded digest
    normalized to lowercase, and the path resolved against the
    show manifest's directory."""

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


def file_sha256(path: str, role: str) -> str:
    """The sha256 hex digest of a file's bytes (§spec:provenance).

    Raises:
        ValueError: When the file is unreadable.
    """
    return hashlib.sha256(read_file_bytes(path, role)).hexdigest()


def enforce_promotion_hash(
    pointer: PromotionPointer, artifact_bytes: bytes, manifest_path: str
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
            f"promotion pointer in '{manifest_path}': recorded sha256 "
            f"{pointer.sha256}, actual sha256 {actual}. The artifact is "
            f"not the measurement of record — refusing to generate "
            f"(§spec:provenance)"
        )
    return actual


def provenance_description(provenance: Provenance) -> str:
    """Greppable provenance lines for the config's top-level
    description (§spec:provenance)."""
    return (
        f"Provenance: show-manifest sha256={provenance.show_manifest_sha256} "
        f"({provenance.show_manifest_file})\n"
        f"Provenance: measurements sha256={provenance.measurements_sha256} "
        f"({provenance.measurements_file})\n"
        f"{_wire_line(provenance)}"
        f"Provenance: generator ociodisplaygen {GENERATOR_VERSION}"
    )


def _wire_line(provenance: Provenance) -> str:
    """The measured link, when the artifact states one.

    Greppable and on its own line, because the question it answers --
    "is this config valid for the link my show delivers over?" -- is
    asked of a config file long after the session that produced it.
    """
    if not provenance.measurements_wire:
        return ""
    return f"Provenance: measured-over {provenance.measurements_wire}\n"


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
            (e.g. "Show manifest")

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
    manifest: Dict[str, Any], manifest_path: str
) -> PromotionPointer:
    """
    Validate the show manifest's promotion pointer and resolve it to
    the measurements artifact of record (§spec:provenance).

    The pointer is `measurements: {file, sha256}`; `file` resolves
    relative to the show manifest's directory. This validates the
    pointer's structure (keys present, well-formed digest); hash
    enforcement is enforce_promotion_hash.

    Raises:
        ValueError: For a missing pointer, missing pointer keys, or a
            malformed recorded digest.
    """
    pointer = manifest.get("measurements")
    if not isinstance(pointer, dict):
        raise ValueError(
            f"Show manifest '{manifest_path}' has no 'measurements' "
            "promotion pointer — expected 'measurements: {file, sha256}' "
            "naming the measurements artifact of record (§spec:provenance)"
        )
    missing = [key for key in ("file", "sha256") if key not in pointer]
    if missing:
        raise ValueError(
            f"Show manifest '{manifest_path}' promotion pointer is "
            f"missing {', '.join(repr(key) for key in missing)} — expected "
            "'measurements: {file, sha256}' (§spec:provenance)"
        )
    pointer_file = str(pointer["file"])
    reject_control_characters(
        pointer_file, f"Show manifest '{manifest_path}' promotion pointer file"
    )
    # The pointer path is recorded verbatim in shipped config metadata:
    # absolute paths and traversal tie the audit trail to one machine's
    # directory layout and leak it into distributed configs.
    if os.path.isabs(pointer_file) or os.pardir in re.split(r"[\\/]", pointer_file):
        raise ValueError(
            f"Show manifest '{manifest_path}' promotion pointer file "
            f"'{pointer_file}' must be a relative path inside the show manifest's "
            f"file's directory (no absolute paths, no '..') — the pointer "
            f"is recorded in shipped config metadata and must stay "
            f"portable (§spec:provenance)"
        )
    recorded_raw = pointer["sha256"]
    if not isinstance(recorded_raw, str):
        raise ValueError(
            f"Show manifest '{manifest_path}' promotion pointer sha256 "
            f"must be a quoted string, got {type(recorded_raw).__name__} — "
            f"YAML parses an unquoted digit-only digest as a number, "
            f"corrupting it (§spec:provenance)"
        )
    recorded = recorded_raw.lower()
    if not SHA256_HEX_PATTERN.fullmatch(recorded):
        raise ValueError(
            f"Show manifest '{manifest_path}' promotion pointer sha256 "
            f"'{recorded_raw}' is malformed — expected a 64-character "
            f"hex sha256 digest of '{pointer_file}' (§spec:provenance)"
        )
    return PromotionPointer(
        file=pointer_file,
        sha256=recorded,
        artifact_path=os.path.join(
            os.path.dirname(os.path.abspath(manifest_path)), pointer_file
        ),
    )


def describe_wire(measurements: Dict[str, Any]) -> Optional[str]:
    """The artifact's wire encoding, as one greppable phrase.

    Reads what display-measure wrote rather than deriving anything: the
    link is the session's declaration, held against the processor where
    the processor could answer and measured by the range probe where it
    could not.
    """
    block = measurements.get("wire_encoding")
    if not isinstance(block, dict):
        return None
    layout = str(block.get("layout", "?"))
    depth = block.get("bit_depth", "?")
    sampling = str(block.get("sampling", "?"))
    levels = str(block.get("levels", "?"))
    if sampling == "rgb":
        return f"{layout} {depth}-bit RGB {levels}"
    matrix = str(block.get("matrix", "?"))
    subsampling = str(block.get("subsampling", "?"))
    return f"{layout} {depth}-bit {matrix} {subsampling} {levels}"


def load_inputs(
    manifest_path: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], Provenance]:
    """
    Load the show manifest and the measurements artifact it promotes,
    enforcing the promotion hash (§spec:provenance). Each file is read
    once; the bytes that are hashed are the bytes that are parsed.

    Returns:
        (manifest, measurements, provenance).

    Raises:
        ValueError: For an unreadable show manifest, a missing or
            malformed promotion pointer, an unreadable artifact, or a
            promotion-hash mismatch.
    """
    manifest_bytes = read_file_bytes(manifest_path, "Show manifest")
    manifest = parse_yaml_mapping(manifest_bytes, manifest_path, "Show manifest")
    pointer = resolve_measurements_pointer(manifest, manifest_path)
    artifact_bytes = read_file_bytes(pointer.artifact_path, "Measurements artifact")
    measurements_sha256 = enforce_promotion_hash(pointer, artifact_bytes, manifest_path)
    measurements = parse_yaml_mapping(
        artifact_bytes, pointer.artifact_path, "Measurements artifact"
    )
    provenance = Provenance(
        show_manifest_file=manifest_path,
        show_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        measurements_file=pointer.file,
        measurements_sha256=measurements_sha256,
        measurements_wire=describe_wire(measurements),
    )
    return manifest, measurements, provenance


def create_characterization(
    manifest: Dict[str, Any], measurements: Dict[str, Any]
) -> DisplayCharacterization:
    """
    Build a DisplayCharacterization from the two inputs: measured
    values from the measurements artifact; naming, EOTF intent, white
    point policy, and processor lockdown from the show manifest
    (§spec:characterization-model).
    """
    show = manifest["show"]

    # Display name composes from panel and processor identity
    panel = show["led_panel"]
    panel_name = f"{panel['manufacturer']} {panel['model']} ({panel['version']})"

    processor = show["led_processor"]
    processor_name = (
        f"{processor['manufacturer']} {processor['model']} ({processor['version']})"
    )

    char = DisplayCharacterization(f"{panel_name} + {processor_name}")

    # Refuse an artifact missing what a config reads, before anything is
    # built from it. The requirement is stated in `requires`, against the
    # measurement blocks an artifact records (§spec:characterization-model).
    from ocio_display_gen.requires import check as check_blocks

    check_blocks(measurements)

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
    contract = manifest.get("signal_contract")
    if not isinstance(contract, dict) or "eotf" not in contract:
        raise ValueError("Show manifest must contain a 'signal_contract.eotf' section")
    eotf = contract["eotf"]
    char.eotf_type = eotf["type"]
    char.gamma_value = eotf.get("gamma_value", 2.4)

    # Processor lockdown state. Optional: validate_inputs warns (or
    # fails in strict mode) when absent.
    intensity = contract.get("intensity")
    char.processor_intensity = None if intensity is None else str(intensity)
    char.processor_processing_disabled = _processing_disabled(contract)

    # White point policy is a generation decision, not a measurement, so
    # it lives under ocio:. Validated at matrix-build time.
    char.white_point_policy = manifest.get("ocio", {}).get(
        "white_point_policy", "adapted"
    )
    return char


def load_validation_settings(beside: Optional[str] = None) -> Dict[str, Any]:
    """Load validation settings, resolved beside the manifest.

    `beside` is the manifest path whose directory holds the settings. It
    used to be the process's working directory, which made the same
    manifest pass from the repository and fail from anywhere else: the
    bounds silently reverted to defaults, and a display outside them was
    rejected for where the caller happened to stand.
    """

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
    validation_file = (
        os.path.join(os.path.dirname(os.path.abspath(beside)), VALIDATION_SETTINGS_FILE)
        if beside
        else VALIDATION_SETTINGS_FILE
    )
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


def validate_manifest_data(
    manifest: Dict[str, Any],
    validation_config: Dict[str, Any],
    strict_mode: bool,
) -> bool:
    """
    Validate the show manifest's human decisions: policy enums and the intended
    processor lockdown state (§spec:signal-contract). Plausibility of
    measured values belongs to validate_measurements_data.
    """
    ocio_settings = manifest.get("ocio", {})

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
        contract = manifest.get("signal_contract", {})
        for field, meaning in (
            ("intensity", "locked processor intensity"),
            ("processing", "per-feature processor processing state"),
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


def validate_inputs(
    manifest: Dict[str, Any],
    measurements: Dict[str, Any],
    beside: Optional[str] = None,
) -> bool:
    """
    Validate both inputs along the human/machine line
    (§spec:characterization-model): manifest checks against the
    show manifest, plausibility checks against the measurements
    artifact. Strict mode comes from the show manifest.
    """
    validation_config = load_validation_settings(beside)
    strict_mode = manifest.get("validation", {}).get("strict_mode", False)

    if not validate_manifest_data(manifest, validation_config, strict_mode):
        return False
    if not validate_measurements_data(measurements, validation_config, strict_mode):
        return False

    print("✓ Configuration validation passed")
    return True


# Verification handoff (§spec:verification): probe patches, their
# predicted on-wall colorimetry, and the artifact that carries both to
# color-wrangler sessions and OLE-Toolset. Analysis of measurements
# against these predictions belongs to OLE-Toolset (§spec:non-goals);
# this component only states what the wall should do.

# Artifact identity. The trailing version moves when the shape changes,
# so a consumer can refuse a file it does not understand.
PREDICTIONS_SCHEMA = "ociodisplaygen/predictions/1"
PREDICTIONS_SUFFIX = ".predictions.yaml"
PROBE_DIR_SUFFIX = ".probe"

# Probe imagery: two solid-color images per patch (§spec:verification).
# The 16-bit PNG records the predicted code values — predictions are
# computed from the quantized code value, so the record and the
# prediction agree exactly, and 16 bits puts that quantization far
# below any instrument's repeatability. The float32 EXR holds the
# recorded scene-linear triple for the renderer to interpret.
PROBE_PATCH_PIXELS = 256
PROBE_CODE_LEVELS = 65535

# Recorded precision. Nine decimals sits below any instrument's
# resolution at these magnitudes and short of the exponent notation
# PyYAML's core schema will not read back as a number. Predictions are
# rounded to it as they are built, so the in-memory prediction and the
# artifact are the same numbers — the artifact is the contract.
PREDICTIONS_DECIMALS = 9

# The probe set, stated as fractions of the wall's full linear drive.
# Fixed and documented rather than configurable: the predictions file is
# a contract between three tools, and a per-show patch list would make
# every session's results incomparable.
#
# The neutral ramp carries the radiometric claim — predicted and
# measured luminance must track with unity exponent (§spec:view-transform)
# — and is spaced to sample the bottom of the range, where display
# response deviates most. The chromatic axes at full drive sit on the
# measured gamut boundary, exercising the view's gamut compressor; at
# half drive and half saturation they sit in its untouched core.
NEUTRAL_RAMP_DRIVE = (0.0, 0.02, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0)
CHROMATIC_AXES = (
    ("red", (1.0, 0.0, 0.0)),
    ("green", (0.0, 1.0, 0.0)),
    ("blue", (0.0, 0.0, 1.0)),
    ("cyan", (0.0, 1.0, 1.0)),
    ("magenta", (1.0, 0.0, 1.0)),
    ("yellow", (1.0, 1.0, 0.0)),
)
CHROMATIC_DRIVE_LEVELS = (1.0, 0.5)
DESATURATED_DRIVE = 0.5
DESATURATED_BLEND = 0.5


class ProbePatch(NamedTuple):
    """A probe patch as a fraction of the wall's full linear drive."""

    id: str
    drive: Tuple[float, float, float]


class PatchPrediction(NamedTuple):
    """One patch's prediction: the scene-linear content that produces
    it, the code values the config emits for that content (quantized to
    the probe imagery's grid), and the XYZ the wall is predicted to
    emit, in cd/m²."""

    id: str
    scene_linear: Tuple[float, float, float]
    code_value: Tuple[float, float, float]
    xyz: Tuple[float, float, float]


class Predictions(NamedTuple):
    """The verification contract for one generated config: what the
    config is, which rendering the predictions are for, and the patches
    (§spec:verification). The config's sha256 binds the two — a
    predictions file is meaningless without the config it describes
    (§spec:provenance)."""

    config_file: str
    config_sha256: str
    display: str
    view: str
    scene_reference: str
    patches: Tuple[PatchPrediction, ...]


def _triple(values: Any) -> Tuple[float, float, float]:
    """Three floats as a fixed-width tuple."""
    first, second, third = (float(value) for value in values)
    return (first, second, third)


def _quantize_code_value(
    emitted: "npt.NDArray[np.float64]",
) -> "npt.NDArray[np.float64]":
    """Clamp emitted code values to [0, 1] and land them on the probe
    imagery's grid — the one statement of the quantization rule, shared
    by prediction and its tests. Code values are a signal, not a float:
    the prediction describes the file the wall is actually driven with
    (§spec:verification)."""
    return np.round(np.clip(emitted, 0.0, 1.0) * PROBE_CODE_LEVELS) / PROBE_CODE_LEVELS


def _recorded_triple(values: Any) -> Tuple[float, float, float]:
    """Three floats at the artifact's recorded precision."""
    return _triple(round(float(value), PREDICTIONS_DECIMALS) for value in values)


def _probe_patch_set() -> Tuple[ProbePatch, ...]:
    """The probe set in emission order: neutral ramp, chromatic axes at
    each drive level, then the desaturated chromatic set."""
    patches = [
        ProbePatch(f"neutral_{round(drive * 100):03d}", (drive, drive, drive))
        for drive in NEUTRAL_RAMP_DRIVE
    ]
    patches += [
        ProbePatch(
            f"{axis}_{round(level * 100):03d}",
            _triple(channel * level for channel in unit),
        )
        for axis, unit in CHROMATIC_AXES
        for level in CHROMATIC_DRIVE_LEVELS
    ]
    patches += [
        ProbePatch(
            f"{axis}_desat",
            _triple(
                DESATURATED_DRIVE
                * (DESATURATED_BLEND * channel + (1.0 - DESATURATED_BLEND))
                for channel in unit
            ),
        )
        for axis, unit in CHROMATIC_AXES
    ]
    return tuple(patches)


PROBE_PATCHES = _probe_patch_set()


def _drive_to_display_reference_matrix(
    characterization: DisplayCharacterization,
    chromatic_adaptation_transform: str,
) -> npt.NDArray[np.float64]:
    """Wall drive fraction → display-reference XYZ: the inverse of the VP
    Radiometric chain's drive-space stage.

    Patches are stated in drive space because that is where a probe set
    is meaningful — full drive is the wall's boundary, half drive its
    midpoint — and the content that produces them follows from the
    anchor and the measured primaries. Boundary patches whose content
    the compressor then moves are predicted from the config itself, not
    from this matrix.
    """
    return np.asarray(
        np.linalg.inv(
            _drive_space_matrix(characterization, chromatic_adaptation_transform)[
                :3, :3
            ]
        ),
        dtype=np.float64,
    )


def _apply_rgb(
    processor: "OCIO.CPUProcessor", rgb: npt.NDArray[np.floating[Any]]
) -> npt.NDArray[np.float64]:
    """One RGB triple through an OCIO CPU processor. The buffer is a
    private copy: applyRGB writes in place."""
    pixel = np.array(rgb, dtype=np.float32)
    processor.applyRGB(pixel)
    return pixel.astype(np.float64)


def predict_probe_patches(
    config: "OCIO.Config",
    display: str,
    characterization: DisplayCharacterization,
    nits_anchor: float,
    view: str,
    chromatic_adaptation_transform: str,
) -> Tuple[PatchPrediction, ...]:
    """
    Predict on-wall colorimetry for the probe set through the generated
    config (§spec:verification).

    Each patch runs forward through the config twice: scene reference →
    (display, view) gives the code values the wall receives, and the
    display colorspace → display reference gives the XYZ those code
    values produce. Inverting a drive-space patch back to the content
    that produces it takes the config's own display-reference → scene-
    reference leg. Predictions therefore come from the same transforms
    the runtime executes — no second implementation to drift, and no
    assumption about which scene reference the base config uses
    (§spec:config-structure).

    Predictions target the default rendering (VP Radiometric): it is the
    one making a radiometric claim, so it is the one a measurement can
    falsify. The photographic view is verifiable only against its own
    tone scale.

    Raises:
        ValueError: When the config lacks the interchange roles the
            display and view depend on.
    """
    scene_reference, display_reference = derive_reference_spaces(config)
    encode = config.getProcessor(
        scene_reference, display, view, OCIO.TRANSFORM_DIR_FORWARD
    ).getDefaultCPUProcessor()
    decode = config.getProcessor(display, display_reference).getDefaultCPUProcessor()
    to_scene_reference = config.getProcessor(
        display_reference, scene_reference
    ).getDefaultCPUProcessor()
    to_display_reference = _drive_to_display_reference_matrix(
        characterization, chromatic_adaptation_transform
    )
    anchor_scale = REFERENCE_LUMINANCE / nits_anchor

    predictions = []
    for patch in PROBE_PATCHES:
        content = to_display_reference @ np.asarray(patch.drive, dtype=np.float64)
        scene_linear = _apply_rgb(to_scene_reference, content) * anchor_scale
        emitted = _apply_rgb(encode, scene_linear)
        code_value = _quantize_code_value(emitted)
        xyz = _apply_rgb(decode, code_value) * REFERENCE_LUMINANCE
        predictions.append(
            PatchPrediction(
                id=patch.id,
                scene_linear=_recorded_triple(scene_linear),
                code_value=_recorded_triple(code_value),
                xyz=_recorded_triple(xyz),
            )
        )
    return tuple(predictions)


def build_predictions(
    config: "OCIO.Config",
    display: str,
    characterization: DisplayCharacterization,
    nits_anchor: float,
    config_path: str,
    view: str = VP_RADIOMETRIC_VIEW,
    chromatic_adaptation_transform: str = "CAT02",
) -> Predictions:
    """
    Predictions for a config already written to disk, bound to it by
    sha256 (§spec:provenance). The config file is named by basename
    only: the predictions sit beside it, and absolute paths would tie a
    shipped artifact to one machine's directory layout.

    Raises:
        ValueError: When the config file is unreadable.
    """
    scene_reference, _ = derive_reference_spaces(config)
    return Predictions(
        config_file=os.path.basename(config_path),
        config_sha256=file_sha256(config_path, "Generated config"),
        display=display,
        view=view,
        scene_reference=scene_reference,
        patches=predict_probe_patches(
            config,
            display,
            characterization,
            nits_anchor,
            view,
            chromatic_adaptation_transform,
        ),
    )


# Fixed preamble: emitted verbatim, so a parsed file re-emits byte for
# byte even though YAML parsing drops comments.
PREDICTIONS_HEADER = (
    "# PREDICTIONS ARTIFACT — machine-written (§spec:verification).\n"
    "# The verification contract for one generated OCIO config. For each\n"
    "# probe patch: the scene-linear content that produces it (identical\n"
    "# to the patch's EXR probe image), the code values the config emits\n"
    "# for that content (identical to its PNG probe image), and the CIE\n"
    "# XYZ the wall is predicted to emit, in cd/m². Bound to its config\n"
    "# by sha256 — never hand-edited, never separated from the config it\n"
    "# names.\n"
)


def _format_float(value: float) -> str:
    """A float as fixed-point text that parses back to itself, so the
    artifact round-trips byte for byte."""
    number = float(value)
    if not np.isfinite(number):
        raise ValueError(f"Predictions cannot record a non-finite value: {number}")
    text = f"{number:.{PREDICTIONS_DECIMALS}f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return "0.0" if text == "-0.0" else text


def _format_triple(values: Tuple[float, float, float]) -> str:
    return f"[{', '.join(_format_float(value) for value in values)}]"


def _format_string(value: str, what: str) -> str:
    """A double-quoted YAML scalar. Unprintable characters are refused
    for the same reason provenance refuses them: the value is written
    verbatim into an artifact other tools parse."""
    reject_control_characters(value, what)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def emit_predictions(predictions: Predictions) -> str:
    """The predictions artifact as text (§spec:verification). Byte-
    deterministic: fixed key order, fixed float formatting, no
    timestamps."""
    lines = [
        PREDICTIONS_HEADER,
        f"schema: {_format_string(PREDICTIONS_SCHEMA, 'Predictions schema')}\n",
        "generator: "
        f"{_format_string(f'ociodisplaygen {GENERATOR_VERSION}', 'Generator')}\n",
        "config:\n",
        f"  file: {_format_string(predictions.config_file, 'Config file')}\n",
        f"  sha256: {_format_string(predictions.config_sha256, 'Config sha256')}\n",
        f"display: {_format_string(predictions.display, 'Display name')}\n",
        f"view: {_format_string(predictions.view, 'View name')}\n",
        "scene_reference: "
        f"{_format_string(predictions.scene_reference, 'Scene reference')}\n",
        "patches:\n",
    ]
    for patch in predictions.patches:
        lines += [
            f"  - id: {_format_string(patch.id, 'Patch id')}\n",
            f"    scene_linear: {_format_triple(patch.scene_linear)}\n",
            f"    code_value: {_format_triple(patch.code_value)}\n",
            f"    xyz: {_format_triple(patch.xyz)}\n",
        ]
    return "".join(lines)


def _require_string(document: Dict[str, Any], key: str, path: str) -> str:
    """A string field from a parsed predictions artifact.

    Unprintable characters are refused on the way in for the same reason
    the emit side refuses them: check_predictions prints these fields to
    the operator as a trust signal, and a newline or ANSI escape in a
    supplied artifact could forge the verdict being read
    (§spec:provenance).
    """
    value = document.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Predictions '{path}' key '{key}' must be a string")
    return reject_control_characters(value, f"Predictions '{path}' key '{key}'")


def _require_bare_filename(value: str, what: str) -> str:
    """A name from a parsed artifact that will be joined to a directory.

    The artifact crosses machines, so a name that escapes its directory
    on any platform is malformed on every platform: both path flavors
    are checked, which catches the backslash POSIX would treat as an
    ordinary character and the drive letter that makes an NT join
    discard the base entirely ('C:x'). '.' and '..' name a directory
    rather than a file and are refused too, so the invariant is exactly
    'a file beside the artifact'.

    Raises:
        ValueError: When value is not a bare filename.
    """
    contained = value and value not in (os.curdir, os.pardir)
    for flavor in (posixpath, ntpath):
        contained = (
            contained
            and not flavor.splitdrive(value)[0]
            and flavor.basename(value) == value
        )
    if not contained:
        raise ValueError(
            f"{what} '{value}' is not a bare filename — it must name a "
            f"file beside the artifact, with no directory, drive, or "
            f"parent component (§spec:provenance)"
        )
    return value


def _parse_triple(
    raw: Any, path: str, patch_id: str, field: str
) -> Tuple[float, float, float]:
    if not isinstance(raw, list) or len(raw) != 3:
        raise ValueError(
            f"Predictions '{path}' patch '{patch_id}' field '{field}' "
            f"must be a list of three numbers"
        )
    try:
        return _triple(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"Predictions '{path}' patch '{patch_id}' field '{field}' "
            f"is not numeric: {e}"
        ) from e


def parse_predictions(data: bytes, path: str) -> Predictions:
    """
    Read a predictions artifact (§spec:verification).

    Raises:
        ValueError: For invalid YAML, an unrecognized schema, or a
            malformed patch list.
    """
    document = parse_yaml_mapping(data, path, "Predictions")
    schema = document.get("schema")
    if schema != PREDICTIONS_SCHEMA:
        raise ValueError(
            f"Predictions '{path}' declares schema {schema!r}; this "
            f"generator reads '{PREDICTIONS_SCHEMA}'"
        )
    config = document.get("config")
    if not isinstance(config, dict):
        raise ValueError(f"Predictions '{path}' has no 'config' mapping")
    raw_patches = document.get("patches")
    if not isinstance(raw_patches, list) or not raw_patches:
        raise ValueError(f"Predictions '{path}' has no patches")
    patches = []
    for entry in raw_patches:
        if not isinstance(entry, dict):
            raise ValueError(f"Predictions '{path}' patch entries must be mappings")
        # Ids key the measurement file a session writes back and name
        # the probe image beside it, so they are filenames in every tool
        # that reads this artifact, not only in this one.
        patch_id = _require_bare_filename(
            _require_string(entry, "id", path), f"Predictions '{path}' patch id"
        )
        patches.append(
            PatchPrediction(
                id=patch_id,
                scene_linear=_parse_triple(
                    entry.get("scene_linear"), path, patch_id, "scene_linear"
                ),
                code_value=_parse_triple(
                    entry.get("code_value"), path, patch_id, "code_value"
                ),
                xyz=_parse_triple(entry.get("xyz"), path, patch_id, "xyz"),
            )
        )
    # Normalized and shape-checked exactly as the promotion pointer's
    # digest is: an uppercase or truncated digest is a malformed file,
    # not a config that fails to match.
    recorded_sha256 = _require_string(config, "sha256", path).lower()
    if not SHA256_HEX_PATTERN.fullmatch(recorded_sha256):
        raise ValueError(
            f"Predictions '{path}' config sha256 '{recorded_sha256}' is "
            f"malformed — expected a 64-character hex sha256 digest "
            f"(§spec:provenance)"
        )
    # The artifact attests that the config sitting beside it is the one
    # it describes, so the recorded name is a bare filename — the same
    # containment the promotion pointer enforces. Without it a supplied
    # artifact could aim the hash check at an unrelated trusted config
    # and pass while carrying forged patch values, or report the digest
    # of any readable file.
    config_file = _require_bare_filename(
        _require_string(config, "file", path), f"Predictions '{path}' config file"
    )
    return Predictions(
        config_file=config_file,
        config_sha256=recorded_sha256,
        display=_require_string(document, "display", path),
        view=_require_string(document, "view", path),
        scene_reference=_require_string(document, "scene_reference", path),
        patches=tuple(patches),
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    """One PNG chunk: length, type, payload, CRC32 over type+payload."""
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload))
    )


def probe_patch_png(prediction: PatchPrediction) -> bytes:
    """
    A solid-color 16-bit PNG for one probe patch — the image a session
    puts on the wall.

    Written against the format with stdlib zlib rather than through an
    image library: the stored pixel is exactly the code value the
    prediction was computed from, with no colorspace tagging, gamma
    chunk, or encoder default able to reinterpret it.

    Raises:
        ValueError: For code values outside [0, 1] — a patch that
            cannot be represented as a signal.
    """
    levels = [round(channel * PROBE_CODE_LEVELS) for channel in prediction.code_value]
    if not all(0 <= level <= PROBE_CODE_LEVELS for level in levels):
        raise ValueError(
            f"Probe patch '{prediction.id}' has code values outside [0, 1]: "
            f"{prediction.code_value}"
        )
    row = b"\x00" + struct.pack(">HHH", *levels) * PROBE_PATCH_PIXELS
    header = struct.pack(
        ">IIBBBBB",
        PROBE_PATCH_PIXELS,
        PROBE_PATCH_PIXELS,
        16,  # bit depth
        2,  # color type: truecolor
        0,  # compression: deflate
        0,  # filter method: adaptive
        0,  # interlace: none
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(row * PROBE_PATCH_PIXELS, 9))
        + _png_chunk(b"IEND", b"")
    )


# EXR framing (§spec:verification): single-part uncompressed scanline,
# little-endian throughout. FLOAT rather than HALF pixels: half's three
# significant digits cannot carry the artifact's recorded precision.
EXR_MAGIC = b"\x76\x2f\x31\x01"
EXR_VERSION = struct.pack("<I", 2)
EXR_PIXEL_TYPE_FLOAT = 2
# chlist entries are sorted by name, per the format. The pixel rows are
# built from this same tuple, so it is the one source of channel order.
EXR_CHANNELS = ("B", "G", "R")
# Scanline chunk header (y coordinate + byte count) and offset-table
# entry widths, per the format.
EXR_SCANLINE_HEADER_BYTES = 8
EXR_OFFSET_ENTRY_BYTES = 8


def _exr_attribute(name: bytes, kind: bytes, payload: bytes) -> bytes:
    """One EXR header attribute: name, type, size, payload."""
    return name + b"\x00" + kind + b"\x00" + struct.pack("<i", len(payload)) + payload


def probe_patch_exr(
    prediction: PatchPrediction, scene_reference: str, config_sha256: str
) -> bytes:
    """
    A solid-color float32 EXR for one probe patch, in the config's
    scene reference space — the stimulus a renderer interprets through
    the generated config (§spec:verification). Its pixels are the
    nearest float32 to the scene-linear triple the predictions file
    records.

    Hand-rolled for byte-determinism (rationale in §spec:verification).
    This file's decisions: fixed alphabetical header attribute order;
    channel-planar solid rows in ``EXR_CHANNELS`` order; sceneReference
    and configSha256 string attributes so each file names its own
    interpretation and config.

    Raises:
        ValueError: For non-finite scene-linear values, or unprintable
            characters in the embedded attributes.
    """
    if not all(np.isfinite(value) for value in prediction.scene_linear):
        raise ValueError(
            f"Probe patch '{prediction.id}' has non-finite scene-linear "
            f"values: {prediction.scene_linear}"
        )
    reject_control_characters(scene_reference, "EXR sceneReference attribute")
    reject_control_characters(config_sha256, "EXR configSha256 attribute")

    size = PROBE_PATCH_PIXELS
    box2i = struct.pack("<4i", 0, 0, size - 1, size - 1)
    chlist = (
        b"".join(
            channel.encode("ascii")
            + b"\x00"
            # pixel type, pLinear + 3 reserved bytes, x/y sampling
            + struct.pack("<i4B2i", EXR_PIXEL_TYPE_FLOAT, 0, 0, 0, 0, 1, 1)
            for channel in EXR_CHANNELS
        )
        + b"\x00"
    )
    header = (
        _exr_attribute(b"channels", b"chlist", chlist)
        + _exr_attribute(b"compression", b"compression", b"\x00")
        + _exr_attribute(b"configSha256", b"string", config_sha256.encode("utf-8"))
        + _exr_attribute(b"dataWindow", b"box2i", box2i)
        + _exr_attribute(b"displayWindow", b"box2i", box2i)
        + _exr_attribute(b"lineOrder", b"lineOrder", b"\x00")
        + _exr_attribute(b"pixelAspectRatio", b"float", struct.pack("<f", 1.0))
        + _exr_attribute(b"sceneReference", b"string", scene_reference.encode("utf-8"))
        + _exr_attribute(b"screenWindowCenter", b"v2f", struct.pack("<2f", 0.0, 0.0))
        + _exr_attribute(b"screenWindowWidth", b"float", struct.pack("<f", 1.0))
        + b"\x00"
    )
    # Channel-planar rows; EXR_CHANNELS is the one source of order.
    by_name = dict(zip(("R", "G", "B"), prediction.scene_linear))
    row = b"".join(
        struct.pack("<f", by_name[channel]) * size for channel in EXR_CHANNELS
    )
    row_len = len(row)
    chunk_length = EXR_SCANLINE_HEADER_BYTES + row_len
    data_start = (
        len(EXR_MAGIC) + len(EXR_VERSION) + len(header) + EXR_OFFSET_ENTRY_BYTES * size
    )
    offsets = struct.pack(
        f"<{size}Q", *(data_start + y * chunk_length for y in range(size))
    )
    parts = [EXR_MAGIC, EXR_VERSION, header, offsets]
    for y in range(size):
        parts.append(struct.pack("<2i", y, row_len))
        parts.append(row)
    return b"".join(parts)


def write_probe_imagery(directory: str, predictions: Predictions) -> List[str]:
    """Write each patch's probe images into directory, creating it: the
    16-bit PNG code-value record, then the scene-linear EXR the
    renderer interprets (§spec:verification). Returns the paths
    written, in patch order."""
    os.makedirs(directory, exist_ok=True)
    written = []
    for patch in predictions.patches:
        for suffix, payload in (
            (".png", probe_patch_png(patch)),
            (
                ".exr",
                probe_patch_exr(
                    patch, predictions.scene_reference, predictions.config_sha256
                ),
            ),
        ):
            path = os.path.join(directory, f"{patch.id}{suffix}")
            with open(path, "wb") as f:
                f.write(payload)
            written.append(path)
    return written


def _sibling_artifact(config_path: str, suffix: str) -> str:
    """A handoff artifact beside a generated config, sharing its stem."""
    return f"{os.path.splitext(config_path)[0]}{suffix}"


def predictions_path(config_path: str) -> str:
    """The predictions artifact beside a generated config."""
    return _sibling_artifact(config_path, PREDICTIONS_SUFFIX)


def probe_directory(config_path: str) -> str:
    """The probe imagery directory beside a generated config."""
    return _sibling_artifact(config_path, PROBE_DIR_SUFFIX)


def check_predictions(path: str) -> None:
    """
    Report what a predictions file describes and whether the config it
    names is still the config it was generated from — the verification
    counterpart to the provenance lines in the config itself
    (§spec:provenance, §req:user-stories).

    Raises:
        ValueError: For an unreadable or malformed file, a missing
            config, or a config whose bytes no longer match.
    """
    predictions = parse_predictions(read_file_bytes(path, "Predictions"), path)
    print(f"Predictions: {path}")
    print(f"   Display: {predictions.display}")
    print(f"   View: {predictions.view}")
    print(f"   Scene reference: {predictions.scene_reference}")
    print(f"   Patches: {len(predictions.patches)}")
    print(f"   Config: {predictions.config_file}")
    print(f"   Recorded sha256: {predictions.config_sha256}")
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(path)), predictions.config_file
    )
    actual = file_sha256(config_path, "Generated config")
    if not hmac.compare_digest(actual, predictions.config_sha256):
        raise ValueError(
            f"Config '{predictions.config_file}' does not match these "
            f"predictions: recorded sha256 {predictions.config_sha256}, "
            f"actual sha256 {actual}. The predictions describe a different "
            f"config — measuring against them would compare the wall to a "
            f"config it is not running (§spec:verification)"
        )
    print(f"   ✓ '{predictions.config_file}' matches the recorded hash")
    _check_probe_exr_bindings(path, predictions)


def _read_exr_string_attribute(data: bytes, name: bytes) -> str:
    """The value of a string attribute in an EXR header, parsed with the
    standard library — enough of the format to audit the writer's own
    fixed framing (§spec:verification).

    Raises:
        ValueError: When the attribute is absent or the header is not
            the writer's single-part framing.
    """
    if not data.startswith(EXR_MAGIC):
        raise ValueError("not an EXR file (bad magic)")
    cursor = len(EXR_MAGIC) + len(EXR_VERSION)
    while data[cursor] != 0:
        name_end = data.index(b"\x00", cursor)
        attribute_name = data[cursor:name_end]
        type_end = data.index(b"\x00", name_end + 1)
        (size,) = struct.unpack_from("<i", data, type_end + 1)
        payload_start = type_end + 1 + 4
        if attribute_name == name:
            return data[payload_start : payload_start + size].decode("utf-8")
        cursor = payload_start + size
    raise ValueError(f"EXR header has no '{name.decode()}' attribute")


def _check_probe_exr_bindings(path: str, predictions: Predictions) -> None:
    """Audit the sibling probe directory's EXR headers against the
    predictions' config hash, so a stale EXR — the artifact that travels
    to another machine and operator — is caught the same way a stale
    config is (§spec:provenance). A missing probe directory is reported,
    not an error: predictions stand alone.

    Raises:
        ValueError: When an EXR names a different config.
    """
    probe_dir = _sibling_artifact(
        os.path.join(os.path.dirname(os.path.abspath(path)), predictions.config_file),
        PROBE_DIR_SUFFIX,
    )
    if not os.path.isdir(probe_dir):
        print("   (no probe directory beside the config; skipping EXR audit)")
        return
    exr_names = sorted(n for n in os.listdir(probe_dir) if n.lower().endswith(".exr"))
    if not exr_names:
        print("   (probe directory holds no EXRs; nothing to audit)")
        return
    for exr_name in exr_names:
        exr_path = os.path.join(probe_dir, exr_name)
        recorded = _read_exr_string_attribute(
            read_file_bytes(exr_path, "Probe EXR"), b"configSha256"
        )
        # The attribute is untrusted file content that this audit prints:
        # hold it to the digest grammar before it reaches the operator's
        # trust signal, like every other supplied-artifact string.
        if not SHA256_HEX_PATTERN.fullmatch(recorded):
            raise ValueError(
                f"Probe EXR '{exr_name}' carries a malformed configSha256 "
                f"attribute — not a sha256 hex digest"
            )
        if not hmac.compare_digest(recorded, predictions.config_sha256):
            raise ValueError(
                f"Probe EXR '{exr_name}' names config sha256 {recorded}, but "
                f"these predictions record {predictions.config_sha256}: the "
                f"imagery is from a different generation — displaying it "
                f"would measure the wall against a config it is not running "
                f"(§spec:verification)"
            )
    print(f"   ✓ {len(exr_names)} probe EXRs name the recorded config hash")


def generate_output_filename(
    manifest: Dict[str, Any], characterization: DisplayCharacterization
) -> str:
    """Generate output filename if not specified in the show manifest."""

    ocio_config = manifest.get("ocio", {})

    # Use specified output config if provided
    if "output_config" in ocio_config:
        return ocio_config["output_config"]

    # Generate filename from display name
    display_name = characterization.name.lower().replace(" ", "_").replace("-", "_")
    return f"{display_name}_config.ocio"


def create_base_ocio_config(manifest: Dict[str, Any]) -> "OCIO.Config":
    """Create base OCIO configuration using ocio:// scheme."""

    base_config = manifest.get("ocio", {}).get("base_config", {})
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
