# OCIO Builtin Transforms — Negative Results

What the OpenColorIO builtin registry does **not** contain. Recorded
because the absences are not documented upstream, and because assuming
a transform exists produces a config that fails to build long after the
design decision that depended on it.

Verified against **PyOpenColorIO 2.5.1** (98 builtins). Re-run the
check below when the pinned version changes.

## No parameterized gamut mapping

None of these exist, under any spelling:

```text
GAMUT-MAP - PERCEPTUAL       GAMUT-MAP - ADAPTIVE
GAMUT-MAP - SATURATION       GAMUT-MAP - SOFT-CLIP
GAMUT-MAP - RELATIVE         GAMUT-MAP - HUE-PRESERVING
GAMUT-MAP - ABSOLUTE
```

The registry holds exactly one gamut-compression builtin,
`ACES-LMT - ACES 1.3 Reference Gamut Compression`, and it takes no
parameters — it cannot be limited to a measured wall's primaries.

Parameterized gamut compression comes from a different API:
`FixedFunctionTransform` with `FIXED_FUNCTION_ACES_GAMUT_COMPRESS_20`
(and `FIXED_FUNCTION_ACES_OUTPUT_TRANSFORM_20`,
`FIXED_FUNCTION_ACES_RGB_TO_JMH_20`), which accepts primaries and peak
luminance and requires config profile ≥ 2.4. This is the evidence
behind SPEC.md §spec:view-transform's "OCIO has no other builtin gamut
mapping": not that gamut compression is unavailable, but that it is not
a builtin and not otherwise parameterizable.

## No generic transfer curves

```text
CURVE - LINEAR_to_sRGB       CURVE - LINEAR_to_GAMMA2.4
CURVE - LINEAR_to_REC709     CURVE - LINEAR_to_HLG
```

The complete `CURVE -` set is eight entries: ST-2084 and HLG in both
directions, plus four camera log curves (ACEScct, Apple Log, Canon
CLog2/CLog3). For a plain power function use `ExponentTransform`; the
`DISPLAY -` builtins are whole pipelines (primaries plus curve), not
curves.

## Checking a different version

```python
import PyOpenColorIO as OCIO

names = [n for n, _ in OCIO.BuiltinTransformRegistry().getBuiltins()]
print(OCIO.__version__, len(names))
print([n for n in names if "GAMUT" in n.upper()])
print([n for n in names if n.startswith("CURVE")])
```
