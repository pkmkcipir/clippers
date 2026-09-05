"""Generates a handful of basic 3D LUT (.cube) files for the LUT / Color
Grading effect. These are simple, hand-specified color transforms (not
scanned from film stock or licensed packs) -- honest starting presets you
can swap out for real .cube files later by dropping them in the same
folder (any .cube file works with editor/effects.py's lut effect).
"""
from __future__ import annotations

from pathlib import Path

Vec3 = tuple[float, float, float]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _warm(r: float, g: float, b: float) -> Vec3:
    return _clamp01(r * 1.12 + 0.02), _clamp01(g * 1.03), _clamp01(b * 0.85)


def _cool(r: float, g: float, b: float) -> Vec3:
    return _clamp01(r * 0.90), _clamp01(g * 1.00), _clamp01(b * 1.15 + 0.02)


def _high_contrast_bw(r: float, g: float, b: float) -> Vec3:
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    # S-curve for extra contrast, centered at 0.5.
    contrasted = _clamp01(0.5 + (luma - 0.5) * 1.6)
    return contrasted, contrasted, contrasted


def _faded(r: float, g: float, b: float) -> Vec3:
    # Lift blacks, compress whites slightly -- the "matte film" look.
    lift = 0.08
    return (_clamp01(r * 0.92 + lift), _clamp01(g * 0.92 + lift), _clamp01(b * 0.90 + lift))


PRESETS = {
    "warm": _warm,
    "cool": _cool,
    "high_contrast_bw": _high_contrast_bw,
    "faded": _faded,
}


def generate_cube_content(transform, size: int = 17) -> str:
    lines = [
        "TITLE \"AI Klipers generated LUT\"",
        f"LUT_3D_SIZE {size}",
    ]
    for b_i in range(size):
        for g_i in range(size):
            for r_i in range(size):
                r, g, b = r_i / (size - 1), g_i / (size - 1), b_i / (size - 1)
                out_r, out_g, out_b = transform(r, g, b)
                lines.append(f"{out_r:.6f} {out_g:.6f} {out_b:.6f}")
    return "\n".join(lines) + "\n"


def ensure_bundled_luts(output_dir: str | Path) -> dict[str, str]:
    """Writes each preset's .cube file into output_dir if not already
    present, returns {preset_name: path}."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, transform in PRESETS.items():
        path = output_dir / f"{name}.cube"
        if not path.exists():
            path.write_text(generate_cube_content(transform), encoding="utf-8")
        paths[name] = str(path)
    return paths
