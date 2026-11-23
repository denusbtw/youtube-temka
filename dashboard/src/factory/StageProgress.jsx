import React from "react";
const stages = [
  ["source", "Source Analysis"],
  ["extraction", "Download / Extraction"],
  ["transcription", "Transcription"],
  ["language", "Language Detection"],
  ["highlights", "AI Highlight Selection"],
  ["retrieval", "Video Retrieval"],
  ["captions_audio", "Audio / Captions"],
  ["rendering", "Rendering"],
  ["validation", "Validation"],
  ["metadata", "Metadata Generation"],
  ["editorial", "Editorial Validation"],
  ["drive", "Drive Upload / Verification"],
  ["approval", "Approval / Publishing"],
];
export default function StageProgress({ job }) {
  const entries = Object.fromEntries((job.stages || []).map((s) => [s.key, s]));
  const terminal = ["READY", "FAILED", "BLOCKED", "CANCELLED"].includes(
    job.state,
  );
  const elapsed = Math.max(
    0,
    Math.floor(
      ((terminal ? job.seen || job.created : Date.now() / 1000) - job.created) /
        60,
    ),
  );
  if (!job.stages?.length && terminal)
    return (
      <small className="stage-panel">
        Архівне завдання · докладні етапи не записувалися цією версією
      </small>
    );
  return (
    <details
      className="stage-panel"
      open={!["READY", "CANCELLED"].includes(job.state)}
    >
      <summary>
        Етапи · {elapsed} хв · {job.result.outputs?.length || 0} готових кліпів
        · {job.worker || "черга"}
      </summary>
      <ol>
        {stages.map(([key, label]) => {
          const s = entries[key];
          const emptySelection = key === "highlights" && terminal &&
            Array.isArray(job.result.candidates) && job.result.candidates.length === 0;
          const pct =
            !emptySelection && s?.current != null && s.total > 0
              ? Math.min(100, Math.floor((s.current / s.total) * 100))
              : null;
          const status =
            emptySelection ? "blocked" : key === "approval" && job.state === "READY"
              ? job.result.outputs?.every((o) => o.approved)
                ? "approved"
                : "awaiting review"
              : s?.status || "pending";
          return (
            <li key={key} className={"stage-" + status}>
              <span>{label}</span>
              <small>
                {status}
                {pct !== null ? ` · ${pct}%` : ""}
                {!emptySelection && s?.unit && s.current != null
                  ? ` · ${Number(s.current).toFixed(0)} / ${s.total ?? "…"} ${s.unit}`
                  : ""}
              </small>
              {status === "running" &&
                (pct !== null ? (
                  <progress value={s.current} max={s.total} />
                ) : (
                  <progress />
                ))}
              {emptySelection ? <small>Кліпів не вибрано. Перегляньте діагностику відбору.</small>
                : s?.message && <small>{s.message}</small>}
            </li>
          );
        })}
      </ol>
      {job.result.failure && <p>
        <code>{job.result.failure.code}</code> · {job.result.failure.stage}
        {job.result.failed_source_id && <> · {job.result.failed_source_id}</>}
      </p>}
      {job.result.retry_at && job.state==='QUEUED' && <p role="status">
        Повторна спроба після {new Date(job.result.retry_at*1000).toLocaleTimeString()}.
      </p>}
      {job.error && <p className="error">{job.error}</p>}
    </details>
  );
}
