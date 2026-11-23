"""Unit & regression tests for Phase 3 Semantic Event Graph & Format-Break Detection."""
from factory.semantic_events import (
    build_event_graph,
    classify_format_break,
    refine_event_boundaries,
    score_adjacent_shots,
)
from factory.source_intelligence import Shot, SourceContext, SourceEvidence


def _shot(sid, start, end, luma=95.0, sat=0.20, hisat=0.03, red=0.02, txt=0.02, mot=0.09, stat=0.05, dark=0.0,
          hsv=None, grid=None, hud=None, speech='', words=0, face=None, btype='hard_cut'):
    return Shot(
        shot_id=sid,
        start=start,
        end=end,
        duration=end - start,
        boundary_type='initial' if sid == 0 else btype,
        boundary_score=0.7 if sid > 0 else 0.0,
        sample_count=max(2, int((end - start) * 2)),
        hsv_hist=hsv or [0.0625] * 16 + [0.125] * 16,
        spatial_grid=grid or [0.4] * 27,
        corner_hud=hud or [0.3] * 24,
        mean_luma=luma,
        mean_sat=sat,
        high_sat_frac=hisat,
        red_tint_score=red,
        center_text_band_score=txt,
        mean_motion=mot,
        peak_motion=mot * 1.5,
        static_fraction=stat,
        dark_fraction=dark,
        face_corners=face or [],
        speech_text=speech,
        speech_word_count=words,
        speech_segment_count=1 if words else 0,
    )


def test_detects_end_card_and_trims_event_before_it():
    s0 = _shot(0, 530.0, 580.0, luma=85.0, speech='swap the truck', words=20)
    s1 = _shot(1, 580.0, 598.0, luma=90.0, speech='get a bison dually', words=18)
    s2 = _shot(2, 598.0, 617.9, luma=60.0, sat=0.72, hisat=0.82, txt=0.08, mot=0.006, stat=0.95)
    ev = SourceEvidence(
        context=SourceContext(source_id='test', source_fingerprint='fp1', start=0, end=617.9, duration=617.9, content_category='gameplay'),
        shots=[s0, s1, s2],
    )
    graph = build_event_graph(ev, {'segments': [{'start': 554.0, 'end': 597.8, 'text': 'swap truck get a bison'}]})
    assert any(b.kind == 'end_card' and b.shot_id == 2 for b in graph.format_breaks)
    assert len(graph.events) == 1
    event = graph.events[0]
    assert event.shot_ids == [0, 1]
    assert event.end <= 598.0
    assert event.terminated_by_break == 'end_card'
    refined = refine_event_boundaries(event, 35.0, 60.0, {'segments': [{'start': 554.0, 'end': 597.8, 'text': 'swap'}]}, 617.9)
    assert refined['status'] == 'valid'
    assert refined['end_seconds'] <= 597.8


def test_separates_unrelated_driving_boat_night_and_title_card():
    # Driving -> Title Card -> Boat -> Night street (even with continuous audio!)
    hsv_car = [0.5, 0.2] + [0.02] * 30
    hsv_boat = [0.02] * 8 + [0.6, 0.2] + [0.01] * 22
    hsv_night = [0.01] * 24 + [0.8, 0.1] + [0.01] * 6
    s0 = _shot(0, 0.0, 24.0, luma=65.0, hsv=hsv_car, grid=[0.25] * 27, hud=[0.1] * 24)
    s1 = _shot(1, 24.0, 28.0, luma=73.0, sat=0.55, hisat=0.60, txt=0.16, mot=0.02, stat=0.75)  # title card
    s2 = _shot(2, 28.0, 46.0, luma=135.0, hsv=hsv_boat, grid=[0.65] * 27, hud=[0.7] * 24)  # daytime boat
    s3 = _shot(3, 46.0, 65.0, luma=25.0, hsv=hsv_night, grid=[0.1] * 27, hud=[0.2] * 24)  # nighttime street
    ev = SourceEvidence(
        context=SourceContext(source_id='test', source_fingerprint='fp2', start=0, end=65.0, duration=65.0, content_category='gameplay'),
        shots=[s0, s1, s2, s3],
    )
    # Even if a transcript spans 20-60s, strong visual contradiction must NOT merge them!
    transcript = {'segments': [{'start': 20.0, 'end': 60.0, 'text': 'continuous background commentary across clips'}]}
    graph = build_event_graph(ev, transcript)
    assert any(b.kind == 'title_card' and b.shot_id == 1 for b in graph.format_breaks)
    assert len(graph.events) == 3
    assert [e.shot_ids for e in graph.events] == [[0], [2], [3]]


def test_anti_padding_rule_refuses_to_pad_22s_event_to_35s():
    s0 = _shot(0, 100.0, 122.0, luma=95.0, mot=0.12)
    ev = SourceEvidence(
        context=SourceContext(source_id='test', source_fingerprint='fp3', start=0, end=200.0, duration=200.0, content_category='gameplay'),
        shots=[s0],
    )
    graph = build_event_graph(ev, {'segments': []})
    refined = refine_event_boundaries(graph.events[0], 35.0, 60.0, {'segments': []}, 200.0)
    assert refined['status'] == 'duration_conflict'
    assert refined['start_seconds'] == 100.0
    assert refined['end_seconds'] == 122.0
    assert refined['duration'] == 22.0
    assert refined['duration_conflict']['requested_minimum'] == 35.0


def test_podcast_speaker_cut_stays_same_event():
    s0 = _shot(0, 0.0, 20.0, luma=110.0, hsv=[0.3] + [0.02] * 31, grid=[0.4] * 27, speech='First speaker explains the idea.', words=15)
    s1 = _shot(1, 20.0, 42.0, luma=95.0, hsv=[0.02] * 4 + [0.3] + [0.02] * 27, grid=[0.5] * 27, speech='Second speaker responds directly.', words=16)
    edge = score_adjacent_shots(s0, s1, None, None, {'segments': []}, content_category='podcast')
    assert edge.same_event is True
    assert edge.relation == 'same_scene_camera_cut'
