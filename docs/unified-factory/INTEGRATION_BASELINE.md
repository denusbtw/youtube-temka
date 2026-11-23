# Unified Factory integration baseline

- Factory 8 PR #1 and original local HEAD: `f6cbed603c99518998c7b1cd9c72453615ae6a4c`.
- Factory 8 local source snapshot: `0f2b0d399d37975faa45c58834f5e0591c8e122e`.
- Factory 9 PR #2 and fork head: `7cdcf19474e4089ac6ad6617b76a9b1a1789a0fb`.
- Common ancestor: `0201d57168e4da9b789f5fe01c71b7a106e7d7e4`.
- PR #1 targets `feat/factory-7.2`, also its merge base `739dac181323fba04855e219d5fd8768e9e3580e`. PR #2 targets main (`d0d7bd9ec8fb5196a3876edce8490843d54f7f0a` at audit).
- The local snapshot captures 26 modified tracked files and four previously untracked files. Inspect the exact paths with `git diff --name-status f6cbed6 0f2b0d3`. Compare each branch with `git diff --name-status 0201d57 0f2b0d3` and `git diff --name-status 0201d57 7cdcf19`.
- The staging controller and worker contain byte-identical copies of the local API, pipeline, worker, analytics and managed-channel files. Images have no Git revision label. These hashes corroborate the local implementation for the files tested.
- Staging web uses port 15175 and the controller uses port 18082. Controller and worker have separate writable data, scratch and model volumes and share the host-lock volume. Controller credential and legacy Drive mounts are read-only. The web image has no mounts.
- Staging controller holds 21 background MP4s. The host archive currently holds 61 MP4s recursively. Registration and provenance require a separate audit.
- Recovery source branch: `backup/factory-8-local-baseline`. Local bundle and online SQLite backup live in `%LOCALAPPDATA%/Factory89-integration-backup-0f2b0d3`. SQLite quick check returned ok and the snapshot contains five jobs. Media remain in the original volumes. Restore into a separate deployment to verify recovery without changing staging.

## Preservation matrix

| Area | Factory 8 baseline | Factory 9 contribution | Integration constraint |
| --- | --- | --- | --- |
| Source evidence | Source Intelligence, discovery, semantic events, Qwen3-VL, ranking and framing | Bounded audio/motion scans and coverage | Reuse source evidence and keep semantic verification for AI decisions |
| Story | Connected semantic selection and sequential splitting | Full-range split with configurable count, overlaps and timestamps | Preserve legacy series meanings and expose two explicit story modes |
| Compilation | Longs timeline and rendering from a single source | Per-source ranges, montage, source IDs, normalization and fades | Keep source-to-output mapping and per-source analysis checkpoints |
| Editing | EditManifest, manual selection, crop, layouts, subtitles and hooks | Revisioned review plan, trims, ordering and transition choices | Link plan revision and source identity to render checkpoints |
| Queue and recovery | SQLite leases, stage checkpoints, remote workers and upload safeguards | Backoff, lease recovery and stale plan fencing | Keep one queue and block uncertain uploads |
| Identity, assets and history | OAuth, Analytics, Drive, asset registration and saved jobs | Workflow UI and API additions | Retain existing paths and defaults, audit in isolated staging |
