"""What this generator needs an artifact to carry (§spec:characterization-model).

The requirement lives with the code that reads it. display-measure owns
the measurement — which codes, at what spacing, under what conditions —
because those satisfy invariants a consumer does not know about. What a
consumer owns is the statement of what it cannot generate without.

A config is a small requirement, and saying so is worth something: it
means a session that only needs a config need not drive the hundreds of
patches a fidelity report reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# Block name -> the lowest version this generator can read. The whole of
# what a config takes from an artifact is the display's primaries, white
# point, black level and peak luminance, and one block carries all four.
REQUIRES: dict[str, int] = {"anchors": 1}

# Artifacts written before blocks were recorded carry a protocol name
# instead. Every artifact measured to date is one of these.
LEGACY_PROTOCOL_BLOCKS: dict[str, dict[str, int]] = {
    "color-wrangler/characterize/1": {"anchors": 1, "response": 1, "additivity": 1},
    "color-wrangler/characterize/2": {"anchors": 1, "response": 1, "additivity": 1},
    "color-wrangler/characterize/3": {"anchors": 1, "response": 1, "additivity": 1},
}


class UnsupportedArtifact(ValueError):
    """The artifact does not carry what the generator reads."""


def blocks_carried(measurements: Mapping[str, object]) -> dict[str, int] | None:
    """What an artifact carries, or None when it says nothing about it.

    None is not "carries nothing": artifacts predating the block record
    and third-party files say nothing, and those are validated on their
    contents as they always were.
    """
    protocol = measurements.get("protocol")
    if not isinstance(protocol, dict):
        return None
    recorded = protocol.get("blocks")
    if recorded is not None:
        blocks: dict[str, int] = {}
        for entry in recorded:
            name, _, version = str(entry).partition("/")
            if name and version.isdigit():
                blocks[name] = int(version)
        return blocks
    name = protocol.get("name")
    if isinstance(name, str) and name in LEGACY_PROTOCOL_BLOCKS:
        return dict(LEGACY_PROTOCOL_BLOCKS[name])
    return None


def check(measurements: Mapping[str, object]) -> None:
    """Raise unless the artifact carries the blocks this generator reads."""
    carried = blocks_carried(measurements)
    if carried is None:
        return
    missing = sorted(name for name in REQUIRES if name not in carried)
    outdated = sorted(
        f"{name}/{carried[name]} (needs {minimum} or later)"
        for name, minimum in REQUIRES.items()
        if name in carried and carried[name] < minimum
    )
    if not missing and not outdated:
        return
    problems = []
    if missing:
        problems.append("does not carry " + ", ".join(missing))
    if outdated:
        problems.append("carries " + ", ".join(outdated))
    raise UnsupportedArtifact(
        "this artifact "
        + "; and ".join(problems)
        + ". A config reads "
        + ", ".join(sorted(REQUIRES))
        + " — measure a suite composing it "
        + "(`display-measure characterize --suite config`)."
    )
