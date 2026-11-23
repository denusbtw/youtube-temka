"""Low-resolution motion, scene and audio analysis with bounded scan windows."""
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from . import media
from .models import Candidate


WINDOW_SECONDS = 60
MAX_WINDOWS = 12
FRAME_BYTES = 64 * 36


def scan(source, request, metadata, end, resolution, root, tick):
    local = source if isinstance(source, Path) else media.local_source(source)
    duration = end - request.start
    count = math.ceil(duration / WINDOW_SECONDS)
    indices = range(count) if count <= MAX_WINDOWS else [
        round(i * (count-1)/(MAX_WINDOWS-1)) for i in range(MAX_WINDOWS)]
    events, scenes = [], []
    scanned = 0.0
    low_resolution = max((r for r in metadata['resolutions'] if r <= 480),
                         default=min(resolution, min(metadata['resolutions'])))
    for index in indices:
        offset = index * WINDOW_SECONDS
        length = min(WINDOW_SECONDS, duration-offset)
        start = request.start + offset
        with TemporaryDirectory(prefix='signals-', dir=root) as folder:
            work = Path(folder)
            path = local
            seek = start
            if path is None:
                path = work/'sample.mp4'
                media.download(source, path, start, start+length, low_resolution, False, tick)
                seek = 0
            motion, cuts, audio = scan_window(path, seek, length, metadata.get('has_audio'), work, tick)
        scenes.extend(offset+t for t in cuts)
        for i in range(max(len(motion),len(audio))):
            visual = motion[i] if i < len(motion) else 0
            sound = audio[i] if i < len(audio) else 0
            if visual or sound:
                events.append((offset+i*.5, visual, sound))
        scanned += length
    return events, scenes, {'scanned_seconds':round(scanned,2), 'source_seconds':round(duration,2),
                            'windows':len(indices), 'fps':2, 'frame_size':[64,36]}


def scan_window(path, start, duration, has_audio, work, tick):
    import numpy as np

    frames = work/'frames.gray'
    samples = work/'audio.pcm'
    args = [media.ffmpeg(),'-hide_banner','-loglevel','error','-y','-threads','2',
            '-ss',str(start),'-i',str(path),'-filter_threads','1',
            '-map','0:v:0','-t',str(duration),'-vf','fps=2,scale=64:36,format=gray',
            '-an','-threads','1','-f','rawvideo',str(frames)]
    if has_audio:
        args += ['-map','0:a:0?','-t',str(duration),'-vn','-ac','1','-ar','4000',
                 '-f','s16le',str(samples)]
    media.run(args, tick)
    motion, cuts, audio = [], [], []
    with frames.open('rb') as stream:
        previous = None
        while data := stream.read(FRAME_BYTES):
            if len(data) != FRAME_BYTES:
                break
            frame = np.frombuffer(data,dtype=np.uint8).astype(np.int16)
            change = 0 if previous is None else float(np.abs(frame-previous).mean())/255
            if change >= .23:
                cuts.append(len(motion)*.5)
            motion.append(change)
            previous = frame
    if samples.is_file():
        levels = []
        with samples.open('rb') as stream:
            while data := stream.read(4000):
                values = np.frombuffer(data,dtype='<i2').astype(np.float32)/32768
                levels.append(float(np.sqrt(np.mean(values**2))))
        baseline = float(np.median(levels)) if levels else 0
        audio = [max(0, level-max(.025,baseline*1.5)) for level in levels]
    return motion, cuts, audio


def from_evidence(evidence):
    samples = evidence.coarse_samples
    step = evidence.sample_step_seconds
    events = [(sample['time'], sample['motion'], sample['audio_change']) for sample in samples]
    scenes = [shot.start for shot in evidence.shots if shot.start > 0]
    return events, scenes, {
        'scanned_seconds': round(min(evidence.context.duration, len(samples) * step), 2),
        'source_seconds': evidence.context.duration,
        'sample_count': len(samples),
        'sample_step_seconds': step,
        'provider': 'source_intelligence',
    }


def propose(events, scenes, request, duration, title):
    length = min(duration, (request.minimum+request.maximum)/2)
    if length < request.minimum:
        return []
    ranked = sorted(events, key=lambda e: -(e[1]*.7 + e[2]*.3))
    candidates = []
    selected = []
    for time, motion, audio in ranked:
        if motion < .065 and audio < .035:
            continue
        start = min(duration-length, max(0, time-length*.4))
        end = start + length
        near = [point for point in scenes if abs(start-point) <= 2
                and point <= time and request.minimum <= end-point <= request.maximum]
        if near:
            start = min(near,key=lambda point:abs(start-point))
        near = [point for point in scenes if abs(end-point) <= 2
                and point > time and request.minimum <= point-start <= request.maximum]
        if near:
            end = min(near,key=lambda point:abs(end-point))
        if any(min(end,b)-max(start,a) >= .25*min(end-start,b-a) for a,b in selected):
            continue
        selected.append((start,end))
        evidence = 'frame change and audio onset' if motion >= .065 and audio >= .035 else (
            'frame change' if motion >= .065 else 'audio onset')
        candidates.append(Candidate(start_seconds=start,end_seconds=end,
            score=min(78,46+100*(motion*.7+audio*.3)),title=(title or 'Source event')[:100],
            language='und',reason=f'Sampled {evidence} at {time:.1f}s; verify the event in preview',
            confidence=.35))
        if len(candidates) >= request.count:
            break
    return candidates
