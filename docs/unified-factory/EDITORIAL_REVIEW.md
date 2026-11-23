# Editorial & Technical Review of Generated Video Outputs

This document records the frame-level visual inspection, audio loudness audit, and technical verification of all video outputs produced during the unified Factory 8 + Factory 9 integration validation on `integration/factory-8-9-unified`.

---

## 1. Source Footage Fingerprints

| Local Filename / Container Path | SHA-256 | Duration | Resolution | FPS | Audio | Content Profile |
| --- | --- | --- | --- | --- | --- | --- |
| `a90eb6ae10bf426b9e60a14025428dc1.mp4` (`/scratch/uploads/a90eb6ae10bf426b9e60a14025428dc1.mp4`) | `1a66b8c9cdf61a1246bcb262ab9f85f01fe0a4216629fd92e41dec53ecb8aeb8` | `617.91s` (`10m 17.9s`) | `1280x720` | `30.0` | Stereo AAC `44.1 kHz` | GTA compilation (`yux8s3lwLss`) with mixed streamer facecams, animal attacks, jetliner crash, yellow pickup bridge stunt, and white dually truck/RV encounter |
| `qa-gta-721.mp4` (`/scratch/uploads/qa-gta-721.mp4`) | `ccad22b4e0b8d7fc1422f2639ef386ffb7aed28f52247c6a60ef00e254a0c22d` | `720.73s` (`12m 00.7s`) | `1280x720` | `30.0` | Stereo AAC `44.1 kHz` | GTA cinematic gameplay (`RtghspIPQeU`: `0–344s` active convertible escape & bridge jump; `344–720s` dark post-video screen/credits/countdown with music) |

Contact sheets for all inspected exports are saved in `%LOCALAPPDATA%\Factory89-unified\exports\contact_sheets\`.

---

## 2. Repaired Smart Highlights (`gta_repaired` vs Previous `gta`)

### 2.1 Root Cause of Previous Early GTA Highlight Failures

In the earlier baseline run (`job_id = 3a6e62ae3dbf4a2d8e24d2ee3b2a6291`, exported to `%LOCALAPPDATA%\Factory89-unified\exports\gta`), `clip-1.mp4` (`99.0–115.5s`) and `clip-2.mp4` (`120.0–140.5s`) contained abrupt mid-clip scene jumps:
1. **`99.0–115.5s` (`Shot 19 -> Shot 20` cut at `111.0s`)**:
   - `95.0–111.0s` shows a GTA parking-lot tiger encounter with a two-person couch webcam overlay in the `bottom_right` corner (both people turned in side profile).
   - At `111.0s`, the source compilation cuts to an unrelated clip of a red sports car driving away without a webcam (`111.0–120.0s`).
   - Because the single frontal Haar cascade missed the side-profile faces in `Shot 19`, `corner_face_presence["bottom_right"]` was `0.0` on both shots, and the 4-corner averaged HUD L1 similarity (`0.81`) allowed the continuity scorer to merge `Shot 19` and `Shot 20`.
2. **`120.0–140.5s` (`Shot 26 -> Shot 27` cut at `127.0s`)**:
   - `120.0–127.0s` shows a daytime clip with streamer xQc's webcam in `bottom_left`.
   - At `127.0s`, the source compilation cuts to a nighttime police shootout without a webcam (`127.0–140.5s`).
   - Although `bottom_left` face presence dropped from `0.5` to `0.0`, the heuristic score remained inside the uncertain VLM edge-verification band (`0.38 <= score <= 0.72`), and `qwen3-vl:2b-instruct` falsely joined the pair.

### 2.2 Visual & Technical Verification of Repaired Smart Highlights (`job_id = 7781118af0794efab72ddfb3f1669fe5`)

With multi-cascade corner webcam detection (`haarcascade_frontalface_default`, `haarcascade_frontalface_alt2`, and `haarcascade_profileface` + horizontal flip at `1.5s` intervals, cache revision `:v2`) and the hard `facecam_contradiction` continuity barrier in `factory/semantic_events.py`:

| File | Contact Sheet | Absolute Source Window | Duration / Frames | Resolution / FPS | Loudness / Peak | Frame-by-Frame Visual & Caption Inspection |
| --- | --- | --- | --- | --- | --- | --- |
| `gta_repaired\clip-1.mp4` | `contact_sheets\gta_repaired_clip1.jpg` | `95.0s – 111.0s` (relative `66.5–82.5s` from `28.5s` offset) | `16.00s` (`480` frames) | `1080x1920` @ `30 fps` (`h264` / `aac`) | `-14.4 LUFS` / `-1.0 dBTP` | **Visual Continuity Verified**: Samples at `t=1.3s, 3.9s, 6.5s, 9.1s, 11.7s, 14.3s` show the player in a white tank top and cap opening the back of a dark van in a parking lot, a tiger with a blue collar jumping out and chasing the player around the van, and the two-person couch webcam consistently present in `bottom_right` across all 6 frames. Centered gameplay framing over darkened blurred background preserves the full 16:9 action and corner webcam. Synchronized subtitles (`"Also Kiste öffnen"`, `"shit!"`, `"Tiger hinten? Ich"`, `"im Tank gehabt"`) appear cleanly at the lower boundary of the 16:9 frame. Terminates cleanly at `111.0s` with zero frames from the unrelated red sports car clip. |
| `gta_repaired\clip-2.mp4` | `contact_sheets\gta_repaired_clip2.jpg` | `30.5s – 47.0s` (relative `2.0–18.5s` from `28.5s` offset) | `16.50s` (`495` frames) | `1080x1920` @ `30 fps` (`h264` / `aac`) | `-14.3 LUFS` / `-1.0 dBTP` | **Visual Continuity Verified**: Samples at `t=1.3s, 4.0s, 6.7s, 9.4s, 12.1s, 14.8s` show a shirtless character in red shorts approaching an alligator on a muddy riverbank (`t=1.3–6.7s`, on-screen streamer captions `"focus"`, `"I'm speed"`), walking along the bank (`t=9.4s`), and being lunged at and knocked down by the alligator (`t=12.1–14.8s`). Single continuous gameplay shot with zero abrupt cuts. |

---

## 3. Real AI-Verified Story Outputs (`ai_story_p1` & `ai_story_p3`)

Two real multi-part `AI-Verified Story` jobs were processed end-to-end through `qwen3-vl:2b-instruct` verification and FFmpeg vertical rendering (`1080x1920`, `30 fps`).

### 3.1 Job `c8f4a90f77164f8a85b9c3996475bcfa` (`P1_bridge_stunt` — Yellow Pickup Bridge Crash & Water Impact)

- **Source**: `a90eb6ae10bf426b9e60a14025428dc1.mp4` (requested range `412.0s – 446.0s`; verified story excerpt **`413.0s – 446.0s`**, relative `1.0–34.0s`, total story span `33.0s`, after trimming the `black_screen` format break at `412.0–413.0s`)
- **AI Story Verdict**: `VERIFIED_COHERENT` (`confidence = 0.98`, 2-pass 8-frame adaptive inspection, `2` VLM calls, peak VRAM `2671 MiB`)
- **Recorded VLM Observations**:
  - *Setup*: `"Opening setup (1.0s): A yellow pickup truck is driving on a highway, with a red car and a large truck."`
  - *Development*: `"Action progression (1.0s): The red car is involved in a collision with the large truck... Critical action / turning point: A yellow pickup truck is driving on a highway, approaching a red car."`
  - *Payoff*: `"Visible outcome (34.0s): The yellow pickup truck is now in the middle of a lane..."` (with Part 2 visually capturing the bridge rail launch at `435.5s` and splashdown into the river at `438–445s`).
- **Export Directory**: `%LOCALAPPDATA%\Factory89-unified\exports\ai_story_p1`

| Part | File & Contact Sheet | Absolute Source Window | Overlap | Duration / Frames | Loudness / Peak | Frame-by-Frame Visual & Cross-Part Inspection |
| --- | --- | --- | --- | --- | --- | --- |
| Part 1 | `ai_story_p1\clip-1.mp4` (`contact_sheets\ai_story_p1_clip1.jpg`) | **`413.0s – 430.5s`** (relative `1.0–18.5s`) | — | `17.50s` (`525` frames) | `-14.2 LUFS` / `-1.3 dBTP` | **Visual Continuity Verified**: Samples at `t=1.4s, 4.2s, 7.0s, 9.8s, 12.6s, 15.4s` follow a yellow pickup truck (El Camino style) driving along a multi-lane coastal highway, sideswiping a red coupe (`t=4.2–7.0s`), recovering across an intersection (`t=9.8s`), and accelerating up the bridge approach ramp (`t=12.6–15.4s`). Consistent minimap in bottom-left and `[GP SUBS] 227/250` overlay in top-right. |
| Part 2 | `ai_story_p1\clip-2.mp4` (`contact_sheets\ai_story_p1_clip2.jpg`) | **`428.5s – 446.0s`** (relative `16.5–34.0s`) | **`2.0s`** (`428.5–430.5s`) | `17.50s` (`525` frames) | `-13.3 LUFS` / `-1.2 dBTP` | **Visual & Cross-Part Continuity Verified**: Frame 1 (`t=1.4s`, source `429.9s`) matches the exact bridge approach from the end of Part 1 (`2.0s` recap overlap). At `t=4.2s` (`432.7s`), the yellow pickup climbs the bridge span; at `t=7.0s` (`435.5s`), a maroon car rams it onto the right bridge railing; at `t=9.8s` (`438.3s`), the yellow pickup tumbles vertically off the bridge toward the water; at `t=12.6–15.4s` (`441.1–443.9s`), it splashes into the river and floats overturned by the wooden pier (`"Engine damaged"`). Cross-part loudness delta is `0.9 LU` (`-14.2` vs `-13.3 LUFS`). |

### 3.2 Job `383316f996ca4cf7848d0ea7f0908840` (`P3_truck_swap_subwindow` — White Dually Pickup & Camper Van Encounter)

- **Source**: `a90eb6ae10bf426b9e60a14025428dc1.mp4` (requested range `526.0s – 598.0s`; verified story excerpt **`554.68s – 598.0s`**, relative `28.68–72.0s`, total story span `43.32s`, cleanly stopping before the `PUMBA` end card at `598.5s`)
- **AI Story Verdict**: `VERIFIED_COHERENT` (`confidence = 0.93`, 2-pass 8-frame adaptive inspection, `3` VLM calls, peak VRAM `2673 MiB`)
- **Recorded VLM & Transcript Observations**:
  - *Setup*: `"A first-person view from a vehicle on a road at sunset. A white pickup truck is ('Changed the truck. Yeah, we could swap it out. What's going on here?')"`
  - *Development*: `"The same scene with the white pickup truck now in a different position... ('But that dude')"`
  - *Payoff*: `"Outcome (72.0s): A white pickup truck is on a road, with a person in the background... ('Get a bison dually')"`
- **Audio Level Audit & Correction (Requirement 5)**:
  - **Initial Audit Finding**: In the initial render of `ai_story_p3`, Part 1 measured **`-19.1 LUFS`** (`LRA 5.0 LU`) while Part 2 measured **`-15.6 LUFS`** (`LRA 6.0 LU`) — a **`3.5 LU` cross-part loudness jump**. Inspection of `ebur128` frame logs showed that `selected-1.mp4` (`554.68–577.84s`) has a high raw loudness range (`LRA = 15.1 LU`, raw integrated `-22.5 LUFS`) where quiet roadside walking/speech (`t=3.0–18.0s`) caused FFmpeg's single-pass `loudnorm=I=-14:TP=-2.0:LRA=11` filter to hold gain conservatively, whereas Part 2 (`574.84–598.0s`) contains sustained truck engine acceleration (`LRA = 11.5 LU`).
  - **Pipeline Fix Applied**: Updated `audio_filter` in [factory/media.py](../../factory/media.py) to precede `LOUDNORM_FILTER` with a gentle dynamic pre-leveler (`dynaudnorm=f=250:g=15:p=0.9:m=10.0`) before `loudnorm=I=-14:TP=-2.0:LRA=11` and the final peak limiter (`alimiter=limit=0.707945:level=false:latency=true`).
  - **Verified Corrected Loudness**: Re-rendered both parts of job `383316f996ca4cf7848d0ea7f0908840` and verified all 25 media regression tests in `factory_tests/test_media.py`. Part 1 now measures **`-14.9 LUFS`** (`Peak -2.7 dBFS`) and Part 2 measures **`-15.7 LUFS`** (`Peak -1.8 dBFS`), reducing the cross-part loudness difference from `3.5 LU` to **`0.8 LU`** (perceptually matched).
- **Export Directory**: `%LOCALAPPDATA%\Factory89-unified\exports\ai_story_p3`

| Part | File & Contact Sheet | Absolute Source Window | Overlap | Duration / Frames | Corrected Loudness / Peak | Frame-by-Frame Visual & Cross-Part Inspection |
| --- | --- | --- | --- | --- | --- | --- |
| Part 1 | `ai_story_p3\clip-1.mp4` (`contact_sheets\ai_story_p3_clip1.jpg`) | **`554.68s – 577.84s`** (relative `28.68–51.84s`) | — | `23.17s` (`695` frames) | **`-14.9 LUFS`** / `-2.7 dBTP` (was `-19.1 LUFS`) | **Visual Continuity Verified**: Samples at `t=1.9s, 5.7s, 9.5s, 13.3s, 17.1s, 20.9s` show a white heavy-duty dually pickup truck pulling over on a tree-lined highway at sunset (`t=1.9s`, caption `"going on here? This"`), the player in a white tank top exiting the white pickup cab (`t=5.7s`), walking across the grass shoulder toward a brown camper van / RV where an NPC is standing (`t=9.5–17.1s`), and inspecting the RV door (`t=20.9s`). |
| Part 2 | `ai_story_p3\clip-2.mp4` (`contact_sheets\ai_story_p3_clip2.jpg`) | **`574.84s – 598.00s`** (relative `48.84–72.0s`) | **`3.0s`** (`574.84–577.84s`) | `23.17s` (`695` frames) | **`-15.7 LUFS`** / `-1.8 dBTP` (was `-15.6 LUFS`) | **Visual & Cross-Part Continuity Verified**: Frame 1 (`t=1.9s`, source `576.7s`) overlaps smoothly with the end of Part 1 beside the brown camper van (`3.0s` overlap). The player walks around the RV (`t=5.7s`, caption `"that dude"`), runs back across the highway toward the white dually pickup as a dark pickup passes (`t=9.5s`, caption `"fucking tripping balls"`), climbs back into the white dually pickup (`t=13.3s`, caption `"He doesn't even know where he"`), and drives down the sunset highway ahead of the RV (`t=17.1–20.9s`), ending cleanly at `598.0s` before the `PUMBA` end card. |

---

## 4. Multi-Source Long-Form Compilation (`compilation_longform`)

### 4.1 Job `0d65a6ed4849425dbcb2e28087db6839`

- **Workflow**: `Longs Compilation` (`workflow_mode = compilation`, `editorial_format = longs_16_9`, `target_duration_sec = 600`)
- **Sources Combined**:
  - `src_1`: `a90eb6ae10bf426b9e60a14025428dc1.mp4` (`617.91s`)
  - `src_2`: `qa-gta-721.mp4` (`720.73s`)
- **Segment Curation & Edit Plan**:
  - Selected **20 coherent, non-overlapping action/dialogue segments** (`14` from `src_1`, `6` from `src_2`) after excluding title/intro cards (`0–28s`), end cards (`598–618s`), and the static post-video dark screen/credits region (`344–720s` in `src_2`).
  - Tested manual editorial operations on the unified edit plan:
    - Segment reordering (`seg_02` and `seg_01` swapped at the opening).
    - Segment trimming (`0.25s` trim on `seg_02`).
    - Per-segment transitions (`fade` transitions applied across source boundaries and chapter breaks).
    - Optimistic concurrency control: verified that submitting a stale `plan_revision` (`revision 1` after advancing to `revision 2`) is rejected with `HTTP 409 Conflict`, and approved `plan_revision = 3`.
- **Technical & Visual QA Metrics (`%LOCALAPPDATA%\Factory89-unified\exports\compilation_longform\long.mp4`, Contact Sheet `contact_sheets\compilation_longform.jpg`)**:
  - **Actual Rendered Duration**: **`392.622722s`** (**6 minutes 32.62 seconds**, `11,778` frames)
  - **Target Runtime vs Actual Export Distinction**:
    - The requested target duration was **`600.0s` (10 minutes)**.
    - Because `src_2` (`qa-gta-721.mp4`) contains only `344.0s` of active gameplay before transitioning to `376.7s` of non-editorial dark post-video screens/credits, and `src_1` contains title/end cards and micro-cuts, the maximum defensible non-overlapping coherent compilation across these two local files is **`392.62s`**.
    - Accordingly, `timeline.json` (`%LOCALAPPDATA%\Factory89-unified\exports\compilation_longform\timeline.json`, `plan_revision = 3`) explicitly keeps **`"duration_shortfall": true`**.
    - **Important Qualification**: This test verifies **multi-source ingestion, revisioned edit-plan reordering/trimming/fades, HTTP 409 stale-revision protection, and 16:9 master assembly (`392.62s`)**, but **does NOT demonstrate a full 10–15 minute (`600–900s`) real-media export**. Full 10–15 minute real-media runtime requires additional authorized source footage (`>= 18–25 minutes` of active non-credit video).
  - **Resolution & Aspect Ratio**: `1280x720` (`16:9` progressive)
  - **Frame Rate & Codecs**: `30.0 fps` (`30/1`), `h264 (High)` video (`2,487 kbps`), `aac (LC)` stereo audio (`48 kHz`, `189 kbps`)
  - **Audio Loudness**: Integrated **`-14.8 LUFS`**, True Peak **`-0.2 dBTP`**, LRA `8.9 LU`
  - **Visual Contact Sheet Check (`contact_sheets\compilation_longform.jpg`)**: Sampled frames across the 6.5-minute timeline confirm clean `16:9` aspect framing, legible burned-in subtitles (`"Anybody behind us. No, I"`, `"We swim for it. Jump?"`), smooth fade transitions between `src_2` convertible escape scenes and `src_1` action segments, and zero black-screen padding.

---

## 5. Honest Assessment of Remaining Editorial Weaknesses

1. **Caption Overlap on Source Footage with Pre-Existing Burned-In Subtitles**:
   - In `compilation_longform` (`src_2` segments) and `gta_repaired\clip-2.mp4`, the raw source video already contains in-game or streamer-burned text in the lower center. When `request.captions=True`, the newly generated ASS subtitles render over the lower-third zone and can partially overlap existing in-game subtitles (visible in `compilation_longform.jpg` panels 1 and 5). Operators editing sources with pre-existing subtitles should either disable generated captions (`text_mode = "none"` / `captions = false`) or raise `caption_margin` in the Editor UI.
2. **Coarse VLM Text Summaries vs Exact Vehicle/Action Details in `qwen3-vl:2b-instruct`**:
   - While the 2-pass 8-frame storyboard architecture reliably distinguishes coherent single-event progressions (`P1–P4`) from compilation cuts and title/end cards (`N1–N5`), the `2B` model's short text descriptions occasionally misstate secondary details (e.g., describing the `P1` payoff frame as `"yellow pickup truck is now in the middle of a lane"` when the truck has actually splashed into the river below the bridge, or describing an NPC near the camper van in `P3` as `"holding a large object, possibly a bison"` influenced by the spoken transcript line `"Get a bison dually"`). These generated beat strings are diagnostic aids for the operator and must not be used as unreviewed public descriptions.
3. **Human Editorial Sign-Off Requirement**:
   - All outputs remain marked `editorial.status = "NEEDS_REVIEW"` / `approved = false` (`PENDING_HUMAN_EDITORIAL_APPROVAL`). Final `EDITORIALLY_APPROVED` status is reserved exclusively for human operator review in the UI.
