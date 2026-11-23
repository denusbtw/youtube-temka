# Acceptance matrix
| Scope | Status | Evidence or blocker |
| --- | --- | --- |
| Baseline and temporal harness | TESTED | Synthetic schema 1, IoU/recall/duplicates/invalid intervals. Real editorial annotations unavailable. |
| Highlight ranking | TESTED | Shared evidence factors, fewer overlapping clips, shot-bounded visual candidates and bounded local inspection. No semantic verification. |
| Real gaming automatic selection | BLOCKED | Available local excerpt produced 0/3, because qualifying shot regions were shorter than 35 s. Human-labeled full-source comparison is unavailable. |
| Grounded metadata | TESTED | Confirmed GTA and unconfirmed edition tests. Contextual draft is always marked for review. |
| Shared geometry and persistent hooks | VERIFIED | 45-second authorized GTA manual rerender. 1248/672 px. Four representative frames inspected, no opaque gaps, visible hook. |
| Library and job safety | TESTED | Empty category disclosure, safe job filters/archive/delete tests. Physical asset deletion remains incomplete. |
| Public publication | TESTED | Compose disables automatic mode. Existing exact-output human approval guards remain. |
| Editorial quality | BLOCKED | Human editorial review and annotated event corpus needed. |
