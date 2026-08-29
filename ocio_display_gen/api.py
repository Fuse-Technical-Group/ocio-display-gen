"""Generation as a library call (§spec:session-ownership).

`main()` did the work and the reporting in one pass: it read a module
constant for the manifest, printed progress, and called `sys.exit` on
every error. None of that can be embedded — a caller had to change
directory, capture stdout, and survive the process exiting underneath
it.

`generate` takes the manifest path, returns what it wrote, and raises.
The command line keeps the printing, because printing is what a command
line is for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ocio_display_gen._core import (
    DEFAULT_NITS_ANCHOR,
    DEFAULT_OVERFLOW_POLICY,
    build_predictions,
    create_base_ocio_config,
    create_characterization,
    create_display_colorspace_from_characterization,
    derive_reference_spaces,
    emit_predictions,
    generate_output_filename,
    load_inputs,
    predictions_path,
    probe_directory,
    record_provenance,
    register_display,
    validate_display_reference,
    validate_inputs,
    write_probe_imagery,
)

if TYPE_CHECKING:
    from ocio_display_gen._core import DisplayCharacterization, Provenance

__all__ = ["GeneratedConfig", "generate"]


@dataclass(frozen=True)
class GeneratedConfig:
    """What a generation run produced, for a caller that must show it."""

    config_path: Path
    predictions_path: Path
    probe_directory: Path
    probe_files: tuple[Path, ...]
    display_name: str
    default_view: str
    views: tuple[str, ...]
    characterization: DisplayCharacterization
    provenance: Provenance
    nits_anchor: float
    overflow_policy: str
    scene_reference: str
    display_reference: str


def _vp_settings(manifest: dict[str, Any]) -> tuple[float, str]:
    """VP Radiometric knobs are generation decisions (§spec:view-transform)."""
    settings = manifest.get("ocio", {}).get("vp_radiometric", {})
    raw = settings.get("nits_anchor", DEFAULT_NITS_ANCHOR)
    try:
        anchor = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"'ocio.vp_radiometric.nits_anchor' must be a number, got {raw!r}"
        ) from exc
    return anchor, settings.get("overflow_policy", DEFAULT_OVERFLOW_POLICY)


def generate(
    manifest_path: str | os.PathLike[str],
    *,
    output_dir: str | os.PathLike[str] | None = None,
) -> GeneratedConfig:
    """Generate the OCIO config, its predictions and its probe imagery.

    `manifest_path` names the show manifest; the measurements artifact it
    promotes is resolved beside it and its promotion hash enforced
    (§spec:provenance). `output_dir` defaults to the manifest's own
    directory, so a caller that passes nothing gets what the command line
    has always produced.

    Raises `ValueError` for bad inputs and `RuntimeError` if the
    generated config fails OCIO validation.
    """
    manifest_path = Path(manifest_path)
    manifest, measurements, provenance = load_inputs(str(manifest_path))
    if not validate_inputs(manifest, measurements, str(manifest_path)):
        raise ValueError(
            f"Show manifest '{manifest_path}' and its promoted measurements "
            "did not pass validation"
        )
    characterization = create_characterization(manifest, measurements)
    nits_anchor, overflow_policy = _vp_settings(manifest)

    destination = Path(output_dir) if output_dir is not None else manifest_path.parent
    destination.mkdir(parents=True, exist_ok=True)
    config_path = destination / generate_output_filename(manifest, characterization)

    config = create_base_ocio_config(manifest)
    scene_reference, display_reference = derive_reference_spaces(config)
    validate_display_reference(display_reference)
    colorspace = create_display_colorspace_from_characterization(characterization)
    display_name = register_display(
        config,
        colorspace,
        characterization,
        nits_anchor=nits_anchor,
        overflow_policy=overflow_policy,
    )
    record_provenance(config, provenance, manifest.get("show", {}).get("description"))
    try:
        config.validate()
    except Exception as exc:
        raise RuntimeError(f"Generated config failed OCIO validation: {exc}") from exc

    config_path.write_text(config.serialize(), encoding="utf-8")
    # Predictions bind to the config's bytes, so they are built from the
    # file just written (§spec:verification, §spec:provenance).
    predictions = build_predictions(
        config, display_name, characterization, nits_anchor, str(config_path)
    )
    written_predictions = Path(predictions_path(str(config_path)))
    written_predictions.write_text(emit_predictions(predictions), encoding="utf-8")
    probe_dir = Path(probe_directory(str(config_path)))
    probe_files = write_probe_imagery(str(probe_dir), predictions)

    return GeneratedConfig(
        config_path=config_path,
        predictions_path=written_predictions,
        probe_directory=probe_dir,
        probe_files=tuple(Path(p) for p in probe_files),
        display_name=display_name,
        default_view=str(config.getDefaultView(display_name)),
        views=tuple(str(v) for v in config.getViews(display_name)),
        characterization=characterization,
        provenance=provenance,
        nits_anchor=nits_anchor,
        overflow_policy=overflow_policy,
        scene_reference=scene_reference,
        display_reference=display_reference,
    )
