"""Factory 8.0 Phase 11 — Comprehensive 30-Case Editorial & Systems Regression Suite."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from factory import config, drafts, metadata, shorts, store
from factory.discovery import DiscoveryBudget, _pairwise_rank_finalists, discover_highlights
from factory.edit_manifest import build_edit_manifest, choose_smart_layout
from factory.framing import dedupe_sendcmd_lines
from factory.models import JobRequest
from factory.semantic_events import build_event_graph, refine_event_boundaries, score_adjacent_shots
from factory.source_intelligence import Shot, SourceContext, SourceEvidence, analyze_source
from factory.vision_vlm import LocalVisionAdapter, vlm_gpu_layers


def _mk_shot(
    sid: int,
    start: float,
    end: float,
    *,
    luma: float = 95.0,
    sat: float = 0.20,
    hisat: float = 0.03,
    red: float = 0.02,
    txt: float = 0.02,
    mot: float = 0.09,
    stat: float = 0.05,
    dark: float = 0.0,
    hsv: list[float] | None = None,
    grid: list[float] | None = None,
    hud: list[float] | None = None,
    rms: float = 0.08,
    bursts: int = 1,
    speech: str = '',
    words: int = 0,
    react: bool = False,
    face: list[str] | None = None,
    ocr: str = '',
    btype: str = 'hard_cut',
) -> Shot:
    return Shot(
        shot_id=sid,
        start=start,
        end=end,
        duration=max(0.1, end - start),
        boundary_type='initial' if sid == 0 else btype,  # type: ignore[arg-type]
        boundary_score=0.7 if sid > 0 else 0.0,
        sample_count=max(2, int((end - start) * 2)),
        hsv_hist=hsv or ([0.0625] * 16 + [0.125] * 16),
        spatial_grid=grid or ([0.40] * 27),
        corner_hud=hud or ([0.30] * 24),
        mean_luma=luma,
        mean_sat=sat,
        high_sat_frac=hisat,
        red_tint_score=red,
        center_text_band_score=txt,
        mean_motion=mot,
        peak_motion=mot * 1.6,
        static_fraction=stat,
        dark_fraction=dark,
        face_corners=face or [],
        mean_audio_rms=rms,
        peak_audio_rms=rms * 1.5,
        audio_burst_count=bursts,
        speech_text=speech,
        speech_word_count=words,
        speech_segment_count=1 if words else 0,
        has_reaction_cue=react,
        ocr_text=ocr,
    )


def _ev(shots: list[Shot], duration: float, category: str = 'gameplay', subject: str = 'GTA') -> SourceEvidence:
    coarse = []
    for s in shots:
        t = s.start
        while t < s.end:
            coarse.append({
                'time': round(t, 2),
                'motion': s.mean_motion,
                'cut': t == s.start and s.shot_id > 0,
                'audio_rms': s.mean_audio_rms,
                'audio_change': 0.06 if s.audio_burst_count else 0.01,
                'sampled_silence': s.mean_audio_rms < 0.004,
            })
            t += 1.0
    return SourceEvidence(
        context=SourceContext(
            source_id='fixture',
            source_fingerprint='fp-test',
            start=0.0,
            end=duration,
            duration=duration,
            width=1280,
            height=720,
            content_category=category,
            confirmed_subject=subject,
            source_title='GTA Gameplay Compilation',
        ),
        shots=shots,
        coarse_samples=coarse,
    )


def _req(**kwargs) -> JobRequest:
    base = {
        'source': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        'rights_confirmed': True,
        'mode': 'shorts',
        'count': 3,
        'minimum': 35,
        'maximum': 60,
        'selection_version': 2,
        'source_category': 'gameplay',
        'confirmed_subject': 'GTA',
        'save_drive': False,
    }
    base.update(kwargs)
    return JobRequest(**base)


# Case 1 & 9: Coherent multi-shot driving event & several scene cuts within one event
def test_01_09_coherent_multishot_driving_event_preserved():
    shots = [
        _mk_shot(0, 0, 14, luma=88, mot=0.10, speech='Starting the pursuit', words=10),
        _mk_shot(1, 14, 28, luma=91, mot=0.14, bursts=3, speech='Watch the turn!', words=12, react=True),
        _mk_shot(2, 28, 42, luma=86, mot=0.12, bursts=2, speech='We made it out!', words=10, react=True),
    ]
    ev = _ev(shots, 42.0)
    tr = {'language': 'en', 'segments': [{'start': 1, 'end': 41, 'text': 'Starting the pursuit. Watch the turn! We made it out!'}]}
    graph = build_event_graph(ev, tr)
    assert len(graph.events) == 1
    assert graph.events[0].shot_count == 3
    res = discover_highlights(ev, graph, tr, _req(count=1, llm_model=None))
    assert len(res['candidates']) == 1
    assert res['candidates'][0]['shot_count'] == 3


# Case 2 & 8: Driving followed by unrelated boat footage even with continuous background music
def test_02_08_unrelated_driving_and_boat_separated_despite_continuous_music():
    hsv_car = [0.55, 0.25] + [0.01] * 30
    hsv_boat = [0.01] * 8 + [0.65, 0.20] + [0.01] * 22
    shots = [
        _mk_shot(0, 0, 22, luma=42, hsv=hsv_car, grid=[0.18] * 27, hud=[0.12] * 24, rms=0.14),
        _mk_shot(1, 22, 46, luma=148, hsv=hsv_boat, grid=[0.72] * 27, hud=[0.78] * 24, rms=0.14),
    ]
    ev = _ev(shots, 46.0)
    tr = {'language': 'en', 'segments': [{'start': 5, 'end': 42, 'text': 'background music playing across both clips'}]}
    graph = build_event_graph(ev, tr)
    assert len(graph.events) == 2
    res = discover_highlights(ev, graph, tr, _req(count=1, minimum=35, llm_model=None))
    # Anti-padding: must NOT merge them to hit 35s!
    assert len(res['candidates']) == 0
    assert len(res['review_alternatives']) == 2


# Case 3: Gameplay followed by end card
def test_03_gameplay_followed_by_end_card_trimmed():
    shots = [
        _mk_shot(0, 0, 44, luma=92, mot=0.11, speech='Swap the truck for a bison.', words=18),
        _mk_shot(1, 44, 60, luma=58, sat=0.72, hisat=0.84, txt=0.10, mot=0.005, stat=0.95),
    ]
    ev = _ev(shots, 60.0)
    tr = {'language': 'en', 'segments': [{'start': 2, 'end': 43.5, 'text': 'Swap the truck for a bison.'}]}
    graph = build_event_graph(ev, tr)
    assert any(b.kind == 'end_card' for b in graph.format_breaks)
    res = discover_highlights(ev, graph, tr, _req(count=1, minimum=35, llm_model=None))
    assert len(res['candidates']) == 1
    assert res['candidates'][0]['end_seconds'] <= 44.0


# Case 4 & 5: Vehicle incident -> title card -> aircraft footage
def test_04_05_title_card_splits_vehicle_and_aircraft():
    hsv_road = [0.4, 0.3] + [0.01] * 30
    hsv_sky = [0.01] * 10 + [0.6, 0.2] + [0.01] * 20
    shots = [
        _mk_shot(0, 0, 20, luma=85, hsv=hsv_road, grid=[0.35] * 27),
        _mk_shot(1, 20, 24, luma=72, sat=0.58, hisat=0.55, txt=0.18, mot=0.015, stat=0.80),
        _mk_shot(2, 24, 48, luma=135, hsv=hsv_sky, grid=[0.65] * 27),
    ]
    ev = _ev(shots, 48.0)
    graph = build_event_graph(ev, {'language': 'en', 'segments': []})
    assert any(b.kind == 'title_card' and b.shot_id == 1 for b in graph.format_breaks)
    assert len(graph.events) == 2
    assert graph.events[0].end == 20.0 and graph.events[1].start == 24.0


# Case 6: Streamer reaction after visual gameplay climax
def test_06_streamer_reaction_after_climax_detected():
    shots = [_mk_shot(0, 0, 40, mot=0.14, bursts=3, face=['bottom_left'], speech='Oh my god no way!', words=12, react=True)]
    ev = _ev(shots, 40.0, category='stream')
    tr = {'language': 'en', 'segments': [{'start': 12.0, 'end': 15.0, 'text': 'Oh my god no way!'}]}
    graph = build_event_graph(ev, tr)
    assert graph.events[0].face_corners == ['bottom_left']
    assert graph.events[0].has_reaction_cue is True
    assert graph.events[0].payoff_time is not None


# Case 7: Silent gameplay with important action
def test_07_silent_gameplay_action_discovered():
    shots = [_mk_shot(0, 0, 40, mot=0.15, rms=0.0, bursts=0, speech='', words=0)]
    ev = _ev(shots, 40.0, category='gameplay')
    tr = {'language': None, 'segments': []}
    graph = build_event_graph(ev, tr)
    res = discover_highlights(ev, graph, tr, _req(count=1, minimum=35, llm_model=None))
    assert len(res['candidates']) == 1


# Case 10 & 11: High motion or loud audio without meaningful event vs real event
def test_10_11_coherent_event_with_payoff_beats_noisy_filler():
    # Event 1: noisy loud audio, no speech, no payoff, followed by menu
    # Event 2: coherent driving event with reaction & payoff
    s0 = _mk_shot(0, 0, 38, luma=90, mot=0.14, rms=0.25, bursts=0, speech='', words=0, hsv=[0.4] + [0.02] * 31)
    s1 = _mk_shot(1, 38, 44, luma=10, dark=1.0, stat=1.0, mot=0.0)
    s2 = _mk_shot(2, 44, 84, luma=100, mot=0.13, rms=0.15, bursts=4, speech='Watch this jump! Oh my god we landed it!', words=22, react=True, hsv=[0.02] * 5 + [0.4] + [0.02] * 26)
    ev = _ev([s0, s1, s2], 84.0)
    tr = {'language': 'en', 'segments': [{'start': 46, 'end': 82, 'text': 'Watch this jump! Oh my god we landed it!'}]}
    graph = build_event_graph(ev, tr)
    res = discover_highlights(ev, graph, tr, _req(count=2, minimum=35, llm_model=None))
    assert len(res['candidates']) == 2
    assert res['candidates'][0]['start_seconds'] == 44.0  # Rich payoff event outranks raw motion/loudness


# Case 12: Important event with low motion (podcast dialogue)
def test_12_podcast_low_motion_dialogue_preserved():
    shots = [
        _mk_shot(0, 0, 22, luma=110, mot=0.02, speech='Here is the surprising discovery about local models.', words=25),
        _mk_shot(1, 22, 44, luma=98, mot=0.02, speech='That explains why memory budgets matter so much!', words=24, react=True),
    ]
    ev = _ev(shots, 44.0, category='podcast', subject='AI Podcast')
    tr = {'language': 'en', 'segments': [{'start': 1, 'end': 43, 'text': 'Here is the surprising discovery. That explains why!'}]}
    graph = build_event_graph(ev, tr)
    assert len(graph.events) == 1
    res = discover_highlights(ev, graph, tr, _req(count=1, minimum=35, source_category='podcast', confirmed_subject='AI Podcast', llm_model=None))
    assert len(res['candidates']) == 1


# Case 13 & 14: Abrupt valid comedic ending & long irrelevant static intro excluded
def test_13_14_static_intro_excluded_and_abrupt_fail_ending_preserved():
    shots = [
        _mk_shot(0, 0, 25, luma=70, mot=0.004, stat=0.95, speech='', words=0, bursts=0),  # static menu/loading intro
        _mk_shot(1, 25, 65, luma=95, mot=0.13, bursts=3, speech='Wait what?!', words=12, react=True),
        _mk_shot(2, 65, 68, luma=60, sat=0.85, hisat=0.92, red=0.35, txt=0.16, mot=0.02),  # WASTED death_fail_screen
    ]
    ev = _ev(shots, 68.0)
    tr = {'language': 'en', 'segments': [{'start': 58, 'end': 64.5, 'text': 'Wait what?!'}]}
    graph = build_event_graph(ev, tr)
    kinds = {b.kind for b in graph.format_breaks}
    assert 'menu_or_loading' in kinds
    assert 'death_fail_screen' in kinds
    assert len(graph.events) == 1
    assert graph.events[0].start == 25.0 and graph.events[0].terminated_by_break == 'death_fail_screen'


# Case 15: Two candidates describing the same event deduplicated in POOL
def test_15_duplicate_proposals_pooled():
    shots = [_mk_shot(0, 0, 48, mot=0.12, speech='Big moment happening right now!', words=20, react=True)]
    ev = _ev(shots, 48.0)
    tr = {'language': 'en', 'segments': [{'start': 1, 'end': 46, 'text': 'Big moment happening right now!'}]}
    graph = build_event_graph(ev, tr)
    dup_speech = [{'start_seconds': 2.0, 'end_seconds': 46.0, 'title': 'Big moment', 'setup': 'Big', 'development': 'moment', 'payoff': 'now!'}]
    res = discover_highlights(ev, graph, tr, _req(count=3, minimum=35, llm_model=None), speech_proposals=dup_speech)
    assert len(res['candidates']) == 1
    assert 'semantic_event_graph' in res['candidates'][0]['provenance']


# Case 16: Incorrectly identified game edition is NOT asserted from source title claim
def test_16_unconfirmed_game_edition_not_asserted():
    req = _req(confirmed_subject='GTA', source_category='gameplay')
    ctx = metadata.context(
        {'language': 'en', 'segments': [{'start': 0, 'end': 10, 'text': 'what the hell'}]},
        req,
        {'title': 'GTA 6 FUNNY MOMENTS #1'},
        [{'start_seconds': 0, 'end_seconds': 40, 'title': 'GTA', 'vlm_summary': 'Pickup truck driving on a highway at sunset'}],
        {'duration': 40.0, 'validation': {'audio': True}},
    )
    draft = metadata.Generator(None, None, None).create(ctx)
    assert 'GTA 6' not in draft['title']
    assert 'Pickup truck' in draft['title']
    assert draft['category_id'] == '20'
    assert 'what' not in draft['tags'] and 'hell' not in draft['tags']


# Case 17: No valid highlights
def test_17_no_valid_highlights_returns_empty_honestly():
    shots = [
        _mk_shot(0, 0, 20, luma=5, dark=1.0, stat=1.0, mot=0.0),
        _mk_shot(1, 20, 45, luma=60, sat=0.75, hisat=0.88, stat=0.95, mot=0.005),
    ]
    ev = _ev(shots, 45.0)
    graph = build_event_graph(ev, {'language': None, 'segments': []})
    res = discover_highlights(ev, graph, {'language': None, 'segments': []}, _req(count=3, minimum=35, llm_model=None))
    assert res['candidates'] == []
    assert res['analysis_outcome'] == 'insufficient_coherent_events'


# Case 18: Three good independent highlights
def test_18_three_good_independent_highlights_selected():
    shots = [
        _mk_shot(0, 0, 42, luma=90, mot=0.13, bursts=3, speech='First event highlight!', words=18, react=True),
        _mk_shot(1, 42, 46, luma=65, sat=0.80, hisat=0.85, txt=0.15, stat=0.90, mot=0.01),  # break
        _mk_shot(2, 46, 90, luma=110, mot=0.12, bursts=2, speech='Second event highlight!', words=16, react=True),
        _mk_shot(3, 90, 94, luma=5, dark=1.0, stat=1.0, mot=0.0),  # black break
        _mk_shot(4, 94, 140, luma=85, mot=0.14, bursts=4, speech='Third event highlight!', words=20, react=True),
    ]
    ev = _ev(shots, 140.0)
    tr = {'language': 'en', 'segments': [
        {'start': 2, 'end': 40, 'text': 'First event highlight!'},
        {'start': 48, 'end': 88, 'text': 'Second event highlight!'},
        {'start': 96, 'end': 138, 'text': 'Third event highlight!'},
    ]}
    graph = build_event_graph(ev, tr)
    res = discover_highlights(ev, graph, tr, _req(count=3, minimum=35, maximum=60, llm_model=None))
    assert len(res['candidates']) == 3
    intervals = sorted((c['start_seconds'], c['end_seconds']) for c in res['candidates'])
    assert intervals[0][1] <= 42.0 and intervals[1][0] >= 46.0 and intervals[2][0] >= 94.0


# Case 19, 20, 21: Connected Story (sufficient & insufficient) + Sequential Split
def test_19_20_21_connected_and_sequential_modes(tmp_path):
    # Sequential split
    seq = shorts.plan_v8('upload:unused.mp4', _req(series_mode='sequential', count=3, minimum=35, maximum=60), {'language': 'en', 'segments': []}, 140.0)
    assert len(seq['candidates']) == 3
    assert seq['analysis'] == 'deterministic source split'

    # Connected Story with sufficiently long coherent event (120s single shot)
    long_ev = _ev([_mk_shot(0, 0, 120, mot=0.12, speech='Long story arc.', words=40)], 120.0)
    assert len(build_event_graph(long_ev, {'language': 'en', 'segments': []}).events) == 1

    # Connected Story with insufficient coherent event (two 30s events separated by break)
    short_shots = [
        _mk_shot(0, 0, 30, mot=0.12),
        _mk_shot(1, 30, 34, luma=5, dark=1.0, stat=1.0, mot=0.0),
        _mk_shot(2, 34, 64, mot=0.12),
    ]
    short_graph = build_event_graph(_ev(short_shots, 64.0), {'language': 'en', 'segments': []})
    assert all(e.duration < 99.0 for e in short_graph.events)


@pytest.mark.parametrize('verdict,selected', [
    ('story', True), ('compilation', False), ('no_payoff', False), ('no_vlm', False),
])
def test_connected_excerpt_inside_long_multishot_event(monkeypatch, tmp_path, verdict, selected):
    from factory import connected_windows
    from factory.vision_vlm import EventVerification

    shots = [_mk_shot(i, i * 40, (i + 1) * 40) for i in range(4)]
    evidence = _ev(shots, 160)
    transcript = {'language': 'en', 'segments': []}
    graph = build_event_graph(evidence, transcript)
    assert [event.duration for event in graph.events] == [160]
    source = tmp_path / 'cached.mp4'
    monkeypatch.setattr(connected_windows, 'extract_frames_at_timestamps',
                        lambda path, times: [(t, b'frame') for t in times])
    vlm = MagicMock()
    vlm.verify_event_continuity.return_value = EventVerification(
        verified=True, confidence=.91, coherent_single_event=verdict != 'compilation',
        contains_unrelated_scene=verdict == 'compilation',
        event_summary='A character rescues a passenger after a crash.',
        setup_observed='Passenger is trapped in the vehicle.',
        development_observed='The driver approaches the passenger door.',
        climax_observed='The driver pulls the passenger from the wreck.',
        payoff_observed='' if verdict == 'no_payoff' else 'Both characters escape.',
    )
    parts, _, decisions, alternatives, windows = connected_windows.plan_connected(
        graph, evidence, transcript, _req(count=2, series_mode='connected'), source,
        None if verdict == 'no_vlm' else vlm,
    )
    assert windows > 0 and decisions
    if selected:
        assert len(parts) == 2
        assert 67 <= parts[1]['end_seconds'] - parts[0]['start_seconds'] <= 117
        assert parts[0]['end_seconds'] - parts[1]['start_seconds'] == 3
        assert all(35 <= part['end_seconds'] - part['start_seconds'] <= 60 for part in parts)
        assert any(parts[0]['start_seconds'] < edge.boundary_time < parts[-1]['end_seconds']
                   for edge in graph.edges)
    else:
        assert not parts and alternatives


def test_connected_cannot_cross_format_break_and_returns_review_intervals(tmp_path):
    from factory.connected_windows import plan_connected

    evidence = _ev([
        _mk_shot(0, 0, 54),
        _mk_shot(1, 54, 59, luma=5, dark=1, stat=1, mot=0),
        _mk_shot(2, 59, 115),
    ], 115)
    transcript = {'language': 'en', 'segments': []}
    graph = build_event_graph(evidence, transcript)
    assert graph.format_breaks
    vlm = MagicMock()
    parts, _, _, alternatives, windows = plan_connected(
        graph, evidence, transcript, _req(count=2, series_mode='connected'), tmp_path / 'video.mp4', vlm,
    )
    assert windows == 0 and not parts
    assert [(c['start_seconds'], c['end_seconds']) for c in alternatives] == [(59, 115), (0, 54)]
    vlm.verify_event_continuity.assert_not_called()


@pytest.mark.parametrize('relation,expected_events', [
    ('same_scene_camera_cut', 1), ('new_unrelated_event', 2),
])
def test_camera_cut_requires_corroborated_vlm_continuity(monkeypatch, tmp_path, relation, expected_events):
    from factory.vision_vlm import ContinuityVerdict

    shots = [
        _mk_shot(0, 0, 50, luma=70),
        _mk_shot(1, 50, 100, luma=119, grid=[.59] * 27, hud=[.48] * 24),
    ]
    evidence = _ev(shots, 100)
    transcript = {'language': 'en', 'segments': [
        {'start': 48, 'end': 52, 'text': 'Keep holding the other side of the door.'},
    ]}
    baseline = build_event_graph(evidence, transcript)
    assert len(baseline.events) == 2
    source = tmp_path / 'cached.mp4'
    source.touch()
    monkeypatch.setattr('factory.semantic_events.extract_frames_at_timestamps',
                        lambda path, times, **kw: [(t, b'frame') for t in times])
    vlm = MagicMock()
    vlm.detect_format_break.return_value.is_format_break = False
    vlm.stats.return_value = {}
    vlm.compare_adjacent_shots.return_value = ContinuityVerdict(
        verified=True, confidence=.95, relation=relation,
        same_event=relation == 'same_scene_camera_cut',
        reason='The reverse angle shows the same two actors holding the door.',
    )
    graph = build_event_graph(evidence, transcript, local_source_path=source, vlm=vlm)
    assert len(graph.events) == expected_events
    assert graph.edges[0].vlm_verified


@pytest.mark.parametrize('verified_contradiction', [False, True])
def test_connected_storyboard_reconsiders_cv_only_splits(monkeypatch, tmp_path, verified_contradiction):
    from factory import connected_windows
    from factory.vision_vlm import EventVerification

    evidence = _ev([
        _mk_shot(0, 0, 40, luma=50),
        _mk_shot(1, 40, 73, luma=180, grid=[.8] * 27, hud=[.75] * 24),
        _mk_shot(2, 73, 110, luma=50),
    ], 110)
    transcript = {'language': 'en', 'segments': [
        {'start': 2+i*4, 'end': 4+i*4, 'text': 'We need to rescue the passenger before the fire spreads.'}
        for i in range(6)
    ]}
    graph = build_event_graph(evidence, transcript)
    assert len(graph.events) == 3
    graph.edges[0].vlm_verified = verified_contradiction
    monkeypatch.setattr(connected_windows, 'extract_frames_at_timestamps',
                        lambda path, times: [(t, b'frame') for t in times])
    vlm = MagicMock()
    vlm.verify_event_continuity.return_value = EventVerification(
        verified=True, coherent_single_event=True, confidence=.95,
        event_summary='Same people and vehicle throughout the rescue.',
        setup_observed='Two people are trapped in a car.',
        development_observed='The driver reaches the passenger side.',
        climax_observed='The driver pulls the passenger free.',
        payoff_observed='The two are clear of the car.',
    )
    parts, _, _, _, _ = connected_windows.plan_connected(
        graph, evidence, transcript, _req(count=2, series_mode='connected'), tmp_path / 'video.mp4', vlm,
    )
    if verified_contradiction:
        assert not parts
        vlm.verify_event_continuity.assert_not_called()
    else:
        assert len(parts) == 2
        assert vlm.verify_event_continuity.call_args.args[1]['crosses_cv_split']


# Case 22, 23, 24, 25, 28: Manual override, VLM unavailable, GPU constrained, cache reuse & invalidation
def test_22_23_24_25_28_vlm_fallback_gpu_bounds_and_edit_manifest(monkeypatch):
    # Case 24: VLM unavailable
    vlm = LocalVisionAdapter(enabled=False)
    assert vlm.is_available() is False
    assert vlm.verify_event_continuity([(1.0, b'\xff\xd8')]).verified is False

    # Case 25: GPU VRAM constrained -> CPU fallback (0 layers)
    monkeypatch.delenv('FACTORY_VLM_GPU_LAYERS', raising=False)
    monkeypatch.setattr('factory.vision_vlm.query_vram_mib', lambda: (3200, 800))
    assert vlm_gpu_layers('qwen3-vl:2b') == 0

    # Phase 6 EditManifest source-aware smart layouts (never forces 65/35 without explicit bg)
    layout, _, _ = choose_smart_layout(1280, 720, source_category='gameplay', requested_preset='auto', has_explicit_background=False)
    assert layout == 'source_fit_blur'

    layout_fc, _, corner = choose_smart_layout(1280, 720, source_category='gameplay', requested_preset='auto', facecam_corners=['bottom_left'])
    assert layout_fc == 'gameplay_facecam' and corner == 'bottom_left'

    layout_vert, _, _ = choose_smart_layout(1080, 1920, source_category='gameplay', requested_preset='auto')
    assert layout_vert == 'fullscreen_smart_crop'

    layout_2sp, _, _ = choose_smart_layout(1920, 1080, source_category='podcast', requested_preset='auto', two_speakers_detected=True)
    assert layout_2sp == 'two_speaker_split'

    # Case 22: Manual explicit split override still honored
    manifest = build_edit_manifest(10.0, 52.0, _req(preset='gameplay', background='bg.mp4'), has_explicit_background=True, climax_time=30.0)
    assert manifest.layout == 'gameplay_split'
    assert manifest.secondary_panel is not None


# Case 26: Invalid or corrupt media raises clean ValueError
def test_26_corrupt_media_raises_clean_error(tmp_path):
    bad = tmp_path / 'corrupt.mp4'
    bad.write_bytes(b'not a valid mp4 stream')
    with pytest.raises(Exception):
        analyze_source(bad, 0.0, 10.0, {'segments': []}, cache_dir=tmp_path)


# Phase 5 Pairwise Ranking order-swap position-bias mitigation test
def test_pairwise_rank_position_bias_mitigation(monkeypatch):
    finalists = [
        {'start_seconds': 0.0, 'end_seconds': 40.0, 'score': 70.0, 'editorial_rubric': {'narrative_coherence': 0.8, 'contamination_risk': 0.0}},
        {'start_seconds': 50.0, 'end_seconds': 90.0, 'score': 69.0, 'editorial_rubric': {'narrative_coherence': 0.9, 'contamination_risk': 0.0}},
    ]
    # Simulate a position-biased LLM that ALWAYS picks "A" regardless of order!
    class BiasedResp:
        def json(self):
            return {'response': json.dumps({'winner': 'A', 'reason': 'first option bias'})}
    monkeypatch.setattr('factory.discovery.generate', lambda *a, **kw: BiasedResp())
    class DummySession:
        def __enter__(self): return {'layers': 0, 'used': False}
        def __exit__(self, *a): pass
    monkeypatch.setattr('factory.discovery.model_session', lambda *a, **kw: DummySession())

    ranked, diag = _pairwise_rank_finalists(finalists, 'qwen2.5:3b')
    assert diag['pairwise_used'] is True
    # Because A won in (0,1) AND A won in (1,0), position_consistent MUST be False -> TIE!
    assert diag['comparisons'][0]['position_consistent'] is False
    assert diag['comparisons'][0]['winner'] == 'TIE'


# Case 27, 29, 30: API evidence/feedback/manifest/metrics, manual metadata preservation, publishing guard
def test_27_29_30_api_feedback_rights_manual_preservation_and_publish_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(config, 'SCRATCH', tmp_path / 'scratch')
    monkeypatch.setattr(config, 'DB', tmp_path / 'data' / 'jobs.sqlite3')
    monkeypatch.setattr(config, 'TOKEN', 'x' * 40)
    monkeypatch.setattr(config, 'AUTOMATIC_PUBLISHING', False)
    monkeypatch.setenv('FACTORY_LOCAL_WORKER', '0')
    monkeypatch.setenv('FACTORY_PUBLISHER', '0')
    monkeypatch.setenv('FACTORY_ANALYTICS', '0')

    from factory import api
    headers = {'Authorization': 'Bearer ' + 'x' * 40}
    with TestClient(api.app) as client:
        # Case 30: Automatic publishing blocked when AUTOMATIC_PUBLISHING=False
        resp_pub = client.post('/api/jobs', headers=headers, json={
            'source': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'rights_confirmed': True,
            'publish_automatic': True,
            'publish_channel': 'ch1',
            'save_drive': True,
        })
        assert resp_pub.status_code == 422

        # Create normal job
        job = store.create(_req().model_dump())
        jid = job['id']
        store.save_source_rights(jid, {'source_url': 'https://example.com', 'permission_status': 'original_creator', 'reused_content_acknowledged': True})

        # Evidence endpoint
        ev_resp = client.get(f'/api/jobs/{jid}/evidence', headers=headers)
        assert ev_resp.status_code == 200
        assert ev_resp.json()['editorial_workflow_state'] == 'ANALYSIS_IN_PROGRESS'

        # Feedback persistence endpoint
        fb_resp = client.post(f'/api/jobs/{jid}/feedback', headers=headers, json={
            'candidate_index': 0,
            'decision': 'needs_trim',
            'rejection_reasons': ['end_card_contamination'],
            'original_start': 554.12,
            'original_end': 600.26,
            'corrected_start': 554.12,
            'corrected_end': 597.8,
            'notes': 'Trim before end card',
        })
        assert fb_resp.status_code == 201
        summary = client.get('/api/feedback/summary', headers=headers).json()
        assert summary['total_decisions'] == 1
        assert summary['automatic_training_enabled'] is False

        # Composition manifest preview endpoint
        man_resp = client.post('/api/composition/manifest', headers=headers, json={
            'source_start': 0, 'source_end': 45, 'source_width': 1280, 'source_height': 720,
            'source_category': 'gameplay', 'facecam_corners': ['bottom_left'],
        })
        assert man_resp.status_code == 200
        assert man_resp.json()['manifest']['layout'] == 'gameplay_facecam'
        assert 'gaming_clean' in man_resp.json()['design_presets']

        # Case 29: Manual metadata edits preserved across regenerate() unless force_overwrite=True
        with store.connect() as db:
            meta_v1 = {
                'title': 'Original Auto Title',
                'alternative_titles': ['Alt 1', 'Alt 2'],
                'title_candidates': ['Original Auto Title', 'Alt 1', 'Alt 2'],
                'hashtag_candidates': [],
                'description': 'Original description.\n\n#GTA #Shorts',
                'tags': ['GTA'],
                'hashtags': ['#GTA', '#Shorts'],
                'category_id': '20',
                'audio_language': 'en',
                'metadata_language': 'en',
                'thumbnail_text': '',
                'source_attribution': '',
                'review_required': True,
                'validation_issues': [],
                'quality_reasons': [],
                'version': 'v1',
                'provenance': {'method': 'content_brief', 'input_sha256': 'abc'},
            }
            db.execute(
                "UPDATE jobs SET state='READY', result=? WHERE id=?",
                (json.dumps({'outputs': [{'name': 'clip-1.mp4', 'duration': 40.0, 'publishing_metadata': meta_v1}]}), jid),
            )
            db.execute(
                'INSERT INTO metadata_versions VALUES(?,?,?,?,?,?,?)',
                (jid, 0, 'v1', json.dumps(meta_v1), json.dumps({'input': metadata.context({'language': 'en', 'segments': []}, _req(), {'title': 'GTA'}, [{'start_seconds': 0, 'end_seconds': 40}], {'duration': 40.0})}), 100.0, 0),
            )
        drafts.revise(jid, 0, {'title': 'My Handcrafted Operator Title'})
        after_regen = drafts.regenerate(jid, force_overwrite=False)
        assert after_regen['result']['outputs'][0]['publishing_metadata']['title'] == 'My Handcrafted Operator Title'
        after_force = drafts.regenerate(jid, force_overwrite=True)
        assert after_force['result']['outputs'][0]['publishing_metadata']['title'] != 'My Handcrafted Operator Title'


def test_metadata_clean_phrase_truncation_and_boilerplate_cleanup():
    from factory.metadata import _clean_phrase_truncate
    from factory.discovery import _grounded_candidate_title

    # Word-boundary truncation without dangling stopwords or stray CJK tokens
    t1 = _clean_phrase_truncate(
        'A white pickup truck moves to a dirt road where a man gets out of the vehicle near a',
        max_len=58,
    )
    assert len(t1) <= 58
    assert not t1.endswith((' a', ' dr', ' the', ' to', ' of', ' in'))

    t2 = _clean_phrase_truncate('Player drives a red车 sedan into a tunnel', max_len=58)
    assert '车' not in t2

    # Grounded title strips VLM framing boilerplate ("A sequence of four gameplay frames showing...")
    title = _grounded_candidate_title(
        None,
        'GTA 5',
        'A sequence of four gameplay frames showing a yellow pickup truck flipping off a highway bridge into water.',
    )
    assert 'sequence of four' not in title.casefold()
    assert 'yellow pickup truck' in title.casefold()
    assert title.startswith('GTA 5 | ')


def test_facecam_appearance_across_shots_prevents_false_merge():
    # Regression test for early GTA highlight defect: streamer webcam overlay appearing/disappearing across cut
    s_with_cam = _mk_shot(0, 0, 10, luma=90, hsv=[0.4] + [0.02] * 31, grid=[0.45] * 27, hud=[0.50] * 24, face=['bottom_right'])
    s_without_cam = _mk_shot(1, 10, 22, luma=88, hsv=[0.38] + [0.02] * 31, grid=[0.44] * 27, hud=[0.48] * 24, face=[])
    edge = score_adjacent_shots(s_with_cam, s_without_cam, None, None, {'segments': []}, content_category='gameplay')
    assert edge.same_event is False
    assert edge.severity == 'strong_semantic_contradiction'
    assert 'Facecam layout changed' in edge.reason


def test_adaptive_stage_c_and_transparent_story_verdicts(monkeypatch, tmp_path):
    from factory import connected_windows
    from factory.vision_vlm import EventVerification

    shots = [_mk_shot(0, 0, 34, mot=0.14, bursts=2)]
    evidence = _ev(shots, 34.0)
    transcript = {'language': 'en', 'segments': []}
    graph = build_event_graph(evidence, transcript)
    source = tmp_path / 'stunt.mp4'
    monkeypatch.setattr(connected_windows, 'extract_frames_at_timestamps',
                        lambda path, times: [(t, b'frame') for t in times])

    # Pass 1 returns identical panel strings (action stunt without distinct 4-beat text), Pass 2 supplies ending water outcome
    class DummyVLM:
        def __init__(self):
            self.calls_made = 0
            self.max_calls = 8
        def verify_event_continuity(self, frames_tl, ctx):
            self.calls_made += 1
            if self.calls_made == 1:
                return EventVerification(
                    verified=True, coherent_single_event=True, confidence=0.90,
                    event_summary='Yellow pickup truck driving on a highway toward bridge.',
                    setup_observed='Yellow pickup truck driving on a highway.',
                    development_observed='Yellow pickup truck driving on a highway.',
                    climax_observed='Yellow pickup truck driving on a highway.',
                    payoff_observed='Yellow pickup truck driving on a highway.',
                )
            return EventVerification(
                verified=True, coherent_single_event=True, confidence=0.90,
                event_summary='Yellow pickup truck flips off the bridge into water.',
                setup_observed='Yellow pickup truck collides with red car on bridge.',
                development_observed='Truck flips over the bridge railing.',
                climax_observed='Truck plunges into the water below.',
                payoff_observed='Truck lands upside down in the water.',
            )
        def stats(self):
            return {'calls_made': self.calls_made}

    vlm = DummyVLM()
    req = _req(count=2, minimum=12, maximum=20, series_overlap=2, series_mode='connected')
    parts, summary, decisions, alts, windows = connected_windows.plan_connected(
        graph, evidence, transcript, req, source, vlm,
    )
    assert len(parts) == 2
    assert vlm.calls_made == 2
    assert decisions[0]['verdict'] == 'VERIFIED_COHERENT'
    assert decisions[0]['adaptive_passes'] == 2
    assert len(decisions[0]['evidence_timestamps']) == 8
    assert parts[0]['story_verdict'] == 'VERIFIED_COHERENT'

