# Factory 7: operation and verification

Start with `./scripts/start-factory.ps1`. The launcher validates configuration,
backs up SQLite, creates missing original starter assets, checks CUDA Whisper
in a subprocess, starts the controller/web/Ollama and reports service readiness.
Images build sequentially and unchanged images are reused. OAuth mounts remain
read-only. Service READY and complete product acceptance are separate results.

## Create and edit

The five main sections are Create, Jobs, Editor, Publish and Settings. Dashboard,
the generated-video Library and Analytics remain accessible through Settings.
Create accepts an authorized local video or completed YouTube/Twitch source.
Choose a channel profile, clip count, layout, text mode and optional background.
Technical controls remain in Advanced Settings.

Layouts are Auto, Fill Screen (face-sampled framing with blur fallback), Center
Crop, Blurred Sides and Split Screen. The composition preview uses a source
thumbnail or uploaded video, not a completed render. Final automatic framing,
subtitle timing and text wrapping must be reviewed in the final MP4. Only the
selected output player is mounted in Editor; other videos are not preloaded.

Text modes are No Text, Subtitles, Static Hook, Hook + Subtitles and Custom Text.
Static text supports duration, position, size, color, outline, shadow and a box.
Hook generation supplies 5-10 candidates when valid grounded output is available;
neutral localized suggestions are used otherwise. Scores are heuristic and all
generated hooks require review. The transcript does not establish unseen events.

Metadata has a versioned schema, title candidate pool, separate hashtag pool and
YouTube tags. Ranking weights are relevance 30%, support 25%, clarity 20%,
curiosity 15%, novelty 10%; this is not a predicted CTR. Insufficient evidence can
produce fewer than five supported titles and an explicit validation warning.
Changing metadata keeps the video hash and invalidates editorial approval.

Editor creates a new job/render version from preserved source intervals. Local
exports preserve their raw editable intervals on disk; Drive exports preserve
verified editable files on Drive. Older results without those sources need an
original-source upload. The previous successful video remains intact, and the
new MP4 is registered only after temporary-output validation. Approval is bound
to the render identity. New edits reserve a version number in SQLite when queued.
Concurrent edits of the same render lineage get distinct numbers and retries
keep their number. Failed or cancelled edits can leave gaps. Revoking approval also blocks a claimed upload before
network work begins.

## Languages and profiles

Publish contains authenticated channel profiles and their language, region,
editing defaults, timezone, public permission and separate automatic opt-in.
Create propagates the selected profile language into metadata and hooks.
Supported suggestion/translation choices include en, es, pt-BR, de, ru and uk.
Selecting translated metadata does not dub the original audio.

Optional subtitle translation preserves phrase start/end boundaries and does
not fabricate word-level alignment. Invalid/incomplete translation fails with
an actionable retry/original-language option. Translations require human review;
automated language detection and factual validation are not a linguistic audit.
When metadata translation is unavailable, the system keeps the selected language
using a neutral localized review draft instead of silently substituting English.

## Connected Shorts and Longs

Connected Series subdivides one continuous selected source event in chronological
order. It does not fill missing parts with unrelated high-scoring highlights.
Overlap is measured in source seconds, 0-5, default 3. An event too short for the
requested count yields fewer parts and an explanation. Semantic continuity still
requires editorial review. Series identity and part numbers persist per output.

Longs preserves operator interval order, permits reorder/remove/trim, supports
subtitles, PNG logos and localized editable CTA, and saves `timeline.json`.
Timeline source times, rendered output times and preserved-edit offsets are
separate, including after pause removal. First/last chosen source intervals can
serve as intro/outro; the transition implemented here is a hard cut. Arbitrary
external intro/outro insertions and a professional multitrack editor are not
implemented. Automated selection remains a proposed montage for human review,
not proof of narrative quality. A target duration bounds selected material; it
does not authorize filler or invented footage.

## Backgrounds, logos and licensing

Settings provides six locally generated CC0 demonstration loops, 270x480 at
24 FPS: bouncing ball, shapes, rings/obstacles, scrolling pattern, particle wave
and satisfying motion. They have no embedded third-party audio. Their lower
resolution limits their visual sharpness when enlarged. They are not actual
Minecraft/Subway Surfers recordings; those categories need operator-owned or
appropriately licensed media.

Assets retain hashes, duration/dimensions, source, licensing statement and
availability. Reimported identical files reuse the existing asset. New custom
uploads default to an explicit operator-rights statement rather than a fabricated
license grant. Additional license/category information can be supplied through
the upload API. Background selection supports none, specific and random category.
When the server is busy generating a preview frame, the editor can preview the
selected background as muted video in the browser. Secondary video is not
mapped into the output audio track.

## Publishing

In Editor, use **До публікації** to open publishing controls for the approved,
Drive-verified output. Visibility starts at Private and queueing requires confirmation.
Manual Public requires the exact approved render, an authenticated matching
channel, per-channel public permission and a final channel/visibility confirmation.
It is independent of automatic publishing, which remains off by default.
Scheduling accepts channel-local wall time and rejects daylight-saving gaps and
ambiguous times. UTC timestamps are persisted. Browser timezone does not silently
override channel timezone.

The publisher stores resumable sessions and video IDs and reconciles uncertain
results. A known/uncertain remote upload must never be replayed as a new upload.
If YouTube keeps an upload private, investigate API project upload restrictions
and channel permissions; making Public selectable does not prove public release.
Real public publication remains an explicit operator action, separate from tests.

## Drive retention

The unit is a completed package, including final videos, editable sources,
metadata, subtitles and previews. Default deletion is OFF, retained limit 50,
grace period 7 days. Dry-run never changes Drive. Protection and history are in
Settings. Enabling deletion lets Factory move eligible published packages to
Drive trash after the grace period, starting with the oldest until the package
limit is reached. Dry-run reports any projected excess after cleanup.

Eligibility requires every output's verified public publication, elapsed grace,
no pending/uncertain schedules, no active edit references and no protection.
Before removal, the authenticated YouTube identity and live public/processed
status are checked again. All files must match tracked size/hash and the job's
Factory ownership key. Folders and untracked files are never recursively removed.
Each trash operation is verified before a package becomes DELETED. Failures stay
retryable; pending/removed packages cannot become a new edit source. The numeric
limit never overrides protection or publication checks. Oldest eligible packages
are selected first. Automatic cleanup uses the same policy as manual cleanup.

## Memory and recovery

The default controller/worker ceiling is 4 GiB, Ollama 4 GiB, with container swap
disabled. These limits are ceilings, not reservations or proof that all models
fit. A shared host lock coordinates cooperating Docker instances and is released
on process exit. RAM admission checks keep tasks queued under low headroom.
Whisper small/int8 and Qwen 3B remain conservative defaults. One heavy pipeline,
metadata generation, starter generation or voice preview uses the slot at a time.

ASR defaults to 300-second chunks with a persisted chunk plan; legacy partial
checkpoints keep their original intervals. Native ASR/TTS allocations exit with
their subprocesses. FFmpeg decoders/filters/encoders and native BLAS threads are
bounded. Logs have queue and record-size bounds with backpressure and cancellable
reader cleanup. Pause removal stages intervals sequentially on disk, using PCM
between cuts to prevent repeated AAC delay. Container output is decoded and
validated for dimensions, duration, audio, black-frame samples and audio/video
end-time difference. These checks cannot establish entertainment value or all
forms of perceptual lip synchronization.

Scratch quota, free-space reserve and persistent asset bytes are distinct.
Temporary cleanup does not remove successful exports, approved renders, the
background library, OAuth or model caches. Checkpoints allow retry after failure.
The resource panel reports memory pressure; process exit diagnostics distinguish
known RAM/VRAM/disk errors without exposing raw signed URLs or secrets.

## Repeating verification

### Twitch VOD range downloads

Twitch completed VODs can use fragmented MP4 HLS. Seeking directly across the
full remote playlist can return an empty container even when FFmpeg exits zero.
Twitch ingestion therefore builds a temporary VOD playlist containing only
segments intersecting the requested interval and its initialization segment.
FFmpeg trims the small boundary offset locally; it never falls back to fetching
the whole VOD. Both audio chunks and selected video intervals use this path.
Live, encrypted and unsupported byte-range playlists fail explicitly. Empty or
undecodable media is rejected before Whisper, and an existing valid output is
replaced only after successful validation.

The regression suite serves real fragmented MP4 with timestamps around the
third hour and asserts that unrelated segments are never requested. A real
10-second audio interval at 03:00:00 from the reported Twitch VOD was verified
as 10 seconds of PCM audio. This fixes ingestion; it is not a claim that every
VOD or generated highlight has been editorially verified.

- `python -m pytest factory_tests tests/test_file_locks.py -q` runs Factory checks.
- `npm run build -- --config vite.factory.config.js` runs the production UI build
  from `dashboard`.
- `python verification/check_v7_memory.py` runs three sequential owned-fixture
  jobs against the running local app, with Drive only when already configured.
  It never queues a YouTube publication or enables retention. Raw samples and
  job IDs remain under ignored `verification/runtime/v7`.
- `./scripts/start-factory.ps1 -Status` reads deployment status without inference.
- `./scripts/logs-factory.ps1` exposes relevant local service diagnostics.

Use the release report for actual run results. Windows/WSL cache, other apps and
GPU desktop usage are separate measurement scopes from container working sets.
The actual verification machine has a 6 GiB RTX 3050; it does not certify a new
physical 4 GiB stress run.
