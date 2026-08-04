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

# The colorimetric view: the bare display colorspace with hard clip,
# for measurement and verification work (§spec:view-transform).
COLORIMETRIC_VIEW = "Colorimetric"

# OCIO's display-reference luminance anchor: linear 1.0 = 100 cd/m².
REFERENCE_LUMINANCE = 100.0

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
        self.viewing_conditions: Dict[
            str, object
        ] = {}  # Ambient light, viewing angle, etc.


def create_display_xyz_to_native_matrix(
    characterization: DisplayCharacterization,
    chromatic_adaptation_transform: str = "CAT02",
    white_point_policy: str = "adapted",
) -> npt.NDArray[np.float64]:
    """
    Build the 4x4 matrix from display-reference CIE XYZ (D65-adapted) to
    the wall's native RGB.

    Policy "adapted": composes a Von Kries chromatic adaptation
    (display-reference D65 → measured wall white) with the wall's derived
    XYZ→RGB matrix, so native RGB (1, 1, 1) is the wall's measured white.
    Policy "absolute": the derived XYZ→RGB matrix alone — no adaptation,
    chromaticity is exact within gamut.

    Args:
        characterization: Measured display data (primaries, white point)
        chromatic_adaptation_transform: CAT name accepted by colour-science
            (adapted policy only)
        white_point_policy: "adapted" or "absolute" (§spec:white-point)

    Returns:
        4x4 matrix for an OCIO MatrixTransform (row-major)

    Raises:
        ValueError: For unknown white point policies.
    """
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
    white_point_policy: str = "adapted",
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
        characterization: Measured display data
        chromatic_adaptation_transform: CAT for D65 → wall white
            adaptation (adapted policy only)
        white_point_policy: "adapted" or "absolute" (§spec:white-point)

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

    if white_point_policy == "absolute":
        policy_note = "absolute (no chromatic adaptation)"
    else:
        policy_note = (
            f"adapted ({chromatic_adaptation_transform}, D65 → wall white)"
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
        f"White point policy: {policy_note}"
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
        characterization, chromatic_adaptation_transform, white_point_policy
    )
    matrix_transform = OCIO.MatrixTransform()
    matrix_transform.setMatrix(matrix_4x4.flatten().tolist())
    group.appendTransform(matrix_transform)

    if eotf_type == "GAMMA":
        # Stage 2: absolute luminance scale — RGB 1.0 = measured peak.
        # Kept as a distinct stage for auditability.
        scale = REFERENCE_LUMINANCE / peak
        scale_transform = OCIO.MatrixTransform()
        scale_matrix = np.diag([scale, scale, scale, 1.0])
        scale_transform.setMatrix(scale_matrix.flatten().tolist())
        group.appendTransform(scale_transform)
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


def register_display(config: "OCIO.Config", colorspace: OCIO.ColorSpace) -> str:
    """
    Register the wall as a named OCIO display with a colorimetric view.

    Adds the colorspace to the config and registers a display named
    after it (studio config convention: display name == display
    colorspace name) with a "Colorimetric" view pointing at the bare
    colorspace. The display is appended to the config's active-display
    list without clobbering the base config's existing entries. An
    empty active list means "all active" in OCIO, so it is left empty.

    Args:
        config: Base OCIO config to extend
        colorspace: Display-referred wall colorspace

    Returns:
        The registered display name
    """
    config.addColorSpace(colorspace)
    display_name = colorspace.getName()
    config.addDisplayView(display_name, COLORIMETRIC_VIEW, display_name)

    active_displays = config.getActiveDisplays()
    if active_displays:
        config.setActiveDisplays(f"{active_displays}, {display_name}")

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
    char.contrast_ratio = char.peak_luminance / char.black_level

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

    # White point policy is a generation decision, not a measurement, so
    # it lives under ocio:. Validated at matrix-build time.
    char.white_point_policy = config.get("ocio", {}).get(
        "white_point_policy", "adapted"
    )

    # Combine viewing conditions, metrology, and processor configuration data
    char.viewing_conditions = config.get("viewing_conditions", {}).copy()
    if "metrology" in config:
        char.viewing_conditions.update(config["metrology"])
    if "configuration" in led_processor_config:
        # Add processor configuration (excluding eotf which we handle separately)
        processor_conf = led_processor_config["configuration"].copy()
        if "eotf" in processor_conf:
            del processor_conf["eotf"]
        char.viewing_conditions.update(processor_conf)
    # Add LED panel and processor info to viewing conditions
    char.viewing_conditions["led_panel_manufacturer"] = led_panel_config["manufacturer"]
    char.viewing_conditions["led_panel_model"] = led_panel_config["model"]
    char.viewing_conditions["led_panel_version"] = led_panel_config["version"]
    char.viewing_conditions["led_panel_last_calibrated"] = led_panel_config.get(
        "last_calibrated", ""
    )
    char.viewing_conditions["led_processor_manufacturer"] = led_processor_config[
        "manufacturer"
    ]
    char.viewing_conditions["led_processor_model"] = led_processor_config["model"]
    char.viewing_conditions["led_processor_version"] = led_processor_config["version"]
    return char


def load_validation_settings() -> Dict[str, Any]:
    """Load validation settings from external file."""

    # Default validation settings
    default_validation: Dict[str, Any] = {
        "check_primaries": True,
        "check_white_point": True,
        "check_luminance": True,
        "check_contrast": True,
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

        if black_level < 0:
            message = "❌ Warning: Black level cannot be negative"
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

    # Check contrast ratio
    if validation_config.get("check_contrast", True):
        black_level = float(config["display"]["led_panel"]["luminance"]["black_level"])
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
        return
    print("Creating display characterization...")
    characterization = create_characterization_from_config(config)
    print(f"\nDisplay: {characterization.name}")
    print(f"Peak luminance: {characterization.peak_luminance} cd/m²")
    print(f"Black level: {characterization.black_level} cd/m²")
    print(f"Contrast ratio: {characterization.contrast_ratio:.0f}:1")
    print(f"EOTF: {characterization.eotf_type}")
    print(f"White point policy: {characterization.white_point_policy}")
    output_config_path = generate_output_filename(config, characterization)
    try:
        print("\nCreating base OCIO config...")
        ocio_config_obj = create_base_ocio_config(config)
        scene_reference, display_reference = derive_reference_spaces(ocio_config_obj)
        print(f"Scene reference space: {scene_reference}")
        print(f"Display reference space: {display_reference}")
        if display_reference != DISPLAY_REFERENCE:
            raise ValueError(
                f"Base config display reference is '{display_reference}', "
                f"but the emitted XYZ→native matrix assumes {DISPLAY_REFERENCE}"
            )
        cs = create_display_colorspace_from_characterization(
            characterization,
            white_point_policy=characterization.white_point_policy,
        )
        display_name = register_display(ocio_config_obj, cs)
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
        print(f"   View: {COLORIMETRIC_VIEW}")
        print("\n📋 Usage Instructions:")
        print(
            f"1. Set OCIO environment variable: export OCIO="
            f"{os.path.abspath(output_config_path)}"
        )
        print(
            f"2. In your application, select display '{display_name}' "
            f"with view '{COLORIMETRIC_VIEW}'"
        )
    except Exception as e:
        print(f"❌ Error creating OCIO config: {e}")
        import traceback

        traceback.print_exc()


# Example usage for single display characterization
if __name__ == "__main__":
    main()
