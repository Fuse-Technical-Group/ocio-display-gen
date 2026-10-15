"""A black level the instrument could not read (§spec:characterization-model).

display-measure records a black its instrument refused as too dark as a
bound below the instrument's floor (measurements schema 3), never a
value. The generator carries the bound as one: the description says
"below", contrast is a lower bound labelled instrument-limited, and the
contrast plausibility check does not run on it.

The real fixture is a dark-room session: a PR-655 on a BP2 (NS) behind an
SX40, 2026-10-15, whose black the PR-655 refused below its stated
3.4 cd/m² floor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import]

from ocio_display_gen import generate
from ocio_display_gen._core import (
    black_level_bound,
    create_characterization,
    describe_black,
    describe_contrast,
    load_inputs,
    parse_measurements_artifact,
    validate_measurements_data,
)

REPO = Path(__file__).parent
DARK = "measurements/sx40_pr655_dark_20261015.csmf"
# The promotion hash the session reported at handoff.
DARK_SHA256 = "31db7eb437538c3c85c8c510529774da89403ffe4ff6fb58e8a34971249dc375"


def _specio_available() -> bool:
    try:
        import specio.serialization.csmf  # noqa: F401
    except ImportError:
        return False
    return True


csmf = pytest.mark.skipif(
    not _specio_available(), reason="needs the csmf extra (colour-specio)"
)


def _sample_manifest() -> dict[str, Any]:
    text = (REPO / "show_manifest.yaml").read_text(encoding="utf-8")
    return dict(yaml.safe_load(text))


def bounded_measurements() -> dict[str, Any]:
    """The shipped sample artifact, with its black refused as too dark."""
    path = REPO / _sample_manifest()["measurements"]["file"]
    measurements: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    measurements["luminance"]["black_level"] = {
        "below": 3.4,
        "floor_source": "nominal: PR-655/670 User Manual, Table 12",
    }
    return measurements


class TestABoundedBlack:
    def test_the_bound_is_read_as_one(self) -> None:
        assert black_level_bound(bounded_measurements()["luminance"]) == 3.4
        assert black_level_bound({"black_level": 0.02}) is None

    def test_the_characterization_says_below_and_instrument_limited(self) -> None:
        char = create_characterization(_sample_manifest(), bounded_measurements())

        assert char.black_level_bound == pytest.approx(3.4)
        assert describe_black(char) == "below 3.4 cd/m² (instrument floor)"
        assert describe_contrast(char).startswith("at least ")
        assert describe_contrast(char).endswith("(instrument-limited)")

    def test_a_reading_is_described_as_before(self) -> None:
        manifest = _sample_manifest()
        path = REPO / manifest["measurements"]["file"]
        measurements = yaml.safe_load(path.read_text(encoding="utf-8"))

        char = create_characterization(manifest, measurements)

        assert char.black_level_bound is None
        assert describe_black(char).endswith(" cd/m²")
        assert not describe_contrast(char).startswith("at least")

    def test_validation_skips_the_contrast_check(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        settings = {"min_contrast_ratio": 100000}

        assert validate_measurements_data(
            bounded_measurements(), settings, strict_mode=True
        )
        assert "instrument-limited" in capsys.readouterr().out


def dark_manifest(tmp_path: Path) -> Path:
    """The shipped show manifest, promoting the dark-room seam file."""
    manifest = _sample_manifest()
    target = tmp_path / DARK
    target.parent.mkdir(parents=True)
    target.write_bytes((REPO / DARK).read_bytes())
    manifest["measurements"] = {"file": DARK, "sha256": DARK_SHA256}
    path = tmp_path / "show_manifest.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return path


@csmf
class TestTheDarkRoomSession:
    def test_it_is_schema_3_with_a_bounded_black(self) -> None:
        data = (REPO / DARK).read_bytes()

        document, digest = parse_measurements_artifact(data, DARK, "Measurements")

        assert digest == DARK_SHA256
        assert document["schema"] == "color-wrangler/measurements/3"
        assert black_level_bound(document["luminance"]) == pytest.approx(3.4)

    def test_it_loads_under_its_promotion_hash(self, tmp_path: Path) -> None:
        _, measurements, provenance = load_inputs(str(dark_manifest(tmp_path)))

        assert provenance.measurements_sha256 == DARK_SHA256
        assert measurements["instrument"]["model"] == "PR655"

    def test_it_generates_a_config_that_says_below(self, tmp_path: Path) -> None:
        result = generate(dark_manifest(tmp_path), output_dir=tmp_path / "out")

        assert result.characterization.black_level_bound == pytest.approx(3.4)
        text = result.config_path.read_text(encoding="utf-8")
        assert f"measurements sha256={DARK_SHA256}" in text
        assert "below 3.4 cd/m² (instrument floor)" in text
