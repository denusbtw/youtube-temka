# Phase 1 — Preserved GTA Baseline Results (`aef60f1`)

## Source Identity (Preserved, Unmodified)
- **Local cached path**: `youtube-temka/scratch/controller/event-acceptance/full-progressive.mp4`
- **Source URL**: `https://www.youtube.com/watch?v=yux8s3lwLss`
- **Source Title Claim**: `GTA 6 FUNNY MOMENTS & WINS #1 (GTA 6 FAILS)`
- **SHA-256**: `1a66b8c9cdf61a1246bcb262ab9f85f01fe0a4216629fd92e41dec53ecb8aeb8`
- **Decoded Duration**: `617.906213 s` (640×360 progressive H.264/AAC)
- **Preserved Whisper Transcript**: `youtube-temka/scratch/controller/factory72-acceptance/transcript.json` (29 English speech segments, 4.34 s–600.26 s)
- **Preserved Baseline Artifacts**: `verification/runtime/event-acceptance/{report.json,visual-v2.json,independent-1..3.mp4,independent-1..3-frame-0..2.jpg,EDITORIAL_OBSERVATIONS.md}`

## Baseline Pipeline Measurements (`aef60f1`)
- Coarse sampling: `309` samples, `54` possible cuts (`21.62 s`)
- Independent planning + local fine inspection: `102` discovered proposals, `3` selected (`171.52 s`)
- Connected Story: `0/3` selected (`314.10 s`, 6 Ollama calls: 3 insufficient, 3 out-of-bounds)
- Technical validation: All 3 rendered MP4s valid 1080×1920, 0 black frames, A/V delta ≤ 22.7 ms.

## Verified Editorial Failures (Visual Frame Inspection Confirmed)
| Candidate | Baseline Interval (s) | Duration | Visual Evidence Inspected | Root Defect | Editorial Status |
|---|---|---|---|---|---|
| **A** (`independent-1.mp4`) | `554.120–600.260` | 46.14 s | `independent-1-frame-2.jpg` shows `"PUMBA"` purple/orange sunset branded end card + subscribe box + stray word `"You"` | End-card contamination (~598.5–600.26 s). Underlying 530–598 s truck-swap sequence is actually a coherent event that needed tail trimming before the end card. | **FAILED** |
| **B** (`independent-2.mp4`) | `172.980–216.980` | 44.00 s | `frame-0`: dark car interior driver; `frame-1`: daytime ocean boat; `frame-2`: nighttime on-foot 4-star wanted street chase | Unrelated scene contamination (4 distinct compilation clips merged blindly to fill 44 s). | **FAILED** |
| **C** (`independent-3.mp4`) | `5.420–52.500` | 47.08 s | `frame-1`: `"PUMBA PRESENTS"` giant neon intro title card; `frame-2`: `DRIFTAIR` commercial airplane + fighter jet over ocean | Intro title-card contamination (~24–28 s) + 5 unrelated compilation clips merged blindly. | **FAILED** |

## Full-Source Visual Inventory (5-second Contact Sheet Inspection)
Visual inspection of the complete 618 s video reveals:
- **Major Format Breaks**:
  - `24–28 s`: `"PUMBA PRESENTS"` neon sunset intro title card
  - `169–173 s`: `"A FEW MOMENTS LATER"` red SpongeBob interstitial card
  - `404–412 s`: `"WASTED"` red screen + black fade (`~409–412 s`)
  - `489–493 s`: `"FAILED"` dark red screen
  - `598.5–617.9 s`: `"PUMBA"` branded sunset end card (19.4 s)
- **Notable Coherent Multi-Shot / Sustained Events**:
  - `528–598 s` (~70 s): Pickup truck off-road → sunset highway → truck-swap RV dialogue → Bison Dually getaway (contains strong dialogue & payoff when trimmed BEFORE 598.5 s end card!)
  - `444–480 s` (~36 s): Neighborhood mobility-scooter ride/chase (continuous character, vehicle, daytime street, minimap)
  - `412–443 s` (~31 s): Yellow pickup truck highway bridge stunt → falls off bridge into water (clear setup → climax → payoff)
  - `324–355 s` (~31 s): Convenience store scene → walkout to parking lot with RV & truck
  - `283–310 s` (~27 s): Streamer flying plane through skyscrapers → crash/explosion
  - `500–526 s` (~26 s): Radio tower bird nest → skydiving / hot air balloon
  - `243–265 s` (~22 s): Pickup truck highway traffic weave / chase
  - `140–160 s` (~20 s): Motorcycle/bicycle street stunt with streamer webcam
  - `94–112 s` (~18 s): Van & lion/mountain-lion encounter with reaction webcam

## Baseline Benchmark Score Summary
- **End-card / title-card contamination rate**: `2/3` (`66.7%`)
- **Unrelated-scene contamination rate**: `2/3` (`66.7%`)
- **Overall baseline editorial pass rate**: `0/3` (`0.0%`)
- **Editorial Quality Status**: `FAILED_BASELINE` (all 3 baseline clips rejected on editorial inspection).
