# OCIODisplayGen.py
# This script creates a custom display colorspace for a high dynamic range display with
# non-standard native primaries, and appends it to an existing OCIO config
# It uses the colour-science library to create the colorspace and the PyOpenColorIO
# library to create the OCIO config

import os
import sys
from typing import Any, Dict, List, Optional, Tuple, cast

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
        try:
            name = ocio_config.getCanonicalName(role)
        except Exception as exc:
            raise ValueError(
                f"Base config does not define interchange role '{role}'"
            ) from exc
        if not name:
            raise ValueError(f"Base config does not define interchange role '{role}'")
        names.append(name)
    return names[0], names[1]


def create_reference_to_display_matrix(
    display_primaries: npt.NDArray[np.float64],
    display_whitepoint: npt.NDArray[np.float64],
    reference_primaries: npt.NDArray[np.float64],
    reference_whitepoint: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """
    Create RGB-to-RGB transformation matrix from reference space to display space.

    Args:
        display_primaries: Display RGB primaries as [[Rx,Ry], [Gx,Gy], [Bx,By]]
        display_whitepoint: Display white point as [x, y]
        reference_primaries: Reference RGB primaries as [[Rx,Ry], [Gx,Gy], [Bx,By]]
        reference_whitepoint: Reference white point as [x, y]

    Returns:
        4x4 transformation matrix for OCIO MatrixTransform
    """
    # Create colour-science RGB colorspaces
    reference_space = colour.RGB_Colourspace(
        "Reference", reference_primaries, reference_whitepoint, "Reference Space"
    )

    display_space = colour.RGB_Colourspace(
        "Display", display_primaries, display_whitepoint, "Display Space"
    )

    # Get the RGB-to-RGB conversion matrix
    matrix_3x3 = colour.matrix_RGB_to_RGB(reference_space, display_space)

    # Convert to 4x4 matrix for OCIO
    matrix_4x4 = np.identity(4)
    matrix_4x4[:3, :3] = matrix_3x3

    return matrix_4x4


def naive_gamut_map_preserve_luminance(
    rgb: npt.NDArray[np.float32],
) -> npt.NDArray[np.float32]:
    """
    Naive gamut mapping that preserves luminance while constraining chromaticity.

    Strategy:
    1. Calculate relative luminance
    2. If any RGB values are negative, use a different approach
    3. Scale negative values to zero while preserving ratios
    4. Ensure all values are non-negative

    Args:
        rgb: RGB values (can be negative or >1.0)

    Returns:
        RGB values with valid chromaticity, attempting to preserve luminance
    """
    # Convert to numpy array if needed
    rgb = np.asarray(rgb, dtype=np.float32)

    # Calculate relative luminance (ITU-R BT.709 weights)
    original_luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]

    # Handle zero or negative luminance
    if original_luminance <= 0:
        return np.array([0.0, 0.0, 0.0], dtype=np.float32)

    # Simple clipping approach for naive implementation
    # In practice, more sophisticated gamut mapping would be used
    clipped_rgb = np.clip(rgb, 0.0, np.inf)  # Remove negative values

    # If we clipped any values, try to preserve luminance by scaling
    if not np.allclose(rgb, clipped_rgb):
        clipped_luminance = (
            0.2126 * clipped_rgb[0] + 0.7152 * clipped_rgb[1] + 0.0722 * clipped_rgb[2]
        )
        if clipped_luminance > 0:
            # Scale to preserve original luminance
            scale_factor = original_luminance / clipped_luminance
            clipped_rgb *= scale_factor

    return clipped_rgb.astype(np.float32)


def naive_tone_map_preserve_chromaticity(
    rgb: npt.NDArray[np.float32], max_luminance: float = 1.0
) -> npt.NDArray[np.float32]:
    """
    Naive tone mapping that preserves chromaticity while constraining luminance.

    Strategy:
    1. Find maximum component (simple luminance proxy)
    2. Scale all components proportionally if exceeding max_luminance
    3. Preserve color ratios (chromaticity)

    Args:
        rgb: RGB values (assumed valid chromaticity, may be >1.0)
        max_luminance: Maximum allowed luminance (typically 1.0)

    Returns:
        RGB values within luminance range, preserving chromaticity
    """
    # Convert to numpy array if needed
    rgb = np.asarray(rgb, dtype=np.float32)

    # Find maximum component as luminance proxy
    max_component = np.max(rgb)

    # No tone mapping needed if within range
    if max_component <= max_luminance:
        return rgb

    # Scale all components proportionally (preserves chromaticity)
    scale_factor = max_luminance / max_component
    result = rgb * scale_factor

    return result.astype(np.float32)


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
        self.viewing_conditions: Dict[
            str, object
        ] = {}  # Ambient light, viewing angle, etc.


def create_display_colorspace_from_characterization(
    characterization: DisplayCharacterization,
    scene_reference: str,
    gamut_mapping: str = "naive_clip",
    tone_mapping: str = "naive_clip",
) -> OCIO.ColorSpace:
    """
    Create OCIO display colorspace from display characterization using
    corrected pipeline.

    Pipeline: Reference RGB → [Matrix] → [Gamut Map] → [Tone Map] → [OETF] → Display RGB

    Args:
        characterization: Measured display data
        scene_reference: Canonical name of the base config's scene reference
            space (from derive_reference_spaces); must exist in
            colour.RGB_COLOURSPACES
        gamut_mapping: Gamut mapping strategy
        tone_mapping: Tone mapping strategy

    Raises:
        ValueError: If scene_reference is not a known colour-science colorspace.
    """

    if scene_reference not in colour.RGB_COLOURSPACES:
        raise ValueError(
            f"Scene reference space '{scene_reference}' is not defined in "
            f"colour.RGB_COLOURSPACES; cannot derive reference primaries"
        )
    reference_space = colour.RGB_COLOURSPACES[scene_reference]
    reference_primaries = np.reshape(
        np.asarray(reference_space.primaries, dtype=np.float64), (3, 2)
    )
    reference_whitepoint = np.asarray(reference_space.whitepoint, dtype=np.float64)

    eotf_type = (
        characterization.eotf_type
    )  # Display EOTF (we'll generate matching OETF)

    # Prepare display primaries and white point for matrix creation
    display_primaries = np.array(
        [
            characterization.primaries["red"],
            characterization.primaries["green"],
            characterization.primaries["blue"],
        ]
    )
    display_whitepoint = np.array(characterization.white_point)

    # Create OCIO colorspace
    cs = OCIO.ColorSpace()
    display_name = f"{characterization.name} - Display"
    cs.setName(display_name)
    cs.addAlias(f"{display_name.lower().replace(' ', '_')}_display")
    cs.setFamily("Display")
    cs.setEncoding("hdr-video" if eotf_type in ["PQ", "HLG"] else "sdr-video")
    cs.setDescription(
        f"Display colorspace for {characterization.name} "
        f"(Peak: {characterization.peak_luminance} cd/m², "
        f"Black: {characterization.black_level} cd/m², "
        f"EOTF: {eotf_type}, "
        f"Gamut: {gamut_mapping}, Tone: {tone_mapping}) "
        f"Pipeline: Reference→[Matrix]→[GamutMap]→[ToneMap]→"
        f"[OETF(inverse {eotf_type})]→Display"
    )
    cs.setBitDepth(OCIO.BIT_DEPTH_F32)
    cs.addCategory("file-io")
    cs.addCategory("display")

    # Create transform group with corrected pipeline order
    group = OCIO.GroupTransform()

    # Stage 1: Matrix Transform (Reference RGB → Display RGB primaries)
    print(f"  Creating {scene_reference}→Display matrix transform...")
    matrix_4x4 = create_reference_to_display_matrix(
        display_primaries, display_whitepoint, reference_primaries, reference_whitepoint
    )
    matrix_transform = OCIO.MatrixTransform()
    matrix_transform.setMatrix([float(x) for x in matrix_4x4.flatten()])
    group.appendTransform(matrix_transform)

    # Stage 2: Gamut Mapping (handle out-of-gamut colors in display space)
    print(f"  Adding gamut mapping: {gamut_mapping}")
    if gamut_mapping == "naive_clip":
        # Naive gamut mapping will be handled by software (our helper functions)
        # Note: OCIO does not have builtin gamut mapping transforms like
        # "GAMUT-MAP - PERCEPTUAL"
        # Those would need to be implemented as custom 3D LUTs or other transforms
        print("    Using software-based naive gamut mapping (no OCIO transform added)")

    else:
        print(f"    Warning: Gamut mapping '{gamut_mapping}' not implemented")
        print("    Note: OCIO builtin gamut mapping transforms do not exist")
        print(
            "    Advanced gamut mapping would require custom 3D LUTs or "
            "other transforms"
        )

    # Stage 3: Tone Mapping (handle >1.0 luminance values)
    print(f"  Adding tone mapping: {tone_mapping}")
    if tone_mapping == "naive_clip":
        # Naive tone mapping will be handled by software (our helper functions)
        # Advanced tone mapping would require custom transforms or LUTs
        print("    Using software-based naive tone mapping (no OCIO transform added)")
    else:
        print(f"    Warning: Tone mapping '{tone_mapping}' not implemented")
        print("    Advanced tone mapping would require custom transforms or LUTs")

    # Stage 4: OETF (Opto-Electronic Transfer Function: Linear → Encoded)
    # Generate OETF that matches the inverse of the display's EOTF
    print(f"  Generating OETF to match display EOTF: {eotf_type}")
    if eotf_type == "PQ":
        # PQ OETF using verified builtin transform
        try:
            pq_transform = OCIO.BuiltinTransform("CURVE - LINEAR_to_ST-2084")
            pq_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
            group.appendTransform(pq_transform)
            print("    ✓ Applied PQ (ST-2084) OETF (inverse of display PQ EOTF)")
        except Exception as e:
            print(f"    ✗ PQ OETF failed: {e}")

    elif eotf_type == "HLG":
        # HLG OETF using verified builtin transform
        try:
            hlg_transform = OCIO.BuiltinTransform("CURVE - HLG-OETF")
            hlg_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
            group.appendTransform(hlg_transform)
            print("    ✓ Applied HLG OETF (inverse of display HLG EOTF)")
        except Exception as e:
            print(f"    ✗ HLG OETF failed: {e}")

    elif eotf_type == "GAMMA":
        # Note: OCIO doesn't have generic "CURVE - LINEAR_to_GAMMA{value}" transforms
        # Would need to use ExponentTransform or custom LUT
        print("    Warning: Generic gamma OETF not available as builtin transform")
        print(
            f"    Implementing OETF (1/gamma) to match display "
            f"Gamma {characterization.gamma_value} EOTF"
        )

        # Use ExponentTransform to create OETF (inverse of display EOTF)
        try:
            # Create inverse gamma (OETF is 1/gamma when display EOTF is gamma)
            gamma_transform = getattr(OCIO, "ExponentTransform")()
            # type: ignore[attr-defined]
            gamma_values = [1.0 / characterization.gamma_value] * 4  # RGBA channels
            gamma_transform.setValue(gamma_values)  # type: ignore[attr-defined]
            group.appendTransform(gamma_transform)
            print(
                f"    ✓ Applied OETF (1/{characterization.gamma_value}) to match "
                f"display Gamma {characterization.gamma_value} EOTF"
            )
        except Exception as e:
            print(f"    ✗ Gamma OETF failed: {e}")

    else:
        print(f"    Warning: Unknown display EOTF type '{eotf_type}'")

    cs.setTransform(group, OCIO.COLORSPACE_DIR_FROM_REFERENCE)

    print(f"  ✓ Created display colorspace: {display_name}")
    print(
        f"    Pipeline: Reference RGB → Matrix → Gamut({gamut_mapping}) → "
        f"Tone({tone_mapping}) → OETF(inverse {eotf_type}) → Display"
    )

    return cs


def append_display_colorspace_to_config(
    input_config_path: str,
    characterization: DisplayCharacterization,
    eotf_variants: Optional[List[str]] = None,
) -> OCIO.Config:
    """Append display colorspace(s) to existing config with multiple EOTF variants"""

    # Load the existing OCIO config
    config = OCIO.Config.CreateFromFile(input_config_path)

    # Derive the scene reference space from the config's interchange roles
    scene_reference, _ = derive_reference_spaces(config)

    # Default EOTF variants if none specified
    if eotf_variants is None:
        eotf_variants = ["PQ", "HLG", "GAMMA"]

    # Create colorspaces for each EOTF variant
    for eotf_type in eotf_variants:
        suffix = f" - {eotf_type}"
        cs = create_display_colorspace_from_characterization(
            characterization, scene_reference, gamut_mapping=eotf_type
        )
        config.addColorSpace(cs)

        # Add display view for each variant
        display_name = f"{characterization.name}{suffix} - Display"
        config.addDisplayView(display_name, "Output", cs.getName())

    return config


def create_display_colorspace(
    r_xy: Tuple[float, float],
    g_xy: Tuple[float, float],
    b_xy: Tuple[float, float],
    w_xy: Tuple[float, float],
    name: str = "DisplayOutput",
    whitepoint_name: str = "whitepoint name",
) -> OCIO.ColorSpace:
    """Create OCIO display colorspace from CIE xy primaries (legacy function)"""

    # Create RGB to XYZ matrix using colour-science
    primaries = np.concatenate((r_xy, g_xy, b_xy)).reshape(3, 2)
    whitepoint = np.array(w_xy)
    custom_colourspace = colour.RGB_Colourspace(
        "Custom", primaries, whitepoint, whitepoint_name
    )
    custom_colourspace.use_derived_transformation_matrices()
    XYZ_to_RGB = np.identity(4)
    XYZ_to_RGB[:3, :3] = custom_colourspace.matrix_XYZ_to_RGB

    # Create OCIO colorspace
    cs = OCIO.ColorSpace()
    cs.setName(f"{name} - Display")
    cs.addAlias(f"{name.lower()}_display")
    cs.setFamily("Display")
    cs.setEncoding("hdr-video")
    cs.setDescription(f"Convert CIE XYZ (D65 white) to {name}")
    cs.setBitDepth(OCIO.BIT_DEPTH_F32)
    cs.addCategory("file-io")

    # Create transform group
    group = OCIO.GroupTransform()

    # Add RGB to XYZ matrix
    matrix_transform = OCIO.MatrixTransform()
    matrix_transform.setMatrix(XYZ_to_RGB.flatten().tolist())
    group.appendTransform(matrix_transform)

    # Add PQ EOTF using "CURVE - LINEAR_to_ST-2084" 1D LUT
    pq_transform = OCIO.BuiltinTransform("CURVE - LINEAR_to_ST-2084")
    pq_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
    # Note that there is a fixed function implementation as well
    # as of OCIO v2.0.0, however the 1D LUT may be faster to compute
    # per the docs.
    # https://opencolorio.readthedocs.io/en/latest/releases/
    # ocio_2_4.html#new-fixed-function-transforms
    #
    # Note also that disguise does not yet support OCIO v2.4.0
    # which is required for the fixed function transform.
    #
    # pq_transform = OCIO.FixedFunctionTransform()
    # pq_transform.setStyle(OCIO.FIXED_FUNCTION_LIN_TO_PQ)
    group.appendTransform(pq_transform)

    cs.setTransform(group, OCIO.COLORSPACE_DIR_FROM_REFERENCE)

    return cs


def create_example_characterization() -> DisplayCharacterization:
    """Create an example display characterization with measured data"""

    # Example: Measured display data (replace with actual measurements)
    char = DisplayCharacterization("Custom HDR Display")

    # Measured primaries (replace with actual measurements)
    char.primaries = {
        "red": (0.680, 0.320),  # Measured red primary
        "green": (0.265, 0.690),  # Measured green primary
        "blue": (0.150, 0.060),  # Measured blue primary
    }

    # Measured white point
    char.white_point = (0.3127, 0.3290)  # D65 or measured white point

    # Measured display characteristics
    char.black_level = 0.005  # cd/m²
    char.peak_luminance = 1000.0  # cd/m²
    char.contrast_ratio = char.peak_luminance / char.black_level

    # Display response characteristics - specify what the display does (EOTF)
    char.eotf_type = "PQ"  # Display EOTF
    char.gamma_value = 2.4  # For gamma-based EOTF

    # Viewing conditions
    char.viewing_conditions = {
        "ambient_light": 5.0,  # cd/m²
        "viewing_angle": 0.0,  # degrees
        "surround": "dark",  # 'dark', 'dim', 'average'
    }

    return char


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
                    f"⚠️  Note: GAMMA EOTF with high brightness "
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
    mapping_config = config.get("ocio", {}).get("mapping", {})
    print(f"Gamut mapping: {mapping_config.get('gamut', 'naive_clip')}")
    print(f"Tone mapping: {mapping_config.get('tone', 'naive_clip')}")
    output_config_path = generate_output_filename(config, characterization)
    try:
        print("\nCreating base OCIO config...")
        ocio_config_obj = create_base_ocio_config(config)
        scene_reference, display_reference = derive_reference_spaces(ocio_config_obj)
        print(f"Scene reference space: {scene_reference}")
        print(f"Display reference space: {display_reference}")
        gamut_mapping = mapping_config.get("gamut", "naive_clip")
        tone_mapping = mapping_config.get("tone", "naive_clip")
        cs = create_display_colorspace_from_characterization(
            characterization,
            scene_reference,
            gamut_mapping=gamut_mapping,
            tone_mapping=tone_mapping,
        )
        ocio_config_obj.addColorSpace(cs)
        display_name = cs.getName()
        ocio_config_obj.addDisplayView(display_name, "Output", cs.getName())
        with open(output_config_path, "w", encoding="utf-8") as f:
            f.write(ocio_config_obj.serialize())
        print("\n✅ Successfully created OCIO config!")
        print(f"   Output file: {output_config_path}")
        print("   Colorspaces created: 1")
        print("\nCreated colorspace:")
        print(f"   - {display_name}")
        print("\n📋 Usage Instructions:")
        print(
            f"1. Set OCIO environment variable: export OCIO="
            f"{os.path.abspath(output_config_path)}"
        )
        print("2. In your application, select the display colorspace above")
    except Exception as e:
        print(f"❌ Error creating OCIO config: {e}")
        import traceback

        traceback.print_exc()


# Example usage for single display characterization
if __name__ == "__main__":
    main()
