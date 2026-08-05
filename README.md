# OCIO Custom Display Configuration Generator

A tool for creating and appending[^1] custom display colorspaces to existing OpenColorIO (OCIO) configurations, using measured display data including custom primaries, white point, luminance, and EOTF characteristics.

The goal is of this system is to have the renderer or compositor handle
the color transformation to the display's native gamut, rather than
having an unknown algorithm in the display do the transformation.

## Features

- **Display Characterization**: Create colorspaces from measured display data
- **Single EOTF Support**: PQ, HLG, or Gamma EOTF based on measured display response
- **Advanced Gamut Mapping**: Multiple strategies for handling out-of-gamut colors
- **YAML Configuration**: Easy-to-edit YAML configuration files
- **Validation**: Comprehensive validation of display measurements
- **Flexible Validation**: External validation settings file automatically loaded by scripts
- **Base Config Selection**: Load OCIO configurations using the `ocio://` scheme with structured version selection

## Quick Start

### Option 1: Using Astral UV package management (Recommended)

1. **Install UV** (if not already installed):
   ```bash
   # On Windows
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   
   # On macOS/Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Install dependencies and create virtual environment**:
   ```bash
   uv sync
   ```

3. **Edit configuration files**:
   - `show_manifest.yaml` - Your show decisions (naming, signal contract, base config selection) and the promotion pointer to the measurements artifact
   - `measurements/ftg_stage1_20240115.yaml` - Measurements artifact of record (machine-format; the shipped file is a hand-built sample)
   - `validation_settings.yaml` - Validation parameters (optional, uses defaults if missing)

4. **Generate custom configuration**:
   ```bash
   uv run ./OCIODisplayGen.py
   ```

### Option 2: Using Traditional pip Python package management

1. **Install dependencies**:
   ```bash
   pip install -e .
   ```

2. **Edit configuration files**:
   - `show_manifest.yaml` - Your show decisions (naming, signal contract, base config selection) and the promotion pointer to the measurements artifact
   - `measurements/ftg_stage1_20240115.yaml` - Measurements artifact of record (machine-format; the shipped file is a hand-built sample)
   - `validation_settings.yaml` - Validation parameters (optional, uses defaults if missing)

3. **Generate custom configuration**:
   ```bash
   python OCIODisplayGen.py
   ```

## Configuration Files

### Show manifest (`show_manifest.yaml`)
Human-authored and reviewed. Contains:
- Show naming (panel and processor identity)
- Intended processor signal contract (EOTF, intensity, processing state)
- Base OCIO configuration selection (type, versions)
- White point policy and VP Radiometric settings (nits anchor, overflow policy)
- Validation mode
- Promotion pointer to the measurements artifact of record:
  `measurements: {file, sha256}`

### Measurements artifact (`measurements/*.yaml`)
Machine-written by a characterization session, immutable, never
hand-edited (the shipped `measurements/ftg_stage1_20240115.yaml` is a
hand-built sample). Contains measured primaries and white point, black
level and peak luminance, ambient floor, instrument identity,
processor-state snapshot, and timestamps. Accept a measurement run by
recording its sha256 in the show manifest's promotion pointer;
generation refuses when the artifact on disk no longer matches the
recorded hash.

The shipped `show_manifest.yaml` and `measurements/ftg_stage1_20240115.yaml`
are a working example; see those files for the schema.

## Base OCIO Configuration Selection

The system loads base configurations using the `ocio://` scheme, which provides access to pre-built OCIO configurations:

### Configuration Types
- **`studio`**: Studio workflow configuration (default)
- **`aces`**: ACES-only configuration  
- **`custom`**: Custom configuration

### Version Components
- **`config_version`**: Configuration version (e.g., "v2.1.0", "v2.0.0")
- **`aces_version`**: ACES version (e.g., "v1.3", "v1.2")
- **`ocio_version`**: OCIO version (e.g., "v2.3", "v2.2")

### Available Configurations

The system constructs URLs in the format:
```
ocio://{type}-config-{config_version}_aces-{aces_version}_ocio-{ocio_version}
```

Available base configurations:
- **`ocio://studio-config-v2.1.0_aces-v1.3_ocio-2.3`** - Latest studio workflow
- **`ocio://aces-config-v2.1.0_aces-v1.3_ocio-2.3`** - Latest ACES-only workflow

## Gamut Mapping Strategies

- **`clip`**: Hard clipping at gamut boundary
- **`perceptual`**: Perceptual gamut mapping (preserves relationships)
- **`saturation`**: Saturation-preserving mapping
- **`relative`**: Relative colorimetric mapping
- **`absolute`**: Absolute colorimetric mapping
- **`soft_clip`**: Roll-off approach preserving response through most of gamut
- **`adaptive`**: Content-aware adaptive mapping
- **`hue_preserving`**: Hue-preserving mapping with saturation compression

## Usage

1. **Set OCIO environment variable**:
   ```bash
   # On Windows (PowerShell)
   $env:OCIO = "C:\path\to\your\custom_display_config.ocio"
   
   # On macOS/Linux
   export OCIO=/path/to/your/custom_display_config.ocio
   ```

2. **Select display colorspace in your application**:
   - Use the generated display colorspace for your measured display

## Documentation

- [Display Characterization Guide](DISPLAY_CHARACTERIZATION.md) - Detailed setup and usage
- [Gamut Mapping Guide](GAMUT_MAPPING_GUIDE.md) - Gamut mapping strategies explained

## Requirements

- Python 3.8+
- PyOpenColorIO
- colour-science
- PyYAML (for YAML configuration)

## License

BSD 3-Clause License - see [LICENSE](LICENSE) file for details.

[^1]: We append/modify rather than creating a dedicated OCIO config because many (?) systems such as Disguise that can use OCIO can only load one config at a time, and cannot merge OCIO components at runtime.