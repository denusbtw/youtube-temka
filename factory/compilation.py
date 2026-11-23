"""Selection and transcript assembly for source-aware long-form timelines."""
from collections import defaultdict


def select(groups, count, target):
    ordered = [sorted(group, key=lambda c: -c.get('score',0)) for group in groups]
    selected = []
    duration = 0
    # Give each source with qualifying material a chance before taking seconds.
    while len(selected) < count and duration < target*.95:
        accepted = False
        for group in ordered:
            while group:
                candidate = group.pop(0)
                length = candidate['end_seconds'] - candidate['start_seconds']
                if duration + length <= target*1.05:
                    selected.append(candidate)
                    duration += length
                    accepted = True
                    break
            if len(selected) == count or duration >= target*.95:
                break
        if not accepted:
            break
    # Keep adjacent excerpts from a source in chronological order. Begin with
    # the source that has the strongest opening among those we can include.
    by_source = defaultdict(list)
    for candidate in selected:
        by_source[candidate['source_id']].append(candidate)
    groups = sorted(by_source.values(),key=lambda group:-max(c.get('score',0) for c in group))
    return [c for group in groups for c in sorted(group,key=lambda c:c['start_seconds'])], duration


def append_transcript(output, transcript, start, end, position):
    shift = position-start
    if transcript.get('language') and output['language'] is None:
        output['language'] = transcript['language']
    for segment in transcript['segments']:
        if segment['end'] <= start or segment['start'] >= end:
            continue
        words = [{**word,'start':max(start,word['start'])+shift,
                  'end':min(end,word['end'])+shift} for word in segment.get('words',[])
                 if word['end'] > start and word['start'] < end]
        output['segments'].append({**segment,'start':max(start,segment['start'])+shift,
                                  'end':min(end,segment['end'])+shift,
                                  'text':''.join(w['word'] for w in words) if words else segment['text'],
                                  'words':words})
