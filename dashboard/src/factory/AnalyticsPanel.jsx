import React, { useEffect, useState } from "react";
const sum = (rows, key) =>
  rows.some((row) => row[key] != null)
    ? rows.reduce((n, row) => n + (row[key] ?? 0), 0)
    : null;
const number = (value) =>
  value == null
    ? "Недоступно"
    : new Intl.NumberFormat("uk-UA", { maximumFractionDigits: 1 }).format(
        value,
      );
export default function AnalyticsPanel({ api, post, act }) {
  const [data, setData] = useState({ state: "LOADING", channels: [] }),
    [channel, setChannel] = useState("");
  const refresh = async () => setData(await api("/analytics"));
  useEffect(() => {
    refresh().catch(() => {});
    const timer = setInterval(() => refresh().catch(() => {}), 10000);
    return () => clearInterval(timer);
  }, []);
  const current =
    data.channels.find((c) => c.connection.channel === channel) ||
    data.channels[0];
  const videos = current?.videos || [];
  const daily = current?.report?.daily || [];
  const totalViews = current?.report?.totals?.views ?? sum(daily, "views");
  const watch = current?.report?.totals?.estimatedMinutesWatched ?? sum(daily, "estimatedMinutesWatched");
  const weighted = videos.filter(
    (v) => (v.metrics.views || 0) > 0 && v.metrics.averageViewDuration != null,
  );
  const average = weighted.length
    ? weighted.reduce(
        (n, v) => n + v.metrics.views * v.metrics.averageViewDuration,
        0,
      ) / weighted.reduce((n, v) => n + v.metrics.views, 0)
    : null;
  const groups = {};
  for (const v of videos) {
    const key = v.language || "und";
    (groups[key] ??= []).push(v);
  }
  const max = Math.max(1, ...daily.map((d) => d.views || 0));
  const points = daily
    .map(
      (d, i) =>
        `${daily.length > 1 ? (i / (daily.length - 1)) * 600 : 300},${130 - ((d.views || 0) / max) * 120}`,
    )
    .join(" ");
  return (
    <section className="analytics-panel">
      <h2>YouTube Analytics</h2>
      {data.state === "ANALYTICS_AUTH_REQUIRED" && (
        <p className="warning">
          ANALYTICS_AUTH_REQUIRED · Потрібен окремий read-only дозвіл. Це не
          впливає на генерацію, Drive або публікаційні чернетки.
        </p>
      )}
      <button
        onClick={() =>
          act(async () => {
            const r = await post("/analytics/discover");
            await refresh();
            if (r.errors.length)
              throw new Error(r.errors.map((e) => e.error).join("; "));
          })
        }
      >
        Перевірити Analytics OAuth
      </button>
      <details>
        <summary>Одноразова read-only авторизація</summary>
        <p>На ПК A, використовуючи свій Google Desktop client:</p>
        <code>
          python -m factory.youtube_oauth --client PATH_TO_CLIENT.json --output
          secrets/analytics/main.json --analytics --open-browser
        </code>
        <p>Цей окремий файл не змінює чинний токен завантаження YouTube.</p>
      </details>
      {current && (
        <>
          {current.report?.experiments?.map(experiment=><details key={experiment.parent}>
            <summary>Варіанти монтажу · {experiment.start} – {experiment.end}</summary>
            <p>Дані за спільний період після публікації обох варіантів.</p>
            <table><thead><tr><th>Варіант</th><th>Перегляди</th><th>Залучені перегляди</th><th>Сер. час, с</th><th>Сер. %</th><th>Вподобання</th><th>Коментарі</th></tr></thead>
            <tbody>{experiment.variants.map(item=><tr key={item.video_id}><td>{item.variant} · {item.title}</td>
              {['views','engagedViews','averageViewDuration','averageViewPercentage','likes','comments'].map(metric=><td key={metric}>{number(item.metrics[metric])}</td>)}
            </tr>)}</tbody></table>
          </details>)}
          <label>
            Канал аналітики
            <select
              value={current.connection.channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              {data.channels.map((c) => (
                <option key={c.connection.channel} value={c.connection.channel}>
                  {c.connection.title}
                </option>
              ))}
            </select>
          </label>
          <p>
            {current.connection.state} · останнє успішне оновлення:{" "}
            {current.connection.last_sync
              ? new Date(current.connection.last_sync * 1000).toLocaleString()
              : "ще не було"}
          </p>
          {current.connection.error && (
            <p className="error">{current.connection.error}</p>
          )}
          <button
            disabled={current.connection.state === "SYNCING"}
            onClick={() =>
              act(async () => {
                await post(`/analytics/${current.connection.channel}/sync`);
                await refresh();
              })
            }
          >
            Оновити реальну статистику
          </button>
          {current.connection.state === "SYNCING" &&
            (current.connection.total > 0 ? (
              <progress
                value={current.connection.progress}
                max={current.connection.total}
              />
            ) : (
              <progress />
            ))}
          {current.report && (
            <>
              <p>
                Період: {current.report.period.start} —{" "}
                {current.report.period.end}. Дані YouTube можуть надходити із
                затримкою. Вік кешу: {Math.floor(current.data_age_seconds / 60)}{" "}
                хв.
              </p>
              {current.report.credential_strategy === "verified_publishing_readonly" && (
                <p>Немонетарний звіт · чинний дозвіл YouTube на читання.</p>
              )}
              <div className="stats">
                <div>
                  <small>Перегляди каналу за період</small>
                  <b>{number(totalViews)}</b>
                </div>
                <div>
                  <small>Watch time · хв</small>
                  <b>{number(watch)}</b>
                </div>
                <div>
                  <small>Середня тривалість перегляду відео · с</small>
                  <b>{number(average)}</b>
                </div>
              </div>
              {daily.length > 0 ? (
                <figure>
                  <figcaption>Перегляди за днями · фактичні дані</figcaption>
                  <svg
                    viewBox="0 0 600 150"
                    role="img"
                    aria-label="Перегляди YouTube за днями"
                  >
                    <polyline
                      points={points}
                      fill="none"
                      stroke="#a5da8d"
                      strokeWidth="3"
                    />
                  </svg>
                  <small>
                    {daily[0].day} — {daily.at(-1).day}
                  </small>
                </figure>
              ) : (
                <p>Для цього періоду немає доступних денних рядків.</p>
              )}
              <h3>Найкращі та останні відео</h3>
              <div className="outputs">
                {[...videos]
                  .sort(
                    (a, b) => (b.metrics.views ?? -1) - (a.metrics.views ?? -1),
                  )
                  .slice(0, 12)
                  .map((v) => (
                    <article key={v.video_id}>
                      <a
                        target="_blank"
                        rel="noreferrer"
                        href={`https://www.youtube.com/watch?v=${v.video_id}`}
                      >
                        {v.title}
                      </a>
                      <p>
                        {v.video_type} · {v.language} · {number(v.duration)} с ·{" "}
                        {v.published_at
                          ? new Date(v.published_at).toLocaleString()
                          : ""}
                      </p>
                      <p>
                        За період: {number(v.metrics.views)} переглядів ·{" "}
                        {number(v.metrics.engagedViews)} engaged views ·{" "}
                        {number(v.metrics.averageViewDuration)} с ·{" "}
                        {number(v.metrics.averageViewPercentage)}% переглянуто
                      </p>
                      <p>
                        Likes {number(v.metrics.likes)} · коментарі{" "}
                        {number(v.metrics.comments)} · підписники +
                        {number(v.metrics.subscribersGained)} / −
                        {number(v.metrics.subscribersLost)}
                      </p>
                      <small>
                        Lifetime views:{" "}
                        {number(v.lifetime_statistics.viewCount)}. Метрики за
                        період і lifetime не змішуються.
                      </small>
                    </article>
                  ))}
              </div>
              <h3>За мовою</h3>
              {Object.entries(groups).map(([lang, rows]) => (
                <p key={lang}>
                  {lang}: {rows.length} відео ·{" "}
                  {number(
                    sum(
                      rows.map((v) => v.metrics),
                      "views",
                    ),
                  )}{" "}
                  доступних переглядів за період
                </p>
              ))}
              <h3>Спостереження та рекомендації</h3>
              <p>
                {current.report.recommendations.status === "INSUFFICIENT_DATA"
                  ? `Недостатньо даних: ${current.report.recommendations.eligible_videos} придатних відео; мінімум 20, по 5 на порівнювану групу.`
                  : "Порівняння доступної вибірки; це не причинний висновок і не гарантія охоплення."}
              </p>
              {current.report.recommendations.items.map((r) => (
                <p key={r.dimension}>
                  {r.dimension}: {r.observed_group} · n={r.videos} · медіана
                  переглянуто {number(r.median_average_percentage_viewed)}%
                </p>
              ))}
              <p className="muted">
                Правила каналу автоматично не змінюються. Impressions, CTR і
                retention curve не показуються без відповідного доступного
                звіту.
              </p>
              {current.report.diagnostics.map((d) => (
                <p key={d} className="warning">
                  {d}
                </p>
              ))}
            </>
          )}
        </>
      )}
    </section>
  );
}
