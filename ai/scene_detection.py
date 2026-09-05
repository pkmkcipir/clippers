"""Visual analysis via OpenCV: scene-cut detection, motion intensity, and
face/smile presence. These feed ai/viral_scorer.py as signals; none of
them require downloading extra model weights (Haar cascades ship inside
opencv-python itself), which keeps the app usable fully offline.
"""
from __future__ import annotations

from dataclasses import dataclass

from utils.logger import get_logger

log = get_logger("scene_detection")


@dataclass
class SceneEvent:
    time_sec: float
    kind: str  # "cut" | "motion_spike"
    intensity: float  # 0-1


@dataclass
class FaceWindow:
    time_sec: float
    face_count: int
    smiling: bool


def detect_scenes(video_path: str, sample_fps: float = 2.0, cut_threshold: float = 0.45) -> list[SceneEvent]:
    """Detect hard scene cuts via HSV histogram distance between sampled
    frames. cut_threshold is a Bhattacharyya distance (0=identical, 1=totally
    different); ~0.4-0.5 works well for typical talking-head / vlog cuts."""
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        log.warning("Could not open video for scene detection: %s", video_path)
        return []

    native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(int(round(native_fps / sample_fps)), 1)

    events: list[SceneEvent] = []
    prev_hist = None
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % step == 0:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
            cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
            if prev_hist is not None:
                diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)
                if diff >= cut_threshold:
                    events.append(SceneEvent(time_sec=frame_idx / native_fps, kind="cut", intensity=min(diff, 1.0)))
            prev_hist = hist
        frame_idx += 1

    cap.release()
    log.info("Detected %d scene cuts in %s", len(events), video_path)
    return events


def detect_faces(video_path: str, sample_fps: float = 1.0) -> list[FaceWindow]:
    """Sample frames and run Haar-cascade face + smile detection. Smile
    detection is intentionally coarse (Haar cascades are lightweight, not
    a deep model) -- it's a signal to blend with others, not a verdict."""
    import cv2

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    smile_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_smile.xml")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        log.warning("Could not open video for face detection: %s", video_path)
        return []

    native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(int(round(native_fps / sample_fps)), 1)

    windows: list[FaceWindow] = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % step == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(60, 60))
            smiling = False
            for (x, y, w, h) in faces:
                roi = gray[y:y + h, x:x + w]
                smiles = smile_cascade.detectMultiScale(roi, scaleFactor=1.7, minNeighbors=22)
                if len(smiles) > 0:
                    smiling = True
                    break
            windows.append(FaceWindow(time_sec=frame_idx / native_fps, face_count=len(faces), smiling=smiling))
        frame_idx += 1

    cap.release()
    log.info("Sampled %d face windows in %s", len(windows), video_path)
    return windows
