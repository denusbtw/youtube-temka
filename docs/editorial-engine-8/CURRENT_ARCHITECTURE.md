# Current Factory Architecture Audit (Baseline `aef60f1`)

## Active Execution Path
1. **API & Queue** (`factory/api.py`, `factory/store.py`, `factory/worker.py`):
   - FastAPI controller backed by SQLite WAL (`jobs.sqlite3`) with atomic claim leases, heartbeats, stage tracking, metadata version history, media asset library, retention packages, and job visibility.
   - Single cooperative `HEAVY_LOCK` (`factory/heavy.py`) serializes GPU/heavy tasks across threads and processes.
2. **Ingestion & Transcription** (`factory/pipeline.py`, `factory/media.py`, `factory/transcribe.py`):
   - `media.probe` probes local uploads (`PyAV`) or remote URLs (`yt-dlp` under `ssrf_guard`).
   - `pipeline.transcribe_chunks` extracts bounded audio chunks (default 300 s) and runs `faster-whisper` (`cpu`/`int8` default) saving `transcript.json` checkpoints.
3. **Candidate Planning** (`factory/shorts.py`, `factory/highlights.py`, `factory/visual_analysis.py`, `factory/event_ranking.py`):
   - `visual_analysis.sample` performs coarse 48×28 grayscale seeks (~2 s step, 309 samples on 618 s) and marks `cut = motion > 0.22`, plus a separate seek pass for `audio_samples`.
   - `highlights.analyze` slices transcripts into 3500-char windows, asks Ollama (`qwen2.5:3b`) to rank up to 12 heuristic intervals per window, and falls back to `heuristics()`.
   - `shorts.plan` builds visual activity windows via `event_ranking.event_window` (expanding ±7/+9 s around high-motion frames to fit 35–60 s regardless of scene cuts) and ranks speech + visual windows on a 4-factor heuristic (`event_signal`, `boundary`, `context`, `duration_fit`).
   - `visual_analysis.refine_local_plan` probes up to 32 frames per selected candidate (RGB 16-bin histogram delta > 0.8) and ONLY rejects when 4+ seconds of simultaneous near-zero motion (<0.015) and silence (<0.003) precede a cut without speech.
4. **Rendering & Validation** (`factory/media.py`, `factory/framing.py`, `factory/composition.py`):
   - `media.download` extracts each candidate segment.
   - `media.render` applies optional pause removal, generates SRT/ASS via MIT `subtitles.py`, computes shared 65/35 panel geometry via `composition.geometry`, optionally runs CPU Haar face tracking (`framing.trajectory`), burns hook (`drawtext`) + ASS captions, normalizes audio (`LOUDNORM_FILTER` + `alimiter`), encodes H.264/AAC, and validates decoded frames/audio/A-V sync (`validate_output`).
5. **Metadata & Editorial Assessment** (`factory/metadata.py`, `factory/hooks.py`, `factory/editorial.py`):
   - `metadata.Generator` produces either a neutral `contextual_draft` for gaming/confirmed subjects (e.g. `"GTA | Gameplay Highlight"`) or strict extractive transcript quotes via Ollama.
   - `editorial.assess` separates technical validity from editorial issues (`NEEDS_REVIEW` / `READY_FOR_REVIEW` / `REJECTED`).
6. **Frontend** (`dashboard/src/factory.jsx`, `dashboard/src/factory/*.jsx`):
   - React/Vite UI served at `/factory.html` with Create, Jobs, SelectionReview, OutputEditor, CompositionControls, MetadataEditor, AssetLibrary, PublisherPanel, AnalyticsPanel, Settings.

## Root Causes of Editorial Failures Identified in Audit
1. **No semantic event boundary modeling**: `event_window()` expands a single motion anchor to 35–60 s directly across unrelated compilation cuts.
2. **No format-break / card / HUD discontinuity detection**: `discontinuities()` only triggers after ≥4 s of total silence + freeze. Branded title cards (`PUMBA PRESENTS`), interstitials (`A FEW MOMENTS LATER`), and end cards (`PUMBA`) have background music/audio and animated graphics, so `discontinuities()` misses 100% of them.
3. **Disconnected, seek-heavy sampling**: Coarse grayscale sampling, audio sampling, and per-candidate fine sampling repeatedly seek the same GOPs without extracting visual appearance signatures, spatial color layout, HUD/facecam consistency, or on-screen text cues.
4. **No multimodal visual inspection**: Neither local VLM nor structured visual classifiers inspect what is actually happening in each shot or whether Shot A and Shot B belong to the same event.
5. **Single-pass shallow ranking**: Visual activity anchors score ~80/100 solely from raw frame difference + duration fit, beating or tying actual coherent events without contamination penalties or pairwise comparison.
6. **Rigid editing composition**: Horizontal gameplay defaults to 65/35 split-screen when a background is attached (or naive blur/crop otherwise), with no scene-aware crop resets across cuts, no facecam-aware layout, no unified `EditManifest`, and no face/caption-aware hook placement.
7. **Generic gaming metadata**: `contextual_draft` bypasses event evidence entirely and outputs identical generic titles (`"GTA | Gameplay Highlight"`) across all three clips.
8. **Limited editorial review visibility**: `SelectionReview.jsx` has no visual shot/event/format-break timeline, no structured evidence or contamination flags, and no persistent human feedback loop.
