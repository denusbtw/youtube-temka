"""Practical Multimodal Vision-Language Adapter (Factory 8.0 Phase 4).

Provides a narrow, schema-validated multimodal interface backed by local Ollama
VLMs (e.g. qwen3-vl:2b, moondream) under HEAVY_LOCK + bounded VRAM admission,
content-addressed caching, and an honest no-provider CV fallback.
Never uploads media to external providers unless explicitly opted in.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from jsonfile import write_json_atomic
from . import config
from .heavy import HEAVY_LOCK

PROMPT_VERSION = 'vlm-8.0-r3'
DEFAULT_VLM_MODEL = os.environ.get('FACTORY_VLM_MODEL', 'qwen3-vl:2b-instruct')


class FrameDescription(BaseModel):
    summary: str = Field(max_length=400)
    scene_type: Literal[
        'gameplay',
        'dialogue_talking_head',
        'title_card',
        'end_card',
        'interstitial',
        'death_fail_screen',
        'menu_or_loading',
        'advertisement',
        'black_screen',
        'other',
    ] = 'other'
    visible_subjects: list[str] = Field(default_factory=list, max_length=8)
    visible_text: str = Field(default='', max_length=200)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    backend: str = 'cv_fallback'
    verified: bool = False


class ShotClassification(BaseModel):
    kind: Literal[
        'gameplay',
        'dialogue_talking_head',
        'title_card',
        'end_card',
        'interstitial',
        'death_fail_screen',
        'menu_or_loading',
        'advertisement',
        'black_screen',
        'other',
    ] = 'other'
    is_format_break: bool = False
    summary: str = Field(default='', max_length=300)
    visible_text: str = Field(default='', max_length=200)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    backend: str = 'cv_fallback'
    verified: bool = False


class ContinuityVerdict(BaseModel):
    same_event: bool = False
    relation: Literal[
        'same_action_continuation',
        'same_scene_camera_cut',
        'reaction_cut',
        'new_unrelated_event',
        'format_break',
        'uncertain',
    ] = 'uncertain'
    reason: str = Field(default='', max_length=300)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    backend: str = 'cv_fallback'
    verified: bool = False


class FormatBreakVerdict(BaseModel):
    is_format_break: bool = False
    break_kind: Literal[
        'none',
        'end_card',
        'title_card',
        'interstitial',
        'death_fail_screen',
        'menu_or_loading',
        'advertisement',
        'black_screen',
    ] = 'none'
    reason: str = Field(default='', max_length=300)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    backend: str = 'cv_fallback'
    verified: bool = False


class EventVerification(BaseModel):
    coherent_single_event: bool = False
    contains_format_break: bool = False
    contains_unrelated_scene: bool = False
    event_summary: str = Field(default='', max_length=400)
    setup_observed: str = Field(default='', max_length=200)
    development_observed: str = Field(default='', max_length=200)
    climax_observed: str = Field(default='', max_length=200)
    payoff_observed: str = Field(default='', max_length=200)
    defects: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    backend: str = 'cv_fallback'
    verified: bool = False


class CandidateVerification(BaseModel):
    decision: Literal[
        'ACCEPT',
        'ACCEPT_WITH_REVIEW',
        'REVISE_BOUNDARIES',
        'SPLIT_EVENTS',
        'REJECT',
        'INSUFFICIENT_EVIDENCE',
    ] = 'INSUFFICIENT_EVIDENCE'
    coherent_single_event: bool = False
    contains_format_break: bool = False
    contains_unrelated_scene: bool = False
    event_summary: str = Field(default='', max_length=400)
    grounded_observations: list[str] = Field(default_factory=list, max_length=8)
    defects: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    backend: str = 'cv_fallback'
    verified: bool = False


def query_vram_mib() -> tuple[int | None, int | None]:
    """Return (used_mib, free_mib) from nvidia-smi, or (None, None)."""
    try:
        out = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=memory.used,memory.free', '--format=csv,noheader,nounits'],
            text=True,
            timeout=3,
        ).splitlines()[0]
        used, free = [int(x.strip()) for x in out.split(',')]
        return used, free
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return None, None


def vlm_gpu_layers(model: str, ollama_url: str | None = None) -> int | None:
    """Conservative VRAM admission for 4 GB GPUs.

    Checks if `model` is already loaded in Ollama VRAM via /api/ps so subsequent
    calls inside a session never misread the model's own VRAM footprint as OOM
    pressure and accidentally evict it to CPU.
    """
    setting = os.environ.get('FACTORY_VLM_GPU_LAYERS', 'auto').strip().lower()
    if setting != 'auto':
        try:
            return max(0, min(99, int(setting)))
        except ValueError:
            return 0
    if ollama_url:
        try:
            ps = httpx.get(f'{ollama_url.rstrip("/")}/api/ps', timeout=2.0, trust_env=False)
            if ps.status_code == 200:
                for m in ps.json().get('models', []):
                    name = str(m.get('name', ''))
                    if (name == model or name == f'{model}:latest' or name.split(':')[0] == model) and int(m.get('size_vram', 0)) > 0:
                        return None  # Already resident in GPU VRAM; reuse without evicting
        except Exception:
            pass
    _, free = query_vram_mib()
    if free is None or free < 2200:
        return 0
    if free >= 2800:
        return None  # Let Ollama's VRAM estimator place optimal layers without over-requesting
    return 14


def compose_frame_grid(frames: list[bytes], labels: list[str] | None = None, tile_w: int = 256, tile_h: int = 144) -> bytes:
    """Compose 1-4 JPEG frames into ONE labeled composite JPEG for 100%-GPU 4 GB VRAM inference.

    Measured on RTX 3050 4 GiB: at num_ctx=2048, a single <=512px composite JPEG keeps
    qwen3-vl:2b 100% on GPU (~2.8 GiB VRAM, ~3s/call), whereas multi-image num_ctx>=3072
    forces CPU/GPU split (>60s/call).
    """
    if len(frames) <= 1:
        return frames[0] if frames else b''
    try:
        import cv2
        import numpy as np
    except ImportError:
        return frames[0]

    tiles = []
    for idx, raw in enumerate(frames[:4]):
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            continue
        resized = cv2.resize(img, (tile_w, tile_h), interpolation=cv2.INTER_AREA)
        lbl = labels[idx] if labels and idx < len(labels) else f'#{idx + 1}'
        cv2.rectangle(resized, (0, 0), (min(tile_w, 12 + len(lbl) * 9), 22), (0, 0, 0), -1)
        cv2.putText(resized, lbl, (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 1, cv2.LINE_AA)
        tiles.append(resized)
    if not tiles:
        return frames[0]
    if len(tiles) == 1:
        grid = tiles[0]
    elif len(tiles) == 2:
        grid = np.hstack(tiles)  # 512x144 side-by-side [LEFT | RIGHT]
    else:
        while len(tiles) < 4:
            tiles.append(np.zeros((tile_h, tile_w, 3), dtype=np.uint8))
        grid = np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:4])])  # 512x288 2x2 grid
    ok, buf = cv2.imencode('.jpg', grid, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes() if ok else frames[0]


def _cache_key(model: str, operation: str, frames: list[bytes], context: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(f'{PROMPT_VERSION}:{model}:{operation}'.encode())
    h.update(json.dumps(context, sort_keys=True, ensure_ascii=False).encode())
    for img in frames:
        h.update(hashlib.sha256(img).digest())
    return h.hexdigest()


class LocalVisionAdapter:
    """Bounded local Ollama VLM adapter with caching, VRAM guard and CV fallback."""

    def __init__(
        self,
        model: str | None = None,
        ollama_url: str | None = None,
        cache_path: Path | None = None,
        max_calls: int = 16,
        timeout: float = 90.0,
        enabled: bool | None = None,
    ):
        self.model = model or os.environ.get('FACTORY_VLM_MODEL', DEFAULT_VLM_MODEL)
        self.ollama_url = (ollama_url or config.OLLAMA).rstrip('/')
        self.cache_path = cache_path
        self.max_calls = max(0, int(max_calls))
        self.timeout = float(timeout)
        self.enabled = (os.environ.get('FACTORY_VLM_ENABLED', '1') != '0') if enabled is None else bool(enabled)
        self.calls_made = 0
        self.cache_hits = 0
        self.failures = 0
        self.peak_vram_mib = 0
        self.total_latency_seconds = 0.0
        self._available: bool | None = None
        self._cache: dict[str, dict] = {}
        if self.cache_path and self.cache_path.is_file():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding='utf-8'))
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.cache_path, self._cache, ensure_ascii=True)

    def is_available(self) -> bool:
        if not self.enabled or self.max_calls <= 0:
            return False
        if self._available is not None:
            return self._available
        try:
            resp = httpx.get(f'{self.ollama_url}/api/tags', timeout=4.0, trust_env=False)
            resp.raise_for_status()
            names = [m.get('name', '') for m in resp.json().get('models', [])]
            target = self.model
            self._available = any(
                n == target or n == f'{target}:latest' or n.split(':')[0] == target
                for n in names
            )
        except Exception:
            self._available = False
        return self._available

    def _call_ollama_vision(
        self,
        operation: str,
        prompt: str,
        frames: list[bytes],
        schema: dict | None,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not frames:
            return None
        key = _cache_key(self.model, operation, frames, context)
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        if not self.is_available() or self.calls_made >= self.max_calls:
            return None

        images_b64 = [base64.b64encode(img).decode('ascii') for img in frames]
        use_schema = schema is not None and not self.model.startswith('moondream')
        layers = vlm_gpu_layers(self.model, self.ollama_url)

        is_thinking_qwen = 'qwen3' in self.model.lower() and 'instruct' not in self.model.lower()
        options: dict[str, Any] = {
            'num_ctx': 2048,
            'temperature': 0,
            'num_predict': 640 if context.get('connected_story') else 450,
            'num_thread': 2,
        }
        if layers is not None:
            options['num_gpu'] = layers

        payload: dict[str, Any] = {
            'model': self.model,
            'messages': [{'role': 'user', 'content': prompt, 'images': images_b64}],
            'stream': False,
            'keep_alive': 120,
            'options': options,
        }
        if is_thinking_qwen:
            payload['think'] = False
        if use_schema:
            payload['format'] = schema

        t0 = time.monotonic()
        with HEAVY_LOCK:
            try:
                timeout = max(self.timeout, 150) if context.get('connected_story') else self.timeout
                with httpx.Client(timeout=timeout, trust_env=False) as client:
                    self.calls_made += 1
                    resp = client.post(f'{self.ollama_url}/api/chat', json=payload)
                    if resp.status_code >= 500 and layers != 0:
                        # Retry once on CPU if GPU VRAM OOM or offload failed
                        payload['options']['num_gpu'] = 0
                        resp = client.post(f'{self.ollama_url}/api/chat', json=payload)
                    resp.raise_for_status()
                    used, _ = query_vram_mib()
                    if used is not None:
                        self.peak_vram_mib = max(self.peak_vram_mib, used)
                    raw_text = resp.json().get('message', {}).get('content', '').strip()
            except Exception:
                self.failures += 1
                self.total_latency_seconds += time.monotonic() - t0
                return None
        self.total_latency_seconds += time.monotonic() - t0

        parsed = _parse_json_lenient(raw_text)
        if parsed is None:
            parsed = {'_raw_text': raw_text}
        self._cache[key] = parsed
        self._save_cache()
        return parsed

    def unload(self) -> None:
        """Explicitly release VLM from GPU VRAM after a batch completes."""
        if self.calls_made <= 0 or not self._available:
            return
        try:
            with httpx.Client(timeout=10.0, trust_env=False) as client:
                client.post(f'{self.ollama_url}/api/chat', json={'model': self.model, 'keep_alive': 0, 'messages': []})
        except Exception:
            pass

    def describe_frames(self, frames: list[bytes], context: dict[str, Any] | None = None) -> FrameDescription:
        ctx = context or {}
        schema = {
            'type': 'object',
            'required': ['summary', 'scene_type', 'visible_subjects', 'visible_text', 'confidence'],
            'properties': {
                'summary': {'type': 'string', 'maxLength': 350},
                'scene_type': {
                    'type': 'string',
                    'enum': [
                        'gameplay', 'dialogue_talking_head', 'title_card', 'end_card',
                        'interstitial', 'death_fail_screen', 'menu_or_loading',
                        'advertisement', 'black_screen', 'other',
                    ],
                },
                'visible_subjects': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string'}},
                'visible_text': {'type': 'string', 'maxLength': 180},
                'confidence': {'type': 'number'},
            },
        }
        prompt = (
            'Describe ONLY what is literally visible in these ordered video frames. '
            'Do not invent events not shown. Classify scene_type accurately: '
            'use end_card for YouTube outro/channel branding/video-box cards, '
            'title_card or interstitial for full-screen graphic text cards (e.g. channel intro or "A few moments later"), '
            'death_fail_screen for WASTED/FAILED overlays, gameplay for actual video game action, '
            'dialogue_talking_head for podcast/interview speakers. '
            'Return strict JSON with keys: summary, scene_type, visible_subjects, visible_text, confidence.'
        )
        raw = self._call_ollama_vision('describe_frames', prompt, frames[:4], schema, ctx)
        if raw and '_raw_text' not in raw:
            try:
                return FrameDescription(
                    summary=str(raw.get('summary', ''))[:400],
                    scene_type=_norm_enum(raw.get('scene_type'), FrameDescription.model_fields['scene_type'].annotation, 'other'),
                    visible_subjects=[str(x)[:60] for x in (raw.get('visible_subjects') or [])[:8]],
                    visible_text=str(raw.get('visible_text', ''))[:200],
                    confidence=_clamp01(raw.get('confidence', 0.7)),
                    backend=f'ollama:{self.model}',
                    verified=True,
                )
            except Exception:
                pass
        if raw and raw.get('_raw_text'):
            txt = raw['_raw_text']
            return FrameDescription(
                summary=txt[:400],
                scene_type=_infer_scene_type_from_text(txt),
                confidence=0.55,
                backend=f'ollama:{self.model}',
                verified=True,
            )
        return FrameDescription(
            summary='Visual-semantic model unavailable; CV features only.',
            scene_type='other',
            confidence=0.0,
            backend='cv_fallback',
            verified=False,
        )

    def classify_shot(self, frames: list[bytes], context: dict[str, Any] | None = None) -> ShotClassification:
        desc = self.describe_frames(frames[:2], context)
        is_break = desc.scene_type in {
            'title_card', 'end_card', 'interstitial', 'death_fail_screen',
            'menu_or_loading', 'advertisement', 'black_screen',
        }
        return ShotClassification(
            kind=desc.scene_type,
            is_format_break=is_break,
            summary=desc.summary,
            visible_text=desc.visible_text,
            confidence=desc.confidence,
            backend=desc.backend,
            verified=desc.verified,
        )

    def detect_format_break(self, frames: list[bytes], context: dict[str, Any] | None = None) -> FormatBreakVerdict:
        ctx = context or {}
        schema = {
            'type': 'object',
            'required': ['is_format_break', 'break_kind', 'reason', 'confidence'],
            'properties': {
                'is_format_break': {'type': 'boolean'},
                'break_kind': {
                    'type': 'string',
                    'enum': [
                        'none', 'end_card', 'title_card', 'interstitial',
                        'death_fail_screen', 'menu_or_loading', 'advertisement', 'black_screen',
                    ],
                },
                'reason': {'type': 'string', 'maxLength': 260},
                'confidence': {'type': 'number'},
            },
        }
        prompt = (
            'Inspect these video frames and determine whether they show a non-gameplay format break: '
            'end_card (outro graphic, channel logo, subscribe/video boxes), title_card (channel intro graphic), '
            'interstitial (e.g. "A few moments later" transition card), death_fail_screen (WASTED/FAILED screen), '
            'menu_or_loading, advertisement, or black_screen. '
            'Normal gameplay with a small corner HUD/minimap or streamer webcam is NOT a format break (break_kind="none"). '
            'Return JSON with keys: is_format_break, break_kind, reason, confidence.'
        )
        raw = self._call_ollama_vision('detect_format_break', prompt, frames[:2], schema, ctx)
        if raw and '_raw_text' not in raw:
            kind = _norm_enum(
                raw.get('break_kind'),
                FormatBreakVerdict.model_fields['break_kind'].annotation,
                'none',
            )
            is_br = bool(raw.get('is_format_break')) or kind != 'none'
            if not is_br:
                kind = 'none'
            return FormatBreakVerdict(
                is_format_break=is_br,
                break_kind=kind,
                reason=str(raw.get('reason', ''))[:300],
                confidence=_clamp01(raw.get('confidence', 0.7)),
                backend=f'ollama:{self.model}',
                verified=True,
            )
        if raw and raw.get('_raw_text'):
            txt = raw['_raw_text']
            inferred = _infer_scene_type_from_text(txt)
            is_br = inferred in {'end_card', 'title_card', 'interstitial', 'death_fail_screen', 'menu_or_loading', 'advertisement', 'black_screen'}
            return FormatBreakVerdict(
                is_format_break=is_br,
                break_kind=inferred if is_br else 'none',  # type: ignore[arg-type]
                reason=txt[:280],
                confidence=0.55,
                backend=f'ollama:{self.model}',
                verified=True,
            )
        return FormatBreakVerdict(
            is_format_break=False,
            break_kind='none',
            reason='VLM unavailable; defer to CV format-break detector.',
            confidence=0.0,
            backend='cv_fallback',
            verified=False,
        )

    def compare_adjacent_shots(
        self,
        frames_a: list[bytes],
        frames_b: list[bytes],
        context: dict[str, Any] | None = None,
    ) -> ContinuityVerdict:
        ctx = context or {}
        if not frames_a or not frames_b:
            return ContinuityVerdict(backend='cv_fallback', verified=False)
        combined_img = compose_frame_grid([frames_a[-1], frames_b[0]], labels=['LEFT: Shot A', 'RIGHT: Shot B'], tile_w=240, tile_h=136)
        schema = {
            'type': 'object',
            'required': ['same_event', 'relation', 'reason', 'confidence'],
            'properties': {
                'same_event': {'type': 'boolean'},
                'relation': {
                    'type': 'string',
                    'enum': [
                        'same_action_continuation',
                        'same_scene_camera_cut',
                        'reaction_cut',
                        'new_unrelated_event',
                        'format_break',
                        'uncertain',
                    ],
                },
                'reason': {'type': 'string', 'maxLength': 280},
                'confidence': {'type': 'number'},
            },
        }
        prompt = (
            'LEFT and RIGHT are two timestamps from a video. '
            'Decide if they belong to the SAME continuous clip/event (same game scene, same vehicle or player moving along a road/sky/street, or same speaker/studio in a conversation -> same_event=true) '
            'OR if they are TWO UNRELATED clips from a compilation (completely different vehicle/location/time-of-day, e.g., car interior vs ocean boat, scooter vs highway truck -> same_event=false, relation="new_unrelated_event") '
            'OR if either side is a graphic title card, end card, outro logo, or text transition card (same_event=false, relation="format_break"). '
            'Movement of the same vehicle or character along a road/bridge/sky is STILL same_event=true. '
            'A camera change or a gameplay-to-cutscene transition can preserve the same event if a specific actor, action, or conversation continues across the cut. A shared game, art style, or HUD alone is insufficient. '
            'State the specific matching detail or return uncertain. '
            'Return strict JSON: same_event, relation, reason, confidence.'
        )
        raw = self._call_ollama_vision('compare_adjacent_shots', prompt, [combined_img], schema, ctx)
        if raw and '_raw_text' not in raw:
            rel = _norm_enum(raw.get('relation'), ContinuityVerdict.model_fields['relation'].annotation, 'uncertain')
            same = bool(raw.get('same_event'))
            conf = _clamp01(raw.get('confidence', 0.7))
            if rel in ('new_unrelated_event', 'format_break'):
                same = False
            elif rel in ('same_action_continuation', 'same_scene_camera_cut', 'reaction_cut'):
                same = True
            return ContinuityVerdict(
                same_event=same,
                relation=rel,
                reason=str(raw.get('reason', ''))[:300],
                confidence=conf,
                backend=f'ollama:{self.model}',
                verified=True,
            )
        if raw and raw.get('_raw_text'):
            txt = raw['_raw_text']
            low = txt.lower()
            is_break = any(k in low for k in ('title card', 'end card', 'logo', 'outro', 'interstitial', 'moments later'))
            is_diff = any(k in low for k in ('unrelated', 'different scene', 'different clip', 'new event', 'no,', 'false'))
            is_same = any(k in low for k in ('same event', 'same scene', 'continuous', 'yes,', 'true')) and not is_diff and not is_break
            rel: Any = 'format_break' if is_break else ('same_action_continuation' if is_same else ('new_unrelated_event' if is_diff else 'uncertain'))
            return ContinuityVerdict(
                same_event=is_same,
                relation=rel,
                reason=txt[:280],
                confidence=0.55,
                backend=f'ollama:{self.model}',
                verified=True,
            )
        return ContinuityVerdict(
            same_event=False,
            relation='uncertain',
            reason='VLM unavailable; continuity judged by CV graph only.',
            confidence=0.0,
            backend='cv_fallback',
            verified=False,
        )

    def verify_event_continuity(
        self,
        frames_timeline: list[tuple[float, bytes]],
        context: dict[str, Any] | None = None,
    ) -> EventVerification:
        ctx = context or {}
        if not frames_timeline:
            return EventVerification(backend='cv_fallback', verified=False)
        # Bound to at most 4 evenly spaced frames to stay comfortably inside 4 GB VRAM
        step_idx = [
            round(i * (len(frames_timeline) - 1) / max(1, min(4, len(frames_timeline)) - 1))
            for i in range(min(4, len(frames_timeline)))
        ]
        chosen = [frames_timeline[i] for i in sorted(set(step_idx))]
        ts_labels = [f'#{i + 1} {t:.1f}s' for i, (t, _) in enumerate(chosen)]
        ts_str = ', '.join(ts_labels)
        grid_img = compose_frame_grid([img for _, img in chosen], labels=ts_labels, tile_w=240, tile_h=136)

        schema = {
            'type': 'object',
            'required': [
                'coherent_single_event', 'contains_format_break', 'contains_unrelated_scene',
                'event_summary', 'setup_observed', 'climax_observed', 'payoff_observed',
                'defects', 'confidence',
            ],
            'properties': {
                'coherent_single_event': {'type': 'boolean'},
                'contains_format_break': {'type': 'boolean'},
                'contains_unrelated_scene': {'type': 'boolean'},
                'event_summary': {'type': 'string', 'maxLength': 140},
                'setup_observed': {'type': 'string', 'maxLength': 90},
                'climax_observed': {'type': 'string', 'maxLength': 90},
                'payoff_observed': {'type': 'string', 'maxLength': 90},
                'defects': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string', 'maxLength': 60}},
                'confidence': {'type': 'number'},
            },
        }
        transcript_excerpt = str(ctx.get('transcript', ''))[:700 if ctx.get('connected_story') else 400]
        prompt = (
            f'This 2x2 storyboard shows 4 frames in chronological order ({ts_str}) from ONE candidate short clip. '
            f'Transcript excerpt: "{transcript_excerpt}". '
            'Answer strictly from what is visible across the 4 panels: '
            '1) contains_format_break=true ONLY if any panel shows a channel title card, end card, outro logo, video-box promo, "moments later" card, or black screen. Normal gameplay with a corner HUD or webcam is false. '
            '2) contains_unrelated_scene=true ONLY if the panels jump between unrelated compilation clips (e.g. car -> boat -> night street, or completely different vehicle/player/time-of-day). A single player driving/riding/flying forward along a road, bridge, or city over 30-50 seconds is coherent_single_event=true and contains_unrelated_scene=false. '
            '3) Write a concise factual event_summary describing ONLY what is visibly happening (e.g. "White pickup truck driving on a sunset highway and swapping vehicles"). '
            'Return strict JSON.'
        )
        if ctx.get('connected_story'):
            schema['required'].extend([
                'development_observed',
                'panel_1_observed', 'panel_2_observed', 'panel_3_observed', 'panel_4_observed',
                'event_progression_observed',
            ])
            for field in (
                'panel_1_observed', 'panel_2_observed', 'panel_3_observed', 'panel_4_observed',
                'setup_observed', 'development_observed', 'climax_observed', 'payoff_observed',
            ):
                schema['properties'][field] = {'type': 'string', 'maxLength': 80}
            schema['properties']['event_progression_observed'] = {'type': 'boolean'}
            schema['properties']['event_summary']['maxLength'] = 140
            schema['properties']['defects']['maxItems'] = 2
            prompt = (
                f'This 2x2 storyboard shows 4 chronological frames ({ts_str}) from one candidate video story. '
                f'Spoken dialogue in this window: "{transcript_excerpt}". '
                'First describe literally what is visible in each panel (panel_1_observed .. panel_4_observed): note the vehicle/characters, location, time of day, and any corner webcam. '
                'Then evaluate: '
                '1) contains_format_break=true ONLY if any panel shows a full-screen graphic title card (e.g. "PUMBA PRESENTS"), end card/outro logo, "moments later" card, menu, or black screen. Normal gameplay with a corner HUD or webcam is false. '
                '2) contains_unrelated_scene=true ONLY if the panels jump between different unrelated compilation clips (e.g. different players, car interior -> ocean boat -> night street, or a streamer webcam overlay appearing/disappearing mid-clip). Camera cuts or character movement within the same continuous mission, stunt, flight, vehicle swap, or conversation are NOT unrelated scenes. '
                '3) coherent_single_event=true if all 4 panels belong to ONE continuous gameplay event, mission scene, stunt, or conversation (with contains_format_break=false and contains_unrelated_scene=false). '
                '4) event_progression_observed=true if the panels or dialogue show a meaningful progression (e.g. approach/preparation -> stunt/action/decision -> outcome/result). '
                '5) Summarize the concrete progression in setup_observed (opening context/preparation), development_observed (ongoing action/escalation), climax_observed (turning point/critical action), and payoff_observed (visible outcome/reaction/escape), using specific details from the frames and dialogue. '
                '6) confidence: 0.80-0.95 when coherent_single_event and event_progression_observed are clearly supported, or <=0.50 when uncertain or discontinuous. '
                'Return only the JSON object.'
            )
        request_context = {'ts': ts_str, **ctx}
        if ctx.get('connected_story'):
            request_context['story_output_version'] = 3
        raw = self._call_ollama_vision('verify_event_continuity', prompt, [grid_img], schema, request_context)
        if raw and '_raw_text' not in raw:
            defects = [str(d)[:120] for d in (raw.get('defects') or [])[:8]]
            has_break = bool(raw.get('contains_format_break'))
            has_unrelated = bool(raw.get('contains_unrelated_scene'))
            coherent = bool(raw.get('coherent_single_event')) and not has_break and not has_unrelated
            if has_break and not any('card' in d.lower() or 'break' in d.lower() for d in defects):
                defects.append('format_break_visible_in_frames')
            if has_unrelated and not any('unrelated' in d.lower() or 'scene' in d.lower() for d in defects):
                defects.append('unrelated_scene_change_visible')
            p1 = str(raw.get('panel_1_observed', '')).strip()
            p2 = str(raw.get('panel_2_observed', '')).strip()
            p3 = str(raw.get('panel_3_observed', '')).strip()
            p4 = str(raw.get('panel_4_observed', '')).strip()
            setup_obs = str(raw.get('setup_observed') or p1)[:200]
            dev_obs = str(raw.get('development_observed') or p2)[:200]
            climax_obs = str(raw.get('climax_observed') or p3)[:200]
            payoff_obs = str(raw.get('payoff_observed') or p4)[:200]
            ev_sum = str(raw.get('event_summary', ''))[:400]
            combined_text = ' '.join((ev_sum, p1, p2, p3, p4, setup_obs, dev_obs, climax_obs, payoff_obs)).lower()
            if any(tok in combined_text for tok in ('title card', 'end card', 'black screen', 'series of credits', 'sequence of credits', 'credit screen')):
                has_break = True
                coherent = False
                if not any('card' in d.lower() or 'break' in d.lower() for d in defects):
                    defects.append('format_break_visible_in_frames')
            # If the model repeated identical beat strings, fall back to distinct panel observations when available
            if len({setup_obs.lower(), dev_obs.lower(), climax_obs.lower(), payoff_obs.lower()}) < 3 and len({p1.lower(), p2.lower(), p3.lower(), p4.lower()} - {''}) >= 3:
                setup_obs, dev_obs, climax_obs, payoff_obs = p1[:200], p2[:200], p3[:200], p4[:200]
            return EventVerification(
                coherent_single_event=coherent,
                contains_format_break=has_break,
                contains_unrelated_scene=has_unrelated,
                event_summary=str(raw.get('event_summary', ''))[:400],
                setup_observed=setup_obs,
                development_observed=dev_obs,
                climax_observed=climax_obs,
                payoff_observed=payoff_obs,
                defects=defects,
                confidence=_clamp01(raw.get('confidence', 0.7)),
                backend=f'ollama:{self.model}',
                verified=True,
            )
        return EventVerification(
            coherent_single_event=False,
            event_summary='VLM unavailable; visual continuity unverified.',
            confidence=0.0,
            backend='cv_fallback',
            verified=False,
        )

    def verify_candidate_content(
        self,
        frames_timeline: list[tuple[float, bytes]],
        transcript: str,
        context: dict[str, Any] | None = None,
    ) -> CandidateVerification:
        ctx = {**(context or {}), 'transcript': transcript[:400]}
        ev = self.verify_event_continuity(frames_timeline, ctx)
        if not ev.verified:
            return CandidateVerification(
                decision='INSUFFICIENT_EVIDENCE',
                coherent_single_event=False,
                event_summary=ev.event_summary,
                confidence=0.0,
                backend=ev.backend,
                verified=False,
            )
        obs = [x for x in (ev.event_summary, ev.setup_observed, ev.climax_observed, ev.payoff_observed) if x]
        if ev.contains_format_break:
            decision: Any = 'REVISE_BOUNDARIES'
        elif ev.contains_unrelated_scene:
            decision = 'SPLIT_EVENTS'
        elif ev.coherent_single_event and ev.confidence >= 0.65:
            decision = 'ACCEPT_WITH_REVIEW'
        elif ev.coherent_single_event:
            decision = 'ACCEPT_WITH_REVIEW'
        else:
            decision = 'REJECT'
        return CandidateVerification(
            decision=decision,
            coherent_single_event=ev.coherent_single_event,
            contains_format_break=ev.contains_format_break,
            contains_unrelated_scene=ev.contains_unrelated_scene,
            event_summary=ev.event_summary,
            grounded_observations=obs[:8],
            defects=ev.defects,
            confidence=ev.confidence,
            backend=ev.backend,
            verified=True,
        )

    def stats(self) -> dict[str, Any]:
        return {
            'model': self.model,
            'enabled': self.enabled,
            'available': self._available,
            'calls_made': self.calls_made,
            'cache_hits': self.cache_hits,
            'failures': self.failures,
            'peak_vram_mib': self.peak_vram_mib,
            'total_latency_seconds': round(self.total_latency_seconds, 3),
        }


def _parse_json_lenient(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else None
    except ValueError:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            val = json.loads(match.group(0))
            return val if isinstance(val, dict) else None
        except ValueError:
            return None
    return None


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _norm_enum(value: Any, annotation: Any, default: str) -> Any:
    allowed = getattr(annotation, '__args__', ())
    cand = str(value or '').strip().lower()
    return cand if cand in allowed else default


def _infer_scene_type_from_text(text: str) -> Any:
    low = text.lower()
    if any(k in low for k in ('end card', 'outro', 'subscribe', 'pumba')) and ('sunset' in low or 'palm' in low or 'end' in low or 'subscribe' in low or 'presents' not in low):
        if 'presents' in low or 'intro' in low:
            return 'title_card'
        return 'end_card'
    if any(k in low for k in ('title card', 'presents', 'intro screen')):
        return 'title_card'
    if any(k in low for k in ('moments later', 'interstitial', 'transition card')):
        return 'interstitial'
    if any(k in low for k in ('wasted', 'mission failed', 'failed screen', 'game over')):
        return 'death_fail_screen'
    if any(k in low for k in ('black screen', 'completely dark', 'blank black')):
        return 'black_screen'
    if any(k in low for k in ('menu', 'loading screen', 'settings')):
        return 'menu_or_loading'
    if any(k in low for k in ('podcast', 'interview', 'speaking', 'microphone')):
        return 'dialogue_talking_head'
    if any(k in low for k in ('game', 'car', 'truck', 'boat', 'airplane', 'plane', 'street', 'player', 'driving')):
        return 'gameplay'
    return 'other'
