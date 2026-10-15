"""The display colorspace encodes through the measured response, not the
declared exponent (§spec:characterization-model, §road:measured-eotf-lut)."""

import math
from typing import Any, Callable

import numpy as np
import PyOpenColorIO as OCIO
import pytest

from conftest import (
    ACES2_STUDIO_CONFIG_URI,
    FULL_CODE,
    GAMMA,
    LADDER_CODES,
    PEAK_LUMINANCE,
    d65_xyz,
    make_characterization,
    response_from,
)
from ocio_display_gen._core import (
    DisplayCharacterization,
    create_display_colorspace_from_characterization,
    measured_channel_response,
    measured_inverse_eotf,
    register_display,
)
from ocio_display_gen.requires import REQUIRES, UnsupportedArtifact, check

# One 12-bit code, as a fraction of full drive.
ONE_CODE = 1.0 / FULL_CODE
# How far the curve may sit from a local power law between measured
# rungs. The reference itself is an assumption there, so this bounds
# the curve's shape, not its truth.
BETWEEN_RUNGS_TOLERANCE = 3 * ONE_CODE
AT_RUNG_TOLERANCE = 0.1 * ONE_CODE

CHANNELS = ("red", "green", "blue")


def _normalized(law: Callable[[float], float]) -> Callable[[float], float]:
    return lambda c: law(c) / law(1.0)


# A panel that runs well above its declared gamma in the shadows, and
# one that runs well below it: the two directions bench panels take.
RUNS_HIGH = _normalized(lambda c: c**GAMMA * (1 + 1.5 * math.exp(-c / 0.05)))
RUNS_LOW = _normalized(lambda c: c**GAMMA * (1 - 0.75 * math.exp(-c / 0.05)))


def characterization(response: Any) -> DisplayCharacterization:
    char = make_characterization("GAMMA")
    char.channel_response = response
    return char


def curve_cpu(char: DisplayCharacterization) -> OCIO.CPUProcessor:
    """Linear drive relative to full → encoded code value, per channel."""
    return (
        OCIO.Config.CreateRaw()
        .getProcessor(measured_inverse_eotf(char))
        .getDefaultCPUProcessor()
    )


def encode(cpu: OCIO.CPUProcessor, channel: int, linear: float) -> float:
    rgb = [0.0, 0.0, 0.0]
    rgb[channel] = linear
    return float(cpu.applyRGB(rgb)[channel])


def power_law_between(rungs: Any, linear: float) -> float:
    """Code for `linear` on a local power law through the measured rungs."""
    codes, lums = np.array(rungs).T
    return float(np.exp(np.interp(np.log(linear), np.log(lums), np.log(codes))))


@pytest.mark.parametrize("law", [RUNS_HIGH, RUNS_LOW], ids=["runs-high", "runs-low"])
def test_measured_rungs_are_reproduced(law: Callable[[float], float]) -> None:
    char = characterization(response_from(law))
    cpu = curve_cpu(char)
    for index, channel in enumerate(CHANNELS):
        for code, linear in char.channel_response[channel]:
            assert encode(cpu, index, linear) == pytest.approx(
                code, abs=AT_RUNG_TOLERANCE
            ), f"{channel} at code {code * FULL_CODE:.0f}"


@pytest.mark.parametrize("law", [RUNS_HIGH, RUNS_LOW], ids=["runs-high", "runs-low"])
def test_between_rungs_follows_a_local_power_law(
    law: Callable[[float], float],
) -> None:
    char = characterization(response_from(law))
    cpu = curve_cpu(char)
    rungs = char.channel_response["red"]
    lowest = rungs[0][1]
    for linear in np.geomspace(lowest, 1.0, 400):
        assert encode(cpu, 0, float(linear)) == pytest.approx(
            power_law_between(rungs, float(linear)), abs=BETWEEN_RUNGS_TOLERANCE
        )


def test_the_declared_exponent_would_not_reproduce_it() -> None:
    """The measured curve is doing work: the declared gamma misses these
    rungs by far more than the curve's own tolerance."""
    response = response_from(RUNS_HIGH)
    worst = max(abs(linear ** (1.0 / GAMMA) - code) for code, linear in response["red"])
    assert worst > 20 * ONE_CODE


def test_an_ideal_display_encodes_as_the_declared_exponent() -> None:
    cpu = curve_cpu(make_characterization("GAMMA"))
    for linear in (1e-5, 1e-3, 0.18, 0.5, 1.0):
        assert encode(cpu, 1, linear) == pytest.approx(
            linear ** (1.0 / GAMMA), abs=AT_RUNG_TOLERANCE
        )


def test_near_black_is_finite_sloped() -> None:
    """Below the lowest measured rung the curve runs straight to zero, so
    the encode has a finite slope there instead of the exponent's
    infinite one."""
    char = characterization(response_from(RUNS_HIGH))
    cpu = curve_cpu(char)
    first_code, first_linear = char.channel_response["red"][0]
    for linear in (first_linear * 1e-3, first_linear * 1e-2, first_linear * 0.5):
        assert encode(cpu, 0, linear) / linear == pytest.approx(
            first_code / first_linear, rel=1e-3
        )
    assert encode(cpu, 0, 0.0) == pytest.approx(0.0, abs=1e-9)


def test_display_colorspace_encodes_through_the_measured_curve() -> None:
    char = characterization(response_from(RUNS_HIGH))
    char.white_point = (0.3127, 0.3290)  # native RGB (1,1,1) is D65
    config = OCIO.Config.CreateFromFile(ACES2_STUDIO_CONFIG_URI)
    cs = create_display_colorspace_from_characterization(char)
    config.addColorSpace(cs)
    wall = config.getProcessor(OCIO.ROLE_INTERCHANGE_DISPLAY, cs.getName())
    cpu = wall.getDefaultCPUProcessor()
    # 100 cd/m² of D65 on a 1000 cd/m² wall is 10% linear drive.
    linear = 100.0 / PEAK_LUMINANCE
    expected = encode(curve_cpu(char), 0, linear)
    assert cpu.applyRGB(list(d65_xyz(1.0))) == pytest.approx([expected] * 3, abs=1e-4)


def test_the_measured_curve_survives_serialization(
    tmp_path: Any,
) -> None:
    """The config stays one self-contained file: the curve is written
    inline and reads back to the same encode."""
    char = characterization(response_from(RUNS_LOW))
    config = OCIO.Config.CreateFromFile(ACES2_STUDIO_CONFIG_URI)
    cs = create_display_colorspace_from_characterization(char)
    register_display(config, cs, char)
    path = tmp_path / "wall.ocio"
    path.write_text(config.serialize(), encoding="utf-8")
    reloaded = OCIO.Config.CreateFromFile(str(path))
    assert not list(tmp_path.glob("*.clf")) and not list(tmp_path.glob("*.spi1d"))
    before = config.getProcessor(OCIO.ROLE_INTERCHANGE_DISPLAY, cs.getName())
    after = reloaded.getProcessor(OCIO.ROLE_INTERCHANGE_DISPLAY, cs.getName())
    for y in (0.001, 0.05, 1.0, 5.0):
        xyz = list(d65_xyz(y))
        assert after.getDefaultCPUProcessor().applyRGB(xyz) == pytest.approx(
            before.getDefaultCPUProcessor().applyRGB(xyz), abs=1e-6
        )


def test_more_rungs_than_the_curve_holds_still_builds() -> None:
    """A dense suite measures more rungs than an inline curve can hold;
    the curve keeps a subset and still follows the response."""
    codes = tuple(int(c) for c in np.unique(np.geomspace(16, FULL_CODE, 48).round()))
    char = characterization(response_from(RUNS_HIGH, codes))
    cpu = curve_cpu(char)
    rungs = char.channel_response["red"]
    for linear in np.geomspace(rungs[0][1], 1.0, 200):
        assert encode(cpu, 0, float(linear)) == pytest.approx(
            power_law_between(rungs, float(linear)), abs=BETWEEN_RUNGS_TOLERANCE
        )


def test_gamma_without_a_measured_response_is_refused() -> None:
    char = characterization({})
    with pytest.raises(ValueError, match="measured per-channel response"):
        create_display_colorspace_from_characterization(char)


def test_description_says_the_curve_is_measured() -> None:
    char = characterization(response_from(RUNS_HIGH))
    description = create_display_colorspace_from_characterization(char).getDescription()
    assert "measured per-channel response" in description
    assert f"{len(LADDER_CODES)} rungs" in description


def _artifact(ramps: dict[str, list[tuple[int, float]]]) -> dict[str, Any]:
    return {
        "per_channel_response": {
            channel: [{"code": c, "xyz": [0.0, y, 0.0]} for c, y in rungs]
            for channel, rungs in ramps.items()
        }
    }


def test_response_is_read_relative_to_full_drive() -> None:
    ramp = [(16, 0.002), (2048, 50.0), (4095, 400.0)]
    response = measured_channel_response(
        _artifact({"red": ramp, "green": ramp, "blue": ramp})
    )
    assert response["red"] == (
        (16 / 4095, 0.002 / 400.0),
        (2048 / 4095, 50.0 / 400.0),
        (1.0, 1.0),
    )


def test_a_ramp_that_falls_is_refused_by_channel_and_code() -> None:
    rising = [(16, 0.002), (24, 0.004), (32, 0.008), (4095, 400.0)]
    falling = [(16, 0.002), (24, 0.025), (32, 0.010), (4095, 400.0)]
    with pytest.raises(ValueError, match=r"red.*24.*32"):
        measured_channel_response(
            _artifact({"red": falling, "green": rising, "blue": rising})
        )


def test_a_ramp_without_light_is_refused() -> None:
    rising = [(16, 0.002), (24, 0.004), (4095, 400.0)]
    dark = [(16, 0.0), (24, 0.004), (4095, 400.0)]
    with pytest.raises(ValueError, match=r"blue.*16"):
        measured_channel_response(
            _artifact({"red": rising, "green": rising, "blue": dark})
        )


def test_the_config_requires_the_response_block() -> None:
    assert REQUIRES == {"anchors": 1, "response": 1}
    with pytest.raises(UnsupportedArtifact, match="response"):
        check({"protocol": {"blocks": ["anchors/1"]}})
