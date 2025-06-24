# OCIO Custom Display Configuration Generator

A tool for creating and appending custom display colorspaces to existing OpenColorIO (OCIO) configurations, using measured display data including custom primaries, white point, luminance, and EOTF characteristics.

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
   - `display_config.yaml` - Your display measurements and base config selection
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
   - `display_config.yaml` - Your display measurements and base config selection
   - `validation_settings.yaml` - Validation parameters (optional, uses defaults if missing)

3. **Generate custom configuration**:
   ```bash
   python OCIODisplayGen.py
   ```

## Configuration Files

### Main Configuration (`display_config.yaml`)
Contains your display measurements and base OCIO configuration selection:
- Primary coordinates (red, green, blue)
- White point
- Luminance characteristics (black level, peak luminance)
- Measured EOTF settings
- Viewing conditions
- Gamut mapping strategy
- Base OCIO configuration selection (type, versions)
- Validation mode settings

### Validation Settings (`validation_settings.yaml`)
Contains validation parameters automatically loaded by the scripts:
- Coordinate validation thresholds
- Luminance and contrast ratio limits
- Gamut coverage requirements
- Viewing condition limits

### Example Configuration

```yaml
# display_config.yaml
display:
  name: "Sony BVM-HX310"

# Validation mode settings
validation:
  strict_mode: false        # Enable strict mode (errors instead of warnings)

primaries:
  red: [0.708, 0.292]
  green: [0.170, 0.797]
  blue: [0.131, 0.046]

white_point: [0.3127, 0.3290]

luminance:
  black_level: 0.001
  peak_luminance: 1000.0

# Measured display response (EOTF)
eotf:
  type: "PQ"     # Measured EOTF: "PQ", "HLG", or "GAMMA"
  # gamma_value: 2.4   # Only include if type is "GAMMA"

gamut_mapping: "soft_clip"

ocio:
  base_config:
    type: "studio"              # studio, aces, or custom
    config_version: "v2.1.0"    # config version
    aces_version: "v1.3"        # ACES version
    ocio_version: "v2.3"        # OCIO version
```

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

## Development with UV

### Project Structure
UV uses `pyproject.toml` for dependency management and project configuration. The `uv.lock` file ensures reproducible builds.

### Common UV Commands

```bash
# Install dependencies
uv sync

# Add a new dependency
uv add package_name

# Add a development dependency
uv add --dev package_name

# Run a script
uv run python script.py

# Run with specific Python version
uv run --python 3.11 python script.py

# Activate virtual environment
uv shell

# Update dependencies
uv sync --upgrade
```

### Benefits of Using UV

- **Faster dependency resolution** - UV is significantly faster than pip
- **Reproducible builds** - Lock file ensures consistent environments
- **Better dependency management** - Automatic virtual environment handling
- **Cross-platform compatibility** - Works consistently across Windows, macOS, and Linux
- **Modern Python tooling** - Built with modern Python packaging standards

## Documentation

- [Display Characterization Guide](DISPLAY_CHARACTERIZATION.md) - Detailed setup and usage
- [Gamut Mapping Guide](GAMUT_MAPPING_GUIDE.md) - Gamut mapping strategies explained

## Requirements

- Python 3.8+
- PyOpenColorIO
- colour-science
- PyYAML (for YAML configuration)

## License

MIT License - see [LICENSE](LICENSE) file for details.