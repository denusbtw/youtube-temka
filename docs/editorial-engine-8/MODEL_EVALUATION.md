# Phase 4 — Empirical Multimodal Model Evaluation Report

Status: `TECHNICALLY_TESTED`

## Hardware & Runtime Environment
- **GPU**: NVIDIA GeForce RTX 3050 Laptop GPU (4096 MiB VRAM, 3962 MiB free idle)
- **System RAM**: ~15.2 GiB total (Windows 11)
- **Local Inference Server**: Ollama `0.40.16` (`OLLAMA_NUM_PARALLEL=1`)
- **Benchmark Harness**: `verification/benchmark_vlm.py` on 11 real-frame cases (10 GTA full-source frame sequences covering coherent action, car→boat, title card, title card→aircraft, same-action cuts, similar daytime streets, end card with audio, continuous music, silent gameplay, red interstitial card + 1 talking-head speech video continuity check).

## Key Engineering Discovery on 4 GB VRAM
1. **Multi-image context bloat**: Sending 2–4 separate images with `num_ctx >= 3072` caused Ollama to split `qwen3-vl:2b` across CPU/GPU (`54%/46%`, >60 s/call). Composing 2–4 frames into a single labeled composite JPEG (`compose_frame_grid`, `480×136` or `480×272`) at `num_ctx=2048` keeps the entire model **100% resident on GPU** at **`~2673 MiB` peak VRAM** (~3.0 s median latency, >1.3 GB free VRAM headroom).
2. **VRAM admission self-eviction bug fixed**: Checking `nvidia-smi` free VRAM without inspecting `/api/ps` caused the second request in a batch to misread the model's own 2.6 GiB footprint as low free VRAM and evict it to CPU. Adding `/api/ps` residency detection in `vlm_gpu_layers()` eliminated self-eviction.
3. **Thinking vs Instruct variant**: `qwen3-vl:2b` (`qwen3-vl-thinking` renderer, default `thinking=true`) exhausted `num_predict` tokens in `<think>` reasoning and failed schema output on 9/11 cases (~78.4 s/call). Switching to **`qwen3-vl:2b-instruct`** (1.7B, Apache 2.0, `qwen3-vl-instruct` renderer, zero thinking overhead) achieved **100% schema validity**, **100% format-break recall**, **0 false continuity merges**, and **3.05 s median latency**.

## Empirical Benchmark Matrix (11 Real-Frame Cases, RTX 3050 4 GB)

| Model | License | Disk Size | Peak VRAM (MiB) | Median Latency (s) | Schema Valid | Format-Break Recall | False Continuity Merges | False Discontinuity Splits | Overall Accuracy | Failures |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **`qwen3-vl:2b-instruct`** (Selected Default) | Apache-2.0 | 1.9 GB (1.1 GB weights + 822 MB clip) | **2673 MiB** | **3.046 s** | **100.0%** (11/11) | **100.0%** (3/3) | **0** | 5 (conservative) | **54.5%** (6/11) | 0 |
| `qwen3-vl:2b` (thinking default) | Apache-2.0 | 1.9 GB | 1051 MiB (CPU/GPU split) | 78.437 s | 18.2% (2/11) | 66.7% (2/3) | 0 | 0 (unverified) | 18.2% (2/11) | 0 |
| `moondream:latest` (1.8B) | Apache-2.0 | 1.7 GB | 2175 MiB | 2.063 s | 100.0%* (text parse) | 66.7% (2/3) | 1 | 5 | 36.4% (4/11) | 0 |
| `cv_fallback` (no VLM provider) | MIT | 0 MB | 40 MiB (CPU only) | 0.110 s | N/A (honest `verified=False`) | Deferred to CV graph | 0 | 0 | N/A (CV graph handles independently) | 0 |

*\*Note: `moondream:latest` does not reliably obey strict JSON schema prompts and emits raw text/bbox fragments, requiring lenient text heuristics.*

## Model Selection Policy Applied in Factory 8.0
1. **Default Local VLM**: `qwen3-vl:2b-instruct` via Ollama under `HEAVY_LOCK` (serialized with Whisper, LLM, and FFmpeg; explicitly unloaded via `keep_alive=0` after each batch).
2. **Role Split (Hybrid CV + VLM)**: Because `qwen3-vl:2b-instruct` exhibits **0% false merges** and **100% format-break recall** while being conservative (over-splitting) on side-by-side continuity comparisons of moving vehicles, Factory 8.0 uses:
   - High-confidence CV shot-graph continuity (`>= 0.72` HSV/spatial/HUD match) directly without unnecessary VLM calls;
   - `qwen3-vl:2b-instruct` selectively on **uncertain CV edges (`0.48–0.68`)**, **suspected tail end cards**, and **finalist 4-frame storyboard verification & factual visual event summarization** (adaptive inference budget ≤ 12 calls/source).
3. **Graceful Degradation**: If Ollama/VLM is unavailable or free VRAM < 2200 MiB, Factory 8.0 automatically falls back to CPU (`num_gpu=0`) or deterministic CV Semantic Event Graph (`verified=False`, honest `cv_graph_review_required` label).
4. **No Hidden Cloud Uploads**: External remote VLMs remain strictly disabled unless explicitly opted in.
