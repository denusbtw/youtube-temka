"""Unified Source Intelligence Engine (Factory 8.0 Phase 2).

Produces one normalized, source-relative, versioned SourceEvidence timeline per
source interval using sequential PyAV decoding (no repeated GOP seeks),
multi-feature shot boundary detection, audio energy/burst/ZCR analysis,
transcript alignment, optional OCR / deterministic text-band cues, and
content-category signal profiles.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from jsonfile import write_json_atomic
from . import media

SCHEMA_VERSION = 1
CACHE_REVISION = 2

REACTION_PATTERNS = re.compile(
    r'\b(oh my god|omg|what the|no way|bro|wait|holy|insane|wasted|mission failed|'
    r'disappeared|wtf|laugh|haha|lol|gg|clutch|let\'?s go|stop|watch out|look)\b',
    re.IGNORECASE,
)


class AudioEvent(BaseModel):
    time: float = Field(ge=0)
    duration: float = Field(gt=0)
    rms: float = Field(ge=0)
    db: float
    delta_rms: float = Field(ge=0)
    zcr: float = Field(ge=0)
    is_silence: bool
    is_burst: bool
    origin: str = 'pyav_sequential_audio'


class ScreenTextObservation(BaseModel):
    time: float = Field(ge=0)
    text: str = ''
    text_band_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    backend: str = 'opencv_text_band_fallback'
    verification_status: Literal['ocr_detected', 'heuristic_text_band'] = 'heuristic_text_band'


class Shot(BaseModel):
    shot_id: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    duration: float = Field(gt=0)
    boundary_type: Literal['initial', 'hard_cut', 'fade_black', 'graphic_transition', 'gradual'] = 'hard_cut'
    boundary_score: float = Field(ge=0)
    detector: str = 'pyav_multifeature_v1'
    sample_count: int = Field(ge=1)
    # Visual appearance descriptors (compact, serializable)
    hsv_hist: list[float] = Field(default_factory=list)
    spatial_grid: list[float] = Field(default_factory=list)
    corner_hud: list[float] = Field(default_factory=list)
    mean_luma: float = 0.0
    std_luma: float = 0.0
    mean_sat: float = 0.0
    high_sat_frac: float = 0.0
    edge_density: float = 0.0
    center_text_band_score: float = 0.0
    red_tint_score: float = 0.0
    mean_motion: float = 0.0
    peak_motion: float = 0.0
    static_fraction: float = 0.0
    dark_fraction: float = 0.0
    face_corners: list[str] = Field(default_factory=list)
    # Audio & speech aggregates
    mean_audio_rms: float = 0.0
    peak_audio_rms: float = 0.0
    audio_silence_fraction: float = 0.0
    audio_burst_count: int = 0
    mean_zcr: float = 0.0
    speech_text: str = ''
    speech_word_count: int = 0
    speech_segment_count: int = 0
    has_reaction_cue: bool = False
    ocr_text: str = ''
    uncertainty: float = Field(default=0.35, ge=0, le=1)


class SourceContext(BaseModel):
    source_id: str
    source_fingerprint: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    duration: float = Field(gt=0)
    width: int = 0
    height: int = 0
    fps: float = 30.0
    has_audio: bool = True
    content_category: str = 'auto'
    confirmed_subject: str = ''
    source_title: str = ''
    detected_facecam_corners: list[str] = Field(default_factory=list)
    ocr_backend: str = 'opencv_text_band_fallback'


class SourceEvidence(BaseModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    context: SourceContext
    shots: list[Shot] = Field(default_factory=list)
    audio_events: list[AudioEvent] = Field(default_factory=list)
    text_observations: list[ScreenTextObservation] = Field(default_factory=list)
    coarse_samples: list[dict[str, Any]] = Field(default_factory=list)
    sample_step_seconds: float = 0.5
    processing_seconds: float = 0.0


def file_fingerprint(path: Path) -> str:
    """Fast deterministic fingerprint combining size + head/tail/mid SHA-256."""
    size = path.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    with path.open('rb') as f:
        h.update(f.read(65536))
        if size > 131072:
            f.seek(size // 2)
            h.update(f.read(65536))
            f.seek(max(0, size - 65536))
            h.update(f.read(65536))
    return h.hexdigest()[:32]


def _extract_frame_features(rgb: np.ndarray) -> dict[str, Any]:
    """Compute lightweight, discriminative visual appearance & card/HUD features from RGB 160x90."""
    import cv2

    h, w = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    # 1. Global HSV histogram (16 H + 8 S + 8 V = 32 floats)
    h_hist = np.bincount((hsv[:, :, 0].ravel() * 16) // 180, minlength=16)[:16].astype(np.float32)
    s_hist = np.bincount(hsv[:, :, 1].ravel() // 32, minlength=8)[:8].astype(np.float32)
    v_hist = np.bincount(hsv[:, :, 2].ravel() // 32, minlength=8)[:8].astype(np.float32)
    n_px = float(max(1, h * w))
    hsv_vec = np.concatenate([h_hist / n_px, s_hist / n_px, v_hist / n_px])

    # 2. 3x3 spatial color grid (27 floats in [0, 1])
    grid = []
    rh, cw = max(1, h // 3), max(1, w // 3)
    for r in range(3):
        for c in range(3):
            patch = rgb[r * rh:(r + 1) * rh if r < 2 else h, c * cw:(c + 1) * cw if c < 2 else w]
            means = patch.mean(axis=(0, 1)) / 255.0
            grid.extend(means.tolist())

    # 3. 4 Corner HUD / Facecam signatures (BL, TL, TR, BR -> 24 floats)
    ch, cw_c = max(8, int(h * 0.28)), max(12, int(w * 0.26))
    corners_coords = [
        (h - ch, h, 0, cw_c),       # bottom-left (minimap / webcam)
        (0, ch, 0, cw_c),           # top-left (webcam / status)
        (0, ch, w - cw_c, w),       # top-right (wanted stars / score)
        (h - ch, h, w - cw_c, w),   # bottom-right (webcam / watermark)
    ]
    gx = np.abs(np.diff(gray.astype(np.int16), axis=1))
    gy = np.abs(np.diff(gray.astype(np.int16), axis=0))
    corner_sig = []
    for y0, y1, x0, x1 in corners_coords:
        c_rgb = rgb[y0:y1, x0:x1]
        c_hsv = hsv[y0:y1, x0:x1]
        c_gx = gx[y0:y1, x0:min(x1, gx.shape[1])]
        c_gy = gy[y0:min(y1, gy.shape[0]), x0:x1]
        edge_c = float((c_gx.mean() + c_gy.mean()) / 510.0) if c_gx.size and c_gy.size else 0.0
        corner_sig.extend([
            float(c_rgb[:, :, 0].mean() / 255.0),
            float(c_rgb[:, :, 1].mean() / 255.0),
            float(c_rgb[:, :, 2].mean() / 255.0),
            float(c_hsv[:, :, 1].mean() / 255.0),
            float(c_hsv[:, :, 2].mean() / 255.0),
            min(1.0, edge_c * 4.0),
        ])

    # 4. Global & graphic-card / title-card / death-screen cues
    mean_luma = float(gray.mean())
    std_luma = float(gray.std())
    mean_sat = float(hsv[:, :, 1].mean() / 255.0)
    high_sat_frac = float((hsv[:, :, 1] >= 150).mean())
    edge_density = float(min(1.0, ((gx.mean() + gy.mean()) / 510.0) * 4.0))

    # Centered text-band cue: high horizontal contrast in middle vertical band
    # combined with strong local binary contrast (typical of title/end/WASTED cards)
    cy0, cy1 = int(h * 0.18), int(h * 0.82)
    cx0, cx1 = int(w * 0.15), int(w * 0.85)
    center_gray = gray[cy0:cy1, cx0:cx1]
    c_gx_hi = (np.abs(np.diff(center_gray.astype(np.int16), axis=1)) > 55).mean()
    c_white_or_bright = (center_gray > 215).mean()
    # Graphic cards often have bright bold lettering over saturated or dark backgrounds
    center_text_band = float(min(1.0, (c_gx_hi * 4.5) * (0.4 + 2.5 * min(0.25, c_white_or_bright))))

    # Red-tint score (WASTED / FAILED / red interstitial cards)
    r_ch = rgb[:, :, 0].astype(np.float32)
    g_ch = rgb[:, :, 1].astype(np.float32)
    b_ch = rgb[:, :, 2].astype(np.float32)
    red_dominance = np.clip((r_ch - np.maximum(g_ch, b_ch)) / 255.0, 0.0, 1.0)
    red_tint_score = float(red_dominance.mean())

    return {
        'hsv_hist': hsv_vec,
        'spatial_grid': np.asarray(grid, dtype=np.float32),
        'corner_hud': np.asarray(corner_sig, dtype=np.float32),
        'mean_luma': mean_luma,
        'std_luma': std_luma,
        'mean_sat': mean_sat,
        'high_sat_frac': high_sat_frac,
        'edge_density': edge_density,
        'center_text_band_score': center_text_band,
        'red_tint_score': red_tint_score,
        'gray_small': gray,
    }


def _sequential_audio_events(path: Path, start: float, end: float, window_sec: float = 0.5) -> list[AudioEvent]:
    """Extract normalized source-relative audio features in ONE sequential PyAV pass."""
    import av

    duration = max(0.0, end - start)
    n_bins = max(1, math.ceil(duration / window_sec))
    sq_sums = np.zeros(n_bins, dtype=np.float64)
    counts = np.zeros(n_bins, dtype=np.int64)
    zcr_sums = np.zeros(n_bins, dtype=np.float64)
    zcr_counts = np.zeros(n_bins, dtype=np.int64)

    with av.open(str(path)) as container:
        if not container.streams.audio:
            return []
        stream = container.streams.audio[0]
        stream.thread_count = 2
        origin = float((stream.start_time or 0) * stream.time_base)
        if start > 2.0:
            try:
                container.seek(int((origin + max(0.0, start - 1.0)) / stream.time_base), stream=stream)
            except Exception:
                pass
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            pos = frame.time - origin
            if pos >= end:
                break
            if pos + (frame.samples / max(1, frame.sample_rate)) < start:
                continue
            idx = int((pos - start) / window_sec)
            if not (0 <= idx < n_bins):
                continue
            arr = frame.to_ndarray()
            vals = arr.astype(np.float32)
            if arr.dtype.kind in ('i', 'u'):
                info = np.iinfo(arr.dtype)
                if arr.dtype.kind == 'u':
                    vals -= 128.0
                    vals /= 128.0
                else:
                    vals /= float(max(abs(info.min), info.max))
            if vals.ndim > 1:
                mono = vals.mean(axis=0) if vals.shape[0] <= 8 else vals.mean(axis=1)
            else:
                mono = vals
            sq_sums[idx] += float(np.dot(mono, mono))
            counts[idx] += mono.size
            if mono.size > 1:
                crossings = np.count_nonzero((mono[:-1] >= 0) != (mono[1:] >= 0))
                zcr_sums[idx] += float(crossings)
                zcr_counts[idx] += mono.size - 1

    rms_vals = [math.sqrt(sq_sums[i] / counts[i]) if counts[i] > 0 else 0.0 for i in range(n_bins)]
    nonzero = [r for r in rms_vals if r > 1e-4]
    median_rms = float(np.median(nonzero)) if nonzero else 0.02
    burst_threshold = max(0.08, median_rms * 1.85)

    events: list[AudioEvent] = []
    prev_rms = None
    for i, rms in enumerate(rms_vals):
        t = round(i * window_sec, 3)
        dur = round(min(window_sec, max(0.05, duration - t)), 3)
        delta = abs(rms - prev_rms) if prev_rms is not None else 0.0
        db = round(20.0 * math.log10(max(rms, 1e-6)), 2)
        zcr = round(zcr_sums[i] / zcr_counts[i], 4) if zcr_counts[i] > 0 else 0.0
        is_silence = rms < 0.004
        is_burst = (rms >= burst_threshold and delta >= 0.03) or delta >= max(0.06, median_rms * 1.2)
        events.append(AudioEvent(
            time=t,
            duration=dur,
            rms=round(rms, 5),
            db=db,
            delta_rms=round(delta, 5),
            zcr=zcr,
            is_silence=is_silence,
            is_burst=is_burst,
        ))
        prev_rms = rms
    return events


def _try_ocr_on_frame(rgb: np.ndarray) -> tuple[str, float, str]:
    """Optional CPU RapidOCR adapter with safe fallback when not installed."""
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except ImportError:
        return '', 0.0, 'opencv_text_band_fallback'
    try:
        ocr = _get_rapidocr_instance(RapidOCR)
        result, _ = ocr(rgb)
        if not result:
            return '', 0.0, 'rapidocr_onnxruntime'
        texts = []
        confs = []
        for item in result:
            if len(item) >= 3 and item[1]:
                texts.append(str(item[1]).strip())
                confs.append(float(item[2]))
        joined = ' '.join(texts)[:200]
        mean_conf = sum(confs) / len(confs) if confs else 0.0
        return joined, round(mean_conf, 3), 'rapidocr_onnxruntime'
    except Exception:
        return '', 0.0, 'opencv_text_band_fallback'


_RAPIDOCR_SINGLETON = None


def _get_rapidocr_instance(cls):
    global _RAPIDOCR_SINGLETON
    if _RAPIDOCR_SINGLETON is None:
        _RAPIDOCR_SINGLETON = cls()
    return _RAPIDOCR_SINGLETON


def _detect_corner_faces(rgb: np.ndarray, detectors) -> list[str]:
    """Detect whether a facecam appears in one of the 4 frame corners using frontal + profile cascades."""
    import cv2

    if not detectors:
        return []
    if not isinstance(detectors, (list, tuple)):
        detectors = [detectors]
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    boxes = {
        'bottom_left': (int(h * 0.38), h, 0, int(w * 0.35)),
        'top_left': (0, int(h * 0.38), 0, int(w * 0.35)),
        'top_right': (0, int(h * 0.38), int(w * 0.65), w),
        'bottom_right': (int(h * 0.45), h, int(w * 0.58), w),
    }
    found = []
    for name, (y0, y1, x0, x1) in boxes.items():
        patch = gray[y0:y1, x0:x1]
        if patch.shape[0] < 24 or patch.shape[1] < 24:
            continue
        hit = False
        for det, check_flip in detectors:
            if det is None or det.empty():
                continue
            faces = det.detectMultiScale(patch, 1.08, 3, minSize=(14, 14))
            if len(faces) > 0:
                hit = True
                break
            if check_flip:
                faces_flip = det.detectMultiScale(cv2.flip(patch, 1), 1.08, 3, minSize=(14, 14))
                if len(faces_flip) > 0:
                    hit = True
                    break
        if hit:
            found.append(name)
    return found


def _optional_transnetv2_cuts(path: Path, start: float, end: float) -> list[float] | None:
    """Use existing OpenShorts scene_detection.py TransNetV2 if explicitly enabled & installed."""
    if os.environ.get('FACTORY_USE_TRANSNETV2', '0') != '1':
        return None
    if start > 0:
        return None
    try:
        import scene_detection
        scenes, fps = scene_detection.detect_scenes(str(path))
        cuts = []
        for s, e in scenes[1:]:
            t = s.get_seconds()
            if 0 < t < end:
                cuts.append(round(float(t), 3))
        return cuts
    except Exception:
        return None


def analyze_source(
    source: str | Path,
    start: float,
    end: float,
    transcript: dict | None,
    cache_dir: Path | None = None,
    content_category: str = 'auto',
    confirmed_subject: str = '',
    source_title: str = '',
    tick=lambda *_: None,
    sample_step: float | None = None,
) -> SourceEvidence:
    """Build or load cached unified SourceEvidence for [start, end] in source-relative coordinates."""
    import time
    import av
    import cv2

    t0 = time.monotonic()
    local_path = Path(source) if isinstance(source, Path) or (isinstance(source, str) and Path(source).is_file()) else media.local_source(str(source))
    duration = max(0.01, float(end - start))
    if local_path is None or not local_path.is_file():
        # Remote-only fallback: build minimal transcript/empty evidence without failing
        return _remote_fallback_evidence(str(source), start, end, transcript or {}, content_category, confirmed_subject, source_title)

    fp = file_fingerprint(local_path)
    cache_path = cache_dir / 'source-evidence-v1.json' if cache_dir else None
    if cache_path and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
            ctx = cached.get('context', {})
            if (
                cached.get('schema_version') == SCHEMA_VERSION
                and ctx.get('source_fingerprint') == fp
                and ctx.get('ocr_backend', '').endswith(':v2')
                and abs(float(ctx.get('start', -1)) - start) < 1e-3
                and abs(float(ctx.get('end', -1)) - end) < 1e-3
            ):
                return SourceEvidence.model_validate(cached)
        except Exception:
            pass

    # Adaptive cadence: 0.5s up to 20 min, scaling gracefully for multi-hour sources (max ~2400 samples)
    step = sample_step if sample_step is not None else max(0.5, duration / 2400.0)
    if duration <= 120.0 and sample_step is None:
        step = 0.5

    # 1. Sequential audio extraction
    audio_events = _sequential_audio_events(local_path, start, end, window_sec=0.5)
    audio_by_halfsec = {int(round(ev.time * 2)): ev for ev in audio_events}

    # 2. Optional TransNetV2 cuts
    tn2_cuts = _optional_transnetv2_cuts(local_path, start, end)

    # 3. Single sequential PyAV video pass (zero repeated GOP seeks!)
    raw_samples: list[dict[str, Any]] = []
    width = height = 0
    fps = 30.0
    has_audio = bool(audio_events)
    face_detectors = [
        (cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'), False),
        (cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_alt2.xml'), False),
        (cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_profileface.xml'), True),
    ]
    ocr_backend_used = 'opencv_text_band_fallback'
    text_observations: list[ScreenTextObservation] = []

    with av.open(str(local_path)) as container:
        if not container.streams.video:
            raise ValueError('Source video has no video stream')
        vstream = container.streams.video[0]
        vstream.thread_count = 2
        width = int(vstream.width or 0)
        height = int(vstream.height or 0)
        fps = float(vstream.average_rate or 30.0)
        origin = float((vstream.start_time or 0) * vstream.time_base)
        has_audio = has_audio or bool(container.streams.audio)

        if start > 2.0:
            try:
                container.seek(int((origin + max(0.0, start - 1.0)) / vstream.time_base), stream=vstream)
            except Exception:
                pass

        next_target = 0.0
        prev_feat = None
        total_expected = max(1, math.ceil(duration / step))
        sample_idx = 0

        for frame in container.decode(vstream):
            if frame.time is None:
                continue
            rel = frame.time - origin - start
            if rel < -0.05:
                continue
            if rel >= duration:
                break
            if rel + 1e-4 < next_target:
                continue

            rgb_small = frame.reformat(160, 90, format='rgb24').to_ndarray()
            feat = _extract_frame_features(rgb_small)

            if prev_feat is not None:
                hist_diff = float(np.abs(feat['hsv_hist'] - prev_feat['hsv_hist']).sum()) / 3.0
                grid_diff = float(np.abs(feat['spatial_grid'] - prev_feat['spatial_grid']).mean())
                hud_diff = float(np.abs(feat['corner_hud'] - prev_feat['corner_hud']).mean())
                static_diff = float(
                    np.abs(feat['gray_small'].astype(np.int16) - prev_feat['gray_small'].astype(np.int16)).mean() / 255.0
                )
            else:
                hist_diff = grid_diff = hud_diff = static_diff = 0.0

            # Periodic facecam check on corner quadrants (every ~1.5s at low cost)
            face_corners: list[str] = []
            face_checked = sample_idx % max(1, int(round(1.5 / step))) == 0
            if face_checked:
                rgb_mid = frame.reformat(480, 270, format='rgb24').to_ndarray()
                face_corners = _detect_corner_faces(rgb_mid, face_detectors)

            # Optional OCR only on high text-band / suspected card frames (bounded budget)
            ocr_text = ''
            if feat['center_text_band_score'] >= 0.35 or feat['high_sat_frac'] >= 0.45 or feat['red_tint_score'] >= 0.22:
                if len(text_observations) < 120:
                    rgb_ocr = frame.reformat(480, 270, format='rgb24').to_ndarray()
                    txt, conf, backend = _try_ocr_on_frame(rgb_ocr)
                    ocr_backend_used = backend
                    ocr_text = txt
                    text_observations.append(ScreenTextObservation(
                        time=round(max(0.0, rel), 3),
                        text=txt,
                        text_band_score=round(feat['center_text_band_score'], 4),
                        confidence=conf if txt else round(feat['center_text_band_score'], 4),
                        backend=backend,
                        verification_status='ocr_detected' if txt else 'heuristic_text_band',
                    ))

            # Multi-feature shot boundary score
            boundary_score = (
                0.45 * min(1.0, hist_diff / 0.55)
                + 0.35 * min(1.0, grid_diff / 0.28)
                + 0.20 * min(1.0, static_diff / 0.25)
            )
            is_fade_break = prev_feat is not None and (
                (prev_feat['mean_luma'] < 14.0 and feat['mean_luma'] >= 24.0)
                or (prev_feat['mean_luma'] >= 24.0 and feat['mean_luma'] < 14.0)
            )
            is_graphic_cut = prev_feat is not None and (
                abs(feat['high_sat_frac'] - prev_feat['high_sat_frac']) >= 0.35
                or abs(feat['red_tint_score'] - prev_feat['red_tint_score']) >= 0.18
            ) and hist_diff >= 0.25
            is_hard_cut = prev_feat is not None and (
                hist_diff >= 0.38
                or (grid_diff >= 0.20 and static_diff >= 0.12)
                or (hud_diff >= 0.20 and hist_diff >= 0.24)
                or boundary_score >= 0.62
            )
            is_tn2_cut = bool(tn2_cuts and any(abs(rel - c) <= step * 0.6 for c in tn2_cuts))

            is_cut = bool(is_hard_cut or is_fade_break or is_graphic_cut or is_tn2_cut)
            btype: Literal['initial', 'hard_cut', 'fade_black', 'graphic_transition', 'gradual'] = 'hard_cut'
            if prev_feat is None:
                btype = 'initial'
            elif is_fade_break:
                btype = 'fade_black'
            elif is_graphic_cut:
                btype = 'graphic_transition'

            a_ev = audio_by_halfsec.get(int(round(max(0.0, rel) * 2)))
            raw_samples.append({
                'time': round(max(0.0, rel), 3),
                'hsv_hist': feat['hsv_hist'],
                'spatial_grid': feat['spatial_grid'],
                'corner_hud': feat['corner_hud'],
                'mean_luma': feat['mean_luma'],
                'std_luma': feat['std_luma'],
                'mean_sat': feat['mean_sat'],
                'high_sat_frac': feat['high_sat_frac'],
                'edge_density': feat['edge_density'],
                'center_text_band_score': feat['center_text_band_score'],
                'red_tint_score': feat['red_tint_score'],
                'motion': round(static_diff, 5),
                'hist_diff': round(hist_diff, 5),
                'grid_diff': round(grid_diff, 5),
                'hud_diff': round(hud_diff, 5),
                'boundary_score': round(boundary_score, 4),
                'cut': is_cut,
                'boundary_type': btype,
                'face_corners': face_corners,
                'face_checked': face_checked,
                'ocr_text': ocr_text,
                'audio_rms': a_ev.rms if a_ev else 0.0,
                'audio_change': a_ev.delta_rms if a_ev else 0.0,
                'sampled_silence': a_ev.is_silence if a_ev else False,
            })

            prev_feat = feat
            sample_idx += 1
            next_target = sample_idx * step
            tick(min(sample_idx, total_expected), total_expected)

    # 4. Aggregate samples into Shots
    shots = _build_shots(
        raw_samples,
        duration=duration,
        step=step,
        audio_events=audio_events,
        transcript=transcript or {},
        detector='transnetv2+pyav_multifeature_v1' if tn2_cuts is not None else 'pyav_multifeature_v1',
    )

    # Summarize detected persistent facecam corners across source
    corner_counts: dict[str, int] = {}
    for sh in shots:
        for c in sh.face_corners:
            corner_counts[c] = corner_counts.get(c, 0) + 1
    detected_facecams = sorted(k for k, v in corner_counts.items() if v >= 1)

    # Backwards-compatible coarse samples
    coarse_samples = [
        {
            'time': s['time'],
            'motion': s['motion'],
            'cut': s['cut'],
            'audio_rms': s['audio_rms'],
            'audio_change': s['audio_change'],
            'sampled_silence': s['sampled_silence'],
        }
        for s in raw_samples
    ]

    evidence = SourceEvidence(
        context=SourceContext(
            source_id=str(source),
            source_fingerprint=fp,
            start=round(start, 3),
            end=round(end, 3),
            duration=round(duration, 3),
            width=width,
            height=height,
            fps=round(fps, 3),
            has_audio=has_audio,
            content_category=content_category,
            confirmed_subject=confirmed_subject,
            source_title=source_title,
            detected_facecam_corners=detected_facecams,
            ocr_backend=f'{ocr_backend_used}:v2',
        ),
        shots=shots,
        audio_events=audio_events,
        text_observations=text_observations,
        coarse_samples=coarse_samples,
        sample_step_seconds=round(step, 4),
        processing_seconds=round(time.monotonic() - t0, 3),
    )

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(cache_path, evidence.model_dump(), ensure_ascii=True)
    return evidence


def _build_shots(
    raw_samples: list[dict[str, Any]],
    duration: float,
    step: float,
    audio_events: list[AudioEvent],
    transcript: dict,
    detector: str,
) -> list[Shot]:
    if not raw_samples:
        return []

    groups: list[list[dict[str, Any]]] = [[raw_samples[0]]]
    for sample in raw_samples[1:]:
        if sample['cut']:
            groups.append([sample])
        else:
            groups[-1].append(sample)

    # Merge tiny 1-sample flicker glitches (<0.6s) only when they are NOT dark/red/high-sat cards
    merged: list[list[dict[str, Any]]] = []
    for g in groups:
        g_start = g[0]['time']
        g_end = g[-1]['time'] + step
        g_dur = g_end - g_start
        is_distinct_card = (
            g[0]['mean_luma'] < 15.0
            or g[0]['red_tint_score'] >= 0.22
            or g[0]['high_sat_frac'] >= 0.45
            or g[0]['center_text_band_score'] >= 0.45
        )
        if merged and len(g) == 1 and g_dur < 0.7 and not is_distinct_card and g[0]['boundary_score'] < 0.78:
            merged[-1].extend(g)
        else:
            merged.append(g)

    segments = transcript.get('segments', [])
    shots: list[Shot] = []
    for idx, g in enumerate(merged):
        s_start = 0.0 if idx == 0 else g[0]['time']
        s_end = min(duration, merged[idx + 1][0]['time'] if idx + 1 < len(merged) else duration)
        if s_end <= s_start:
            s_end = min(duration, s_start + max(0.1, step))
        s_dur = max(0.05, s_end - s_start)

        hsv_mat = np.stack([x['hsv_hist'] for x in g], axis=0)
        grid_mat = np.stack([x['spatial_grid'] for x in g], axis=0)
        hud_mat = np.stack([x['corner_hud'] for x in g], axis=0)

        # Exclude the cut-entry sample's motion spike from intra-shot motion stats
        intra_motions = [x['motion'] for x in g[1:]] if len(g) > 1 else [g[0]['motion']]
        mean_motion = float(np.mean(intra_motions))
        peak_motion = float(np.max(intra_motions))
        static_frac = float(np.mean([m < 0.025 for m in intra_motions]))
        dark_frac = float(np.mean([x['mean_luma'] < 15.0 for x in g]))

        # Audio stats inside [s_start, s_end]
        sh_audio = [a for a in audio_events if a.time + a.duration > s_start and a.time < s_end]
        mean_rms = float(np.mean([a.rms for a in sh_audio])) if sh_audio else 0.0
        peak_rms = float(np.max([a.rms for a in sh_audio])) if sh_audio else 0.0
        aud_sil = float(np.mean([a.is_silence for a in sh_audio])) if sh_audio else 0.0
        aud_bursts = sum(1 for a in sh_audio if a.is_burst)
        mean_zcr = float(np.mean([a.zcr for a in sh_audio])) if sh_audio else 0.0

        # Speech inside [s_start, s_end]
        sh_segs = [seg for seg in segments if seg.get('end', 0) > s_start and seg.get('start', 0) < s_end]
        sh_text = ' '.join(str(seg.get('text', '')).strip() for seg in sh_segs).strip()
        sh_words = len(sh_text.split()) if sh_text else 0
        has_reaction = bool(sh_text and (REACTION_PATTERNS.search(sh_text) or '!' in sh_text or '?' in sh_text))

        # Face corners & OCR texts inside shot (require majority persistence when >=2 checked frames exist)
        checked_count = sum(1 for x in g if x.get('face_checked'))
        corner_hits: dict[str, int] = {}
        for x in g:
            for c in x['face_corners']:
                corner_hits[c] = corner_hits.get(c, 0) + 1
        min_face_hits = 2 if checked_count >= 2 else 1
        corners = sorted(c for c, cnt in corner_hits.items() if cnt >= min_face_hits)
        ocr_texts = [x['ocr_text'] for x in g if x['ocr_text']]
        ocr_joined = ' | '.join(dict.fromkeys(ocr_texts))[:200]

        shots.append(Shot(
            shot_id=idx,
            start=round(s_start, 3),
            end=round(s_end, 3),
            duration=round(s_dur, 3),
            boundary_type='initial' if idx == 0 else g[0]['boundary_type'],
            boundary_score=round(float(g[0]['boundary_score']), 4),
            detector=detector,
            sample_count=len(g),
            hsv_hist=[round(float(v), 5) for v in np.median(hsv_mat, axis=0)],
            spatial_grid=[round(float(v), 5) for v in np.median(grid_mat, axis=0)],
            corner_hud=[round(float(v), 5) for v in np.median(hud_mat, axis=0)],
            mean_luma=round(float(np.mean([x['mean_luma'] for x in g])), 2),
            std_luma=round(float(np.mean([x['std_luma'] for x in g])), 2),
            mean_sat=round(float(np.mean([x['mean_sat'] for x in g])), 4),
            high_sat_frac=round(float(np.mean([x['high_sat_frac'] for x in g])), 4),
            edge_density=round(float(np.mean([x['edge_density'] for x in g])), 4),
            center_text_band_score=round(float(np.mean([x['center_text_band_score'] for x in g])), 4),
            red_tint_score=round(float(np.mean([x['red_tint_score'] for x in g])), 4),
            mean_motion=round(mean_motion, 5),
            peak_motion=round(peak_motion, 5),
            static_fraction=round(static_frac, 4),
            dark_fraction=round(dark_frac, 4),
            face_corners=corners,
            mean_audio_rms=round(mean_rms, 5),
            peak_audio_rms=round(peak_rms, 5),
            audio_silence_fraction=round(aud_sil, 4),
            audio_burst_count=aud_bursts,
            mean_zcr=round(mean_zcr, 4),
            speech_text=sh_text[:600],
            speech_word_count=sh_words,
            speech_segment_count=len(sh_segs),
            has_reaction_cue=has_reaction,
            ocr_text=ocr_joined,
            uncertainty=0.2 if len(g) >= 4 else 0.45,
        ))
    return shots


def _remote_fallback_evidence(
    source: str,
    start: float,
    end: float,
    transcript: dict,
    content_category: str,
    confirmed_subject: str,
    source_title: str,
) -> SourceEvidence:
    duration = max(0.01, float(end - start))
    segments = transcript.get('segments', [])
    sh_text = ' '.join(str(s.get('text', '')).strip() for s in segments if s.get('end', 0) > 0 and s.get('start', 0) < duration).strip()
    shot = Shot(
        shot_id=0,
        start=0.0,
        end=round(duration, 3),
        duration=round(duration, 3),
        boundary_type='initial',
        boundary_score=0.0,
        detector='remote_transcript_only_fallback',
        sample_count=1,
        speech_text=sh_text[:600],
        speech_word_count=len(sh_text.split()) if sh_text else 0,
        speech_segment_count=len(segments),
        has_reaction_cue=bool(REACTION_PATTERNS.search(sh_text)) if sh_text else False,
        uncertainty=0.8,
    )
    return SourceEvidence(
        context=SourceContext(
            source_id=source,
            source_fingerprint=hashlib.sha256(source.encode()).hexdigest()[:32],
            start=round(start, 3),
            end=round(end, 3),
            duration=round(duration, 3),
            content_category=content_category,
            confirmed_subject=confirmed_subject,
            source_title=source_title,
            ocr_backend='unavailable_remote_source',
        ),
        shots=[shot],
        coarse_samples=[],
    )


def extract_frames_at_timestamps(
    path: Path,
    timestamps: list[float],
    max_width: int = 448,
    jpeg_quality: int = 80,
) -> list[tuple[float, bytes]]:
    """Extract resized JPEG frames sequentially/efficiently at source-relative timestamps."""
    import av
    import cv2

    if not timestamps:
        return []
    targets = sorted(max(0.0, float(t)) for t in timestamps)
    results: list[tuple[float, bytes]] = []
    with av.open(str(path)) as container:
        if not container.streams.video:
            return []
        stream = container.streams.video[0]
        stream.thread_count = 2
        origin = float((stream.start_time or 0) * stream.time_base)
        for t in targets:
            target = origin + t
            try:
                container.seek(int(max(0.0, target - 0.1) / stream.time_base), stream=stream)
            except Exception:
                pass
            frame = next(
                (f for f in container.decode(stream) if f.time is not None and f.time >= target - 0.15),
                None,
            )
            if frame is None:
                continue
            bgr = frame.to_ndarray(format='bgr24')
            h, w = bgr.shape[:2]
            if w > max_width:
                nh = max(2, int(round(h * max_width / w)))
                bgr = cv2.resize(bgr, (max_width, nh), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            if ok:
                results.append((round(t, 3), buf.tobytes()))
    return results
