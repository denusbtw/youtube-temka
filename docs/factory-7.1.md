# Factory 7.1 Shorts and secondary footage

New Shorts jobs use selection schema 2. Existing jobs retain their original
recovery behavior. Replanning creates a new job with a copied transcript
checkpoint from local scratch or verified Drive storage. Local visual analysis
is copied when available. The selection identity includes mode, count, duration
bounds, overlap, source interval, model, transcript and algorithm version.

| Mode | Selection |
| --- | --- |
| Independent Highlights | Rank supplied speech intervals for opening, context and resolution. Deterministic proposals prefer complete sentences, natural gaps, dense speech and a duration near 45 seconds. Supplement a partial result outside explicitly rejected windows. Suppress overlapping intervals and identical normalized transcript text. |
| Connected Story | Search for one event supported by exact transcript quotes and one beat per part. Require a feasible span before splitting. For three 45-second parts with 3-second overlaps, the preferred span is 129 seconds. Prefer nearby sentence endings, coarse scene changes and sampled silence for splits. |
| Sequential Source Split | Cover the selected interval in order with the exact requested overlap. Report incompatible count and duration settings without silently switching modes. |

The Create form defaults to a 35-second minimum and a 60-second maximum. The
[YouTube Shorts platform cap](https://support.google.com/youtube/answer/15424877)
is 180 seconds. Manual intervals allow shorter clips. Independent pause removal
is skipped if it would cross the configured minimum. Series keep the planned
timeline.

Coarse frame changes, motion, audio RMS, silence and transcript cues guide
visual review. They do not establish event semantics or audience probabilities.
Connected Story currently requires grounded speech evidence. Sparse probes can
miss events between samples, especially on multi-hour sources. A series split
may still require review if a feasible boundary intersects speech.

Enable the plan-only option in Create to review candidates, adjust bounds,
remove or add intervals, reorder, rerun selection, and approve a new render.
Uploaded sources preview inline. YouTube previews link to the source timestamp.
Approving an unchanged plan retains its narrative evidence. Experimental edits
preserve one ending and use available sentence boundaries for shorter variants.
They do not enable automatic publication. Analytics compares manually published
variants on the same channel over a shared complete report window. It does not
infer viewed-versus-swiped or rewatch data.

The persistent controller volume stores video in `/data/backgrounds`, metadata
in `/data/jobs.sqlite3` and cached frames in `/data/asset-previews`. Archived assets
remain recoverable for existing jobs. The Commons importer requires CC0
metadata and operator provenance review, caps downloads at 100 MiB, normalizes
video to silent MP4, validates it, and hashes the result. Operator-owned game
footage can be uploaded. Split screen supports panel order, split ratio, fit or
fill, caption margin, offset and playback speed. The secondary video can loop
or hold its final frame. Loop repeats use hard cuts.

## Owned-fixture acceptance

An authenticated Docker controller processed a 150-second generated source.
The real Create and Editor screens were verified against the isolated test
controller.

| Part | Source interval (s) | Decoded duration (s) | Decoded frames |
| --- | --- | --- | --- |
| 1 | 0–52 | 52.00 | 624 |
| 2 | 49–101 | 52.00 | 624 |
| 3 | 98–150 | 52.00 | 624 |

All three outputs were 1080×1920. Validation sampled zero black frames and
found no audio stream even though the secondary asset contained audio.
The preview confirmed secondary video above the primary video. The fixture was
silent. Existing media tests exercise burned captions separately. Changing
overlap from 3 seconds to 0 changed the selection identity and produced a
50-second first part with a byte-identical transcript checkpoint. Re-uploading
the secondary asset reused its library identity.

## Persistent catalog additions

| Asset | Author | Category | Imported media |
| --- | --- | --- | --- |
| [Animation of a tesseract](https://commons.wikimedia.org/wiki/File:Animation_of_a_tesseract.webm) | JasonHise | Abstract Motion | 28.83 s, 720×406, silent |
| [Stop-Motion Animation (Basic)](https://commons.wikimedia.org/wiki/File:Stop-Motion_Animation_(Basic).webm) | Teja Silaparasetty | Satisfying | 20.50 s, 720×540, silent |

Both files declare CC0 1.0 on the per-file Commons page and passed the provider
eligibility check. The tesseract animation's underlying GIF has a public-domain
declaration by the same author. Commons also records a license review for the
stop-motion original on Flickr. Both normalized assets passed media validation
and generated preview frames. Asset bytes live in the existing
`youtube-temka_controller_data` volume. The six original starter loops and
pre-existing operator assets were preserved.
