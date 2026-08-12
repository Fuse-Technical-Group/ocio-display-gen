#!/usr/bin/env python3
"""Tests for the verification handoff (§spec:verification): the probe
patch set, predicted on-wall XYZ, the predictions artifact's round-trip
and config binding, and the probe imagery the session displays.

The ΔE2000 and unity-exponent analysis lives here rather than in the
product: OLE-Toolset owns validation reports (§spec:non-goals). These
tests stand in for that consumer and prove the artifact carries enough
to do the job.
"""

import hashlib
import shutil
import zlib
from pathlib import Path
from typing import Any, Dict, List, Tuple

import colour
import numpy as np
import numpy.typing as npt
import OpenEXR
import PyOpenColorIO as OCIO
import pytest
import yaml  # type: ignore[import]

from conftest import (
    SAMPLE_ARTIFACT_NAME,
    SAMPLE_MANIFEST_PATH,
    SAMPLE_PEAK_LUMINANCE,
    load_sample_inputs,
)
from OCIODisplayGen import (
    D65_WHITE_XY,
    GENERATOR_VERSION,
    PREDICTIONS_SCHEMA,
    PROBE_CODE_LEVELS,
    PROBE_PATCH_PIXELS,
    PROBE_PATCHES,
    DisplayCharacterization,
    PatchPrediction,
    _apply_rgb,
    _format_float,
    _quantize_code_value,
    _recorded_triple,
    build_predictions,
    check_predictions,
    create_base_ocio_config,
    create_characterization,
    create_display_colorspace_from_characterization,
    derive_reference_spaces,
    emit_predictions,
    main,
    parse_predictions,
    probe_directory,
    probe_patch_exr,
    probe_patch_png,
    register_display,
    write_probe_imagery,
)

# The nits anchor the shipped show manifest records.
NITS_ANCHOR = 300.0

# Umbrella verification thresholds (§spec:verification).
DELTA_E_AVG_LIMIT = 2.0
DELTA_E_MAX_LIMIT = 5.0


def generate_sample_config(path: Path) -> Tuple[str, DisplayCharacterization]:
    """The shipped sample inputs through the real generation path,
    serialized to path. Returns the registered display and its
    characterization."""
    manifest, measurements = load_sample_inputs()
    assert manifest["ocio"]["vp_radiometric"]["nits_anchor"] == NITS_ANCHOR
    char = create_characterization(manifest, measurements)
    config = create_base_ocio_config(manifest)
    cs = create_display_colorspace_from_characterization(char)
    display = register_display(config, cs, char, nits_anchor=NITS_ANCHOR)
    path.write_text(config.serialize(), encoding="utf-8")
    return display, char


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Tuple[Path, Any]:
    """The sample wall generated to disk, plus its predictions."""
    path = tmp_path_factory.mktemp("predictions") / "wall.ocio"
    display, char = generate_sample_config(path)
    config = OCIO.Config.CreateFromFile(str(path))
    predictions = build_predictions(config, display, char, NITS_ANCHOR, str(path))
    return path, predictions


# ---------------------------------------------------------------------
# The probe patch set (documented and stable)
# ---------------------------------------------------------------------


def test_probe_patch_ids_are_unique(generated: Tuple[Path, Any]) -> None:
    """Patch ids key the measurement file a session writes back, so a
    duplicate would silently drop a patch from the comparison."""
    _, predictions = generated
    ids = [patch.id for patch in PROBE_PATCHES]
    assert len(ids) == len(set(ids))
    # Predictions are emitted in patch-set order — the order a session
    # measures in.
    assert [patch.id for patch in predictions.patches] == ids


def test_probe_patch_drives_are_within_full_drive() -> None:
    for patch in PROBE_PATCHES:
        assert len(patch.drive) == 3
        assert all(0.0 <= channel <= 1.0 for channel in patch.drive)


def test_probe_patch_set_spans_black_to_full_neutral() -> None:
    drives = {patch.id: patch.drive for patch in PROBE_PATCHES}
    assert drives["neutral_000"] == (0.0, 0.0, 0.0)
    assert drives["neutral_100"] == (1.0, 1.0, 1.0)
    # Six chromatic axes at full drive probe the wall's gamut boundary.
    for axis in ("red", "green", "blue", "cyan", "magenta", "yellow"):
        assert f"{axis}_100" in drives


# ---------------------------------------------------------------------
# Prediction math against colour-science
# ---------------------------------------------------------------------


def wall_native_to_xyz_matrix(
    char: DisplayCharacterization,
) -> npt.NDArray[np.float64]:
    """Wall native RGB → CIE XYZ (D65-adapted), built independently of
    the generator: derived primaries matrix, then wall white → D65."""
    space = colour.RGB_Colourspace(
        char.name,
        np.array(
            [char.primaries["red"], char.primaries["green"], char.primaries["blue"]]
        ),
        np.asarray(char.white_point),
        "Wall White",
    )
    space.use_derived_transformation_matrices()
    cat = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        colour.xy_to_XYZ(np.asarray(char.white_point)),
        colour.xy_to_XYZ(np.array(D65_WHITE_XY)),
        transform="CAT02",
    )
    return np.asarray(cat @ space.matrix_RGB_to_XYZ, dtype=np.float64)


def ap0_to_xyz_d65_matrix() -> npt.NDArray[np.float64]:
    """ACES2065-1 → CIE XYZ (D65-adapted, Bradford), built in colour-
    science — the independent reference for the AP0 → display-reference
    builtin the generator takes from OCIO."""
    ap0 = colour.RGB_COLOURSPACES["ACES2065-1"]
    cat = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        colour.xy_to_XYZ(ap0.whitepoint),
        colour.xy_to_XYZ(np.array(D65_WHITE_XY)),
        transform="Bradford",
    )
    return np.asarray(cat @ ap0.matrix_RGB_to_XYZ, dtype=np.float64)


def test_predicted_xyz_matches_colour_science_display_model(
    generated: Tuple[Path, Any],
) -> None:
    """Every prediction states: drive the wall with these code values and
    it emits this XYZ. Reproduce that claim with colour-science."""
    _, predictions = generated
    manifest, measurements = load_sample_inputs()
    char = create_characterization(manifest, measurements)
    matrix = wall_native_to_xyz_matrix(char)
    for patch in predictions.patches:
        linear = np.asarray(patch.code_value, dtype=np.float64) ** char.gamma_value
        expected = matrix @ (linear * char.peak_luminance)
        assert np.allclose(patch.xyz, expected, rtol=2e-4, atol=2e-4), patch.id


def test_neutral_ramp_matches_colour_science_end_to_end(
    generated: Tuple[Path, Any],
) -> None:
    """Neutrals sit in the gamut compressor's untouched core, so the
    whole scene-linear → on-wall chain reduces to anchor scale plus the
    AP0 → XYZ matrix."""
    _, predictions = generated
    matrix = ap0_to_xyz_d65_matrix()
    neutrals = [p for p in predictions.patches if p.id.startswith("neutral_")]
    assert len(neutrals) >= 5
    for patch in neutrals:
        scene = np.asarray(patch.scene_linear, dtype=np.float64)
        expected = matrix @ (scene * NITS_ANCHOR)
        assert np.allclose(patch.xyz, expected, rtol=1e-3, atol=1e-3), patch.id


def log_log_slope(pairs: List[Tuple[float, float]]) -> float:
    """Fitted exponent relating the second value to the first."""
    xs, ys = zip(*pairs)
    return float(np.polyfit(np.log(xs), np.log(ys), 1)[0])


def measurable_neutrals(
    predictions: Tuple[PatchPrediction, ...],
) -> Tuple[PatchPrediction, ...]:
    """Ramp patches bright enough to fit a slope through — black and the
    near-black steps carry no usable log-log signal."""
    return tuple(
        p for p in predictions if p.id.startswith("neutral_") and p.xyz[1] > 1.0
    )


def test_neutral_ramp_has_unity_system_gamma(generated: Tuple[Path, Any]) -> None:
    """Scene-linear light and on-wall light are proportional: the defining
    property of VP Radiometric (§spec:view-transform)."""
    _, predictions = generated
    exponent = log_log_slope(
        [
            (p.scene_linear[1], p.xyz[1])
            for p in measurable_neutrals(predictions.patches)
        ]
    )
    assert exponent == pytest.approx(1.0, abs=1e-3)


# ---------------------------------------------------------------------
# The predictions artifact
# ---------------------------------------------------------------------


def test_predictions_record_the_config_hash(generated: Tuple[Path, Any]) -> None:
    config_path, predictions = generated
    assert (
        predictions.config_sha256
        == hashlib.sha256(config_path.read_bytes()).hexdigest()
    )
    assert predictions.config_file == config_path.name


def test_predictions_round_trip_byte_identical(generated: Tuple[Path, Any]) -> None:
    _, predictions = generated
    text = emit_predictions(predictions)
    reparsed = parse_predictions(text.encode("utf-8"), "wall.predictions.yaml")
    assert emit_predictions(reparsed) == text
    assert reparsed == predictions


def test_predictions_header_is_greppable(generated: Tuple[Path, Any]) -> None:
    _, predictions = generated
    document = yaml.safe_load(emit_predictions(predictions))
    assert document["schema"] == PREDICTIONS_SCHEMA
    assert document["generator"] == f"ociodisplaygen {GENERATOR_VERSION}"
    assert document["view"] == predictions.view
    assert document["config"]["sha256"] == predictions.config_sha256
    assert len(document["patches"]) == len(PROBE_PATCHES)


@pytest.mark.parametrize(
    "value,text",
    [
        (0.0, "0.0"),
        (-0.0, "0.0"),
        (-1e-12, "0.0"),  # below recorded precision, and not "-0.0"
        (10.0, "10.0"),  # trailing zeros stripped, integer part intact
        (1000.0, "1000.0"),
        (1e-9, "0.000000001"),  # no exponent: PyYAML would read it as text
        (-3.25, "-3.25"),
    ],
)
def test_recorded_floats_are_fixed_point(value: float, text: str) -> None:
    """Float formatting carries the round-trip guarantee, and stripping
    trailing zeros is easy to get wrong at the decimal point."""
    assert _format_float(value) == text
    assert isinstance(yaml.safe_load(f"value: {text}"), dict)
    assert yaml.safe_load(f"value: {text}")["value"] == pytest.approx(value, abs=1e-9)


def test_predictions_carry_no_exponent_notation(generated: Tuple[Path, Any]) -> None:
    """PyYAML's core schema reads '1e-09' as a string, which would break
    the artifact for every consumer."""
    _, predictions = generated
    assert "e-" not in emit_predictions(predictions).lower().split("patches:")[1]


def test_predictions_reject_malformed_documents() -> None:
    with pytest.raises(ValueError, match="schema"):
        parse_predictions(b"schema: someone-elses/predictions/9\n", "bogus.yaml")
    with pytest.raises(ValueError, match="not valid YAML|must be a YAML mapping"):
        parse_predictions(b"- just\n- a\n- list\n", "bogus.yaml")


def tampered_predictions(generated: Tuple[Path, Any], **fields: str) -> bytes:
    """The sample predictions with named top-level or config fields
    replaced — a hand-edited artifact arriving from another facility."""
    _, predictions = generated
    document = yaml.safe_load(emit_predictions(predictions))
    for key, value in fields.items():
        if key == "config_file":
            document["config"]["file"] = value
        else:
            document[key] = value
    return yaml.safe_dump(document).encode("utf-8")


ESCAPING_NAMES = [
    "../victim.ocio",
    "../../etc/passwd",
    "/etc/passwd",
    "sub/dir/wall.ocio",
    "..\\victim.ocio",  # a separator on NT, an ordinary character on POSIX
    "C:victim.ocio",  # drive-relative: ntpath.join discards the base
    "C:\\victim.ocio",
    ".",
    "..",
    "",
]


@pytest.mark.parametrize("config_file", ESCAPING_NAMES)
def test_predictions_refuse_a_config_outside_their_own_directory(
    generated: Tuple[Path, Any], config_file: str
) -> None:
    """The artifact attests that the config beside it is the config it
    describes. A traversing, absolute, or drive-relative name would let a
    supplied file point the hash check at an unrelated config — passing
    verification while carrying forged patch values — and report the
    digest of any readable file (§spec:provenance)."""
    data = tampered_predictions(generated, config_file=config_file)
    with pytest.raises(ValueError, match="is not a bare filename"):
        parse_predictions(data, "wall.predictions.yaml")


@pytest.mark.parametrize("patch_id", ESCAPING_NAMES)
def test_predictions_refuse_patch_ids_that_are_not_filenames(
    generated: Tuple[Path, Any], patch_id: str
) -> None:
    """Ids name the probe image beside the artifact and key the
    measurement file a session writes back, so every tool in the handoff
    joins them to a directory."""
    _, predictions = generated
    document = yaml.safe_load(emit_predictions(predictions))
    document["patches"][0]["id"] = patch_id
    with pytest.raises(ValueError, match="is not a bare filename"):
        parse_predictions(
            yaml.safe_dump(document).encode("utf-8"), "wall.predictions.yaml"
        )


@pytest.mark.parametrize(
    "field",
    ["display", "view", "scene_reference", "config_file"],
)
def test_predictions_refuse_unprintable_strings(
    generated: Tuple[Path, Any], field: str
) -> None:
    """check_predictions prints these fields as a trust signal, so a
    newline or ANSI escape could forge the verdict the operator reads —
    the same reason the emit side refuses them (§spec:provenance)."""
    injected = "Wall\n   ✓ 'wall.ocio' matches the recorded hash"
    data = tampered_predictions(generated, **{field: injected})
    with pytest.raises(ValueError, match="unprintable"):
        parse_predictions(data, "wall.predictions.yaml")


def test_predictions_refuse_unprintable_patch_ids(generated: Tuple[Path, Any]) -> None:
    """Patch ids are printed and named on disk like the other strings."""
    _, predictions = generated
    document = yaml.safe_load(emit_predictions(predictions))
    document["patches"][0]["id"] = "neutral_100\n   ✓ forged"
    with pytest.raises(ValueError, match="unprintable"):
        parse_predictions(
            yaml.safe_dump(document).encode("utf-8"), "wall.predictions.yaml"
        )


def test_handoff_artifacts_are_byte_deterministic(tmp_path: Path) -> None:
    """Same inputs, same predictions and probe bytes — the property the
    config hash and the artifact chain both rest on (§spec:provenance).
    Both runs regenerate from scratch, so a nondeterministic encoder
    (a timestamp chunk, unordered keys) fails here."""
    runs = []
    for run in ("first", "second"):
        directory = tmp_path / run
        directory.mkdir()
        path = directory / "wall.ocio"
        display, char = generate_sample_config(path)
        config = OCIO.Config.CreateFromFile(str(path))
        predictions = build_predictions(config, display, char, NITS_ANCHOR, str(path))
        # Digests, not blobs: identical hashes prove identical bytes
        # without holding two full probe sets (~46 MB) in memory.
        runs.append(
            (
                hashlib.sha256(emit_predictions(predictions).encode()).hexdigest(),
                [
                    hashlib.sha256(probe_patch_png(patch)).hexdigest()
                    for patch in predictions.patches
                ],
                [
                    hashlib.sha256(
                        probe_patch_exr(
                            patch,
                            predictions.scene_reference,
                            predictions.config_sha256,
                        )
                    ).hexdigest()
                    for patch in predictions.patches
                ],
            )
        )
    assert runs[0] == runs[1]


# ---------------------------------------------------------------------
# Consumer analysis: ΔE2000 and the unity-exponent criterion
# ---------------------------------------------------------------------


def write_synthetic_measurements(
    path: Path, patches: Tuple[PatchPrediction, ...]
) -> Path:
    """A session's measurement file for a perfect wall: measured XYZ
    equal to the prediction."""
    path.write_text(
        yaml.safe_dump({p.id: [float(v) for v in p.xyz] for p in patches}),
        encoding="utf-8",
    )
    return path


def delta_e2000(
    predictions: Tuple[PatchPrediction, ...], measured: Dict[str, List[float]]
) -> Dict[str, float]:
    """Per-patch ΔE2000, referencing both sides to the predicted white."""
    white = next(p.xyz for p in predictions if p.id == "neutral_100")
    white_xyz = np.asarray(white, dtype=np.float64)
    white_xy = colour.XYZ_to_xy(white_xyz)

    def lab(xyz: List[float]) -> Any:
        return colour.XYZ_to_Lab(
            np.asarray(xyz, dtype=np.float64) / white_xyz[1], white_xy
        )

    return {
        patch.id: float(
            colour.difference.delta_E_CIE2000(
                lab(list(patch.xyz)), lab(measured[patch.id])
            )
        )
        for patch in predictions
    }


def unity_exponent(
    predictions: Tuple[PatchPrediction, ...], measured: Dict[str, List[float]]
) -> float:
    """Log-log slope of measured against predicted neutral luminance —
    1.0 when the wall tracks the prediction with no residual gamma."""
    return log_log_slope(
        [(p.xyz[1], measured[p.id][1]) for p in measurable_neutrals(predictions)]
    )


def test_perfect_wall_passes_the_umbrella_thresholds(
    generated: Tuple[Path, Any], tmp_path: Path
) -> None:
    _, predictions = generated
    measurement_file = write_synthetic_measurements(
        tmp_path / "measured.yaml", predictions.patches
    )
    measured = yaml.safe_load(measurement_file.read_text(encoding="utf-8"))
    deltas = delta_e2000(predictions.patches, measured)
    assert max(deltas.values()) == pytest.approx(0.0, abs=1e-9)
    assert np.mean(list(deltas.values())) < DELTA_E_AVG_LIMIT
    assert unity_exponent(predictions.patches, measured) == pytest.approx(1.0, abs=1e-9)


def test_perturbed_patch_is_flagged(
    generated: Tuple[Path, Any], tmp_path: Path
) -> None:
    _, predictions = generated
    measurement_file = write_synthetic_measurements(
        tmp_path / "measured.yaml", predictions.patches
    )
    measured = yaml.safe_load(measurement_file.read_text(encoding="utf-8"))
    # A hue shift on one mid-drive patch, well inside instrument noise
    # for luminance but far outside the ΔE budget.
    target = "green_050"
    measured[target] = [
        measured[target][0] * 1.7,
        measured[target][1],
        measured[target][2] * 1.7,
    ]
    deltas = delta_e2000(predictions.patches, measured)
    flagged = [patch for patch, value in deltas.items() if value > DELTA_E_AVG_LIMIT]
    assert flagged == [target]
    assert deltas[target] > DELTA_E_MAX_LIMIT


def test_perturbed_ramp_breaks_the_unity_exponent(
    generated: Tuple[Path, Any], tmp_path: Path
) -> None:
    _, predictions = generated
    measurement_file = write_synthetic_measurements(
        tmp_path / "measured.yaml", predictions.patches
    )
    measured = yaml.safe_load(measurement_file.read_text(encoding="utf-8"))
    # A wall running an extra gamma of 1.1 on top of the config.
    for patch in predictions.patches:
        if patch.id.startswith("neutral_") and patch.xyz[1] > 0.0:
            measured[patch.id] = [
                value * (patch.xyz[1] / 1000.0) ** 0.1 for value in patch.xyz
            ]
    assert unity_exponent(predictions.patches, measured) == pytest.approx(1.1, abs=1e-6)


# ---------------------------------------------------------------------
# Probe imagery
# ---------------------------------------------------------------------


def png_pixel(data: bytes) -> Tuple[int, int, int]:
    """First pixel of a 16-bit truecolor PNG, decoded from the raw
    chunks — no image library, so the writer is checked against the
    format rather than against itself."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    chunks: Dict[bytes, bytes] = {}
    offset = 8
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        crc = int.from_bytes(data[offset + 8 + length : offset + 12 + length], "big")
        assert crc == zlib.crc32(kind + payload), kind
        chunks[kind] = payload
        offset += 12 + length
    width = int.from_bytes(chunks[b"IHDR"][0:4], "big")
    height = int.from_bytes(chunks[b"IHDR"][4:8], "big")
    assert (width, height) == (PROBE_PATCH_PIXELS, PROBE_PATCH_PIXELS)
    assert chunks[b"IHDR"][8:13] == bytes([16, 2, 0, 0, 0])
    raw = zlib.decompress(chunks[b"IDAT"])
    assert len(raw) == height * (1 + width * 6)
    assert raw[0] == 0  # filter type "None"
    return (
        int.from_bytes(raw[1:3], "big"),
        int.from_bytes(raw[3:5], "big"),
        int.from_bytes(raw[5:7], "big"),
    )


def test_probe_image_encodes_the_patch_code_value(
    generated: Tuple[Path, Any],
) -> None:
    _, predictions = generated
    for patch in predictions.patches:
        pixel = png_pixel(probe_patch_png(patch))
        assert pixel == tuple(round(c * PROBE_CODE_LEVELS) for c in patch.code_value), (
            patch.id
        )


def test_write_probe_imagery_names_two_files_per_patch(
    generated: Tuple[Path, Any], probe_dir: Path
) -> None:
    """Each patch gets a PNG (code-value record) and an EXR
    (scene-linear renderer stimulus) sharing the patch id as stem, in
    patch order — the documented contract of the returned path list."""
    _, predictions = generated
    written = [path.name for path in probe_dir_written(probe_dir)]
    assert written == [
        f"{patch.id}{suffix}"
        for patch in predictions.patches
        for suffix in (".png", ".exr")
    ]


# ---------------------------------------------------------------------
# Scene-linear EXR probes (§spec:verification)
# ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def probe_dir(
    generated: Tuple[Path, Any], tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """The probe imagery written once, into the directory name
    production derives (probe_directory), for every probe test."""
    config_path, predictions = generated
    directory = Path(
        probe_directory(str(tmp_path_factory.mktemp("probe") / config_path.name))
    )
    written = write_probe_imagery(str(directory), predictions)
    (directory / ".written-order").write_text(
        "\n".join(Path(item).name for item in written)
    )
    return directory


def probe_dir_written(directory: Path) -> List[Path]:
    """The paths write_probe_imagery returned, in its documented order."""
    names = (directory / ".written-order").read_text().splitlines()
    return [directory / name for name in names]


def read_exr(path: Path) -> Tuple[Dict[str, Any], npt.NDArray[np.float32]]:
    """Header and pixels of an EXR through the OpenEXR library — the
    independent reader, so the hand-rolled writer is checked against
    the format rather than against itself."""
    exr = OpenEXR.File(str(path))
    channels = exr.channels()
    assert set(channels) == {"RGB"}
    return exr.header(), channels["RGB"].pixels


def test_probe_exr_reads_back_the_recorded_scene_linear(
    generated: Tuple[Path, Any], probe_dir: Path
) -> None:
    """The EXR holds exactly the scene-linear triple the predictions
    file records — two views of one computation, like the PNGs."""
    _, predictions = generated
    for patch in predictions.patches:
        _, pixels = read_exr(probe_dir / f"{patch.id}.exr")
        assert pixels.dtype == np.float32
        assert pixels.shape == (PROBE_PATCH_PIXELS, PROBE_PATCH_PIXELS, 3)
        expected = np.asarray(patch.scene_linear, dtype=np.float32)
        assert (pixels == expected).all(), patch.id


def test_probe_exr_header_is_self_describing(
    generated: Tuple[Path, Any], probe_dir: Path
) -> None:
    """sceneReference and configSha256 attributes bind each EXR to the
    space it is stated in and the config it verifies, so a renderer
    operator needs nothing beyond the file (§spec:provenance)."""
    _, predictions = generated
    header, _ = read_exr(probe_dir / "neutral_100.exr")
    assert header["sceneReference"] == predictions.scene_reference
    assert header["configSha256"] == predictions.config_sha256
    assert header["compression"] == OpenEXR.NO_COMPRESSION


def test_probe_exr_through_default_view_reproduces_code_values(
    generated: Tuple[Path, Any], probe_dir: Path
) -> None:
    """The renderer-path criterion (§spec:verification): displaying an
    EXR through the generated config's default (display, view)
    reproduces the patch's predicted code values on the probe
    imagery's quantization grid."""
    config_path, predictions = generated
    config = OCIO.Config.CreateFromFile(str(config_path))
    assert config.getDefaultView(predictions.display) == predictions.view
    processor = config.getProcessor(
        predictions.scene_reference,
        predictions.display,
        predictions.view,
        OCIO.TRANSFORM_DIR_FORWARD,
    ).getDefaultCPUProcessor()
    for patch in predictions.patches:
        _, pixels = read_exr(probe_dir / f"{patch.id}.exr")
        emitted = _apply_rgb(processor, pixels[0, 0])
        # One quantization rule, owned by the module (§spec:verification):
        # the artifact records code values at nine decimals, so the
        # comparison lands there too.
        quantized = _quantize_code_value(emitted)
        assert _recorded_triple(quantized) == patch.code_value, patch.id


# ---------------------------------------------------------------------
# CLI surface (§road:probe-patches integration point)
# ---------------------------------------------------------------------


def copy_samples(tmp_path: Path) -> None:
    repo = SAMPLE_MANIFEST_PATH.parent
    (tmp_path / "measurements").mkdir()
    for name in (
        "show_manifest.yaml",
        "validation_settings.yaml",
        SAMPLE_ARTIFACT_NAME,
    ):
        (tmp_path / name).write_bytes((repo / name).read_bytes())


@pytest.fixture(scope="module")
def cli_output(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One default CLI run of the shipped samples. Module-scoped: the run
    loads and validates the ACES studio config, which every consuming
    test would otherwise repeat. Copy it before mutating it."""
    directory = tmp_path_factory.mktemp("cli")
    copy_samples(directory)
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(directory)
        patch.setattr("sys.argv", ["OCIODisplayGen.py"])
        main()
    return directory


def test_check_predictions_audits_probe_exr_bindings(cli_output: Path) -> None:
    """check_predictions sweeps the sibling probe directory: intact EXRs
    pass the audit; an EXR naming a different config is refused — the
    stale-artifact failure the audit exists to catch (§spec:provenance)."""
    import shutil

    directory = cli_output / "audit"
    directory.mkdir()
    for name in (
        "custom_display_config.ocio",
        "custom_display_config.predictions.yaml",
    ):
        shutil.copy(cli_output / name, directory / name)
    shutil.copytree(
        cli_output / "custom_display_config.probe",
        directory / "custom_display_config.probe",
    )
    predictions_path = directory / "custom_display_config.predictions.yaml"
    check_predictions(str(predictions_path))  # intact: passes

    predictions = parse_predictions(
        predictions_path.read_bytes(), str(predictions_path)
    )
    stale = probe_patch_exr(
        predictions.patches[0], predictions.scene_reference, "0" * 64
    )
    exr_path = (
        directory / "custom_display_config.probe" / f"{predictions.patches[0].id}.exr"
    )
    exr_path.write_bytes(stale)
    with pytest.raises(ValueError, match="different generation"):
        check_predictions(str(predictions_path))


def test_cli_emits_predictions_and_probe_imagery(cli_output: Path) -> None:
    config = cli_output / "custom_display_config.ocio"
    predictions_path = cli_output / "custom_display_config.predictions.yaml"
    probe_dir = cli_output / "custom_display_config.probe"
    assert config.is_file()
    predictions = parse_predictions(
        predictions_path.read_bytes(), str(predictions_path)
    )
    assert predictions.config_file == config.name
    assert predictions.config_sha256 == hashlib.sha256(config.read_bytes()).hexdigest()
    assert len(list(probe_dir.glob("*.png"))) == len(PROBE_PATCHES)
    assert len(list(probe_dir.glob("*.exr"))) == len(PROBE_PATCHES)


def test_cli_checks_a_predictions_file_against_its_config(
    cli_output: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Tampering below would poison the shared run, so work on a copy.
    shutil.copytree(cli_output, tmp_path, dirs_exist_ok=True)
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()

    monkeypatch.setattr(
        "sys.argv",
        [
            "OCIODisplayGen.py",
            "--check-predictions",
            "custom_display_config.predictions.yaml",
        ],
    )
    main()
    output = capsys.readouterr().out
    assert "custom_display_config.ocio" in output
    assert "matches" in output

    (tmp_path / "custom_display_config.ocio").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
    assert "does not match" in capsys.readouterr().out


def test_scene_reference_and_display_are_recorded(generated: Tuple[Path, Any]) -> None:
    config_path, predictions = generated
    config = OCIO.Config.CreateFromFile(str(config_path))
    scene_reference, _ = derive_reference_spaces(config)
    assert predictions.scene_reference == scene_reference
    assert predictions.display in [str(d) for d in config.getDisplays()]
    assert predictions.view in [str(v) for v in config.getViews(predictions.display)]


def test_predicted_white_is_the_measured_peak(generated: Tuple[Path, Any]) -> None:
    """Full neutral drive lands on the measured white at the measured
    peak — the anchor and the display model agree."""
    _, predictions = generated
    white = next(p for p in predictions.patches if p.id == "neutral_100")
    assert white.xyz[1] == pytest.approx(SAMPLE_PEAK_LUMINANCE, rel=1e-3)
    xy = colour.XYZ_to_xy(np.asarray(white.xyz, dtype=np.float64))
    assert xy == pytest.approx(D65_WHITE_XY, abs=1e-3)
