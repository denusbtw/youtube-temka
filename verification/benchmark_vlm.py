"""Phase 4 Empirical Multimodal VLM Benchmark on RTX 3050 4 GB.

Evaluates qwen3-vl:2b, moondream:latest, and cv_fallback across 10 required
frame-sequence test scenarios (real GTA gameplay/cards/cuts + talking-head
speaker switching). Measures semantic accuracy, false merges, false splits,
end-card detection, schema validity, latency, peak VRAM (MiB), and RAM.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

from factory.resources import memory_status
from factory.source_intelligence import extract_frames_at_timestamps
from factory.vision_vlm import LocalVisionAdapter, query_vram_mib


def build_cases(gta_path: Path, speech_path: Path | None) -> list[dict]:
    """Build 10 representative real-frame test cases matching Phase 4.3 spec."""
    cases = [
        # 1. Coherent GTA multi-shot event (truck swap 556s -> 585s, same sunset truck event)
        {
            'id': '01_coherent_gta_event',
            'op': 'compare',
            'path': gta_path,
            'ts_a': [558.0],
            'ts_b': [584.0],
            'expected_same': True,
            'expected_break': False,
            'note': 'Same GTA truck swap sequence across a camera angle cut',
        },
        # 2. Driving followed by unrelated boat scene (177s car interior -> 196s ocean boat)
        {
            'id': '02_driving_to_unrelated_boat',
            'op': 'compare',
            'path': gta_path,
            'ts_a': [177.0],
            'ts_b': [196.0],
            'expected_same': False,
            'expected_break': False,
            'note': 'Dark car driver interior -> bright daytime ocean boat',
        },
        # 3. Gameplay followed by title card (22s gameplay -> 27.5s PUMBA PRESENTS)
        {
            'id': '03_gameplay_to_title_card',
            'op': 'format_break',
            'path': gta_path,
            'ts': [27.5],
            'expected_break': True,
            'expected_kind': 'title_card',
            'note': 'PUMBA PRESENTS neon sunset intro title card',
        },
        # 4. Title card followed by aircraft footage (27.5s PUMBA -> 51.5s airplane)
        {
            'id': '04_title_card_to_aircraft',
            'op': 'compare',
            'path': gta_path,
            'ts_a': [27.5],
            'ts_b': [51.5],
            'expected_same': False,
            'expected_break': True,
            'note': 'Title card followed by unrelated aircraft footage',
        },
        # 5. Scene cuts inside one continuous action (416s yellow truck highway -> 435s yellow truck on bridge)
        {
            'id': '05_cuts_within_same_action',
            'op': 'compare',
            'path': gta_path,
            'ts_a': [418.0],
            'ts_b': [435.0],
            'expected_same': True,
            'expected_break': False,
            'note': 'Yellow pickup truck driving -> flipping off bridge rail',
        },
        # 6. Two unrelated scenes sharing similar daytime street appearance (460s mobility scooter -> 545s highway pickup)
        {
            'id': '06_unrelated_similar_daytime_streets',
            'op': 'compare',
            'path': gta_path,
            'ts_a': [460.0],
            'ts_b': [545.0],
            'expected_same': False,
            'expected_break': False,
            'note': 'Mobility scooter neighborhood vs highway pickup truck',
        },
        # 7. End card without long silence (602s PUMBA end card)
        {
            'id': '07_end_card_with_audio',
            'op': 'format_break',
            'path': gta_path,
            'ts': [602.0],
            'expected_break': True,
            'expected_kind': 'end_card',
            'note': 'PUMBA branded outro end card with background audio',
        },
        # 8. Gameplay with continuous background music (448s -> 472s mobility scooter)
        {
            'id': '08_gameplay_continuous_music',
            'op': 'compare',
            'path': gta_path,
            'ts_a': [448.0],
            'ts_b': [472.0],
            'expected_same': True,
            'expected_break': False,
            'note': 'Continuous mobility scooter gameplay',
        },
        # 9. Gameplay with no speech (288s -> 301s streamer airplane crash)
        {
            'id': '09_silent_gameplay_action',
            'op': 'compare',
            'path': gta_path,
            'ts_a': [288.0],
            'ts_b': [301.0],
            'expected_same': True,
            'expected_break': False,
            'note': 'No-speech streamer jetliner flying into skyscraper',
        },
        # 10. Interstitial card ("A FEW MOMENTS LATER" at 172.5s) / or podcast cut if speech fixture exists
        {
            'id': '10_interstitial_or_speaker',
            'op': 'format_break',
            'path': gta_path,
            'ts': [172.5],
            'expected_break': True,
            'expected_kind': 'interstitial',
            'note': 'A FEW MOMENTS LATER red interstitial transition card',
        },
    ]
    if speech_path and speech_path.is_file():
        cases.append({
            'id': '11_talking_head_continuity',
            'op': 'compare',
            'path': speech_path,
            'ts_a': [2.0],
            'ts_b': [8.0],
            'expected_same': True,
            'expected_break': False,
            'note': 'Continuous speaker talking-head video',
        })
    return cases


def evaluate_model(model_name: str, cases: list[dict], ollama_url: str) -> dict:
    enabled = model_name != 'cv_fallback'
    adapter = LocalVisionAdapter(
        model=model_name if enabled else 'none',
        ollama_url=ollama_url,
        cache_path=None,
        max_calls=len(cases) + 2,
        timeout=90.0,
        enabled=enabled,
    )
    results = []
    latencies = []
    correct = 0
    false_merges = 0
    false_splits = 0
    end_card_hits = 0
    end_card_total = 0
    schema_valid = 0
    vram_samples = []

    used0, free0 = query_vram_mib()
    if used0 is not None:
        vram_samples.append(used0)

    for case in cases:
        t0 = time.monotonic()
        if case['op'] == 'compare':
            fa = [img for _, img in extract_frames_at_timestamps(case['path'], case['ts_a'], max_width=320)]
            fb = [img for _, img in extract_frames_at_timestamps(case['path'], case['ts_b'], max_width=320)]
            verdict = adapter.compare_adjacent_shots(fa, fb, {'case_id': case['id']})
            elapsed = time.monotonic() - t0
            latencies.append(elapsed)
            ok = verdict.verified and (verdict.same_event == case['expected_same'])
            if verdict.verified:
                schema_valid += 1
            if ok:
                correct += 1
            elif verdict.verified:
                if verdict.same_event and not case['expected_same']:
                    false_merges += 1
                elif not verdict.same_event and case['expected_same']:
                    false_splits += 1
            results.append({
                'id': case['id'],
                'op': 'compare',
                'expected_same': case['expected_same'],
                'predicted_same': verdict.same_event,
                'relation': verdict.relation,
                'reason': verdict.reason,
                'verified': verdict.verified,
                'correct': ok,
                'latency_s': round(elapsed, 3),
            })
        else:
            fr = [img for _, img in extract_frames_at_timestamps(case['path'], case['ts'], max_width=320)]
            verdict = adapter.detect_format_break(fr, {'case_id': case['id']})
            elapsed = time.monotonic() - t0
            latencies.append(elapsed)
            if case.get('expected_break'):
                end_card_total += 1
                if verdict.is_format_break:
                    end_card_hits += 1
            ok = verdict.verified and (verdict.is_format_break == case['expected_break'])
            if verdict.verified:
                schema_valid += 1
            if ok:
                correct += 1
            results.append({
                'id': case['id'],
                'op': 'format_break',
                'expected_break': case['expected_break'],
                'predicted_break': verdict.is_format_break,
                'break_kind': verdict.break_kind,
                'reason': verdict.reason,
                'verified': verdict.verified,
                'correct': ok,
                'latency_s': round(elapsed, 3),
            })
        used, _ = query_vram_mib()
        if used is not None:
            vram_samples.append(used)
        last = results[-1]
        print(f"  [{model_name}] {last['id']}: ok={last['correct']} verified={last['verified']} {last['latency_s']}s vram={used}MiB", flush=True)

    adapter.unload()
    mem = memory_status()
    return {
        'model': model_name,
        'available': adapter.is_available() if enabled else False,
        'cases_total': len(cases),
        'correct': correct,
        'accuracy': round(correct / max(1, len(cases)), 4),
        'false_continuity_merges': false_merges,
        'false_discontinuity_splits': false_splits,
        'format_break_recall': round(end_card_hits / max(1, end_card_total), 4) if end_card_total else None,
        'schema_valid_rate': round(schema_valid / max(1, len(cases)), 4),
        'median_latency_s': round(statistics.median(latencies), 3) if latencies else 0.0,
        'total_latency_s': round(sum(latencies), 3),
        'peak_vram_mib': max(vram_samples) if vram_samples else None,
        'host_available_ram_mb': round(mem.get('host_available_bytes', 0) / (1024 * 1024), 1),
        'failures': adapter.failures,
        'details': results,
    }


def main():
    gta = Path(r'c:\Users\Eclipse\Desktop\7 семестр\робота\youtube-temka\scratch\controller\event-acceptance\full-progressive.mp4')
    speech = Path(__file__).resolve().parent / 'source-en.mp4'
    ollama_url = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434')
    cases = build_cases(gta, speech if speech.is_file() else None)
    report = {'ollama_url': ollama_url, 'models': []}
    for model in ('qwen3-vl:2b', 'moondream:latest', 'cv_fallback'):
        print(f'Benchmarking {model} on {len(cases)} real-frame cases...', flush=True)
        res = evaluate_model(model, cases, ollama_url)
        report['models'].append(res)
        print(json.dumps({k: v for k, v in res.items() if k != 'details'}, indent=2), flush=True)
    out = Path(r'C:\Users\Eclipse\.gemini\antigravity\brain\45244f68-2887-464a-b332-e9f07670ac5a\scratch\vlm_benchmark_results.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Saved full VLM benchmark report to {out}', flush=True)


if __name__ == '__main__':
    main()
