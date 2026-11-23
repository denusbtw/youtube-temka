"""Bounded coarse video coverage for visual-review proposals."""
import math
from . import media
from jsonfile import write_json_atomic


def audio_samples(path,start,end,step,seek=False):
    import av
    import numpy as np

    buckets={}
    with av.open(str(path)) as container:
        if not container.streams.audio:return {}
        stream=container.streams.audio[0]
        origin=float((stream.start_time or 0)*stream.time_base)
        def include(frame,index):
            data=frame.to_ndarray()
            values=data.astype('float32')
            if data.dtype.kind in ('i','u'):
                info=np.iinfo(data.dtype)
                if data.dtype.kind=='u':values-=128
                values/=max(abs(info.min),info.max) if data.dtype.kind=='i' else 128
            total,count=buckets.get(index,(0,0))
            buckets[index]=total+float((values**2).sum()),count+values.size
        if seek:
            for index in range(math.ceil((end-start)/step)):
                target=start+origin+index*step
                container.seek(int(target/stream.time_base),stream=stream)
                for frame in container.decode(stream):
                    if frame.time is None or frame.time < target:continue
                    if frame.time >= target + .4:break
                    include(frame,index)
        else:
            for frame in container.decode(stream):
                if frame.time is None:continue
                position=frame.time-origin
                if position >= end:break
                if position >= start:include(frame,int((position-start)/step))
    result={};previous=None
    for index,(energy,count) in buckets.items():
        rms=math.sqrt(energy/count)
        result[index]={'audio_rms':round(rms,5),'audio_change':round(abs(rms-previous),5) if previous is not None else 0,
                       'sampled_silence': rms < .003}
        previous=rms
    return result


def sample(source, start, end, root, tick):
    import av
    import json
    path = root / 'visual-v2.json'
    if path.is_file():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved['start'] == start and saved['end'] == end:
            return saved['samples']
    local = media.local_source(source)
    duration = end - start
    step = max(2, duration / 450)
    count = math.ceil(duration / step)
    samples = []
    if local:
        with av.open(str(local)) as container:
            stream = container.streams.video[0]
            origin = float((stream.start_time or 0) * stream.time_base)
            previous = None
            for index in range(count):
                offset = index * step
                target = start + offset + origin
                container.seek(int(target / stream.time_base), stream=stream)
                frame = next((frame for frame in container.decode(stream)
                              if frame.time is not None and frame.time >= target - .1), None)
                if frame is None:
                    continue
                current = frame.reformat(48, 28, format='gray').to_ndarray().astype('int16')
                motion = float(abs(current - previous).mean() / 255) if previous is not None else 0
                samples.append({'time': offset, 'motion': motion, 'cut': motion > .22})
                previous = current
                tick(index + 1, count)
    else:
        # Retrieve at most 24 short, low-resolution probes irrespective of source length.
        count = min(16, math.ceil(duration / 40))
        probe = root / 'visual-probe.mp4'
        for index in range(count):
            offset = index * duration / count
            length = min(4, duration - offset)
            try:
                media.download(source, probe, start + offset, start + offset + length, 360, False,
                               lambda *_: tick(index, count))
                audio=audio_samples(probe,0,length,1)
                with av.open(str(probe)) as container:
                    previous = None
                    last = -1
                    for frame in container.decode(video=0):
                        if frame.time is None or frame.time < last + 1 or frame.time >= length:
                            continue
                        current = frame.reformat(48, 28, format='gray').to_ndarray().astype('int16')
                        motion = float(abs(current - previous).mean() / 255) if previous is not None else 0
                        samples.append({'time': offset + frame.time, 'motion': motion, 'cut': motion > .22,
                                        **audio.get(int(frame.time),{})})
                        previous, last = current, frame.time
            except (ValueError, RuntimeError, OSError, TimeoutError):
                pass
            finally:
                probe.unlink(missing_ok=True)
            tick(index + 1, count)
    if local and samples:
        audio=audio_samples(local,start,end,step,seek=True)
        for sample in samples:
            sample.update(audio.get(round(sample['time']/step),{}))
    if samples:
        write_json_atomic(path, {'start': start, 'end': end, 'samples': samples})
    return samples


def inspect_local_interval(path, start, end):
    """Bounded follow-up seeks; histogram change is not semantic evidence."""
    import av
    import numpy as np

    probes = min(32,max(3,math.ceil((end-start)/1.5)))
    boundaries = []
    previous = None
    previous_pixels = None
    observations = []
    step=(end-start)/probes
    audio=audio_samples(path,start,end,step,seek=True)
    count = 0
    with av.open(str(path)) as container:
        if not container.streams.video:
            return {'schema_version':2,'frames':0,'possible_cuts':[],'observations':[],
                    'semantic_verification':'unverified'}
        stream=container.streams.video[0]
        stream.thread_count=2
        origin=float((stream.start_time or 0)*stream.time_base)
        for index in range(probes):
            target=start+index*(end-start)/probes
            timestamp=origin+target
            container.seek(int(timestamp/stream.time_base),stream=stream)
            frame=next((item for item in container.decode(stream)
                        if item.time is not None and item.time>=timestamp-.08),None)
            if frame is None:continue
            pixels=frame.reformat(48,28,format='rgb24').to_ndarray()
            histogram=np.concatenate([np.bincount(ch.reshape(-1)//16,minlength=16)
                                      for ch in pixels.transpose((2,0,1))])/pixels.size
            if previous is not None and float(np.abs(histogram-previous).sum())>.8:
                boundaries.append(round(target,3))
            motion=float(np.abs(pixels.astype('int16')-previous_pixels).mean()/255) if previous_pixels is not None else None
            observations.append({'time':target,'motion':motion,'audio_rms':audio.get(index,{}).get('audio_rms')})
            previous_pixels=pixels.astype('int16')
            previous=histogram
            count+=1
    return {'schema_version':2,'frames':count,'possible_cuts':boundaries,'observations':observations,
            'semantic_verification':'unverified'}


def discontinuities(inspection, transcript, source_start):
    """Flag strong format breaks only when sampled inactivity and a cut agree."""
    quiet=[]
    breaks=[]
    for item in inspection.get('observations',[]):
        motion,rms=item['motion'],item['audio_rms']
        if quiet and item['time']-quiet[-1]>3:
            quiet=[]
        if motion is not None and rms is not None and motion<.015 and rms<.003:
            quiet.append(item['time'])
            continue
        if quiet and quiet[-1]-quiet[0]>=4 and item['motion'] is not None and item['motion']>.08:
            restart=item['time']
            if any(abs(restart-cut)<=2 for cut in inspection['possible_cuts']) and not any(
                seg['end']>quiet[0]-source_start and seg['start']<restart-source_start
                for seg in transcript.get('segments',[])
            ):
                breaks.append(round(restart-source_start,3))
        quiet=[]
    return breaks


def refine_local_plan(path, start, request, result, transcript=None, duration=None):
    """Preserve event windows across cuts unless independent evidence requires correction."""
    candidates=[]
    alternatives=[]
    decisions=[]
    attempts=0
    for clip in result['candidates']:
        a,b=clip['start_seconds'],clip['end_seconds']
        if not (math.isfinite(a) and math.isfinite(b) and 0<=a<b
                and request.minimum-1e-7<=b-a<=min(180,request.maximum)+1e-7
                and (duration is None or b<=duration+1e-7)):
            decisions.append({'start':a,'end':b,'status':'rejected','reason':'Invalid timestamps or duration'})
            continue
        if attempts>=6:
            candidates.append(clip|{'fine_review':'budget_exhausted','editorial_review_required':True})
            decisions.append({'start':a,'end':b,'status':'review_required','reason':'Fine inspection budget exhausted'})
            continue
        attempts+=1
        try:
            inspected=inspect_local_interval(path,start+a,start+b)
        except (OSError,ValueError,RuntimeError):
            decisions.append({'start':a,'end':b,'status':'rejected','reason':'Unable to decode inspection media'})
            continue
        if not inspected['frames']:
            decisions.append({'start':a,'end':b,'status':'rejected','reason':'No decodable inspection frames'})
            continue
        breaks=discontinuities(inspected,transcript or {},start)
        if breaks:
            alternatives.append(clip|{'fine_review':inspected,
                'reason':'A cut following sustained audiovisual inactivity suggests unrelated scenes. Review and trim manually.'})
            decisions.append({'start':a,'end':b,'status':'rejected','reason':'Audiovisual discontinuity','boundaries':breaks})
            continue
        reactions=sum(seg['end']>a and seg['start']<b for seg in (transcript or {}).get('segments',[]))
        inspected.update(speech_segments=reactions)
        candidates.append(clip|{'fine_review':inspected,'editorial_review_required':True,
                                'reason':clip.get('reason','')+' Fine inspection found no corroborated break. Event continuity requires review.'})
        decisions.append({'start':a,'end':b,'status':'review_required','reason':'Multi-shot interval retained; continuity unverified',
                          'sampled_cuts':len(inspected['possible_cuts'])})
    rejected=len(result['candidates'])-len(candidates)
    notice=f' Fine inspection retained {len(candidates)}/{request.count}; rejected {rejected}.'
    return result|{'candidates':candidates,'review_alternatives':alternatives,
                  'fine_decisions':decisions,'selection_summary':result['selection_summary']+notice}
