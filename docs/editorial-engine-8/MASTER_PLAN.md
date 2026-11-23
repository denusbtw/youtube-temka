# AI Content Factory 8.0 — Master Engineering Plan

## Mission
Transform AI Content Factory's automated video clipping from a heuristic activity/duration window picker into an evidence-backed, editorially defensible, locally deployable multimodal highlight discovery and intelligent video editing system on consumer hardware (NVIDIA RTX 3050 4 GB VRAM, 16 GB RAM).

## Baseline & Target Repository State
- Repository: `https://github.com/Eclipse-XS/youtube-temka` (`delivery` remote)
- Active development worktree: `youtube-temka-v7` on branch `feat/factory-quality-rebuild`
- Verified baseline commit: `aef60f14d31e3728b6c3112289915ad69edd73b3` (PR #1)
- Preserved live runtime worktree: `youtube-temka` (`feat/one-command-startup`, untouched)
- Verified baseline test status: `222 passed` in `factory_tests`
- Verified GTA baseline defect set (`yux8s3lwLss`, 617.906 s, SHA-256 `1a66b8c9cdf61a1246bcb262ab9f85f01fe0a4216629fd92e41dec53ecb8aeb8`):
  - Candidate A `554.12–600.26 s`: Coherent truck-swap gameplay event contaminated by the `"PUMBA"` branded end card starting at ~598.5 s.
  - Candidate B `172.98–216.98 s`: Unrelated compilation scenes stitched together (dark car interior → street streamer → daytime ocean boat → nighttime 4-star on-foot gameplay).
  - Candidate C `5.42–52.50 s`: Multiple unrelated clips merged right across the `"PUMBA PRESENTS"` intro title card (~24–28 s) and into aircraft footage.

## Execution Order & Gates
| Phase | Focus | Gate Criteria | Status |
|---|---|---|---|
| Phase 0 | Codebase, Legacy OpenShorts & Upstream Reuse Audit | Verified file/call trace, license compliance matrix, no AGPL copying | IMPLEMENTED |
| Phase 1 | Reproducible Editorial Benchmark (`factory/benchmark.py`) | Schema v2, preserved GTA baseline, metrics + rubric tested | IN_PROGRESS |
| Phase 2 | Unified Source Intelligence (`factory/source_intelligence.py`) | Normalized timeline, sequential shot/audio/HUD/OCR-fallback evidence, zero redundant ASR | NOT_STARTED |
| Phase 3 | Semantic Event Graph (`factory/semantic_events.py`) | Shot graph continuity, format-break detection, event grouping, anti-padding boundary refinement, GTA regression tests | NOT_STARTED |
| Phase 4 | Practical Multimodal Vision (`factory/vision_vlm.py`) | Ollama VLM adapter + CV fallback, empirical 10-case benchmark on RTX 3050 4 GB (VRAM/RAM/latency/accuracy) | NOT_STARTED |
| Phase 5 | Multi-Stage Highlight Discovery & Ranking (`factory/discovery.py`) | Map → Pool → Verify → Score → Pairwise Rank → Select, 12-dim rubric, Independent/Connected/Sequential modes | NOT_STARTED |
| Phase 6 | Professional Intelligent Video Editing (`factory/edit_manifest.py`, `framing.py`, `media.py`) | Versioned EditManifest, source-aware layouts (no forced 65/35 split), scene-aware crops, design presets, hook/caption parity | NOT_STARTED |
| Phase 7 | Contextual Metadata & Hook Intelligence (`metadata.py`, `hooks.py`, `drafts.py`) | Grounded event-specific titles/hooks, manual edit preservation, zero hallucinated claims | NOT_STARTED |
| Phase 8 | Editorial Review UX (`api.py`, `store.py`, React Factory UI) | Evidence timeline, candidate inspection, trim/split/merge/feedback persistence, honest review states | NOT_STARTED |
| Phase 9 | Rights, Policy Warnings & Production Economics | Rights schema, reused-content warnings, processing/approval efficiency tracking | NOT_STARTED |
| Phase 10 | Performance Engineering & Resource Bounds | Bounded GPU/RAM admission, cache reuse, measured performance report | NOT_STARTED |
| Phase 11 | Exhaustive Editorial Acceptance & Full GTA Evaluation | 30+ regression cases, full 618 s GTA reanalysis, rendered MP4s & visual storyboards inspected | NOT_STARTED |
| Phase 12 | Integration, CI & Release Candidate | Full backend/frontend/launcher suites, diff & secret scan, safe push to `feat/factory-quality-rebuild`, release report | NOT_STARTED |

## Mandatory Safety & Non-Negotiable Constraints
1. Automatic public publishing remains disabled (`FACTORY_AUTOMATIC_PUBLISHING=0`).
2. Never upload source media to external inference APIs without explicit opt-in and authorization.
3. Never copy AGPL-3.0 code (`ColinGPT9/clips-studio`, `osbytes/reelsaw`); implement clean-room concepts only.
4. Never hard-code GTA benchmark timestamps into production heuristics.
5. Never conflate technical validity or heuristic scores with human editorial approval or predicted virality.
