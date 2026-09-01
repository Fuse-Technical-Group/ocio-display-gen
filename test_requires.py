"""What a config needs an artifact to carry (§spec:characterization-model)."""

import pytest

from ocio_display_gen.requires import (
    REQUIRES,
    UnsupportedArtifact,
    blocks_carried,
    check,
)


def test_a_config_reads_one_block() -> None:
    """The whole of what a config takes from an artifact — primaries,
    white point, black level, peak luminance — is the anchors."""
    assert REQUIRES == {"anchors": 1}


def test_an_artifact_carrying_the_anchors_passes() -> None:
    check({"protocol": {"blocks": ["anchors/1", "response/1"]}})


def test_a_config_grade_artifact_passes() -> None:
    """Five patches is a whole measurement for this consumer."""
    check({"protocol": {"blocks": ["anchors/1"]}})


def test_an_artifact_without_the_anchors_is_refused_by_name() -> None:
    with pytest.raises(UnsupportedArtifact, match="anchors"):
        check({"protocol": {"blocks": ["response/1", "additivity/1"]}})


def test_an_older_block_is_refused_with_what_it_carries() -> None:
    with pytest.raises(UnsupportedArtifact, match="anchors/0"):
        check({"protocol": {"blocks": ["anchors/0"]}})


def test_a_legacy_protocol_name_maps_to_its_blocks() -> None:
    """Every artifact measured before blocks were recorded carries a
    name instead, and their compositions are known."""
    carried = blocks_carried({"protocol": {"name": "color-wrangler/characterize/3"}})

    assert carried == {"anchors": 1, "response": 1, "additivity": 1}
    check({"protocol": {"name": "color-wrangler/characterize/3"}})


def test_an_artifact_that_records_no_protocol_is_not_refused() -> None:
    """The reference format and third-party files say nothing about
    blocks, and are validated on their contents as they always were."""
    assert blocks_carried({"colorimetry": {}}) is None
    check({"colorimetry": {}})
