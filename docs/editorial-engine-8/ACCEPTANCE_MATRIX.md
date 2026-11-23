# Factory 8.0 — Editorial & Technical Acceptance Matrix

Status vocabulary: `NOT_STARTED` | `IN_PROGRESS` | `IMPLEMENTED` | `TECHNICALLY_TESTED` | `EDITORIALLY_VERIFIED` | `BLOCKED`

| Criterion | Target | Baseline (`aef60f1`) | Factory 8.0 Measured | Status |
|---|---|---|---|---|
| GTA Regression: Candidate A (`554.12–600.26`) end card removed | 0% end-card contamination | FAILED (PUMBA end card at 598.5–600.26 s) | PASS (`554.12–598.35 s`, 44.23 s; 6-frame storyboard confirms zero PUMBA end card) | `TECHNICALLY_TESTED` |
| GTA Regression: Candidate B (`172.98–216.98`) unrelated scenes separated | 0% multi-clip merge | FAILED (car → street → boat → night foot) | PASS (split cleanly; replaced by coherent `445.5–478.5 s` scooter & `413.0–445.5 s` yellow truck bridge stunt) | `TECHNICALLY_TESTED` |
| GTA Regression: Candidate C (`5.42–52.50`) intro card & unrelated clips separated | 0% title-card contamination | FAILED (`PUMBA PRESENTS` + 5 clips) | PASS (`PUMBA PRESENTS` 26.5–30.5 s detected as `title_card` format break; excluded) | `TECHNICALLY_TESTED` |
| Anti-padding rule on strict 35–60 s band | Never pad short events with unrelated footage | FAILED (padded 20–30 s clips to 44–47 s) | PASS (strict 35–60 s returned 1/3 coherent highlight + clean shorter `review_alternatives`) | `TECHNICALLY_TESTED` |
| Temporal correctness | 100% valid timestamps & durations | PASS | PASS (`0.0` invalid rate; decoded durations 44.23 s, 33.00 s, 32.50 s) | `TECHNICALLY_TESTED` |
| Technical render validity (1080×1920, audio, sync, 0 black frames) | 100% pass | PASS | PASS (all 3 MP4s 1080×1920, `black_frame_fraction=0.0`, `audio_clipped_fraction=0.0`, A/V delta ≤ 12.0 ms) | `TECHNICALLY_TESTED` |
| Semantic grounding (no unsupported claims presented as verified) | 0 unsupported verified claims | Generic titles | PASS (`qwen3-vl:2b-instruct` grounded summaries + transcript; never asserts unconfirmed "GTA 6") | `TECHNICALLY_TESTED` |
| Source-aware layouts (no forced 65/35 split without secondary video) | Adaptive layout per source | FAILED (65/35 bouncing-ball split) | PASS (`source_fit_blur` selected automatically for landscape gameplay; `gameplay_facecam` / `two_speaker_split` / `fullscreen_smart_crop` tested) | `TECHNICALLY_TESTED` |
| 30 mandatory regression test cases | 30/30 passing | Not implemented | PASS (`factory_tests/test_editorial_engine_8.py` — 18 test functions covering all 30 cases) | `TECHNICALLY_TESTED` |
| GPU stability on RTX 3050 4 GB (no OOM) | 0 OOMs under default config | PASS (CPU/text only) | PASS (`2673–2736 MiB` peak VRAM, `0` OOMs, `0` VLM failures, CPU fallback verified) | `TECHNICALLY_TESTED` |
| Cross-source Precision@3 on independently annotated holdout set (15–20 sources) | ≥ 70% | No holdout dataset | External authorized multi-genre sources & independent human annotators not supplied locally | `BLOCKED` |
| Final Human Editorial Approval (≥ 2/3 GTA highlights) | Human reviewer sign-off | 0/3 approved | 3/3 rendered MP4s + 6-frame storyboards + metadata drafts prepared for human reviewer sign-off (`PENDING_HUMAN_REVIEW`) | `IN_PROGRESS` |
