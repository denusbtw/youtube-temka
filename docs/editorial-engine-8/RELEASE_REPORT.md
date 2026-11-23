# Phase 12 — Release Candidate Report (AI Content Factory 8.0)

Status: `TECHNICALLY_TESTED` (Human Editorial Sign-Off: `PENDING_HUMAN_REVIEW`)

## Release Identity
- **Repository**: `https://github.com/Eclipse-XS/youtube-temka` (`delivery` remote)
- **Feature Branch**: `feat/factory-quality-rebuild`
- **Pull Request**: `https://github.com/Eclipse-XS/youtube-temka/pull/1`
- **Previous Baseline / Rollback Commit**: `aef60f14d31e3728b6c3112289915ad69edd73b3` (and prior `feat/factory-7.2` @ `739dac1`)
- **Automatic Public Publishing**: **DISABLED** (`FACTORY_AUTOMATIC_PUBLISHING=0` in `docker-compose.yml`)
- **Live Production Worktree (`youtube-temka`)**: Preserved untouched on `feat/one-command-startup`

## Architectural Changes Delivered
- **Reused**: `subtitles.py`, `ffmpeg_utils.py`, `transcribe_backends.py`, `gpu_decode.py`, `jsonfile.py`, `security_utils.py`, `ssrf_guard.py`, `file_locks.py`, existing SQLite WAL queue/jobs/library/publisher/analytics infrastructure, `HEAVY_LOCK`, React Factory shell, `scripts/start-factory.ps1`.
- **Newly Created Modules**:
  - `factory/benchmark.py` — Versioned v2 editorial benchmark schema, contamination metrics, and 1–5 human rubric evaluator.
  - `factory/source_intelligence.py` — Unified sequential PyAV source intelligence engine (multi-feature shot detection, sequential audio RMS/burst/ZCR, corner HUD/facecam signatures, optional RapidOCR with deterministic text-band fallback, `source-evidence-v1.json` cache).
  - `factory/semantic_events.py` — Temporal Scene Graph, multi-signal format-break detector (`end_card`, `title_card`, `interstitial`, `death_fail_screen`, `black_screen`, `graphic_stinger`, `menu_or_loading`), shot continuity graph, setup/climax/payoff inference, and strict anti-padding boundary refinement.
  - `factory/vision_vlm.py` — Local Ollama VLM adapter (`qwen3-vl:2b-instruct` default, Apache 2.0) with single-composite-grid 4 GB VRAM optimization, `/api/ps` residency check, content-addressed cache, and honest CV fallback.
  - `factory/discovery.py` — Clean-room Map → Pool → Verify → Score (12-dim category-weighted rubric) → Pairwise Rank (order-swapped position-bias mitigation) → Select.
  - `factory/edit_manifest.py` — Versioned `EditManifest` v1 driving preview & FFmpeg render parity, source-aware layout selection (`source_fit_blur`, `gameplay_facecam`, `two_speaker_split`, `fullscreen_smart_crop`, `gameplay_split`), 5 curated design presets, and safe-area hook/caption placement.
- **Modified Active Pipeline & UI Modules**:
  - `factory/shorts.py` (`plan_v8`), `factory/pipeline.py`, `factory/media.py`, `factory/framing.py` (scene-aware per-shot cut resets + two-speaker detection), `factory/metadata.py` (grounded event-specific titles/tags + clean word-boundary truncation), `factory/drafts.py` (manual edit preservation), `factory/store.py` (additive `editorial_feedback`, `source_rights`, `production_metrics` tables + explicit editorial workflow states), `factory/api.py` (`/api/jobs/{jid}/evidence`, `/api/jobs/{jid}/feedback`, `/api/feedback/summary`, `/api/composition/manifest`, `/api/production/metrics`).
  - `dashboard/src/factory/SelectionReview.jsx`, `dashboard/src/factory/CompositionControls.jsx`, `dashboard/src/factory.css` — Visual shot/event/format-break timeline, evidence/defect badges, split/merge/trim/feedback controls, honest workflow state labels, curated design presets.

## Verification Summary (Release Candidate)
- **Backend `factory_tests` suite**: **254 passed, 0 failed** (full 253-test run caught and fixed `media.py` `shot_cuts` backward compatibility for legacy 2-arg `framing.trajectory` monkeypatches; re-verified 85/85 across all modified Factory 8.0, media, API, metadata, vision, event, intelligence, and benchmark suites + new metadata truncation regression test).
- **Windows retained MIT core suite** (`tests/test_file_locks.py`, `tests/test_transcribe_backends.py::TestHostAsrSlot`, `tests/test_ssrf_guard.py`): **42 passed, 0 failed**.
- **Windows launcher / updater / maintenance** (`check_launcher.ps1`, `check_updater.ps1`, `check_maintenance.ps1`): **15 passed, 0 failed** (4 + 7 + 4).
- **Frontend build** (`dashboard`: `npm run build -- --config vite.factory.config.js`): **PASSED** (8.09 s).
- **Static & security hygiene** (`python -m compileall -q factory`, `python scripts/check-secrets.py`): **PASSED**.
- **Docker Compose validation** (`docker compose --profile controller --profile worker config --quiet` and with `compose.gpu.yml`): **PASSED**.
- **Live preserved deployment** (`http://127.0.0.1:8000/health` and `http://127.0.0.1:5175/factory.html`): **Verified healthy and untouched**.

## Rollback Instructions
1. Stop idle Factory containers (`.\scripts\stop-factory.ps1` or `docker compose down` — never while a job is active).
2. Back up SQLite `jobs.sqlite3` (all Factory 8.0 schema additions are strictly additive `CREATE TABLE IF NOT EXISTS`, so existing jobs/outputs remain intact on rollback).
3. Check out rollback commit `aef60f14d31e3728b6c3112289915ad69edd73b3` on `feat/factory-quality-rebuild` and verify `FACTORY_AUTOMATIC_PUBLISHING=0`.
4. Restart with `.\scripts\start-factory.ps1`.
