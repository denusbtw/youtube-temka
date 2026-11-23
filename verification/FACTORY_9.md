# Factory 9 verification

This records the collaborator branch before integration. For current contracts and staging, see the [unified guide](../docs/unified-factory/WORKFLOWS.md).

## Baseline

The working tree started clean on `denusbtw/youtube-temka` main at
`0201d57168e4da9b789f5fe01c71b7a106e7d7e4`, matching origin. The named
`Eclipse-XS/youtube-temka` fork was at
`d0d7bd9ec8fb5196a3876edce8490843d54f7f0a`, an ancestor eleven commits
behind the checkout. The implementation uses the current checkout, including
the existing bounded Twitch HLS fixes from the intervening commits.

The previous pipeline checkpointed ASR and individual renders. Expired
processing leases required manual retry, the connected mode subdivided a
selected highlight, and Longs used a single input. The recovery, workflow and
media tests exercise the new behavior along with older requests.

## Reproduction

```powershell
.venv/Scripts/python.exe -m pytest factory_tests -q -p no:cacheprovider --basetemp verification/test-output/factory9-tests
.venv/Scripts/python.exe -m verification.check_v9
.venv/Scripts/python.exe -m verification.check_v9 --youtube
docker compose config --quiet
Set-Location dashboard
npm run build -- --config vite.factory.config.js
```

Use a scratch path short enough for Windows media and asset-cache filenames.
FastAPI TestClient requires Windows loopback sockets. The test process used a
short workspace path and loopback access after the managed sandbox prevented
the TestClient from starting. Owned fixtures and output live under ignored
`verification/test-output`. The YouTube run uses the source and attribution
documented in [SOURCE_LICENSE.md](SOURCE_LICENSE.md).

## Local outcomes

- **Highlights** rendered a controlled visual event with no dialogue and no audio track. The job reported the sampled media signals as its selection method.
- **YouTube** probed and scanned seconds 60–120 of the authorized Blender fixture and rendered a verified 35-second highlight locally. The initial 30–40-second range produced no qualifying event and correctly stopped for review.
- **Story** passed a 12-minute planning test with three parts, full range coverage and the requested four-second overlap. A separate 15-second end-to-end fixture rendered three playable parts of 5.67, 5.50 and 5.67 seconds.
- **Compilation** rendered one validated 900-second MP4 from three owned five-minute fixtures with different dimensions and frame rates. The timeline kept three distinct source IDs. A shorter three-input test saved a revised order, rejected a stale revision and rendered the approved fade without rerunning ASR.
- **Recovery** passed injected interruption, retry-budget, upload fencing and expired-processing lease checks. In the real-media interruption test, a failure during the second render requeued the job and reused the first render.
- **Preflight** reported each source individually and rejected an unauthenticated request. Story settings outside the feasible part counts and the Shorts destination limit are checked before rendering.
- **Build** passed the 190-test Factory suite, followed by the 12-test API rerun with the new preflight test. The production dashboard build, Python compilation and Compose configuration validation also passed.

## Performance and editorial scope

The native Windows session reported 16.31 GB physical RAM and an RTX 3050
Laptop GPU with 6,144 MiB VRAM. The test used CPU x264. The 900-second
compilation took 219.64 seconds and the short story render took 7.62 seconds.
Host available memory snapshots were 8.50 GB at start and 8.37 GB at finish.
The snapshots do not measure peak job, process, container or GPU usage.

The controlled fixtures establish technical playback, source identity and
visual-event detection. A human entertainment-quality review on real gaming
footage is still pending. Scene, motion and audio measurements are limited to
the sampled intervals. The supported transitions are straight cuts and brief
fades. Automated game-event recognition, sound-effect placement and
cross-source narrative quality remain open.

Docker Desktop's Linux engine pipe was unavailable. Live Docker deployment,
an actual container restart, a full authorized Twitch VOD, and the new
compilation through live Drive remain unverified in this run. The automated
suite uses local or mocked services for these paths and does not publish.
