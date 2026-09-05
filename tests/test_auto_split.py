"""Tests for ai/auto_split.py + ai/viral_scorer.py -- the core "never cut
mid-sentence, prefer high-signal moments" logic. Run with:
pytest tests/test_auto_split.py
"""
import numpy as np
import pytest

from ai.transcription import Sentence
from ai.auto_split import auto_split, DURATION_BUCKETS
from ai.viral_scorer import compute_baseline_rms


def _build_sentences(lines: list[str], gap: float = 0.4) -> list[Sentence]:
    sentences, t = [], 0.0
    for line in lines:
        dur = max(len(line.split()) * 0.45, 2.0)
        sentences.append(Sentence(text=line, start=t, end=t + dur))
        t += dur + gap
    return sentences


LINES = [
    "Halo semua, hari ini kita bahas sesuatu yang gila.",
    "Kalian tau gak sih ternyata ini rahasia yang jarang dibahas?",
    "Jadi ceritanya begini.",
    "Aku dulu juga gak percaya sama sekali.",
    "Tapi setelah dicoba, hasilnya bikin kaget parah.",
    "Nah ini bagian pentingnya, jangan sampai kelewat.",
    "Oke lanjut ke topik berikutnya ya.",
    "Ini agak teknis tapi tetap penting.",
    "Sekian dulu untuk hari ini, sampai jumpa.",
]


def _fake_waveform(total_duration: float, spike_range: tuple[float, float] | None = None, seed: int = 0):
    sr = 16000
    rng = np.random.default_rng(seed)
    waveform = rng.normal(0, 0.02, int(total_duration * sr) + sr).astype(np.float32)
    if spike_range:
        lo, hi = int(spike_range[0] * sr), int(spike_range[1] * sr)
        waveform[lo:hi] += rng.normal(0, 0.25, hi - lo).astype(np.float32)
    return waveform, sr


@pytest.mark.parametrize("bucket", [15, 30, 45])
def test_clips_respect_duration_bucket_and_sentence_boundaries(bucket):
    sentences = _build_sentences(LINES)
    waveform, sr = _fake_waveform(sentences[-1].end)
    baseline = compute_baseline_rms(waveform)

    clips = auto_split(
        sentences, duration_bucket=bucket, max_clips=5,
        scorer_kwargs=dict(waveform=waveform, sample_rate=sr, baseline_rms=baseline,
                            scene_events=[], face_windows=[], language="id"),
    )

    lo, hi = DURATION_BUCKETS[bucket]
    sentence_starts = {round(s.start, 6) for s in sentences}
    sentence_ends = {round(s.end, 6) for s in sentences}

    for clip in clips:
        assert lo - 0.01 <= clip.duration <= hi + 0.01
        assert round(clip.start, 6) in sentence_starts, "clip must start exactly on a sentence boundary"
        assert round(clip.end, 6) in sentence_ends, "clip must end exactly on a sentence boundary"


def test_clips_never_overlap():
    sentences = _build_sentences(LINES)
    waveform, sr = _fake_waveform(sentences[-1].end)
    baseline = compute_baseline_rms(waveform)

    clips = auto_split(
        sentences, duration_bucket=15, max_clips=10,
        scorer_kwargs=dict(waveform=waveform, sample_rate=sr, baseline_rms=baseline,
                            scene_events=[], face_windows=[], language="id"),
    )
    ordered = sorted(clips, key=lambda c: c.start)
    for a, b in zip(ordered, ordered[1:]):
        assert a.end <= b.start + 1e-6


def test_loud_segment_scores_higher_than_quiet_segment():
    """The sentence pair around a synthetic audio spike should outscore
    an otherwise-similar quiet pair -- i.e. the scorer is actually
    responsive to the audio-energy signal, not just returning noise."""
    sentences = _build_sentences(LINES)
    spike_sentence = sentences[4]  # "...bikin kaget parah."
    waveform, sr = _fake_waveform(sentences[-1].end, spike_range=(sentences[3].start, spike_sentence.end))
    baseline = compute_baseline_rms(waveform)

    clips = auto_split(
        sentences, duration_bucket=15, max_clips=10,
        scorer_kwargs=dict(waveform=waveform, sample_rate=sr, baseline_rms=baseline,
                            scene_events=[], face_windows=[], language="id"),
    )
    assert len(clips) >= 2

    loud_clip = max(clips, key=lambda c: c.start <= spike_sentence.start < c.end)
    quietest_clip = min(clips, key=lambda c: c.viral_score)
    top_clip = max(clips, key=lambda c: c.viral_score)

    # The top-ranked clip should be the one overlapping the loud spike.
    assert top_clip.start <= spike_sentence.start < top_clip.end
    assert top_clip.viral_score > quietest_clip.viral_score


def test_empty_transcript_returns_no_clips():
    assert auto_split([], duration_bucket=30) == []


def test_custom_duration_range():
    sentences = _build_sentences(LINES)
    waveform, sr = _fake_waveform(sentences[-1].end)
    baseline = compute_baseline_rms(waveform)

    clips = auto_split(
        sentences, duration_bucket=0, custom_range=(5, 12), max_clips=5,
        scorer_kwargs=dict(waveform=waveform, sample_rate=sr, baseline_rms=baseline,
                            scene_events=[], face_windows=[], language="id"),
    )
    for clip in clips:
        assert 4.99 <= clip.duration <= 12.01
