"""The importable surface (§spec:session-ownership).

Generation is a library call. The command line is one caller of it, so
these tests exercise it without argparse, without a working directory
convention, and without capturing stdout — a caller that must chdir and
parse printed text is a script wearing a library's name.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ocio_display_gen import GeneratedConfig, generate

REPO = Path(__file__).parent


def test_generate_takes_a_manifest_path_and_returns_what_it_wrote(
    tmp_path: Path,
) -> None:
    """No chdir, no module constant: the caller says which manifest."""
    result = generate(REPO / "show_manifest.yaml", output_dir=tmp_path)
    assert isinstance(result, GeneratedConfig)
    assert result.config_path.is_file()
    assert result.predictions_path.is_file()
    assert result.probe_directory.is_dir()
    assert result.probe_files
    assert result.config_path.parent == tmp_path


def test_generate_reports_the_display_it_registered(tmp_path: Path) -> None:
    """The caller needs the names to show them, not to scrape them."""
    result = generate(REPO / "show_manifest.yaml", output_dir=tmp_path)
    assert result.display_name
    assert result.default_view in result.views
    assert result.characterization.peak_luminance > 0


def test_generate_raises_rather_than_exiting(tmp_path: Path) -> None:
    """A library that calls sys.exit cannot be embedded in a UI."""
    broken = tmp_path / "show_manifest.yaml"
    broken.write_text("show:\n  description: no promotion pointer\n")
    with pytest.raises(ValueError):
        generate(broken, output_dir=tmp_path)


def test_generate_is_byte_deterministic(tmp_path: Path) -> None:
    """Two calls agree, which is what provenance rests on."""
    first = generate(REPO / "show_manifest.yaml", output_dir=tmp_path / "a")
    second = generate(REPO / "show_manifest.yaml", output_dir=tmp_path / "b")
    assert first.config_path.read_bytes() == second.config_path.read_bytes()
    assert first.predictions_path.read_bytes() == second.predictions_path.read_bytes()


def test_importing_the_package_runs_no_generation_and_prints_nothing() -> None:
    """Import is free of side effects, so a UI can import it at startup."""
    proc = subprocess.run(
        [sys.executable, "-c", "import ocio_display_gen"],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_generate_does_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation settings sit beside the manifest, not beside the caller.

    `load_validation_settings` read `validation_settings.yaml` from the
    process's working directory, so the same manifest passed from the
    repository and failed from anywhere else — the contrast bounds
    silently reverted to defaults this display exceeds.
    """
    monkeypatch.chdir(tmp_path)
    result = generate(REPO / "show_manifest.yaml", output_dir=tmp_path / "out")
    assert result.config_path.is_file()
