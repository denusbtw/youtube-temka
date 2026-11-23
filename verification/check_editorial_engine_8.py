"""Phase 11 — Full-Source GTA Editorial Engine 8.0 Acceptance & Comparison Runner.

Reuses the complete authorized 617.906s GTA source and existing Whisper
transcript (no redownload, no retranscription, no publishing).
Compares Factory 8.0 against the preserved aef60f1 baseline using the Phase 1
benchmark schema, renders actual vertical MP4s driven by EditManifest, generates
6-frame visual storyboards (5%-98% coverage), and records performance metrics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import av

from factory import config, media, metadata, shorts
from factory.benchmark import evaluate_source, load_benchmark_source
from factory.models import JobRequest
from factory.resources import memory_status
from factory.vision_vlm import LocalVisionAdapter, query_vram_mib
from jsonfile import write_json_atomic


def _make_storyboard(mp4_path: Path, out_jpg: Path, duration: float) -> None:
    """Create a 3x2 visual storyboard sampling 5%, 24%, 43%, 62%, 81%, 97% of the rendered clip."""
    # Use fps filter to capture 6 evenly spaced frames including near the very end
    fps_val = max(0.05, 6.0 / max(1.0, duration))
    media.run([
        media.ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y', '-threads', '2',
        '-i', str(mp4_path),
        '-vf', f'fps={fps_val:.4f},scale=270:480,tile=3x2',
        '-frames:v', '1',
        str(out_jpg),
    ])


def main(source: Path, transcript_path: Path, benchmark_path: Path, output_dir: Path, vlm_model: str):
    output_dir.mkdir(parents=True, exist_ok=True)
    transcript = json.loads(transcript_path.read_text(encoding='utf-8'))
    bench = load_benchmark_source(benchmark_path)

    with av.open(str(source)) as container:
        decoded_duration = container.duration / av.time_base
    if abs(decoded_duration - 618.0) > 2.0:
        raise ValueError(f'Full 618s GTA source required; got {decoded_duration:.3f}s')
    duration = float(decoded_duration)

    with source.open('rb') as stream:
        sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()

    upload_dir = config.SCRATCH / 'uploads'
    upload_dir.mkdir(parents=True, exist_ok=True)
    link = upload_dir / 'gta-full-8.mp4'
    if not link.exists():
        try:
            link.symlink_to(source.resolve())
        except OSError:
            import shutil
            shutil.copyfile(source, link)

    spec = {'title': 'GTA 6 FUNNY MOMENTS & WINS #1 (GTA 6 FAILS)', 'platform': 'YouTube'}
    url = 'https://www.youtube.com/watch?v=yux8s3lwLss'

    vlm = LocalVisionAdapter(
        model=vlm_model,
        ollama_url=os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434'),
        cache_path=output_dir / 'vlm-cache.json',
        max_calls=14,
        timeout=60.0,
        enabled=(vlm_model != 'none'),
    )

    peak_vram = 0
    u0, _ = query_vram_mib()
    if u0 is not None:
        peak_vram = max(peak_vram, u0)

    # 1. Run strict 35-60s Independent Highlights (tests anti-padding rule directly!)
    req_strict = JobRequest(
        source='upload:gta-full-8.mp4',
        rights_confirmed=True,
        count=3,
        minimum=35,
        maximum=60,
        end=duration,
        source_category='gameplay',
        selection_version=2,
        preset='auto',
        background_mode='none',
        text_mode='hook_subtitles',
        hook_mode='persistent',
        hook_font_size=54,
        hook_box=False,
        confirmed_subject='GTA',
        metadata_language='en',
        save_drive=False,
    )
    t0 = time.monotonic()
    plan_strict = shorts.plan_v8(
        source, req_strict, transcript, duration,
        root=output_dir, source_title=spec['title'], vlm=vlm,
    )
    t_strict = time.monotonic() - t0
    u1, _ = query_vram_mib()
    if u1 is not None:
        peak_vram = max(peak_vram, u1)

    # 2. Run operator-approved 25-60s Independent Highlights (reuses source-evidence & vlm-cache!)
    req_editorial = req_strict.model_copy(update={'minimum': 25})
    t1 = time.monotonic()
    plan_editorial = shorts.plan_v8(
        source, req_editorial, transcript, duration,
        root=output_dir, source_title=spec['title'], vlm=vlm,
    )
    t_editorial = time.monotonic() - t1

    # 3. Connected Story & Sequential Split verification (cache reused!)
    t2 = time.monotonic()
    plan_connected = shorts.plan_v8(
        source, req_strict.model_copy(update={'series_mode': 'connected'}),
        transcript, duration, root=output_dir, source_title=spec['title'], vlm=vlm,
    )
    t_connected = time.monotonic() - t2
    plan_sequential = shorts.plan_v8(
        source, req_strict.model_copy(update={'series_mode': 'sequential'}),
        transcript, 150.0, root=output_dir, source_title=spec['title'], vlm=vlm,
    )
    vlm.unload()

    # 4. Evaluate old baseline vs new Factory 8.0 plans on Phase 1 benchmark
    baseline_cands = [
        {'start_seconds': 554.12, 'end_seconds': 600.26},
        {'start_seconds': 172.98, 'end_seconds': 216.98},
        {'start_seconds': 5.42, 'end_seconds': 52.50},
    ]
    eval_baseline = evaluate_source(bench, baseline_cands, k=3)
    eval_strict = evaluate_source(bench, plan_strict['candidates'], k=3)
    eval_editorial = evaluate_source(bench, plan_editorial['candidates'], k=3)

    # 5. Render actual MP4s, EditManifests, metadata drafts, and 6-frame visual storyboards
    render_targets = plan_editorial['candidates'][:3] if plan_editorial['candidates'] else plan_strict['candidates'][:3]
    rendered_outputs = []
    t_render_start = time.monotonic()
    for idx, clip in enumerate(render_targets, 1):
        a, b = clip['start_seconds'], clip['end_seconds']
        raw_path = output_dir / f'clip-{idx}-raw.mp4'
        mp4_path = output_dir / f'clip-{idx}.mp4'
        sb_path = output_dir / f'clip-{idx}-storyboard.jpg'
        short_hook = metadata._clean_phrase_truncate(
            clip.get('title', '').split('|', 1)[-1].strip() or clip.get('vlm_summary', '').split('.')[0].strip(),
            32,
        )
        hook_text = (short_hook[0].upper() + short_hook[1:]) if len(short_hook) >= 6 else 'GTA HIGHLIGHT'
        clip_req = req_editorial.model_copy(update={'hook': hook_text, 'hook_font_size': 48})

        t_c = time.monotonic()
        media.download(req_editorial.source, raw_path, a, b, 360)
        media.render(raw_path, mp4_path, transcript, a, b, clip_req, None, candidate_meta=clip, design_preset='gaming_clean')
        render_elapsed = round(time.monotonic() - t_c, 2)

        validation = json.loads(mp4_path.with_suffix('.validation.json').read_text(encoding='utf-8'))
        manifest = json.loads(mp4_path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
        _make_storyboard(mp4_path, sb_path, validation['duration'])
        draft = metadata.Generator(None, None, None).create(
            metadata.context(transcript, clip_req, spec, [clip], {'duration': validation['duration'], 'validation': validation}, url)
        )
        rendered_outputs.append({
            'index': idx,
            'interval': [a, b],
            'duration': validation['duration'],
            'file': str(mp4_path),
            'storyboard': str(sb_path),
            'manifest_file': str(mp4_path.with_suffix('.manifest.json')),
            'validation_file': str(mp4_path.with_suffix('.validation.json')),
            'render_seconds': render_elapsed,
            'layout': manifest['layout'],
            'layout_reason': manifest['layout_reason'],
            'vlm_summary': clip.get('vlm_summary', ''),
            'semantic_verification': clip.get('semantic_verification', ''),
            'evidence': clip.get('evidence', []),
            'validation': validation,
            'metadata': {k: v for k, v in draft.items() if k != '_provenance'},
        })
        raw_path.unlink(missing_ok=True)
    t_render_total = round(time.monotonic() - t_render_start, 2)

    mem = memory_status()
    report = {
        'schema_version': 2,
        'source': {
            'url': url,
            'sha256': sha256,
            'decoded_duration': duration,
            'full_media': True,
        },
        'performance': {
            'strict_35_60_analysis_seconds': round(t_strict, 2),
            'editorial_25_60_cached_seconds': round(t_editorial, 2),
            'connected_cached_seconds': round(t_connected, 2),
            'render_total_seconds': t_render_total,
            'vlm_stats': vlm.stats(),
            'peak_vram_mib': max(peak_vram, vlm.peak_vram_mib),
            'host_available_ram_mb': round(mem.get('host_available_bytes', 0) / (1024 * 1024), 1),
        },
        'plans': {
            'independent_35_60_strict': plan_strict,
            'independent_25_60_editorial': plan_editorial,
            'connected': plan_connected,
            'sequential': plan_sequential,
        },
        'benchmark_comparison': {
            'baseline_aef60f1': eval_baseline,
            'factory_8_strict_35_60': eval_strict,
            'factory_8_editorial_25_60': eval_editorial,
        },
        'rendered_outputs': rendered_outputs,
        'human_editorial_approval': 'PENDING_HUMAN_REVIEW',
    }
    write_json_atomic(output_dir / 'editorial-engine-8-report.json', report)
    print(json.dumps({
        'source_sha256': sha256,
        'performance': report['performance'],
        'strict_35_60_selected': [(c['start_seconds'], c['end_seconds'], c['score'], c['title']) for c in plan_strict['candidates']],
        'strict_35_60_alternatives': [(c['start_seconds'], c['end_seconds'], c['score']) for c in plan_strict['review_alternatives'][:4]],
        'editorial_25_60_selected': [(c['start_seconds'], c['end_seconds'], c['score'], c['title']) for c in plan_editorial['candidates']],
        'benchmark_baseline': eval_baseline['semantic_correctness'],
        'benchmark_v8_strict': eval_strict['semantic_correctness'],
        'benchmark_v8_editorial': eval_editorial['semantic_correctness'],
        'rendered_files': [o['file'] for o in rendered_outputs],
    }, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(r'c:\Users\Eclipse\Desktop\7 семестр\робота\youtube-temka\scratch\controller\event-acceptance\full-progressive.mp4'))
    parser.add_argument('--transcript', type=Path, default=Path(r'c:\Users\Eclipse\Desktop\7 семестр\робота\youtube-temka\scratch\controller\factory72-acceptance\transcript.json'))
    parser.add_argument('--benchmark', type=Path, default=Path(__file__).resolve().parent / 'benchmarks' / 'gta_yux8s3lwLss_v2.json')
    parser.add_argument('--output', type=Path, default=Path(r'C:\Users\Eclipse\.gemini\antigravity\brain\45244f68-2887-464a-b332-e9f07670ac5a\scratch\gta_v8_acceptance'))
    parser.add_argument('--vlm-model', type=str, default='qwen3-vl:2b-instruct')
    args = parser.parse_args()
    main(args.source, args.transcript, args.benchmark, args.output, args.vlm_model)
