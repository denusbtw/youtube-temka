"""Unit tests for Phase 4 Multimodal Vision Adapter (offline & mocked)."""
import json
from unittest.mock import MagicMock

from factory.vision_vlm import LocalVisionAdapter, _parse_json_lenient


def test_vlm_disabled_returns_honest_cv_fallback():
    adapter = LocalVisionAdapter(model='qwen3-vl:2b', enabled=False)
    assert adapter.is_available() is False
    desc = adapter.describe_frames([b'\xff\xd8fake'])
    assert desc.verified is False
    assert desc.backend == 'cv_fallback'
    br = adapter.detect_format_break([b'\xff\xd8fake'])
    assert br.verified is False
    assert br.is_format_break is False
    cont = adapter.compare_adjacent_shots([b'\xff\xd8a'], [b'\xff\xd8b'])
    assert cont.verified is False
    assert cont.relation == 'uncertain'


def test_vlm_mocked_ollama_and_cache_hit(tmp_path, monkeypatch):
    cache_file = tmp_path / 'vlm-cache.json'
    adapter = LocalVisionAdapter(model='qwen3-vl:2b', ollama_url='http://127.0.0.1:11434', cache_path=cache_file, max_calls=4, enabled=True)
    adapter._available = True

    calls = {'n': 0}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def post(self, url, json):
            calls['n'] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = lambda: None
            resp.json.return_value = {
                'message': {
                    'content': '{"is_format_break": true, "break_kind": "end_card", "reason": "Branded PUMBA outro card", "confidence": 0.92}'
                }
            }
            return resp

    monkeypatch.setattr('factory.vision_vlm.httpx.Client', FakeClient)
    monkeypatch.setattr('factory.vision_vlm.vlm_gpu_layers', lambda m, u=None: 0)

    v1 = adapter.detect_format_break([b'\xff\xd8img1'], {'shot': 100})
    assert v1.verified is True
    assert v1.is_format_break is True
    assert v1.break_kind == 'end_card'
    assert calls['n'] == 1
    assert cache_file.is_file()

    # Second identical call hits persistent cache (0 extra network calls)
    adapter2 = LocalVisionAdapter(model='qwen3-vl:2b', ollama_url='http://127.0.0.1:11434', cache_path=cache_file, max_calls=4, enabled=True)
    adapter2._available = True
    v2 = adapter2.detect_format_break([b'\xff\xd8img1'], {'shot': 100})
    assert v2.is_format_break is True
    assert adapter2.cache_hits == 1
    assert calls['n'] == 1


def test_parse_json_lenient():
    assert _parse_json_lenient('{"a": 1}') == {'a': 1}
    assert _parse_json_lenient('Sure:\n```json\n{"same_event": false}\n```') == {'same_event': False}
    assert _parse_json_lenient('not json') is None
