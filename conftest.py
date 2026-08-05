"""Shared test fixtures: sample-wall values from the shipped sample inputs
(show_manifest.yaml + its promoted measurements artifact)."""

from pathlib import Path
from typing import Any

import colour
import numpy as np
import numpy.typing as npt
import PyOpenColorIO as OCIO
import pytest

from OCIODisplayGen import (
    D65_WHITE_XY,
    DisplayCharacterization,
    create_display_colorspace_from_characterization,
    load_inputs,
    register_display,
)

STUDIO_CONFIG_URI = "ocio://studio-config-v2.1.0_aces-v1.3_ocio-v2.3"
ACES2_STUDIO_CONFIG_URI = "ocio://studio-config-v4.0.0_aces-v2.0_ocio-v2.5"

# The shipped sample inputs (show manifest + measurements artifact).
SAMPLE_MANIFEST_PATH = Path(__file__).parent / "show_manifest.yaml"


def load_sample_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    """Fresh parse of the shipped samples — each call returns new objects."""
    manifest, measurements, _ = load_inputs(str(SAMPLE_MANIFEST_PATH))
    return manifest, measurements


# Sample wall measurements from the shipped measurements artifact
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


def build_reloaded_config(
    tmp_path_factory: pytest.TempPathFactory,
    filename: str,
    **register_kwargs: Any,
) -> tuple[OCIO.Config, str]:
    """Register the wall via the real generation path, serialize, reload."""
    config = OCIO.Config.CreateFromFile(ACES2_STUDIO_CONFIG_URI)
    char = make_characterization()
    cs = create_display_colorspace_from_characterization(char)
    display_name = register_display(config, cs, char, **register_kwargs)
    path = tmp_path_factory.mktemp("ocio") / filename
    path.write_text(config.serialize(), encoding="utf-8")
    return OCIO.Config.CreateFromFile(str(path)), display_name


def scene_view_cpu(setup: tuple[OCIO.Config, str], view: str) -> OCIO.CPUProcessor:
    """Scene reference → (display, view) CPU processor."""
    cfg, display_name = setup
    proc = cfg.getProcessor(
        OCIO.ROLE_INTERCHANGE_SCENE,
        display_name,
        view,
        OCIO.TRANSFORM_DIR_FORWARD,
    )
    return proc.getDefaultCPUProcessor()


def d65_xyz(luminance_y: float) -> npt.NDArray[np.float64]:
    """D65 white XYZ at the given Y (units of 100 cd/m²)."""
    return np.asarray(
        colour.xy_to_XYZ(np.array(D65_WHITE_XY)) * luminance_y, dtype=np.float64
    )
