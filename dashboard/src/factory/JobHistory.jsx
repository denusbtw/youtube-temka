import React,{useEffect,useState} from 'react';

const terminal = ['READY','FAILED','BLOCKED','CANCELLED'];

export default function JobHistory({jobs,render,api,post,act,refresh}) {
  const [scope,setScope]=useState('recent'),[search,setSearch]=useState(''),[state,setState]=useState('');
  const [after,setAfter]=useState(''),[sort,setSort]=useState('newest');
  const [page,setPage]=useState(0),[selected,setSelected]=useState([]),[preview,setPreview]=useState(null);
  const [data,setData]=useState({items:[],has_more:false}),[error,setError]=useState('');
  const query=new URLSearchParams({scope,search,state,sort,offset:String(page*10),
    after:String(after?new Date(after+'T00:00:00').getTime()/1000:0)});
  useEffect(()=>{
    const request=new AbortController();
    api('/job-history?'+query,{signal:request.signal}).then(value=>{setData(value);setError('');})
      .catch(error=>{if(!request.signal.aborted)setError(error.message);});
    return ()=>request.abort();
  },[query.toString(),jobs]);
  const rows=data.items;
  const move=status=>act(async()=>{
    await post('/jobs/visibility',{ids:selected,status});setSelected([]);setPreview(null);await refresh();
  });
  const reset=setter=>e=>{setter(e.target.value);setPage(0);setSelected([]);setPreview(null);};
  return <section>
    <div className="grid">
      <label>Пошук<input value={search} onChange={reset(setSearch)}/></label>
      <label>Історія<select value={scope} onChange={reset(setScope)}><option value="recent">Останні</option><option value="archived">Архів</option><option value="trash">Кошик</option><option value="all">Усі</option></select></label>
      <label>Стан<select value={state} onChange={reset(setState)}><option value="">Усі стани</option><option value="active">Активні</option>{terminal.map(s=><option key={s}>{s}</option>)}</select></label>
      <label>Створено після<input type="date" value={after} onChange={reset(setAfter)}/></label>
      <label>Сортування<select value={sort} onChange={reset(setSort)}><option value="newest">Новіші</option><option value="oldest">Старіші</option></select></label>
    </div>
    {selected.length>0&&<div>
      <button onClick={()=>move('archived')}>Архівувати вибрані</button>
      <button onClick={()=>move('trash')}>Перемістити в кошик</button>
      <button onClick={()=>move('recent')}>Відновити</button>
      {selected.length===1&&<button onClick={()=>act(async()=>setPreview(await api('/jobs/'+selected[0]+'/deletion-preview')))}>Перевірити залежності</button>}
    </div>}
    {preview&&<p>Файли: {preview.outputs} · Drive: {preview.remote_artifacts} · Публікації: {preview.publications} · Залежні монтажі: {preview.dependent_edits} · {preview.permanent_delete_available?'Доступне видалення порожнього запису.':'Запис захищено.'}</p>}
    {preview?.permanent_delete_available&&<button onClick={()=>act(async()=>{
      const confirmation=window.prompt('Введіть ID для остаточного видалення запису: '+preview.job);
      if(confirmation===null)return;
      await post('/jobs/'+preview.job+'/delete-record',{confirmation});
      setSelected([]);setPreview(null);await refresh();
    })}>Остаточно видалити запис</button>}
    {rows.map(job=><div key={job.id}>
      <label className="check"><input type="checkbox" disabled={!terminal.includes(job.state)} checked={selected.includes(job.id)}
        onChange={e=>{setPreview(null);setSelected(e.target.checked?[...selected,job.id]:selected.filter(id=>id!==job.id));}}/>Вибрати завдання</label>
      {render(job)}
    </div>)}
    {!rows.length&&<p>Завдань не знайдено.</p>}
    {error&&<p className="error">{error}</p>}
    {(data.has_more||page>0)&&<div><button disabled={!page} onClick={()=>setPage(page-1)}>Назад</button> {page+1} <button disabled={!data.has_more} onClick={()=>setPage(page+1)}>Далі</button></div>}
  </section>;
}
