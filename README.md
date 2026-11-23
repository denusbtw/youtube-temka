# AI Content Factory

Self-hosted clipping and editing on the MIT OpenShorts core. Smart Highlights
uses multimodal events and activity signals. Connect a Story offers chronological
splitting of a selected range and an AI-verified narrative mode. Longs Compilation
assembles selected ranges from multiple sources into a horizontal MP4.
The existing single-source Longs and manual editing workflows are also available.

See the [unified workflow and deployment guide](docs/unified-factory/WORKFLOWS.md)
and the [integration baseline](docs/unified-factory/INTEGRATION_BASELINE.md).
The [Factory 7 guide](docs/FACTORY_7.md) covers historical operation and limits.

Acceptance results and remaining limits: [actual verification](verification/REPORT.md).
One owner-authorized private YouTube upload and separate read-only Analytics
access have been verified. Public release and live scheduling were not authorized.
Current evidence and limitations: [verification report](verification/REPORT.md).

## Start

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-factory.ps1
# PC A
docker compose --profile controller up -d --build
# Optional PC B, after its private controller URL/token and Drive mount are configured
docker compose --profile worker up -d --build
```

Open [localhost:5175](http://localhost:5175) and use the token from the ignored
`.env`. Configure models once in Settings. Existing OAuth files mount read-only;
no credential regeneration or replacement is necessary for supported formats.
A working legacy split Drive client/token integration has been reused and verified.

## Included

- FastAPI + React, persistent SQLite queue, fenced local/remote workers.
- Authenticated, checksummed background/music synchronization with a bounded worker cache.
- Automatic source probing, collapsible advanced controls, reusable preferences.
- Multi-hour audio-first analysis in 5-minute checkpointed chunks; selected video
  retrieval only. No full-VOD download fallback.
- Ollama structured ranking, explained unsuitable/visual decisions, visible
  heuristic fallback on malformed/empty responses or technical failures.
- Original sound, authorized music ducking/fades, silent mode and optional CPU
  Kokoro EN/ES narration with real voice previews.
- CPU face-sampled Smart Crop with smoothing and honest blur fallback, Gameplay
  Split, subtitles, opening hooks, synchronized pause removal, chronological Longs.
- Measured stages, preview/approval, verified resumable Drive uploads, checksummed
  editable-source preservation and new versions after local cleanup.
- Direct bounded Drive-to-YouTube resumable publishing, native schedules, channel
  limits, durable sessions and uncertain-outcome protection. Private/review default.
- One-time independent YouTube consent and editable queue metadata before upload.
- Automatic grounded publishing drafts from local Qwen: three title choices,
  description, tags/hashtags, category, audio/metadata languages and source credit.
  Versions, transcript evidence and manual revisions survive local cleanup.
- Separate technical/editorial checks, review-first approval, bounded waveform
  and frame QA, duplicate checks and explicit channel auto-approval safeguards.
- Channel-local calendar quotas and UTC spacing, existing-video reconciliation,
  representative real-frame thumbnails and visible API/Studio fallback status.
- Separate read-only Analytics, measured sync progress, real period/lifetime
  metrics and conservative observations when sample sizes support them.

## Guides and development

- [Deployment: PC A / PC B, GPU, OAuth, recovery](docs/FACTORY_DEPLOYMENT.md)
- [Metadata, YouTube and Analytics setup](docs/PUBLISHING_ANALYTICS.md)
- [Exact Windows PC B setup and physical acceptance limits](docs/PC_B_WINDOWS.md)
- [Verification and remaining blockers](verification/REPORT.md)
- [Integration provenance](THIRD_PARTY_FACTORY.md)
- [Optional TTS licenses](docs/licenses/TTS.md)
- [Upstream README](docs/UPSTREAM_README.md)

Default Docker processing uses CPU/int8. The optional GPU overlay checks available
VRAM and unloads sequential model stages. This release uses a physical RTX 3050
6 GB; the prior developer's 4 GB evidence remains historical. A second physical
PC is optional and excluded from this release's acceptance at the owner's request.
Narration is optional (`WITH_TTS=1`), requires a separate model download and adds
GPL phonemizer/eSpeak dependencies; the normal clipping image excludes them.

Python 3.12 development: install `requirements-factory.lock`, plus pytest, in a
venv. Run `python -m pytest factory_tests -q`. Build the actual UI with
`npm run build -- --config vite.factory.config.js` in `dashboard/`.
CI also exercises retained MIT modules on Linux, validates Compose and scans for
credential patterns. HTTP protocol fixtures are never described as real Google
acceptance tests. No public uploads occur without explicit channel authorization.

## One-command Windows deployment

From this checkout, run:

```powershell
.\scripts\start-factory.ps1
```

The default controller starts Ollama, FastAPI with its supervised local worker,
publisher and analytics loops, and the production web UI at
http://localhost:5175/factory.html. Missing local authentication is generated only
when `.env` is absent. Existing configuration and OAuth files are preserved.
A short Whisper inference initializes the persistent cache and verifies the selected
runtime. NVIDIA container access is probed before selecting the GPU overlay;
failed CUDA inference receives an explicitly reported CPU fallback.

Options: `-Role Worker`, `-CpuOnly`, `-Rebuild`, `-Status`, `-WithTTS`,
`-TestInference`. Worker mode requires initial private controller configuration.
Use `scripts/stop-factory.ps1`, `restart-factory.ps1`, `logs-factory.ps1`, and
`update-factory.ps1` for maintenance. Updates require a clean fast-forwardable
checkout and a successful SQLite online backup before pulling changes.
See [deployment details](docs/FACTORY_DEPLOYMENT.md).
