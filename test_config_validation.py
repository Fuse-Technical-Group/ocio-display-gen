#!/usr/bin/env python3
"""Tests for measurement-plausibility validation of the YAML config."""

from typing import Any

from conftest import GAMMA, PEAK_LUMINANCE, WALL_PRIMARIES, WALL_WHITEPOINT
from OCIODisplayGen import create_characterization_from_config, validate_config_data


def make_config_dict(
    black_level: float = 0.005, strict_mode: bool = False
) -> dict[str, Any]:
    """Config dict with the sample-yaml wall measurements."""
    return {
        "validation": {"strict_mode": strict_mode},
        "display": {
            "led_panel": {
                "manufacturer": "ROE",
                "model": "Black Pearl 2 (NS)",
                "version": "2018",
                "colorimetry": {
                    "primaries": {
                        "red": list(WALL_PRIMARIES[0]),
                        "green": list(WALL_PRIMARIES[1]),
                        "blue": list(WALL_PRIMARIES[2]),
                    },
                    "white_point": list(WALL_WHITEPOINT),
                },
                "luminance": {
                    "black_level": black_level,
                    "peak_luminance": PEAK_LUMINANCE,
                },
            },
            "led_processor": {
                "manufacturer": "Brompton",
                "model": "S8",
                "version": "3.5.2",
                "configuration": {
                    "eotf": {"type": "GAMMA", "gamma_value": GAMMA},
                    "intensity": "100%",
                    "processing_disabled": True,
                },
            },
        },
    }


def test_sample_measurements_validate() -> None:
    assert validate_config_data(make_config_dict(strict_mode=True))


def test_zero_black_level_fails_strict_without_crashing() -> None:
    assert not validate_config_data(make_config_dict(black_level=0.0, strict_mode=True))


def test_zero_black_level_warns_non_strict_without_crashing() -> None:
    assert validate_config_data(make_config_dict(black_level=0.0))


def test_zero_black_level_characterization_has_infinite_contrast() -> None:
    char = create_characterization_from_config(make_config_dict(black_level=0.0))
    assert char.contrast_ratio == float("inf")
