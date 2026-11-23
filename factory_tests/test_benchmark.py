"""Tests for Factory 8.0 Phase 1 benchmark schema and evaluation metrics."""
import json
from pathlib import Path

import pytest

from factory.benchmark import BenchmarkSource, evaluate_source, load_benchmark_source


ROOT = Path(__file__).resolve().parents[1]
GTA_FIXTURE = ROOT / 'verification' / 'benchmarks' / 'gta_yux8s3lwLss_v2.json'
GTA_BASELINE_REPORT = ROOT / 'verification' / 'runtime' / 'event-acceptance' / 'report.json'


def test_gta_benchmark_fixture_loads_and_validates():
    source = load_benchmark_source(GTA_FIXTURE)
    assert source.schema_version == 2
    assert source.source_id == 'gta-yux8s3lwLss-full-618s'
    assert source.annotation_status == 'provisional_engineering_inspection'
    assert len(source.format_breaks) >= 5
    assert len(source.rejected_intervals) == 3


def test_baseline_gta_report_fails_editorial_contamination_checks():
    source = load_benchmark_source(GTA_FIXTURE)
    if GTA_BASELINE_REPORT.is_file():
        report = json.loads(GTA_BASELINE_REPORT.read_text(encoding='utf-8'))
        candidates = report['independent']['candidates']
    else:
        candidates = [
            {'start_seconds': 554.12, 'end_seconds': 600.26},
            {'start_seconds': 172.98, 'end_seconds': 216.98},
            {'start_seconds': 5.42, 'end_seconds': 52.50},
        ]
    result = evaluate_source(source, candidates, k=3)
    assert result['selected_count'] == 3
    assert result['semantic_correctness']['end_card_title_card_contamination_rate'] == pytest.approx(2 / 3, abs=1e-3)
    assert result['semantic_correctness']['unrelated_scene_contamination_rate'] >= 0.33
    assert result['semantic_correctness']['rejected_baseline_overlap_count'] == 3
    assert result['editorial']['editorial_verification_status'] == 'PENDING_HUMAN_REVIEW'
    assert result['editorial']['human_editorial_precision_at_k'] is None


def test_clean_selections_pass_contamination_and_match_reference_events():
    source = load_benchmark_source(GTA_FIXTURE)
    clean_candidates = [
        {'start_seconds': 550.0, 'end_seconds': 597.5, 'setup': 'truck drive', 'payoff': 'bison getaway'},
        {'start_seconds': 444.0, 'end_seconds': 480.0, 'setup': 'scooter start', 'payoff': 'street dodge'},
        {'start_seconds': 412.5, 'end_seconds': 443.5, 'setup': 'bridge entry', 'payoff': 'splashdown'},
    ]
    result = evaluate_source(source, clean_candidates, k=3)
    assert result['semantic_correctness']['end_card_title_card_contamination_rate'] == 0.0
    assert result['semantic_correctness']['unrelated_scene_contamination_rate'] == 0.0
    assert result['semantic_correctness']['rejected_baseline_overlap_count'] == 0
    assert result['discovery']['false_positive_rate'] == 0.0
    assert result['discovery']['candidate_duplication_rate'] == 0.0
    assert result['editorial']['setup_payoff_coverage'] == 1.0


def test_invalid_bounds_rejected_by_schema():
    with pytest.raises(ValueError):
        BenchmarkSource(
            source_id='bad',
            rights_status='synthetic_fixture',
            source_type='compilation',
            content_category='gameplay',
            duration=60.0,
            format_breaks=[{'start': 55.0, 'end': 75.0, 'kind': 'end_card'}],
        )
