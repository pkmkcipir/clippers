"""Builds ffmpeg filter graphs for every effect in the Video Editor.

Every builder here returns a `-filter_complex`-ready graph string with
input pad `[0:v]` and output pad `[outv]` -- even effects that are just a
single linear filter -- so editor/timeline_renderer.py can invoke ffmpeg
the same way for every clip regardless of which effects are attached.

Two effects (glow, blur_background) need a split/blend sub-graph instead
of a simple comma-chain; at most one such "graph effect" is supported per
clip (documented limitation -- combining both would need pad-name
bookkeeping this version doesn't do). Speed is handled by the renderer,
not here, since it also changes a clip's duration and needs a matching
audio-tempo filter.

EFFECT_REGISTRY describes each effect's parameters (for building generic
UI controls) -- see app/pages/video_editor.py's effects panel.
"""
from __future__ import annotations

from utils.logger import get_logger

log = get_logger("effects")

EFFECT_REGISTRY = {
    "color": {
        "label_id": "Warna (Brightness/Contrast/Saturation)", "label_en": "Color (Brightness/Contrast/Saturation)",
        "params": {"brightness": {"min": -1.0, "max": 1.0, "default": 0.0},
                   "contrast": {"min": 0.0, "max": 3.0, "default": 1.0},
                   "saturation": {"min": 0.0, "max": 3.0, "default": 1.0}},
    },
    "vignette": {"label_id": "Vignette", "label_en": "Vignette",
                 "params": {"strength": {"min": 0.0, "max": 1.0, "default": 0.5}}},
    "sharpen": {"label_id": "Sharpen", "label_en": "Sharpen",
                "params": {"amount": {"min": 0.0, "max": 3.0, "default": 1.0}}},
    "denoise": {"label_id": "Denoise", "label_en": "Denoise",
                "params": {"strength": {"min": 0.0, "max": 10.0, "default": 4.0}}},
    "glow": {"label_id": "Glow", "label_en": "Glow",
             "params": {"intensity": {"min": 0.0, "max": 1.0, "default": 0.5}}},
    "lut": {"label_id": "LUT / Color Grading", "label_en": "LUT / Color Grading",
            "params": {"preset": {"options": ["warm", "cool", "high_contrast_bw", "faded"], "default": "warm"}}},
    "auto_zoom": {"label_id": "Auto Zoom", "label_en": "Auto Zoom",
                  "params": {"start_zoom": {"min": 1.0, "max": 2.0, "default": 1.0},
                             "end_zoom": {"min": 1.0, "max": 2.0, "default": 1.15}}},
    "motion_blur": {"label_id": "Motion Blur (aproksimasi)", "label_en": "Motion Blur (approximation)",
                    "params": {"frames": {"min": 2, "max": 7, "default": 3}}},
    "reframe": {"label_id": "Face Tracking / Auto Reframe", "label_en": "Face Tracking / Auto Reframe",
                "params": {"target_aspect": {"options": [("9:16", 9 / 16), ("1:1", 1.0), ("4:5", 0.8)], "default": 9 / 16}}},
    "blur_background": {"label_id": "Blur Background (aproksimasi)", "label_en": "Blur Background (approximation)",
                         "params": {"strength": {"min": 4, "max": 30, "default": 12}}},
    "speed": {"label_id": "Slow Motion / Speed Ramp", "label_en": "Slow Motion / Speed Ramp",
              "params": {"multiplier": {"min": 0.25, "max": 4.0, "default": 1.0}}},
}

GRAPH_EFFECT_KINDS = {"glow", "blur_background"}


# ---- linear (single filter fragment) builders --------------------------

def _color(params, **_):
    b = float(params.get("brightness", 0.0))
    c = float(params.get("contrast", 1.0))
    s = float(params.get("saturation", 1.0))
    return f"eq=brightness={b:.3f}:contrast={c:.3f}:saturation={s:.3f}"


def _vignette(params, **_):
    strength = float(params.get("strength", 0.5))
    angle = 0.3 + strength * (1.35)  # radians, capped comfortably under PI/2
    return f"vignette=angle={angle:.4f}"


def _sharpen(params, **_):
    amount = float(params.get("amount", 1.0))
    return f"unsharp=5:5:{amount:.2f}:5:5:0.0"


def _denoise(params, **_):
    strength = float(params.get("strength", 4.0))
    chroma = strength * 0.6
    return f"hqdn3d={strength:.1f}:{strength:.1f}:{chroma:.1f}:{chroma:.1f}"


def _lut(params, lut_dir="temp/luts", **_):
    from editor.lut_presets import ensure_bundled_luts
    custom_path = params.get("path")
    if custom_path:
        path = custom_path
    else:
        presets = ensure_bundled_luts(lut_dir)
        path = presets.get(params.get("preset", "warm"), next(iter(presets.values())))
    escaped = str(path).replace("\\", "/").replace(":", "\\:")
    return f"lut3d='{escaped}'"


def _auto_zoom(params, clip_duration=10.0, **_):
    start_zoom = float(params.get("start_zoom", 1.0))
    end_zoom = float(params.get("end_zoom", 1.15))
    total_frames = max(int(round(clip_duration * 30)), 1)
    zoom_expr = f"{start_zoom:.3f}+({end_zoom:.3f}-{start_zoom:.3f})*on/{total_frames}"
    return f"zoompan=z='{zoom_expr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':fps=30"


def _motion_blur(params, **_):
    frames = int(params.get("frames", 3))
    frames = frames if frames % 2 == 1 else frames + 1  # tmix wants an odd count for a centered blend
    return f"tmix=frames={frames}"


def _crop_dims_for_aspect(width: int, height: int, target_aspect: float) -> tuple[int, int]:
    if width / height > target_aspect:
        crop_h = height
        crop_w = int(round(crop_h * target_aspect / 2)) * 2
    else:
        crop_w = width
        crop_h = int(round(crop_w / target_aspect / 2)) * 2
    return max(crop_w, 2), max(crop_h, 2)


def _piecewise_pixel_expr(path, getter, crop_dim: int, frame_dim: int) -> str:
    """Builds a nested if(lt(t, ...), ..., ...) ffmpeg expression that
    linearly interpolates a pixel-space crop coordinate between keyframes
    in `path` (see ai/face_tracking.PanPoint), clamped to stay in-frame."""
    def clamp(expr: str) -> str:
        return f"max(0,min({frame_dim - crop_dim},{expr}))"

    if len(path) < 2:
        center = (getter(path[0]) if path else 0.5) * frame_dim
        return clamp(f"{center - crop_dim / 2:.2f}")

    segments = [(p0, p1) for p0, p1 in zip(path, path[1:]) if p1.t > p0.t]
    if not segments:
        center = getter(path[-1]) * frame_dim
        return clamp(f"{center - crop_dim / 2:.2f}")

    last_c = getter(segments[-1][1]) * frame_dim
    expr = clamp(f"{last_c - crop_dim / 2:.2f}")  # base case: t past the final keyframe

    for p0, p1 in reversed(segments):
        c0, c1 = getter(p0) * frame_dim, getter(p1) * frame_dim
        interp = f"({c0:.2f}+({c1:.2f}-{c0:.2f})*(t-{p0.t:.3f})/{(p1.t - p0.t):.3f})"
        expr = f"if(lt(t,{p1.t:.3f}),{clamp(f'({interp}-{crop_dim}/2)')},{expr})"
    return expr


def _get_reframe_path(video_path, clip_source_start, clip_source_end, clip_duration):
    from ai.face_tracking import build_reframe_path, flat_center_path
    if video_path and clip_source_start is not None and clip_source_end is not None:
        try:
            return build_reframe_path(video_path, clip_source_start, clip_source_end)
        except Exception as exc:
            log.warning("Face tracking failed (%s), falling back to a centered crop", exc)
    return flat_center_path(clip_duration)


def _reframe(params, width=1920, height=1080, clip_duration=10.0, video_path=None,
             clip_source_start=None, clip_source_end=None, **_):
    target_aspect = float(params.get("target_aspect", 9 / 16))
    path = _get_reframe_path(video_path, clip_source_start, clip_source_end, clip_duration)
    crop_w, crop_h = _crop_dims_for_aspect(width, height, target_aspect)
    x_expr = _piecewise_pixel_expr(path, lambda p: p.cx, crop_w, width)
    y_expr = _piecewise_pixel_expr(path, lambda p: p.cy, crop_h, height)
    return f"crop=w={crop_w}:h={crop_h}:x='{x_expr}':y='{y_expr}'"


_LINEAR_BUILDERS = {
    "color": _color, "vignette": _vignette, "sharpen": _sharpen, "denoise": _denoise,
    "lut": _lut, "auto_zoom": _auto_zoom, "motion_blur": _motion_blur, "reframe": _reframe,
}


# ---- graph (split/blend) effects ----------------------------------------

def _build_graph_effect(effect, pre_chain, *, width, height, clip_duration, video_path,
                         clip_source_start, clip_source_end) -> str:
    pre = f"{pre_chain}," if pre_chain else ""

    if effect.kind == "glow":
        intensity = float(effect.params.get("intensity", 0.5))
        blur_sigma = 4 + intensity * 10
        brightness = 0.05 + intensity * 0.15
        opacity = 0.3 + intensity * 0.4
        return (
            f"[0:v]{pre}split=2[gbase][gsrc];"
            f"[gsrc]gblur=sigma={blur_sigma:.1f},eq=brightness={brightness:.2f}[gblurred];"
            f"[gbase][gblurred]blend=all_mode=screen:all_opacity={opacity:.2f}[outv]"
        )

    if effect.kind == "blur_background":
        strength = float(effect.params.get("strength", 12))
        path = _get_reframe_path(video_path, clip_source_start, clip_source_end, clip_duration)
        fg_w, fg_h = int(width * 0.42 / 2) * 2, int(height * 0.62 / 2) * 2
        x_expr = _piecewise_pixel_expr(path, lambda p: p.cx, fg_w, width)
        y_expr = _piecewise_pixel_expr(path, lambda p: p.cy, fg_h, height)
        return (
            f"[0:v]{pre}split=2[bbase][bsrc];"
            f"[bbase]boxblur={strength:.0f}:1[bblurred];"
            f"[bsrc]crop=w={fg_w}:h={fg_h}:x='{x_expr}':y='{y_expr}'[bfg];"
            f"[bblurred][bfg]overlay=x='{x_expr}':y='{y_expr}'[outv]"
        )

    raise ValueError(f"Unknown graph effect kind: {effect.kind}")


# ---- public entry point --------------------------------------------------

def build_video_filter_graph(effects, *, width: int, height: int, clip_duration: float,
                              video_path: str | None = None, clip_source_start: float | None = None,
                              clip_source_end: float | None = None, lut_dir: str = "temp/luts",
                              extra_tail: str | None = None) -> str:
    """Always returns a full `-filter_complex` graph string ending in
    `[outv]`, whether the clip has zero, one, or several effects, and
    whether or not one of them needs a split/blend sub-graph. `extra_tail`
    lets the renderer graft a trailing filter (namely `setpts=...` for a
    constant-speed effect) onto whichever path was taken, without the
    caller needing to know which shape the graph is."""
    linear_parts: list[str] = []
    graph_effect = None

    for fx in effects:
        if fx.kind == "speed":
            continue  # handled by the renderer directly
        if fx.kind in GRAPH_EFFECT_KINDS:
            if graph_effect is not None:
                log.warning("Only one of %s is supported per clip; ignoring extra '%s'",
                            GRAPH_EFFECT_KINDS, fx.kind)
                continue
            graph_effect = fx
            continue
        builder = _LINEAR_BUILDERS.get(fx.kind)
        if not builder:
            log.warning("Unknown effect kind '%s', skipping", fx.kind)
            continue
        frag = builder(fx.params, width=width, height=height, clip_duration=clip_duration,
                       video_path=video_path, clip_source_start=clip_source_start,
                       clip_source_end=clip_source_end, lut_dir=lut_dir)
        if frag:
            linear_parts.append(frag)

    if graph_effect is None:
        chain_parts = linear_parts + ([extra_tail] if extra_tail else [])
        chain = ",".join(chain_parts) if chain_parts else "null"
        return f"[0:v]{chain}[outv]"

    graph = _build_graph_effect(graph_effect, ",".join(linear_parts) if linear_parts else None,
                                 width=width, height=height, clip_duration=clip_duration, video_path=video_path,
                                 clip_source_start=clip_source_start, clip_source_end=clip_source_end)
    if extra_tail:
        graph = graph[: -len("[outv]")] + f",{extra_tail}[outv]"
    return graph


def build_speed_audio_filter(multiplier: float) -> str:
    """atempo only accepts 0.5-2.0 per instance; chain instances to cover
    a wider range (e.g. 4x speed = two atempo=2.0 filters back to back)."""
    multiplier = max(float(multiplier), 0.05)
    filters = []
    remaining = multiplier
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)
