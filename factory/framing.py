"""Scene-aware CPU face trajectory, two-speaker detection, and FFmpeg camera path.

Adapts MIT OpenShorts reframe_v2.py (sendcmd deduplication) and split_layout.py
(two-speaker coexistence geometry); LICENSE/NOTICE retained.
Evaluates crop trajectories independently across visual cuts so camera motion
never interpolates across unrelated shots.
"""
from __future__ import annotations

import numpy as np


def dedupe_sendcmd_lines(xs, fps, target='crop@c'):
    # Reused from MIT OpenShorts reframe_v2.py; LICENSE/NOTICE retained.
    lines = []
    previous = None
    for index, x in enumerate(xs):
        if x != previous:
            lines.append(f'{index / fps:.4f} {target} x {x};')
            previous = x
    return lines


def _dedupe_boxes(boxes, iou_threshold=0.30):
    """Drop duplicate overlapping detections of the same face, keeping the largest."""
    kept = []
    for box in sorted(boxes, key=lambda b: float(b[2]) * b[3], reverse=True):
        ax, ay, aw, ah = box
        overlap = False
        for bx, by, bw, bh in kept:
            ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
            iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
            inter = ix * iy
            union = aw * ah + bw * bh - inter
            if union > 0 and (inter / union) > iou_threshold:
                overlap = True
                break
        if not overlap:
            kept.append(box)
    return kept


def trajectory(source, duration, shot_cuts=()):
    """Compute scene-aware per-shot face trajectory (and detect two-speaker scenes).

    Resets camera smoothing cleanly at every cut in ``shot_cuts`` (seconds) so
    the crop jumps cleanly on cuts rather than panning across unrelated shots.
    """
    try:
        import cv2
    except ImportError:
        return None, {'strategy': 'blur_fallback', 'reason': 'Face detector unavailable'}
    capture = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 2])
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if not width or not height:
        capture.release()
        return None, {'strategy': 'blur_fallback', 'reason': 'No decodable video'}
    crop_w = min(width, int(height * 9 / 16)) // 2 * 2
    crop_h = min(height, int(crop_w * 16 / 9)) // 2 * 2
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

    cuts = sorted(float(c) for c in (shot_cuts or ()) if 0.0 < float(c) < duration)
    sample_fps = 2.0
    total_samples = max(1, int(duration * sample_fps) + 1)
    center_default = (width - crop_w) / 2.0
    camera = center_default
    positions = []
    detections = 0
    two_speaker_frames = 0
    left_centres = []
    right_centres = []
    prev_shot_idx = 0

    try:
        for index in range(total_samples):
            t_sec = index / sample_fps
            shot_idx = sum(1 for c in cuts if c <= t_sec)
            new_shot = shot_idx != prev_shot_idx
            prev_shot_idx = shot_idx

            capture.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000.0)
            ok, frame = capture.read()
            if not ok:
                break
            ratio = min(1.0, 640.0 / width)
            small = cv2.resize(frame, None, fx=ratio, fy=ratio)
            raw_faces = detector.detectMultiScale(
                cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(24, 24)
            )
            faces = _dedupe_boxes([tuple(map(float, f)) for f in raw_faces]) if len(raw_faces) else []

            # Two-speaker coexistence check (MIT split_layout.py rule: >=20% separation, >=4.5% width)
            big_faces = [f for f in faces if (f[2] / ratio) >= 0.045 * width]
            if len(big_faces) >= 2:
                pair = sorted(big_faces[:2], key=lambda f: f[0])
                lcx = (pair[0][0] + pair[0][2] / 2.0) / ratio
                lcy = (pair[0][1] + pair[0][3] / 2.0) / ratio
                rcx = (pair[1][0] + pair[1][2] / 2.0) / ratio
                rcy = (pair[1][1] + pair[1][3] / 2.0) / ratio
                if (rcx - lcx) >= 0.20 * width:
                    two_speaker_frames += 1
                    left_centres.append((lcx, lcy))
                    right_centres.append((rcx, rcy))

            if faces:
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                target = max(0.0, min(width - crop_w, (x + w / 2.0) / ratio - crop_w / 2.0))
                detections += 1
                if new_shot:
                    # Snap immediately on a visual cut — never pan across shot boundaries!
                    camera = target
                else:
                    movement = max(-crop_w * 0.125, min(crop_w * 0.125, (target - camera) * 0.35))
                    camera += movement
            elif new_shot:
                camera = center_default

            positions.append(int(round(camera / 2.0)) * 2)
    finally:
        capture.release()

    n_samples = len(positions)
    two_speakers = bool(n_samples >= 4 and two_speaker_frames >= 0.5 * n_samples)
    speaker_centres = None
    if two_speakers and left_centres and right_centres:
        speaker_centres = (
            (float(np.median([p[0] for p in left_centres])), float(np.median([p[1] for p in left_centres]))),
            (float(np.median([p[0] for p in right_centres])), float(np.median([p[1] for p in right_centres]))),
        )

    if not detections:
        return None, {
            'strategy': 'blur_fallback',
            'reason': 'No reliable face detected',
            'samples': n_samples,
            'two_speakers': False,
            'scene_aware_cuts': len(cuts),
        }
    return (crop_w, crop_h, positions), {
        'strategy': 'scene_aware_face_tracking' if cuts else 'face_tracking',
        'samples': n_samples,
        'detections': detections,
        'two_speakers': two_speakers,
        'speaker_centres': speaker_centres,
        'scene_aware_cuts': len(cuts),
    }
