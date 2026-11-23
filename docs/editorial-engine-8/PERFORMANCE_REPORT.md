# Phase 10 — Measured Performance Engineering Report

Status: `TECHNICALLY_TESTED`

## Hardware & Runtime Environment
- **GPU**: NVIDIA GeForce RTX 3050 Laptop GPU (4096 MiB total VRAM, 3962 MiB free idle)
- **System RAM**: ~15.2 GiB total physical memory (Windows 11)
- **Local Ollama**: `0.40.16` with `qwen2.5:3b` (text LLM, 1.9 GB) and `qwen3-vl:2b-instruct` (vision LLM, 1.9 GB total, Apache 2.0)

## Baseline vs Factory 8.0 Measured Performance (Full 617.906 s GTA Source)

| Stage / Metric | Baseline (`aef60f1`) | Factory 8.0 (`qwen3-vl:2b-instruct` + CV Event Graph) | Notes |
|---|---:|---:|---|
| Coarse / Source Intelligence Extraction | 21.62 s (309 seeks @ 2.0 s step) | **19.61 s** (single sequential PyAV pass @ 0.5 s step, 4× denser, 101 shots + 1236 audio events + HUD/facecam/text-band cues) | Zero repeated GOP seeks; cached in `source-evidence-v1.json` |
| Independent Planning + Semantic Verification (cold) | 171.52 s (32 seeks × 3, no VLM) | **84.05 s** (strict 35–60 s incl. VLM edge + storyboard checks & LLM pairwise) | **2.04× faster** while adding true VLM visual understanding |
| Re-planning with Different Duration Bounds (warm cache) | Full re-run | **76.30 s** (reused `source-evidence-v1.json` + **9 VLM cache hits**, 0 redundant ASR) | Zero video re-decoding or ASR recomputation |
| Connected Story Search | 314.10 s | **193.98 s** (reused event graph + format-break guard) | 0/3 honestly reported when no single 99–174 s coherent event exists |
| VLM Calls & Latency (total across 3 plans) | 0 (text-only) | **8 cold calls** + **9 cache hits** (`41.00 s` total VLM time, ~3.05 s median warm, `0` failures) | Single-composite-grid JPEG keeps model 100% on GPU |
| Peak GPU VRAM (measured via `nvidia-smi`) | Not measured | **2673–2736 MiB** out of 4096 MiB (**>1.3 GiB headroom**) | **0 OOMs**; serialized via `HEAVY_LOCK`; unloaded via `keep_alive=0` |
| Host Available RAM (measured post-run) | Not measured | **3990 MiB available** (peak process bounded; 160×90 frame descriptors discarded immediately) | No uncontrolled memory growth |
| Render Time (3 × 1080×1920 `libx264` MP4s + storyboards) | Not totaled | **498.72 s** total for 3 clips (109.7 s of 1080×1920 30 fps `source_fit_blur` + blurred extension + drawtext + ASS + loudnorm) | CPU `libx264` default (`FFMPEG_ENCODER=x264`); `h264_nvenc` optional for faster drafts |

## Cache Reuse & Invalidations Verified
- Changing hook text or regenerating metadata does **not** rerun ASR or source intelligence.
- Changing duration/ranking parameters reuses `source-evidence-v1.json` (keyed by `source_fingerprint`, `start`, `end`, `schema_version=1`) and `vlm-cache.json` (keyed by `model`, `prompt_version`, `operation`, `sha256(image + context)`).
- Changing source file or bounds automatically invalidates stale caches cleanly.
