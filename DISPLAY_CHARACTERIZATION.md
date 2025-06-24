# Display Characterization Guide

This guide explains how to characterize a display and create custom OCIO configurations for that display for color-managed workflows.

## Configuration Files

The system uses three main configuration files:

1. **`display_config.yaml`** - Main display configuration
2. **`validation_settings.yaml`** - Validation parameters and thresholds (automatically loaded by scripts)
3. **Base OCIO Config** - Loaded using the `ocio://` scheme based on configuration selection

### Main Configuration File

The main configuration file contains your display measurements and settings:

```yaml
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

viewing_conditions:
  ambient_light: 5.0
  viewing_angle: 0.0
  surround: "dark"

gamut_mapping: "soft_clip"

ocio:
  base_config:
    type: "studio"              # studio, aces, or custom
    config_version: "v2.1.0"    # config version (e.g., v2.1.0, v2.0.0)
    aces_version: "v1.3"        # ACES version (e.g., v1.3, v1.2)
    ocio_version: "v2.3"        # OCIO version (e.g., v2.3, v2.2)
  output_config: "custom_display_config.ocio"

advanced:
  manufacturer: "Sony"
  model_year: 2023
  calibration_date: "2024-01-15"
  calibration_software: "LightSpace CMS"
  color_temperature: 6500
  gamut_coverage: 95.5
  uniformity_correction: false
```

### Base OCIO Configuration Selection

The system loads base configurations using the `ocio://` scheme, which provides access to pre-built OCIO configurations:

#### Configuration Type
- **`studio`**: Studio workflow configuration (default)
- **`aces`**: ACES-only configuration
- **`custom`**: Custom configuration

#### Version Components
- **`config_version`**: Configuration version (e.g., "v2.1.0", "v2.0.0")
- **`aces_version`**: ACES version (e.g., "v1.3", "v1.2")
- **`ocio_version`**: OCIO version (e.g., "v2.3", "v2.2")

#### OCIO:// URL Format

The system constructs URLs in the format:
```
ocio://{type}-config-{config_version}_aces-{aces_version}_ocio-{ocio_version}
```

#### Example Configurations

```yaml
# Studio workflow with latest versions
ocio:
  base_config:
    type: "studio"
    config_version: "v2.1.0"
    aces_version: "v1.3"
    ocio_version: "v2.3"
# Results in: ocio://studio-config-v2.1.0_aces-v1.3_ocio-v2.3

# ACES-only configuration
ocio:
  base_config:
    type: "aces"
    config_version: "v2.1.0"
    aces_version: "v1.3"
    ocio_version: "v2.3"
# Results in: ocio://aces-config-v2.1.0_aces-v1.3_ocio-v2.3
```

#### Available Base Configurations

The following base configurations are available through the `ocio://` scheme:

- **`ocio://studio-config-v2.1.0_aces-v1.3_ocio-v2.3`** - Latest studio workflow
- **`ocio://aces-config-v2.1.0_aces-v1.3_ocio-v2.3`** - Latest ACES-only workflow

### Validation Settings File

The `validation_settings.yaml` file contains validation parameters that are automatically loaded by the characterization scripts:

```yaml
# PRIMARY COORDINATE VALIDATION
check_primaries: true      # Validate primary coordinates are within 0-1
check_white_point: true    # Validate white point coordinates are within 0-1

# LUMINANCE VALIDATION
check_luminance: true      # Validate luminance values are positive
check_black_level: true    # Validate black level is reasonable
check_peak_luminance: true # Validate peak luminance is reasonable

# CONTRAST RATIO VALIDATION
check_contrast: true       # Validate contrast ratio is reasonable
min_contrast_ratio: 100    # Minimum acceptable contrast ratio
max_contrast_ratio: 10000  # Maximum acceptable contrast ratio

# GAMUT VALIDATION
check_gamut_coverage: true # Validate gamut coverage is reasonable
min_gamut_coverage: 50.0   # Minimum acceptable gamut coverage (%)
max_gamut_coverage: 100.0  # Maximum acceptable gamut coverage (%)

# WHITE POINT VALIDATION
check_white_point_temp: true  # Validate white point temperature is reasonable
min_white_point_temp: 4000    # Minimum acceptable white point temperature (K)
max_white_point_temp: 10000   # Maximum acceptable white point temperature (K)
max_duv_deviation: 0.006       # Maximum acceptable duv deviation from Planckian locus

# VIEWING CONDITIONS VALIDATION
check_viewing_conditions: true # Validate viewing condition parameters
max_ambient_light: 50.0        # Maximum acceptable ambient light (cd/m²)
max_viewing_angle: 45.0        # Maximum acceptable viewing angle (degrees)

# EOTF VALIDATION
check_eotf_parameters: true    # Validate EOTF parameters
min_gamma_value: 1.8           # Minimum acceptable gamma value
max_gamma_value: 3.0           # Maximum acceptable gamma value
```

**Note**: The validation settings file is automatically loaded by the characterization scripts. You don't need to reference it in your main configuration file.

## Running the Characterization

### Using YAML Configuration

```bash
python OCIODisplayGen.py
```

## Validation Process

The system performs comprehensive validation of your display measurements:

1. **Primary Coordinates**: Ensures all primary coordinates are within valid CIE xy range [0,1]
2. **White Point**: Validates white point coordinates, temperature, and duv deviation
3. **Luminance**: Checks that black level and peak luminance are positive and reasonable
4. **Contrast Ratio**: Validates contrast ratio is within acceptable range
5. **Gamut Coverage**: Ensures gamut coverage is reasonable
6. **Viewing Conditions**: Validates ambient light and viewing angle settings
7. **EOTF Parameters**: Checks gamma values are within acceptable range

### Validation Modes

- **Warning Mode** (default): Shows warnings but continues processing
- **Strict Mode**: Treats validation failures as errors and stops processing

To enable strict mode, set `strict_mode: true` in your `display_config.yaml` file under the `validation` section.

## Output

The system generates:

1. **OCIO Configuration File**: Custom display colorspace with gamut mapping
2. **Single EOTF Colorspace**: Based on your measured display response
3. **Display View**: Ready-to-use display configuration

## Usage

1. Set the OCIO environment variable:
   ```bash
   export OCIO=/path/to/your/custom_display_config.ocio
   ```

2. In your application, select the generated display colorspace for your measured display.

## Troubleshooting

### Common Validation Issues

1. **Primary coordinates outside range**: Check your colorimeter measurements
2. **Contrast ratio too low**: Verify black level and peak luminance measurements
3. **White point temperature**: Ensure your white point is reasonable (typically 5000K-7000K)

### Missing Files

- If `validation_settings.yaml` is missing, the system will use default validation settings
- Base OCIO configurations are loaded via the `ocio://` scheme, so no external files are required

### Base Configuration Errors

If you encounter errors loading base configurations:

1. **Check your configuration parameters**: Ensure the type, versions, and format are correct
2. **Verify available configurations**: The system will show available `ocio://` URLs when errors occur
3. **Update OCIO**: Ensure you have a recent version of OCIO that supports the requested configuration

### Validation Warnings

Warnings indicate potential issues but don't prevent processing. Review the warnings and adjust your measurements if needed.

## Overview

The script provides a comprehensive framework for creating OCIO display colorspaces based on actual measured display characteristics rather than theoretical values. This ensures accurate color reproduction for your specific display hardware.

## 🎯 **Configuration Files Approach**

**The easiest way to characterize your display is using configuration files (YAML).**

### Quick Start with YAML

1. **Edit the configuration file:**
   ```bash
   # Edit display_config.yaml with your measurements
   nano display_config.yaml
   ```

2. **Run the characterization script:**
   ```bash
   python OCIODisplayGen.py
   ```

## Configuration File Format

### YAML Configuration (`display_config.yaml`)

```yaml
display:
  name: "Sony BVM-HX310"  # Your display model name

# MEASURED PRIMARIES (replace with your actual measurements)
primaries:
  red: [0.680, 0.320]    # Measured red primary
  green: [0.265, 0.690]  # Measured green primary
  blue: [0.150, 0.060]   # Measured blue primary

# MEASURED WHITE POINT
white_point: [0.3127, 0.3290]  # D65 or measured white point

# MEASURED LUMINANCE CHARACTERISTICS
luminance:
  black_level: 0.005     # cd/m² - measured black level
  peak_luminance: 1000.0 # cd/m² - measured peak luminance

# DISPLAY RESPONSE CHARACTERISTICS
eotf:
  type: "PQ"     # Primary EOTF: "PQ", "HLG", or "GAMMA"
  # gamma_value: 2.4       # For gamma-based EOTF

# EOTF VARIANTS TO CREATE
eotf_variants:
  - "PQ"    # For HDR PQ content
  - "HLG"   # For HDR HLG content
  - "GAMMA" # For SDR content
```

## Key Features

### 1. **DisplayCharacterization Class**
- Centralized data structure for all display measurements
- Type-safe attributes for primaries, luminance, and viewing conditions
- Support for multiple EOTF variants (PQ, HLG, Gamma)

### 2. **Multiple EOTF Support**
- **PQ (ST-2084)**: For HDR content with peak luminance ≥ 400 cd/m²
- **HLG**: For broadcast HDR content
- **Gamma**: For SDR content (2.2, 2.4, etc.)

### 3. **Measured Data Integration**
- Support for actual measured primaries instead of theoretical values
- Measured black level and peak luminance
- Viewing condition documentation

## Required Measurements

To characterize your display, you'll need to measure:

### 1. **Color Primaries** (xy coordinates)
- **Red primary**: (x, y) coordinates
- **Green primary**: (x, y) coordinates  
- **Blue primary**: (x, y) coordinates
- **White point**: (x, y) coordinates

### 2. **Luminance Characteristics**
- **Black level**: Minimum luminance (cd/m²)
- **Peak luminance**: Maximum luminance (cd/m²)
- **Contrast ratio**: Peak/black level ratio

### 3. **Viewing Conditions**
- **Ambient light**: Surround illumination (cd/m²)
- **Viewing angle**: Measurement angle (degrees)
- **Measurement distance**: Distance from display (meters)

## Equipment Needed

- **Colorimeter or spectrophotometer** (e.g., X-Rite i1 Pro 2, Klein K-10A)
- **Luminance meter** (for black level and peak measurements)
- **Dark viewing environment** (for accurate measurements)

## Usage Examples

### 1. **Using Configuration Files**

#### YAML Approach
```bash
# 1. Edit the configuration file with your measurements
nano display_config.yaml

# 2. Run the characterization script
python OCIODisplayGen.py
```

## Generated Colorspaces

The script creates separate colorspaces for each EOTF variant:

- `Display Name - PQ - Display`: For HDR PQ content
- `Display Name - HLG - Display`: For HDR HLG content  
- `