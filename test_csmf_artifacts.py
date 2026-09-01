"""Reading a CSMF measurements artifact (§spec:provenance).

display-measure writes the measurement of record as CSMF: the spectra
and tristimulus in protobuf, with everything that format does not model
in a provenance block its reserved ancillary field carries. This package
read only the YAML rendering that CSMF replaced.
"""

import hashlib
from pathlib import Path

import pytest

from ocio_display_gen._core import (
    SEAM_SUFFIX,
    parse_measurements_artifact,
)

BENCH = Path("/tmp/bench_config_20260831_2102.csmf")


class TestDispatchesOnSuffix:
    def test_yaml_is_read_as_before_and_hashed_over_its_bytes(
        self, tmp_path: Path
    ) -> None:
        """A YAML artifact's bytes are canonical, so its own bytes are
        what the promotion pointer records."""
        path = tmp_path / "m.yaml"
        path.write_text("schema: x\ncolorimetry: {}\n")
        data = path.read_bytes()

        document, digest = parse_measurements_artifact(data, str(path), "Measurements")

        assert document["schema"] == "x"
        assert digest == hashlib.sha256(data).hexdigest()

    def test_a_non_mapping_yaml_is_still_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "m.yaml"
        path.write_text("- not a mapping\n")

        with pytest.raises(ValueError, match="mapping"):
            parse_measurements_artifact(path.read_bytes(), str(path), "Measurements")


@pytest.mark.skipif(not BENCH.is_file(), reason="needs a measured artifact")
class TestReadsARealSeamFile:
    """The fixture is a real `display-measure characterize` session, so
    this fails if either side of the seam drifts."""

    def test_the_projection_is_read_as_the_measurements_mapping(self) -> None:
        document, _ = parse_measurements_artifact(
            BENCH.read_bytes(), str(BENCH), "Measurements"
        )

        assert document["colorimetry"]["primaries"]["red"]
        assert document["luminance"]["peak_luminance"] > 0

    def test_the_digest_covers_the_projection_not_the_file_bytes(self) -> None:
        """protobuf guarantees round-trip, not canonical encoding, so a
        digest over raw bytes would rotate on a dependency upgrade. The
        artifact's own digest covers its canonical projection, and that
        is what a promotion pointer records."""
        data = BENCH.read_bytes()
        _, digest = parse_measurements_artifact(data, str(BENCH), "Measurements")

        assert digest != hashlib.sha256(data).hexdigest()
        assert len(digest) == 64

    def test_the_artifact_names_the_blocks_it_measured(self) -> None:
        document, _ = parse_measurements_artifact(
            BENCH.read_bytes(), str(BENCH), "Measurements"
        )

        assert "anchors/1" in document["protocol"]["blocks"]

    def test_a_truncated_seam_file_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="Measurements"):
            parse_measurements_artifact(
                b"not a csmf", "x" + SEAM_SUFFIX, "Measurements"
            )


@pytest.mark.skipif(not BENCH.is_file(), reason="needs a measured artifact")
class TestASeamFileVerifiesAgainstItself:
    """A seam file reports its own digest, so trusting that report would
    let a file edited along with its envelope pass the promotion check.
    """

    def test_a_projection_edited_under_its_digest_is_refused(
        self, tmp_path: Path
    ) -> None:
        from ocio_display_gen._core import measurements_digest, read_seam_provenance

        recorded, projection = read_seam_provenance(str(BENCH), "Measurements")
        tampered = tmp_path / "tampered.csmf"
        tampered.write_bytes(
            BENCH.read_bytes().replace(b"peak_luminance", b"peak_lumXnance")
        )

        with pytest.raises(ValueError, match="does not verify against itself"):
            measurements_digest(tampered.read_bytes(), str(tampered), "Measurements")

    def test_an_untampered_file_verifies(self) -> None:
        from ocio_display_gen._core import measurements_digest

        digest, text = measurements_digest(
            BENCH.read_bytes(), str(BENCH), "Measurements"
        )

        assert len(digest) == 64
        assert b"peak_luminance" in text
