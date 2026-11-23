import React,{useEffect,useState} from 'react';

const categories={
  satisfying:'Satisfying / ASMR','soap-cutting':'Soap Cutting','kinetic-sand':'Kinetic Sand',
  'bouncing-balls':'Bouncing Ball','obstacle-courses':'Obstacle Courses','abstract-motion':'Abstract Motion',
  'minecraft-parkour':'Minecraft Parkour','subway-surfers':'Subway Surfers','platformer-gameplay':'Platformer Gameplay',
  parkour:'Parkour',minecraft:'Minecraft-style Gameplay',subway:'Subway Surfers-style Gameplay',custom:'Custom'
};
const starterCategories={
  'bouncing-ball':'bouncing-balls','geometric-shapes':'abstract-motion','obstacle-rings':'obstacle-courses',
  'scrolling-pattern':'abstract-motion','particle-wave':'abstract-motion','satisfying-loop':'satisfying'
};

export default function AssetLibrary({api,post,act}) {
  const [items,setItems]=useState([]);
  const [search,setSearch]=useState('');
  const [filter,setFilter]=useState('');
  const [category,setCategory]=useState('custom');
  const [query,setQuery]=useState('');
  const [results,setResults]=useState(null);
  const [favorites,setFavorites]=useState(false);
  const [archived,setArchived]=useState(false);
  const [license,setLicense]=useState('');
  const refresh=()=>api('/library').then(setItems);
  useEffect(()=>{refresh().catch(()=>{});},[]);
  const status=(item)=>item.license_status||(item.source==='Operator upload'?'Права підтверджено оператором':'Оригінальна анімація');
  const visible=items.filter(item=>!!item.removed===archived&&(!favorites||item.favorite)&&
    (item.display_name||item.name).toLowerCase().includes(search.toLowerCase())&&
    (!filter||(starterCategories[item.category]||item.category)===filter));
  const update=(item,changes)=>act(async()=>{await post(`/library/backgrounds/${item.name}/preferences`,changes);await refresh();});
  return <section>
    <h2>Додаткові відео та лого</h2>
    <button onClick={()=>act(async()=>{await post('/library/bootstrap');await refresh();})}>Створити шість оригінальних фонів</button>
    <div className="grid">
      <label>Категорія імпорту<select value={category} onChange={e=>setCategory(e.target.value)}>{Object.entries(categories).map(([id,text])=><option key={id} value={id}>{text}</option>)}</select></label>
      <label>Документ або умови ліцензії<input value={license} onChange={e=>setLicense(e.target.value)} maxLength={1000}/></label>
      <label>Авторизований MP4 / PNG<input type="file" accept=".mp4,.png" onChange={e=>{
        const file=e.target.files[0];e.target.value='';if(!file)return;
        act(async()=>{
          if(!window.confirm('Ви маєте права використовувати цей матеріал у готовому відео?'))return;
          const body=new FormData();
          body.append('file',file);body.append('rights_confirmed','true');body.append('category',category);
          if(license.trim())body.append('license',license.trim());
          body.append(file.name.toLowerCase().endsWith('.png')?'logo':'background','true');
          await api('/uploads',{method:'POST',body});await refresh();
        });
      }}/></label>
    </div>
    <details>
      <summary>Пошук у Wikimedia Commons</summary>
      <form onSubmit={e=>{e.preventDefault();act(async()=>setResults(await api('/library/commons?query='+encodeURIComponent(query))));}}>
        <label>Запит<input value={query} onChange={e=>setQuery(e.target.value)} minLength={2} maxLength={100} required/></label>
        <button>Знайти</button>
      </form>
      {results?.length===0&&<p>Відео не знайдено.</p>}
      {results?.map(item=><article key={item.id}>
        <b>{item.title}</b><p>{item.author} · {item.license} · {item.eligible?'Доступний після перевірки':'Потрібна ручна перевірка'}</p>
        {item.source.startsWith('https://commons.wikimedia.org/')&&<a href={item.source} target="_blank" rel="noreferrer">Переглянути джерело та ліцензію</a>}
        {item.eligible&&<button onClick={()=>act(async()=>{
          if(!window.confirm('Ви переглянули матеріал і підтверджуєте застосовність CC0 до відеоряду?'))return;
          await post('/library/commons',{page_id:item.id,category,license_reviewed:true});await refresh();
        })}>Імпортувати</button>}
      </article>)}
    </details>
    <div className="grid">
      <label>Категорія<select value={filter} onChange={e=>setFilter(e.target.value)}><option value="">Усі</option>{Object.entries(categories).map(([id,text])=><option key={id} value={id}>{text}</option>)}</select></label>
      <label className="check"><input type="checkbox" checked={favorites} onChange={e=>setFavorites(e.target.checked)}/>Вибране</label>
      <label className="check"><input type="checkbox" checked={archived} onChange={e=>setArchived(e.target.checked)}/>Вилучені з вибору</label>
    </div>
    <label>Пошук у бібліотеці<input value={search} onChange={e=>setSearch(e.target.value)}/></label>
    {visible.length===0&&<p>У цій категорії немає доступних матеріалів. Додайте власний запис або матеріал із перевіреною ліцензією.</p>}
    <div className="outputs">{visible.map(item=>{
      const url=`/api/library/${item.kind}/${item.name}`;
      const poster=item.kind==='backgrounds'?`${url}/frame`:undefined;
      return <article key={item.kind+item.id}>
        <b>{item.display_name || item.name}</b>
        <p>{categories[starterCategories[item.category]||item.category]||item.category} · {item.width}×{item.height} · {item.duration.toFixed(1)} с</p>
        <p>{status(item)} · {item.license}</p>
        {poster&&<img src={poster} loading="lazy" width="180" alt="Кадр фонового відео"/>}
        <details><summary>Прев’ю та походження</summary>
          {item.kind==='logos'?<img loading="lazy" src={url} alt={item.name}/>:<video controls muted preload="none" poster={poster} src={url}/>}
          <p>{item.author} {item.source?.startsWith('https://')?<a href={item.source} target="_blank" rel="noreferrer">Джерело</a>:item.source}</p><p>{item.fps||0} FPS · {(item.size_bytes/1048576).toFixed(2)} MiB · {item.attribution_required==null?'Атрибуція потребує перевірки':item.attribution_required?'Потрібна атрибуція':'Атрибуція не обов’язкова'}</p><p>Завдань із матеріалом: {item.job_references??0}</p><code>{item.sha256}</code>
        </details>
        {item.kind==='backgrounds'&&<>
          <button onClick={()=>{const label=window.prompt('Назва матеріалу',item.display_name||'');if(label!==null)update(item,{label});}}>Перейменувати</button>
          <button onClick={()=>update(item,{favorite:!item.favorite})}>{item.favorite?'Прибрати з вибраного':'Додати до вибраного'}</button>
          <button onClick={()=>update(item,{removed:!item.removed})}>{item.removed?'Повернути в бібліотеку':'Прибрати з бібліотеки'}</button>
        </>}
      </article>;
    })}</div>
  </section>;
}
