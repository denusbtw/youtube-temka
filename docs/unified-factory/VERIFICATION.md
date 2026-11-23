# Integration verification

The two incoming heads, local snapshot and shared ancestor are recorded in
[INTEGRATION_BASELINE.md](INTEGRATION_BASELINE.md). This branch targets
`feat/factory-7.2` and is delivered in draft PR #3.

## Regression commands

The Factory test command in the offline Docker regression run was
`python -m pytest -p no:cacheprovider -q factory_tests` from the checked-out
source. With the new multi-cascade facecam continuity, adaptive 2-pass AI Story
verification, and ASR/CV/VLM checkpoint-interruption tests, the suite reports
**291 passes** (`0` failures). Its shared-module run reports **161 passes**:

```
python -m pytest tests/test_jsonfile.py tests/test_ffmpeg_utils.py tests/test_subtitles.py tests/test_ssrf_guard.py tests/test_ssrf_connect_guard.py tests/test_transcribe_backends.py tests/test_file_locks.py -q
```

Windows script verification (`scripts/check_launcher.ps1`: 4 passed;
`scripts/check_updater.ps1`: 7 passed; `scripts/check_maintenance.ps1`: 4
passed), `python -m compileall -q factory`, `python scripts/check-secrets.py`,
`docker compose -f compose.unified.local.yml config --quiet`, and
`npm run build -- --config vite.factory.config.js` in `dashboard` all succeed.
Browser checks on `http://127.0.0.1:25175/factory.html` exercised `Create`,
`Jobs`, `Editor` (including the new AI Story diagnostics panel and `story_verdict`
badges in `SelectionReview.jsx` as well as `EditPlan.jsx`), `Library`, `Publish`,
and `Settings`.

## Runtime and evidence

The loopback unified deployment uses Docker volumes for isolated SQLite and
scratch data. It preserves the five original history entries, uses the model
cache read-only, and mounts OAuth records read-only. The controller and web
ports are 28082 and 25175. The original staging deployment retains its ports
(18082 and 15175) and volumes untouched.

Drive authorization succeeded. The application verified the exact YouTube
identity for Leonida Mayhem (`UCysXu2xss5DoYoz2RUQKjMQ`) and TheViceScope
(`UCAIKU2V6DDb4GRFkBcX7-Dg`). Both Analytics synchronizations reached READY.
Each of the 21 background MP4s had registered license metadata and returned HTTP
206 to a byte-range request. Publication was off throughout.

### Repaired Smart Highlights (`gta_repaired`)

The earlier GTA Smart Highlights excerpts (`99.0–115.5s` and `120.0–140.5s` in
job `3a6e62ae3dbf4a2d8e24d2ee3b2a6291`) contained abrupt scene jumps across
streamer webcam cuts (`111.0s` and `127.0s`). After introducing multi-cascade
corner webcam detection (`haarcascade_frontalface_default`,
`haarcascade_frontalface_alt2`, `haarcascade_profileface` + horizontal flip at
`1.5s` cadence, cache revision `:v2`) and a hard `facecam_contradiction`
continuity barrier in `factory/semantic_events.py`, the repaired Smart
Highlights run (`job_id = 7781118af0794efab72ddfb3f1669fe5`, exported to
`%LOCALAPPDATA%\Factory89-unified\exports\gta_repaired`) selected two clean,
self-contained 1080x1920 @ 30 fps Shorts with zero unrelated-clip merges:
- `clip-1.mp4`: `95.0–111.0s` (`16.0s`, 480 frames, `-14.4 LUFS`, complete
  parking-lot tiger encounter with consistent two-person couch webcam in
  `bottom_right`, ending before the `111.0s` compilation cut).
- `clip-2.mp4`: `30.5–47.0s` (`16.5s`, 495 frames, `-14.3 LUFS`, continuous
  alligator ambush sequence on the riverbank).

### Real AI-Verified Story Evaluation & Rendered Jobs (`ai_story_p1`, `ai_story_p3`)

Root-cause analysis of the six previously blocked `AI-Verified Story` jobs is
documented in [AI_STORY_ROOT_CAUSE.md](AI_STORY_ROOT_CAUSE.md). With the 5-stage
adaptive verification pipeline in `factory/connected_windows.py` and the
panel-first `story_output_version=3` prompt in `factory/vision_vlm.py` (up to
two bounded 4-frame storyboards per candidate = 8 frames total, peak VRAM
`2667–2673 MiB` on the 4096 MiB RTX 3050 Laptop GPU), the 9-case evaluation on
the two local authorized GTA sources ([AI_STORY_BENCHMARK.md](AI_STORY_BENCHMARK.md))
verified all 4 positive regression cases (`P1_bridge_stunt`,
`P2_bridge_jump_story_721`, `P3_truck_swap_subwindow`,
`P4_airplane_crash_action` as `VERIFIED_COHERENT`) and rejected all 5 negative
cases (`N1_unrelated_clips_28_169` regression + `N2_title_card_crossing`,
`N3_unrelated_scene_change_173_217`, `N4_unfinished_no_outcome`,
`N5_short_event_no_padding` holdout slices/constraints). Two real multi-part
`AI-Verified Story` jobs were rendered end to end and visually inspected via
contact sheets (`%LOCALAPPDATA%\Factory89-unified\exports\contact_sheets\`):
- Job `c8f4a90f77164f8a85b9c3996475bcfa` (`P1_bridge_stunt`, requested
  `412.0–446.0s`, verified story excerpt `413.0–446.0s`, `VERIFIED_COHERENT` @
  `0.98`, exported to `exports\ai_story_p1`): `clip-1.mp4` (`413.0–430.5s`,
  `17.5s`, `-14.2 LUFS`) and `clip-2.mp4` (`428.5–446.0s`, `17.5s`, `2.0s`
  overlap, `-13.3 LUFS`).
- Job `383316f996ca4cf7848d0ea7f0908840` (`P3_truck_swap_subwindow`, requested
  `526.0–598.0s`, verified story excerpt `554.68–598.0s`, `VERIFIED_COHERENT` @
  `0.93`, exported to `exports\ai_story_p3`): `clip-1.mp4` (`554.68–577.84s`,
  `23.17s`, `-14.9 LUFS` after adding `dynaudnorm` pre-leveling in
  `factory/media.py`) and `clip-2.mp4` (`574.84–598.0s`, `23.17s`, `3.0s`
  overlap, `-15.7 LUFS`, cross-part loudness delta `0.8 LU`).

### Chronological Story Split & Multi-Source Longs Compilation (`compilation_longform`)

Chronological splitting covered 28.5–160.0 seconds in three files with 3.0
seconds of overlap between adjacent parts. The first segment's SHA-256 remained
identical after the real Docker lease expired and the job recovered.

In addition to the earlier three-source `67.888s` compilation check
(`exports\compilation_all_three`), a multi-source Longs Compilation job
(`job_id = 0d65a6ed4849425dbcb2e28087db6839`, exported to
`%LOCALAPPDATA%\Factory89-unified\exports\compilation_longform\long.mp4`)
combined the two full GTA sources (`617.91s` + `720.73s`) across **20 coherent,
non-overlapping segments**. Reordering (`seg_02` before `seg_01`), `0.25s`
trimming, fade transitions, and optimistic concurrency (`HTTP 409` on stale
revision 1, approved at `plan_revision = 3`) produced a **`392.622722s`**
(`6m 32.6s`, `11,778` frames) H.264/AAC 1280x720 @ 30 fps master measuring
`-14.8 LUFS` (`-0.2 dBTP`). Because the available non-credit active footage
across the two local sources totals `392.62s` against the `600s` target,
`timeline.json` preserves `"duration_shortfall": true` (demonstrating
multi-source compilation and edit-plan revisioning, while full 10–15 minute
real-media runtime remains pending longer source inputs). Detailed editorial and
release acceptance matrices are recorded in
[EDITORIAL_REVIEW.md](EDITORIAL_REVIEW.md) and
[FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md).
