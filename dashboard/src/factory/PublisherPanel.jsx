import React, { useEffect, useState } from "react";
import MetadataEditor from "./MetadataEditor.jsx";
import PublicationEditor from "./PublicationEditor.jsx";
export default function PublisherPanel({
  outputs,
  initialSelection = "",
  settings,
  act,
  api,
  post,
  refreshJobs,
}) {
  const [data, setData] = useState({ channels: [], publications: [], profiles: [] }),
    [channel, setChannel] = useState(""),
    [selected, setSelected] = useState(initialSelection),
    [privacy, setPrivacy] = useState("private"),
    [when, setWhen] = useState(""),
    [policy, setPolicy] = useState({
      daily_limit: 3,
      public_allowed: false,
      automatic: false,
      interval_minutes: 120,
      preferences: {
        review_first: true,
        auto_approve: false,
        timezone: "Europe/Kyiv",
      },
    });
  const refresh = async () => setData(await api("/publisher"));
  useEffect(() => {
    let stopped=false,t;
    const poll=async()=>{try{await refresh();}catch{}if(!stopped)t=setTimeout(poll,3000);};
    poll();
    return () => {stopped=true;clearTimeout(t);};
  }, []);
  useEffect(() => {
    if (!channel && data.channels.length === 1)
      chooseChannel(data.channels[0].id);
  }, [data.channels.length]);
  const chooseChannel = (id) => {
    setChannel(id);
    const c = data.channels.find((c) => c.id === id);
    if (c?.managed_profile) {setPrivacy("private");setWhen("");}
    if (c)
      setPolicy({
        ...c,
        preferences: {
          review_first: true,
          auto_approve: false,
          timezone: "Europe/Kyiv",
          ...c.preferences,
        },
      });
  };
  const available = outputs.filter((o) => o.job.result.cloud_verified);
  const current = available.find((o) => `${o.job.id}:${o.index}` === selected);
  const destination = data.channels.find(c => c.id === channel);
  const managed = destination?.managed_profile;
  const compatible = (!current?.job.request.publish_channel || current.job.request.publish_channel === channel) &&
    (!current?.job.request.youtube_channel_key || current.job.request.youtube_channel_key === managed?.channel_key);
  const destinationReady = !!destination && (!managed || (managed.enabled && managed.status === "CONNECTED"));
  const change = (key, value) => setPolicy((p) => ({ ...p, [key]: value }));
  const preference = (key, value) =>
    setPolicy((p) => ({
      ...p,
      preferences: { ...p.preferences, [key]: value },
    }));
  const timezone = policy.preferences.timezone || "Europe/Kyiv";
  const calendarTime = (value) => {
    try {
      return new Date(value).toLocaleString("uk-UA", { timeZone: timezone });
    } catch {
      return (
        new Date(value).toLocaleString("uk-UA", { timeZone: "UTC" }) + " UTC"
      );
    }
  };
  return (
    <>
      <section>
        <h2>YouTube · підключення каналу</h2>
        {!data.channels.length && (
          <p className="warning">
            YOUTUBE_AUTH_REQUIRED · Відео й метадані залишаються на Drive. Для
            публікації потрібна окрема авторизація YouTube.
          </p>
        )}
        <button
          onClick={() =>
            act(async () => {
              const r = await post("/publisher/discover");
              await refresh();
              if (r.errors.length)
                throw new Error(r.errors.map((e) => e.error).join("; "));
            })
          }
        >
          Перевірити OAuth і знайти канали
        </button>
        {(data.profiles || []).map(profile => (
          <article key={profile.channel_key}>
            <b>{profile.title}</b>
            <p>{profile.enabled ? profile.status : "DISCONNECTED"} · {profile.channel_id || "ID очікує OAuth"} · {profile.profile} · {profile.mode === "shorts" ? "Shorts" : "Long-form"}</p>
            {profile.verified_at && <p>Остання перевірка: {calendarTime(profile.verified_at * 1000)}</p>}
            {profile.error && <p className="warning">{profile.error}</p>}
            <details>
              <summary>{profile.channel_id ? "Перепідключити OAuth" : "Підключити OAuth"}</summary>
              <p>Запустіть команду на ПК з Desktop OAuth client JSON. У Google виберіть саме цей YouTube-канал.</p>
              <code>python -m factory.youtube_oauth --client PATH_TO_CLIENT.json --channel-key {profile.channel_key}{profile.channel_id ? " --reconnect" : ""}</code>
              <p>Для окремого дозволу Analytics додайте <code>--analytics</code>. Після згоди перевірте підключення.</p>
            </details>
            <button onClick={() => act(async () => {
              const result=await post(`/youtube/profiles/${profile.channel_key}/verify`);
              await refresh();
              if(result.error)throw new Error(result.error);
            })}>Перевірити профіль</button>
            {profile.channel_id && <button onClick={() => act(async () => {
              await post(`/youtube/profiles/${profile.channel_key}/state`,{enabled:!profile.enabled});
              await refresh();
            })}>{profile.enabled ? "Відключити в Factory" : "Увімкнути в Factory"}</button>}
          </article>
        ))}
        <details>
          <summary>Одноразове підключення</summary>
          <p>
            На ПК A використайте завантажений Desktop OAuth client JSON. У
            відкритому Google-вікні власник сам вибирає канал і підтверджує
            доступ.
          </p>
          <code>
            python -m factory.youtube_oauth --client PATH_TO_CLIENT.json
            --output secrets/youtube/main.json --open-browser
          </code>
          <p>Analytics підключається окремим read-only дозволом у Settings.</p>
        </details>
        <label>
          Авторизований канал
          <select
            value={channel}
            onChange={(e) => chooseChannel(e.target.value)}
          >
            <option value="">Виберіть канал</option>
            {data.channels.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
        </label>
        {channel && (
          <details>
            <summary>Правила каналу</summary>
            <div className="grid"><label>Назва профілю<input value={policy.preferences.profile_name||''} onChange={e=>preference('profile_name',e.target.value)}/></label><label>Регіон<input value={policy.preferences.target_region||''} onChange={e=>preference('target_region',e.target.value)}/></label><label>Мова каналу<select value={policy.preferences.metadata_language||''} onChange={e=>preference('metadata_language',e.target.value||null)}><option value="">Мова джерела</option>{['en','es','pt-BR','de','ru','uk'].map(l=><option key={l}>{l}</option>)}</select></label><label>Макет за замовчуванням<select value={policy.preferences.editing_preset||'auto'} onChange={e=>preference('editing_preset',e.target.value)}>{['auto','smart','blur','gameplay','crop'].map(p=><option key={p}>{p}</option>)}</select></label></div>
            <div className="grid">
              <label>
                Ліміт на добу
                <input
                  type="number"
                  min="1"
                  max="20"
                  value={policy.daily_limit}
                  onChange={(e) => change("daily_limit", +e.target.value)}
                />
              </label>
              <label>
                Мінімальний інтервал, хв
                <input
                  type="number"
                  min="30"
                  max="1440"
                  value={policy.interval_minutes}
                  onChange={(e) => change("interval_minutes", +e.target.value)}
                />
              </label>
              <label>
                Часовий пояс
                <input
                  value={timezone}
                  onChange={(e) => preference("timezone", e.target.value)}
                />
              </label>
              <label>
                Мова метаданих
                <input
                  value={policy.preferences.metadata_language || ""}
                  placeholder="Мова джерела"
                  onChange={(e) =>
                    preference("metadata_language", e.target.value || null)
                  }
                />
              </label>
            </div>
            <label>
              Постійний підпис каналу
              <textarea
                maxLength="500"
                value={policy.preferences.description_footer || ""}
                onChange={(e) =>
                  preference("description_footer", e.target.value)
                }
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={!!policy.public_allowed}
                disabled={!!managed}
                onChange={(e) => change("public_allowed", e.target.checked)}
              />
              Я дозволяю публічні й заплановані публікації на цьому каналі
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={!!policy.automatic}
                disabled={!!managed}
                onChange={(e) => change("automatic", e.target.checked)}
              />
              Увімкнути автоматичну чергу для явно вибраних завдань
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={!!policy.preferences.auto_approve}
                onChange={(e) => {
                  preference("auto_approve", e.target.checked);
                  preference("review_first", !e.target.checked);
                }}
              />
              Я дозволяю автоматично схвалювати лише результати, які пройшли всі
              редакторські перевірки
            </label>
            <p className="muted">
              Слабкі, heuristic, дубльовані та непідтверджені результати
              потребують ручного перегляду незалежно від цього дозволу.
            </p>
            <button
              onClick={() =>
                act(async () => {
                  await post(`/publisher/channels/${channel}/policy`, policy);
                  await refresh();
                })
              }
            >
              Зберегти правила
            </button>
          </details>
        )}
      </section>
      <section>
        <h2>Готові публікаційні чернетки</h2>
        <label>
          Відео
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">Виберіть відео</option>
            {available.map((o) => (
              <option
                key={`${o.job.id}:${o.index}`}
                value={`${o.job.id}:${o.index}`}
              >
                {o.title} · {o.approved ? "схвалено" : "потрібен перегляд"}
              </option>
            ))}
          </select>
        </label>
        {current && (
          <>
            <MetadataEditor
              job={current.job}
              output={current}
              index={current.index}
              api={api}
              post={post}
              act={act}
              onSaved={refreshJobs}
            />
            {!current.approved && (
              <p className="warning">
                Перевірте чернетку та схваліть результат у Editor.
              </p>
            )}
            {current.thumbnail && (
              <img
                style={{ maxWidth: 240 }}
                alt="Мініатюра відео"
                src={`/api/jobs/${current.job.id}/files/${current.thumbnail.name}`}
              />
            )}
            <div className="grid">
              <label>
                Видимість
                <select
                  value={privacy}
                  onChange={(e) => setPrivacy(e.target.value)}
                >
                  <option value="private">Приватне</option>
                  <option value="unlisted">За посиланням</option>
                  <option value="public" disabled={!policy.public_allowed}>
                    Публічне
                  </option>
                </select>
              </label>
              <label>
                Публічний реліз · {timezone}
                <input
                  type="datetime-local"
                  disabled={!policy.public_allowed}
                  value={when}
                  onChange={(e) => setWhen(e.target.value)}
                />
              </label>
            </div>
            {!policy.public_allowed&&<p className="warning">Публічні публікації та розклад вимкнені для цього каналу. У «Правилах каналу» можна окремо дозволити ручний Public; автоматична публікація залишається незалежною.</p>}
            <button
              className="primary"
              disabled={
                !destinationReady || !compatible || !current.approved || !current.publishing_metadata
              }
              onClick={() =>
                act(async () => {
                  const target=data.channels.find(c=>c.id===channel);
                  if(!window.confirm(`Підтвердити публікацію на каналі ${target?.title||channel} (${channel})? Режим: ${when?'scheduled':privacy}.`))return;
                  await post("/publisher/queue", {
                    job: current.job.id,
                    output: current.index,
                    channel,
                    privacy,
                    publish_at_local: when || null,
                  });
                  await refresh();
                })
              }
            >
              Поставити в чергу з готовими метаданими
            </button>
          </>
        )}
      </section>
      <section>
        <h2>Календар публікацій · {timezone}</h2>
        <p>
          Ліміт {policy.daily_limit}/добу · інтервал {policy.interval_minutes}{" "}
          хв · майбутні слоти призначаються через native scheduling YouTube.
        </p>
        <ol>
          {data.publications
            .filter((p) => !channel || p.channel === channel)
            .filter((p) => p.metadata.status.publishAt)
            .sort((a, b) =>
              a.metadata.status.publishAt.localeCompare(
                b.metadata.status.publishAt,
              ),
            )
            .map((p) => (
              <li key={p.id}>
                {calendarTime(p.metadata.status.publishAt)} ·{" "}
                {p.metadata.snippet.title} · {p.state}
              </li>
            ))}
        </ol>
      </section>
      <h2>Черга та історія</h2>
      {data.publications.map((p) => (
        <section key={p.id}>
          <b>{p.metadata.snippet.title}</b>
          <p>
            {data.channels.find(c=>c.id===p.channel)?.title || p.channel} · {p.metadata.format || "—"} · {p.state} ·{" "}
            {p.metadata.status.publishAt || p.metadata.status.privacyStatus}
          </p>
          {p.video_id && <p>YouTube ID: {p.video_id}</p>}
          {p.total > 0 && (
            <>
              <progress value={p.current} max={p.total} />
              <small>
                {p.current.toLocaleString()} / {p.total.toLocaleString()} bytes
              </small>
            </>
          )}
          {p.error && <p className="error">{p.error}</p>}
          {p.thumbnail_status && (
            <p>
              Обкладинка: {p.thumbnail_status.state}
              {p.thumbnail_status.error ? " · " + p.thumbnail_status.error : ""}
            </p>
          )}
          {p.editable && (
            <PublicationEditor
              publication={p}
              api={api}
              act={act}
              refresh={refresh}
            />
          )}
          <details>
            <summary>Метадані YouTube</summary>
            <p>{p.metadata.snippet.description}</p>
            <p>Теги: {p.metadata.snippet.tags?.join(", ") || "немає"}</p>
            <p>
              Категорія: {p.metadata.snippet.categoryId} ·{" "}
              {p.metadata.snippet.defaultLanguage || "und"} · аудіо{" "}
              {p.metadata.snippet.defaultAudioLanguage || "без мовлення"}
            </p>
          </details>
          {p.video_id && (
            <a
              target="_blank"
              rel="noreferrer"
              href={`https://www.youtube.com/watch?v=${p.video_id}`}
            >
              Переглянути на YouTube
            </a>
          )}
          {p.state === "FAILED" && (
            <button
              onClick={() =>
                act(async () => {
                  await post(`/publisher/${p.id}/retry`);
                  await refresh();
                })
              }
            >
              Продовжити збережену сесію
            </button>
          )}
          {p.state === "UNCERTAIN" && (
            <>
              <p className="warning">
                Перевірте YouTube Studio; ідентифікатор відео збережено.
              </p>
              {p.video_id && (
                <button
                  onClick={() =>
                    act(async () => {
                      await post(`/publisher/${p.id}/reconcile`);
                      await refresh();
                    })
                  }
                >
                  Перевірити вже завантажене відео
                </button>
              )}
            </>
          )}
        </section>
      ))}
    </>
  );
}
