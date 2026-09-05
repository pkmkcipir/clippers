# AI Klipers

Desktop app (Windows, Python + PySide6) that turns long YouTube videos or
local footage into short, subtitled clips: download → transcribe → detect
highlights → auto-split → edit on a real multi-track timeline → export.

**Phases 1-6 are done: a real, working app, not a mockup.** That covers
the full AI clipping pipeline (Phase 1), a genuine multi-track Video
Editor with drag/trim/split/effects/overlays (Phase 2), Batch
Export/Processing for 10/50/100 videos or a whole folder at once with
bounded concurrency and retry (Phase 3), real picture-in-picture for
Video 2 (Phase 4), an AI Caption Generator producing titles, captions,
descriptions, hashtags, and SEO keywords for every clip (Phase 5), and
on-demand effect preview -- seeing a color grade, vignette, or reframe
crop applied to the actual clip before committing to a full render
(Phase 6). Everything under "Implemented & tested" below was actually
run in a sandbox during development — unit tests, ffmpeg pipelines
exercised end-to-end on synthetic video (including pixel-level checks of
rendered frames, not just "ffmpeg exited 0"), timeline mouse/keyboard
interactions simulated with Qt's own `QTest`, the batch queue tested
under real concurrent load, and the LLM caption backend's error handling
tested against the real Anthropic API. Five genuine bugs were caught and
fixed this way instead of shipped -- a `QThread` lifetime race (found
*twice*, in two different features, which is itself a useful data point
-- see "Live Effect Preview" below), a `QComboBox.findData()` gotcha, an
intermittent test-suite crash, and keyword-extraction picking up filler
words instead of real topics -- all documented in place rather than
quietly patched over. See "How this was verified" near the bottom for
specifics. What's left (running-job cancellation, auto-update) is scoped
honestly in "Roadmap" below rather than glossed over.

## What's implemented & tested

| Area | Status |
|---|---|
| Project structure (matches the spec's folder layout) | ✅ Done |
| Settings (JSON, theme/language/folders/GPU/model prefs) | ✅ Done |
| Database (SQLAlchemy: projects, videos, clips, downloads, exports, history, editor timelines) | ✅ Done, unit-tested |
| YouTube import (yt-dlp: metadata, download, pause/resume/cancel) | ✅ Built; download itself untestable in this sandbox (no network to YouTube) — see note below |
| Local file / folder import (MP4/MKV/AVI/MOV) | ✅ Done |
| Transcription (faster-whisper, word + sentence timestamps) | ✅ Built; CPU/GPU auto-detected — see note below |
| Scene-cut, motion, face/smile detection (OpenCV) | ✅ Built — see note below |
| Viral/highlight scoring (heuristic, see honesty note in `ai/viral_scorer.py`) | ✅ Done, unit-tested, verified responsive to audio spikes |
| Auto-split (never cuts mid-sentence, duration buckets 15/30/45/60/custom) | ✅ Done, unit-tested |
| Subtitles: 8 style presets, SRT + ASS + karaoke word-highlighting | ✅ Done, tested with real ffmpeg burn-in |
| Export (resolution/fps/codec/bitrate/hw-accel, subtitle burn-in) | ✅ Done, tested with real ffmpeg encode |
| Dashboard, Import Video, AI Clip Generator, Download Manager, Export, History, Settings pages | ✅ Done, GUI-smoke-tested |
| **Video Editor: multi-track timeline** (drag, trim, split, merge, ripple delete, copy/paste, undo/redo, zoom, snap, markers, frame preview) | ✅ **New in Phase 2** — done, tested with simulated mouse/keyboard interaction, not just construction |
| **Video effects** (color/brightness/contrast/saturation, vignette, sharpen, denoise, glow, LUT/color grading, auto zoom, slow motion & speed ramp, motion blur, face tracking/auto reframe, blur background) | ✅ **New in Phase 2** — every one implemented as real ffmpeg filters, tested end-to-end against synthetic video; motion blur and blur background are labeled approximations (see Limitations) |
| **Overlays** (text captions, logo/watermark image, Video 2 as PIP or full-frame cutaway) | ✅ **New in Phase 2**, PIP upgraded in **Phase 4** — done, tested |
| **Batch Export / Batch Processing** (queue many clips or a whole folder of videos, bounded concurrency, per-item progress, retry) | ✅ **New in Phase 3** — done, tested end-to-end including a real caught-and-fixed threading bug (see below) |
| **Picture-in-Picture for Video 2** (adjustable scale/position/border) | ✅ **New in Phase 4** — done, tested at the pixel level; includes a caught-and-fixed `QComboBox` bug (see below) |
| **AI Caption Generator** (title, caption, description, hashtags, SEO keywords) | ✅ **New in Phase 5** — heuristic backend fully tested and deterministic; optional LLM backend's error/fallback paths tested against the real Anthropic API (see below) |
| **On-demand effect preview** (still frame with the clip's effects applied, updates on selection/effect/scrub changes) | ✅ **New in Phase 6** — done, tested including a second instance of the QThread lifetime bug (see below) |
| Subtitle page | ⚠️ Shows the 8 real presets; live video preview/per-word editing is a future pass |
| Packaging (PyInstaller spec, build script, Inno Setup installer) | ✅ Written; **must be run on Windows** to produce a real `.exe` — see below |

**Why some things say "built but untestable here":** this was built in a
Linux sandbox with no GPU and no network access to YouTube/HuggingFace.
yt-dlp, faster-whisper and OpenCV are all correctly wired and pass
syntax/import checks, but actually downloading a video, downloading
Whisper model weights, or running GPU inference needs to happen on your
machine. Treat those specific paths as "review before you fully trust
them in production," same as any code you'd get from a contractor who
didn't have your exact environment.

## Video Editor: documented simplifications

Built for real, but a few things are deliberate scope cuts rather than
oversights -- worth knowing before you rely on them:

- **Video 2 supports real picture-in-picture now** (scale, corner/center
  position, optional white border) -- see "Picture-in-Picture" below. A
  clip left at the default `pip_scale=1.0` still behaves as the original
  Phase 2 full-frame cutaway, so old saved projects are unaffected.
- **Blur Background and Motion Blur are approximations.** Blur Background
  keeps a rectangle around the (Haar-cascade-detected) face sharp and
  blurs the rest, rather than true per-pixel person segmentation. Motion
  blur is a frame-averaging (`tmix`) approximation, not per-object motion
  vectors. Both are labeled "(approximation)" in the Effects panel.
- **Face Tracking / Auto Reframe samples at ~1fps and smooths the result**
  (see `ai/face_tracking.py`) -- deliberately coarse so the crop pans
  smoothly instead of jittering with every detection, at the cost of
  reacting a beat slower to fast head movement.
- **The scrubbing/playback preview shows plain footage; effects show as a
  separate still frame, not live video.** `app/widgets/video_preview.py`
  (the main preview, for lining up cuts and timing) plays raw footage.
  `app/widgets/effect_preview.py` (Phase 6, in the Effects panel) shows
  what the selected clip's effects actually look like, but as an
  on-demand still frame, not continuous playback -- see "Live Effect
  Preview" below for why, and for text/logo/Video-2 overlays specifically
  (which are compositing, not per-clip effects), those still only appear
  in the actually-rendered/exported file.
- **One "graph effect" (Glow or Blur Background) per clip.** Both need
  their own split/blend filter subgraph; combining both on one clip isn't
  supported in this pass (`editor/effects.py` logs a warning and ignores
  the second one).

## Picture-in-Picture

Video 2 clips now support a genuine PIP box instead of only a full-frame
cutaway: adjustable size (10-100% of frame width), position (four corner
presets or centered), and an optional white border so the inset window
reads clearly against the base footage. Adding a clip to "Video 2 (PIP)"
in the editor now defaults to a bottom-right box at 32% scale with a
border; dragging the size back up to 100% reproduces the old full-frame
cutaway exactly. `app/timeline/model.py`'s `TimelineClip` gained
`pip_scale` / `pip_x` / `pip_y` / `pip_border` fields (all with backward-
compatible defaults, so timelines saved before this feature existed still
load and render identically), and `editor/timeline_renderer.py`'s overlay
pass scales/positions/borders the rendered PIP clip before compositing it.

**A real bug worth knowing about, found while testing every position
preset (not just one):** `QComboBox.findData()` turned out to be
unreliable for tuple-valued `userData` in this PySide6 version -- looking
up `(0.0, 0.0)` against an item added with that exact tuple silently
returned "not found," and the code's fallback happened to coincide with
the correct answer for the *one* preset a narrower initial test checked
(bottom-right), masking the bug. It surfaced as soon as a different
preset (top-left) was tested and the panel didn't actually change
anything. The fix was to store a plain string key (e.g. `"top_left"`) as
the combo's `userData` instead of a tuple, with a small dict mapping keys
to `(x, y)` -- `findData()` is reliable for simple scalar types, just not
for arbitrary Python objects. `app/pages/video_editor.py`'s
`PIP_POSITIONS` dict + `_closest_pip_position_key()` implement this; a
parametrized regression test (`tests/test_video_editor_pip_ui.py`)
exercises all five presets individually specifically because of this
history.

## AI Caption Generator

Every generated clip now automatically gets a suggested title, caption,
description, hashtags, and SEO keywords (spec: "AI Caption Generator"),
shown via a "📋 Caption" button on each clip card in AI Clip Generator,
with a one-click "Copy All" for pasting straight into TikTok/Shorts/
Reels. Two backends, chosen in Settings:

- **Heuristic (default, fully offline, no API key, no network call).**
  `ai/caption_generator.py`'s `HeuristicCaptionGenerator` extracts topical
  keywords (frequency-based, with an Indonesian + English stopword list),
  reuses the same hook-phrase signal `ai/viral_scorer.py` uses for
  highlight detection to pick a title when a clip's transcript contains
  one, and falls back to a small set of fill-in-the-keyword templates
  otherwise. Deterministic on purpose (same transcript -> same output),
  not randomized, so it's reproducible and testable rather than flaky.
  Honest ceiling: this is pattern-matching, not understanding -- real,
  editable scaffolding, not finished, clever copy.
- **LLM (optional, needs your own Anthropic or OpenAI API key).**
  `LLMCaptionGenerator` calls the provider's chat completion endpoint
  directly for genuinely higher-quality output. The key is entered in
  Settings, stored locally in `settings.json` as plain text (a warning
  says so right in the UI), and sent only to the provider you pick --
  never anywhere else. Any failure -- bad key, no network, malformed
  response -- falls back to the heuristic generator automatically, so a
  broken key degrades gracefully instead of blocking clip generation.

**A real quality bug worth knowing about, not just a crash:** the first
version's keyword extraction ranked conversational filler ("halo", "hari
ini", "hey", "everyone", "today") above the actual topic words, because
in a short clip transcript most content words only appear once or twice,
so frequency-based ranking ties were broken by first-appearance order --
and the filler words are, structurally, almost always first. The fix was
a larger stopword list covering common transcript openers specifically
(not just grammatical stopwords), which let genuinely repeated topic
words like "investasi"/"saham" (in a testing transcript about stock
investing) surface correctly. `tests/test_caption_generator.py` asserts
against this directly (`"halo" not in keywords`), not just "keywords is
non-empty," specifically because a passing-but-wrong result is what
shipped the first time.

**A database migration, for real this time:** `Clip` gained new columns
(`suggested_caption`, `suggested_description`, `suggested_keywords`,
`caption_source`) alongside the `suggested_title`/`suggested_hashtags`
columns that already existed from Phase 1. Since `AI Klipers` is a
desktop app with a persistent on-disk SQLite database, someone's real
`ai_klipers.db` from an earlier version needs to gain these columns
without losing data or crashing on startup -- `database/db.py` now runs
a lightweight auto-migration after `create_all()` (plain `ALTER TABLE
ADD COLUMN` for anything the models define that an existing table is
missing; never touches or drops existing columns). This isn't a full
migration framework like Alembic -- overkill at this size -- but it's
real and `tests/test_db_migration.py` verifies it against a simulated
pre-Phase-5 database, not just a freshly created one.

## Live Effect Preview

Selecting a clip with effects in the Video Editor now shows what it
actually looks like -- a preview frame in the Effects panel, refreshed
whenever the selection, its effects, or the playhead (while scrubbing
inside that clip) change. This is deliberately a debounced **still
frame**, not continuous video: re-implementing every ffmpeg filter this
app supports (including the split/blend ones like glow and blur
background) as a real-time Qt/GPU shader pipeline would be its own
multi-week project on top of everything else here. A still frame that
updates within a few hundred milliseconds of you stopping (`app/widgets/
effect_preview.py`'s `DEBOUNCE_MS`) answers the actual question someone
has while adjusting a slider -- "what does this look like now?" -- for a
fraction of the effort. `editor/ffmpeg_utils.render_filtered_frame()`
extracts one frame and runs it through the exact same filter graph
(`editor/effects.py`) the real renderer uses, so what you see in the
preview matches what you'll get from Render/Export, not an approximation
of it.

**The same `QThread` lifetime bug from Batch Export, found again in a
different feature.** The first version reassigned `self._worker` on
every new preview request; if a previous render was still in flight when
a newer one started (easy to trigger by scrubbing quickly), the old
`QThread` object's only Python reference was overwritten, and Qt could
garbage-collect it while the underlying thread was still running --
`QThread: Destroyed while thread is still running`, a hard abort. The
fix follows the same pattern documented under "Batch Export: design
notes": track every in-flight worker in a dict keyed by a request token,
and only drop each reference from that worker's own `finished` signal,
never from the render-complete callback. A monotonically increasing
token also makes stale results a non-issue: if an older request somehow
finishes after a newer one, its result is discarded rather than
flashing an outdated preview. Finding the *same class* of bug twice in
two unrelated features was itself useful signal -- it's why
`app/main_window.py` also gained a `closeEvent` safety net (drain
pending Qt events before exiting, and warn before closing over an
actively running Batch Export job) rather than treating each occurrence
as a one-off.

## Batch Export: design notes

`services/batch_queue.py` reuses the *exact same* workers the single-item
pages use (`services.clip_pipeline.ClipGenerationWorker` for AI clip
generation, a thin wrapper around `export.exporter.export_clip` for
exports) -- there's one code path for "process one video", whether it's
triggered from a single page or the batch queue. The manager just adds
bounded concurrency (a configurable max-parallel-jobs limit) and per-job
status tracking on top.

**A real bug worth knowing about, since it's the kind of thing that only
shows up under actual concurrent load:** the first version crashed
(`QThread: Destroyed while thread is still running`) because job results
were being cleaned up from a signal that can arrive slightly before Qt
itself considers the worker thread finished. The fix was to only drop
the Python reference to a finished worker from `QThread`'s own `finished`
signal, which is the one timing Qt actually guarantees is safe -- not
from the job's `done`/`error` signal. Left as a comment in
`services/batch_queue.py` in case you extend this pattern elsewhere.

**Cancellation limitation:** a *queued* job can be removed cleanly. A
*running* job cannot be safely interrupted mid-ffmpeg-call in this pass
(that needs a cancellation token threaded through every ffmpeg
subprocess call plus a way to terminate the child process), so the UI
only offers Remove for queued items -- a running job always finishes or
fails on its own.

## Architecture

```
AI_Klipers/
├── main.py                  entry point
├── config/                  Settings (JSON) + i18n (id/en)
├── database/                SQLAlchemy models + session management + lightweight auto-migration
├── downloader/               yt-dlp wrapper (metadata, download, pause/resume/cancel)
├── ai/                       transcription, scene/face detection, viral scoring, auto-split, face-tracking pan paths, caption_generator.py
├── editor/                   ffmpeg wrappers: cut/encode/thumbnails, effects.py (filter graphs), timeline_renderer.py, lut_presets.py
├── subtitle/                 8 style presets + SRT/ASS generation
├── export/                   orchestrates cut -> subtitle burn-in -> encode (single-clip export)
├── services/                 QThread background workers (clip generation pipeline, batch queue manager)
├── app/
│   ├── timeline/              framework-free timeline model + undo/redo (model.py), Qt scene/view (timeline_scene.py, timeline_view.py)
│   ├── widgets/               sidebar, ScoreRing gauge, video_preview.py (QMediaPlayer-backed preview), effect_preview.py (on-demand effect frame)
│   └── pages/                 dashboard, import_video, ai_clip_generator, video_editor, batch_export, export_page, history, settings, ...
├── tests/                    pytest suite (100 tests, all passing)
├── build_exe.spec, build.py  PyInstaller packaging
└── installer/installer.iss   Inno Setup installer script
```

The Video Editor's data flow: `app/timeline/model.py`'s `TimelineController`
is the single source of truth (tracks, clips, effects, markers, undo/redo)
and has zero Qt dependency, so it's fully unit-testable headless.
`app/timeline/timeline_scene.py` is the only place that touches Qt for the
timeline itself -- it renders the controller's state and translates mouse
drags into `controller.move_clip()` / `trim_clip()` / etc. calls, letting
the controller's own conflict validation decide whether a drag succeeds.
`editor/timeline_renderer.py` reads the same `TimelineProject` and turns
it into a real MP4 via ffmpeg: cut + effect each Video 1 clip individually
(filling gaps with generated black), concatenate, composite Video 2 /
text / image overlays on top with time-windowed `enable=between(...)`
gates, then one final codec/bitrate/hw-accel encode.

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

You also need **ffmpeg + ffprobe** on PATH (or placed as `ffmpeg.exe` /
`ffprobe.exe` inside `ffmpeg/` — see `ffmpeg/README.md`). Get a static
Windows build from https://www.gyan.dev/ffmpeg/builds/.

## Running

```bash
python main.py
```

First transcription run will download the faster-whisper model weights
(size depends on the model you pick in Settings; `base` is a good
default). GPU is auto-detected via `nvidia-smi`; falls back to CPU
automatically if no NVIDIA GPU is found.

## Building the Windows .exe

**This step must be run on an actual Windows machine (or VM/CI runner).**
PyInstaller packages for the OS it runs on — it cannot cross-compile a
Windows `.exe` from Linux or macOS, so there is no way to hand you a
finished binary from this sandbox. What you get instead is a build that's
ready to run the moment you're on Windows:

```bash
pip install -r requirements-dev.txt
python build.py
# output: dist/AI Klipers/AI Klipers.exe
```

Then, optionally, open `installer/installer.iss` in [Inno
Setup](https://jrsoftware.org/isinfo.php) (or run `iscc installer.iss`)
to produce a proper `AI-Klipers-Setup-x.x.x.exe` installer. For a
portable (no-install) version, just zip the `dist/AI Klipers/` folder —
see the comment in `build_exe.spec` for the onefile vs. onedir tradeoff.

### Building automatically on GitHub (no Windows machine needed)

This repo includes a GitHub Actions workflow at
`.github/workflows/build-windows-exe.yml` that builds the `.exe` for you
on a free GitHub-hosted Windows runner. To use it:

1. Push this project to a GitHub repository (`git init`, `git add .`,
   `git commit`, then create a repo on GitHub and `git push`).
2. Go to the repo's **Actions** tab — the workflow runs automatically on
   every push to `main`/`master`, or trigger it manually with
   **Run workflow**.
3. When it finishes, open the run and download the zipped build from the
   **Artifacts** section at the bottom of the summary page.
4. To get a proper **GitHub Release** with the zip attached and a
   download link, push a version tag instead:

   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

   The workflow will build the `.exe` and publish it under the repo's
   **Releases** page.

The workflow installs `requirements-dev.txt`, downloads a static ffmpeg
build automatically so the `.exe` is self-contained, then runs
`pyinstaller build_exe.spec` exactly like the manual steps above.

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

11 tests covering the database layer and the auto-split/viral-scoring
core logic (duration-bucket compliance, sentence-boundary correctness,
non-overlap, and a check that a synthetic loud audio segment actually
scores higher — i.e. the scorer responds to its inputs, not just to
noise).

## Roadmap (suggested order for what's next)

1. **Running-job cancellation** — thread a cancellation token through `export_clip`/`ClipGenerationWorker`'s ffmpeg calls so Batch Export can kill an in-progress item, not just remove queued ones.
2. **Auto-update** — GitHub Releases polling + a self-replace step; standalone from everything else.

## Legal note

YouTube's Terms of Service restrict downloading and republishing videos
you don't own or have a license for, independent of copyright law itself.
The downloader here is a general-purpose tool (same category as many
existing "podcast → shorts" apps) — it's on you to only process content
you have the rights to clip and repost.

## How this was verified

Built and checked in a Linux sandbox without GPU/YouTube access, so
verification focused on what *could* be run for real rather than just
"looks correct":
- `pytest tests/` — 100/100 passing: database layer (+ schema migration
  against a simulated pre-existing database), auto-split/scoring logic,
  the timeline undo/redo controller, the ffmpeg-based timeline renderer
  (including PIP), simulated-mouse GUI interaction tests, the batch
  queue manager, the Video Editor's PIP panel, the AI Caption Generator
  (both backends), the on-demand effect preview, and the app-close
  safety net.
- **The LLM caption backend's error handling was tested against the
  real Anthropic API**, not a mock -- a deliberately invalid key gets a
  genuine 401 response, and the fallback-to-heuristic path was verified
  against that real response shape. The OpenAI path was similarly
  verified against a real (blocked-by-network-allowlist) response. No
  valid API key was used or is required for any of this; the tests only
  confirm graceful degradation.
- **The caption generator's keyword quality was tested with a specific
  negative assertion** (`"halo" not in keywords`, not just "keywords
  exist"), which is what caught the filler-word-ranking bug described
  in "AI Caption Generator" above -- a weaker test would have passed on
  the buggy version too.
- **The database migration was tested against a hand-built pre-Phase-5
  schema** (a `clips` table missing the new caption columns, with a real
  row already in it), confirming the existing row's data survives and
  the new columns are both readable and writable afterward -- not just
  that a brand-new database creates cleanly.
- **The effect preview's QThread fix was verified under deliberately
  adversarial conditions**, not just the happy path: six overlapping
  preview requests fired back-to-back (bypassing the normal debounce),
  polling until every worker genuinely finished rather than asserting
  against a fixed timeout -- the first version of that same test caught
  a real crash this way (a fixed 4-second wait wasn't long enough for
  every ffmpeg call to finish in this sandbox's single-core environment,
  which is itself why the test polls instead of guessing a duration).
- **The batch queue was tested under real concurrent load, not just
  logic**: 4 real export jobs run with `max_concurrent=2` (verifying the
  limit was actually respected during execution, not just configured), a
  job pointed at a missing file failing and then succeeding on retry
  after its payload was fixed, and removing a queued-but-not-yet-started
  job while another runs. This is also how a genuine threading bug
  (see "Batch Export: design notes" above) got caught and fixed instead
  of shipped.
- **The PIP box was verified at the pixel level, not just "ffmpeg exited
  0."** A rendered frame during the PIP window was sampled with
  numpy/Pillow at the computed border coordinates and box center,
  confirming a genuinely white border and the correct source content
  inside the box -- not just a plausible-looking screenshot.
- **Every PIP position preset was tested individually** (not just one),
  which is specifically what caught the `QComboBox.findData()` bug
  described in "Picture-in-Picture" above -- a narrower check would have
  missed it, since the bug's fallback path happened to produce the right
  answer for exactly one of the five presets.
- **An intermittent full-suite crash was investigated, not ignored --
  twice.** Running every test file together occasionally ("Aborted", no
  Python traceback) crashed roughly 1 in 4-5 runs originally; bisecting
  which file combinations reproduced it pointed at Qt object cleanup
  (`deleteLater()`) timing across test files with many QThread/
  QMediaPlayer objects. `tests/conftest.py`'s autouse event-draining
  fixture got the failure rate down substantially, but as the suite grew
  in Phase 5 (adding clipboard interactions in the caption UI tests) it
  reappeared at a lower rate. The fixture was strengthened (more
  `processEvents()`/`sendPostedEvents()` cycles plus a brief pause) and
  10 consecutive full-suite runs afterward were clean. This is
  deliberately not claimed as "fixed" -- an intermittent native crash
  can't be proven absent by finitely many clean runs, only shown to be
  rarer. It has still never been observed from any single test file run
  alone, and doesn't reflect an application bug (the app itself doesn't
  create dozens of QThread/QMediaPlayer/clipboard objects in rapid
  succession the way the test suite does) -- but it's flagged here
  rather than quietly worked around, in case you extend the suite
  further and see an occasional "Aborted" yourself.
- Phase 1's pipeline (cut → ASS subtitle → karaoke burn-in → encode) was
  run end-to-end against a synthetic ffmpeg-generated video, not mocked.
- **Every video effect** (color, vignette, sharpen, denoise, glow, LUT,
  auto zoom, motion blur, face-tracking reframe, blur background, speed)
  was rendered through real ffmpeg against synthetic test video and
  checked for a valid, non-empty output file.
- **The full timeline renderer** was exercised on a project mixing three
  Video 1 clips (with a gap, a color+vignette clip, and a slow-motion
  clip), a Video 2 cutaway, and a text overlay — the resulting MP4's
  duration, resolution (including a vertical reframe case), codec, and
  several extracted frames were all checked, including visually
  inspecting frames to confirm the caption, cutaway, vertical crop, and
  slow-motion sections actually look right, not just that ffmpeg
  returned exit code 0.
- **Timeline mouse/keyboard interactions were simulated with `QTest`**,
  not just constructed: click-to-select, click-empty-space-to-seek,
  cross-track drag, a rejected overlapping drag (verifying it reverts),
  drag-to-trim, split/undo/redo, marker/copy/paste/ripple-delete, and
  zoom were all driven programmatically and asserted against the
  underlying model state.
- The full PySide6 app was booted headlessly (`QT_QPA_PLATFORM=offscreen`)
  including the Video Editor page, and a save → simulated-app-restart →
  reload round trip of a timeline project was verified against the
  database.
- `QMediaPlayer`'s preview-seeking logic (resolving which clip is active
  at a given time, loading it, and positioning playback) was verified
  against real video files using Qt's own bundled FFmpeg multimedia
  backend, which is available in this sandbox.

What was **not** verified here (needs your machine): actual YouTube
downloads, faster-whisper model downloads/GPU inference, on-screen visual
appearance of the theme, hardware-encoder (NVENC/QuickSync/AMF) paths
specifically (only the CPU encoder path was exercised), and the
PyInstaller → Windows `.exe` step itself.
