import React,{useEffect,useState} from 'react';

export default function StoragePanel({api,post,act}) {
  const [data,setData]=useState(null),[result,setResult]=useState(null);
  const refresh=()=>api('/storage/retention').then(setData);
  useEffect(()=>{refresh().catch(()=>{});},[]);
  if(!data)return <p>Завантаження налаштувань зберігання…</p>;
  const set=(key,value)=>setData(d=>({...d,settings:{...d.settings,[key]:value}}));
  return <section><h2>Google Drive · зберігання пакетів</h2><p>Один пакет містить відео, метадані, субтитри та прев’ю. Видалення можливе лише після перевіреної публічної публікації та пільгового періоду.</p>
    <label className="check"><input type="checkbox" checked={data.settings.enabled} onChange={e=>set('enabled',e.target.checked)}/>Видаляти опубліковані пакети з Drive</label>
    <div className="grid"><label>Ліміт пакетів<input type="number" min="1" max="10000" value={data.settings.maximum_packages} onChange={e=>set('maximum_packages',+e.target.value)}/></label><label>Пільговий період, днів<input type="number" min="1" max="365" value={data.settings.grace_days} onChange={e=>set('grace_days',+e.target.value)}/></label></div>
    <button onClick={()=>act(async()=>{await post('/storage/retention/settings',data.settings);await refresh();})}>Зберегти політику</button>
    <button onClick={()=>act(async()=>setResult(await post('/storage/retention/run?dry_run=true')))}>Перевірити без видалення</button>
    <button disabled={!data.settings.enabled} onClick={()=>act(async()=>{if(!window.confirm('Перемістити лише перевірені придатні пакети в кошик Drive?'))return;setResult(await post('/storage/retention/run?dry_run=false'));await refresh();})}>Виконати очищення</button>
    {result&&<p role="status">Вибрано {result.selected.length}. Видалено {result.deleted.length}. Помилок {result.errors.length}. {result.dry_run?'Прогноз пакетів понад ліміт':'Пакетів понад ліміт'} — {result.over_limit}.</p>}
    <details><summary>Пакети та захист ({data.packages.length})</summary>{data.packages.map(item=><p key={item.job}><code>{item.job.slice(0,12)}</code> · {item.state} · {item.eligible?'Придатний':item.reasons.join(', ')} <button disabled={item.state!=='ACTIVE'} onClick={()=>act(async()=>{await post(`/storage/retention/${item.job}/protect`,{approved:!item.protected});await refresh();})}>{item.protected?'Зняти захист':'Захистити'}</button></p>)}</details>
    <details><summary>Історія очищення</summary>{data.history.map((item,i)=><p key={i}>{new Date(item.at*1000).toLocaleString()} · {item.job.slice(0,12)} · {item.state} · {item.detail}</p>)}</details>
  </section>;
}
