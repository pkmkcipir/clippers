"""Turns a transcript into ranked, non-overlapping clip candidates.

Hard rule from the spec: never cut mid-sentence. We only ever start/end a
candidate clip on a sentence boundary from the transcript, then keep
whichever combination of window length + start sentence lands closest to
the requested duration bucket.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai.transcription import Sentence
from ai.viral_scorer import ScoredWindow, score_window

# (min_seconds, max_seconds) tolerance band per duration bucket.
DURATION_BUCKETS = {
    15: (10, 20),
    30: (22, 38),
    45: (35, 55),
    60: (50, 75),
}


@dataclass
class ClipCandidate:
    start: float
    end: float
    duration: float
    text: str
    viral_score: float
    confidence_score: float
    signal_breakdown: dict


def _candidate_windows(sentences: list[Sentence], min_dur: float, max_dur: float):
    """Yield (start_idx, end_idx) sentence spans whose total duration falls
    inside [min_dur, max_dur]. O(n * window) but transcripts are at most a
    few thousand sentences, so this stays fast."""
    n = len(sentences)
    for i in range(n):
        start_t = sentences[i].start
        for j in range(i, n):
            end_t = sentences[j].end
            dur = end_t - start_t
            if dur > max_dur:
                break
            if dur >= min_dur:
                yield i, j


def _select_non_overlapping(candidates: list[ClipCandidate], max_clips: int) -> list[ClipCandidate]:
    """Greedy NMS-style selection: take the highest-scoring candidate, drop
    everything that overlaps it, repeat."""
    remaining = sorted(candidates, key=lambda c: c.viral_score, reverse=True)
    chosen: list[ClipCandidate] = []

    while remaining and len(chosen) < max_clips:
        best = remaining.pop(0)
        chosen.append(best)
        remaining = [c for c in remaining if c.end <= best.start or c.start >= best.end]

    return sorted(chosen, key=lambda c: c.start)


def auto_split(
    sentences: list[Sentence],
    *,
    duration_bucket: int = 30,
    custom_range: tuple[float, float] | None = None,
    max_clips: int = 10,
    scorer_kwargs: dict | None = None,
) -> list[ClipCandidate]:
    """
    duration_bucket: 15 / 30 / 45 / 60, or 0 for a custom (min,max) range.
    scorer_kwargs: forwarded to ai.viral_scorer.score_window (waveform,
        sample_rate, baseline_rms, scene_events, face_windows, language).
    """
    if not sentences:
        return []

    if duration_bucket == 0:
        if not custom_range:
            raise ValueError("custom_range=(min,max) is required when duration_bucket=0")
        min_dur, max_dur = custom_range
    else:
        min_dur, max_dur = DURATION_BUCKETS[duration_bucket]

    scorer_kwargs = scorer_kwargs or {}
    candidates: list[ClipCandidate] = []

    for i, j in _candidate_windows(sentences, min_dur, max_dur):
        start, end = sentences[i].start, sentences[j].end
        text = " ".join(s.text for s in sentences[i:j + 1])
        scored: ScoredWindow = score_window(text, start, end, **scorer_kwargs)
        candidates.append(ClipCandidate(
            start=start, end=end, duration=round(end - start, 2), text=text,
            viral_score=scored.viral_score, confidence_score=scored.confidence_score,
            signal_breakdown=scored.signal_breakdown,
        ))

    return _select_non_overlapping(candidates, max_clips)
