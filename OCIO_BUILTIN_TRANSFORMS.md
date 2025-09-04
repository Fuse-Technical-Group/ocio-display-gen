# OCIO Builtin Transforms Reference

## Overview

This document clarifies what builtin transforms actually exist in OpenColorIO 2.4.2, as there is often confusion about which transforms are available.

## ❌ **What Does NOT Exist**

Many commonly assumed builtin transforms **do not actually exist** in OCIO:

### **Gamut Mapping (All Non-Existent)**
```python
# ❌ These DO NOT EXIST:
OCIO.BuiltinTransform("GAMUT-MAP - PERCEPTUAL")     # ❌
OCIO.BuiltinTransform("GAMUT-MAP - SATURATION")     # ❌  
OCIO.BuiltinTransform("GAMUT-MAP - RELATIVE")       # ❌
OCIO.BuiltinTransform("GAMUT-MAP - ABSOLUTE")       # ❌
OCIO.BuiltinTransform("GAMUT-MAP - SOFT-CLIP")      # ❌
OCIO.BuiltinTransform("GAMUT-MAP - ADAPTIVE")       # ❌
OCIO.BuiltinTransform("GAMUT-MAP - HUE-PRESERVING") # ❌
```

### **Generic Curve Transforms (Most Non-Existent)**
```python
# ❌ These DO NOT EXIST:
OCIO.BuiltinTransform("CURVE - LINEAR_to_sRGB")      # ❌
OCIO.BuiltinTransform("CURVE - LINEAR_to_REC709")    # ❌
OCIO.BuiltinTransform("CURVE - LINEAR_to_GAMMA2.4")  # ❌
OCIO.BuiltinTransform("CURVE - LINEAR_to_HLG")       # ❌
```

## ✅ **What Actually Exists**

### **Working Curve Transforms**
```python
# ✅ These DO EXIST:
OCIO.BuiltinTransform("CURVE - LINEAR_to_ST-2084")    # ✅ PQ OETF
OCIO.BuiltinTransform("CURVE - ST-2084_to_LINEAR")    # ✅ PQ EOTF 
OCIO.BuiltinTransform("CURVE - HLG-OETF")             # ✅ HLG OETF
OCIO.BuiltinTransform("CURVE - HLG-OETF-INVERSE")     # ✅ HLG EOTF
```

### **Display Transforms (Include Full Pipeline)**
```python
# ✅ These DO EXIST and include primaries + OETF:
OCIO.BuiltinTransform("DISPLAY - CIE-XYZ-D65_to_sRGB")              # ✅
OCIO.BuiltinTransform("DISPLAY - CIE-XYZ-D65_to_REC.1886-REC.709")  # ✅
OCIO.BuiltinTransform("DISPLAY - CIE-XYZ-D65_to_REC.2100-PQ")       # ✅
OCIO.BuiltinTransform("DISPLAY - CIE-XYZ-D65_to_DisplayP3")         # ✅
```

### **ACES Transforms**
```python
# ✅ These DO EXIST:
OCIO.BuiltinTransform("UTILITY - ACES-AP1_to_CIE-XYZ-D65_BFD")      # ✅
OCIO.BuiltinTransform("ACES-LMT - BLUE_LIGHT_ARTIFACT_FIX")          # ✅
OCIO.BuiltinTransform("ACES-OUTPUT - ACES2065-1_to_CIE-XYZ-D65 - SDR-VIDEO_1.0") # ✅
```

## 🛠️ **Alternatives for Missing Transforms**

### **For Gamut Mapping:**
Since builtin gamut mapping transforms don't exist, you must:
1. **Software Implementation**: Use helper functions like our `naive_gamut_map_preserve_luminance()`
2. **Custom 3D LUTs**: Generate 3D LUTs with sophisticated gamut mapping and use `FileTransform`
3. **Color Science Libraries**: Use libraries like `colour-science` to implement advanced algorithms

### **For Generic Gamma Curves:**
```python
# Instead of non-existent "CURVE - LINEAR_to_GAMMA2.4"
gamma_transform = OCIO.ExponentTransform()
gamma_values = [1.0/2.4] * 4  # OETF is 1/gamma for RGBA
gamma_transform.setValue(gamma_values)
```

### **For Other Missing Curves:**
```python
# Use FileTransform with 1D LUTs
file_transform = OCIO.FileTransform()
file_transform.setSrc("path/to/custom_curve.lut")
```

## 📋 **Complete List of Available Builtin Transforms**

OCIO 2.4.2 contains **93 builtin transforms** total. Key categories:

- **ACES Transforms**: ~60 transforms for ACES workflows
- **Camera Transforms**: Apple, ARRI, Canon, Panasonic, RED, Sony
- **Display Transforms**: ~15 transforms for common display standards  
- **Curve Transforms**: ~5 basic curve operations
- **Utility Transforms**: Color space conversions

## 🚨 **Important Notes**

1. **No Gamut Mapping**: OCIO has no builtin gamut mapping transforms
2. **Limited Curves**: Only PQ and HLG have builtin curve transforms
3. **Display Transforms**: These are complete pipelines (primaries + OETF), not just curves
4. **Documentation Gap**: OCIO documentation doesn't clearly list all available builtins

## 🔍 **How to Discover Available Transforms**

```python
import PyOpenColorIO as OCIO

# Get the registry (OCIO 2.x+)
registry = OCIO.BuiltinTransformRegistry()
transforms = registry.getBuiltins()

for name, description in transforms:
    print(f"{name}: {description}")
```

## 💡 **Best Practices**

1. **Always test**: Use try/catch when creating builtin transforms
2. **Have fallbacks**: Implement alternatives for missing transforms  
3. **Use software helpers**: For gamut mapping and tone mapping
4. **Check versions**: Builtin transforms vary between OCIO versions
5. **Read the registry**: Use the registry to discover what's actually available

This knowledge is crucial for implementing robust color pipelines that work with actual OCIO capabilities rather than assumed functionality.