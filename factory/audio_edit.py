"""Transcript-driven pause removal with one shared video/audio/caption timeline."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory


def speech_timeline(transcript,start,end,padding=.18,min_gap=.8):
    ranges=[]
    for segment in transcript['segments']:
        units=segment.get('words') or [segment]
        for item in units:
            a=max(start,item['start']-padding);b=min(end,item['end']+padding)
            if b<=a:continue
            if ranges and a-ranges[-1][1]<min_gap:ranges[-1][1]=max(ranges[-1][1],b)
            else:ranges.append([a,b])
    if not ranges:return [[0,end-start]],transcript,start,end
    # Limit work and keep the source if there is no meaningful gap.
    if len(ranges)>80 or end-start-sum(b-a for a,b in ranges)<min_gap:
        return [[0,end-start]],transcript,start,end
    rebased=[];offset=0
    for a,b in ranges:
        for segment in transcript['segments']:
            if segment['end']<=a or segment['start']>=b:continue
            s=deepcopy(segment);s['start']=offset+max(0,segment['start']-a);s['end']=offset+min(b-a,segment['end']-a)
            if segment.get('words'):
                s['words']=[{**w,'start':offset+max(0,w['start']-a),'end':offset+min(b-a,w['end']-a)} for w in segment['words'] if w['end']>a and w['start']<b]
                s['text']=''.join(w['word'] for w in s['words'])
            rebased.append(s)
        offset+=b-a
    return [[a-start,b-start] for a,b in ranges],{**transcript,'segments':rebased},0,offset


def remove_pauses(source,output,transcript,start,end,has_audio,tick):
    from .media import run,ffmpeg
    ranges,timed,caption_start,duration=speech_timeline(transcript,start,end)
    if len(ranges)==1 and ranges[0]==[0,end-start]:return source,transcript,start,end
    # Parallel trim branches feed a sequential concat and can retain decoded
    # frames for the whole source. Render one interval at a time to disk instead.
    # PCM intermediates avoid adding AAC encoder delay to every interval.
    with TemporaryDirectory(prefix='pause-',dir=Path(output).parent) as work:
        parts=[]
        for i,(a,b) in enumerate(ranges):
            part=Path(work)/f'part-{i:03d}.mkv'
            args=[ffmpeg(),'-hide_banner','-loglevel','error','-y','-threads','2',
                  '-ss',str(a),'-i',str(source),'-t',str(b-a),'-map','0:v:0']
            args+=['-map','0:a:0','-c:a','pcm_s16le'] if has_audio else ['-an']
            run(args+['-c:v','libx264','-preset','fast','-crf','18','-threads','2',str(part)],tick)
            parts.append(part)
        manifest=Path(work)/'segments.txt'
        # Use editorial durations, not each container's rounded video duration,
        # so frame rounding cannot accumulate across dozens of speech intervals.
        manifest.write_text(''.join(f"file '{p.name}'\nduration {b-a:.9f}\n"
                                    for p,(a,b) in zip(parts,ranges)),encoding='utf-8')
        args=[ffmpeg(),'-hide_banner','-loglevel','error','-y','-f','concat','-safe','1',
                  '-i',manifest.name,'-map','0:v:0','-c:v','copy']
        args+=['-map','0:a:0','-c:a','aac'] if has_audio else ['-an']
        run(args+['-threads','2',str(Path(output).resolve())],tick,cwd=work)
    return output,timed,caption_start,duration
