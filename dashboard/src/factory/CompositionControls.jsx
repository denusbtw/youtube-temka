import React,{useEffect,useState} from 'react';

export const layouts={auto:['Auto Recommended','Зберігає основне відео, якщо додатковий матеріал не вибрано'],smart:['Fill Screen','Основне відео заповнює кадр'],crop:['Center Crop','Центральна частина кадру'],blur:['Blurred Sides','Основне відео на розмитому фоні'],gameplay:['Split Screen','Основне відео зверху, додаткове знизу']};
const hookPresets={
  'Gaming Clean':{preset:'blur',hook_font:'sans',hook_font_size:52,hook_outline:3,hook_shadow:true,hook_box:false,hook_position:'top',caption_style:'clean'},
  'Gaming Dynamic':{preset:'auto',hook_font:'sans',hook_font_size:58,hook_outline:4,hook_shadow:true,hook_box:false,hook_position:'top',caption_style:'pop'},
  'Streamer Reaction':{preset:'auto',hook_font:'sans',hook_font_size:50,hook_outline:3,hook_shadow:true,hook_box:false,hook_position:'top',caption_style:'pop'},
  'Podcast Premium':{preset:'smart',hook_font:'sans',hook_font_size:48,hook_outline:2,hook_shadow:true,hook_box:false,hook_position:'top',caption_style:'clean'},
  'Satisfying / Visual':{preset:'blur',hook_font:'serif',hook_font_size:44,hook_outline:2,hook_shadow:true,hook_box:false,hook_position:'top',caption_style:'clean'},
};
export const textModes={none:'No Text',subtitles:'Subtitles',static_hook:'Static Hook',hook_subtitles:'Hook + Subtitles',custom:'Custom Text'};

function BackgroundPreview({name}) {
  const [useVideo,setUseVideo]=useState(false);
  const source=`/api/library/backgrounds/${encodeURIComponent(name)}`;
  if(useVideo) return <video src={source} aria-label="Прев’ю додаткового відео" muted playsInline preload="metadata" controls/>;
  return <img src={`${source}/frame`} alt="Кадр додаткового відео" onError={()=>setUseVideo(true)}/>;
}

export function LayoutPreview({form,thumbnail,video}) {
  const wide=form.mode==='longs',split=!wide&&form.preset==='gameplay',blur=!wide&&form.preset==='blur';
  const [geometry,setGeometry]=useState(null);
  const geometryRequest=JSON.stringify({ratio:form.split_ratio||.65,secondary_position:form.background_position||'bottom',
    split,hook_position:form.hook_position||'top',caption_margin:form.caption_margin??null});
  useEffect(()=>{
    const request=new AbortController();
    fetch('/api/composition/geometry',{method:'POST',headers:{'Content-Type':'application/json'},body:geometryRequest,signal:request.signal})
      .then(response=>response.ok?response.json():null).then(value=>{if(!request.signal.aborted)setGeometry(value);}).catch(()=>{});
    return ()=>request.abort();
  },[geometryRequest]);
  const primary=geometry?.primary.height/1920||form.split_ratio||.65;
  const hookTop=split&&geometry?geometry.hook_anchor_y/19.2:({top:12,center:50,bottom:75}[form.hook_position||'top']);
  const captionBottom=100*(geometry?.caption_margin??form.caption_margin??.15);
  const frame=thumbnail ? <img src={thumbnail} alt="Кадр джерела"/> : video ? <video src={video} muted playsInline preload="metadata" controls/> : <span>Додайте джерело для перегляду кадру</span>;
  return <figure className="layout-preview"><div style={split?{display:'flex',flexDirection:form.background_position==='top'?'column-reverse':'column'}:undefined} className={`layout-frame ${wide?'wide':''} ${blur?'blurred':''} ${split?'split':''}`}>
    <div className={`main-frame ${split&&form.split_crop!=='fill'?'panel-fit':''}`} style={split?{height:`${primary*100}%`}:undefined}>{split&&form.split_crop!=='fill'&&form.split_crop!=='fit'&&thumbnail&&<img aria-hidden="true" className="panel-extension" src={thumbnail}/>}<div className="panel-content">{frame}</div></div>{split&&<div className={`secondary-frame ${form.split_crop!=='fill'?'panel-fit':''}`} style={{height:`${(1-primary)*100}%`}}>{form.background_mode==='none'?'Додаткове відео вимкнене':form.background?<BackgroundPreview key={form.background} name={form.background}/>:'Додаткове відео з бібліотеки'}</div>}
    {form.hook_mode!=='none'&&['static_hook','hook_subtitles','custom'].includes(form.text_mode)&&<strong className="preview-hook" style={{top:`${hookTop}%`,transform:form.hook_position==='bottom'?'translateY(-100%)':form.hook_position==='center'?'translateY(-50%)':undefined,color:form.hook_color||'#fff',background:form.hook_box===false?'transparent':'#000b',fontFamily:{sans:'sans-serif',serif:'serif',mono:'monospace'}[form.hook_font||'sans'],fontSize:Math.max(9,(form.hook_font_size||48)/6)}}>{form.hook||'Заголовок відео'}</strong>}
    {(form.text_mode?['subtitles','hook_subtitles'].includes(form.text_mode):form.captions)&&<span className="preview-subtitles" style={{bottom:`${captionBottom}%`}}>Приклад субтитрів</span>}
    {form.logo_asset&&<span className="preview-logo"><img src={`/api/library/logos/${form.logo_asset}`} alt="Лого" style={{width:'100%'}}/></span>}
  </div><figcaption>Preview only · {layouts[form.preset]?.[1]}. Остаточне кадрування перевіряється після рендера.</figcaption></figure>;
}

export default function CompositionControls({form,set,backgrounds=[],channels=[],profiles=[],routingLocked=false,thumbnail,video}) {
  const [assets,setAssets]=useState([]);
  useEffect(()=>{fetch('/api/library').then(r=>r.ok?r.json():[]).then(setAssets).catch(()=>{});},[]);
  return <section className="composition-controls"><div className="grid">
    <label>Цільовий YouTube-канал<select disabled={routingLocked} value={form.youtube_channel_key||form.publish_channel||''} onChange={e=>{
      const profile=profiles.find(p=>p.channel_key===e.target.value);
      set('youtube_channel_key',profile?.channel_key||null);
      if(profile){
        set('publish_channel',profile.channel_id||null);
        set('publish_automatic',false);
        set('mode',profile.mode);
        set('metadata_language',profile.language);
        const preferences=channels.find(c=>c.id===profile.channel_id)?.preferences;
        if(preferences?.editing_preset)set('preset',preferences.editing_preset);
        return;
      }
      const channel=channels.find(c=>c.id===e.target.value);set('publish_channel',channel?.id||null);set('publish_automatic',false);
      if(channel?.preferences?.metadata_language)set('metadata_language',channel.preferences.metadata_language);
      if(channel?.preferences?.editing_preset)set('preset',channel.preferences.editing_preset);
    }}><option value="">Без публікації · ручний перегляд</option>{profiles.filter(p=>!['story','compilation'].includes(form.workflow)||p.mode===form.mode).map(p=><option key={p.channel_key} value={p.channel_key}>{p.title} — {p.mode==='shorts'?'Shorts':'Long-form'}</option>)}{channels.filter(c=>!profiles.some(p=>p.channel_id===c.id)).map(c=><option key={c.id} value={c.id}>{c.preferences?.profile_name||c.title} · {c.preferences?.metadata_language||'auto'}</option>)}</select></label>
    <label>Текст<select value={form.text_mode||(form.captions?'subtitles':'none')} onChange={e=>{set('text_mode',e.target.value);set('captions',['subtitles','hook_subtitles'].includes(e.target.value));if(['none','subtitles'].includes(e.target.value))set('hook','');}}>{Object.entries(textModes).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
    <label>Макет<select value={form.preset} onChange={e=>set('preset',e.target.value)}>{Object.entries(layouts).map(([id,[label]])=><option key={id} value={id}>{label}</option>)}</select></label>
    <label>Додаткове відео<select value={['none','random'].includes(form.background_mode)?form.background_mode:form.background||'auto'} onChange={e=>{set('background_mode',['none','auto','random'].includes(e.target.value)?e.target.value:'specific');set('background',['none','auto','random'].includes(e.target.value)?null:e.target.value);}}><option value="none">Без додаткового відео</option><option value="auto">Автовибір з бібліотеки</option><option value="random">Випадковий із категорії</option>{backgrounds.map(name=><option key={name} value={name}>{assets.find(a=>a.name===name)?.display_name || "Фонове відео"}</option>)}</select></label>
    {form.background_mode==='random'&&<label>Категорія фону<select value={form.background_category||''} onChange={e=>set('background_category',e.target.value||null)}><option value="">Усі дозволені</option>{[...new Set(assets.filter(a=>a.kind==='backgrounds').map(a=>a.category))].map(c=><option key={c}>{c}</option>)}</select></label>}
  </div>
  <details><summary>Контекст публікації</summary><div className="grid">
    <label>Тип джерела<select value={form.source_category||'auto'} onChange={e=>set('source_category',e.target.value)}><option value="auto">Auto</option><option value="gameplay">Gaming</option><option value="podcast">Podcast</option><option value="stream">Stream</option><option value="visual">Visual</option></select></label>
    <label>Підтверджена гра або тема<input value={form.confirmed_subject||''} maxLength={100} onChange={e=>set('confirmed_subject',e.target.value)}/></label>
    <label>Подія, яку видно в уривку<input value={form.clip_observation||''} maxLength={500} onChange={e=>set('clip_observation',e.target.value)}/></label>
    <label>Категорія YouTube (ID)<input value={form.category_override||''} onChange={e=>set('category_override',e.target.value||null)}/></label>
    <label className="check"><input type="checkbox" checked={form.include_source_link||false} onChange={e=>set('include_source_link',e.target.checked)}/>Включити посилання на джерело</label>
    <label className="check"><input type="checkbox" checked={form.attribution_required||false} onChange={e=>set('attribution_required',e.target.checked)}/>Обов’язкова атрибуція</label>
  </div></details>
  {['static_hook','hook_subtitles','custom'].includes(form.text_mode)&&<div className="grid"><label>Текст заголовка<input maxLength={120} value={form.hook||''} placeholder="Залиште порожнім для AI-кандидатів" onChange={e=>set('hook',e.target.value)}/></label><label>Стиль заголовка<select defaultValue="" onChange={e=>{
    const preset=hookPresets[e.target.value];if(preset)Object.entries(preset).forEach(([key,value])=>set(key,value));
  }}><option value="">Власний стиль</option>{Object.keys(hookPresets).map(name=><option key={name}>{name}</option>)}</select></label><label>Показ заголовка<select value={form.hook_mode||(form.text_mode==='custom'?'intro':'persistent')} onChange={e=>set('hook_mode',e.target.value)}><option value="persistent">Persistent</option><option value="timed">Timed</option><option value="intro">Intro Only</option><option value="none">None</option></select></label>{form.hook_mode==='timed'&&<><label>Початок<input type="number" min="0" value={form.hook_start??0} onChange={e=>set('hook_start',+e.target.value)}/></label><label>Кінець<input type="number" min=".1" value={form.hook_end??15} onChange={e=>set('hook_end',+e.target.value)}/></label></>}{(form.hook_mode||(form.text_mode==='custom'?'intro':'persistent'))==='intro'&&<label>Тривалість заголовка, сек<input type="number" min=".5" max="30" step=".5" value={form.hook_duration||3} onChange={e=>set('hook_duration',+e.target.value)}/></label>}<label>Позиція<select value={form.hook_position||'top'} onChange={e=>set('hook_position',e.target.value)}><option value="top">Зверху</option><option value="center">По центру</option><option value="bottom">Знизу</option></select></label></div>}
  {!form.workflow&&form.mode==='shorts'&&<div className="grid"><label>Тип Shorts<select value={form.series_mode||'independent'} onChange={e=>set('series_mode',e.target.value)}><option value="independent">Independent Highlights</option><option value="connected">Connected Story</option><option value="sequential">Sequential Source Split</option></select></label>{['connected','sequential'].includes(form.series_mode)&&<label>Перекриття джерела, сек<input type="number" min="0" max="5" step=".1" value={form.series_overlap??3} onChange={e=>set('series_overlap',+e.target.value)}/></label>}</div>}
  {!form.workflow&&form.mode==='shorts'&&<label className="check"><input type="checkbox" checked={form.plan_only||false} onChange={e=>{set('plan_only',e.target.checked);if(e.target.checked)set('publish_automatic',false);}}/>Переглянути план перед рендером</label>}
  {form.preset==='gameplay'&&<div className="grid">
    <label>Позиція додаткового відео<select value={form.background_position||'bottom'} onChange={e=>set('background_position',e.target.value)}><option value="bottom">Знизу</option><option value="top">Зверху</option></select></label>
    <label>Швидкість фону<input type="number" min=".5" max="2" step=".05" value={form.background_speed??1} onChange={e=>set('background_speed',+e.target.value)}/></label>
    <label>Початок фону, сек<input type="number" min="0" step=".1" value={form.background_offset??0} onChange={e=>set('background_offset',+e.target.value)}/></label>
    <label>Субтитри, відступ від низу (%)<input type="number" min="7" max="75" value={Math.round((form.caption_margin??.15)*100)} onChange={e=>set('caption_margin',+e.target.value/100)}/></label>
    <label>Кадрування<select value={form.split_crop||'blurred'} onChange={e=>set('split_crop',e.target.value)}><option value="blurred">Весь кадр із розмитим заповненням</option><option value="fit">Весь кадр із чорними полями</option><option value="fill">Заповнити панелі</option></select></label>
    <label className="check"><input type="checkbox" checked={form.background_loop??true} onChange={e=>set('background_loop',e.target.checked)}/>Повторювати фонове відео</label>
  </div>}
  {form.mode==='shorts'&&<p className="muted">{({
    independent:'Окремі моменти з різних частин джерела.',
    connected:'Послідовні частини однієї підтвердженої події.',
    sequential:'Поділ заданого інтервалу за часом.'
  })[form.series_mode||'independent']}</p>}
  <LayoutPreview form={form} thumbnail={thumbnail} video={video}/>
  <details><summary>Оформлення тексту та переклад</summary><div className="grid"><label>Шрифт<select value={form.hook_font||'sans'} onChange={e=>set('hook_font',e.target.value)}><option value="sans">Sans</option><option value="serif">Serif</option><option value="mono">Mono</option></select></label><label>Колір<input type="color" value={form.hook_color||'#ffffff'} onChange={e=>set('hook_color',e.target.value)}/></label><label>Розмір шрифту<input type="number" min="18" max="96" value={form.hook_font_size||48} onChange={e=>set('hook_font_size',+e.target.value)}/></label><label>Обведення<input type="number" min="0" max="8" value={form.hook_outline??2} onChange={e=>set('hook_outline',+e.target.value)}/></label><label>Переклад субтитрів<select value={form.subtitle_language||''} onChange={e=>set('subtitle_language',e.target.value||null)}><option value="">Мова оригіналу</option>{['en','es','pt-BR','de','ru','uk'].map(l=><option key={l}>{l}</option>)}</select></label></div>{[['hook_box','Підкладка'],['hook_shadow','Тінь']].map(([key,label])=><label className="check" key={key}><input type="checkbox" checked={form[key]??true} onChange={e=>set(key,e.target.checked)}/>{label}</label>)}<p>Переклад зберігає часові межі фраз і потребує редакторської перевірки.</p></details>
  </section>;
}
