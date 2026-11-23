import React,{useEffect,useState} from 'react';

const FEEDBACK_CHOICES = [
  ['approved', 'Approved'],
  ['needs_trim', 'Needs trim'],
  ['needs_different_layout', 'Needs different layout'],
  ['incorrect_event', 'Incorrect event'],
  ['unrelated_scenes', 'Unrelated scenes'],
  ['missing_payoff', 'Missing payoff'],
  ['weak_hook', 'Weak hook'],
  ['reject', 'Reject'],
];

const WORKFLOW_LABELS = {
  ANALYSIS_IN_PROGRESS: 'Analysis In Progress',
  RENDERING_IN_PROGRESS: 'Rendering In Progress',
  CANDIDATES_READY: 'Candidates Ready',
  NEEDS_EDITORIAL_REVIEW: 'Needs Editorial Review',
  APPROVED: 'Approved',
  RENDER_VALIDATION_FAILED: 'Render Validation Failed',
  READY_FOR_PUBLISHING_REVIEW: 'Ready for Publishing Review',
};

export default function SelectionReview({job,post,act,onCreated}) {
  const sourceStart=job.request.start||0;
  const [mode,setMode]=useState(job.request.series_mode||'independent');
  const [count,setCount]=useState(job.request.count);
  const [minimum,setMinimum]=useState(job.request.minimum);
  const [maximum,setMaximum]=useState(Math.min(180,job.request.maximum));
  const [overlap,setOverlap]=useState(job.request.series_overlap??3);
  const [evidence,setEvidence]=useState(null);
  const [feedbackSaved,setFeedbackSaved]=useState({});
  const rows=(job.result.candidates||[]).map((clip,id)=>({...clip,id,
    start:+(clip.start_seconds+sourceStart).toFixed(3),end:+(clip.end_seconds+sourceStart).toFixed(3)}));
  const [edited,setEdited]=useState(null);
  const parts=edited??rows;
  const available=['READY','FAILED','BLOCKED'].includes(job.state);
  const source=job.request.source;

  useEffect(()=>{
    if(!available) return;
    let active=true;
    fetch(`/api/jobs/${job.id}/evidence`).then(r=>r.ok?r.json():null).then(data=>{
      if(active&&data) setEvidence(data);
    }).catch(()=>{});
    return ()=>{active=false;};
  },[job.id,available]);

  const update=(index,key,value)=>setEdited(parts.map((part,i)=>i===index?{...part,[key]:value}:part));
  const submit=(data)=>act(async()=>onCreated(await post('/jobs/'+job.id+'/replan',data)));
  const move=(index,direction)=>{
    const next=[...parts];
    [next[index],next[index+direction]]=[next[index+direction],next[index]];
    setEdited(next);
  };
  const splitCandidate=(index)=>{
    const item=parts[index];
    const mid=+((item.start+item.end)/2).toFixed(3);
    if(mid<=item.start+1||item.end<=mid+1) return;
    const first={...item,id:`${item.id}-a`,title:`${item.title} (A)`,end:mid};
    const second={...item,id:`${item.id}-b`,title:`${item.title} (B)`,start:mid};
    const next=[...parts];
    next.splice(index,1,first,second);
    setEdited(next);
  };
  const mergeWithNext=(index)=>{
    if(index>=parts.length-1) return;
    const a=parts[index],b=parts[index+1];
    const merged={...a,id:`${a.id}-m`,start:Math.min(a.start,b.start),end:Math.max(a.end,b.end),
      title:`${a.title} + ${b.title}`.slice(0,140),reason:'Operator merged adjacent candidates for manual review.'};
    const next=[...parts];
    next.splice(index,2,merged);
    setEdited(next);
  };
  const recordFeedback=(index,part,decision)=>{
    act(async()=>{
      await post(`/jobs/${job.id}/feedback`,{
        candidate_index:index,
        decision,
        rejection_reasons:decision==='approved'?[]:[decision],
        original_start:part.start_seconds!=null?+(part.start_seconds+sourceStart).toFixed(3):part.start,
        original_end:part.end_seconds!=null?+(part.end_seconds+sourceStart).toFixed(3):part.end,
        corrected_start:part.start,
        corrected_end:part.end,
        original_score:part.score??null,
        selected_layout:job.request.preset||'auto',
      });
      setFeedbackSaved(prev=>({...prev,[index]:decision}));
    });
  };

  const totalDuration=Math.max(1,(evidence?.duration)||(job.result.processing_end??sourceStart)-sourceStart||1);
  const formatBreaks=evidence?.format_breaks||job.result.format_breaks||[];
  const semanticEvents=evidence?.events||job.result.events_summary||[];
  const diag=job.result.analysis_diagnostics||{};
  const vlmStats=diag.vlm_stats||{};
  const decisions=diag.decisions||[];
  const discoveredCount=job.result.discovered_candidates??diag.windows??semanticEvents.length;
  const verifiedCount=job.result.vlm_verified_count??(decisions.filter(d=>d.verified).length||diag.vlm_verified||0);
  const workflowState=job.editorial_workflow_state||evidence?.editorial_workflow_state||(job.result.planning_only?'CANDIDATES_READY':'NEEDS_EDITORIAL_REVIEW');

  return <details open={job.request.plan_only||job.state==='BLOCKED'||undefined}>
    <summary>План відбору Shorts · {WORKFLOW_LABELS[workflowState]||workflowState}</summary>
    <p className="muted">
      Статус редакторської перевірки: <b>{WORKFLOW_LABELS[workflowState]||workflowState}</b>.
      {' '}Кандидатів проаналізовано: <b>{discoveredCount}</b> · перевірено VLM: <b>{verifiedCount}</b> · вибрано: <b>{parts.length}</b>
      {vlmStats.model?` · модель ${vlmStats.model} (викликів: ${vlmStats.calls_made||0}, кеш: ${vlmStats.cache_hits||0})`:''}.
      {' '}Оцінки — це редакторські евристики на основі доказів, а не прогноз віральності.
      Редагування та субтитри не надають авторських прав чи гарантії монетизації для стороннього контенту.
    </p>
    {decisions.length>0&&<details className="stage-panel">
      <summary>Діагностика ШІ-верифікації сюжету ({decisions.length} перевірених вікон)</summary>
      <ul>
        {decisions.map((d,i)=><li key={i}>
          <b>{(sourceStart+(d.start||0)).toFixed(1)}–{(sourceStart+(d.end||0)).toFixed(1)} с</b>
          {' '}({d.event_id||'event'}) · вердикт: <code>{d.verdict||(d.verified?'VERIFIED':'INSUFFICIENT_EVIDENCE')}</code>
          {d.confidence!=null?` · впевненість: ${(d.confidence*100).toFixed(0)}% (невизначеність: ${((1-d.confidence)*100).toFixed(0)}%)`:''}
          {d.adaptive_passes?` · проходів: ${d.adaptive_passes} (${d.evidence_timestamps?.length||4} кадрів)`:''}
          {' '}— {d.reason}
        </li>)}
      </ul>
    </details>}

    {(formatBreaks.length>0||semanticEvents.length>0)&&<div className="editorial-timeline" aria-label="Візуальний таймлайн подій і форматних розривів">
      <div className="timeline-legend">
        <span><i className="swatch event"/>Події ({semanticEvents.length})</span>
        <span><i className="swatch break"/>Форматні розриви / картки ({formatBreaks.length})</span>
        <span><i className="swatch selected"/>Вибрані кліпи ({parts.length})</span>
      </div>
      <div className="timeline-track">
        {semanticEvents.map((ev,i)=><span key={`ev-${i}`} className="tl-seg tl-event"
          title={`Event ${ev.event_id||i+1}: ${ev.start}–${ev.end}s (${ev.duration}s)`}
          style={{left:`${(ev.start/totalDuration)*100}%`,width:`${Math.max(0.5,((ev.end-ev.start)/totalDuration)*100)}%`}}/>)}
        {formatBreaks.map((br,i)=><span key={`br-${i}`} className="tl-seg tl-break"
          title={`Format break (${br.kind}): ${br.start}–${br.end}s`}
          style={{left:`${(br.start/totalDuration)*100}%`,width:`${Math.max(0.8,((br.end-br.start)/totalDuration)*100)}%`}}/>)}
        {parts.map((p,i)=><span key={`sel-${i}`} className="tl-seg tl-selected"
          title={`Clip #${i+1}: ${p.start}–${p.end}s`}
          style={{left:`${((p.start-sourceStart)/totalDuration)*100}%`,width:`${Math.max(1,((p.end-p.start)/totalDuration)*100)}%`}}/>)}
      </div>
    </div>}

    {parts.map((part,index)=><article key={part.id} className="candidate-card">
      <b>{part.part ? `Частина ${part.part}/${part.parts} · ` : ''}#{index+1} · {part.title}</b>
      <p>{part.reason}</p>
      <div className="evidence-badges">
        <span>{(part.end-part.start).toFixed(2)} с</span>
        {part.shot_count!=null&&<span>{part.shot_count} шот(ів)</span>}
        {part.story_verdict&&<span>Вердикт: {part.story_verdict}</span>}
        {part.confidence!=null&&<span>Впевненість: {(part.confidence*100).toFixed(0)}%</span>}
        {part.semantic_verification&&<span>{part.semantic_verification}</span>}
        {part.semantic_decision&&<span>Рішення: {part.semantic_decision}</span>}
        {part.face_corners?.length>0&&<span>Facecam: {part.face_corners.join(', ')}</span>}
      </div>
      {(part.setup_time!=null||part.climax_time!=null||part.payoff_time!=null)&&<p className="muted">
        {part.setup_time!=null?`Setup: ${part.setup_time}s `:''}
        {part.climax_time!=null?`· Climax: ${part.climax_time}s `:''}
        {part.payoff_time!=null?`· Payoff: ${part.payoff_time}s`:''}
      </p>}
      {[part.setup,part.development,part.payoff,part.connection].filter(Boolean).map((text,i)=><p key={i}>{text}</p>)}
      {part.evidence?.length>0&&<ul className="evidence-list">{part.evidence.map((e,i)=><li key={i}>{e}</li>)}</ul>}
      {part.penalties?.length>0&&<p className="warning">Потенційні дефекти: {part.penalties.join('; ')}</p>}
      <div className="grid">
        <label>Початок, с<input type="number" min={sourceStart} step=".001" value={part.start} onChange={e=>update(index,'start',+e.target.value)}/></label>
        <label>Кінець, с<input type="number" step=".001" value={part.end} onChange={e=>update(index,'end',+e.target.value)}/></label>
      </div>
      {source.startsWith('upload:')&&<video controls preload="none" src={`/api/source-preview/${encodeURIComponent(source.slice(7))}#t=${part.start},${part.end}`}/>}
      {source.startsWith('https://www.youtube.com/watch?')&&<a target="_blank" rel="noreferrer" href={`${source}&t=${Math.floor(part.start)}s`}>Переглянути в джерелі</a>}
      <div className="candidate-actions">
        <button type="button" disabled={!index} onClick={()=>move(index,-1)}>Вище</button>
        <button type="button" disabled={index===parts.length-1} onClick={()=>move(index,1)}>Нижче</button>
        <button type="button" onClick={()=>splitCandidate(index)}>Розділити</button>
        <button type="button" disabled={index>=parts.length-1} onClick={()=>mergeWithNext(index)}>Об’єднати з наступним</button>
        <button type="button" onClick={()=>setEdited(parts.filter((_,i)=>i!==index))}>Відхилити</button>
        {available&&<select aria-label="Редакторський відгук" value={feedbackSaved[index]||''} onChange={e=>e.target.value&&recordFeedback(index,part,e.target.value)}>
          <option value="">Записати редакторську оцінку…</option>
          {FEEDBACK_CHOICES.map(([val,label])=><option key={val} value={val}>{label}</option>)}
        </select>}
        {job.result.planning_only&&!part.context_required&&part.payoff&&<button type="button" onClick={()=>submit({experiment:part.id})}>Рендерувати варіанти монтажу</button>}
      </div>
      {feedbackSaved[index]&&<small>Збережено редакторський відгук: {feedbackSaved[index]}</small>}
    </article>)}
    {available&&job.result.review_alternatives?.map((item,index)=>{
      const id=`alternative-${index}`;
      return <article key={id} className="candidate-card alt-card">
        <b>{item.title} · альтернатива на перевірку {item.story_verdict?`(${item.story_verdict})`:''}</b><p>{item.reason}</p>
        <p>{(sourceStart+item.start_seconds).toFixed(2)}–{(sourceStart+item.end_seconds).toFixed(2)} с ({(item.end_seconds-item.start_seconds).toFixed(1)} с)</p>
        {item.duration_conflict&&<small>Коротша цілісна подія ({item.duration_conflict.event_duration}s &lt; {item.duration_conflict.requested_minimum}s мінімум) збережена без доповнення стороннім відео.</small>}
        <button type="button" disabled={parts.length>=20||parts.some(part=>part.id===id)} onClick={()=>setEdited([...parts,{
          ...item,id,start:sourceStart+item.start_seconds,end:sourceStart+item.end_seconds
        }])}>Додати для ручного монтажу</button>
      </article>;
    })}
    {available&&<>
      <button type="button" onClick={()=>setEdited(null)}>Відновити план</button>
      <button type="button" disabled={parts.length>=20||(job.result.processing_end??sourceStart)<=sourceStart} onClick={()=>setEdited([...parts,{
        id:`manual-${Date.now()}`,title:'Власний інтервал',start:sourceStart,
        end:Math.min(sourceStart+45,job.result.processing_end)
      }])}>Додати власний інтервал</button>
      <button type="button" className="primary" disabled={!parts.length||parts.some(p=>p.end<=p.start)} onClick={()=>submit({intervals:parts.map(p=>[p.start,p.end]),maximum})}>Підтвердити та рендерити</button>
      <div className="grid">
        <label>Режим<select value={mode} onChange={e=>setMode(e.target.value)}><option value="independent">Independent Highlights</option><option value="connected">Connected Story</option><option value="sequential">Sequential Source Split</option></select></label>
        <label>Кількість<input type="number" min="1" max="20" value={count} onChange={e=>setCount(+e.target.value)}/></label>
        <label>Мінімум, с<input type="number" min="5" max="180" value={minimum} onChange={e=>setMinimum(+e.target.value)}/></label>
        <label>Максимум, с<input type="number" min="5" max="180" value={maximum} onChange={e=>setMaximum(+e.target.value)}/></label>
        <label>Перекриття, с<input type="number" min="0" max="5" step=".1" value={overlap} onChange={e=>setOverlap(+e.target.value)}/></label>
      </div>
      <button type="button" onClick={()=>submit({mode,count,minimum,maximum,overlap})}>Перебудувати відбір</button>
    </>}
  </details>;
}
