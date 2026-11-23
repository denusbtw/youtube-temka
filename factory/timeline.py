"""Deterministic source/output arithmetic; editorial continuity needs review."""
import math
from bisect import bisect_left, bisect_right


def story_part_count(request, duration):
    overlap = request.series_overlap
    minimum_count = max(1, math.ceil((duration-overlap)/(request.maximum-overlap)-1e-9))
    maximum_count = min(100, math.floor((duration-overlap)/(request.minimum-overlap)+1e-9))
    if minimum_count > maximum_count:
        raise ValueError('Source range cannot be covered with these part lengths. Adjust the range or duration bounds.')
    count = request.story_count or max(minimum_count, min(maximum_count,
        round((duration-overlap)/((request.minimum+request.maximum)/2-overlap))))
    if not minimum_count <= count <= maximum_count:
        raise ValueError(f'Choose {minimum_count}–{maximum_count} story parts, or use automatic part count.')
    return count


def story(transcript, request, duration, title, scene_boundaries=()):
    count = story_part_count(request, duration)
    overlap = request.series_overlap
    segments = transcript['segments']
    boundaries = list(scene_boundaries)
    for segment, following in zip(segments, segments[1:]):
        gap = following['start'] - segment['end']
        if gap >= .35:
            boundaries.append(segment['end'] + min(gap, overlap or gap)/2)
        elif segment['text'].rstrip(' "”»').endswith(('.', '?', '!', '。', '！', '？')):
            boundaries.append(segment['end'])
    boundaries.sort()
    parts = []
    start = 0.0
    for index in range(count):
        remaining = count - index - 1
        lower = max(start+request.minimum, duration-remaining*(request.maximum-overlap))
        upper = min(start+request.maximum, duration-remaining*(request.minimum-overlap))
        ideal = min(upper, max(lower, start+(duration-start+remaining*overlap)/(remaining+1)))
        left = bisect_left(boundaries, max(lower, ideal-8))
        right = bisect_right(boundaries, min(upper, ideal+8))
        boundary = min(boundaries[left:right], key=lambda point: abs(point-ideal), default=None)
        end = duration if not remaining else ideal if boundary is None else boundary
        parts.append({'start_seconds':start, 'end_seconds':end, 'title':title,
                      'series_title':title, 'part':index+1, 'parts':count,
                      'reason':'Nearby scene, pause or sentence boundary' if boundary is not None and remaining
                               else 'Coverage boundary; verify continuity in preview',
                      'editorial_review_required':True})
        start = end-overlap
    return parts, f'{count} chronological parts covering the selected source with {overlap:g}s overlap.'


def connected(candidates,request,duration):
    if not candidates:return [],'No continuous event was identified'
    # Only subdivide one selected event, never concatenate unrelated highlights.
    event=max(candidates,key=lambda c:(c['end_seconds']-c['start_seconds'],c.get('score',0)))
    start=max(0,event['start_seconds']);end=min(duration,event['end_seconds'])
    span=end-start;overlap=request.series_overlap
    count=min(request.count,max(0,math.floor((span-overlap)/(request.minimum-overlap))))
    if not count:return [],'Selected event is shorter than the minimum clip duration'
    length=min(request.maximum,(span+(count-1)*overlap)/count)
    result=[]
    for i in range(count):
        a=start+i*(length-overlap);b=min(end,a+length)
        result.append({**event,'start_seconds':a,'end_seconds':b,
                       'title':event['title'],'series_title':event['title'],'part':i+1,'parts':count,
                       'editorial_review_required':True,'reason':'Continuous selected source event, chronological subdivision; review semantic continuity'})
    return result,f'{count}/{request.count} parts from one selected source event; overlap {overlap:g}s. Editorial continuity requires review.'


def manifest(candidates,request):
    position=0;segments=[]
    for i,item in enumerate(candidates):
        length=item['end_seconds']-item['start_seconds']
        offset=item.get('source_offset',request.start)
        segments.append({'segment_id':str(i+1),'source_id':item.get('source_id','source-1'),
                         'source_start_ms':round((offset+item['start_seconds'])*1000),
                         'source_end_ms':round((offset+item['end_seconds'])*1000),
                         'output_start_ms':round(position*1000),'output_end_ms':round((position+length)*1000),
                         'edit_start_ms':round(position*1000),'edit_end_ms':round((position+length)*1000),
                         'transition':item.get('transition',request.transition),'subtitle_track':bool(request.captions),
                         'overlay_assets':[request.logo_asset] if request.logo_asset else [],'audio_settings':{'mode':request.audio_mode}})
        position+=length
    return {'schema_version':1,'segments':segments,'duration_ms':round(position*1000)}
