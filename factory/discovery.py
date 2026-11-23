"""Multi-Stage Editorial Highlight Discovery & Ranking Engine (Phase 5).

Pipeline architecture (clean-room Map -> Pool -> Verify -> Score -> Pairwise Rank -> Select):
1. MAP: Independently propose candidates from SemanticEventGraph, Transcript/LLM
   windows, Audio/Reaction bursts, and Visual Climax anchors without forcing
   empty windows to emit clips.
2. POOL: Merge and deduplicate overlapping proposals while preserving provenance
   and ensuring a wider noisy span never suppresses a clean coherent event.
3. VERIFY: Enforce format-break exclusion, shot-graph continuity checks,
   anti-padding boundary refinement, and bounded VLM visual verification on top
   finalists (adaptive inference budget).
4. SCORE: Compute an explicit 12-dimension editorial rubric with category
   profiles (gameplay, stream, podcast, visual, auto) and evidence-backed
   contamination/cutoff penalties.
5. PAIRWISE RANK: Mitigate LLM position bias via order-swapped (A vs B and
   B vs A) pairwise comparison among top finalists when Ollama is available.
6. SELECT: Choose diverse, non-overlapping highlights; return fewer than K with
   honest explanations and review_alternatives when fewer publishable events exist.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from . import config
from .inference import generate, model_session
from .semantic_events import (
    EventGraph,
    FormatBreak,
    SemanticEvent,
    refine_event_boundaries,
)
from .source_intelligence import SourceEvidence, extract_frames_at_timestamps
from .vision_vlm import LocalVisionAdapter


class EditorialRubric(BaseModel):
    event_significance: float = Field(ge=0, le=1)
    hook_potential: float = Field(ge=0, le=1)
    narrative_coherence: float = Field(ge=0, le=1)
    payoff_quality: float = Field(ge=0, le=1)
    visual_interest: float = Field(ge=0, le=1)
    context_sufficiency: float = Field(ge=0, le=1)
    pacing: float = Field(ge=0, le=1)
    standalone_value: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    editability: float = Field(ge=0, le=1)
    contamination_risk: float = Field(ge=0, le=1)  # 0 = clean, 1 = contaminated
    claim_reliability: float = Field(ge=0, le=1)


CATEGORY_WEIGHTS: dict[str, dict[str, float]] = {
    'gameplay': {
        'event_significance': 16.0,
        'hook_potential': 10.0,
        'narrative_coherence': 18.0,
        'payoff_quality': 13.0,
        'visual_interest': 12.0,
        'context_sufficiency': 6.0,
        'pacing': 8.0,
        'standalone_value': 7.0,
        'novelty': 4.0,
        'editability': 4.0,
        'claim_reliability': 2.0,
    },
    'stream': {
        'event_significance': 15.0,
        'hook_potential': 11.0,
        'narrative_coherence': 16.0,
        'payoff_quality': 14.0,
        'visual_interest': 10.0,
        'context_sufficiency': 8.0,
        'pacing': 8.0,
        'standalone_value': 8.0,
        'novelty': 4.0,
        'editability': 4.0,
        'claim_reliability': 2.0,
    },
    'podcast': {
        'event_significance': 16.0,
        'hook_potential': 14.0,
        'narrative_coherence': 16.0,
        'payoff_quality': 12.0,
        'visual_interest': 4.0,
        'context_sufficiency': 12.0,
        'pacing': 8.0,
        'standalone_value': 10.0,
        'novelty': 4.0,
        'editability': 2.0,
        'claim_reliability': 2.0,
    },
    'visual': {
        'event_significance': 16.0,
        'hook_potential': 12.0,
        'narrative_coherence': 18.0,
        'payoff_quality': 11.0,
        'visual_interest': 16.0,
        'context_sufficiency': 4.0,
        'pacing': 9.0,
        'standalone_value': 6.0,
        'novelty': 4.0,
        'editability': 2.0,
        'claim_reliability': 2.0,
    },
    'auto': {
        'event_significance': 16.0,
        'hook_potential': 11.0,
        'narrative_coherence': 17.0,
        'payoff_quality': 13.0,
        'visual_interest': 10.0,
        'context_sufficiency': 8.0,
        'pacing': 8.0,
        'standalone_value': 7.0,
        'novelty': 4.0,
        'editability': 4.0,
        'claim_reliability': 2.0,
    },
}


class DiscoveryBudget(BaseModel):
    max_coarse_candidates: int = Field(default=60, ge=5, le=200)
    max_fine_inspected: int = Field(default=12, ge=1, le=40)
    max_vlm_verified: int = Field(default=6, ge=0, le=20)
    max_pairwise_finalists: int = Field(default=5, ge=0, le=10)


def _overlap_sec(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _interval_iou(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = _overlap_sec(a0, a1, b0, b1)
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def _check_format_break_overlap(start: float, end: float, breaks: list[FormatBreak]) -> list[FormatBreak]:
    hits = []
    for br in breaks:
        # A brief death_fail_screen strictly at the very end (<0.5s inside) is acceptable payoff;
        # title_card, end_card, interstitial, black_screen, menu_or_loading are contamination.
        if br.kind == 'death_fail_screen':
            if _overlap_sec(start, end, br.start, br.end) >= 1.5 and br.start < end - 2.0:
                hits.append(br)
        elif _overlap_sec(start, end, br.start, br.end) >= 0.4:
            hits.append(br)
    return hits


def _score_candidate(
    cand: dict[str, Any],
    event: SemanticEvent | None,
    graph: EventGraph,
    transcript: dict,
    category: str,
    minimum: float,
    maximum: float,
) -> tuple[float, EditorialRubric, list[str]]:
    start, end = float(cand['start_seconds']), float(cand['end_seconds'])
    dur = max(0.1, end - start)
    segs = [s for s in transcript.get('segments', []) if s.get('end', 0) > start and s.get('start', 0) < end]
    speech = ' '.join(str(s.get('text', '')).strip() for s in segs).strip()
    words = len(speech.split()) if speech else 0

    break_hits = _check_format_break_overlap(start, end, graph.format_breaks)
    # Count distinct non-break events substantially overlapped (>=3.5s)
    spanned_events = [e for e in graph.events if _overlap_sec(start, end, e.start, e.end) >= 3.5]

    penalties: list[str] = []
    contamination = 0.0
    if break_hits:
        contamination = min(1.0, contamination + 0.65 * len(break_hits))
        penalties.append(f'format_break_contamination:{",".join(sorted({b.kind for b in break_hits}))}')
    if len(spanned_events) >= 2:
        contamination = min(1.0, contamination + 0.55)
        penalties.append(f'unrelated_events_spanned:{len(spanned_events)}')
    if cand.get('vlm_contains_format_break'):
        contamination = min(1.0, contamination + 0.70)
        penalties.append('vlm_confirmed_format_break')
    if cand.get('vlm_contains_unrelated_scene'):
        contamination = min(1.0, contamination + 0.60)
        penalties.append('vlm_confirmed_unrelated_scene')

    # Narrative coherence
    if event is not None and len(spanned_events) <= 1 and not break_hits:
        coherence = max(0.45, min(1.0, event.mean_continuity))
        if cand.get('vlm_verified') and cand.get('vlm_coherent'):
            coherence = min(1.0, max(coherence, 0.90))
    else:
        coherence = max(0.05, 0.35 - 0.25 * max(0, len(spanned_events) - 1))

    # Event significance (activity + bursts + speech + climax)
    mot = event.mean_motion if event else float(cand.get('mean_motion', 0.05))
    peak_mot = event.peak_motion if event else mot * 1.4
    bursts = event.audio_burst_count if event else 0
    has_react = bool(event.has_reaction_cue if event else False)
    has_climax = bool(event and event.climax_time is not None)
    sig = min(1.0, 0.22 + 2.2 * min(0.18, mot) + 0.14 * min(3, bursts) / 3.0 + (0.20 if words >= 15 else 0.08 * min(1.0, words / 15.0)) + (0.12 if has_react else 0.0))

    # Hook potential (activity or speech in first 6s)
    early_speech = any(s.get('start', 0) < start + 6.0 and s.get('end', 0) > start for s in segs)
    hook_pot = min(1.0, 0.35 + (0.28 if early_speech else 0.0) + (0.22 if peak_mot >= 0.12 else 0.10) + (0.15 if has_react else 0.0))

    # Payoff quality
    has_payoff = bool(event and event.payoff_time is not None) or bool(cand.get('payoff'))
    complete_sent = bool(speech and speech.rstrip().endswith(('.', '!', '?', '…')))
    payoff_q = min(1.0, 0.25 + (0.35 if has_payoff else 0.0) + (0.20 if has_react else 0.0) + (0.15 if complete_sent else 0.0) + (0.15 if event and event.terminated_by_break == 'death_fail_screen' else 0.0))
    if not has_payoff and words == 0:
        penalties.append('missing_clear_payoff')

    # Visual interest
    vis_int = min(1.0, 0.25 + 3.0 * min(0.18, mot) + 0.8 * min(0.25, peak_mot) + (0.12 if event and event.face_corners else 0.0))

    # Context sufficiency & standalone value
    ctx_suff = min(1.0, 0.35 + (0.30 if words >= 12 else 0.10) + (0.20 if event and event.setup_time is not None else 0.0) + (0.15 if cand.get('vlm_verified') else 0.0))
    standalone = min(1.0, 0.30 + 0.40 * coherence + 0.20 * payoff_q + (0.10 if complete_sent or category in ('gameplay', 'visual') else 0.0))

    # Pacing (duration fit + non-static action)
    preferred = min(maximum, max(minimum, 44.0))
    dur_fit = max(0.35, 1.0 - abs(dur - preferred) / max(30.0, maximum))
    pacing = min(1.0, 0.55 * dur_fit + 0.45 * min(1.0, mot / 0.10))

    # Editability (single coherent event, facecam or clean widescreen)
    editability = 0.85 if (event and event.face_corners) else 0.78 if coherence >= 0.7 else 0.45

    # Claim reliability (higher when grounded in VLM or direct transcript)
    claim_rel = 0.90 if cand.get('vlm_verified') else (0.72 if words >= 8 else 0.58)
    novelty = 0.75

    rubric = EditorialRubric(
        event_significance=round(sig, 4),
        hook_potential=round(hook_pot, 4),
        narrative_coherence=round(coherence, 4),
        payoff_quality=round(payoff_q, 4),
        visual_interest=round(vis_int, 4),
        context_sufficiency=round(ctx_suff, 4),
        pacing=round(pacing, 4),
        standalone_value=round(standalone, 4),
        novelty=round(novelty, 4),
        editability=round(editability, 4),
        contamination_risk=round(contamination, 4),
        claim_reliability=round(claim_rel, 4),
    )

    weights = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS['auto'])
    raw = sum(getattr(rubric, dim) * w for dim, w in weights.items())
    # Explicit contamination penalty (heavily demotes end-card or multi-clip merges!)
    penalty_points = 55.0 * rubric.contamination_risk
    if 'missing_clear_payoff' in penalties:
        penalty_points += 4.0
    final_score = round(max(0.0, min(100.0, raw - penalty_points)), 2)
    return final_score, rubric, penalties


def _pairwise_rank_finalists(
    finalists: list[dict[str, Any]],
    model: str | None,
    ollama_url: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Order-swapped (A vs B and B vs A) pairwise comparison to mitigate LLM position bias."""
    if len(finalists) < 2 or not model or os.environ.get('FACTORY_PAIRWISE_LLM', '1') == '0':
        return finalists, {'pairwise_used': False, 'reason': 'skipped_or_single_finalist'}

    url = (ollama_url or config.OLLAMA).rstrip('/') + '/api/generate'
    wins = {i: 0.0 for i in range(len(finalists))}
    comparisons = []
    calls = 0

    def brief_card(c: dict[str, Any]) -> dict[str, Any]:
        return {
            'duration_s': round(c['end_seconds'] - c['start_seconds'], 1),
            'shots': c.get('shot_count', 1),
            'coherence': c.get('editorial_rubric', {}).get('narrative_coherence', 0),
            'contamination_risk': c.get('editorial_rubric', {}).get('contamination_risk', 0),
            'vlm_summary': c.get('vlm_summary', '')[:220],
            'transcript_excerpt': c.get('speech_text', '')[:240],
            'evidence': c.get('evidence', [])[:4],
            'penalties': c.get('penalties', []),
        }

    schema = {
        'type': 'object',
        'required': ['winner', 'reason'],
        'properties': {
            'winner': {'type': 'string', 'enum': ['A', 'B', 'TIE']},
            'reason': {'type': 'string', 'maxLength': 200},
        },
    }

    try:
        with httpx.Client(timeout=45.0, trust_env=False) as client, model_session(client, url, model) as session:
            # Round-robin over top finalists (bounded: at most 5 finalists -> up to 6 adjacent/top pairs * 2 orders)
            pairs = [(i, j) for i in range(len(finalists)) for j in range(i + 1, len(finalists))][:6]
            for i, j in pairs:
                votes = []
                for first_idx, second_idx in ((i, j), (j, i)):
                    prompt = {
                        'instruction': (
                            'Compare Candidate A and Candidate B as standalone YouTube Shorts highlights. '
                            'Prefer ONE coherent event with clear setup/action/payoff, higher visual/speech interest, '
                            'and ZERO end-card or unrelated-scene contamination. Return winner ("A", "B", or "TIE") and concise reason.'
                        ),
                        'A': brief_card(finalists[first_idx]),
                        'B': brief_card(finalists[second_idx]),
                    }
                    calls += 1
                    resp = generate(
                        client,
                        url,
                        {
                            'model': model,
                            'prompt': json.dumps(prompt, ensure_ascii=False),
                            'format': schema,
                            'stream': False,
                            'keep_alive': 0,
                            'options': {'num_ctx': 2048, 'temperature': 0, 'num_predict': 120, 'num_thread': 2},
                        },
                        session=session,
                    )
                    parsed = json.loads(resp.json()['response'])
                    w = parsed.get('winner', 'TIE')
                    if w == 'A':
                        votes.append(first_idx)
                    elif w == 'B':
                        votes.append(second_idx)
                    else:
                        votes.append(None)
                # Position-bias check: consistent winner gets +1.0; split decision -> 0.5 each (TIE)
                if votes[0] is not None and votes[0] == votes[1]:
                    wins[votes[0]] += 1.0
                    comparisons.append({'pair': [i, j], 'winner': votes[0], 'position_consistent': True})
                else:
                    wins[i] += 0.5
                    wins[j] += 0.5
                    comparisons.append({'pair': [i, j], 'winner': 'TIE', 'position_consistent': False})
    except Exception as exc:
        return finalists, {'pairwise_used': False, 'reason': f'llm_unavailable:{type(exc).__name__}', 'calls': calls}

    for idx, c in enumerate(finalists):
        c['pairwise_wins'] = round(wins[idx], 2)
        # Blend pairwise win rate gently (+0..6 pts) without overriding hard contamination penalties
        if c.get('editorial_rubric', {}).get('contamination_risk', 0.0) == 0.0:
            c['score'] = round(min(100.0, c['score'] + 2.0 * wins[idx]), 2)

    reranked = sorted(finalists, key=lambda c: (-c['score'], -c.get('pairwise_wins', 0.0), c['start_seconds']))
    return reranked, {'pairwise_used': True, 'calls': calls, 'comparisons': comparisons}


import os


def discover_highlights(
    evidence: SourceEvidence,
    graph: EventGraph,
    transcript: dict,
    request: Any,
    local_source_path: Path | None = None,
    vlm: LocalVisionAdapter | None = None,
    budget: DiscoveryBudget | None = None,
    speech_proposals: list[dict[str, Any]] | None = None,
    activity_proposals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute Map -> Pool -> Verify -> Score -> Pairwise Rank -> Select."""
    budget = budget or DiscoveryBudget()
    duration = evidence.context.duration
    minimum = float(request.minimum)
    maximum = float(min(180.0, request.maximum))
    target_count = int(request.count)
    category = getattr(request, 'source_category', 'auto') or 'auto'
    language = transcript.get('language') or getattr(request, 'language', None) or 'und'

    # --- STAGE 1: MAP ---
    mapped: list[dict[str, Any]] = []
    review_alternatives: list[dict[str, Any]] = []
    rejected_log: list[dict[str, Any]] = []

    # 1a. Map from SemanticEventGraph (primary coherent event proposals)
    for ev in graph.events:
        if ev.decision == 'REJECT':
            rejected_log.append({
                'event_id': ev.event_id,
                'start_seconds': ev.start,
                'end_seconds': ev.end,
                'decision': 'REJECT',
                'reason': '; '.join(ev.defect_flags) or 'Low activity and no speech',
            })
            continue
        refined = refine_event_boundaries(ev, minimum, maximum, transcript, duration)
        base_entry = {
            'event_id': ev.event_id,
            'start_seconds': refined['start_seconds'],
            'end_seconds': refined['end_seconds'],
            'provenance': ['semantic_event_graph'],
            'event': ev,
            'shot_count': ev.shot_count,
            'speech_text': ev.speech_text,
            'mean_motion': ev.mean_motion,
            'setup': f'Event start at {ev.setup_time:.1f}s' if ev.setup_time is not None else '',
            'development': f'Climax around {ev.climax_time:.1f}s ({ev.shot_count} coherent shots)' if ev.climax_time is not None else '',
            'payoff': f'Payoff/reaction around {ev.payoff_time:.1f}s' if ev.payoff_time is not None else '',
            'setup_time': ev.setup_time,
            'climax_time': ev.climax_time,
            'payoff_time': ev.payoff_time,
            'reaction_time': ev.reaction_time,
            'natural_end_time': ev.natural_end_time,
            'face_corners': ev.face_corners,
            'evidence': list(ev.evidence),
            'boundary_note': refined['reason'],
            'content_type': 'speech' if ev.speech_word_count >= 15 else 'visual_activity',
            'language': language,
        }
        if refined['status'] == 'duration_conflict':
            # Anti-padding: preserve as an operator-reviewable short alternative if substantial (>=12s)
            if refined['duration'] >= 12.0:
                score, rubric, penalties = _score_candidate(base_entry, ev, graph, transcript, category, 12.0, maximum)
                review_alternatives.append({
                    'start_seconds': refined['start_seconds'],
                    'end_seconds': refined['end_seconds'],
                    'score': score,
                    'title': _grounded_candidate_title(ev, evidence.context.confirmed_subject, ''),
                    'language': language,
                    'reason': refined['reason'],
                    'confidence': round(ev.mean_continuity, 3),
                    'schema_version': 2,
                    'content_type': base_entry['content_type'],
                    'hook_start_seconds': refined['start_seconds'],
                    'setup': base_entry['setup'],
                    'development': base_entry['development'],
                    'payoff': base_entry['payoff'],
                    'setup_time': ev.setup_time,
                    'climax_time': ev.climax_time,
                    'payoff_time': ev.payoff_time,
                    'evidence': list(ev.evidence),
                    'narrative_completeness': round((int(bool(ev.setup_time is not None)) + int(bool(ev.climax_time is not None)) + int(bool(ev.payoff_time is not None))) / 3.0, 3),
                    'context_required': ev.speech_word_count < 12,
                    'visual_review_required': True,
                    'editorial_review_required': True,
                    'semantic_decision': 'REVISE_BOUNDARIES',
                    'duration_conflict': refined['duration_conflict'],
                    'editorial_rubric': rubric.model_dump(),
                    'penalties': penalties,
                    'event_id': ev.event_id,
                    'shot_count': ev.shot_count,
                    'face_corners': ev.face_corners,
                })
            continue
        mapped.append(base_entry)

    # External proposals are all checked against the semantic event graph.
    proposals = [
        *((item, 'transcript_proposal') for item in (speech_proposals or [])),
        *((item, 'activity_proposal') for item in (activity_proposals or [])),
    ]
    for sp, provenance in proposals:
        is_activity = provenance == 'activity_proposal'
        label = 'Activity' if is_activity else 'Speech'
        a, b = float(sp.get('start_seconds', 0)), float(sp.get('end_seconds', 0))
        if not (0 <= a < b <= duration):
            continue
        # Trim away any overlapping format break at boundaries before pooling
        trimmed_a, trimmed_b = a, b
        for br in graph.format_breaks:
            if br.start <= trimmed_a < br.end:
                trimmed_a = min(trimmed_b, br.end)
            if br.start < trimmed_b <= br.end:
                trimmed_b = max(trimmed_a, br.start - 0.15)
        if trimmed_b - trimmed_a < minimum:
            rejected_log.append({
                'start_seconds': a,
                'end_seconds': b,
                'decision': 'REJECT',
                'reason': f'{label} proposal contaminated by format break; remaining clean span below minimum duration.',
            })
            continue
        # Reject proposals that cross an interior format break or merge unrelated events
        interior_breaks = _check_format_break_overlap(trimmed_a, trimmed_b, graph.format_breaks)
        if interior_breaks:
            rejected_log.append({
                'start_seconds': a,
                'end_seconds': b,
                'decision': 'SPLIT_EVENTS',
                'reason': f'{label} proposal crosses interior format break ({interior_breaks[0].kind}).',
            })
            continue
        matching_events = [e for e in graph.events if _overlap_sec(trimmed_a, trimmed_b, e.start, e.end) >= 3.5]
        if len(matching_events) >= 2:
            rejected_log.append({
                'start_seconds': a,
                'end_seconds': b,
                'decision': 'SPLIT_EVENTS',
                'reason': f'{label} proposal spans {len(matching_events)} unrelated visual events.',
            })
            continue
        ev = matching_events[0] if matching_events else None
        mapped.append({
            'event_id': ev.event_id if ev else None,
            'start_seconds': round(trimmed_a, 3),
            'end_seconds': round(trimmed_b, 3),
            'provenance': [provenance],
            'event': ev,
            'shot_count': ev.shot_count if ev else 1,
            'speech_text': sp.get('setup', '') + ' ' + sp.get('development', '') + ' ' + sp.get('payoff', ''),
            'mean_motion': ev.mean_motion if ev else 0.05,
            'setup': sp.get('setup', ''),
            'development': sp.get('development', ''),
            'payoff': sp.get('payoff', ''),
            'setup_time': ev.setup_time if ev else round(trimmed_a, 3),
            'climax_time': ev.climax_time if ev else None,
            'payoff_time': ev.payoff_time if ev else None,
            'reaction_time': ev.reaction_time if ev else None,
            'natural_end_time': round(trimmed_b, 3),
            'face_corners': ev.face_corners if ev else [],
            'evidence': list(sp.get('evidence', [])) + ([sp['reason']] if is_activity and sp.get('reason') else [])
                        + (list(ev.evidence[:2]) if ev else []),
            'boundary_note': 'Trimmed cleanly before format break' if (trimmed_a != a or trimmed_b != b) else f'{label} proposal checked against the event graph',
            'content_type': 'visual_activity' if is_activity else 'speech',
            'language': language,
            'llm_title': sp.get('title', ''),
        })

    # --- STAGE 2: POOL (Deduplicate overlapping proposals, preserving provenance) ---
    pooled: list[dict[str, Any]] = []
    # Score pre-VLM so deduplication keeps the cleanest version of overlapping intervals
    for cand in mapped[:budget.max_coarse_candidates]:
        sc, rub, pen = _score_candidate(cand, cand.get('event'), graph, transcript, category, minimum, maximum)
        cand['score'] = sc
        cand['editorial_rubric'] = rub.model_dump()
        cand['penalties'] = pen

    for cand in sorted(mapped[:budget.max_coarse_candidates], key=lambda c: (-c['score'], c['start_seconds'])):
        dup = next(
            (p for p in pooled if _interval_iou(cand['start_seconds'], cand['end_seconds'], p['start_seconds'], p['end_seconds']) >= 0.55),
            None,
        )
        if dup is not None:
            dup['provenance'] = sorted(set(dup['provenance'] + cand['provenance']))
            continue
        pooled.append(cand)

    # --- STAGE 3: VERIFY (Bounded VLM Visual Verification on Top Candidates) ---
    vlm_verified_count = 0
    if vlm and vlm.is_available() and local_source_path and local_source_path.is_file() and budget.max_vlm_verified > 0:
        for cand in pooled[:budget.max_vlm_verified]:
            a, b = cand['start_seconds'], cand['end_seconds']
            span = b - a
            sample_ts = [
                evidence.context.start + a + span * frac
                for frac in (0.08, 0.36, 0.68, 0.94)
            ]
            frames_tl = extract_frames_at_timestamps(local_source_path, sample_ts, max_width=320)
            if not frames_tl:
                continue
            ver = vlm.verify_candidate_content(
                frames_tl,
                cand.get('speech_text', ''),
                {'source_title': evidence.context.source_title, 'subject': evidence.context.confirmed_subject},
            )
            if ver.verified:
                vlm_verified_count += 1
                cand['vlm_verified'] = True
                cand['vlm_backend'] = ver.backend
                cand['vlm_coherent'] = ver.coherent_single_event
                cand['vlm_contains_format_break'] = ver.contains_format_break
                cand['vlm_contains_unrelated_scene'] = ver.contains_unrelated_scene
                cand['vlm_summary'] = ver.event_summary
                cand['vlm_decision'] = ver.decision
                cand['vlm_defects'] = ver.defects
                if ver.grounded_observations:
                    cand['evidence'] = list(dict.fromkeys(cand['evidence'] + ver.grounded_observations[:3]))[:8]
                # Re-score incorporating VLM verification!
                sc, rub, pen = _score_candidate(cand, cand.get('event'), graph, transcript, category, minimum, maximum)
                cand['score'] = sc
                cand['editorial_rubric'] = rub.model_dump()
                cand['penalties'] = pen

    # Filter out candidates explicitly rejected by VLM or heavily contaminated (>0.5)
    clean_pool: list[dict[str, Any]] = []
    for cand in pooled:
        rub = cand['editorial_rubric']
        if rub['contamination_risk'] >= 0.50 or cand.get('vlm_decision') in ('REJECT', 'SPLIT_EVENTS'):
            rejected_log.append({
                'event_id': cand.get('event_id'),
                'start_seconds': cand['start_seconds'],
                'end_seconds': cand['end_seconds'],
                'decision': cand.get('vlm_decision', 'REJECT'),
                'reason': '; '.join(cand.get('penalties') or cand.get('vlm_defects') or ['Failed semantic verification']),
            })
            continue
        clean_pool.append(cand)

    # --- STAGE 4 & 5: SCORE & PAIRWISE RANK ---
    clean_pool.sort(key=lambda c: (-c['score'], c['start_seconds']))
    finalists = clean_pool[:max(target_count, budget.max_pairwise_finalists)]
    pairwise_diag: dict[str, Any] = {'pairwise_used': False}
    if len(finalists) >= 2 and getattr(request, 'llm_model', None) is not None:
        finalists, pairwise_diag = _pairwise_rank_finalists(finalists, request.llm_model)
        clean_pool = finalists + clean_pool[len(finalists):]

    # --- STAGE 6: SELECT (Diverse, non-overlapping top-K; never fabricate filler) ---
    selected_raw: list[dict[str, Any]] = []
    for cand in clean_pool:
        a, b = cand['start_seconds'], cand['end_seconds']
        if any(
            _overlap_sec(a, b, s['start_seconds'], s['end_seconds']) / max(1e-6, min(b - a, s['end_seconds'] - s['start_seconds'])) >= 0.05
            for s in selected_raw
        ):
            continue
        selected_raw.append(cand)
        if len(selected_raw) >= target_count:
            break

    # Sort review_alternatives by score and keep top 6 non-overlapping with selected
    review_alternatives.sort(key=lambda c: (-c['score'], c['start_seconds']))
    filtered_alts = [
        alt for alt in review_alternatives
        if all(_interval_iou(alt['start_seconds'], alt['end_seconds'], s['start_seconds'], s['end_seconds']) < 0.35 for s in selected_raw)
    ][:6]

    # Materialize final ClipPlan-compatible candidate dicts
    final_candidates = []
    for cand in selected_raw:
        ev = cand.get('event')
        vlm_sum = cand.get('vlm_summary', '')
        title = _grounded_candidate_title(ev, evidence.context.confirmed_subject, vlm_sum or cand.get('llm_title', ''))
        decision = cand.get('vlm_decision') or 'ACCEPT_WITH_REVIEW'
        sem_ver = f"vlm_verified:{cand['vlm_backend']}" if cand.get('vlm_verified') else 'cv_graph_review_required'
        reason_parts = [cand.get('boundary_note', '')]
        if vlm_sum:
            reason_parts.append(f'Visual check: {vlm_sum}')
        elif ev:
            reason_parts.append(f'Semantic graph: {ev.shot_count} coherent shot(s), continuity {ev.mean_continuity:.2f}.')
        reason = ' '.join(p for p in reason_parts if p)[:950]

        completeness = round(
            (int(cand.get('setup_time') is not None) + int(cand.get('climax_time') is not None) + int(cand.get('payoff_time') is not None)) / 3.0,
            3,
        )
        final_candidates.append({
            'start_seconds': cand['start_seconds'],
            'end_seconds': cand['end_seconds'],
            'score': cand['score'],
            'title': title[:150],
            'language': language,
            'reason': reason,
            'confidence': round(cand['editorial_rubric']['narrative_coherence'], 3),
            'schema_version': 2,
            'content_type': cand['content_type'],
            'hook_start_seconds': cand['start_seconds'],
            'setup': cand.get('setup', '')[:500],
            'development': (vlm_sum or cand.get('development', ''))[:500],
            'payoff': cand.get('payoff', '')[:500],
            'evidence': cand.get('evidence', [])[:10],
            'narrative_completeness': completeness,
            'context_required': not bool(cand.get('vlm_verified')) and (ev.speech_word_count < 12 if ev else True),
            'visual_review_required': not bool(cand.get('vlm_verified')),
            'editorial_review_required': True,
            'semantic_verification': sem_ver,
            'semantic_decision': decision,
            'event_id': cand.get('event_id'),
            'shot_count': cand.get('shot_count', 1),
            'setup_time': cand.get('setup_time'),
            'climax_time': cand.get('climax_time'),
            'payoff_time': cand.get('payoff_time'),
            'reaction_time': cand.get('reaction_time'),
            'face_corners': cand.get('face_corners', []),
            'vlm_summary': vlm_sum,
            'editorial_rubric': cand['editorial_rubric'],
            'penalties': cand.get('penalties', []),
            'provenance': cand.get('provenance', []),
            'selection_method': 'editorial_engine_8',
        })

    shortfall_note = ''
    if len(final_candidates) < target_count:
        shortfall_note = (
            f' Only {len(final_candidates)}/{target_count} coherent events met the {minimum:g}–{maximum:g}s bounds '
            f'without padding unrelated footage. {len(filtered_alts)} shorter coherent event(s) are preserved in review_alternatives.'
        )

    return {
        'candidates': final_candidates,
        'review_alternatives': filtered_alts,
        'rejected_candidates': rejected_log,
        'discovered_candidates': len(mapped),
        'pooled_candidates': len(pooled),
        'vlm_verified_count': vlm_verified_count,
        'pairwise_diagnostics': pairwise_diag,
        'format_breaks': [b.model_dump() for b in graph.format_breaks],
        'events_summary': [
            {
                'event_id': e.event_id,
                'start': e.start,
                'end': e.end,
                'duration': e.duration,
                'shot_count': e.shot_count,
                'mean_continuity': e.mean_continuity,
                'terminated_by_break': e.terminated_by_break,
            }
            for e in graph.events
        ],
        'selection_summary': (
            f'Editorial Engine 8.0 planned {len(final_candidates)}/{target_count} verified coherent highlights '
            f'(detected {len(graph.format_breaks)} format breaks across {len(evidence.shots)} shots).'
            + shortfall_note
        ),
        'analysis': 'editorial_engine_8',
        'model': vlm.model if (vlm and vlm_verified_count > 0) else (getattr(request, 'llm_model', None) or config.MODEL),
        'analysis_outcome': 'selected' if final_candidates else 'insufficient_coherent_events',
        'analysis_diagnostics': {
            'shots': len(evidence.shots),
            'events': len(graph.events),
            'format_breaks': len(graph.format_breaks),
            'vlm_verified': vlm_verified_count,
            'vlm_stats': vlm.stats() if vlm else {},
            'pairwise': pairwise_diag,
            'decisions': [],
            'requests': (vlm.calls_made if vlm else 0) + pairwise_diag.get('calls', 0),
            'rejected_intervals': len(rejected_log),
            'windows': len(graph.events),
            'used_fallback': vlm_verified_count == 0,
        },
    }


def _grounded_candidate_title(event: SemanticEvent | None, subject: str, visual_summary: str) -> str:
    from .metadata import _clean_phrase_truncate
    import re
    subj = subject.strip()
    if visual_summary:
        cleaned = re.sub(
            r'^(?:a\s+)?(?:sequence of (?:four|\d+) (?:gameplay )?frames(?: from a video game)? showing|'
            r'video game sequence shows?|four (?:gameplay )?frames(?: showing)?|'
            r'the (?:video|clip|scene|frames) shows?)\s+',
            '',
            visual_summary,
            flags=re.IGNORECASE,
        ).strip()
        clean = _clean_phrase_truncate(cleaned.split('.')[0].strip(), 68)
        if clean:
            clean = clean[0].upper() + clean[1:]
        if subj and subj.casefold() not in clean.casefold():
            return f'{subj} | {clean}'[:120]
        return clean[:120]
    if event and event.speech_text:
        snippet = _clean_phrase_truncate(' '.join(event.speech_text.split()[:12]).strip(), 68)
        if subj and subj.casefold() not in snippet.casefold():
            return f'{subj} | {snippet}'[:120]
        return snippet[:120]
    if subj:
        return f'{subj} | Coherent Gameplay Event'
    return 'Coherent Event Highlight'
