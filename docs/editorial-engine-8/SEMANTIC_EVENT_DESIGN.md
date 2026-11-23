# Phase 3 — Semantic Event Graph Design

## Core Problem Statement
Baseline Factory v7 treats any 35–60 s window with motion or speech activity as a highlight candidate, failing to distinguish:
1. A multi-shot coherent event (e.g., driving → crash → streamer reaction) from
2. Unrelated compilation clips placed back-to-back (e.g., car interior → daytime ocean boat → nighttime on-foot chase), or
3. Non-gameplay format breaks (e.g., `"PUMBA PRESENTS"` intro card, `"A FEW MOMENTS LATER"` interstitial, `"PUMBA"` end card, `"WASTED"` / `"FAILED"` screens).

## Semantic Hierarchy
1. **FrameSample**: Timestamped visual & audio feature vector at source-relative time $t$.
2. **Shot**: Visually continuous camera segment $[t_s, t_e]$ between detected shot boundaries, enriched with appearance signature (HSV histograms, 3×3 spatial grid, luminance/saturation/edge stats, corner HUD/facecam signature, static variance, graphic-card probability, OCR/text-band cues, audio profile, transcript span, optional VLM description).
3. **FormatBreak**: Explicit negative-evidence classification on a shot or boundary:
   - `end_card`, `title_card`, `interstitial`, `black_screen`, `death_fail_screen`, `menu_or_loading`, `promo_card`.
   - Severity tiers: `hard_invalid_media`, `strong_semantic_contradiction`, `probable_format_break`, `weak_discontinuity`, `unknown_continuity`.
4. **Scene**: Maximal run of adjacent non-break shots linked by compatible visual/HUD/lighting/semantic edges.
5. **SemanticEvent**: One coherent editorial unit spanning one or more shots within a scene (or across a short verified reaction/replay cut), carrying:
   - Supported `setup_time`, `action_start`, `climax_time`, `reaction_time`, `payoff_time`, `natural_end` (or `None` when unsupported).
   - Structured `continuity_confidence`, `evidence_items`, `defect_flags`, and `decision` (`ACCEPT`, `ACCEPT_WITH_REVIEW`, `REVISE_BOUNDARIES`, `SPLIT_EVENTS`, `REJECT`, `INSUFFICIENT_EVIDENCE`).
6. **HighlightCandidate**: A boundary-refined excerpt of a `SemanticEvent` (or operator-requested split) with explicit provenance and editorial rubric scores.

## Adjacent-Shot Continuity Edge Model
For each adjacent shot pair $(S_i, S_{i+1})$:
- **Visual appearance similarity** ($s_{\text{vis}} \in [0,1]$): Bhattacharyya/L1 similarity on global HSV histogram + 3×3 spatial color grid.
- **Lighting & palette shift** ($\Delta_{\text{light}}$): Mean luminance shift + warm/cool chromatic shift (detects daytime ocean ↔ dark car interior ↔ nighttime city).
- **Persistent HUD / Overlay signature similarity** ($s_{\text{hud}} \in [0,1]$): Compares the 4 frame corners (minimap bottom-left, wanted stars/score top-right, streamer webcam inset, channel watermark/KICK banner). In gameplay compilations, changing HUD/minimap/facecam layout across a cut is a high-precision signal of a new unrelated clip.
- **Audio & speech continuity** ($s_{\text{aud}}, s_{\text{speech}}$): Checks whether a spoken sentence spans the cut or whether an abrupt audio floor/spectral change occurs. **Rule**: Continuous music/audio NEVER overrides a strong visual + HUD contradiction.
- **Format-break gate**: If either shot is classified as `title_card`, `end_card`, `interstitial`, `black_screen`, or `menu_or_loading`, the edge is a hard `format_break` (never merged across). A `death_fail_screen` (`WASTED`/`FAILED`) closes the preceding event as a payoff marker and opens a hard boundary before the next clip.
- **Optional VLM edge verification** (`compare_adjacent_shots`): Invoked within budget on uncertain edges (e.g., moderate visual change where two camera angles might belong to the same action) and on finalist boundaries.

## Anti-Padding Boundary Refinement Rules
1. Trim leading/trailing format breaks (end cards, title cards, black fades) strictly outside the clip interval.
2. Snap boundaries cleanly to complete speech words/sentences where speech exists without spilling into a format break or unrelated shot.
3. Preserve setup before the climax and reaction/payoff after the climax.
4. **Never pad a coherent short event** (e.g., 26 s yellow-truck bridge fall) with unrelated neighboring clips to satisfy a 35 s minimum! Instead:
   - Keep the clean 26 s event in `review_alternatives` with `duration_conflict = {"event_duration": 26.0, "requested_minimum": 35.0}` so the operator can approve a duration override.
   - Only emit automatic clips in `[minimum, maximum]` when genuine coherent events satisfy the bounds.
