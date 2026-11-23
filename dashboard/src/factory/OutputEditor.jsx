import React,{useEffect,useState} from 'react';
import CompositionControls from './CompositionControls.jsx';
const ctas={en:'Subscribe for more videos.',es:'Suscríbete para ver más videos.','pt-BR':'Inscreva-se para mais vídeos.',de:'Abonniere für weitere Videos.',ru:'Подпишитесь, чтобы увидеть новые видео.',uk:'Підпишіться, щоб побачити нові відео.'};
export default function OutputEditor({job,output,index,act,api,post,onCreated,backgrounds=[],settings}) {
  const [form,setForm]=useState({...job.request,hook:output.hook||job.request.hook||''});
  const [ranges,setRanges]=useState([[0,output.edit_duration||output.duration]]),[logos,setLogos]=useState([]),[hooks,setHooks]=useState(output.hook_candidates);
  const set=(key,value)=>setForm(v=>({...v,[key]:value}));
  useEffect(()=>{api('/library').then(v=>setLogos(v.filter(a=>a.kind==='logos'))).catch(()=>{});
    if(job.request.mode==='longs')api(`/jobs/${job.id}/files/timeline.json`).then(v=>setRanges(v.segments.map(s=>[(s.edit_start_ms??s.output_start_ms)/1000,(s.edit_end_ms??s.output_end_ms)/1000]))).catch(()=>{});
  },[job.id]);
  if(!output.edit_source)return <small>Для цього результату немає збереженого вихідного уривка. Повторно додайте оригінальне джерело для монтажу.</small>;
  return <details><summary>Редагувати відео · нова версія</summary><p>Попереднє відео та його схвалення зберігаються. Нова версія потребуватиме окремого перегляду.</p>
    <CompositionControls form={form} set={set} backgrounds={backgrounds} channels={settings?.channels||[]} profiles={settings?.youtube_profiles||[]} routingLocked={!!job.request.youtube_channel_key} thumbnail={output.thumbnail?`/api/jobs/${job.id}/files/${output.thumbnail.name}`:null}/>
    <div className="grid"><label>Лого<select value={form.logo_asset||''} onChange={e=>set('logo_asset',e.target.value||null)}><option value="">Без лого</option>{logos.map(a=><option key={a.id}>{a.name}</option>)}</select></label><label>CTA<input value={form.cta_text||''} maxLength={120} onChange={e=>set('cta_text',e.target.value)}/></label></div>
    <button onClick={()=>set('cta_text',ctas[form.metadata_language||job.result.language]||ctas.en)}>Додати CTA мовою каналу</button>
    <h3>Таймлайн · секунди від початку збереженого джерела</h3>
    {ranges.map(([a,b],i)=><div className="grid" key={i}><label>Початок<input type="number" step=".01" min="0" value={a} onChange={e=>setRanges(v=>v.map((r,j)=>j===i?[+e.target.value,r[1]]:r))}/></label><label>Кінець<input type="number" step=".01" max={output.edit_duration||output.duration} value={b} onChange={e=>setRanges(v=>v.map((r,j)=>j===i?[r[0],+e.target.value]:r))}/></label><span><button disabled={!i} onClick={()=>setRanges(v=>{const copy=[...v];[copy[i-1],copy[i]]=[copy[i],copy[i-1]];return copy;})}>↑ Перемістити</button><button disabled={ranges.length===1} onClick={()=>setRanges(v=>v.filter((_,j)=>j!==i))}>Прибрати</button></span></div>)}
    {hooks&&<details><summary>AI-кандидати заголовка ({hooks.method})</summary>{hooks.candidates.map((h,i)=><p key={i}><button onClick={()=>{set('hook',h.text);set('text_mode','static_hook');}}>{h.text}</button> · {h.ranking_score} · {h.reason}</p>)}<p>Рейтинг евристичний; перевірте відповідність уривку.</p></details>}
    <button onClick={()=>act(async()=>setHooks(await post(`/jobs/${job.id}/outputs/${index}/hooks`)))}>Згенерувати 5–10 заголовків</button>
    <button disabled={ranges.some(([a,b])=>b<=a||a<0||b>(output.edit_duration||output.duration))} onClick={()=>act(async()=>onCreated(await post('/jobs',{...form,source:`edit:${job.id}:${index}`,workflow:null,sources:[],review_before_render:false,start:0,end:output.edit_duration||output.duration,intervals:ranges,count:1,series_mode:'independent',publish_automatic:false,audio_mode:'original',tts_text:''}))) }>Зберегти й рендерити нову версію</button>
  </details>;
}
