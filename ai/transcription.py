"""Speech-to-text via faster-whisper, producing sentence-level segments
with word-level timestamps (needed by ai/auto_split.py to avoid cutting
mid-sentence, and by subtitle/generator.py for karaoke-style captions).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path

from utils.logger import get_logger
from utils.system_info import detect_whisper_device

log = get_logger("transcription")

_SENTENCE_END = re.compile(r"[.!?]+[\"')\]]*\s*$")


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Sentence:
    text: str
    start: float
    end: float
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    language: str
    sentences: list[Sentence]

    def to_json(self, path: str | Path) -> None:
        data = {
            "language": self.language,
            "sentences": [
                {"text": s.text, "start": s.start, "end": s.end,
                 "words": [asdict(w) for w in s.words]}
                for s in self.sentences
            ],
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path: str | Path) -> "Transcript":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        sentences = [
            Sentence(
                text=s["text"], start=s["start"], end=s["end"],
                words=[Word(**w) for w in s.get("words", [])],
            )
            for s in data["sentences"]
        ]
        return cls(language=data.get("language", "en"), sentences=sentences)


class Transcriber:
    def __init__(self, model_size: str = "base", device: str = "auto", compute_type: str | None = None):
        self.model_size = model_size
        self.device = detect_whisper_device() if device == "auto" else device
        self.compute_type = compute_type or ("float16" if self.device == "cuda" else "int8")
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            log.info("Loading faster-whisper model=%s device=%s compute_type=%s",
                      self.model_size, self.device, self.compute_type)
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        return self._model

    def transcribe(self, audio_path: str | Path, language: str | None = None,
                    progress_cb=None) -> Transcript:
        model = self._load()
        segments, info = model.transcribe(
            str(audio_path), language=language, word_timestamps=True, vad_filter=True,
        )

        sentences: list[Sentence] = []
        cur_words: list[Word] = []
        cur_text_parts: list[str] = []

        for seg in segments:
            seg_words = getattr(seg, "words", None) or []
            if not seg_words:
                # Fallback: no word timestamps returned, treat whole segment as one sentence.
                sentences.append(Sentence(text=seg.text.strip(), start=seg.start, end=seg.end))
                if progress_cb:
                    progress_cb(seg.end, info.duration)
                continue

            for w in seg_words:
                cur_words.append(Word(text=w.word, start=w.start, end=w.end))
                cur_text_parts.append(w.word)
                if _SENTENCE_END.search(w.word.strip()):
                    text = "".join(cur_text_parts).strip()
                    if text:
                        sentences.append(Sentence(
                            text=text, start=cur_words[0].start, end=cur_words[-1].end,
                            words=cur_words,
                        ))
                    cur_words, cur_text_parts = [], []

            if progress_cb:
                progress_cb(seg.end, info.duration)

        # Flush any trailing words that never hit terminal punctuation.
        if cur_words:
            text = "".join(cur_text_parts).strip()
            if text:
                sentences.append(Sentence(
                    text=text, start=cur_words[0].start, end=cur_words[-1].end, words=cur_words,
                ))

        log.info("Transcribed %d sentences (lang=%s)", len(sentences), info.language)
        return Transcript(language=info.language, sentences=sentences)
