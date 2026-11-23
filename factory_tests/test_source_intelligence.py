"""Tests for Phase 2 Unified Source Intelligence Engine."""
import subprocess
from pathlib import Path

from factory import media
from factory.source_intelligence import analyze_source, extract_frames_at_timestamps


def _make_multiscene_mp4(path: Path) -> Path:
    """Create a 12s synthetic video with 3 visually distinct shots + audio burst."""
    cmd = [
        media.ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:d=4:r=15',
        '-f', 'lavfi', '-i', 'color=c=red:s=320x180:d=4:r=15',
        '-f', 'lavfi', '-i', 'color=c=green:s=320x180:d=4:r=15',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=12',
        '-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
        '-map', '[v]', '-map', '3:a',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '64k',
        str(path),
    ]
    subprocess.run(cmd, check=True)
    return path


def test_source_intelligence_detects_shots_and_caches(tmp_path):
    video = _make_multiscene_mp4(tmp_path / 'multi.mp4')
    transcript = {
        'language': 'en',
        'segments': [
            {'start': 1.0, 'end': 3.5, 'text': 'Watch this blue scene!'},
            {'start': 8.5, 'end': 11.0, 'text': 'Oh my god green ending!'},
        ],
    }
    ev = analyze_source(video, 0.0, 12.0, transcript, cache_dir=tmp_path, content_category='gameplay')
    assert ev.schema_version == 1
    assert ev.context.width == 320 and ev.context.height == 180
    assert len(ev.shots) >= 3
    assert len(ev.audio_events) >= 20
    # Middle shot should have elevated red_tint_score
    red_shots = [s for s in ev.shots if s.start >= 3.0 and s.end <= 9.0]
    assert any(s.red_tint_score > 0.4 for s in red_shots)
    # Speech aligned to shots
    assert any('blue scene' in s.speech_text for s in ev.shots)
    assert any(s.has_reaction_cue for s in ev.shots)
    # Cache file created and reused
    cache_file = tmp_path / 'source-evidence-v1.json'
    assert cache_file.is_file()
    ev2 = analyze_source(video, 0.0, 12.0, transcript, cache_dir=tmp_path, content_category='gameplay')
    assert ev2.context.source_fingerprint == ev.context.source_fingerprint
    assert len(ev2.shots) == len(ev.shots)


def test_extract_frames_at_timestamps(tmp_path):
    video = _make_multiscene_mp4(tmp_path / 'multi.mp4')
    frames = extract_frames_at_timestamps(video, [1.0, 5.5, 10.0], max_width=240)
    assert len(frames) == 3
    for ts, jpg in frames:
        assert jpg[:2] == b'\xff\xd8'
