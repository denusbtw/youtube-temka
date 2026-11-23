# Factory 7.1 incident evidence

The persisted job 876e12125d8d4b50957d3831e8d0bea2 processed YouTube video
yux8s3lwLss, titled GTA 6 FUNNY MOMENTS & WINS #1 (GTA 6 FAILS). It used
Connected Series, count 3, minimum 20 s, configured maximum 400 s, overlap 3 s
and the entire 0–618 s source. Silence removal and Drive storage were enabled.

The job recorded one qwen2.5:3b call in one window, zero rejected intervals and
no technical fallback. The retained normalized selection was 105.02–139.80 s,
score 85, confidence 0.9, title “I had a tank earlier.” The raw model response
and local analysis checkpoint were absent. The old heuristic generated these
25 proposals when rerun against the 29-segment transcript retrieved from the
job's private Drive artifact:

```
4.34–68.06    12.36–106.64   15.89–110.84   16.59–110.84   105.02–139.80
108.48–172.98 108.96–172.98  109.82–172.98  138.20–187.22  171.36–191.54
176.75–215.94 186.08–215.94  187.72–215.94  188.78–215.94  189.88–216.98
190.88–235.28 192.22–236.22  215.34–236.22  216.26–251.82  234.56–545.99
235.36–545.99 237.62–545.99  251.82–545.99  534.32–597.82  554.12–597.82
```

Ollama saw only 12 evenly sampled proposals. The series stage then subdivided
the sole selected highlight. A 34.78 s selection cannot hold three 20 s parts
with 3 s overlaps (54 s needed). It returned one part instead. The actual 6.30 s
output was the post-silence-removal MP4, recorded as 1080×1920, 189 decoded
frames, audio present and no sampled black frames. The selected source interval
remained 34.78 s. This evidence was collected before changing the planner.

## Replay against the preserved transcript

A fresh legacy Ollama call gave three candidates, of which two survived
the overlap filter (105.02–139.80 s and 186.08–215.94 s). The legacy series
stage again kept only 105.02–139.80 s and returned 1/3. The exact source ID
probed successfully as a 618-second video. The replay and persisted record
are separate observations.

## Factory 7.1 replay

The final Connected Story planner reused the 29-segment ASR checkpoint and
60 frame/audio samples from bounded probes. At three parts, 35–60 seconds each
and 3 seconds of overlap, the acceptable event span was 99–174 seconds.
Ollama marked four windows insufficient and proposed two event spans outside
the feasible range. The job reported insufficiency and exported no series.
This establishes that the available evidence did not support automatic
continuity for this run.

A separate Independent Highlights plan returned three review candidates:
554.120–600.260 s from speech, 103.908–148.908 s from frame and audio change,
and 217.783–262.783 s from frame and audio change. The first model title and
its mission-failure explanation were not grounded in the selected excerpt.
The review export replaces that explanation with a transcript-based reason.
These intervals are editorial candidates. Their visual activity evidence
does not establish narrative completeness.

The authenticated controller rendered those three candidates with blurred
sides, source audio and English captions for local review. The decoded MP4
durations were 46.167, 45.000 and 45.000 seconds at 1080×1920. The largest
audio/video end delta was 23 ms. The three clips had 0, 1 and 0 black samples,
respectively. The two activity windows crossed multiple scenes. All three
outputs remain in editorial review. No publication was submitted.

During acceptance, the first completed encode was recovered after the storage
scan frequency was reduced. The transcript and exact approved intervals were
reused. A third-clip SRT cue had identical start/end times because of an ASR
timestamp. SRT generation now drops invisible cues. The three corrected SRT
files and burned ASS tracks have positive cues inside the decoded durations.
The corrected sidecars preserved the byte-identical burned tracks.
