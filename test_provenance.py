#!/usr/bin/env python3
"""Tests for hash binding (§spec:provenance): promotion-hash enforcement,
provenance metadata in the generated config, and byte-determinism."""

import hashlib
import shutil
import tomllib
from pathlib import Path
from typing import Any

import PyOpenColorIO as OCIO
import pytest
import yaml  # type: ignore[import]

from conftest import (
    ACES2_STUDIO_CONFIG_URI,
    SAMPLE_ARTIFACT_NAME,
    SAMPLE_MANIFEST_PATH,
)
from ocio_display_gen._core import (
    GENERATOR_VERSION,
    create_characterization,
    create_display_colorspace_from_characterization,
    load_inputs,
    record_provenance,
    register_display,
)

REPO_DIR = SAMPLE_MANIFEST_PATH.parent
ARTIFACT_NAME = SAMPLE_ARTIFACT_NAME


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_samples(tmp_path: Path) -> Path:
    """Copy the shipped sample inputs into tmp_path; return manifest path."""
    (tmp_path / "measurements").mkdir()
    shutil.copy(REPO_DIR / "show_manifest.yaml", tmp_path / "show_manifest.yaml")
    shutil.copy(REPO_DIR / ARTIFACT_NAME, tmp_path / ARTIFACT_NAME)
    return tmp_path / "show_manifest.yaml"


def rewrite_pointer_sha256(manifest_path: Path, sha256: str) -> None:
    """Rewrite the promotion pointer's recorded digest in place."""
    data: dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    data["measurements"]["sha256"] = sha256
    manifest_path.write_text(yaml.safe_dump(data), encoding="utf-8")


def generate_config_text(manifest_path: Path) -> str:
    """The library generation path: load, characterize, register, record."""
    manifest, measurements, provenance = load_inputs(str(manifest_path))
    char = create_characterization(manifest, measurements)
    config = OCIO.Config.CreateFromFile(ACES2_STUDIO_CONFIG_URI)
    cs = create_display_colorspace_from_characterization(char)
    register_display(config, cs, char)
    record_provenance(config, provenance, manifest.get("show", {}).get("description"))
    return config.serialize()


# Provenance metadata (§spec:provenance): the generated config's
# top-level description records both input hashes and the generator
# version, each on a greppable line.


def test_description_carries_input_hashes_and_version(tmp_path: Path) -> None:
    manifest_path = copy_samples(tmp_path)
    decisions_sha = sha256_file(manifest_path)
    measurements_sha = sha256_file(tmp_path / ARTIFACT_NAME)

    config_text = generate_config_text(manifest_path)
    reloaded = OCIO.Config.CreateFromStream(config_text)
    description = reloaded.getDescription()

    assert f"show-manifest sha256={decisions_sha}" in description
    assert f"measurements sha256={measurements_sha} ({ARTIFACT_NAME})" in description
    assert f"generator ociodisplaygen {GENERATOR_VERSION}" in description
    assert "Show: FTG Stage 1" in description


def test_provenance_appends_to_base_description(tmp_path: Path) -> None:
    manifest_path = copy_samples(tmp_path)
    base_description = OCIO.Config.CreateFromFile(
        ACES2_STUDIO_CONFIG_URI
    ).getDescription()

    reloaded = OCIO.Config.CreateFromStream(generate_config_text(manifest_path))

    assert base_description.rstrip() in reloaded.getDescription()


def test_show_description_with_control_characters_rejected(tmp_path: Path) -> None:
    # A multi-line description could forge Provenance: lines in the
    # generated config's greppable description block.
    manifest_path = copy_samples(tmp_path)
    manifest, _, provenance = load_inputs(str(manifest_path))
    config = OCIO.Config.CreateFromFile(ACES2_STUDIO_CONFIG_URI)
    forged = "FTG\nProvenance: measurements sha256=deadbeef (x.yaml)"
    with pytest.raises(ValueError, match="unprintable"):
        record_provenance(config, provenance, forged)


def test_unicode_line_separator_in_show_description_rejected(
    tmp_path: Path,
) -> None:
    # U+2028 LINE SEPARATOR is not in the C0/C1 ranges but OCIO's
    # serializer emits it as a physical newline, forging a Provenance:
    # line at column 0 in the raw .ocio file.
    manifest_path = copy_samples(tmp_path)
    _, _, provenance = load_inputs(str(manifest_path))
    config = OCIO.Config.CreateFromFile(ACES2_STUDIO_CONFIG_URI)
    forged = "FTG Provenance: measurements sha256=deadbeef (x.yaml)"
    with pytest.raises(ValueError, match="unprintable"):
        record_provenance(config, provenance, forged)


def test_empty_show_description_emits_no_show_line(tmp_path: Path) -> None:
    manifest_path = copy_samples(tmp_path)
    _, _, provenance = load_inputs(str(manifest_path))
    config = OCIO.Config.CreateFromFile(ACES2_STUDIO_CONFIG_URI)
    record_provenance(config, provenance, "")
    assert "Show:" not in config.getDescription()


def test_non_string_show_description_rejected(tmp_path: Path) -> None:
    manifest_path = copy_samples(tmp_path)
    _, _, provenance = load_inputs(str(manifest_path))
    config = OCIO.Config.CreateFromFile(ACES2_STUDIO_CONFIG_URI)
    with pytest.raises(ValueError, match="string"):
        record_provenance(config, provenance, {"name": "FTG"})  # type: ignore[arg-type]


def test_generator_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((REPO_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert GENERATOR_VERSION == pyproject["project"]["version"]


# Promotion-hash enforcement (§spec:provenance): generation refuses
# when the artifact on disk does not hash to the recorded digest.


def test_tampered_artifact_refuses_naming_both_hashes(tmp_path: Path) -> None:
    manifest_path = copy_samples(tmp_path)
    artifact_path = tmp_path / ARTIFACT_NAME
    recorded_sha = sha256_file(artifact_path)

    tampered = bytearray(artifact_path.read_bytes())
    tampered[-1] ^= 0x01  # flip one bit of the last byte
    artifact_path.write_bytes(bytes(tampered))
    tampered_sha = sha256_file(artifact_path)

    with pytest.raises(ValueError) as excinfo:
        load_inputs(str(manifest_path))
    message = str(excinfo.value)
    assert ARTIFACT_NAME in message
    assert recorded_sha in message
    assert tampered_sha in message


@pytest.mark.parametrize(
    "bad_digest",
    ["0" * 63, "g" * 64, "not a digest"],
    ids=["wrong-length", "non-hex", "free-text"],
)
def test_malformed_recorded_digest_refuses(tmp_path: Path, bad_digest: str) -> None:
    manifest_path = copy_samples(tmp_path)
    rewrite_pointer_sha256(manifest_path, bad_digest)

    with pytest.raises(ValueError, match="malformed"):
        load_inputs(str(manifest_path))


def test_uppercase_recorded_digest_accepted(tmp_path: Path) -> None:
    manifest_path = copy_samples(tmp_path)
    rewrite_pointer_sha256(manifest_path, sha256_file(tmp_path / ARTIFACT_NAME).upper())

    manifest, _, provenance = load_inputs(str(manifest_path))
    assert provenance.measurements_sha256 == sha256_file(tmp_path / ARTIFACT_NAME)


# Byte-determinism (§spec:provenance): hashing and reproducibility
# enforce each other — no timestamps or environment-dependent values.


def test_consecutive_generations_are_byte_identical(tmp_path: Path) -> None:
    manifest_path = copy_samples(tmp_path)
    first = generate_config_text(manifest_path)
    second = generate_config_text(manifest_path)
    assert first == second
