#!/usr/bin/env python3
"""Tests for split-input loading (decisions + measurements) and validation."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import]

from conftest import (
    GAMMA,
    PEAK_LUMINANCE,
    SAMPLE_DECISIONS_PATH,
    WALL_PRIMARIES,
    WALL_WHITEPOINT,
)
from OCIODisplayGen import (
    create_characterization,
    load_inputs,
    resolve_measurements_pointer,
    validate_inputs,
)


def make_decisions_dict(strict_mode: bool = False) -> dict[str, Any]:
    """Decisions dict matching the shipped sample's human choices."""
    return {
        "validation": {"strict_mode": strict_mode},
        "show": {
            "description": "FTG Stage 1",
            "led_panel": {
                "manufacturer": "ROE",
                "model": "Black Pearl 2 (NS)",
                "version": "2018",
            },
            "led_processor": {
                "manufacturer": "Brompton",
                "model": "S8",
                "version": "3.5.2",
            },
        },
        "signal_contract": {
            "eotf": {"type": "GAMMA", "gamma_value": GAMMA},
            "intensity": "100%",
            "processing_disabled": True,
        },
        "measurements": {
            "file": "measurements/ftg_stage1_20240115.yaml",
            "sha256": "0" * 64,
        },
        "ocio": {"white_point_policy": "adapted"},
    }


def make_measurements_dict(black_level: float = 0.005) -> dict[str, Any]:
    """Measurements dict with the sample-artifact wall measurements."""
    return {
        "measurement_date": "2024-01-15",
        "instrument": {"model": "X-Rite i1 Pro 2", "firmware": "unknown"},
        "colorimetry": {
            "primaries": {
                # plain floats: numpy scalars are not YAML-serializable
                "red": [float(v) for v in WALL_PRIMARIES[0]],
                "green": [float(v) for v in WALL_PRIMARIES[1]],
                "blue": [float(v) for v in WALL_PRIMARIES[2]],
            },
            "white_point": list(WALL_WHITEPOINT),
        },
        "luminance": {
            "black_level": black_level,
            "peak_luminance": PEAK_LUMINANCE,
        },
        "ambient_floor": 5.0,
        "processor_state": {
            "eotf": {"type": "GAMMA", "gamma_value": GAMMA},
            "intensity": "100%",
            "processing_disabled": True,
        },
    }


# Validation (§spec:characterization-model): plausibility checks run
# against the measurements artifact; decisions checks (policy enums,
# processor-state presence) against the decisions file, which is also
# the strict-mode source.


def test_sample_measurements_validate() -> None:
    assert validate_inputs(
        make_decisions_dict(strict_mode=True), make_measurements_dict()
    )


def test_zero_black_level_fails_strict_without_crashing() -> None:
    assert not validate_inputs(
        make_decisions_dict(strict_mode=True), make_measurements_dict(black_level=0.0)
    )


def test_zero_black_level_warns_non_strict_without_crashing() -> None:
    assert validate_inputs(
        make_decisions_dict(), make_measurements_dict(black_level=0.0)
    )


def test_zero_black_level_characterization_has_infinite_contrast() -> None:
    char = create_characterization(
        make_decisions_dict(), make_measurements_dict(black_level=0.0)
    )
    assert char.contrast_ratio == float("inf")


def test_invalid_white_point_policy_fails_strict() -> None:
    decisions = make_decisions_dict(strict_mode=True)
    decisions["ocio"]["white_point_policy"] = "perceptual"
    assert not validate_inputs(decisions, make_measurements_dict())


def test_invalid_overflow_policy_fails_strict() -> None:
    decisions = make_decisions_dict(strict_mode=True)
    decisions["ocio"]["vp_radiometric"] = {"overflow_policy": "wrap"}
    assert not validate_inputs(decisions, make_measurements_dict())


# Split-input loading (§spec:characterization-model): the decisions
# file's promotion pointer names the measurements artifact of record;
# missing pointer, missing keys, or an unreadable artifact fail loud.


def test_shipped_samples_characterization_matches_sample_wall() -> None:
    decisions, measurements, _ = load_inputs(str(SAMPLE_DECISIONS_PATH))
    char = create_characterization(decisions, measurements)
    assert char.name == "ROE Black Pearl 2 (NS) (2018) + Brompton S8 (3.5.2)"
    assert char.primaries == {
        "red": tuple(WALL_PRIMARIES[0]),
        "green": tuple(WALL_PRIMARIES[1]),
        "blue": tuple(WALL_PRIMARIES[2]),
    }
    assert char.white_point == WALL_WHITEPOINT
    assert char.black_level == 0.005
    assert char.peak_luminance == PEAK_LUMINANCE
    assert char.eotf_type == "GAMMA"
    assert char.gamma_value == GAMMA
    assert char.processor_intensity == "100%"
    assert char.processor_processing_disabled is True
    assert char.white_point_policy == "adapted"


def test_missing_promotion_pointer_fails() -> None:
    decisions = make_decisions_dict()
    del decisions["measurements"]
    with pytest.raises(ValueError, match="promotion pointer"):
        resolve_measurements_pointer(decisions, "decisions.yaml")


def test_pointer_missing_sha256_fails() -> None:
    decisions = make_decisions_dict()
    del decisions["measurements"]["sha256"]
    with pytest.raises(ValueError, match="sha256"):
        resolve_measurements_pointer(decisions, "decisions.yaml")


def test_pointer_missing_file_key_fails() -> None:
    decisions = make_decisions_dict()
    del decisions["measurements"]["file"]
    with pytest.raises(ValueError, match="file"):
        resolve_measurements_pointer(decisions, "decisions.yaml")


def test_missing_artifact_fails(tmp_path: Path) -> None:
    decisions = make_decisions_dict()
    decisions["measurements"]["file"] = "no_such_artifact.yaml"
    decisions_path = tmp_path / "decisions.yaml"
    decisions_path.write_text(yaml.safe_dump(decisions), encoding="utf-8")
    with pytest.raises(ValueError, match="no_such_artifact.yaml"):
        load_inputs(str(decisions_path))


def test_artifact_path_resolved_relative_to_decisions_file(tmp_path: Path) -> None:
    # The decisions file lives away from the cwd; its pointer must
    # resolve against the decisions file's own directory.
    show_dir = tmp_path / "show"
    (show_dir / "measurements").mkdir(parents=True)
    artifact_path = show_dir / "measurements" / "artifact.yaml"
    artifact_path.write_text(
        yaml.safe_dump(make_measurements_dict()), encoding="utf-8"
    )
    decisions = make_decisions_dict()
    decisions["measurements"]["file"] = "measurements/artifact.yaml"
    decisions["measurements"]["sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()
    (show_dir / "decisions.yaml").write_text(
        yaml.safe_dump(decisions), encoding="utf-8"
    )
    _, measurements, _ = load_inputs(str(show_dir / "decisions.yaml"))
    assert measurements["luminance"]["peak_luminance"] == PEAK_LUMINANCE
