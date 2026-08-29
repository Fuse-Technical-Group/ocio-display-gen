"""Generate an OCIO config from a show manifest and its measurements.

The public surface is `generate`, which takes a manifest path and returns
a `GeneratedConfig` describing what it wrote. Importing this package has
no side effects and prints nothing, so a UI may import it at startup
(`§road:ui-ocio-config` in color-wrangler).

The command line lives in `ocio_display_gen.cli` and is one caller among
others.
"""

from ocio_display_gen._core import (
    DisplayCharacterization,
    Predictions,
    Provenance,
    check_predictions,
    parse_predictions,
    provenance_description,
)
from ocio_display_gen.api import GeneratedConfig, generate

__all__ = [
    "DisplayCharacterization",
    "GeneratedConfig",
    "Predictions",
    "Provenance",
    "check_predictions",
    "generate",
    "parse_predictions",
    "provenance_description",
]
