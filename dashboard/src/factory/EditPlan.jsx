import React, { useState } from "react";

export default function EditPlan({ job, api, post, act, onSaved }) {
  const plan = job.result.edit_plan;
  const [segments,setSegments] = useState(plan.segments);
  const [target,setTarget] = useState(plan.target_duration);
  const [preview,setPreview] = useState(null);
  const [saving,setSaving] = useState(false);
  const path = "/jobs/"+job.id;
  const length = segments.reduce((sum,s)=>sum+s.end_seconds-s.start_seconds,0);
  const change = (id,key,value) => setSegments(items=>items.map(s=>s.id===id?{...s,[key]:value}:s));
  const save = (render) => act(async () => {
    setSaving(true);
    try {
      const body = {revision:plan.revision,target_duration:target,segments:segments.map(
        ({id,start_seconds,end_seconds,transition})=>({id,start_seconds,end_seconds,transition}))};
      if (render) await post(path+"/render",body);
      else await api(path+"/edit-plan",{method:"PATCH",body:JSON.stringify(body)});
      await onSaved();
    } finally {
      setSaving(false);
    }
  });
  return <section>
    <h2>План монтажу</h2>
    <p>{length.toFixed(1)} сек · {segments.length} фрагментів</p>
    {target!==null && <label>Цільова тривалість, сек
      <input type="number" min="10" max="3600" value={target} onChange={e=>setTarget(+e.target.value)}/>
    </label>}
    {segments.map((s,index) => {
      const source = plan.sources[s.source_id];
      const start = s.start_seconds+s.source_offset, end = s.end_seconds+s.source_offset;
      const local = source.source.startsWith("upload:");
      let sourceLink;
      if (!local && source.source.startsWith("https://")) {
        const url = new URL(source.source);
        url.searchParams.set("t",Math.floor(start)+"s");
        sourceLink = url.toString();
      }
      return <article className="candidate" key={s.id}>
        <b>{s.title}</b>
        <p>{source.title} · {start.toFixed(1)}–{end.toFixed(1)} сек у джерелі</p>
        <small>{s.score==null?"":'Оцінка '+s.score.toFixed(1)+' · '}{s.reason}</small>
        <div className="grid">
          <label>Початок від початку аналізу, сек
            <input type="number" min="0" step=".1" value={s.start_seconds}
              onChange={e=>change(s.id,"start_seconds",+e.target.value)}/>
          </label>
          <label>Кінець від початку аналізу, сек
            <input type="number" min="0" step=".1" value={s.end_seconds}
              onChange={e=>change(s.id,"end_seconds",+e.target.value)}/>
          </label>
          <label>Перехід
            <select value={s.transition} onChange={e=>change(s.id,"transition",e.target.value)}>
              <option value="cut">Прямий монтаж</option><option value="fade">Затемнення</option>
            </select>
          </label>
        </div>
        <div className="actions">
          <button type="button" disabled={!index} onClick={()=>setSegments(items=>{
            const ordered=[...items];
            [ordered[index-1],ordered[index]]=[ordered[index],ordered[index-1]];
            return ordered;
          })}>Вище</button>
          <button type="button" disabled={segments.length===1}
            onClick={()=>setSegments(items=>items.filter(item=>item.id!==s.id))}>Прибрати</button>
          {local && <button type="button" onClick={()=>setPreview(preview===s.id?null:s.id)}>Переглянути</button>}
          {sourceLink && <a href={sourceLink} target="_blank" rel="noreferrer">Відкрити джерело</a>}
        </div>
        {local && preview===s.id && <video controls preload="metadata"
          src={"/api/source-preview/"+encodeURIComponent(source.source.slice(7))+"#t="+Math.max(0,start)+","+end}/>}
      </article>;
    })}
    <div className="actions">
      <button type="button" disabled={saving} onClick={()=>save(false)}>Зберегти план</button>
      <button type="button" className="primary" disabled={saving} onClick={()=>save(true)}>Схвалити й рендерити</button>
    </div>
  </section>;
}
