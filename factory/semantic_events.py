"""Semantic Event Graph, Format-Break Detection & Boundary Refinement (Phase 3).

Models the explicit hierarchy:
  FrameSample -> Shot -> FormatBreak / ContinuityEdge -> SemanticEvent -> RefinedCandidate

Key guarantees:
- Never conflates shot boundaries with automatic event boundaries.
- Never treats continuous audio/music as proof of visual event continuity.
- Explicitly detects title cards, end cards, interstitials, fail screens, menus,
  and black fades using multi-signal CV + optional VLM verification.
- Never pads a short coherent event with unrelated adjacent footage to meet a
  minimum duration threshold.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from .source_intelligence import Shot, SourceEvidence, extract_frames_at_timestamps
from .vision_vlm import LocalVisionAdapter


BreakSeverity = Literal[
    'none',
    'unknown_continuity',
    'weak_discontinuity',
    'probable_format_break',
    'strong_semantic_contradiction',
    'hard_invalid_media',
]

EdgeRelation = Literal[
    'same_action_continuation',
    'same_scene_camera_cut',
    'reaction_cut',
    'probable_new_event',
    'format_break_title_card',
    'format_break_end_card',
    'format_break_interstitial',
    'format_break_death_fail',
    'format_break_fade',
    'format_break_menu',
    'uncertain',
]

CandidateDecision = Literal[
    'ACCEPT',
    'ACCEPT_WITH_REVIEW',
    'REVISE_BOUNDARIES',
    'SPLIT_EVENTS',
    'REJECT',
    'INSUFFICIENT_EVIDENCE',
]


OCR_END_CARD_RE = re.compile(r'\b(subscribe|watch next|thanks for watching|outro|follow|send your|pumba)\b', re.I)
OCR_TITLE_CARD_RE = re.compile(r'\b(presents|moments later|chapter|episode|part \d+|intro|loading)\b', re.I)
OCR_FAIL_RE = re.compile(r'\b(wasted|mission failed|failed|game over|defeat|busted|eliminated)\b', re.I)


class FormatBreak(BaseModel):
    shot_id: int
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    kind: Literal[
        'end_card',
        'title_card',
        'interstitial',
        'death_fail_screen',
        'black_screen',
        'menu_or_loading',
        'graphic_stinger',
        'advertisement',
    ]
    severity: BreakSeverity = 'probable_format_break'
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    vlm_verified: bool = False


class ShotEdge(BaseModel):
    from_shot: int
    to_shot: int
    boundary_time: float = Field(ge=0)
    same_event: bool
    relation: EdgeRelation
    severity: BreakSeverity
    continuity_score: float = Field(ge=0, le=1)
    visual_similarity: float = Field(ge=0, le=1)
    hud_similarity: float = Field(ge=0, le=1)
    luma_shift: float = Field(ge=0, le=1)
    speech_crosses_cut: bool = False
    vlm_verified: bool = False
    reason: str = ''


class SemanticEvent(BaseModel):
    event_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    duration: float = Field(gt=0)
    shot_ids: list[int] = Field(default_factory=list)
    shot_count: int = Field(ge=1)
    mean_continuity: float = Field(ge=0, le=1)
    setup_time: float | None = None
    action_start_time: float | None = None
    climax_time: float | None = None
    reaction_time: float | None = None
    payoff_time: float | None = None
    natural_end_time: float | None = None
    terminated_by_break: str | None = None
    preceded_by_break: str | None = None
    speech_text: str = ''
    speech_word_count: int = 0
    has_reaction_cue: bool = False
    mean_motion: float = 0.0
    peak_motion: float = 0.0
    mean_audio_rms: float = 0.0
    peak_audio_rms: float = 0.0
    audio_burst_count: int = 0
    face_corners: list[str] = Field(default_factory=list)
    vlm_summary: str = ''
    vlm_verified: bool = False
    evidence: list[str] = Field(default_factory=list)
    defect_flags: list[str] = Field(default_factory=list)
    decision: CandidateDecision = 'ACCEPT_WITH_REVIEW'
    uncertainty: float = Field(default=0.35, ge=0, le=1)


class EventGraph(BaseModel):
    schema_version: Literal[1] = 1
    source_fingerprint: str
    duration: float
    format_breaks: list[FormatBreak] = Field(default_factory=list)
    edges: list[ShotEdge] = Field(default_factory=list)
    events: list[SemanticEvent] = Field(default_factory=list)
    vlm_stats: dict[str, Any] = Field(default_factory=dict)


def classify_format_break(
    shot: Shot,
    source_duration: float,
    content_category: str = 'auto',
    is_last_shot: bool = False,
) -> FormatBreak | None:
    """Multi-signal deterministic detector for non-content format breaks."""
    reasons: list[str] = []
    ocr = shot.ocr_text or ''

    # 1. Black screen / fade to black
    if shot.dark_fraction >= 0.70 and shot.mean_luma < 16.0:
        reasons.append(f'dark_fraction={shot.dark_fraction:.2f}, mean_luma={shot.mean_luma:.1f}')
        return FormatBreak(
            shot_id=shot.shot_id,
            start=shot.start,
            end=shot.end,
            kind='black_screen',
            severity='probable_format_break',
            confidence=0.92,
            evidence=reasons,
        )

    # 2. Death / Fail / Mission Failed screen (red wash or OCR match)
    if (
        (shot.red_tint_score >= 0.25 and shot.duration <= 6.0 and shot.high_sat_frac >= 0.55)
        or (OCR_FAIL_RE.search(ocr) and shot.duration <= 6.5)
        or (shot.high_sat_frac >= 0.88 and shot.mean_sat >= 0.80 and shot.static_fraction >= 0.80 and shot.mean_luma <= 35.0 and shot.duration <= 4.0)
    ):
        reasons.append(
            f'red_tint={shot.red_tint_score:.2f}, high_sat={shot.high_sat_frac:.2f}, static={shot.static_fraction:.2f}'
        )
        if ocr:
            reasons.append(f'ocr="{ocr[:80]}"')
        # Distinguish red interstitial ("A FEW MOMENTS LATER") vs death/fail screen
        is_interstitial = (
            bool(OCR_TITLE_CARD_RE.search(ocr))
            or (shot.red_tint_score >= 0.42 and shot.center_text_band_score >= 0.14 and shot.static_fraction >= 0.85)
        )
        return FormatBreak(
            shot_id=shot.shot_id,
            start=shot.start,
            end=shot.end,
            kind='interstitial' if is_interstitial else 'death_fail_screen',
            severity='probable_format_break',
            confidence=0.88,
            evidence=reasons,
        )

    # 3. Branded end card (strictly at the tail of the video, not an interior card!)
    near_end = is_last_shot or shot.end >= source_duration - 2.0 or (
        shot.start >= source_duration * 0.85 and shot.end >= source_duration * 0.94
    )
    strong_graphic_static = (
        shot.static_fraction >= 0.65
        and shot.mean_motion <= 0.035
        and (shot.high_sat_frac >= 0.40 or shot.mean_sat >= 0.48 or shot.center_text_band_score >= 0.06)
    )
    if (near_end and strong_graphic_static and shot.duration >= 2.5) or (
        OCR_END_CARD_RE.search(ocr) and shot.static_fraction >= 0.50 and near_end
    ):
        reasons.append(
            f'near_end={near_end}, static={shot.static_fraction:.2f}, motion={shot.mean_motion:.3f}, '
            f'high_sat={shot.high_sat_frac:.2f}, sat={shot.mean_sat:.2f}'
        )
        if ocr:
            reasons.append(f'ocr="{ocr[:80]}"')
        return FormatBreak(
            shot_id=shot.shot_id,
            start=shot.start,
            end=shot.end,
            kind='end_card',
            severity='probable_format_break',
            confidence=0.92,
            evidence=reasons,
        )

    # 4. Intro title card / branded interstitial card
    if (
        shot.duration <= 10.0
        and (
            (shot.high_sat_frac >= 0.40 and shot.mean_sat >= 0.45 and (shot.static_fraction >= 0.50 or shot.center_text_band_score >= 0.08))
            or (OCR_TITLE_CARD_RE.search(ocr) and shot.static_fraction >= 0.40)
            or (shot.high_sat_frac >= 0.68 and shot.mean_sat >= 0.62 and shot.duration <= 3.0)
        )
    ):
        kind: Any = 'graphic_stinger' if shot.duration <= 2.5 and shot.static_fraction < 0.5 else 'title_card'
        reasons.append(
            f'dur={shot.duration:.1f}s, high_sat={shot.high_sat_frac:.2f}, sat={shot.mean_sat:.2f}, '
            f'txt={shot.center_text_band_score:.2f}, static={shot.static_fraction:.2f}'
        )
        if ocr:
            reasons.append(f'ocr="{ocr[:80]}"')
        return FormatBreak(
            shot_id=shot.shot_id,
            start=shot.start,
            end=shot.end,
            kind=kind,
            severity='probable_format_break',
            confidence=0.86,
            evidence=reasons,
        )

    # 5. Menu / static loading / scrolling credits screen (non-podcast, long frozen frame with no speech/action)
    if (
        content_category not in ('podcast',)
        and shot.duration >= 4.0
        and shot.static_fraction >= 0.85
        and shot.mean_motion <= 0.012
        and (shot.peak_motion <= 0.018 or (shot.mean_luma <= 42.0 and shot.static_fraction >= 0.88))
        and shot.speech_word_count == 0
        and shot.audio_burst_count == 0
    ):
        reasons.append(f'frozen_static={shot.static_fraction:.2f}, motion={shot.mean_motion:.4f}, no_speech_or_burst')
        return FormatBreak(
            shot_id=shot.shot_id,
            start=shot.start,
            end=shot.end,
            kind='menu_or_loading',
            severity='probable_format_break',
            confidence=0.72,
            evidence=reasons,
        )

    return None


def _l1_similarity(vec_a: list[float], vec_b: list[float], scale: float) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.5
    diff = float(np.abs(np.asarray(vec_a, dtype=np.float32) - np.asarray(vec_b, dtype=np.float32)).mean())
    return max(0.0, min(1.0, 1.0 - diff / scale))


def _hist_similarity(hist_a: list[float], hist_b: list[float]) -> float:
    if not hist_a or not hist_b or len(hist_a) != len(hist_b):
        return 0.5
    a = np.asarray(hist_a, dtype=np.float32)
    b = np.asarray(hist_b, dtype=np.float32)
    # Sum of L1 across H, S, V normalized histograms lies in [0, 6]; typical same-scene < 1.2
    l1 = float(np.abs(a - b).sum())
    return max(0.0, min(1.0, 1.0 - l1 / 2.6))


def score_adjacent_shots(
    shot_a: Shot,
    shot_b: Shot,
    break_a: FormatBreak | None,
    break_b: FormatBreak | None,
    transcript: dict,
    content_category: str = 'auto',
) -> ShotEdge:
    """Estimate semantic & visual continuity between adjacent shots."""
    cut_time = shot_b.start
    segments = transcript.get('segments', [])
    speech_crosses = any(
        seg.get('start', 0) < cut_time - 0.15 and seg.get('end', 0) > cut_time + 0.15
        for seg in segments
    )

    hsv_sim = _hist_similarity(shot_a.hsv_hist, shot_b.hsv_hist)
    grid_sim = _l1_similarity(shot_a.spatial_grid, shot_b.spatial_grid, scale=0.28)
    hud_sim = _l1_similarity(shot_a.corner_hud, shot_b.corner_hud, scale=0.26)
    luma_shift = min(1.0, abs(shot_a.mean_luma - shot_b.mean_luma) / 140.0)
    visual_sim = round(0.55 * hsv_sim + 0.45 * grid_sim, 4)

    # Hard gate on format breaks
    if break_b is not None:
        rel_map: dict[str, EdgeRelation] = {
            'end_card': 'format_break_end_card',
            'title_card': 'format_break_title_card',
            'graphic_stinger': 'format_break_title_card',
            'interstitial': 'format_break_interstitial',
            'death_fail_screen': 'format_break_death_fail',
            'black_screen': 'format_break_fade',
            'menu_or_loading': 'format_break_menu',
            'advertisement': 'format_break_title_card',
        }
        return ShotEdge(
            from_shot=shot_a.shot_id,
            to_shot=shot_b.shot_id,
            boundary_time=cut_time,
            same_event=False,
            relation=rel_map.get(break_b.kind, 'format_break_title_card'),
            severity=break_b.severity,
            continuity_score=0.0,
            visual_similarity=visual_sim,
            hud_similarity=round(hud_sim, 4),
            luma_shift=round(luma_shift, 4),
            speech_crosses_cut=speech_crosses,
            reason=f'Next shot classified as {break_b.kind}',
        )

    if break_a is not None:
        return ShotEdge(
            from_shot=shot_a.shot_id,
            to_shot=shot_b.shot_id,
            boundary_time=cut_time,
            same_event=False,
            relation='format_break_fade' if break_a.kind == 'black_screen' else 'format_break_title_card',
            severity=break_a.severity,
            continuity_score=0.0,
            visual_similarity=visual_sim,
            hud_similarity=round(hud_sim, 4),
            luma_shift=round(luma_shift, 4),
            speech_crosses_cut=speech_crosses,
            reason=f'Previous shot was format break ({break_a.kind})',
        )

    # Facecam consistency signal
    fc_a, fc_b = set(shot_a.face_corners), set(shot_b.face_corners)
    facecam_delta = 0.0
    facecam_contradiction = False
    if fc_a and fc_b:
        if fc_a & fc_b:
            facecam_delta = 0.14
        else:
            facecam_delta = -0.25
            facecam_contradiction = True
    elif bool(fc_a) != bool(fc_b) and min(shot_a.duration, shot_b.duration) >= 3.0 and max(shot_a.duration, shot_b.duration) >= 4.0:
        # Streamer facecam appearing or disappearing between substantial shots indicates compilation cut
        facecam_delta = -0.20
        facecam_contradiction = True

    # Audio continuity is supportive ONLY, never overrides strong visual contradiction
    audio_delta = 0.0
    if speech_crosses and visual_sim >= 0.42:
        audio_delta = 0.12
    elif shot_a.speech_word_count > 0 and shot_b.speech_word_count > 0 and visual_sim >= 0.50:
        # Both shots carry ongoing speech in visually compatible scene
        audio_delta = 0.08

    # Podcast / interview camera-switching rule: same conversation across speaker cuts
    if content_category == 'podcast' and (speech_crosses or (shot_a.speech_word_count >= 4 and shot_b.speech_word_count >= 4)):
        if luma_shift < 0.55 and visual_sim >= 0.25:
            score = min(1.0, max(0.62, 0.55 + 0.30 * visual_sim + 0.15 * (1.0 - luma_shift)))
            return ShotEdge(
                from_shot=shot_a.shot_id,
                to_shot=shot_b.shot_id,
                boundary_time=cut_time,
                same_event=True,
                relation='same_scene_camera_cut',
                severity='none',
                continuity_score=round(score, 4),
                visual_similarity=visual_sim,
                hud_similarity=round(hud_sim, 4),
                luma_shift=round(luma_shift, 4),
                speech_crosses_cut=speech_crosses,
                reason='Continuous podcast/interview dialogue across camera cut',
            )

    raw_score = (
        0.42 * visual_sim
        + 0.34 * hud_sim
        + 0.24 * (1.0 - luma_shift)
        + facecam_delta
        + audio_delta
    )
    score = max(0.0, min(1.0, raw_score))

    # Strong contradiction check: abrupt lighting + visual + HUD jump (e.g. car -> ocean -> night street)
    strong_visual_contradiction = (
        facecam_contradiction
        or (visual_sim < 0.48 and hud_sim < 0.62)
        or (luma_shift >= 0.35 and visual_sim < 0.62)
        or (hud_sim < 0.50 and visual_sim < 0.68)
        or (shot_b.boundary_type in ('fade_black', 'graphic_transition'))
    )

    if strong_visual_contradiction or score < 0.70:
        severity: BreakSeverity = 'strong_semantic_contradiction' if (facecam_contradiction or visual_sim < 0.45 or luma_shift >= 0.35) else 'weak_discontinuity'
        reason_msg = (
            f'Facecam layout changed across cut ({sorted(fc_a)} -> {sorted(fc_b)})'
            if facecam_contradiction
            else f'Visual/HUD/lighting shift (vis={visual_sim:.2f}, hud={hud_sim:.2f}, luma_shift={luma_shift:.2f})'
        )
        return ShotEdge(
            from_shot=shot_a.shot_id,
            to_shot=shot_b.shot_id,
            boundary_time=cut_time,
            same_event=False,
            relation='probable_new_event',
            severity=severity,
            continuity_score=round(score, 4),
            visual_similarity=visual_sim,
            hud_similarity=round(hud_sim, 4),
            luma_shift=round(luma_shift, 4),
            speech_crosses_cut=speech_crosses,
            reason=reason_msg,
        )

    relation: EdgeRelation = 'same_action_continuation' if score >= 0.70 else 'same_scene_camera_cut'
    return ShotEdge(
        from_shot=shot_a.shot_id,
        to_shot=shot_b.shot_id,
        boundary_time=cut_time,
        same_event=True,
        relation=relation,
        severity='none' if score >= 0.68 else 'unknown_continuity',
        continuity_score=round(score, 4),
        visual_similarity=visual_sim,
        hud_similarity=round(hud_sim, 4),
        luma_shift=round(luma_shift, 4),
        speech_crosses_cut=speech_crosses,
        reason=f'Consistent appearance and HUD across cut (vis={visual_sim:.2f}, hud={hud_sim:.2f})',
    )


def build_event_graph(
    evidence: SourceEvidence,
    transcript: dict,
    local_source_path: Path | None = None,
    vlm: LocalVisionAdapter | None = None,
    vlm_edge_budget: int = 4,
) -> EventGraph:
    """Build the complete temporal scene graph, format breaks, and semantic events."""
    shots = evidence.shots
    duration = evidence.context.duration
    category = evidence.context.content_category

    # 1. Detect format breaks on every shot
    breaks_by_shot: dict[int, FormatBreak] = {}
    for idx, shot in enumerate(shots):
        br = classify_format_break(
            shot,
            source_duration=duration,
            content_category=category,
            is_last_shot=(idx == len(shots) - 1),
        )
        if br is not None:
            breaks_by_shot[shot.shot_id] = br

    # Optional VLM verification of borderline tail shots (catches subtle end cards)
    if vlm and vlm.is_available() and local_source_path and local_source_path.is_file() and shots:
        tail_shots = [s for s in shots[-3:] if s.shot_id not in breaks_by_shot and s.duration >= 2.0 and s.static_fraction >= 0.45]
        for sh in tail_shots[:1]:
            mid_t = evidence.context.start + (sh.start + sh.end) / 2.0
            imgs = [b for _, b in extract_frames_at_timestamps(local_source_path, [mid_t], max_width=320)]
            if imgs:
                verdict = vlm.detect_format_break(imgs, {'shot_id': sh.shot_id, 'tail': True})
                if verdict.verified and verdict.is_format_break and verdict.break_kind != 'none':
                    breaks_by_shot[sh.shot_id] = FormatBreak(
                        shot_id=sh.shot_id,
                        start=sh.start,
                        end=sh.end,
                        kind=verdict.break_kind,  # type: ignore[arg-type]
                        severity='probable_format_break',
                        confidence=verdict.confidence,
                        evidence=[f'VLM ({verdict.backend}): {verdict.reason}'],
                        vlm_verified=True,
                    )

    # 2. Score adjacent shot edges
    edges: list[ShotEdge] = []
    uncertain_edge_indices: list[int] = []
    for i in range(len(shots) - 1):
        sa, sb = shots[i], shots[i + 1]
        edge = score_adjacent_shots(
            sa, sb,
            breaks_by_shot.get(sa.shot_id),
            breaks_by_shot.get(sb.shot_id),
            transcript,
            content_category=category,
        )
        edges.append(edge)
        # Speech spanning the cut can corroborate a changed viewpoint or a transition
        # into a cutscene. Keep fades and graphic transitions out of that path.
        if (
            sa.shot_id not in breaks_by_shot
            and sb.shot_id not in breaks_by_shot
            and sb.boundary_type not in ('fade_black', 'graphic_transition')
            and not edge.reason.startswith('Facecam layout changed')
            and (
                (sa.duration >= 4 and sb.duration >= 4 and 0.62 <= edge.continuity_score <= 0.80)
                or (edge.speech_crosses_cut and min(sa.duration, sb.duration) >= 1
                    and edge.continuity_score >= 0.42 and not edge.same_event)
            )
        ):
            uncertain_edge_indices.append(len(edges) - 1)

    # 3. Spend bounded VLM budget ONLY on uncertain edges between substantial shots
    if vlm and vlm.is_available() and local_source_path and local_source_path.is_file() and vlm_edge_budget > 0:
        # Prioritize uncertain edges with longest combined duration (most likely to form top candidates)
        uncertain_edge_indices.sort(
            key=lambda idx: (
                edges[idx].same_event, not edges[idx].speech_crosses_cut,
                -(shots[idx].duration + shots[idx + 1].duration),
            )
        )
        for idx in uncertain_edge_indices[:vlm_edge_budget]:
            sa, sb = shots[idx], shots[idx + 1]
            t_a = evidence.context.start + max(sa.start, sa.end - 0.8)
            t_b = evidence.context.start + min(sb.end, sb.start + 0.8)
            fa = [b for _, b in extract_frames_at_timestamps(local_source_path, [t_a], max_width=320)]
            fb = [b for _, b in extract_frames_at_timestamps(local_source_path, [t_b], max_width=320)]
            if fa and fb:
                verdict = vlm.compare_adjacent_shots(fa, fb, {'edge': [sa.shot_id, sb.shot_id]})
                if verdict.verified and verdict.relation != 'uncertain':
                    edge = edges[idx]
                    edge.vlm_verified = True
                    edge.reason = f'VLM ({verdict.backend}): {verdict.reason}'
                    if verdict.relation == 'format_break':
                        edge.same_event = False
                        edge.relation = 'format_break_title_card'
                        edge.severity = 'probable_format_break'
                        edge.continuity_score = 0.0
                    elif verdict.same_event:
                        corroborated = (
                            (edge.visual_similarity >= 0.58 and edge.hud_similarity >= 0.62)
                            or (edge.speech_crosses_cut and verdict.relation in ('same_scene_camera_cut', 'reaction_cut'))
                        )
                        if edge.same_event or (verdict.confidence >= 0.88 and corroborated
                                               and bool(verdict.reason.strip())):
                            edge.same_event = True
                            edge.relation = verdict.relation  # type: ignore[assignment]
                            edge.severity = 'none'
                            edge.continuity_score = max(edge.continuity_score, round(verdict.confidence, 4))
                        else:
                            edge.reason += ' (insufficient corroboration to join the shots)'
                    else:
                        edge.same_event = False
                        edge.relation = 'probable_new_event'
                        edge.severity = 'strong_semantic_contradiction'
                        edge.continuity_score = min(edge.continuity_score, 0.25)

    # 4. Group contiguous non-break shots connected by same_event edges into SemanticEvents
    events: list[SemanticEvent] = []
    current_group: list[Shot] = []
    current_edges: list[ShotEdge] = []

    def flush_group(preceded_break: str | None, terminated_break: str | None):
        if not current_group:
            return
        ev = _materialize_event(
            len(events) + 1,
            current_group,
            current_edges,
            evidence,
            transcript,
            preceded_break,
            terminated_break,
        )
        events.append(ev)

    preceded: str | None = None
    for idx, shot in enumerate(shots):
        br = breaks_by_shot.get(shot.shot_id)
        if br is not None:
            flush_group(preceded, br.kind)
            current_group = []
            current_edges = []
            preceded = br.kind
            continue

        if not current_group:
            current_group = [shot]
            current_edges = []
        else:
            edge = edges[idx - 1]
            if edge.same_event:
                current_group.append(shot)
                current_edges.append(edge)
            else:
                flush_group(preceded, None)
                current_group = [shot]
                current_edges = []
                preceded = None

    flush_group(preceded, None)

    return EventGraph(
        source_fingerprint=evidence.context.source_fingerprint,
        duration=duration,
        format_breaks=sorted(breaks_by_shot.values(), key=lambda b: b.start),
        edges=edges,
        events=events,
        vlm_stats=vlm.stats() if vlm else {},
    )


def _materialize_event(
    index: int,
    group: list[Shot],
    group_edges: list[ShotEdge],
    evidence: SourceEvidence,
    transcript: dict,
    preceded_by_break: str | None,
    terminated_by_break: str | None,
) -> SemanticEvent:
    e_start = group[0].start
    e_end = group[-1].end
    e_dur = max(0.05, e_end - e_start)

    mean_cont = float(np.mean([e.continuity_score for e in group_edges])) if group_edges else 1.0
    total_dur = sum(s.duration for s in group) or 1.0
    mean_mot = sum(s.mean_motion * s.duration for s in group) / total_dur
    peak_mot = max(s.peak_motion for s in group)
    mean_rms = sum(s.mean_audio_rms * s.duration for s in group) / total_dur
    peak_rms = max(s.peak_audio_rms for s in group)
    bursts = sum(s.audio_burst_count for s in group)
    # Only report persistent facecam corners (present in >=50% of the event's shots)
    corner_hits: dict[str, int] = {}
    for s in group:
        for c in s.face_corners:
            corner_hits[c] = corner_hits.get(c, 0) + 1
    face_corners = sorted(c for c, cnt in corner_hits.items() if cnt >= max(1, (len(group) + 1) // 2))

    # Extract speech segments strictly inside this event
    segs = [
        seg for seg in transcript.get('segments', [])
        if seg.get('end', 0) > e_start and seg.get('start', 0) < e_end
    ]
    speech_text = ' '.join(str(seg.get('text', '')).strip() for seg in segs).strip()
    word_count = len(speech_text.split()) if speech_text else 0
    has_reaction = any(s.has_reaction_cue for s in group)

    # Infer supported setup / action / climax / reaction / payoff timestamps (never fabricate!)
    setup_time = round(e_start, 3)

    # Sample-level climax detection inside [e_start, e_end]
    ev_samples = [
        s for s in evidence.coarse_samples
        if e_start <= s['time'] < e_end
    ]
    ev_audio = [
        a for a in evidence.audio_events
        if e_start <= a.time < e_end
    ]
    action_start: float | None = None
    climax_time: float | None = None
    if ev_samples:
        med_mot = float(np.median([s['motion'] for s in ev_samples]))
        for s in ev_samples:
            if s['motion'] >= max(0.04, med_mot * 0.9) or s.get('audio_change', 0) >= 0.04:
                action_start = round(float(s['time']), 3)
                break
        best_s = max(
            ev_samples,
            key=lambda s: s['motion'] + 1.4 * min(0.15, s.get('audio_change', 0)) + 0.5 * min(0.2, s.get('audio_rms', 0)),
        )
        if best_s['motion'] >= 0.03 or best_s.get('audio_change', 0) >= 0.03:
            climax_time = round(float(best_s['time']), 3)

    # Reaction time: spoken reaction cue or audio burst near/after climax
    from .source_intelligence import REACTION_PATTERNS
    reaction_time: float | None = None
    explicit_react = [seg for seg in segs if REACTION_PATTERNS.search(str(seg.get('text', ''))) or '!' in str(seg.get('text', ''))]
    if explicit_react:
        reaction_time = round(float(explicit_react[-1]['start']), 3)
    elif climax_time is not None:
        react_segs = [
            seg for seg in segs
            if climax_time - 1.0 <= seg.get('start', 0) <= climax_time + 5.0
        ]
        if react_segs:
            reaction_time = round(float(react_segs[0]['start']), 3)
        else:
            react_audio = [a for a in ev_audio if climax_time <= a.time <= climax_time + 4.0 and a.is_burst]
            if react_audio:
                reaction_time = round(float(react_audio[0].time), 3)

    # Payoff time: supported only if reaction, death_fail_screen ending, or closing speech exists
    payoff_time: float | None = None
    if terminated_by_break == 'death_fail_screen':
        payoff_time = round(e_end, 3)
    elif reaction_time is not None:
        payoff_time = reaction_time
    elif segs and segs[-1].get('end', 0) >= e_start + e_dur * 0.55:
        payoff_time = round(min(e_end, float(segs[-1]['end'])), 3)
    elif climax_time is not None and climax_time >= e_start + e_dur * 0.45:
        payoff_time = round(min(e_end, climax_time + 2.0), 3)

    evidence_notes = [
        f'{len(group)} coherent shot(s) ({e_start:.1f}-{e_end:.1f}s, {e_dur:.1f}s), mean continuity {mean_cont:.2f}',
    ]
    if word_count:
        evidence_notes.append(f'{word_count} spoken words' + (' with reaction cues' if has_reaction else ''))
    if bursts:
        evidence_notes.append(f'{bursts} audio energy bursts (peak RMS {peak_rms:.3f})')
    if face_corners:
        evidence_notes.append(f'Detected facecam in {", ".join(face_corners)}')
    if terminated_by_break:
        evidence_notes.append(f'Cleanly bounded before {terminated_by_break} at {e_end:.1f}s')
    if preceded_by_break:
        evidence_notes.append(f'Starts cleanly after {preceded_by_break} at {e_start:.1f}s')

    defects: list[str] = []
    if e_dur < 8.0:
        defects.append('very_short_event')
    if mean_mot < 0.015 and word_count < 6:
        defects.append('low_activity_and_speech')
    if climax_time is None and word_count < 6:
        defects.append('no_clear_climax_or_dialogue')

    decision: CandidateDecision = 'ACCEPT_WITH_REVIEW'
    if 'low_activity_and_speech' in defects and e_dur < 10.0:
        decision = 'REJECT'

    return SemanticEvent(
        event_id=f'ev-{index:03d}',
        start=round(e_start, 3),
        end=round(e_end, 3),
        duration=round(e_dur, 3),
        shot_ids=[s.shot_id for s in group],
        shot_count=len(group),
        mean_continuity=round(mean_cont, 4),
        setup_time=setup_time,
        action_start_time=action_start,
        climax_time=climax_time,
        reaction_time=reaction_time,
        payoff_time=payoff_time,
        natural_end_time=round(e_end, 3),
        terminated_by_break=terminated_by_break,
        preceded_by_break=preceded_by_break,
        speech_text=speech_text[:800],
        speech_word_count=word_count,
        has_reaction_cue=has_reaction,
        mean_motion=round(mean_mot, 5),
        peak_motion=round(peak_mot, 5),
        mean_audio_rms=round(mean_rms, 5),
        peak_audio_rms=round(peak_rms, 5),
        audio_burst_count=bursts,
        face_corners=face_corners,
        evidence=evidence_notes[:8],
        defect_flags=defects,
        decision=decision,
        uncertainty=round(max(0.15, 1.0 - mean_cont) if len(group) > 1 else 0.2, 3),
    )


def refine_event_boundaries(
    event: SemanticEvent,
    minimum: float,
    maximum: float,
    transcript: dict,
    source_duration: float,
) -> dict[str, Any]:
    """Refine clip bounds strictly INSIDE a single coherent SemanticEvent.

    Anti-padding rule: if event.duration < minimum, NEVER pad with unrelated
    neighboring footage. Return status='duration_conflict' with the clean
    natural bounds as an operator-reviewable alternative.
    """
    e_start, e_end = event.start, min(source_duration, event.end)
    e_dur = e_end - e_start
    segs = [
        s for s in transcript.get('segments', [])
        if s.get('end', 0) > e_start and s.get('start', 0) < e_end
    ]

    if e_dur < minimum - 1e-6:
        return {
            'status': 'duration_conflict',
            'start_seconds': round(e_start, 3),
            'end_seconds': round(e_end, 3),
            'duration': round(e_dur, 3),
            'reason': (
                f'Coherent event lasts {e_dur:.1f}s, shorter than requested {minimum:g}s minimum. '
                'Unrelated adjacent footage was NOT padded into this clip; approve duration override to render.'
            ),
            'duration_conflict': {'event_duration': round(e_dur, 3), 'requested_minimum': minimum},
        }

    # Case 1: Event naturally fits [minimum, maximum]
    if minimum - 1e-6 <= e_dur <= maximum + 1e-6:
        # Trim trailing silence/stray cut-off word if a clean sentence ends just before a break
        start, end = e_start, e_end
        if segs and event.terminated_by_break == 'end_card':
            # Keep strictly before end_card with small safety margin (0.2s)
            end = max(start + minimum, min(end, e_end - 0.2))
        return {
            'status': 'valid',
            'start_seconds': round(start, 3),
            'end_seconds': round(end, 3),
            'duration': round(end - start, 3),
            'reason': 'Natural coherent event boundaries preserved.',
        }

    # Case 2: Event is LONGER than maximum -> select the strongest sub-window inside [e_start, e_end]
    preferred = min(maximum, max(minimum, min(52.0, maximum * 0.85)))
    # Anchor around payoff + climax + dialogue density
    anchor_end = event.payoff_time or event.climax_time or e_end
    if segs:
        # Prefer covering the densest dialogue + payoff tail (e.g., truck swap dialogue 554-597.8s!)
        last_speech_end = min(e_end, float(segs[-1]['end']))
        if last_speech_end - e_start >= minimum:
            anchor_end = last_speech_end

    # Trim slight safety buffer before end_card
    safe_e_end = e_end - 0.2 if event.terminated_by_break == 'end_card' else e_end
    end = min(safe_e_end, max(e_start + minimum, anchor_end + 1.5))
    start = max(e_start, end - preferred)
    if end - start < minimum:
        end = min(safe_e_end, start + minimum)
    if end - start > maximum:
        start = end - maximum

    # Snap start/end gently to nearby sentence starts/ends strictly inside [e_start, safe_e_end]
    seg_starts = [float(s['start']) for s in segs if e_start <= float(s['start']) <= safe_e_end]
    seg_ends = [float(s['end']) for s in segs if e_start <= float(s['end']) <= safe_e_end]

    near_starts = [t for t in seg_starts if abs(t - start) <= 6.0 and minimum <= end - t <= maximum]
    if near_starts:
        start = min(near_starts, key=lambda t: abs(t - start))
    near_ends = [t for t in seg_ends if abs(t - end) <= 3.0 and t <= safe_e_end and minimum <= t - start <= maximum]
    if near_ends:
        end = min(near_ends, key=lambda t: abs(t - end))

    # Final clamp ensuring strict validity
    start = max(e_start, min(start, safe_e_end - minimum))
    end = min(safe_e_end, max(start + minimum, min(start + maximum, end)))
    return {
        'status': 'valid',
        'start_seconds': round(start, 3),
        'end_seconds': round(end, 3),
        'duration': round(end - start, 3),
        'reason': f'Refined {end - start:.1f}s peak window strictly inside coherent {e_dur:.1f}s event.',
    }
