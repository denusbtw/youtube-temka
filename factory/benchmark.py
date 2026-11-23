"""Versioned editorial benchmark dataset schema (v2) and evaluation metrics.

Supports multi-annotator uncertainty, format-break contamination checks,
unrelated-event merge detection, and 1-5 human editorial rubric scoring.
Never converts provisional or uncertain annotations into verified human approval.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


FormatBreakKind = Literal[
    'end_card',
    'title_card',
    'interstitial',
    'death_fail_screen',
    'black_screen',
    'advertisement',
    'menu',
    'loading_screen',
    'promo_card',
]

RejectionReason = Literal[
    'end_card_contamination',
    'title_card_contamination',
    'unrelated_scenes_merged',
    'missing_payoff',
    'missing_setup',
    'weak_hook',
    'poor_pacing',
    'layout_defect',
    'caption_defect',
    'unsupported_claim',
    'menu_or_loading_screen',
    'no_interesting_event',
    'duplicate_event',
    'other',
]


class FormatBreakAnnotation(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    kind: FormatBreakKind
    note: str = Field(default='', max_length=300)

    @model_validator(mode='after')
    def check_order(self):
        if self.end <= self.start:
            raise ValueError('Format break end must follow start')
        return self


class SemanticEventAnnotation(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    preferred_start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    preferred_end: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    setup: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    climax: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    payoff: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    label: str = Field(min_length=1, max_length=200)
    category: str = Field(default='gameplay', max_length=50)
    interest_rating: float | None = Field(default=None, ge=1, le=5, allow_inf_nan=False)
    uncertainty: Literal['low', 'medium', 'high'] = 'medium'
    annotator_disagreement: bool = False
    notes: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def check_bounds(self):
        if self.end <= self.start:
            raise ValueError('Event end must follow start')
        if self.preferred_start is not None and self.preferred_end is not None:
            if self.preferred_end <= self.preferred_start:
                raise ValueError('Preferred end must follow preferred start')
        return self


class RejectedIntervalAnnotation(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=300)
    defect_tags: list[RejectionReason] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_order(self):
        if self.end <= self.start:
            raise ValueError('Rejected interval end must follow start')
        return self


class HumanRubricRating(BaseModel):
    annotator: str = Field(min_length=1, max_length=80)
    clip_start: float = Field(ge=0, allow_inf_nan=False)
    clip_end: float = Field(gt=0, allow_inf_nan=False)
    interest: int = Field(ge=1, le=5)
    coherence: int = Field(ge=1, le=5)
    hook: int = Field(ge=1, le=5)
    payoff: int = Field(ge=1, le=5)
    pacing: int = Field(ge=1, le=5)
    visual_composition: int = Field(ge=1, le=5)
    captions: int = Field(ge=1, le=5)
    overall_readiness: int = Field(ge=1, le=5)
    decision: Literal[
        'approved',
        'needs_trim',
        'needs_different_layout',
        'incorrect_event',
        'unrelated_scenes',
        'missing_payoff',
        'weak_hook',
        'reject',
    ]
    rejection_reasons: list[RejectionReason] = Field(default_factory=list)
    notes: str = Field(default='', max_length=500)


class BenchmarkSource(BaseModel):
    schema_version: Literal[2] = 2
    source_id: str = Field(min_length=1, max_length=120)
    source_sha256: str | None = Field(default=None, max_length=64)
    source_url: str | None = Field(default=None, max_length=2048)
    rights_status: Literal['authorized_local', 'synthetic_fixture', 'pending_rights']
    source_type: Literal[
        'compilation',
        'single_session_gameplay',
        'livestream',
        'podcast',
        'interview',
        'visual_no_speech',
        'no_highlights',
    ]
    content_category: Literal['gameplay', 'racing', 'shooter', 'stream', 'podcast', 'visual', 'auto']
    subject: str = Field(default='', max_length=120)
    duration: float = Field(gt=0, allow_inf_nan=False)
    split: Literal['dev', 'regression', 'holdout'] = 'regression'
    annotation_status: Literal['provisional_engineering_inspection', 'human_verified_ground_truth'] = (
        'provisional_engineering_inspection'
    )
    annotators: list[str] = Field(default_factory=list)
    shot_boundaries: list[float] = Field(default_factory=list)
    format_breaks: list[FormatBreakAnnotation] = Field(default_factory=list)
    semantic_events: list[SemanticEventAnnotation] = Field(default_factory=list)
    rejected_intervals: list[RejectedIntervalAnnotation] = Field(default_factory=list)
    human_ratings: list[HumanRubricRating] = Field(default_factory=list)
    false_positive_notes: list[str] = Field(default_factory=list)
    missed_moment_notes: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_all_within_duration(self):
        for br in self.format_breaks:
            if br.end > self.duration + 1e-3:
                raise ValueError(f'Format break {br.start}-{br.end} exceeds duration {self.duration}')
        for ev in self.semantic_events:
            if ev.end > self.duration + 1e-3:
                raise ValueError(f'Semantic event {ev.id} exceeds duration {self.duration}')
        for rej in self.rejected_intervals:
            if rej.end > self.duration + 1e-3:
                raise ValueError(f'Rejected interval {rej.start}-{rej.end} exceeds duration {self.duration}')
        return self


def overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def temporal_iou(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    inter = overlap_seconds(a_start, a_end, b_start, b_end)
    union = max(a_end, b_end) - min(a_start, b_start)
    return inter / union if union > 0 else 0.0


def evaluate_source(
    source: BenchmarkSource | dict,
    candidates: list[dict],
    k: int = 3,
    operational: dict | None = None,
) -> dict:
    """Evaluate candidate selections against a versioned BenchmarkSource."""
    if not isinstance(source, BenchmarkSource):
        source = BenchmarkSource.model_validate(source)
    if k < 1:
        raise ValueError('k must be >= 1')

    top_k = list(candidates[:k])
    valid_clips: list[tuple[float, float, dict]] = []
    invalid_count = 0
    for item in top_k:
        a = item.get('start_seconds')
        b = item.get('end_seconds')
        if (
            isinstance(a, (int, float))
            and isinstance(b, (int, float))
            and math.isfinite(a)
            and math.isfinite(b)
            and 0 <= a < b <= source.duration + 1e-3
        ):
            valid_clips.append((float(a), float(b), item))
        else:
            invalid_count += 1

    # Discovery metrics
    events = source.semantic_events
    best_ious: list[float] = []
    for ev in events:
        ref_start = ev.preferred_start if ev.preferred_start is not None else ev.start
        ref_end = ev.preferred_end if ev.preferred_end is not None else ev.end
        best = max((temporal_iou(ref_start, ref_end, a, b) for a, b, _ in valid_clips), default=0.0)
        best_ious.append(best)

    recall_at_k = (
        round(sum(score >= 0.5 for score in best_ious) / len(best_ious), 4)
        if best_ious
        else None
    )
    mean_best_iou = round(sum(best_ious) / len(best_ious), 4) if best_ious else None
    missed_events = sum(score < 0.5 for score in best_ious)

    duplicates = sum(
        any(temporal_iou(a, b, pa, pb) >= 0.5 for pa, pb, _ in valid_clips[:i])
        for i, (a, b, _) in enumerate(valid_clips)
    )
    dup_rate = round(duplicates / len(valid_clips), 4) if valid_clips else 0.0

    # Merged coverage of source
    intervals = sorted((a, b) for a, b, _ in valid_clips)
    merged: list[tuple[float, float]] = []
    for a, b in intervals:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    coverage = round(sum(b - a for a, b in merged) / source.duration, 4) if source.duration > 0 else 0.0

    # Semantic correctness metrics
    card_kinds = {'end_card', 'title_card', 'interstitial', 'promo_card'}
    menu_kinds = {'menu', 'loading_screen', 'advertisement'}
    end_card_contaminated = 0
    menu_contaminated = 0
    unrelated_scene_contaminated = 0
    rejected_overlaps = 0
    false_positives = 0
    unsupported_claims = 0
    setup_payoff_present = 0
    clip_diagnostics: list[dict] = []

    for a, b, item in valid_clips:
        card_hits = [
            br.kind
            for br in source.format_breaks
            if br.kind in card_kinds and overlap_seconds(a, b, br.start, br.end) >= 0.5
        ]
        menu_hits = [
            br.kind
            for br in source.format_breaks
            if br.kind in menu_kinds and overlap_seconds(a, b, br.start, br.end) >= 0.5
        ]
        if card_hits:
            end_card_contaminated += 1
        if menu_hits:
            menu_contaminated += 1

        # Count distinct reference events substantially overlapped (>=3s each)
        covered_events = [
            ev.id
            for ev in events
            if overlap_seconds(a, b, ev.start, ev.end) >= 3.0
        ]
        if len(covered_events) >= 2:
            unrelated_scene_contaminated += 1

        hit_rejected = False
        for r in source.rejected_intervals:
            if temporal_iou(a, b, r.start, r.end) < 0.5:
                continue
            # If the rejected interval failed specifically due to format-break or
            # multi-event contamination, count it as reproduced only if the defect
            # remains present (e.g., trimming out the end card resolves Candidate A).
            defect_set = set(r.defect_tags)
            if defect_set and defect_set <= {'end_card_contamination', 'title_card_contamination', 'unrelated_scenes_merged', 'menu_or_loading_screen'}:
                reproduced = (
                    ('end_card_contamination' in defect_set and bool(card_hits))
                    or ('title_card_contamination' in defect_set and bool(card_hits))
                    or ('menu_or_loading_screen' in defect_set and bool(menu_hits))
                    or ('unrelated_scenes_merged' in defect_set and len(covered_events) >= 2)
                )
                if reproduced:
                    hit_rejected = True
                    break
            else:
                hit_rejected = True
                break
        if hit_rejected:
            rejected_overlaps += 1

        matched_event = any(
            temporal_iou(
                ev.preferred_start if ev.preferred_start is not None else ev.start,
                ev.preferred_end if ev.preferred_end is not None else ev.end,
                a,
                b,
            ) >= 0.5
            for ev in events
        )
        is_fp = hit_rejected or bool(card_hits) or bool(menu_hits) or len(covered_events) >= 2 or (bool(events) and not matched_event)
        if is_fp:
            false_positives += 1

        if item.get('setup') and item.get('payoff'):
            setup_payoff_present += 1
        elif item.get('setup_time') is not None and item.get('payoff_time') is not None:
            setup_payoff_present += 1

        if item.get('semantic_verification') == 'verified' and not item.get('evidence'):
            unsupported_claims += 1

        clip_diagnostics.append({
            'interval': [round(a, 3), round(b, 3)],
            'duration': round(b - a, 3),
            'card_contamination': card_hits,
            'menu_contamination': menu_hits,
            'overlapped_events': covered_events,
            'unrelated_scenes_merged': len(covered_events) >= 2,
            'overlaps_known_rejected': hit_rejected,
            'matches_reference_event': matched_event,
        })

    n_valid = len(valid_clips)
    # Human editorial precision@K ONLY when verified human ratings exist
    human_approved = None
    if source.human_ratings and n_valid:
        matched_ratings = []
        for a, b, _ in valid_clips:
            ratings_for_clip = [
                r for r in source.human_ratings if temporal_iou(a, b, r.clip_start, r.clip_end) >= 0.7
            ]
            if ratings_for_clip:
                approved_votes = sum(r.decision == 'approved' and not r.rejection_reasons for r in ratings_for_clip)
                matched_ratings.append(approved_votes > len(ratings_for_clip) / 2)
        if matched_ratings:
            human_approved = round(sum(matched_ratings) / len(matched_ratings), 4)

    return {
        'schema_version': 2,
        'source_id': source.source_id,
        'split': source.split,
        'annotation_status': source.annotation_status,
        'k': k,
        'selected_count': len(top_k),
        'valid_count': n_valid,
        'discovery': {
            'event_recall_at_k': recall_at_k,
            'mean_best_event_iou': mean_best_iou,
            'missed_event_count': missed_events,
            'candidate_duplication_rate': dup_rate,
            'source_coverage': coverage,
            'false_positive_rate': round(false_positives / n_valid, 4) if n_valid else 0.0,
            'invalid_interval_rate': round(invalid_count / len(top_k), 4) if top_k else 0.0,
        },
        'semantic_correctness': {
            'end_card_title_card_contamination_rate': round(end_card_contaminated / n_valid, 4) if n_valid else 0.0,
            'unrelated_scene_contamination_rate': round(unrelated_scene_contaminated / n_valid, 4) if n_valid else 0.0,
            'menu_loading_contamination_rate': round(menu_contaminated / n_valid, 4) if n_valid else 0.0,
            'rejected_baseline_overlap_count': rejected_overlaps,
            'unsupported_claim_rate': round(unsupported_claims / n_valid, 4) if n_valid else 0.0,
        },
        'editorial': {
            'setup_payoff_coverage': round(setup_payoff_present / n_valid, 4) if n_valid else 0.0,
            'human_editorial_precision_at_k': human_approved,
            'editorial_verification_status': (
                'EDITORIALLY_VERIFIED' if human_approved is not None and source.annotation_status == 'human_verified_ground_truth'
                else 'PENDING_HUMAN_REVIEW'
            ),
        },
        'operational': operational or {},
        'clip_diagnostics': clip_diagnostics,
    }


def load_benchmark_source(path: Path) -> BenchmarkSource:
    return BenchmarkSource.model_validate_json(path.read_text(encoding='utf-8'))


def save_benchmark_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
