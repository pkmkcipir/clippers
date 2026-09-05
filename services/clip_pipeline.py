"""Background orchestration for the AI Clip Generator page.

Runs as a QThread so the GUI never blocks: download (if the source is a
YouTube URL and isn't downloaded yet) -> extract audio -> transcribe ->
scene/face detection -> score + auto-split -> generate captions -> write
Clip rows + thumbnails to the database. Progress is reported via Qt
signals the page connects to.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ai.transcription import Transcriber
from ai.scene_detection import detect_scenes, detect_faces
from ai.viral_scorer import compute_baseline_rms
from ai.auto_split import auto_split
from ai.caption_generator import HeuristicCaptionGenerator, LLMCaptionGenerator
from editor.ffmpeg_utils import extract_audio_file, extract_audio_waveform, generate_thumbnail
from database.db import get_session
from database.models import Clip, SourceVideo
from utils.logger import get_logger

log = get_logger("clip_pipeline")


class ClipGenerationWorker(QThread):
    stage_changed = Signal(str)       # "audio" | "transcribe" | "scenes" | "faces" | "scoring" | "captions" | "done"
    progress = Signal(float)          # 0-100 within the current stage
    clip_ready = Signal(dict)         # emitted per generated clip (dict form, UI-safe)
    finished_ok = Signal(int)         # total clip count
    failed = Signal(str)

    def __init__(self, source_video_id: str, video_path: str, *,
                 duration_bucket: int = 30, max_clips: int = 10,
                 whisper_model_size: str = "base", language: str | None = "id",
                 temp_dir: str = "temp", caption_backend: str = "heuristic",
                 caption_llm_provider: str = "anthropic", caption_llm_api_key: str = "",
                 caption_llm_model: str = ""):
        super().__init__()
        self.source_video_id = source_video_id
        self.video_path = video_path
        self.duration_bucket = duration_bucket
        self.max_clips = max_clips
        self.whisper_model_size = whisper_model_size
        self.language = language
        self.temp_dir = Path(temp_dir)
        self.caption_backend = caption_backend
        self.caption_llm_provider = caption_llm_provider
        self.caption_llm_api_key = caption_llm_api_key
        self.caption_llm_model = caption_llm_model

    def _make_caption_generator(self):
        if self.caption_backend == "llm" and self.caption_llm_api_key:
            return LLMCaptionGenerator(provider=self.caption_llm_provider, api_key=self.caption_llm_api_key,
                                        model=self.caption_llm_model)
        return HeuristicCaptionGenerator()

    def run(self):
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)

            self.stage_changed.emit("audio")
            audio_path = str(self.temp_dir / f"{Path(self.video_path).stem}.wav")
            extract_audio_file(self.video_path, audio_path)
            waveform = extract_audio_waveform(self.video_path)
            baseline_rms = compute_baseline_rms(waveform)
            self.progress.emit(100)

            self.stage_changed.emit("transcribe")
            transcriber = Transcriber(model_size=self.whisper_model_size)
            transcript = transcriber.transcribe(
                audio_path, language=self.language,
                progress_cb=lambda cur, total: self.progress.emit(min(cur / max(total, 1) * 100, 100)),
            )
            transcript.to_json(self.temp_dir / f"{Path(self.video_path).stem}_transcript.json")

            self.stage_changed.emit("scenes")
            scene_events = detect_scenes(self.video_path)
            self.progress.emit(100)

            self.stage_changed.emit("faces")
            face_windows = detect_faces(self.video_path)
            self.progress.emit(100)

            self.stage_changed.emit("scoring")
            candidates = auto_split(
                transcript.sentences,
                duration_bucket=self.duration_bucket,
                max_clips=self.max_clips,
                scorer_kwargs=dict(
                    waveform=waveform, sample_rate=16000, baseline_rms=baseline_rms,
                    scene_events=scene_events, face_windows=face_windows,
                    language=transcript.language or "id",
                ),
            )

            self.stage_changed.emit("captions")
            caption_gen = self._make_caption_generator()
            caption_lang = self.language or transcript.language or "id"
            caption_results = []
            for i, cand in enumerate(candidates):
                caption_results.append(caption_gen.generate(cand.text, language=caption_lang))
                self.progress.emit((i + 1) / max(len(candidates), 1) * 100)

            saved = []
            with get_session() as session:
                for i, (cand, caption) in enumerate(zip(candidates, caption_results)):
                    thumb_path = str(self.temp_dir / f"{self.source_video_id}_{i}.jpg")
                    try:
                        generate_thumbnail(self.video_path, cand.start + cand.duration / 2, thumb_path)
                    except Exception:
                        thumb_path = None

                    clip = Clip(
                        source_video_id=self.source_video_id,
                        start_time=cand.start, end_time=cand.end, duration=cand.duration,
                        viral_score=cand.viral_score, confidence_score=cand.confidence_score,
                        transcript_text=cand.text, thumbnail_path=thumb_path, status="candidate",
                        suggested_title=caption.title, suggested_hashtags=caption.hashtags_str(),
                        suggested_caption=caption.caption, suggested_description=caption.description,
                        suggested_keywords=caption.keywords_str(), caption_source=caption.source,
                    )
                    session.add(clip)
                    session.commit()
                    session.refresh(clip)

                    clip_dict = {
                        "id": clip.id, "start_time": clip.start_time, "end_time": clip.end_time,
                        "duration": clip.duration, "viral_score": clip.viral_score,
                        "confidence_score": clip.confidence_score, "transcript_text": clip.transcript_text,
                        "thumbnail_path": clip.thumbnail_path, "suggested_title": clip.suggested_title,
                        "suggested_hashtags": clip.suggested_hashtags, "suggested_caption": clip.suggested_caption,
                        "suggested_description": clip.suggested_description,
                        "suggested_keywords": clip.suggested_keywords, "caption_source": clip.caption_source,
                    }
                    saved.append(clip_dict)
                    self.clip_ready.emit(clip_dict)

                video = session.get(SourceVideo, self.source_video_id)
                if video:
                    video.status = "ready"
                    video.transcript_path = str(self.temp_dir / f"{Path(self.video_path).stem}_transcript.json")
                    session.commit()

            self.stage_changed.emit("done")
            self.finished_ok.emit(len(saved))

        except Exception as exc:
            log.exception("Clip generation pipeline failed")
            self.failed.emit(str(exc))
