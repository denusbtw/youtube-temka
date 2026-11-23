"""Offline temporal benchmark. Editorial annotations are external evidence."""
import argparse
import json
import math
from pathlib import Path


def interval(value, duration):
    return (isinstance(value, list) and len(value) == 2 and
            all(type(v) in (int,float) and math.isfinite(v) for v in value) and
            0 <= value[0] < value[1] <= duration)


def iou(left, right):
    common = max(0, min(left[1],right[1])-max(left[0],right[0]))
    return common / (max(left[1],right[1])-min(left[0],right[0]))


def evaluate(annotations, results, k=3):
    duration=annotations['duration']
    if annotations['schema_version']!=1 or not math.isfinite(duration) or duration<=0 or k<1:
        raise ValueError('Expected annotation schema 1, positive duration and positive K')
    events=annotations['events']
    if not all(interval(event['interval'],duration) for event in events):
        raise ValueError('Invalid reference event interval')
    chosen=results['candidates'][:k]
    valid=[]
    for item in chosen:
        bounds=[item.get('start_seconds'),item.get('end_seconds')]
        if interval(bounds,duration):valid.append(bounds)
    matches=[max((iou(event['interval'],bounds) for bounds in valid),default=0) for event in events]
    duplicate=sum(any(iou(bounds,prior)>=.5 for prior in valid[:i]) for i,bounds in enumerate(valid))
    rejected=annotations.get('rejected_intervals',[])
    if not all(interval(bounds,duration) for bounds in rejected):raise ValueError('Invalid rejected interval')
    return {
        'source':annotations['source_id'],'fixture_type':annotations['fixture_type'],'k':k,
        'event_recall_at_k':sum(score>=.5 for score in matches)/len(matches) if matches else None,
        'mean_best_event_iou':sum(matches)/len(matches) if matches else None,
        'invalid_interval_rate':(len(chosen)-len(valid))/len(chosen) if chosen else None,
        'duplicate_rate':duplicate/len(valid) if valid else None,
        'rejected_overlap_count':sum(any(iou(bounds,bad)>=.5 for bad in rejected) for bounds in valid),
        'setup_payoff_present':sum(bool(item.get('setup') and item.get('payoff')) for item in chosen),
        'technical_observations':results.get('technical_observations',{}),
        'editorial_quality':'NOT_VERIFIED',
    }


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotations',type=Path)
    parser.add_argument('results',type=Path)
    parser.add_argument('--k',type=int,default=3)
    args=parser.parse_args()
    print(json.dumps(evaluate(json.loads(args.annotations.read_text(encoding='utf-8')),
                              json.loads(args.results.read_text(encoding='utf-8')),args.k),indent=2))
