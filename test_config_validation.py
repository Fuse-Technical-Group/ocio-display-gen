#!/usr/bin/env python3
"""Tests for split-input loading (manifest + measurements) and validation."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import]

from conftest import (
    GAMMA,
    PEAK_LUMINANCE,
    SAMPLE_ARTIFACT_NAME,
    SAMPLE_BLACK_LEVEL,
    SAMPLE_GAMMA,
    SAMPLE_INTENSITY,
    SAMPLE_MANIFEST_PATH,
    SAMPLE_PEAK_LUMINANCE,
    WALL_PRIMARIES,
    WALL_WHITEPOINT,
)
from ocio_display_gen._core import (
    create_characterization,
    load_inputs,
    resolve_measurements_pointer,
    validate_inputs,
)


def make_manifest_dict(strict_mode: bool = False) -> dict[str, Any]:
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
            "processing": {
                "dark-magic": True,
                "puretone": True,
                "extended-bit-depth": True,
                "overdrive": False,
            },
        },
        "measurements": {
            "file": SAMPLE_ARTIFACT_NAME,
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
            "processing": {
                "dark-magic": True,
                "puretone": True,
                "extended-bit-depth": True,
                "overdrive": False,
            },
        },
    }


# Validation (§spec:characterization-model): plausibility checks run
# against the measurements artifact; manifest checks (policy enums,
# processor-state presence) against the show manifest, which is also
# the strict-mode source.


def test_sample_measurements_validate() -> None:
    assert validate_inputs(
        make_manifest_dict(strict_mode=True), make_measurements_dict()
    )


def test_zero_black_level_fails_strict_without_crashing() -> None:
    assert not validate_inputs(
        make_manifest_dict(strict_mode=True), make_measurements_dict(black_level=0.0)
    )


def test_zero_black_level_warns_non_strict_without_crashing() -> None:
    assert validate_inputs(
        make_manifest_dict(), make_measurements_dict(black_level=0.0)
    )


def test_zero_black_level_characterization_has_infinite_contrast() -> None:
    char = create_characterization(
        make_manifest_dict(), make_measurements_dict(black_level=0.0)
    )
    assert char.contrast_ratio == float("inf")


def test_invalid_white_point_policy_fails_strict() -> None:
    manifest = make_manifest_dict(strict_mode=True)
    manifest["ocio"]["white_point_policy"] = "perceptual"
    assert not validate_inputs(manifest, make_measurements_dict())


def test_invalid_overflow_policy_fails_strict() -> None:
    manifest = make_manifest_dict(strict_mode=True)
    manifest["ocio"]["vp_radiometric"] = {"overflow_policy": "wrap"}
    assert not validate_inputs(manifest, make_measurements_dict())


# Split-input loading (§spec:characterization-model): the manifest
# file's promotion pointer names the measurements artifact of record;
# missing pointer, missing keys, or an unreadable artifact fail loud.


def test_shipped_samples_characterization_matches_sample_wall() -> None:
    manifest, measurements, _ = load_inputs(str(SAMPLE_MANIFEST_PATH))
    char = create_characterization(manifest, measurements)
    assert char.name == "ROE Black Pearl 2 (NS) (2018) + Brompton S8 (3.5.2)"
    # Expected values derive from the loaded artifact itself: this test
    # pins the loader's field mapping, not the wall's numbers.
    primaries = measurements["colorimetry"]["primaries"]
    assert char.primaries == {
        channel: tuple(primaries[channel]) for channel in ("red", "green", "blue")
    }
    assert char.white_point == tuple(measurements["colorimetry"]["white_point"])
    assert char.black_level == SAMPLE_BLACK_LEVEL
    assert char.peak_luminance == SAMPLE_PEAK_LUMINANCE
    assert char.eotf_type == "GAMMA"
    assert char.gamma_value == SAMPLE_GAMMA
    assert char.processor_intensity == SAMPLE_INTENSITY
    assert char.processor_processing_disabled is True
    assert char.white_point_policy == "adapted"


def test_missing_promotion_pointer_fails() -> None:
    manifest = make_manifest_dict()
    del manifest["measurements"]
    with pytest.raises(ValueError, match="promotion pointer"):
        resolve_measurements_pointer(manifest, "show_manifest.yaml")


def test_pointer_missing_sha256_fails() -> None:
    manifest = make_manifest_dict()
    del manifest["measurements"]["sha256"]
    with pytest.raises(ValueError, match="sha256"):
        resolve_measurements_pointer(manifest, "show_manifest.yaml")


def test_pointer_missing_file_key_fails() -> None:
    manifest = make_manifest_dict()
    del manifest["measurements"]["file"]
    with pytest.raises(ValueError, match="file"):
        resolve_measurements_pointer(manifest, "show_manifest.yaml")


@pytest.mark.parametrize(
    "bad_path",
    ["/abs/artifact.yaml", "../outside/artifact.yaml", "a/../../b.yaml"],
    ids=["absolute", "parent", "embedded-parent"],
)
def test_pointer_path_outside_decisions_directory_rejected(bad_path: str) -> None:
    # The pointer path is recorded verbatim in shipped config metadata;
    # absolute paths and traversal break portability of the audit trail.
    manifest = make_manifest_dict()
    manifest["measurements"]["file"] = bad_path
    with pytest.raises(ValueError, match="relative path"):
        resolve_measurements_pointer(manifest, "show_manifest.yaml")


def test_pointer_path_with_control_characters_rejected() -> None:
    manifest = make_manifest_dict()
    manifest["measurements"]["file"] = "measurements/a\nProvenance: forged.yaml"
    with pytest.raises(ValueError, match="unprintable"):
        resolve_measurements_pointer(manifest, "show_manifest.yaml")


def test_unquoted_numeric_digest_rejected() -> None:
    # YAML parses an unquoted all-digit digest as a number, silently
    # corrupting it; the error must say to quote it, not "malformed".
    manifest = make_manifest_dict()
    manifest["measurements"]["sha256"] = 12345678
    with pytest.raises(ValueError, match="quoted string"):
        resolve_measurements_pointer(manifest, "show_manifest.yaml")


def test_missing_artifact_fails(tmp_path: Path) -> None:
    manifest = make_manifest_dict()
    manifest["measurements"]["file"] = "no_such_artifact.yaml"
    manifest_path = tmp_path / "show_manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="no_such_artifact.yaml"):
        load_inputs(str(manifest_path))


def test_artifact_path_resolved_relative_to_show_manifest_file(tmp_path: Path) -> None:
    # The show manifest lives away from the cwd; its pointer must
    # resolve against the show manifest's own directory.
    show_dir = tmp_path / "show"
    (show_dir / "measurements").mkdir(parents=True)
    artifact_path = show_dir / "measurements" / "artifact.yaml"
    artifact_path.write_text(yaml.safe_dump(make_measurements_dict()), encoding="utf-8")
    manifest = make_manifest_dict()
    manifest["measurements"]["file"] = "measurements/artifact.yaml"
    manifest["measurements"]["sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()
    (show_dir / "show_manifest.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )
    _, measurements, _ = load_inputs(str(show_dir / "show_manifest.yaml"))
    assert measurements["luminance"]["peak_luminance"] == PEAK_LUMINANCE
