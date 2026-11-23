# Factory YouTube channels

Factory stores the two managed profile keys in SQLite. The YouTube ID is initially empty. A successful authenticated `channels.list(mine=true)` call establishes the binding. The Create selector is available before consent for offline rendering. The selected profile and any verified destination travel with the job; queueing a different channel is rejected. Mode remains overridable.

| Profile key | Display name | Default |
| --- | --- | --- |
| `leonida_mayhem` | Leonida Mayhem | English entertainment Shorts, 9:16 |
| `thevicescope` | TheViceScope | English editorial long-form, 16:9 |

### Authorize on the Factory host

Run the command from the repository with Python 3.12 and `httpx`. Use an existing Desktop OAuth client configured for YouTube. An existing YouTube authorized-user JSON can also supply its client fields. Its refresh grant is never reused for the new profile.

```powershell
uv run --no-project --python 3.12 --with httpx==0.28.1 python -m factory.youtube_oauth --client "PATH_TO_EXISTING_YOUTUBE_CLIENT_JSON" --channel-key leonida_mayhem
```

In Google's browser flow choose the required YouTube channel. If Google shows only the Google account or returns another identity, switch to the correct channel in YouTube and repeat consent. The CLI verifies the returned identity, including `@leo_may` for the first Leonida binding, and asks you to compare the authenticated ID with YouTube Studio. It saves the file only after confirmation.

Use `thevicescope` for TheViceScope if it needs new consent. Use `--reconnect` for an already saved managed credential. Reconnection verifies the existing ID and retains a local backup before atomically replacing its own file. The controller reads `secrets/youtube-managed/upload` through a read-only mount. Add `--analytics` for separate read-only Analytics consent, written to `secrets/youtube-managed/analytics`. In Factory verify the publishing identity first, then discover Analytics.

An existing legacy alias can be attached without moving or copying it using the authenticated `POST /api/youtube/profiles/{key}/legacy-binding` endpoint with `{"alias":"main"}`. This accepts only a channel verified by Google and already registered with that exact alias. Legacy credentials remain in their original directory. Do not use the Drive OAuth client unless the corresponding Google project is configured for the requested YouTube API and scopes.

### Publishing

Managed profiles require an approved Drive-verified render and private visibility. Safe disconnect disables the profile in Factory and retains its authorization files and audit history. The Publisher verifies the destination immediately before upload and checks the video ID, channel and privacy after upload. Retries resume saved sessions. Uncertain outcomes require reconciliation before any further attempt.

Live private verification requires an explicit approval for the uploads. An authenticated channel lookup and the mock upload tests are separate from live upload verification.

### Analytics credential selection

Analytics prefers a dedicated read-only credential. If no dedicated file exists for Leonida Mayhem, Factory can read non-monetary reports using its verified publishing credential with `youtube.readonly`. A failed dedicated credential is never silently replaced. Each sync checks the authenticated channel, uses its explicit ID in reports, and permits only the channel, uploads playlist, and video lookups and the allowlisted non-monetary report metrics. The Reporting API, group endpoints, and revenue metrics are outside this integration.
