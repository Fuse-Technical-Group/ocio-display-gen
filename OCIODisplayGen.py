import colour
import numpy as np
import PyOpenColorIO as OCIO

def create_display_colorspace(r_xy, g_xy, b_xy, w_xy, name="DisplayOutput", whitepoint_name="whitepoint name"):
    """Create OCIO display colorspace from CIE xy primaries"""
    
    # Create RGB to XYZ matrix using colour-science
    primaries = np.concatenate((r_xy, g_xy, b_xy))
    whitepoint = np.array(w_xy)
    custom_colourspace = colour.RGB_Colourspace(
        "Custom",
        primaries,
        whitepoint,
        whitepoint_name
    )
    custom_colourspace.use_derived_transformation_matrices = True
    XYZ_to_RGB = np.identity(4)
    XYZ_to_RGB[:3, :3] = custom_colourspace.matrix_XYZ_to_RGB
    
    # Create OCIO colorspace
    cs = OCIO.ColorSpace(OCIO.ReferenceSpaceType.REFERENCE_SPACE_DISPLAY,
                         name=f"{name} - Display")
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

# append to existing config
def append_display_colorspace_to_config(input_config_path, output_config_path):
    # Load the existing OCIO config
    config = OCIO.Config.CreateFromFile(input_config_path)

    
    # Example display (P3-D65)
    display_name = "Custom P3-D65"
    r_xy = (0.680, 0.320)
    g_xy = (0.265, 0.690)
    b_xy = (0.150, 0.060)

    whitepoint_name = "D65"
    w_xy = (0.3127, 0.3290)  # D65
    
    # Create and add colorspace
    cs = create_display_colorspace(r_xy, g_xy, b_xy, w_xy, name=display_name, whitepoint_name=whitepoint_name)
    config.addColorSpace(cs)
    
    # Add display
    config.addDisplayView(f"{display_name} - Display", "Output", cs.getName())
    
    return config

# Save config
input_config_path = "studio-config-v2.1.0_aces-v1.3_ocio-v2.3.ocio"
output_config_path = "custom+studio-config-v2.1.0_aces-v1.3_ocio-v2.3.ocio"
config = append_display_colorspace_to_config(input_config_path, output_config_path)
with open(output_config_path, "w") as f:
    f.write(config.serialize())