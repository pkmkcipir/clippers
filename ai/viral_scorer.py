"""Heuristic highlight ("viral") scoring.

Important honesty note: this is a rule-based scorer combining several
cheap signals, not a deep model trained on millions of videos' worth of
engagement data (which is what commercial "viral score" products use).
It's a genuinely useful first pass -- audio energy spikes really do
correlate with laughter/excitement, hook phrases really do correlate
with strong openers -- but treat the 0-100 number as a ranking aid for
choosing which candidate clips to review first, not ground truth.

Signals blended (each normalized to 0-1, then weighted):
  - audio_energy   : RMS loudness spike relative to the video's own baseline
  - keyword_density : hook/emotional words per second in the transcript
  - scene_activity  : proximity to a detected scene cut (topic changes)
  - face_signal     : presence of a face, bonus if smiling
  - sentence_shape  : is it a complete, well-formed sentence/question
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ai.transcription import Sentence
from ai.scene_detection import SceneEvent, FaceWindow
from utils.logger import get_logger

log = get_logger("viral_scorer")

# Generic hook/engagement words, Indonesian + English. Intentionally broad
# and generic (not tied to any platform's private ranking signals).
HOOK_WORDS = {
    "id": [
        "gila", "ternyata", "rahasia", "jangan sampai", "bahaya", "viral",
        "gokil", "wow", "parah", "sumpah", "beneran", "shock", "kaget",
        "nangis", "ketawa", "gila sih", "penting banget", "wajib tau",
    ],
    "en": [
        "secret", "never", "insane", "crazy", "unbelievable", "shocking",
        "wow", "honestly", "literally", "you won't believe", "wait for it",
        "mind blown", "the truth is", "nobody tells you",
    ],
}


@dataclass
class ScoredWindow:
    start: float
    end: float
    viral_score: float       # 0-100
    confidence_score: float  # 0-100, agreement across signals
    signal_breakdown: dict


def _keyword_density(text: str, language: str) -> float:
    words = HOOK_WORDS.get(language, []) + HOOK_WORDS["en"]
    text_lower = text.lower()
    hits = sum(text_lower.count(w) for w in words)
    duration_words = max(len(text_lower.split()), 1)
    return min(hits / duration_words * 5.0, 1.0)  # scale so 1-2 hits already registers


def _audio_energy_for_window(waveform: np.ndarray, sample_rate: int, start: float, end: float,
                              baseline_rms: float) -> float:
    lo, hi = int(start * sample_rate), int(end * sample_rate)
    segment = waveform[max(lo, 0):min(hi, len(waveform))]
    if segment.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(segment))))
    if baseline_rms <= 1e-6:
        return 0.0
    return float(np.clip((rms / baseline_rms - 1.0), 0.0, 2.0) / 2.0)  # normalize ~0-1


def _scene_activity(scene_events: list[SceneEvent], start: float, end: float) -> float:
    if not scene_events:
        return 0.0
    nearby = [e for e in scene_events if start - 1.0 <= e.time_sec <= end + 1.0]
    if not nearby:
        return 0.0
    return float(np.clip(max(e.intensity for e in nearby), 0.0, 1.0))


def _face_signal(face_windows: list[FaceWindow], start: float, end: float) -> float:
    if not face_windows:
        return 0.0
    nearby = [f for f in face_windows if start <= f.time_sec <= end]
    if not nearby:
        return 0.0
    has_face = any(f.face_count > 0 for f in nearby)
    smiling = any(f.smiling for f in nearby)
    return 1.0 if smiling else (0.5 if has_face else 0.0)


def _sentence_shape(text: str) -> float:
    text = text.strip()
    if not text:
        return 0.0
    score = 0.3
    if text.endswith("?"):
        score += 0.4
    if text.endswith(("!", ".")):
        score += 0.2
    if len(text.split()) >= 4:
        score += 0.1
    return min(score, 1.0)


WEIGHTS = {
    "audio_energy": 0.30,
    "keyword_density": 0.25,
    "scene_activity": 0.20,
    "face_signal": 0.15,
    "sentence_shape": 0.10,
}


def score_window(
    text: str, start: float, end: float, *,
    waveform: np.ndarray | None, sample_rate: int, baseline_rms: float,
    scene_events: list[SceneEvent], face_windows: list[FaceWindow],
    language: str = "id",
) -> ScoredWindow:
    signals = {
        "audio_energy": _audio_energy_for_window(waveform, sample_rate, start, end, baseline_rms)
                         if waveform is not None else 0.0,
        "keyword_density": _keyword_density(text, language),
        "scene_activity": _scene_activity(scene_events, start, end),
        "face_signal": _face_signal(face_windows, start, end),
        "sentence_shape": _sentence_shape(text),
    }
    weighted = sum(signals[k] * WEIGHTS[k] for k in WEIGHTS)
    viral_score = round(weighted * 100, 1)

    # Confidence = how many signals meaningfully fired (agreement), not just one spike.
    active = sum(1 for v in signals.values() if v >= 0.3)
    confidence_score = round((active / len(signals)) * 100, 1)

    return ScoredWindow(start=start, end=end, viral_score=viral_score,
                         confidence_score=confidence_score, signal_breakdown=signals)


def compute_baseline_rms(waveform: np.ndarray) -> float:
    if waveform is None or waveform.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(waveform))))
