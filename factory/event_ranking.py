"""Cross-modal evidence ranking. Scores are editorial heuristics."""
def event_window(time, duration, minimum, maximum, activity, sentence_edges):
    """Build a review window around nearby activity, allowing shot transitions."""
    if duration < minimum or maximum < minimum:
        return None
    nearby = [value for value in activity if abs(time-value)<=8]
    setup = min(nearby,default=time)-7
    payoff = max(nearby,default=time)+9
    target = min(duration,maximum,max(minimum,payoff-setup+15))
    start = max(0,min(duration-target,setup-(target-(payoff-setup))/2))
    end = start+target
    starts = [edge for edge in sentence_edges if 0<=edge<=time and abs(edge-start)<=2]
    ends = [edge for edge in sentence_edges if time<=edge<=duration and abs(edge-end)<=2]
    choices = [(a,b) for a in [start,*starts] for b in [end,*ends]
               if minimum-1e-7 <= b-a <= maximum+1e-7]
    return min(choices,key=lambda pair: (
        -int(pair[0] in starts)-int(pair[1] in ends),
        abs(pair[0]-start)+abs(pair[1]-end),pair,
    ),default=None)


def rank(features, category):
    weights = (45,30,15,10) if category in ('gameplay','visual') else (35,20,30,15)
    return round(sum(w*features[k] for w,k in zip(weights,('event_signal','boundary','context','duration_fit'))),2)
