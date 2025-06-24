# Gamut Mapping Guide: Handling Out-of-Gamut Colors

## Overview

When characterizing a display, you'll often encounter image colors that are outside your display's gamut (the range of colors it can reproduce). This guide explains the different gamut mapping strategies available and when to use each one.

## The Problem: Out-of-Gamut Colors

### What Happens Without Gamut Mapping

Without proper gamut mapping, out-of-gamut colors are simply **clipped** to the nearest valid RGB value:

```
Original Color: [1.2, 0.8, 0.3]  # Red component > 1.0 (out of gamut)
Clipped Result: [1.0, 0.8, 0.3]  # Red clipped to 1.0
```

This causes:
- **Loss of color relationships** - colors that were different become identical
- **Saturation loss** - vibrant colors become dull
- **Color shifts** - the overall color balance changes
- **Poor visual quality** - especially noticeable in gradients and skin tones

## Available Gamut Mapping Strategies

### Basic Strategies

#### 1. **"clip" - Simple Clipping (Default)**

**What it does:** Directly clips RGB values to [0,1] range
**Speed:** Fastest
**Quality:** Basic

```yaml
gamut_mapping: "clip"
```

**Use when:**
- Speed is critical
- You're working with content that's already gamut-limited
- You want predictable, simple behavior

**Example:**
```
Input:  [1.2, 0.8, 0.3]  # Out of gamut red
Output: [1.0, 0.8, 0.3]  # Clipped red
```

#### 2. **"perceptual" - Perceptual Gamut Mapping (Recommended)**

**What it does:** Preserves color relationships and visual appearance
**Speed:** Medium
**Quality:** Best for most content

```yaml
gamut_mapping: "perceptual"
```

**Use when:**
- Working with photographic or cinematic content
- You want natural-looking results
- Preserving color relationships is important
- **This is the recommended choice for most applications**

**Example:**
```
Input:  [1.2, 0.8, 0.3]  # Bright red
Output: [1.0, 0.75, 0.28]  # Adjusted to maintain relationships
```

#### 3. **"saturation" - Saturation-Preserving**

**What it does:** Preserves saturation while adjusting lightness
**Speed:** Medium
**Quality:** Good for graphics and logos

```yaml
gamut_mapping: "saturation"
```

**Use when:**
- Working with graphics, logos, or UI elements
- Saturation is more important than lightness
- You want vibrant colors even if they're not exactly the same

**Example:**
```
Input:  [1.2, 0.8, 0.3]  # Oversaturated red
Output: [1.0, 0.67, 0.25]  # Same saturation, adjusted lightness
```

#### 4. **"relative" - Relative Colorimetric**

**What it does:** Preserves colors that are in gamut, clips out-of-gamut colors
**Speed:** Fast
**Quality:** Good for proofing

```yaml
gamut_mapping: "relative"
```

**Use when:**
- Proofing or comparing colors
- You want to see exactly what's in/out of gamut
- Working with color-accurate workflows

**Example:**
```
Input:  [1.2, 0.8, 0.3]  # Out of gamut
Output: [1.0, 0.8, 0.3]  # Clipped (same as "clip")
```

#### 5. **"absolute" - Absolute Colorimetric**

**What it does:** Preserves absolute color values where possible
**Speed:** Fast
**Quality:** Best for color-accurate workflows

```yaml
gamut_mapping: "absolute"
```

**Use when:**
- Color-accurate proofing
- Working with color management systems
- You need to preserve exact color values

### Advanced Strategies

#### 6. **"soft_clip" - Soft Clipping (Roll-Off Approach)**

**What it does:** Preserves color response through most of gamut, gradually compresses near edges
**Speed:** Medium
**Quality:** Excellent for professional work

```yaml
gamut_mapping: "soft_clip"
```

**How it works:**
```
Input Color Space: [0, 1.5] range (wider than display)
Display Gamut:     [0, 1.0] range

Soft Clipping Response:
- Colors 0.0 to 0.8: Linear mapping (preserved)
- Colors 0.8 to 1.0: Gradual compression
- Colors 1.0 to 1.5: Smooth roll-off to 1.0
```

**Use when:**
- **Cinematic content** where color relationships are critical
- **HDR to SDR conversion** where you want to preserve most of the color space
- **Professional color grading** where smooth transitions are important
- **Content with wide color gamuts** (Rec.2020, Adobe RGB)

**Advantages:**
- ✅ **Natural color reproduction** in the central gamut
- ✅ **Smooth gradients** without banding
- ✅ **Preserved color relationships** for most content
- ✅ **No hard clipping artifacts** at gamut boundaries

#### 7. **"adaptive" - Adaptive Gamut Mapping**

**What it does:** Automatically selects the best strategy based on content characteristics
**Speed:** Medium
**Quality:** Excellent for mixed content

```yaml
gamut_mapping: "adaptive"
```

**How it works:**
The adaptive algorithm analyzes the content and applies:
- **Perceptual mapping** for photographic content
- **Saturation mapping** for graphics/logos
- **Soft clipping** for content with wide gamut coverage
- **Hard clipping** for content already within gamut

**Use when:**
- Working with **mixed content** (photos, graphics, UI elements)
- You want **automatic optimization** per content type
- **Professional workflows** where content varies significantly

#### 8. **"hue_preserving" - Hue-Preserving Gamut Mapping**

**What it does:** Maintains exact hue values while adjusting saturation and lightness
**Speed:** Medium
**Quality:** Excellent for color-critical applications

```yaml
gamut_mapping: "hue_preserving"
```

**Use when:**
- **Brand colors** where hue accuracy is critical
- **Product photography** where color fidelity matters
- **UI/UX design** where color consistency is important
- **Print-to-screen proofing** where hue matching is essential

## Performance Comparison

| Strategy | Speed | Quality | Complexity | Use Case |
|----------|-------|---------|------------|----------|
| `clip` | ⚡⚡⚡ | ⭐ | Low | Speed-critical |
| `relative` | ⚡⚡ | ⭐⭐ | Low | Color proofing |
| `absolute` | ⚡⚡ | ⭐⭐⭐ | Low | Color-accurate |
| `saturation` | ⚡ | ⭐⭐⭐ | Medium | Graphics/UI |
| `perceptual` | ⚡ | ⭐⭐⭐⭐ | Medium | Most content |
| `soft_clip` | ⚡ | ⭐⭐⭐⭐⭐ | High | Professional |
| `adaptive` | ⚡ | ⭐⭐⭐⭐ | High | Mixed content |
| `hue_preserving` | ⚡ | ⭐⭐⭐ | Medium | Color-critical |

## Configuration Examples

### For Cinematic Content
```yaml
gamut_mapping: "soft_clip"  # Preserve color relationships with smooth roll-off
```

### For Mixed Content Applications
```yaml
gamut_mapping: "adaptive"  # Automatic optimization per content type
```

### For Brand Identity Graphics
```yaml
gamut_mapping: "hue_preserving"  # Maintain exact brand colors
```

### For Graphics/UI
```yaml
gamut_mapping: "saturation"  # Keep colors vibrant
```

### For Color Proofing
```yaml
gamut_mapping: "relative"    # See what's in/out of gamut
```

### For Speed-Critical Applications
```yaml
gamut_mapping: "clip"        # Fastest option
```

## Real-World Examples

### Example 1: HDR Cinematic Content

**Content:** HDR movie with Rec.2020 gamut
**Display:** sRGB monitor
**Challenge:** Preserve cinematic color relationships

**Solution:** `soft_clip`
```
Result: 
- Skin tones preserved naturally
- Bright highlights roll off smoothly
- Color relationships maintained
- No hard clipping artifacts
```

### Example 2: Brand Identity Graphics

**Content:** Company logo with specific brand colors
**Display:** Various monitors with different gamuts
**Challenge:** Maintain brand color accuracy

**Solution:** `hue_preserving`
```
Result:
- Brand colors maintain correct hue
- Saturation adjusted to fit display
- Consistent appearance across devices
- Brand identity preserved
```

### Example 3: Mixed Content Application

**Content:** Application with photos, graphics, and UI elements
**Display:** Professional monitor
**Challenge:** Optimal handling of diverse content types

**Solution:** `adaptive`
```
Result:
- Photos use perceptual mapping
- Graphics use saturation mapping
- UI elements use appropriate strategy
- Automatic optimization per content type
```

### Example 4: HDR to SDR Conversion

When converting HDR content (Rec.2020) to an SDR display (sRGB):

**Without gamut mapping:**
- Bright reds become dull
- Skin tones shift unnaturally
- Color relationships are lost

**With perceptual gamut mapping:**
- Colors maintain their relative relationships
- Skin tones look natural
- Overall image quality is preserved

**With soft clipping:**
- Most colors preserved naturally
- Smooth roll-off at gamut boundaries
- Professional-quality results

### Example 5: Wide Gamut to Standard Gamut

When converting from a wide gamut (Adobe RGB) to standard gamut (sRGB):

**Without gamut mapping:**
- Vibrant greens become muted
- Color gradients show banding
- Saturation is lost

**With saturation gamut mapping:**
- Greens remain vibrant
- Gradients are smooth
- Saturation is preserved

## Implementation Details

### Soft Clipping Algorithm

The soft clipping implementation uses a **compression curve** that:

1. **Analyzes the source gamut** vs. target gamut
2. **Calculates compression ratios** for each color channel
3. **Applies gradual compression** starting at 80% of gamut
4. **Uses smooth roll-off** functions (sigmoid, exponential, etc.)

### Mathematical Approach

```python
# Simplified soft clipping algorithm
def soft_clip(color_value, gamut_limit=1.0, compression_start=0.8):
    if color_value <= compression_start * gamut_limit:
        # Linear region - no compression
        return color_value
    else:
        # Compression region - gradual roll-off
        excess = color_value - (compression_start * gamut_limit)
        compression_range = gamut_limit - (compression_start * gamut_limit)
        compression_factor = 1.0 - (excess / compression_range) ** 2
        return (compression_start * gamut_limit) + (excess * compression_factor)
```

## Testing and Validation

### Test Patterns for Gamut Mapping

1. **Gamut Boundary Test**
   - Create test images with colors at gamut boundaries
   - Verify smooth transitions without hard clipping

2. **Color Relationship Test**
   - Test with images containing color gradients
   - Ensure relationships are preserved appropriately

3. **Performance Test**
   - Measure processing time for different strategies
   - Verify acceptable performance for your use case

## Recommendations

### For Most Users
```yaml
gamut_mapping: "perceptual"  # Best balance of quality and performance
```

### For Professional Work
```yaml
gamut_mapping: "soft_clip"  # Highest quality for cinematic content
```

### For Graphics/UI Work
```yaml
gamut_mapping: "saturation"  # Preserve vibrant colors
```

### For Mixed Content
```yaml
gamut_mapping: "adaptive"  # Automatic optimization
```

### For Color-Accurate Workflows
```yaml
gamut_mapping: "absolute"  # Preserve exact color values
```

### For Speed-Critical Applications
```yaml
gamut_mapping: "clip"  # Fastest option
```

## Troubleshooting

### Common Issues

1. **Hard clipping artifacts**
   - **Solution:** Use `soft_clip` or `perceptual` instead of `clip`

2. **Color banding in gradients**
   - **Solution:** Use `soft_clip` for smooth transitions

3. **Poor performance**
   - **Solution:** Use `clip` for speed-critical applications

4. **Inconsistent results across content types**
   - **Solution:** Use `adaptive` for mixed content

### Performance Optimization

- **Batch processing:** Use faster strategies (`clip`, `relative`) for bulk operations
- **Real-time applications:** Use `clip` or `relative` for live preview
- **Quality-critical work:** Use `soft_clip` or `perceptual` for final output 