# Phase 1 — Editorial Benchmark Specification (Schema v2)

## Purpose
Provide a versioned, reproducible evaluation framework (`factory/benchmark.py`) that measures discovery quality, semantic correctness, format-break avoidance, editorial usefulness, and operational efficiency before and after changes to the clipping engine.

## Dataset Annotation Schema (`schema_version = 2`)
Each benchmark source annotation JSON includes:
- `schema_version`: `2`
- `source_id`, `source_sha256`, `source_url` (optional), `rights_status` (`authorized_local`, `synthetic_fixture`, `pending_rights`)
- `source_type` (`compilation`, `single_session_gameplay`, `livestream`, `podcast`, `interview`, `visual_no_speech`, `no_highlights`)
- `content_category` (`gameplay`, `racing`, `shooter`, `stream`, `podcast`, `visual`, `auto`)
- `subject` (game or topic)
- `duration` (seconds)
- `annotators`: list of annotator IDs + `annotation_status` (`provisional_engineering_inspection` vs `human_verified_ground_truth`)
- `shot_boundaries`: optional list of timestamped cuts
- `format_breaks`: list of `{start, end, kind, note}` (`end_card`, `title_card`, `interstitial`, `death_fail_screen`, `black_screen`, `ad`, `menu`, `loading_screen`)
- `semantic_events`: list of `{id, start, end, preferred_start, preferred_end, setup, climax, payoff, label, category, interest_rating, uncertainty, notes}`
- `rejected_intervals`: list of `{start, end, reason}` (including baseline regression failures!)
- `human_ratings`: optional multi-annotator 1–5 rubric ratings per clip across 8 dimensions (`interest`, `coherence`, `hook`, `payoff`, `pacing`, `visual_composition`, `captions`, `overall_readiness`) plus explicit rejection reasons.

## Computed Metrics
### Discovery
- `event_recall_at_k`: Fraction of reference events matched at temporal IoU ≥ 0.5 by top-K candidates.
- `mean_best_event_iou`: Mean best temporal IoU across reference events.
- `missed_event_count`: Reference events with best IoU < 0.5.
- `candidate_duplication_rate`: Fraction of selected candidates overlapping another selected candidate at IoU ≥ 0.5.
- `source_coverage`: Fraction of source duration covered by selected clips.
- `false_positive_rate`: Fraction of selected clips matching a rejected interval or overlapping <0.5 with any valid event.

### Semantic Correctness
- `end_card_contamination_rate`: Fraction of selected clips overlapping any `end_card` or `title_card`/`interstitial` format break by > 0.5 s.
- `unrelated_scene_contamination_rate`: Fraction of selected clips spanning ≥2 distinct reference semantic events (each ≥ 3 s inside the clip).
- `menu_loading_contamination_rate`: Fraction of selected clips overlapping `menu`/`loading_screen`/`ad` breaks.
- `rejected_baseline_overlap_count`: Count of selected clips overlapping known baseline defect intervals at IoU ≥ 0.5.
- `unsupported_claim_rate`: Fraction of clips whose metadata/hook asserts unverified specific claims without evidence.

### Editorial & Operational
- `setup_payoff_coverage`: Fraction of selected clips containing supported setup and payoff timestamps/evidence.
- `editorial_precision_at_k`: Computed ONLY when human rubric ratings exist (`None` / `PENDING_HUMAN_REVIEW` otherwise).
- Operational fields: `analysis_seconds`, `render_seconds`, `peak_vram_mib`, `peak_ram_mb`, `model_calls`, `cache_hit_rate`.

## Diversity & Holdout Policy
Target: 15–20 authorized sources across GTA gameplay, racing, shooter, livestreams, gaming compilations, podcasts/interviews, streamer reactions, low-speech visual footage, rapid compilations, ad/card-heavy sources, and no-highlight sources, with a dedicated holdout subset.
- **Current local availability**: Full 617.906 s authorized GTA compilation (`full-progressive.mp4`), local English speech fixture (`verification/source-en.mp4`), owned synthetic/multi-hour fixtures (`scratch/v7-fixtures/*`), and offline multi-genre regression fixtures. External multi-genre authorized long-form sources (racing, shooter, podcast, holdout set) are **NOT_YET_SUPPLIED** and are tracked honestly as a dataset blocker rather than fabricated.
