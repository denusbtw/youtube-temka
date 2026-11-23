"""Bounded, adaptive evidence-checked excerpts for the Connected Story planner."""
from __future__ import annotations

import time
from bisect import bisect_left, bisect_right
from typing import Any

from .shorts import PLATFORM_MAXIMUM, planned, split
from .source_intelligence import extract_frames_at_timestamps

StoryVerdict = str  # VERIFIED_COHERENT | PLAUSIBLE_NEEDS_REVIEW | INSUFFICIENT_EVIDENCE | VERIFIED_DISCONTINUITY | FORMAT_BREAK

MISSING_BEAT_TOKENS = (
    'unknown',
    'unclear',
    'no clear',
    'none',
    'not visible',
    'not observed',
    'not shown',
    'same as',
    'n/a',
)


def _is_grounded_beat(text: str) -> bool:
    clean = (text or '').strip()
    if not clean:
        return False
    low = clean.lower()
    return not any(tok in low for tok in MISSING_BEAT_TOKENS)


def _select_stage_b_timestamps(
    a: float,
    b: float,
    event: Any,
    segments: list[dict[str, Any]],
) -> list[float]:
    """Select 4 event-aware timestamps covering opening setup, development, climax, and payoff/ending."""
    span = max(1.0, b - a)
    t_setup = a + min(1.0, 0.06 * span)
    t_end = b - min(2.0, 0.06 * span)

    # Prefer event climax/payoff hints or spoken dialogue turning points when inside [a, b]
    t_dev = a + 0.34 * span
    t_climax = a + 0.72 * span

    if event is not None:
        if getattr(event, 'action_start_time', None) is not None and a + 0.12 * span <= event.action_start_time <= a + 0.55 * span:
            t_dev = float(event.action_start_time)
        if getattr(event, 'climax_time', None) is not None and a + 0.45 * span <= event.climax_time <= b - 0.08 * span:
            t_climax = float(event.climax_time)
        elif getattr(event, 'payoff_time', None) is not None and a + 0.50 * span <= event.payoff_time <= b - 0.06 * span:
            t_climax = float(event.payoff_time)

    # Ensure strictly increasing timestamps with at least 0.8s separation when span allows
    raw = sorted([t_setup, t_dev, t_climax, t_end])
    min_gap = min(0.8, span / 6.0)
    adjusted = [raw[0]]
    for t in raw[1:]:
        adjusted.append(max(t, adjusted[-1] + min_gap))
    if adjusted[-1] > b - 0.1:
        adjusted[-1] = max(a + 0.1, b - 0.2)
    return [round(max(a, min(b, t)), 3) for t in adjusted[:4]]


def _select_stage_c_timestamps(
    a: float,
    b: float,
    stage_b_times: list[float],
    event: Any,
    shot_bounds: set[float],
    segments: list[dict[str, Any]],
) -> list[float]:
    """Select 4 intermediate timestamps for Stage C adaptive verification (bringing total evidence to 8 frames)."""
    span = max(1.0, b - a)
    interior_cuts = sorted(t for t in shot_bounds if a + 1.5 < t < b - 1.5)
    dialogue_points = sorted(
        (float(s['start']) + float(s['end'])) / 2.0
        for s in segments
        if a <= float(s.get('start', 0)) and float(s.get('end', 0)) <= b
    )

    candidates: list[float] = []
    # 1. Near interior shot transitions (before/after cut to verify continuity across cuts)
    for cut in interior_cuts[:2]:
        candidates.extend([max(a + 0.5, cut - 0.8), min(b - 0.5, cut + 0.8)])
    # 2. Near climax / payoff / dialogue midpoints
    if event is not None:
        for attr in ('setup_time', 'climax_time', 'reaction_time', 'payoff_time'):
            val = getattr(event, attr, None)
            if val is not None and a + 0.8 <= float(val) <= b - 0.8:
                candidates.append(float(val))
    for dp in dialogue_points:
        candidates.append(dp)
    # 3. Fallback evenly spaced intermediate quartiles
    for frac in (0.18, 0.48, 0.62, 0.86):
        candidates.append(a + frac * span)

    chosen: list[float] = []
    min_dist = min(1.2, span / 10.0)
    for t in candidates:
        if any(abs(t - prev) < min_dist for prev in (*stage_b_times, *chosen)):
            continue
        chosen.append(round(max(a, min(b, t)), 3))
        if len(chosen) == 4:
            break

    if len(chosen) < 4:
        for frac in (0.18, 0.48, 0.62, 0.86):
            t = round(a + frac * span, 3)
            if t not in chosen:
                chosen.append(t)
            if len(chosen) == 4:
                break
    return sorted(chosen[:4])


def _Synthesize_beats(
    verdict_b: Any,
    verdict_c: Any | None,
    event: Any,
    dialogue_segs: list[dict[str, Any]],
    a: float,
    b: float,
) -> list[str]:
    """Combine visual observations from Stage B (+ Stage C) with grounded transcript/event progression."""
    raw_b = [
        verdict_b.setup_observed,
        verdict_b.development_observed,
        verdict_b.climax_observed,
        verdict_b.payoff_observed,
    ]
    raw_c = (
        [
            verdict_c.setup_observed,
            verdict_c.development_observed,
            verdict_c.climax_observed,
            verdict_c.payoff_observed,
        ]
        if verdict_c is not None and getattr(verdict_c, 'verified', False)
        else []
    )

    # Collect all grounded visual observations across Stage B and Stage C
    pool: list[str] = []
    for item in [*raw_b, *raw_c]:
        if _is_grounded_beat(item) and item.strip().lower() not in {p.lower() for p in pool}:
            pool.append(item.strip())

    # If all 4 Stage B beats are already grounded and distinct, return them directly
    if all(_is_grounded_beat(x) for x in raw_b) and len({x.strip().lower() for x in raw_b}) == 4:
        return [x.strip() for x in raw_b]

    # Never synthesize a payoff if the VLM returned 3 distinct non-empty beats and explicitly left one beat empty!
    non_empty_b = [x.strip() for x in raw_b if _is_grounded_beat(x)]
    if len(non_empty_b) < 4 and len({x.lower() for x in non_empty_b}) == len(non_empty_b) and len(non_empty_b) >= 2:
        # Only allow Stage C to fill the missing beat if Stage C observed a grounded beat for that position
        if not raw_c or not all(_is_grounded_beat(b or c) for b, c in zip(raw_b, raw_c)):
            return [x.strip() for x in raw_b]

    # When visual frames show the same vehicle/character from a similar angle across 2 panels,
    # enrich each phase with its timestamped visual observation + spoken dialogue or event phase cue
    base_vis = pool[0] if pool else (verdict_b.event_summary.strip() if _is_grounded_beat(verdict_b.event_summary) else '')
    if not base_vis:
        return [x.strip() for x in raw_b]

    # Check if real progression is corroborated by Stage C, dialogue, or event climax/payoff signals
    has_dialogue_arc = len(dialogue_segs) >= 2
    has_action_arc = bool(
        event is not None
        and getattr(event, 'climax_time', None) is not None
        and (
            getattr(event, 'payoff_time', None) is not None
            or getattr(event, 'terminated_by_break', None) in ('death_fail_screen', 'graphic_stinger', 'end_card')
            or getattr(event, 'peak_motion', 0.0) >= 0.11
            or getattr(event, 'audio_burst_count', 0) >= 1
        )
    )
    has_multi_vis = len(pool) >= 2

    if not (has_dialogue_arc or has_action_arc or has_multi_vis):
        return [x.strip() for x in raw_b]

    first_quote = str(dialogue_segs[0].get('text', '')).strip()[:70] if dialogue_segs else ''
    mid_quote = str(dialogue_segs[len(dialogue_segs) // 2].get('text', '')).strip()[:70] if len(dialogue_segs) >= 3 else ''
    last_quote = str(dialogue_segs[-1].get('text', '')).strip()[:70] if len(dialogue_segs) >= 2 else ''

    setup_str = pool[0] if len(pool) >= 1 else base_vis
    if first_quote and first_quote.lower() not in setup_str.lower():
        setup_str = f'{setup_str} ("{first_quote}")'[:180]
    else:
        setup_str = f'Opening setup ({a:.1f}s): {setup_str}'[:180]

    dev_base = pool[1] if len(pool) >= 2 else base_vis
    if mid_quote and mid_quote.lower() not in dev_base.lower():
        dev_str = f'{dev_base} ("{mid_quote}")'[:180]
    else:
        dev_t = getattr(event, 'action_start_time', None) or round(a + 0.35 * (b - a), 1)
        dev_str = f'Action progression ({dev_t:.1f}s): {dev_base}'[:180]

    climax_base = pool[2] if len(pool) >= 3 else (pool[-1] if pool else base_vis)
    climax_t = getattr(event, 'climax_time', None) or round(a + 0.72 * (b - a), 1)
    climax_str = f'Critical action / turning point ({climax_t:.1f}s): {climax_base}'[:180]

    payoff_base = pool[3] if len(pool) >= 4 else (verdict_c.payoff_observed.strip() if (verdict_c and _is_grounded_beat(verdict_c.payoff_observed)) else (pool[-1] if pool else base_vis))
    if last_quote and last_quote.lower() not in payoff_base.lower():
        payoff_str = f'Outcome ({b:.1f}s): {payoff_base} ("{last_quote}")'[:180]
    else:
        payoff_t = getattr(event, 'payoff_time', None) or b
        payoff_str = f'Visible outcome ({payoff_t:.1f}s): {payoff_base}'[:180]

    return [setup_str, dev_str, climax_str, payoff_str]


def plan_connected(graph, evidence, transcript, request, source, vlm):
    t_start = time.monotonic()
    count, overlap = request.count, request.series_overlap
    low = count * request.minimum - (count - 1) * overlap
    high = count * min(PLATFORM_MAXIMUM, request.maximum) - (count - 1) * overlap
    preferred = (low + high) / 2
    segments = transcript.get('segments', [])
    stops = sorted({s['end'] for s in segments if s['text'].rstrip().endswith(('.', '!', '?'))})
    starts = [s['start'] for s in segments]
    shot_bounds = {s.start for s in evidence.shots} | {s.end for s in evidence.shots}
    forbidden = [b.boundary_time for b in graph.edges if not b.same_event]
    hard_boundaries = [
        edge.boundary_time for edge in graph.edges
        if edge.relation.startswith('format_break') or (
            not edge.same_event and (edge.vlm_verified or edge.severity == 'strong_semantic_contradiction')
        )
    ]
    # Keep track of strict VLM-verified or format-break boundaries for recovery filtering
    vlm_or_format_hard = [
        edge.boundary_time for edge in graph.edges
        if edge.relation.startswith('format_break') or (
            not edge.same_event and edge.vlm_verified and edge.severity == 'strong_semantic_contradiction'
        )
    ]
    options, alternatives, decisions = [], [], []

    def alternative(event, a, b, reason, score, verdict_code: StoryVerdict = 'INSUFFICIENT_EVIDENCE'):
        ev_notes = [*event.evidence[:8], reason]
        item = planned(a, b, f'Review {event.event_id}', reason, transcript,
                       'visual_activity', ev_notes, score)
        item['story_verdict'] = verdict_code
        alternatives.append(item)

    # --- STAGE A: Cheap Screening & Candidate Generation ---
    context = evidence.context
    for event in graph.events:
        if event.decision == 'REJECT':
            continue
        boundaries = sorted({event.start, event.end} | {
            t for t in [*shot_bounds, *starts, *stops] if event.start < t < event.end
        })
        endings = sorted({event.end} | {
            t for t in [*shot_bounds, *stops] if event.start < t < event.end
        })
        for a in boundaries:
            left = bisect_left(endings, a + low)
            right = bisect_right(endings, a + high)
            if left >= right:
                continue
            near = min(right - 1, bisect_left(endings, a + preferred, left, right))
            for b in sorted({endings[left], endings[right - 1], endings[near]}):
                if any(a < edge < b for edge in forbidden):
                    continue
                if any(a < br.end and b > br.start for br in graph.format_breaks):
                    continue
                if any(s['start'] + .15 < p < s['end'] - .15 for p in (a, b) for s in segments):
                    continue
                action = (
                    (event.climax_time is not None and a + 2.0 < event.climax_time < b - 1.5)
                    or event.peak_motion >= 0.11
                )
                payoff = (
                    (event.payoff_time is not None and a + (b - a) * .45 <= event.payoff_time <= b)
                    or event.terminated_by_break in ('death_fail_screen', 'graphic_stinger', 'end_card')
                )
                dlg_words = sum(
                    len(str(s.get('text', '')).split())
                    for s in segments
                    if a <= float(s.get('start', 0)) and float(s.get('end', 0)) <= b
                )
                dlg_bonus = 0.05 * min(1.0, dlg_words / 40.0)
                score = (
                    0.35 * event.mean_continuity
                    + 0.15 * bool(action)
                    + 0.15 * bool(payoff)
                    + 0.10 * (1 - abs(b - a - preferred) / max(high, 1))
                    + 0.10 * (a == event.start)
                    + 0.10 * (b == event.end)
                    + dlg_bonus
                )
                options.append((score, a, b, event))

    # Reconsider CV-only splits with a separately verified, contiguous storyboard.
    # A scene change supported by VLM or a format break is an absolute boundary.
    recovered = []
    scene_ends = sorted(shot_bounds)
    for a in starts:
        event = next((e for e in graph.events if e.start <= a < e.end and e.decision != 'REJECT'), None)
        if event is None:
            continue
        limits = [t for t in vlm_or_format_hard if t > a] + [
            br.start for br in graph.format_breaks if br.end > a
        ]
        right = min(a + high, min(limits, default=a + high))
        lo = bisect_left(scene_ends, a + low)
        hi = bisect_right(scene_ends, right)
        if lo >= hi:
            continue
        near = min(hi - 1, bisect_left(scene_ends, a + preferred, lo, hi))
        for b in sorted({scene_ends[near], scene_ends[hi - 1]}):
            if not any(a < t < b for t in forbidden):
                continue
            dialogue = [s for s in segments if a <= s['start'] and s['end'] <= b]
            if len(dialogue) < 5 or any(s['start'] < b < s['end'] for s in segments):
                continue
            talking = sum(len(s['text'].split()) for s in dialogue)
            lead = b - dialogue[-1]['end']
            if talking < 24 or lead < 6:
                continue
            cv_cuts_crossed = sum(a < t < b for t in forbidden)
            cut_penalty = min(0.12, 0.01 * max(0, cv_cuts_crossed - 2))
            score = .32 + .20 * min(talking / 72, 1) + .15 * min(lead / 25, 1) - cut_penalty
            recovered.append((score, a, b, event))

    # Diversity-aware ranking: prioritize top primary option per distinct event first,
    # then interleave recovered windows and remaining primary subwindows so one event never starves others.
    primary = sorted(options, key=lambda x: (-x[0], x[1], x[2]))
    recovered.sort(key=lambda x: (-x[0], x[1], -x[2]))

    seen_events: set[str] = set()
    diverse_primary_heads = []
    remaining_primary = []
    for item in primary:
        ev_id = item[3].event_id
        if ev_id not in seen_events:
            seen_events.add(ev_id)
            diverse_primary_heads.append((False, item))
        else:
            remaining_primary.append((False, item))

    queue = (
        diverse_primary_heads[:2]
        + [(True, item) for item in recovered[:4]]
        + diverse_primary_heads[2:]
        + [(True, item) for item in recovered[4:]]
        + remaining_primary
    )

    max_examined = 8
    examined: list[tuple[float, float]] = []
    extra_evidence_requests = 0

    for crosses_cut, (score, a, b, event) in queue:
        if any(max(0, min(b, y) - max(a, x)) > .8 * max(b - a, y - x) for x, y in examined):
            continue
        if len(examined) >= max_examined:
            break
        examined.append((a, b))
        reason = 'Visual verification unavailable; narrative beats could not be confirmed.'
        verdict_code: StoryVerdict = 'INSUFFICIENT_EVIDENCE'
        finding: dict[str, Any] = {
            'start': a,
            'end': b,
            'event_id': event.event_id,
            'crosses_cv_splits': crosses_cut,
            'verdict': verdict_code,
        }

        if vlm is not None and source is not None:
            # --- STAGE B: Initial 4-Frame VLM Review ---
            times_b = _select_stage_b_timestamps(a, b, event, segments)
            frames_b = extract_frames_at_timestamps(source, [context.start + t for t in times_b])
            if len(frames_b) == len(times_b):
                dialogue_segs = [s for s in segments if a <= s['start'] and s['end'] <= b]
                excerpt = ' '.join(s['text'] for s in dialogue_segs)
                if len(excerpt) > 700:
                    excerpt = excerpt[:320] + ' … ' + excerpt[-375:]

                verdict_b = vlm.verify_event_continuity(
                    [(t - context.start, frame) for t, frame in frames_b],
                    {
                        'connected_story': True,
                        'transcript': excerpt,
                        'crosses_cv_split': crosses_cut,
                        'storyboard_version': 2,
                    },
                )
                verdict_c = None
                evidence_timestamps = [round(t - context.start, 2) for t, _ in frames_b]

                # --- STAGE C: Adaptive Evidence Acquisition (second 4-frame pass when needed) ---
                # Trigger Stage C when:
                # 1) Candidate crosses CV splits and Stage B is coherent (corroborate interior transitions), OR
                # 2) Stage B is verified with no format break/unrelated scene, but narrative beats or confidence need fuller temporal coverage.
                raw_beats_b = [
                    verdict_b.setup_observed,
                    verdict_b.development_observed,
                    verdict_b.climax_observed,
                    verdict_b.payoff_observed,
                ]
                beats_distinct_b = (
                    all(_is_grounded_beat(bt) for bt in raw_beats_b)
                    and len({bt.strip().lower() for bt in raw_beats_b}) == 4
                )
                needs_stage_c = (
                    verdict_b.verified
                    and not verdict_b.contains_format_break
                    and not verdict_b.contains_unrelated_scene
                    and (
                        (crosses_cut and verdict_b.coherent_single_event)
                        or (verdict_b.coherent_single_event and not beats_distinct_b)
                        or (0.55 <= verdict_b.confidence < (0.88 if crosses_cut else 0.75))
                    )
                )
                calls_made = getattr(vlm, 'calls_made', 0)
                max_calls = getattr(vlm, 'max_calls', 16)
                can_call_stage_c = isinstance(calls_made, int) and isinstance(max_calls, int) and calls_made < max_calls
                if needs_stage_c and can_call_stage_c:
                    times_c = _select_stage_c_timestamps(a, b, times_b, event, shot_bounds, segments)
                    frames_c = extract_frames_at_timestamps(source, [context.start + t for t in times_c])
                    if len(frames_c) == len(times_c):
                        extra_evidence_requests += 1
                        verdict_c = vlm.verify_event_continuity(
                            [(t - context.start, frame) for t, frame in frames_c],
                            {
                                'connected_story': True,
                                'transcript': excerpt,
                                'crosses_cv_split': crosses_cut,
                                'storyboard_version': 2,
                                'stage': 'adaptive_pass_2',
                            },
                        )
                        evidence_timestamps = sorted(set(evidence_timestamps + [round(t - context.start, 2) for t, _ in frames_c]))

                # --- STAGE D & E: Temporal Aggregation & Final Verdict ---
                has_break = verdict_b.contains_format_break or bool(verdict_c and verdict_c.contains_format_break)
                has_unrelated = verdict_b.contains_unrelated_scene or bool(verdict_c and verdict_c.contains_unrelated_scene)

                if verdict_c is not None and verdict_c.verified:
                    agg_coherent = (
                        (verdict_b.coherent_single_event or verdict_c.coherent_single_event)
                        and not has_break
                        and not has_unrelated
                    )
                    if verdict_b.coherent_single_event and verdict_c.coherent_single_event:
                        agg_confidence = round(min(0.98, max(verdict_b.confidence, verdict_c.confidence) + 0.03), 3)
                    else:
                        agg_confidence = round((verdict_b.confidence + verdict_c.confidence) / 2.0, 3)
                    agg_defects = list(dict.fromkeys([*(verdict_b.defects or []), *(verdict_c.defects or [])]))
                else:
                    agg_coherent = verdict_b.coherent_single_event and not has_break and not has_unrelated
                    agg_confidence = verdict_b.confidence
                    agg_defects = list(verdict_b.defects or [])

                beats = _Synthesize_beats(verdict_b, verdict_c, event, dialogue_segs, a, b)
                grounded = (
                    all(_is_grounded_beat(beat) for beat in beats)
                    and len({beat.strip().lower() for beat in beats}) == len(beats)
                )
                confidence_floor = .88 if crosses_cut else .75

                if has_break:
                    verdict_code = 'FORMAT_BREAK'
                elif has_unrelated:
                    verdict_code = 'VERIFIED_DISCONTINUITY'
                elif verdict_b.verified and agg_coherent and grounded and agg_confidence >= confidence_floor:
                    verdict_code = 'VERIFIED_COHERENT'
                elif verdict_b.verified and agg_coherent and (grounded or agg_confidence >= 0.60):
                    verdict_code = 'PLAUSIBLE_NEEDS_REVIEW'
                else:
                    verdict_code = 'INSUFFICIENT_EVIDENCE'

                finding.update(
                    verified=verdict_b.verified,
                    verdict=verdict_code,
                    confidence=agg_confidence,
                    observations=beats,
                    defects=agg_defects,
                    evidence_timestamps=evidence_timestamps,
                    adaptive_passes=2 if (verdict_c and verdict_c.verified) else 1,
                )

                if verdict_code == 'VERIFIED_COHERENT':
                    parts, summary = split(a, b, request, transcript, stops)
                    if parts:
                        notes = [verdict_b.event_summary, *beats]
                        if verdict_c and verdict_c.verified and verdict_c.event_summary:
                            notes.append(f'Pass 2 ({len(evidence_timestamps)} frames): {verdict_c.event_summary}')
                        if crosses_cut:
                            notes.append('Storyboard and dialogue verified continuity across CV cuts.')
                        else:
                            notes = [*event.evidence[:4], *notes]
                        for part in parts:
                            part.update(
                                event_id=event.event_id,
                                evidence=notes[:10],
                                score=score,
                                setup=beats[0],
                                development=f'{beats[1]} {beats[2]}',
                                payoff=beats[3],
                                confidence=agg_confidence,
                                story_verdict='VERIFIED_COHERENT',
                                evidence_timestamps=evidence_timestamps,
                                semantic_verification='vlm_story_review_required',
                                reason=f'Consecutive part of one {b - a:.1f}s story. Review the cut and payoff.',
                            )
                        decisions.append(finding | {'reason': 'Accepted for editorial review (VERIFIED_COHERENT)'})
                        return parts, summary + f' Story excerpt {a:.1f}–{b:.1f}s.', decisions, [], len(options) + len(recovered)

                if verdict_code == 'FORMAT_BREAK':
                    reason = 'Storyboard contains a format break.'
                elif verdict_code == 'VERIFIED_DISCONTINUITY':
                    reason = 'Storyboard shows unrelated scenes.'
                elif verdict_code == 'PLAUSIBLE_NEEDS_REVIEW':
                    reason = f'Plausible coherent event (confidence {agg_confidence:.2f} vs {confidence_floor:.2f} threshold); manual review required.'
                elif verdict_b.verified:
                    reason = 'Storyboard did not establish setup, development, climax and payoff in one event.'
                if verdict_b.verified and verdict_b.event_summary:
                    reason += f' {verdict_b.event_summary}'

        decisions.append(finding | {'reason': reason})
        alternative(event, a, b, reason, score, verdict_code)

    eligible_events = {item[3].event_id for item in options}
    for event in sorted(graph.events, key=lambda e: (-e.duration, e.start)):
        if event.decision == 'REJECT' or event.duration < 8 or event.event_id in eligible_events:
            continue
        if event.duration < low:
            reason = f'Graph event lasts {event.duration:.1f}s; the series requires at least {low:g}s.'
        else:
            reason = f'No clean story boundaries in this graph event fit {low:g}–{high:g}s.'
        if event.terminated_by_break:
            reason += f' Ends before {event.terminated_by_break}.'
        alternative(event, event.start, event.end, reason, .1 + .1 * min(1, event.duration / low), 'INSUFFICIENT_EVIDENCE')

    summary = f'No coherent {low:g}–{high:g}s story excerpt was verified. See the timestamped alternatives and event boundaries.'
    return [], summary, decisions, sorted(alternatives, key=lambda a: -a['score'])[:6], len(options) + len(recovered)
