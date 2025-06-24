# OCIODisplayGen.py
# This script creates a custom display colorspace for a high dynamic range display with
# non-standard native primaries, and appends it to an existing OCIO config
# It uses the colour-science library to create the colorspace and the PyOpenColorIO
# library to create the OCIO config

import colour
import numpy as np
import PyOpenColorIO as OCIO
from typing import Dict, List, Tuple, Optional, Any
import yaml
import os
import sys

class DisplayCharacterization:
    """Class to hold display characterization data"""
    def __init__(self, name: str):
        self.name = name
        self.primaries: Dict[str, Tuple[float, float]] = {}  # Measured RGB primaries (xy coordinates)
        self.white_point: Optional[Tuple[float, float]] = None  # Measured white point (xy coordinates)
        self.black_level = 0.0  # Measured black level (cd/m²)
        self.peak_luminance = 1000.0  # Measured peak luminance (cd/m²)
        self.contrast_ratio = 1000.0  # Measured contrast ratio
        self.eotf_type = "PQ"  # EOTF type: "PQ", "HLG", "GAMMA"
        self.gamma_value = 2.4  # For gamma-based EOTF
        self.measured_response: Optional[str] = None  # Custom measured response curve
        self.viewing_conditions: Dict[str, object] = {}  # Ambient light, viewing angle, etc.

def create_display_colorspace_from_characterization(
    characterization: DisplayCharacterization,
    gamut_mapping: str = "clip"
) -> OCIO.ColorSpace:
    """Create OCIO display colorspace from comprehensive display characterization"""
    
    eotf_type = characterization.eotf_type
    
    # Create RGB to XYZ matrix using colour-science
    primaries = np.array([
        characterization.primaries['red'],
        characterization.primaries['green'], 
        characterization.primaries['blue']
    ])
    whitepoint = np.array(characterization.white_point)
    
    custom_colourspace = colour.RGB_Colourspace(
        characterization.name,
        primaries,
        whitepoint,
        "Custom"
    )
    custom_colourspace.use_derived_transformation_matrices()
    XYZ_to_RGB = np.identity(4)
    XYZ_to_RGB[:3, :3] = custom_colourspace.matrix_XYZ_to_RGB
    
    # Create OCIO colorspace
    cs = OCIO.ColorSpace()
    display_name = f"{characterization.name} - Display"
    cs.setName(f"{display_name}")
    cs.addAlias(f"{display_name.lower().replace(' ', '_')}_display")
    cs.setFamily("Display")
    cs.setEncoding("hdr-video" if eotf_type in ["PQ", "HLG"] else "sdr-video")
    cs.setDescription(
        f"Display colorspace for {characterization.name} "
        f"(Peak: {characterization.peak_luminance} cd/m², "
        f"Black: {characterization.black_level} cd/m², "
        f"EOTF: {eotf_type}, "
        f"Gamut: {gamut_mapping})"
    )
    cs.setBitDepth(OCIO.BIT_DEPTH_F32)
    cs.addCategory("file-io")
    cs.addCategory("display")
    
    # Create transform group
    group = OCIO.GroupTransform()
    
    # Add gamut mapping based on strategy
    if gamut_mapping == "perceptual":
        # Perceptual gamut mapping - preserve color relationships
        # Use OCIO's builtin perceptual gamut mapping
        gamut_transform = OCIO.BuiltinTransform("GAMUT-MAP - PERCEPTUAL")
        gamut_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
        group.appendTransform(gamut_transform)
        
    elif gamut_mapping == "saturation":
        # Saturation-preserving gamut mapping
        # Use OCIO's builtin saturation gamut mapping
        gamut_transform = OCIO.BuiltinTransform("GAMUT-MAP - SATURATION")
        gamut_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
        group.appendTransform(gamut_transform)
        
    elif gamut_mapping == "relative":
        # Relative colorimetric gamut mapping
        # Use OCIO's builtin relative colorimetric gamut mapping
        gamut_transform = OCIO.BuiltinTransform("GAMUT-MAP - RELATIVE")
        gamut_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
        group.appendTransform(gamut_transform)
        
    elif gamut_mapping == "absolute":
        # Absolute colorimetric gamut mapping
        # Use OCIO's builtin absolute colorimetric gamut mapping
        gamut_transform = OCIO.BuiltinTransform("GAMUT-MAP - ABSOLUTE")
        gamut_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
        group.appendTransform(gamut_transform)
        
    elif gamut_mapping == "soft_clip":
        # Soft clipping - preserve response through most of gamut, roll off near edges
        # Use perceptual gamut mapping as a fallback for soft clipping
        try:
            soft_clip_transform = OCIO.BuiltinTransform("GAMUT-MAP - SOFT-CLIP")
            soft_clip_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
            group.appendTransform(soft_clip_transform)
        except:
            # Fallback to perceptual if soft-clip not available
            perceptual_transform = OCIO.BuiltinTransform("GAMUT-MAP - PERCEPTUAL")
            perceptual_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
            
    elif gamut_mapping == "adaptive":
        # Adaptive gamut mapping - combines multiple strategies
        # Use OCIO's adaptive gamut mapping (if available)
        try:
            adaptive_transform = OCIO.BuiltinTransform("GAMUT-MAP - ADAPTIVE")
            adaptive_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
            group.appendTransform(adaptive_transform)
        except:
            # Fallback to perceptual if adaptive not available
            perceptual_transform = OCIO.BuiltinTransform("GAMUT-MAP - PERCEPTUAL")
            perceptual_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
            group.appendTransform(perceptual_transform)
            
    elif gamut_mapping == "hue_preserving":
        # Hue-preserving gamut mapping
        # Use OCIO's hue-preserving gamut mapping (if available)
        try:
            hue_transform = OCIO.BuiltinTransform("GAMUT-MAP - HUE-PRESERVING")
            hue_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
            group.appendTransform(hue_transform)
        except:
            # Fallback to saturation if hue-preserving not available
            saturation_transform = OCIO.BuiltinTransform("GAMUT-MAP - SATURATION")
            saturation_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
    
    # Add RGB to XYZ matrix
    matrix_transform = OCIO.MatrixTransform()
    matrix_transform.setMatrix(XYZ_to_RGB.flatten().tolist())
    group.appendTransform(matrix_transform)
    
    # Add appropriate EOTF based on type
    if eotf_type == "PQ":
        # PQ EOTF using builtin transform
        pq_transform = OCIO.BuiltinTransform("CURVE - LINEAR_to_ST-2084")
        pq_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
        group.appendTransform(pq_transform)
        
    elif eotf_type == "HLG":
        # HLG EOTF using builtin transform
        hlg_transform = OCIO.BuiltinTransform("CURVE - LINEAR_to_HLG")
        hlg_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
        group.appendTransform(hlg_transform)
        
    elif eotf_type == "GAMMA":
        # Gamma EOTF using builtin transform
        gamma_transform = OCIO.BuiltinTransform(f"CURVE - LINEAR_to_GAMMA{characterization.gamma_value}")
        gamma_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
        group.appendTransform(gamma_transform)
        
    # Note: Custom measured response curves would require FileTransform
    # which is not available in the current type stubs
    # elif eotf_type == "CUSTOM" and characterization.measured_response is not None:
    #     # Custom measured response curve using 1D LUT
    #     lut_transform = OCIO.FileTransform()
    #     lut_transform.setSrc(characterization.measured_response)
    #     lut_transform.setInterpolation(OCIO.INTERP_DEFAULT)
    #     group.appendTransform(lut_transform)

    cs.setTransform(group, OCIO.COLORSPACE_DIR_FROM_REFERENCE)
    
    return cs

def append_display_colorspace_to_config(
    input_config_path: str, 
    output_config_path: str,
    characterization: DisplayCharacterization,
    eotf_variants: Optional[List[str]] = None
) -> OCIO.Config:
    """Append display colorspace(s) to existing config with multiple EOTF variants"""
    
    # Load the existing OCIO config
    config = OCIO.Config.CreateFromFile(input_config_path)
    
    # Default EOTF variants if none specified
    if eotf_variants is None:
        eotf_variants = ["PQ", "HLG", "GAMMA"]
    
    # Create colorspaces for each EOTF variant
    for eotf_type in eotf_variants:
        suffix = f" - {eotf_type}"
        cs = create_display_colorspace_from_characterization(
            characterization, 
            gamut_mapping=eotf_type
        )
        config.addColorSpace(cs)
        
        # Add display view for each variant
        display_name = f"{characterization.name}{suffix} - Display"
        config.addDisplayView(display_name, "Output", cs.getName())
    
    return config

def create_display_colorspace(r_xy, g_xy, b_xy, w_xy, name="DisplayOutput", whitepoint_name="whitepoint name"):
    """Create OCIO display colorspace from CIE xy primaries (legacy function)"""
    
    # Create RGB to XYZ matrix using colour-science
    primaries = np.concatenate((r_xy, g_xy, b_xy))
    whitepoint = np.array(w_xy)
    custom_colourspace = colour.RGB_Colourspace(
        "Custom",
        primaries,
        whitepoint,
        whitepoint_name
    )
    custom_colourspace.use_derived_transformation_matrices()
    XYZ_to_RGB = np.identity(4)
    XYZ_to_RGB[:3, :3] = custom_colourspace.matrix_XYZ_to_RGB
    
    # Create OCIO colorspace
    cs = OCIO.ColorSpace()
    cs.setName(f"{name} - Display")
    cs.addAlias(f"{name.lower()}_display")
    cs.setFamily("Display")
    cs.setEncoding("hdr-video")
    cs.setDescription(f"Convert CIE XYZ (D65 white) to {name}")
    cs.setBitDepth(OCIO.BIT_DEPTH_F32)
    cs.addCategory("file-io")
    
    # Create transform group
    group = OCIO.GroupTransform()
    
    # Add RGB to XYZ matrix
    matrix_transform = OCIO.MatrixTransform()
    matrix_transform.setMatrix(XYZ_to_RGB.flatten().tolist())
    group.appendTransform(matrix_transform)
    
    # Add PQ EOTF using "CURVE - LINEAR_to_ST-2084" 1D LUT
    pq_transform = OCIO.BuiltinTransform("CURVE - LINEAR_to_ST-2084")
    pq_transform.setDirection(OCIO.TRANSFORM_DIR_FORWARD)
    # Note that there is a fixed function implementation as well
    # as of OCIO v2.0.0, however the 1D LUT may be faster to compute
    # per the docs.
    # https://opencolorio.readthedocs.io/en/latest/releases/ocio_2_4.html#new-fixed-function-transforms
    #
    # Note also that disguise does not yet support OCIO v2.4.0
    # which is required for the fixed function transform.
    #
    # pq_transform = OCIO.FixedFunctionTransform()
    # pq_transform.setStyle(OCIO.FIXED_FUNCTION_LIN_TO_PQ)
    group.appendTransform(pq_transform)

    cs.setTransform(group, OCIO.COLORSPACE_DIR_FROM_REFERENCE)
    
    return cs

def create_example_characterization() -> DisplayCharacterization:
    """Create an example display characterization with measured data"""
    
    # Example: Measured display data (replace with actual measurements)
    char = DisplayCharacterization("Custom HDR Display")
    
    # Measured primaries (replace with actual measurements)
    char.primaries = {
        'red': (0.680, 0.320),    # Measured red primary
        'green': (0.265, 0.690),  # Measured green primary  
        'blue': (0.150, 0.060)    # Measured blue primary
    }
    
    # Measured white point
    char.white_point = (0.3127, 0.3290)  # D65 or measured white point
    
    # Measured display characteristics
    char.black_level = 0.005  # cd/m²
    char.peak_luminance = 1000.0  # cd/m²
    char.contrast_ratio = char.peak_luminance / char.black_level
    
    # Display response characteristics
    char.eotf_type = "PQ"  # Primary EOTF
    char.gamma_value = 2.4  # For gamma-based EOTF
    
    # Viewing conditions
    char.viewing_conditions = {
        'ambient_light': 5.0,  # cd/m²
        'viewing_angle': 0.0,  # degrees
        'surround': 'dark'     # 'dark', 'dim', 'average'
    }
    
    return char

# YAML Configuration Functions
def load_config_from_yaml(config_path: str) -> Dict[str, Any]:
    """Load display configuration from YAML file."""
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        print(f"❌ Error: Configuration file '{config_path}' not found.")
        print("   Please create a 'display_config.yaml' file with your display measurements.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"❌ Error parsing YAML file: {e}")
        sys.exit(1)

def create_characterization_from_config(config: Dict[str, Any]) -> DisplayCharacterization:
    """Create DisplayCharacterization object from YAML config."""
    
    char = DisplayCharacterization(config['display']['name'])
    primaries_config = config['primaries']
    char.primaries = {
        'red': tuple(primaries_config['red']),
        'green': tuple(primaries_config['green']),
        'blue': tuple(primaries_config['blue'])
    }
    char.white_point = tuple(config['white_point'])
    luminance_config = config['luminance']
    char.black_level = luminance_config['black_level']
    char.peak_luminance = luminance_config['peak_luminance']
    char.contrast_ratio = char.peak_luminance / char.black_level
    eotf_config = config['eotf']
    char.eotf_type = eotf_config['type']
    char.gamma_value = eotf_config.get('gamma_value', 2.4)
    char.viewing_conditions = config.get('viewing_conditions', {})
    if 'advanced' in config:
        char.viewing_conditions.update(config['advanced'])
    return char

def load_validation_settings() -> Dict[str, Any]:
    """Load validation settings from external file."""
    
    # Default validation settings
    default_validation = {
        'check_primaries': True,
        'check_white_point': True,
        'check_luminance': True,
        'check_contrast': True,
        'min_contrast_ratio': 100,
        'max_contrast_ratio': 10000,
        'warn_on_validation_failure': True,
        'strict_mode': False
    }
    
    # Load external validation settings file
    validation_file = "validation_settings.yaml"
    if os.path.exists(validation_file):
        try:
            with open(validation_file, 'r') as f:
                external_validation = yaml.safe_load(f)
            print(f"✓ Loaded validation settings from '{validation_file}'")
        except yaml.YAMLError as e:
            print(f"⚠️  Warning: Error parsing validation settings file: {e}")
            print("   Using default validation settings")
            external_validation = {}
    else:
        print(f"⚠️  Warning: Validation settings file '{validation_file}' not found")
        print("   Using default validation settings")
        external_validation = {}
    
    # Merge settings: external file -> defaults
    validation_settings = default_validation.copy()
    validation_settings.update(external_validation)
    
    return validation_settings

def validate_config_data(config: Dict[str, Any]) -> bool:
    """Validate configuration data from YAML file."""
    
    # Load validation settings
    validation_config = load_validation_settings()
    
    # Get validation mode from display config (overrides validation settings)
    strict_mode = config.get('validation', {}).get('strict_mode', False)
    
    # Check primaries
    if validation_config.get('check_primaries', True):
        print("Validating display primaries: basic chromaticity range check (not a true spectral locus test)...")
        for color, coords in config['primaries'].items():
            x, y = coords
            # Basic range check
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and x + y <= 1.0):
                message = f"❌ Warning: {color} primary ({x}, {y}) is outside the valid xy chromaticity triangle (0 ≤ x ≤ 1, 0 ≤ y ≤ 1, x + y ≤ 1)"
                if strict_mode:
                    print(message)
                    return False
                else:
                    print(message)
            else:
                print(f"✓ {color} primary ({x:.4f}, {y:.4f}) is within the valid xy chromaticity triangle")
    
    # Check white point
    if validation_config.get('check_white_point', True):
        x, y = config['white_point']
        
        # Basic range check
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            message = f"❌ Warning: White point ({x}, {y}) outside valid range [0,1]"
            if strict_mode:
                print(message)
                return False
            else:
                print(message)
        else:
            print(f"✓ White point ({x:.4f}, {y:.4f}) is within valid range [0,1]")
        
        # Check duv deviation from Planckian locus (using CIE 1960 UCS uv)
        try:
            # Convert xy to CIE 1960 UCS uv coordinates
            xy_coords = np.array([x, y])
            uv_coords = colour.xy_to_UCS_uv(xy_coords)
            
            # Calculate CCT and duv
            CCT, duv = colour.uv_to_CCT(uv_coords, method='Ohno 2013')
            
            # Check CCT range
            min_cct = validation_config.get('min_white_point_temp', 4000)
            max_cct = validation_config.get('max_white_point_temp', 10000)
            
            if not (min_cct <= CCT <= max_cct):
                message = f"❌ Warning: White point CCT ({CCT:.0f}K) outside acceptable range [{min_cct}, {max_cct}]K"
                if strict_mode:
                    print(message)
                    return False
                else:
                    print(message)
            else:
                print(f"✓ White point CCT ({CCT:.0f}K) is within acceptable range [{min_cct}, {max_cct}]K")
            
            # Check duv deviation
            max_duv_deviation = validation_config.get('max_duv_deviation', 0.15)
            
            if abs(duv) > max_duv_deviation:
                message = f"❌ Warning: White point duv deviation ({duv:.4f}) exceeds maximum ({max_duv_deviation:.4f})"
                if strict_mode:
                    print(message)
                    return False
                else:
                    print(message)
            else:
                print(f"✓ White point duv deviation ({duv:.4f}) is within acceptable range (±{max_duv_deviation:.4f})")
                
        except Exception as e:
            # Fallback if duv calculation fails
            print(f"⚠️  Warning: Could not calculate duv deviation: {e}")
            print(f"   Skipping duv validation")
    
    # Check luminance values
    if validation_config.get('check_luminance', True):
        black_level = config['luminance']['black_level']
        peak_luminance = config['luminance']['peak_luminance']
        
        if black_level < 0:
            message = "❌ Warning: Black level cannot be negative"
            if strict_mode:
                print(message)
                return False
            else:
                print(message)
        
        if peak_luminance <= 0:
            message = "❌ Warning: Peak luminance must be positive"
            if strict_mode:
                print(message)
                return False
            else:
                print(message)
        
        if peak_luminance < black_level:
            message = "❌ Warning: Peak luminance must be greater than black level"
            if strict_mode:
                print(message)
                return False
            else:
                print(message)
    
    # Check contrast ratio
    if validation_config.get('check_contrast', True):
        black_level = float(config['luminance']['black_level'])
        peak_luminance = float(config['luminance']['peak_luminance'])
        contrast_ratio = peak_luminance / black_level
        min_contrast = validation_config.get('min_contrast_ratio', 100)
        max_contrast = validation_config.get('max_contrast_ratio', 10000)
        
        if not (min_contrast <= contrast_ratio <= max_contrast):
            message = f"❌ Warning: Contrast ratio {contrast_ratio:.0f}:1 outside acceptable range [{min_contrast}, {max_contrast}]"
            if strict_mode:
                print(message)
                return False
            else:
                print(message)
    
    print("✓ Configuration validation passed")
    return True

def generate_output_filename(config: Dict[str, Any], characterization: DisplayCharacterization) -> str:
    """Generate output filename if not specified in config."""
    
    ocio_config = config.get('ocio', {})
    
    # Use specified output config if provided
    if 'output_config' in ocio_config:
        return ocio_config['output_config']
    
    # Generate filename from display name
    display_name = characterization.name.lower().replace(' ', '_').replace('-', '_')
    return f"{display_name}_config.ocio"

def create_base_ocio_config(config: Dict[str, Any]) -> 'OCIO.Config':
    """Create base OCIO configuration using ocio:// scheme."""
    
    base_config = config.get('ocio', {}).get('base_config', {})
    config_type = base_config.get('type', 'studio')
    config_version = base_config.get('config_version', 'v2.1.0')
    aces_version = base_config.get('aces_version', 'v1.3')
    ocio_version = base_config.get('ocio_version', 'v2.3')
    
    # Construct the ocio:// URL based on configuration
    ocio_url = f"ocio://{config_type}-config-{config_version}_aces-{aces_version}_ocio-{ocio_version}"
    
    print(f"Loading base OCIO config: {ocio_url}")
    
    try:
        # Load the base configuration using ocio:// scheme
        ocio_config = OCIO.Config.CreateFromFile(ocio_url)
        print(f"✓ Successfully loaded base configuration")
        return ocio_config
        
    except Exception as e:
        print(f"❌ Error loading base configuration: {e}")
        print(f"   Attempted URL: {ocio_url}")
        print(f"   Available configurations:")
        print(f"     - ocio://studio-config-v2.1.0_aces-v1.3_ocio-v2.3")
        print(f"     - ocio://aces-config-v2.1.0_aces-v1.3_ocio-v2.3")
        print(f"   Please check your configuration parameters.")
        raise

def main():
    print("=== OCIO Display Generator ===")
    config_file = "display_config.yaml"
    print(f"Loading configuration from '{config_file}'...")
    config = load_config_from_yaml(config_file)
    print("Validating configuration data...")
    if not validate_config_data(config):
        print("❌ Configuration validation failed. Please check your measurements.")
        return
    print("Creating display characterization...")
    characterization = create_characterization_from_config(config)
    print(f"\nDisplay: {characterization.name}")
    print(f"Peak luminance: {characterization.peak_luminance} cd/m²")
    print(f"Black level: {characterization.black_level} cd/m²")
    print(f"Contrast ratio: {characterization.contrast_ratio:.0f}:1")
    print(f"EOTF: {characterization.eotf_type}")
    print(f"Gamut mapping: {config.get('gamut_mapping', 'clip')}")
    ocio_config = config.get('ocio', {})
    output_config_path = generate_output_filename(config, characterization)
    try:
        print(f"\nCreating base OCIO config...")
        ocio_config_obj = create_base_ocio_config(config)
        gamut_mapping = config.get('gamut_mapping', 'clip')
        cs = create_display_colorspace_from_characterization(
            characterization,
            gamut_mapping=gamut_mapping
        )
        ocio_config_obj.addColorSpace(cs)
        display_name = cs.getName()
        ocio_config_obj.addDisplayView(display_name, "Output", cs.getName())
        with open(output_config_path, "w") as f:
            f.write(ocio_config_obj.serialize())
        print(f"\n✅ Successfully created OCIO config!")
        print(f"   Output file: {output_config_path}")
        print(f"   Colorspaces created: 1")
        print("\nCreated colorspace:")
        print(f"   - {display_name}")
        print(f"\n📋 Usage Instructions:")
        print(f"1. Set OCIO environment variable: export OCIO={os.path.abspath(output_config_path)}")
        print(f"2. In your application, select the display colorspace above")
    except Exception as e:
        print(f"❌ Error creating OCIO config: {e}")
        import traceback
        traceback.print_exc()

# Example usage for single display characterization
if __name__ == "__main__":
    main()