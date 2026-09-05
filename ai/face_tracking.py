"""Turns raw face detections into a smoothed pan path for effects that
need to know "where's the subject": Face Tracking / Auto Reframe (crop a
horizontal video to vertical, following the speaker) and Blur Background
(keep the subject sharp, blur everything else).

This is a coarse, practical implementation -- Haar-cascade detection at
~1 sample/sec, moving-average smoothed -- not a trained tracker. It's
enough to keep a talking-head subject roughly centered without jittering
frame to frame, which is what these two effects actually need.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai.scene_detection import detect_faces
from editor.ffmpeg_utils import get_media_info
from utils.logger import get_logger

log = get_logger("face_tracking")

MAX_KEYFRAMES = 40  # keeps the generated ffmpeg expression bounded


@dataclass
class PanPoint:
    t: float           # seconds, relative to the clip's own start (0 = clip start)
    cx: float           # face center x, as a 0-1 fraction of frame width
    cy: float           # face center y, as a 0-1 fraction of frame height


def build_reframe_path(video_path: str, start: float, end: float, *,
                        sample_fps: float = 1.0, smoothing_window: int = 2) -> list[PanPoint]:
    """Returns a path covering [0, end-start] (clip-relative seconds).
    Falls back to a flat centered path if no faces are found anywhere in
    the range, so callers can always treat this as "the pan path" without
    a None-check."""
    clip_len = max(end - start, 0.01)
    info = get_media_info(video_path)
    width, height = info.get("width", 0), info.get("height", 0)
    if not width or not height:
        return [PanPoint(0.0, 0.5, 0.5), PanPoint(clip_len, 0.5, 0.5)]

    windows = [w for w in detect_faces(video_path, sample_fps=sample_fps)
               if start <= w.time_sec <= end and w.face_count > 0]
    if not windows:
        return [PanPoint(0.0, 0.5, 0.5), PanPoint(clip_len, 0.5, 0.5)]

    import cv2
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(video_path)
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

    raw_points: list[PanPoint] = []
    for w in windows:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(w.time_sec * native_fps)))
        ret, frame = cap.read()
        if not ret:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(60, 60))
        if len(faces) == 0:
            continue
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])  # largest face = likely subject
        raw_points.append(PanPoint(round(w.time_sec - start, 3), (fx + fw / 2) / width, (fy + fh / 2) / height))
    cap.release()

    if not raw_points:
        return [PanPoint(0.0, 0.5, 0.5), PanPoint(clip_len, 0.5, 0.5)]

    smoothed: list[PanPoint] = []
    for i in range(len(raw_points)):
        lo, hi = max(0, i - smoothing_window), min(len(raw_points), i + smoothing_window + 1)
        window = raw_points[lo:hi]
        smoothed.append(PanPoint(
            raw_points[i].t,
            sum(p.cx for p in window) / len(window),
            sum(p.cy for p in window) / len(window),
        ))

    if smoothed[0].t > 0.05:
        smoothed.insert(0, PanPoint(0.0, smoothed[0].cx, smoothed[0].cy))
    if smoothed[-1].t < clip_len - 0.05:
        smoothed.append(PanPoint(clip_len, smoothed[-1].cx, smoothed[-1].cy))

    if len(smoothed) > MAX_KEYFRAMES:
        stride = len(smoothed) / MAX_KEYFRAMES
        smoothed = [smoothed[int(i * stride)] for i in range(MAX_KEYFRAMES)]
        if smoothed[-1].t < clip_len - 0.05:
            smoothed.append(raw_points[-1])

    log.info("Reframe path: %d keyframes over %.1fs", len(smoothed), clip_len)
    return smoothed


def flat_center_path(duration: float) -> list[PanPoint]:
    return [PanPoint(0.0, 0.5, 0.5), PanPoint(max(duration, 0.01), 0.5, 0.5)]
