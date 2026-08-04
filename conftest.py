"""Shared test fixtures: sample-wall values from display_config.yaml."""

import colour
import numpy as np
import numpy.typing as npt

from OCIODisplayGen import D65_WHITE_XY, DisplayCharacterization

STUDIO_CONFIG_URI = "ocio://studio-config-v2.1.0_aces-v1.3_ocio-v2.3"
ACES2_STUDIO_CONFIG_URI = "ocio://studio-config-v4.0.0_aces-v2.0_ocio-v2.5"

# Sample wall measurements from display_config.yaml
WALL_PRIMARIES = np.array([[0.680, 0.320], [0.265, 0.690], [0.150, 0.060]])
WALL_WHITEPOINT = (0.3127, 0.3290)
PEAK_LUMINANCE = 1000.0
GAMMA = 2.4


def make_characterization(
    eotf_type: str = "GAMMA",
    white_point: tuple[float, float] = WALL_WHITEPOINT,
    white_point_policy: str = "adapted",
    intensity: str | None = None,
    processing_disabled: bool | None = None,
) -> DisplayCharacterization:
    """Characterization with the sample-yaml wall measurements."""
    char = DisplayCharacterization("Test Wall")
    char.primaries = {
        "red": tuple(WALL_PRIMARIES[0]),
        "green": tuple(WALL_PRIMARIES[1]),
        "blue": tuple(WALL_PRIMARIES[2]),
    }
    char.white_point = white_point
    char.black_level = 0.005
    char.peak_luminance = PEAK_LUMINANCE
    char.eotf_type = eotf_type
    char.gamma_value = GAMMA
    char.white_point_policy = white_point_policy
    char.processor_intensity = intensity
    char.processor_processing_disabled = processing_disabled
    return char


def d65_xyz(luminance_y: float) -> npt.NDArray[np.float64]:
    """D65 white XYZ at the given Y (units of 100 cd/m²)."""
    return np.asarray(
        colour.xy_to_XYZ(np.array(D65_WHITE_XY)) * luminance_y, dtype=np.float64
    )
