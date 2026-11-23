import React, { useEffect, useRef, useState } from "react";
import CompositionControls from './CompositionControls.jsx';

const initial = {
  source: "",
  rights_confirmed: false,
  workflow: "highlights",
  workflow_version: 2,
  story_mode: "chronological",
  destination: "youtube_shorts",
  mode: "shorts",
  preset: "auto",
  resolution: 720,
  start: 0,
  end: 1800,
  count: 1,
  minimum: 35,
  maximum: 60,
  language: null,
  captions: true,
  audio_mode: "original",
  caption_style: "pop",
  bitrate: 8000000,
  background: null,
  save_drive: true,
  intervals: [],
};
const presets = {
  podcast: {
    source_category: "podcast",
    preset: "auto",
    audio_mode: "original",
    captions: true,
  },
  stream: {
    source_category: "stream",
    preset: "auto",
    audio_mode: "original",
    captions: true,
  },
  gameplay: {
    source_category: "gameplay",
    preset: "gameplay",
    audio_mode: "original",
  },
  silent: { source_category: "visual", audio_mode: "silent", captions: false },
  narrated: { audio_mode: "voiceover", tts_voice: "af_heart", language: "en" },
};
function loadDefaults(settings) {
  try {
    return {
      ...initial,
      save_drive: !!settings?.drive_configured,
      ...JSON.parse(localStorage.getItem("factory.preferences") || "{}"),
      source: "",
      workflow: "highlights",
      workflow_version: 2,
      story_mode: "chronological",
      mode: "shorts",
      destination: "youtube_shorts",
      maximum: 60,
      rights_confirmed: false,
      start: 0,
      end: 1800,
      audio_mode: "original",
      tts_text: "",
      hook: "",
      source_attribution: "",
      publish_automatic: false,
    };
  } catch {
    return { ...initial, save_drive: !!settings?.drive_configured };
  }
}

export default function CreatePage({
  api,
  post,
  act,
  settings,
  backgrounds,
  onCreated,
}) {
  const [form, setForm] = useState(() => loadDefaults(settings)),
    [metadata, setMetadata] = useState(null),
    [probing, setProbing] = useState(false),
    [probeError, setProbeError] = useState(""),
    [submitting, setSubmitting] = useState(false),
    [intervals, setIntervals] = useState(""),
    [music, setMusic] = useState([]),
    [additional, setAdditional] = useState([]),
    [plan, setPlan] = useState(null);
  const submission = useRef(false);
  const compilation = form.workflow === 'compilation';
  const singleLong = form.workflow === 'single_long';
  const story = form.workflow === 'story';
  const set = (key, value) => {
    setPlan(null);
    setForm((v) => {
      if (key === "mode" && value !== v.mode && !["story","compilation"].includes(v.workflow)) {
        const wide = value === "longs";
        return {...v, mode:value, workflow:wide?"single_long":"highlights",
          destination:wide?"video":"youtube_shorts", series_mode:"independent", plan_only:false,
          review_before_render:false, maximum:wide?180:60};
      }
      if (key === "review_before_render" && value) return {...v, [key]:value, plan_only:false};
      if (key === "plan_only" && value) return {...v, [key]:value, review_before_render:false, publish_automatic:false};
      return {...v, [key]:value};
    });
  };
  const changeWorkflow = (workflow) => {
    setPlan(null);
    setForm(v => ({
      ...v,
      workflow,
      series_mode: 'independent',
      plan_only: false,
      mode:['compilation','single_long'].includes(workflow)?'longs':'shorts',
      destination:['compilation','single_long'].includes(workflow)?'video':'youtube_shorts',
      preset:'auto',
      count:workflow==='compilation'?20:5,
      maximum:['compilation','single_long'].includes(workflow)?180:60,
      end:workflow==='highlights'?1800:null,
      target_duration:workflow==='compilation'?900:null,
      audio_mode:workflow==='compilation'&&v.audio_mode==='voiceover'?'original':v.audio_mode,
      preserve_fps:workflow==='compilation'?false:v.preserve_fps,
      review_before_render:['story','single_long'].includes(workflow)?false:v.review_before_render,
      silence_removal:['compilation','story'].includes(workflow)?false:v.silence_removal,
    }));
  };
  const setSources = (update) => {
    setPlan(null);
    setAdditional(update);
  };
  const updateSource = (index, patch) => setSources(items =>
    items.map((item,i) => i === index ? {...item,...patch} : item));
  useEffect(() => {
    api("/assets/music")
      .then(setMusic)
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (!form.source) {
      setMetadata(null);
      setProbeError("");
      setProbing(false);
      return;
    }
    const abort = new AbortController();
    setMetadata(null);
    setProbing(true);
    setProbeError("");
    const timer = setTimeout(async () => {
      try {
        const result = await api("/probe", {
          method: "POST",
          body: JSON.stringify({ source: form.source }),
          signal: abort.signal,
        });
        if (!abort.signal.aborted) setMetadata(result);
      } catch (e) {
        if (!abort.signal.aborted) setProbeError(e.message);
      } finally {
        if (!abort.signal.aborted) setProbing(false);
      }
    }, 600);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [form.source]);
  const background = form.background ||
    null;
  const selectedResolution =
    metadata?.platform === "local"
      ? Math.min(
          Math.max(...metadata.resolutions),
          form.resolution === "best" ? Infinity : form.resolution,
        )
      : metadata?.resolutions
          .filter((r) => form.resolution === "best" || r <= form.resolution)
          .sort((a, b) => b - a)[0];
  const upload = async (file, kind, index = null) => {
    if (!form.rights_confirmed)
      throw new Error(
        "Спершу підтвердьте права на джерело та вибрані матеріали.",
      );
    const body = new FormData();
    body.append("file", file);
    body.append("rights_confirmed", "true");
    body.append("background", String(kind === "background"));
    body.append("music", String(kind === "music"));
    const result = await api("/uploads", { method: "POST", body });
    if (kind === "source") {
      if (index === null) set("source", result.source);
      else setSources(items=>items.map((item,i)=>i===index?{...item,source:result.source}:item));
    }
    if (kind === "background") set("background", result.name);
    if (kind === "music") {
      setMusic((v) => [...v, result.name]);
      set("music_asset", result.name);
    }
  };
  const buildRequest = () => {
    const ranges = !story && !compilation && intervals.trim()
      ? intervals.trim().split("\n").map(line => {
          const pair = line.split(",").map(Number);
          if (pair.length !== 2 || pair.some(n => !Number.isFinite(n))) {
            throw new Error("Інтервал має вигляд start,end у секундах.");
          }
          return pair;
        })
      : [];
    const source = form.source.trim();
    const sources = compilation
      ? [{source,start:form.start,end:form.end}, ...additional.map(item=>({...item,source:item.source.trim()}))]
      : [];
    return {...form,source,sources,intervals:ranges, workflow:singleLong?null:form.workflow, story_mode:story?form.story_mode:null,
      background:compilation||form.background_mode==='none'?null:background,
      background_mode:compilation?'none':form.background_mode};
  };
  const checkPlan = async (request) => {
    const result = await post("/preflight", request);
    setPlan(result);
    return result;
  };
  const generate = async (e) => {
    e.preventDefault();
    if (submission.current) return;
    submission.current = true;
    setSubmitting(true);
    try {
      await act(async () => {
        const request = buildRequest();
        if ((story || compilation) && !plan?.ready && !(await checkPlan(request)).ready) return;
        const {
          source,
          rights_confirmed,
          intervals: unused,
          sources,
          workflow,
          start,
          end,
          tts_text,
          hook,
          source_attribution,
          confirmed_subject,
          clip_observation,
          attribution_required,
          publish_automatic,
          ...preferences
        } = request;
        try {
          localStorage.setItem(
            "factory.preferences",
            JSON.stringify(preferences),
          );
        } catch {}
        const job = await post("/jobs", request);
        await onCreated(job);
      });
    } finally {
      submission.current = false;
      setSubmitting(false);
    }
  };
  return (
    <form onSubmit={generate} className="create-simple">
      <section className="source-entry">
        <label>
          Режим монтажу
          <select value={form.workflow} onChange={e=>changeWorkflow(e.target.value)}>
            <option value="highlights">Smart Highlights</option>
            <option value="story">Connect a Story</option>
            <option value="compilation">Longs Compilation</option>
            <option value="single_long">Longs з одного джерела</option>
          </select>
        </label>
        <p className="muted">{story
          ? "Виберіть хронологічний поділ або відбір частин однієї підтвердженої події."
          : compilation ? "Зберіть один горизонтальний ролик із кількох джерел."
          : "Знайдіть найкращі моменти у відео за мовленням, звуком і рухом."}</p>
        <label>
          {compilation ? "Джерело 1 · URL" : "URL відео"}
          <input
            className="source-url"
            type="text"
            value={form.source}
            onChange={(e) => set("source", e.target.value)}
            placeholder="https://www.youtube.com/watch?v=…"
            required
            autoComplete="off"
          />
        </label>
          <label>
            Або додайте локальне відео
            <input
              type="file"
              accept=".mp4,.mkv,.mov"
              onChange={(e) =>
                e.target.files[0] &&
                act(() => upload(e.target.files[0], "source"))
              }
            />
          </label>
        {compilation && <section aria-label="Додаткові джерела">
          {additional.map((item,index)=><div className="grid" key={index}>
            <label>Джерело {index+2} · URL
              <input value={item.source} required onChange={e=>updateSource(index,{source:e.target.value})}
                placeholder="https://www.twitch.tv/videos/…" />
            </label>
            <label>Локальний файл
              <input type="file" accept=".mp4,.mkv,.mov"
                onChange={e=>e.target.files[0]&&act(()=>upload(e.target.files[0],"source",index))}/>
            </label>
            <label>Початок, сек<input type="number" min="0" step=".1" value={item.start}
              onChange={e=>updateSource(index,{start:+e.target.value})}/></label>
            <label>Кінець, сек<input type="number" min=".1" step=".1" value={item.end??''}
              onChange={e=>updateSource(index,{end:e.target.value?+e.target.value:null})}/></label>
            <div>
              <button type="button" disabled={index===0} onClick={()=>setSources(items=>{
                const ordered=[...items];
                [ordered[index-1],ordered[index]]=[ordered[index],ordered[index-1]];
                return ordered;
              })}>Вище</button>
              <button type="button" onClick={()=>setSources(items=>items.filter((_,i)=>i!==index))}>Прибрати</button>
            </div>
          </div>)}
          <button type="button" disabled={additional.length>=11} onClick={()=>setSources(items=>[...items,{source:"",start:0,end:null}])}>Додати джерело</button>
        </section>}
        {probing && (
          <div role="status" className="source-skeleton">
            Перевіряю назву, тривалість і доступні формати…
          </div>
        )}
        {probeError && (
          <p role="alert" className="error">
            {probeError}
          </p>
        )}
        {metadata && (
          <div className="source-summary">
            {metadata.thumbnail && (
              <img src={metadata.thumbnail} alt="Джерело відео" />
            )}
            <div>
              <b>{metadata.title}</b>
              <p>
                {metadata.platform} · {(metadata.duration / 60).toFixed(1)} хв ·{" "}
                {selectedResolution || "недоступно"}p
              </p>
              <small>
                Діапазон: {form.start}–
                {Math.min(form.end || metadata.duration, metadata.duration)} сек
                ·{" "}
                {metadata.size
                  ? `${(metadata.size / 1024 ** 2).toFixed(1)} MB джерела`
                  : "Розмір передачі невідомий"}
                {metadata.duration > 3600
                  ? " · багаточанкова обробка доступна"
                  : ""}
              </small>
            </div>
          </div>
        )}
        {!compilation && !singleLong && <label>Формат вертикального відео
          <select value={form.destination} onChange={e=>set("destination",e.target.value)}>
            <option value="youtube_shorts">YouTube Shorts</option>
            <option value="video">Звичайне вертикальне відео</option>
          </select>
        </label>}
        {story ? <div className="grid">
          <label>Тип історії<select value={form.story_mode} onChange={e=>set("story_mode",e.target.value)}>
            <option value="chronological">Хронологічний поділ</option>
            <option value="ai_verified">Історія з перевіркою ШІ</option>
          </select></label>
          <label>Кількість частин
            <input type="number" min="1" max={form.story_mode==="ai_verified"?20:100} value={form.story_count??''}
              placeholder="Автоматично" onChange={e=>set('story_count',e.target.value?+e.target.value:null)}/>
          </label>
          <label>Перекриття, сек
            <input type="number" min="0" max="5" step=".1" value={form.series_overlap??3}
              onChange={e=>set('series_overlap',+e.target.value)}/>
          </label>
        </div> : <div className="grid">
          <label>{compilation?'Максимум фрагментів':'Кількість кліпів'}
            <input type="number" min="1" max="20" value={form.count} onChange={e=>set('count',+e.target.value)}/>
          </label>
          {(compilation || singleLong) && <label>Цільова тривалість, хв
            <input type="number" min=".2" max="60" step=".1"
              list="compilation-durations" value={form.target_duration==null?'':form.target_duration/60}
              onChange={e=>set('target_duration',e.target.value?+e.target.value*60:null)}/>
            <datalist id="compilation-durations"><option value="15"/><option value="20"/><option value="30"/></datalist>
          </label>}
        </div>}
        <CompositionControls form={form} set={set} backgrounds={backgrounds} channels={settings?.channels||[]} profiles={settings?.youtube_profiles||[]} thumbnail={metadata?.thumbnail} video={form.source.startsWith('upload:')?'/api/source-preview/'+form.source.slice(7):null}/>
        {form.workflow==="highlights" && <label className="check">
          <input type="checkbox" checked={!!form.plan_only} onChange={e=>set("plan_only",e.target.checked)}/>
          Зберегти лише план відбору
        </label>}
        {!story && !singleLong && <label className="check">
          <input type="checkbox" checked={!!form.review_before_render}
            onChange={e=>set('review_before_render',e.target.checked)}/>
          Переглянути план перед рендером
        </label>}
        <label className="check">
          <input
            type="checkbox"
            checked={form.rights_confirmed}
            onChange={(e) => set("rights_confirmed", e.target.checked)}
          />
          Я маю права на джерело, фон і вибрану музику
        </label>
        {(story || compilation) && <button type="button" disabled={submitting || !metadata ||
          !form.rights_confirmed || (compilation && (!additional.length || additional.some(s=>!s.source.trim())))}
          onClick={()=>act(()=>checkPlan(buildRequest()))}>Перевірити план</button>}
        {plan && <ul aria-label="Результат перевірки">
          {plan.sources.map((item,index)=><li key={item.source_id}>
            {item.error ? 'Джерело '+(index+1)+' — '+item.error :
              item.title+' · '+Math.round(item.selected_seconds)+' сек'+(item.story_count?' · '+item.story_count+' частин':'')}
          </li>)}
        </ul>}
        <button
          className="primary generate"
          disabled={
            submitting ||
            probing ||
            !metadata ||
            !form.rights_confirmed ||
            (compilation && (!additional.length || additional.some(s=>!s.source.trim()))) ||
            (!compilation && form.preset === "gameplay" && !background && form.background_mode !== "random")
          }
        >
          {submitting ? "Додаю до черги…" : "Почати монтаж"}
        </button>
        {!compilation && form.preset === "gameplay" && !background && form.background_mode !== "random" && (
          <p className="warning">
            Додайте авторизований MP4-фон в Advanced Settings.
          </p>
        )}
        <p className="muted">
          Якість джерела {form.resolution} · {form.minimum}–
          {form.maximum} сек · мова {form.language || "auto"} · звук{" "}
          {form.audio_mode === "original" ? "оригінальний" : form.audio_mode} ·{" "}
          {form.captions ? "субтитри ввімкнено" : "без субтитрів"} ·{" "}
          {form.save_drive ? "Google Drive" : "локальний експорт"} ·{" "}
          {form.publish_automatic
            ? "дозволена автоматична черга"
            : "review first"}
        </p>
      </section>
      <details className="advanced">
        <summary>Advanced Settings</summary>
        <section>
          <label>
            Збережений пресет
            <select
              defaultValue=""
              onChange={(e) => {
                setPlan(null);
                setForm(v => ({...v,...presets[e.target.value], workflow:v.workflow, mode:v.mode}));
              }}
            >
              <option value="">Власні налаштування</option>
              <option value="podcast">Podcast Shorts</option>
              <option value="stream">Stream Highlights</option>
              {!compilation && <option value="gameplay">Split Screen</option>}
              <option value="silent">Silent Satisfying Clips</option>
              {!compilation && <option value="narrated">Optional Narrated Video</option>}
            </select>
          </label>
          <div className="grid">
            <label>
              Якість
              <select
                value={form.resolution}
                onChange={(e) =>
                  set(
                    "resolution",
                    e.target.value === "best" ? "best" : +e.target.value,
                  )
                }
              >
                {[480, 720, 1080].map((n) => (
                  <option key={n} value={n}>
                    {n}p
                  </option>
                ))}
                <option value="best">Best</option>
              </select>
            </label>
            <label>
              Мова
              <select
                value={form.language || ""}
                onChange={(e) => set("language", e.target.value || null)}
              >
                <option value="">Auto</option>
                {["en", "uk", "ru", "es", "de", "fr"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label>
              Початок, сек
              <input
                type="number"
                min="0"
                value={form.start}
                onChange={(e) => set("start", +e.target.value)}
              />
            </label>
            <label>
              Кінець, сек
              <input
                type="number"
                min="1"
                value={form.end ?? ""}
                placeholder="Увесь VOD"
                onChange={(e) =>
                  set("end", e.target.value ? +e.target.value : null)
                }
              />
            </label>
            <button
              type="button"
              onClick={() => {
                set("start", 0);
                set("end", null);
              }}
            >
              Аналізувати увесь VOD
            </button>

            <label>
              Мінімальна тривалість
              <input
                type="number"
                min="5"
                value={form.minimum}
                onChange={(e) => set("minimum", +e.target.value)}
              />
            </label>
            <label>
              Максимальна тривалість
              <input
                type="number"
                min="5"
                max={form.mode === "shorts" ? 180 : 600}
                value={form.maximum}
                onChange={(e) => set("maximum", +e.target.value)}
              />
            </label>
          </div>
          {!story && !compilation && <label>
            Ручні інтервали, за потреби
            <textarea
              value={intervals}
              onChange={(e) => setIntervals(e.target.value)}
              placeholder="0,40&#10;120,180"
            />
            <small>
              Порожнє поле вмикає автоматичний відбір. Відлік від початку джерела.
            </small>
          </label>}
          <label>
            Audio mode
            <select
              value={form.audio_mode}
              onChange={(e) => set("audio_mode", e.target.value)}
            >
              <option value="original">Original Audio · default</option>
              <option value="music">Original Audio + Music</option>
              <option value="silent">Silent Video</option>
              {!compilation && <option value="voiceover">AI Voice-over · optional</option>}
            </select>
          </label>
          {form.audio_mode === "music" && (
            <>
              <label>
                Ліцензована музика
                <select
                  value={form.music_asset || ""}
                  onChange={(e) => set("music_asset", e.target.value || null)}
                >
                  <option value="">Без додаткової музики</option>
                  {music.map((n) => (
                    <option key={n}>{n}</option>
                  ))}
                </select>
              </label>
              <label>
                Додати музику
                <input
                  type="file"
                  accept=".wav,.mp3"
                  onChange={(e) =>
                    e.target.files[0] &&
                    act(() => upload(e.target.files[0], "music"))
                  }
                />
              </label>
            </>
          )}
          {form.audio_mode === "voiceover" && (
            <>
              <p className="warning">
                Лише явне озвучення. Kokoro тут підтримує EN/ES; оригінальне
                аудіо зберігається в матеріалах для редагування.
              </p>
              <label>
                Голос
                <select
                  value={form.tts_voice || "af_heart"}
                  onChange={(e) => set("tts_voice", e.target.value)}
                >
                  {[
                    "af_heart",
                    "af_bella",
                    "am_michael",
                    "ef_dora",
                    "em_alex",
                  ].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <label>
                Текст озвучення
                <textarea
                  maxLength="5000"
                  value={form.tts_text || ""}
                  onChange={(e) => set("tts_text", e.target.value)}
                />
              </label>
              <label>
                Темп
                <input
                  type="number"
                  min=".85"
                  max="1.15"
                  step=".05"
                  value={form.tts_speed || 1}
                  onChange={(e) => set("tts_speed", +e.target.value)}
                />
              </label>
            </>
          )}

          <label>
            Додати MP4-фон
            <input
              type="file"
              accept=".mp4"
              onChange={(e) =>
                e.target.files[0] &&
                act(() => upload(e.target.files[0], "background"))
              }
            />
          </label>

          <div className="grid">
            <label>
              Стиль субтитрів
              <select
                value={form.caption_style}
                onChange={(e) => set("caption_style", e.target.value)}
              >
                {["pop", "karaoke", "clean"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label>
              Бітрейт, Mbps
              <input
                type="number"
                min="1"
                max="50"
                value={form.bitrate / 1e6}
                onChange={(e) => set("bitrate", +e.target.value * 1e6)}
              />
            </label>
            <label>
              Split ratio
              <input
                type="range"
                min=".2"
                max=".8"
                step=".05"
                value={form.split_ratio || 0.65}
                onChange={(e) => set("split_ratio", +e.target.value)}
              />
            </label>
            <label>
              Longs resolution
              <select
                value={form.output_height || 720}
                onChange={(e) => set("output_height", +e.target.value)}
              >
                <option value="720">720p</option>
                <option value="1080">1080p</option>
              </select>
            </label>
          </div>

          <label>
            Авторство / ліцензія
            <input
              maxLength="1000"
              value={form.source_attribution || ""}
              onChange={(e) => set("source_attribution", e.target.value)}
            />
          </label>
          {[
            ["preserve_fps", "Зберегти FPS"],
            ["silence_removal", "Видаляти довгі паузи"],
            ["save_drive", "Зберігати в Google Drive"],
          ].filter(([key])=>!(story&&key==='silence_removal')&&!(compilation&&key==='preserve_fps')).map(([key, label]) => (
            <label key={key} className="check">
              <input
                type="checkbox"
                checked={!!form[key]}
                onChange={(e) => set(key, e.target.checked)}
              />
              {label}
            </label>
          ))}

          <label className="check">
            <input
              type="checkbox"
              checked={!!form.publish_automatic}
              disabled={!form.publish_channel || !form.save_drive || !!form.youtube_channel_key}
              onChange={(e) => set("publish_automatic", e.target.checked)}
            />
            Дозволена автоматична публікація
          </label>
        </section>
      </details>
    </form>
  );
}
