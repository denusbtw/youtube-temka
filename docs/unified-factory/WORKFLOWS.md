# Unified workflows

Requests without a workflow keep their Factory 8 series semantics. Existing
Factory 9 workflow requests deserialize as version 1. Version 1 Story still
covers its full range. New dashboard requests use version 2. Version 2 Story
requires an explicit `story_mode` of `chronological` or `ai_verified`.

Version 2 semantic highlights, AI Story and compilation use Source Intelligence,
the Semantic Event Graph and the existing VLM verification adapter. Highlights
and compilation derive activity proposals and coverage from the shared source
evidence. Chronological splitting can use bounded signal scans to improve cut
placement while keeping coverage and overlap deterministic.

Reviewable highlights and compilations enter `REVIEW`. The edit-plan read,
update and render endpoints share an optimistic revision. Stale updates return
409. Approved revisions requeue the existing job. A timeline points to its source
ranges, output ranges and per-clip EditManifests. Segment checkpoints use source
and render fingerprints. SQLite keeps request and result changes in its existing
JSON columns. The existing schema and data remain readable.

## Isolated Windows staging

`compose.unified.local.yml` uses the local Factory 8 dependency images, mounts
the unified code and built dashboard read-only, and shares the read-only model
cache and the host inference lock. SQLite and the asset copy live in the
`factory89-unified_unified_data` Docker volume. Render outputs and checkpoints
live in the separate `factory89-unified_unified_scratch` volume. Native Docker
volumes keep SQLite and scratch accounting off Windows bind mounts. Set
`FACTORY89_CONTROLLER_TOKEN` to a separate token of at least 32 characters.
To import prior state, create the controller without starting it. Seed its new
data volume with an online SQLite backup and copies of the asset directories.
Copy the checkpoint and media snapshot into the new scratch volume. Run SQLite's
quick check and set ownership to the container user (10001) before starting the
controller. Finished videos can be exported with `docker cp`. Build the dashboard with
`npm ci` and `npm run build` in `dashboard`, then run:

```powershell
docker compose -p factory89-unified -f compose.unified.local.yml up -d
```

The dashboard listens on loopback port 25175 and the API on 28082. The deployment
disables automatic publishing and the publisher and Analytics background loops.
For local staging, keep any provider verification read-only and keep credential
mounts outside the repository. The integration backup and operator token stay on
the local machine.

To verify the existing providers through the unified controller, combine
`compose.unified.local.yml` with `compose.unified.providers.yml`. Point
`FACTORY89_MANAGED_OAUTH`, `FACTORY89_LEGACY_YOUTUBE`,
`FACTORY89_LEGACY_ANALYTICS` and `FACTORY89_LEGACY_DRIVE` to the existing
credential directories. The override mounts them read-only. Compose keeps the
unified data and scratch mounts and leaves both background provider loops disabled.
