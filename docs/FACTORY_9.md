# Factory 9 workflows and recovery

This records the collaborator branch before integration. For current contracts and staging, see the [unified guide](../docs/unified-factory/WORKFLOWS.md).

## Create and review

- **Smart Highlights** ranks speech-based moments and adds candidates from sampled motion, scene changes and audio onsets. Set the source interval, clip duration bounds and preferred count. The Editor shows why each candidate was selected and how much media the signal scan covered.
- **Connect a Story** covers the selected source from beginning to end. Enter a fixed part count or let the system choose one. The planner computes the feasible count range from the duration bounds and overlap. It prefers nearby shot changes, pauses or sentence endings within those constraints.
- **Longs Compilation** accepts two to twelve YouTube, completed Twitch or uploaded sources. The preflight reports each source individually. Select the target running time and segment limit. The selection prioritizes coverage across sources and reports a shorter plan when suitable footage falls short. The renderer normalizes resolution, frame rate and audio layout before assembling a single horizontal MP4.

For story parts and highlights, the vertical export profile is independent of
the destination. The YouTube Shorts profile validates the configured maximum at
submission and after rendering. Select regular vertical video for longer parts.
The default is 180 seconds via `FACTORY_SHORTS_MAX_SECONDS`.
[YouTube's Shorts eligibility rules](https://support.google.com/youtube/answer/15424877?hl=en)

Turn on **Переглянути план перед рендером** to pause a Highlights or Longs job
in the Editor after analysis. There you can preview an uploaded source at a
selected range or open the hosted source, edit timing, reorder or remove
segments, and choose a cut or a short fade. Saving updates the revision.
Approving queues the same job for rendering. A saved plan retains its edits
through retries. The completed Longs timeline records both the source times and
the times in the assembled output. To revise a completed output, the existing
Output Editor creates a new render from the preserved editable media.

## Processing and storage

The controller stores work and lease state in SQLite. Job requests carry an
optional workflow and source list. Older single-source requests continue
through their original paths. Retry state and edit-plan revisions live in the
existing result JSON. No table rebuild or data reset is needed. A remote worker
can take a compilation when every source is remote and Drive output is enabled.

Local and remote worker code share probe, ASR, ranking, render and verification
functions. Each compilation source has its own scratch folder with source
metadata, ASR chunks, a signal scan and a selection checkpoint. The planner
works through sources sequentially. Subtitle translation sees only overlapping
segments from the selected clip. The default ASR chunk remains 300 seconds and
existing jobs keep their original chunk plans. Media logs use a bounded queue.

The visual pass samples grayscale frames at 64×36 and 2 fps. It measures
frame differences and audio energy relative to the source window. Up to twelve
60-second windows are spread across the selected range, and the job reports the
actual coverage. These signals can surface silent events and suggest cut
boundaries. Editors still verify what occurred and whether the scene works in
context. The implementation uses motion and audio measurements with the
existing transcript ranker. It has no object or game-event classifier.

## Failure and recovery

The job record carries a diagnostic code, stage, actionable message and
retryability. Recognized transient retrieval failures and processing timeouts
return the job to the queue for up to two automatic retries. Rate-limit
backoffs start at 30 seconds. Other transient failures start at 5 seconds.
Both double on the next attempt. The queue reconciles processing leases that
have been silent for more than two minutes. An expired upload lease without a
verified cloud checkpoint requires inspection before manual retry.

Reclaimed jobs use a new lease. Writes with an old lease are rejected. Probe,
ASR, signal scans, analysis and validated segment renders are reused from the
worker's scratch directory. A replacement machine can recover queue state and
approved plans from the controller. It needs to recompute local artifacts it
cannot access. The worker bounds child-process logs and periodically checks
progress, disk space, cancellation and leases during FFmpeg work.

Use the job's event list and stage panel to inspect the failure. A queued retry
shows the next attempt time. After addressing a terminal failure, **Повторити**
resumes from retained checkpoints. Completed local jobs are only marked ready
after media validation. Drive jobs also require verification in storage.

## HTTP entry points

- `POST /api/preflight` accepts a job request and returns a record per source.
- `GET /api/jobs/{id}/edit-plan` returns the persisted plan and revision.
- `PATCH /api/jobs/{id}/edit-plan` saves the submitted ordered segments.
- `POST /api/jobs/{id}/render` approves the submitted revision and requeues rendering.
- `GET /api/jobs/{id}/files/timeline.json` serves the assembled timeline.

These endpoints use the existing controller authentication. Changes based
on a stale plan revision return HTTP 409.
