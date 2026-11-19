import React,{useEffect,useState} from 'react';

export const layouts={auto:['Auto','Автоматичний вибір макета'],smart:['Fill Screen','Основне відео заповнює кадр'],crop:['Center Crop','Центральна частина кадру'],blur:['Blurred Sides','Основне відео на розмитому фоні'],gameplay:['Split Screen','Основне відео зверху, додаткове знизу']};
export const textModes={none:'No Text',subtitles:'Subtitles',static_hook:'Static Hook',hook_subtitles:'Hook + Subtitles',custom:'Custom Text'};

function BackgroundPreview({name}) {
  const [useVideo,setUseVideo]=useState(false);
  const source=`/api/library/backgrounds/${encodeURIComponent(name)}`;
  if(useVideo) return <video src={source} aria-label="Прев’ю додаткового відео" muted playsInline preload="metadata" controls/>;
  return <img src={`${source}/frame`} alt="Кадр додаткового відео" onError={()=>setUseVideo(true)}/>;
}

export function LayoutPreview({form,thumbnail,video}) {
  const split=form.preset==='gameplay',blur=form.preset==='blur',wide=form.mode==='longs';
  const frame=thumbnail ? <img src={thumbnail} alt="Кадр джерела"/> : video ? <video src={video} muted playsInline preload="metadata" controls/> : <span>Додайте джерело для перегляду кадру</span>;
  return <figure className="layout-preview"><div className={`layout-frame ${wide?'wide':''} ${blur?'blurred':''} ${split?'split':''}`}>
    <div className="main-frame" style={split?{height:`${(form.split_ratio||.5)*100}%`}:undefined}>{frame}</div>{split&&<div className="secondary-frame" style={{height:`${(1-(form.split_ratio||.5))*100}%`}}>{form.background_mode==='none'?'Додаткове відео вимкнене':form.background?<BackgroundPreview key={form.background} name={form.background}/>:'Додаткове відео з бібліотеки'}</div>}
    {['static_hook','hook_subtitles','custom'].includes(form.text_mode)&&<strong className="preview-hook" style={{top:form.hook_position==='center'?'45%':form.hook_position==='bottom'?'65%':'12%',color:form.hook_color||'#fff',background:form.hook_box===false?'transparent':'#000b',fontFamily:{sans:'sans-serif',serif:'serif',mono:'monospace'}[form.hook_font||'sans'],fontSize:Math.max(9,(form.hook_font_size||48)/6)}}>{form.hook||'Заголовок відео'}</strong>}
    {(form.text_mode?['subtitles','hook_subtitles'].includes(form.text_mode):form.captions)&&<span className="preview-subtitles">Приклад субтитрів</span>}
    {form.logo_asset&&<span className="preview-logo"><img src={`/api/library/logos/${form.logo_asset}`} alt="Лого" style={{width:'100%'}}/></span>}
  </div><figcaption>Preview only · {layouts[form.preset]?.[1]}. Остаточне кадрування перевіряється після рендера.</figcaption></figure>;
}

export default function CompositionControls({form,set,backgrounds=[],channels=[],thumbnail,video}) {
  const [assets,setAssets]=useState([]);
  useEffect(()=>{fetch('/api/library').then(r=>r.ok?r.json():[]).then(setAssets).catch(()=>{});},[]);
  return <section className="composition-controls"><div className="grid">
    <label>Канал / профіль<select value={form.publish_channel||''} onChange={e=>{
      const channel=channels.find(c=>c.id===e.target.value);set('publish_channel',channel?.id||null);set('publish_automatic',false);
      if(channel?.preferences?.metadata_language)set('metadata_language',channel.preferences.metadata_language);
      if(channel?.preferences?.editing_preset)set('preset',channel.preferences.editing_preset);
    }}><option value="">Без публікації · ручний перегляд</option>{channels.map(c=><option key={c.id} value={c.id}>{c.preferences?.profile_name||c.title} · {c.preferences?.metadata_language||'auto'}</option>)}</select></label>
    <label>Текст<select value={form.text_mode||(form.captions?'subtitles':'none')} onChange={e=>{set('text_mode',e.target.value);set('captions',['subtitles','hook_subtitles'].includes(e.target.value));if(['none','subtitles'].includes(e.target.value))set('hook','');}}>{Object.entries(textModes).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
    <label>Макет<select value={form.preset} onChange={e=>set('preset',e.target.value)}>{Object.entries(layouts).map(([id,[label]])=><option key={id} value={id}>{label}</option>)}</select></label>
    <label>Додаткове відео<select value={['none','random'].includes(form.background_mode)?form.background_mode:form.background||'auto'} onChange={e=>{set('background_mode',['none','auto','random'].includes(e.target.value)?e.target.value:'specific');set('background',['none','auto','random'].includes(e.target.value)?null:e.target.value);}}><option value="none">Без додаткового відео</option><option value="auto">Автовибір з бібліотеки</option><option value="random">Випадковий із категорії</option>{backgrounds.map(name=><option key={name}>{name}</option>)}</select></label>
    {form.background_mode==='random'&&<label>Категорія фону<select value={form.background_category||''} onChange={e=>set('background_category',e.target.value||null)}><option value="">Усі дозволені</option>{[...new Set(assets.filter(a=>a.kind==='backgrounds').map(a=>a.category))].map(c=><option key={c}>{c}</option>)}</select></label>}
  </div>
  {['static_hook','hook_subtitles','custom'].includes(form.text_mode)&&<div className="grid"><label>Текст заголовка<input maxLength={120} value={form.hook||''} placeholder="Залиште порожнім для AI-кандидатів" onChange={e=>set('hook',e.target.value)}/></label><label>Тривалість заголовка, сек<input type="number" min=".5" max="30" step=".5" value={form.hook_duration||3} onChange={e=>set('hook_duration',+e.target.value)}/></label><label>Позиція<select value={form.hook_position||'top'} onChange={e=>set('hook_position',e.target.value)}><option value="top">Зверху</option><option value="center">По центру</option><option value="bottom">Знизу</option></select></label></div>}
  {form.mode==='shorts'&&<div className="grid"><label>Тип Shorts<select value={form.series_mode||'independent'} onChange={e=>set('series_mode',e.target.value)}><option value="independent">Independent Shorts</option><option value="connected">Connected Series</option></select></label>{form.series_mode==='connected'&&<label>Перекриття джерела, сек<input type="number" min="0" max="5" step=".1" value={form.series_overlap??3} onChange={e=>set('series_overlap',+e.target.value)}/></label>}</div>}
  <LayoutPreview form={form} thumbnail={thumbnail} video={video}/>
  <details><summary>Оформлення тексту та переклад</summary><div className="grid"><label>Шрифт<select value={form.hook_font||'sans'} onChange={e=>set('hook_font',e.target.value)}><option value="sans">Sans</option><option value="serif">Serif</option><option value="mono">Mono</option></select></label><label>Колір<input type="color" value={form.hook_color||'#ffffff'} onChange={e=>set('hook_color',e.target.value)}/></label><label>Розмір шрифту<input type="number" min="18" max="96" value={form.hook_font_size||48} onChange={e=>set('hook_font_size',+e.target.value)}/></label><label>Обведення<input type="number" min="0" max="8" value={form.hook_outline??2} onChange={e=>set('hook_outline',+e.target.value)}/></label><label>Переклад субтитрів<select value={form.subtitle_language||''} onChange={e=>set('subtitle_language',e.target.value||null)}><option value="">Мова оригіналу</option>{['en','es','pt-BR','de','ru','uk'].map(l=><option key={l}>{l}</option>)}</select></label></div>{[['hook_box','Підкладка'],['hook_shadow','Тінь']].map(([key,label])=><label className="check" key={key}><input type="checkbox" checked={form[key]??true} onChange={e=>set(key,e.target.checked)}/>{label}</label>)}<p>Переклад зберігає часові межі фраз і потребує редакторської перевірки.</p></details>
  </section>;
}
