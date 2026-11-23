import asyncio
from typing import Literal
from .composition import GeometryRequest, geometry
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import threading
import time
import uuid
import re
import mimetypes
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from . import config, store, media
from .models import JobRequest
from .resources import preflight, usage


@asynccontextmanager
async def lifespan(app):
    config.initialize()
    from .worker import local_loop
    stop=threading.Event()
    if os.environ.get('FACTORY_LOCAL_WORKER','1')=='1':
        thread=threading.Thread(target=local_loop,args=(stop,),daemon=True)
        thread.start()
        app.state.local_worker=thread
    if os.environ.get('FACTORY_PUBLISHER','1')=='1':
        from .publisher import loop
        app.state.publisher=threading.Thread(target=loop,args=(stop,),daemon=True)
        app.state.publisher.start()
    if os.environ.get('FACTORY_ANALYTICS','1')=='1':
        from .analytics import loop as analytics_loop
        app.state.analytics=threading.Thread(target=analytics_loop,args=(stop,),daemon=True)
        app.state.analytics.start()
    yield
    stop.set()
    for name in ('local_worker','publisher','analytics'):
        thread=getattr(app.state,name,None)
        if thread:thread.join(timeout=5)


app=FastAPI(title='AI Content Factory',lifespan=lifespan)


def drive_is_configured():
    standard=os.environ.get('DRIVE_TOKEN_FILE','')
    legacy=[os.environ.get('DRIVE_OAUTH_CLIENT_FILE',''),os.environ.get('DRIVE_OAUTH_TOKEN_FILE','')]
    return bool((standard and Path(standard).is_file()) or all(p and Path(p).is_file() for p in legacy))


@app.middleware('http')
async def authenticate(request, call_next):
    from fastapi.responses import JSONResponse
    if request.url.path not in ('/health','/api/session'):
        provided=request.headers.get('Authorization','').removeprefix('Bearer ') or request.cookies.get('factory_session','')
        if len(config.TOKEN)<32:
            return JSONResponse({'detail':'Set FACTORY_TOKEN to a random secret of at least 32 characters before using this controller'},status_code=503)
        if not hmac.compare_digest(provided,config.TOKEN):
            return JSONResponse({'detail':'Controller authentication required'},status_code=401)
    return await call_next(request)


@app.get('/health')
def health():
    worker=getattr(app.state,'local_worker',None)
    if worker is not None and not worker.is_alive():raise HTTPException(503,'Local processing worker stopped; inspect controller logs')
    return {'status':'ok','configured':len(config.TOKEN)>=32,'worker_alive':worker is None or worker.is_alive()}


class Login(BaseModel):
    token: str = Field(max_length=256)


@app.get('/api/readiness')
def deployment_readiness():
    from .deployment import readiness
    result=readiness()
    result['services']={}
    for name in ('local_worker','publisher','analytics'):
        thread=getattr(app.state,name,None)
        result['services'][name]='RUNNING' if thread and thread.is_alive() else 'DISABLED_OR_STOPPED'
    return result


@app.post('/api/session')
def login(body:Login):
    from fastapi.responses import JSONResponse
    if len(config.TOKEN)<32 or not hmac.compare_digest(body.token,config.TOKEN):
        raise HTTPException(401,'Invalid controller token or controller not configured')
    response=JSONResponse({'authenticated':True})
    response.set_cookie('factory_session',body.token,httponly=True,samesite='strict',secure=os.environ.get('FACTORY_HTTPS')=='1')
    return response


@app.delete('/api/session')
def logout():
    from fastapi.responses import JSONResponse
    response=JSONResponse({'authenticated':False})
    response.delete_cookie('factory_session')
    return response


@app.get('/api/settings')
def settings():
    from .model_setup import status
    from .youtube_channels import profiles
    from .publisher import channels
    from .tts import status as tts_status
    return {'storage':usage(),'workers':store.workers(),'channels':channels(),'youtube_profiles':profiles(),'drive_configured':drive_is_configured(),'inference_mode':os.environ.get('OLLAMA_GPU_LAYERS','0'),'ollama_model':config.MODEL,'whisper_model':config.ASR_MODEL,'model_setup':status(),'tts':tts_status(),'publishing':{'enabled':bool(list(Path(os.environ.get('YOUTUBE_TOKEN_DIR','secrets/youtube')).glob('*.json'))),'state':'CONNECTED' if channels() else 'YOUTUBE_AUTH_REQUIRED','reason':'Use the one-time Desktop OAuth consent, discover the channel, then approve a versioned publishing draft. Public publishing requires explicit channel authorization.'},'limits':{'interval_minutes':1440,'audio_chunk_minutes':config.AUDIO_CHUNK_SECONDS/60,'concurrent_jobs':1},'notes':['Smart Crop samples faces on CPU; no-face footage falls back to a blurred background.','GPU inference is sequential; CPU/int8 is the default.','Remote workers process URL jobs with Drive enabled; uploaded local sources stay on controller.','Failed/cancelled scratch workspaces expire after 72 hours; running jobs are excluded.']}


@app.post('/api/settings/check-ollama')
def check_ollama():
    import httpx
    try:
        response=httpx.get(config.OLLAMA+'/api/tags',timeout=5,trust_env=False)
        response.raise_for_status()
        found=config.MODEL in [m['name'] for m in response.json()['models']]
        return {'available':found,'action':None if found else 'ollama pull '+config.MODEL}
    except (httpx.HTTPError,KeyError,ValueError):
        return {'available':False,'action':'Start Ollama, then run: ollama pull '+config.MODEL}


@app.post('/api/tts/setup')
def tts_setup():
    from .tts import setup,status
    if not status()['runtime']:raise HTTPException(409,'Rebuild with WITH_TTS=1 for optional narration. Original audio works without it.')
    return setup()


class VoicePreview(BaseModel):
    voice:str=Field(pattern=r'^(af_heart|af_bella|am_michael|ef_dora|em_alex)$')


@app.post('/api/tts/preview')
def voice_preview(body:VoicePreview):
    from .tts import synthesize,VOICES
    language=VOICES[body.voice]
    text='A clear, friendly voice for your next video.' if language=='en' else 'Una voz clara y agradable para tu próximo vídeo.'
    folder=config.DATA/'voice-previews';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(body.voice+'.wav')
    from .heavy import HEAVY_LOCK
    if not HEAVY_LOCK.acquire(False):raise HTTPException(409,'Processing slot busy; try the voice preview after the current task')
    try:
        if not path.is_file():synthesize(text,body.voice,1,language,path)
    except (ValueError,RuntimeError) as error:raise HTTPException(409,str(error)) from None
    finally:HEAVY_LOCK.release()
    return {'url':'/api/tts/previews/'+body.voice+'.wav'}


@app.get('/api/tts/previews/{name}')
def voice_file(name:str):
    from .tts import VOICES
    if name not in [v+'.wav' for v in VOICES]:raise HTTPException(404,'Unknown voice')
    path=config.DATA/'voice-previews'/name
    if not path.is_file():raise HTTPException(404,'Generate the preview first')
    return FileResponse(path,media_type='audio/wav')


@app.post('/api/settings/setup-model')
def setup_model():
    from .model_setup import start
    return start()


class Models(BaseModel):
    ollama_model:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$')
    whisper_model:str=Field(pattern=r'^(tiny|base|small|medium|large-v3|large-v3-turbo)$')


@app.post('/api/settings/models')
def configure_models(body:Models):
    from .model_setup import _lock
    from jsonfile import write_json_atomic
    if _lock.locked():
        raise HTTPException(409,'Wait for the active model setup to finish')
    config.initialize()
    write_json_atomic(config.DATA/'settings.json',body.model_dump())
    config.MODEL=body.ollama_model
    config.ASR_MODEL=body.whisper_model
    return {'saved':True,'action':'Check availability or download the configured model. Existing jobs retain their model selection.'}


@app.post('/api/settings/check-drive')
def check_drive():
    from .drive import Drive, BASE
    try:
        drive=Drive()
        try:
            drive.request('GET',BASE+'/about',params={'fields':'storageQuota'})
        finally:
            drive.close()
        return {'authorized':True}
    except (ValueError,KeyError,OSError):
        raise HTTPException(409,'Drive authorization unavailable. Configure the existing authorized-user JSON with Drive scope; original credential files are not modified.') from None


class Source(BaseModel):
    source:str=Field(max_length=2048)


@app.post('/api/probe')
def probe(body:Source):
    try:
        return media.probe(body.source)
    except Exception:
        raise HTTPException(400,'Source cannot be probed. Use a completed public YouTube/Twitch VOD or a valid uploaded video; verify network access.') from None


@app.post('/api/uploads')
async def upload(file:UploadFile=File(...), rights_confirmed:bool=Form(...), background:bool=Form(False),music:bool=Form(False),logo:bool=Form(False),license:str=Form('Operator-confirmed rights; license documentation not supplied'),category:str=Form('custom')):
    if not rights_confirmed:
        raise HTTPException(422,'Source/background rights confirmation is required')
    suffix=Path(file.filename or '').suffix.lower()
    if sum((background,music,logo))>1:raise HTTPException(422,'Choose only one asset type')
    if suffix not in (('.png',) if logo else (('.mp3','.wav','.mp4') if music else ('.mp4','.mkv','.mov'))) or (background and suffix!='.mp4'):
        raise HTTPException(422,'Upload MP4/MKV/MOV; backgrounds must be MP4')
    folder=config.DATA/'logos' if logo else (config.DATA/'music' if music else ((config.DATA/'backgrounds') if background else config.SCRATCH/'uploads'))
    folder.mkdir(parents=True,exist_ok=True)
    name=uuid.uuid4().hex+suffix
    target=folder/name
    try:
        with target.open('xb') as output:
            while chunk:=await file.read(1024*1024):
                preflight(len(chunk))
                if target.stat().st_size+len(chunk)>2*1024**3:
                    raise ValueError('Upload exceeds 2 GB; use a bounded source URL or a smaller file')
                output.write(chunk)
        if not background and not music and not logo:
            metadata=media.probe('upload:'+name)
        else:
            metadata=None
            from .library import register
            documented=license!='Operator-confirmed rights; license documentation not supplied'
            provenance={'license_status':'operator_documented' if documented else 'operator_confirmed_manual_review',
                        'attribution_required':None}
            name,duplicate=register(target,'logos' if logo else ('music' if music else 'backgrounds'),license[:1000],
                                    'Operator upload',category[:80],provenance)
    except Exception:
        target.unlink(missing_ok=True)
        raise HTTPException(400,'Upload failed validation or exceeded available storage') from None
    finally:
        await file.close()
    return {'source':'upload:'+name,'name':name,'metadata':metadata,'duplicate':duplicate if background or music or logo else False}


@app.get('/api/library')
def library():
    from .library import entries
    return entries()


@app.post('/api/library/bootstrap')
def starter_library():
    from .library import bootstrap
    from .heavy import HEAVY_LOCK
    if not HEAVY_LOCK.acquire(False):raise HTTPException(409,'Wait for the current heavy task before creating starter assets')
    try:return bootstrap()
    finally:HEAVY_LOCK.release()


class LibraryPreference(BaseModel):
    favorite: bool | None = None
    removed: bool | None = None
    label: str | None = Field(default=None,max_length=100)


@app.post('/api/library/backgrounds/{name}/preferences')
def library_preferences(name:str,body:LibraryPreference):
    from .library import preferences
    try:return preferences(name,body.favorite,body.removed,body.label)
    except ValueError as error:raise HTTPException(404,str(error)) from None


class CommonsImport(BaseModel):
    page_id: int = Field(gt=0)
    category: str = Field(default='abstract-motion',max_length=80)
    license_reviewed: bool


@app.get('/api/library/commons')
def commons_search(query:str):
    from .commons import search
    try:return search(query)
    except (ValueError,RuntimeError) as error:raise HTTPException(400,str(error)) from None


@app.post('/api/library/commons')
def commons_import(body:CommonsImport):
    from .commons import import_video
    from .heavy import HEAVY_LOCK
    if not body.license_reviewed:raise HTTPException(422,'Verify provenance and rights before importing')
    if not HEAVY_LOCK.acquire(False):raise HTTPException(409,'Wait for the current processing task')
    try:return import_video(body.page_id,body.category)
    except (ValueError,RuntimeError) as error:raise HTTPException(400,str(error)) from None
    finally:HEAVY_LOCK.release()


@app.get('/api/library/{kind}/{name}')
def library_file(kind:str,name:str):
    from .assets import asset_path
    try:return FileResponse(asset_path(kind,name))
    except ValueError as error:raise HTTPException(404,str(error)) from None


@app.get('/api/library/{kind}/{name}/frame')
def library_frame(kind:str,name:str):
    from .assets import asset_path
    from .heavy import HEAVY_LOCK
    try:source=asset_path(kind,name)
    except ValueError as error:raise HTTPException(404,str(error)) from None
    folder=config.DATA/'asset-previews';folder.mkdir(parents=True,exist_ok=True)
    target=folder/(kind+'-'+name+'.jpg')
    if not target.is_file():
        if not HEAVY_LOCK.acquire(False):raise HTTPException(409,'Preview waits for the active processing task')
        try:
            temporary=target.with_suffix('.part.jpg')
            try:
                media.run([media.ffmpeg(),'-hide_banner','-loglevel','error','-y','-threads','1','-i',str(source),'-vf','scale=270:-2','-frames:v','1','-threads','1',str(temporary)],timeout=30)
                temporary.replace(target)
            finally:temporary.unlink(missing_ok=True)
        finally:HEAVY_LOCK.release()
    return FileResponse(target)


@app.post('/api/composition/geometry')
def composition_geometry(body:GeometryRequest):
    return geometry(body)


class ManifestPreviewRequest(BaseModel):
    source_start: float = Field(default=0.0, ge=0.0)
    source_end: float = Field(default=45.0, gt=0.0)
    source_width: int = Field(default=1280, gt=0)
    source_height: int = Field(default=720, gt=0)
    mode: Literal['shorts', 'longs'] = 'shorts'
    preset: Literal['auto', 'smart', 'crop', 'blur', 'gameplay'] = 'auto'
    source_category: Literal['auto', 'podcast', 'stream', 'gameplay', 'visual'] = 'auto'
    split_ratio: float = Field(default=0.65, ge=0.2, le=0.8)
    background_position: Literal['top', 'bottom'] = 'bottom'
    background: str | None = None
    split_crop: Literal['fill', 'fit', 'blurred'] = 'blurred'
    hook: str = Field(default='', max_length=120)
    hook_mode: Literal['persistent', 'timed', 'intro', 'none'] | None = 'persistent'
    hook_position: Literal['top', 'center', 'bottom'] = 'top'
    hook_font: Literal['sans', 'serif', 'mono'] = 'sans'
    hook_font_size: int = Field(default=54, ge=18, le=96)
    hook_color: str = Field(default='#FFFFFF', pattern=r'^#[0-9A-Fa-f]{6}$')
    hook_outline: int = Field(default=3, ge=0, le=8)
    hook_shadow: bool = True
    hook_box: bool = False
    captions: bool = True
    caption_style: Literal['pop', 'karaoke', 'clean'] = 'pop'
    caption_margin: float | None = Field(default=None, ge=0.07, le=0.75)
    facecam_corners: list[str] = Field(default_factory=list)
    has_center_face: bool = False
    two_speakers_detected: bool = False
    design_preset: Literal['gaming_clean', 'gaming_dynamic', 'streamer_reaction', 'podcast_premium', 'satisfying_visual'] | None = None


@app.post('/api/composition/manifest')
def composition_manifest(body: ManifestPreviewRequest):
    from .edit_manifest import DESIGN_PRESETS, build_edit_manifest
    manifest = build_edit_manifest(
        source_start=body.source_start,
        source_end=body.source_end,
        request=body,
        source_width=body.source_width,
        source_height=body.source_height,
        facecam_corners=body.facecam_corners,
        has_center_face=body.has_center_face,
        two_speakers_detected=body.two_speakers_detected,
        design_preset=body.design_preset,
        has_explicit_background=bool(body.background),
    )
    return {'manifest': manifest.model_dump(), 'design_presets': DESIGN_PRESETS}


@app.get('/api/backgrounds')
def backgrounds():
    from .library import entries
    removed={a['name'] for a in entries() if a['kind']=='backgrounds' and a.get('removed')}
    return [p.name for p in (config.DATA/'backgrounds').glob('*.mp4') if p.name not in removed]


@app.get('/api/source-preview/{name}')
def source_preview(name:str):
    try:
        path=media.local_source('upload:'+name)
        return FileResponse(path)
    except ValueError as error:raise HTTPException(404,str(error)) from None


@app.get('/api/assets/music')
def music_assets():
    return [p.name for p in (config.DATA/'music').glob('*') if p.suffix in ('.mp3','.wav','.mp4')]


@app.post('/api/preflight')
def preflight_job(body: JobRequest):
    from .models import CompilationSource
    from .timeline import story_part_count

    sources = body.sources or [CompilationSource(source=body.source,start=body.start,end=body.end)]
    checked = []
    for index, item in enumerate(sources,1):
        record = {'source_id':f'source-{index}'}
        try:
            data = media.probe(item.source)
            end = min(data['duration'], item.end or data['duration'])
            span = end-item.start
            if not 0 < span <= 86400:
                raise ValueError('Choose a selected range of up to 24 hours inside the source')
            if body.resolution != 'best' and data['platform']!='local' and not any(r<=body.resolution for r in data['resolutions']):
                raise ValueError('No source format matches the selected input resolution')
            if body.workflow=='story' and body.story_mode=='chronological':
                record['story_count'] = story_part_count(body,span)
            if any(b>end for _,b in body.intervals):
                raise ValueError('A manual interval extends beyond the source')
            record.update(title=data['title'],duration=data['duration'],selected_seconds=span)
        except (ValueError, media.MediaFailure) as error:
            record['error'] = str(error)[:500]
        except Exception:
            record['error'] = 'Media is unavailable. Check access, format or connection.'
        checked.append(record)
    return {'sources':checked,'ready':all('error' not in item for item in checked),
            'shorts_max_seconds':config.SHORTS_MAX_SECONDS}


@app.post('/api/jobs',status_code=201)
def create(body:JobRequest):
    if body.youtube_channel_key:
        from .youtube_channels import PROFILES
        profile = PROFILES[body.youtube_channel_key]
        if 'mode' not in body.model_fields_set:
            body.mode = profile['mode']
        if not body.metadata_language:
            body.metadata_language = profile['language']
    if body.mode == 'shorts' and not (body.workflow == 'highlights' and body.workflow_version == 1):
        body.selection_version = 2
        if 'minimum' not in body.model_fields_set:
            body.minimum=min(35,body.maximum)
    body.experiment_parent=None
    return create_job(body)


def create_job(body, seed=None, approved=None):
    if body.youtube_channel_key:
        from .youtube_channels import profiles
        profile = next(row for row in profiles() if row['channel_key'] == body.youtube_channel_key)
        if body.publish_channel and body.publish_channel != profile['channel_id']:
            raise HTTPException(409,'The selected YouTube channel does not match the content profile')
        if body.publish_automatic:
            raise HTTPException(422,'Managed channels require manual approval and private visibility')
        body.publish_channel = profile['channel_id']
    body.asr_model=body.asr_model or config.ASR_MODEL
    body.llm_model=body.llm_model or config.MODEL
    if body.publish_channel:body.metadata_preferences=store.preferences(body.publish_channel)
    if body.background_mode=='random' or (not body.background and body.background_category and body.background_mode=='auto'):
        from .library import recommend
        choice=recommend(body.background_category)
        if not choice:raise HTTPException(409,'No eligible asset in this category; import authorized media or choose another category')
        body.background=choice
        body.background_selection_reason='Eligible license, requested category, fewer recent uses, favorites and vertical resolution'
        body.background_mode='specific'
    if body.selection_version==2 and body.preset=='gameplay' and body.background_mode=='auto' and not body.background:
        raise HTTPException(422,'Choose a background or select Random with a category for Split Screen')
    if body.category_override:
        from .metadata import CATEGORIES
        if body.category_override not in CATEGORIES:raise HTTPException(422,'Unsupported YouTube category')
    if body.hook_mode=='timed' and body.mode=='shorts' and body.hook_start>=min(body.maximum,180):
        raise HTTPException(422,'Timed hook must start within the clip')
    if body.background and body.background_mode!='none' and body.background not in backgrounds():
        raise HTTPException(409,'Selected secondary asset is unavailable')
    if body.background and body.background_mode != 'none':
        from .library import entries
        asset = next((item for item in entries() if item['kind'] == 'backgrounds' and item['name'] == body.background), {})
        credit = asset.get('required_credit')
        if credit:
            existing_credit = body.source_attribution.strip()
            if credit not in existing_credit:
                combined = '\n'.join(filter(None, (existing_credit, credit)))
                if len(combined) > 1000:
                    raise HTTPException(422, 'Shorten source attribution to make room for the required background credit')
                body.source_attribution = combined
            body.attribution_required = True
    if body.publish_automatic:
        if not config.AUTOMATIC_PUBLISHING:raise HTTPException(422,'Automatic publishing is disabled')
        from .publisher import channels
        policy=next((c for c in channels() if c['id']==body.publish_channel),None)
        if not policy or not policy['public_allowed'] or not policy['automatic']:
            raise HTTPException(409,'Explicitly enable automatic public publishing for this authenticated channel first')
    try:
        if body.workflow=='compilation':
            for index, item in enumerate(body.sources,1):
                try:
                    if not media.local_source(item.source):
                        media.validate_url(item.source)
                except ValueError as error:
                    raise ValueError(f'Source {index}: {error}') from error
        elif body.source.startswith('edit:'):
            _,origin,index=body.source.split(':')
            original=store.get(origin)
            if not original or original['state']!='READY' or not original['result']['outputs'][int(index)].get('edit_source'):raise ValueError('Editable source unavailable; re-upload this older source once')
            original_request=original['request']
            if original_request.get('youtube_channel_key') and body.youtube_channel_key != original_request['youtube_channel_key']:
                raise ValueError('An edited version must keep its original YouTube profile')
        elif not media.local_source(body.source):
            media.validate_url(body.source)
    except (ValueError,IndexError,KeyError) as error:
        raise HTTPException(422,str(error)) from None
    try:
        preflight()
    except RuntimeError as error:
        raise HTTPException(409,str(error)) from None
    try:
        created = store.create(body.model_dump(), seed=seed, approved=approved)
        store.save_source_rights(created['id'], {
            'source_url': body.source if body.source.startswith('https://') else None,
            'permission_status': 'operator_confirmed',
            'attribution_required': body.attribution_required,
            'license': body.source_attribution[:300] if body.source_attribution else 'Operator-confirmed rights',
        })
        return created
    except ValueError as error:raise HTTPException(409,str(error)) from None


class Replan(BaseModel):
    mode: str | None = None
    count: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    overlap: float | None = None
    intervals: list[tuple[float,float]] | None = Field(default=None,min_length=1,max_length=30)
    experiment: int | None = Field(default=None,ge=0,le=19)


@app.post('/api/jobs/{jid}/replan',status_code=201)
def replan(jid:str, body:Replan):
    from tempfile import TemporaryDirectory
    job=get_job(jid)
    original=job['request']
    if original['mode']!='shorts' or job['state'] not in ('READY','BLOCKED','FAILED'):
        raise HTTPException(409,'Wait for Shorts analysis to finish')
    values={**original,'selection_version':2,'publish_automatic':False,
            'workflow':None,'story_mode':None,'review_before_render':False,
            'plan_only':body.intervals is None,'intervals':body.intervals or []}
    for target, value in (('series_mode',body.mode),('count',body.count),
                          ('minimum',body.minimum),('maximum',body.maximum),('series_overlap',body.overlap)):
        if value is not None:values[target]=value
    if body.experiment is not None:
        from .shorts import variants
        candidates=job['result'].get('candidates',[])
        if body.experiment >= len(candidates) or not job['result'].get('planning_only'):
            raise HTTPException(422,'Choose a candidate from a completed Shorts plan')
        candidate=candidates[body.experiment]
        options=variants(candidate,job['result'].get('experiment_boundaries',[]))
        if len(options)<2:raise HTTPException(422,'The proposed clip lacks multiple supported variant durations')
        body.intervals=[(original.get('start',0)+a,original.get('start',0)+b) for _,a,b in options]
        values.update(plan_only=False,intervals=body.intervals,experiment_parent=f'{jid}:{body.experiment}')
    approved=None
    if body.intervals:
        values.update(series_mode='independent', count=min(20,len(body.intervals)), silence_removal=False)
        existing=job['result'].get('candidates',[])
        source_start=original.get('start',0)
        if body.experiment is None and len(existing)==len(body.intervals) and all(
            abs(a-source_start-c['start_seconds'])<.001 and abs(b-source_start-c['end_seconds'])<.001
            for (a,b),c in zip(body.intervals,existing)
        ):
            approved=existing
        maximum=min(180,values['maximum'])
        if any(not 0 < b-a <= maximum for a,b in body.intervals):
            raise HTTPException(422,f'Edited clips must be positive and at most {maximum:g}s')
    try:request=JobRequest.model_validate(values)
    except ValueError as error:raise HTTPException(422,str(error)) from None
    root=config.SCRATCH/jid
    checkpoint=root/'transcript.json'
    if checkpoint.is_file():
        return create_job(request,checkpoint,approved)
    archived=job['result'].get('artifacts',{}).get('transcript.json')
    if not archived:raise HTTPException(409,'Transcript checkpoint is unavailable')
    with TemporaryDirectory(dir=config.SCRATCH) as folder:
        try:checkpoint=media.fetch_drive(archived,Path(folder)/'transcript.json')
        except (ValueError,RuntimeError,OSError) as error:raise HTTPException(409,'Transcript checkpoint could not be recovered') from error
        return create_job(request,checkpoint,approved)


@app.get('/api/jobs')
def jobs(limit:int=50,offset:int=0):
    if not 1<=limit<=200 or offset<0:raise HTTPException(422,'Use limit 1-200 and a nonnegative offset')
    return store.list_jobs(limit,offset)


@app.get('/api/job-history')
def job_history(scope:Literal['recent','all','archived','trash']='recent',
                state:Literal['','active','READY','FAILED','BLOCKED','CANCELLED']='',
                search:str='',after:float=0,sort:Literal['newest','oldest']='newest',
                limit:int=10,offset:int=0):
    if not 1<=limit<=50 or not 0<=offset or len(search)>150:
        raise HTTPException(422,'Invalid history filter or page size')
    terminal=('READY','FAILED','BLOCKED','CANCELLED')
    where=['j.created>=?'];params=[after]
    if scope!='all':
        where.append("coalesce(v.status,'recent')=?");params.append(scope)
    if state=='active':
        where.append('j.state NOT IN (?,?,?,?)');params.extend(terminal)
    elif state:
        where.append('j.state=?');params.append(state)
    if search:
        where.append("instr(fold(coalesce(json_extract(j.result,'$.source.title'),json_extract(j.request,'$.source'),j.id)),?)>0")
        params.append(search.casefold())
    order='DESC' if sort=='newest' else 'ASC'
    with store.connect() as db:
        db.create_function('fold',1,str.casefold,deterministic=True)
        rows=db.execute('SELECT j.* FROM jobs j LEFT JOIN job_visibility v ON v.job=j.id WHERE '+
                        ' AND '.join(where)+f' ORDER BY j.created {order},j.id {order} LIMIT ? OFFSET ?',
                        (*params,limit+1,offset)).fetchall()
        return {'items':[store.decode(row,db) for row in rows[:limit]],'has_more':len(rows)>limit}


class JobVisibility(BaseModel):
    ids: list[str] = Field(min_length=1,max_length=50)
    status: Literal['recent','archived','trash']


@app.post('/api/jobs/visibility')
def job_visibility(body:JobVisibility):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for jid in set(body.ids):
            row=db.execute('SELECT state,lease FROM jobs WHERE id=?',(jid,)).fetchone()
            if row is None:raise HTTPException(404,'Job not found')
            if row['lease'] or row['state'] not in ('READY','FAILED','BLOCKED','CANCELLED'):
                raise HTTPException(409,'Active jobs cannot be archived or moved to trash')
            db.execute('INSERT OR REPLACE INTO job_visibility VALUES(?,?)',(jid,body.status))
    return {'updated':len(set(body.ids))}


def job_deletion_dependencies(db,jid):
    if not re.fullmatch(r'[a-f0-9]{32}',jid):raise HTTPException(404,'Job not found')
    row=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    if not row:raise HTTPException(404,'Job not found')
    job=store.decode(row,db)
    publications=db.execute('SELECT COUNT(*) FROM publications WHERE job=?',(jid,)).fetchone()[0]
    edits=db.execute("SELECT COUNT(*) FROM jobs WHERE json_extract(request,'$.source') LIKE ?",(f'edit:{jid}:%',)).fetchone()[0]
    packages=db.execute('SELECT COUNT(*) FROM retention_packages WHERE job=?',(jid,)).fetchone()[0]
    outputs=len(job['result'].get('outputs',[]))
    artifacts=len(job['result'].get('artifacts',{}))
    workspace=os.path.lexists(config.SCRATCH/jid)
    allowed=(job['visibility']=='trash' and job['state'] in ('FAILED','CANCELLED')
             and not any((row['lease'],publications,edits,packages,outputs,artifacts,workspace)))
    return {'job':jid,'permanent_delete_available':allowed,'publications':publications,'dependent_edits':edits,
            'outputs':outputs,'remote_artifacts':artifacts,'workspace_present':workspace,
            'retention_packages':packages,'shared_background':job['request'].get('background')}


@app.get('/api/jobs/{jid}/deletion-preview')
def deletion_preview(jid:str):
    with store.connect() as db:return job_deletion_dependencies(db,jid)


class JobDeletion(BaseModel):
    confirmation: str


@app.post('/api/jobs/{jid}/delete-record')
def delete_job_record(jid:str,body:JobDeletion):
    if body.confirmation!=jid:raise HTTPException(422,'Type the job ID to confirm')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not job_deletion_dependencies(db,jid)['permanent_delete_available']:
            raise HTTPException(409,'Only trashed failed or cancelled records without artifacts or dependencies can be deleted')
        for table in ('job_stages','events','metadata_versions','job_visibility'):
            db.execute(f'DELETE FROM {table} WHERE job=?',(jid,))
        db.execute('DELETE FROM jobs WHERE id=?',(jid,))
    return {'removed':jid}


@app.get('/api/jobs/{jid}')
def get_job(jid:str):
    value=store.get(jid)
    if not value:
        raise HTTPException(404,'Job not found')
    value['editorial_workflow_state'] = store.editorial_workflow_state(value)
    value['source_rights'] = store.get_source_rights(jid)
    return value


@app.get('/api/jobs/{jid}/evidence')
def job_evidence(jid:str):
    job = get_job(jid)
    root = config.SCRATCH / jid
    graph_path = root / 'event-graph.json'
    ev_path = root / 'source-evidence-v1.json'
    graph_data = json.loads(graph_path.read_text(encoding='utf-8')) if graph_path.is_file() else {}
    shots = []
    duration = job['result'].get('source', {}).get('duration') or job['result'].get('processing_end') or 0
    if ev_path.is_file():
        ev_data = json.loads(ev_path.read_text(encoding='utf-8'))
        duration = ev_data.get('context', {}).get('duration', duration)
        shots = [
            {
                'shot_id': s['shot_id'],
                'start': s['start'],
                'end': s['end'],
                'duration': s['duration'],
                'boundary_type': s.get('boundary_type', 'hard_cut'),
                'mean_motion': s.get('mean_motion', 0),
                'speech_word_count': s.get('speech_word_count', 0),
                'face_corners': s.get('face_corners', []),
            }
            for s in ev_data.get('shots', [])
        ]
    return {
        'job_id': jid,
        'duration': duration,
        'shots': shots,
        'format_breaks': graph_data.get('format_breaks') or job['result'].get('format_breaks', []),
        'events': graph_data.get('events') or job['result'].get('events_summary', []),
        'candidates': job['result'].get('candidates', []),
        'review_alternatives': job['result'].get('review_alternatives', []),
        'rejected_candidates': job['result'].get('rejected_candidates', []),
        'editorial_workflow_state': job['editorial_workflow_state'],
    }


class EditorialFeedbackRequest(BaseModel):
    candidate_index: int | None = Field(default=None, ge=0, le=100)
    decision: Literal[
        'approved',
        'needs_trim',
        'needs_different_layout',
        'incorrect_event',
        'unrelated_scenes',
        'missing_payoff',
        'weak_hook',
        'reject',
    ]
    rejection_reasons: list[str] = Field(default_factory=list, max_length=10)
    original_start: float | None = None
    original_end: float | None = None
    corrected_start: float | None = None
    corrected_end: float | None = None
    original_score: float | None = None
    selected_layout: str | None = Field(default=None, max_length=60)
    edited_hook: str | None = Field(default=None, max_length=150)
    notes: str = Field(default='', max_length=500)


@app.post('/api/jobs/{jid}/feedback', status_code=201)
def post_feedback(jid: str, body: EditorialFeedbackRequest):
    get_job(jid)
    return store.record_feedback(jid, body.model_dump())


@app.get('/api/jobs/{jid}/feedback')
def get_job_feedback(jid: str):
    get_job(jid)
    return store.list_feedback(jid)


@app.get('/api/feedback/summary')
def get_feedback_summary():
    return store.feedback_summary()


@app.get('/api/production/metrics')
def get_production_metrics():
    return store.production_summary()


@app.get('/api/jobs/{jid}/events')
def events(jid:str):
    get_job(jid)
    return store.events(jid)


@app.post('/api/jobs/{jid}/cancel')
def cancel(jid:str):
    store.cancel(jid)
    return get_job(jid)


@app.post('/api/jobs/{jid}/retry')
def retry(jid:str):
    if not store.retry(jid):
        raise HTTPException(409,'Only failed, blocked or cancelled jobs can be retried')
    return get_job(jid)


class EditSegment(BaseModel):
    id: int = Field(ge=1, le=30)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    transition: Literal['cut','fade'] = 'cut'


class EditPlanUpdate(BaseModel):
    revision: int = Field(ge=1)
    segments: list[EditSegment] = Field(min_length=1, max_length=30)
    target_duration: float | None = Field(default=None,ge=10,le=3600,allow_inf_nan=False)


@app.get('/api/jobs/{jid}/edit-plan')
def get_edit_plan(jid: str):
    plan = get_job(jid)['result'].get('edit_plan')
    if not plan:
        raise HTTPException(404,'No edit plan available')
    return plan


def save_edit_plan(jid, body, approve=False):
    try:
        return store.edit_plan(jid,body.revision,[item.model_dump() for item in body.segments],
                               body.target_duration,approve)
    except KeyError:
        raise HTTPException(404,'Job not found') from None
    except InterruptedError as error:
        raise HTTPException(409,str(error)) from None
    except ValueError as error:
        raise HTTPException(422,str(error)) from None


@app.patch('/api/jobs/{jid}/edit-plan')
def revise_edit_plan(jid: str, body: EditPlanUpdate):
    return save_edit_plan(jid,body)


@app.post('/api/jobs/{jid}/render')
def render_plan(jid: str, body: EditPlanUpdate):
    return save_edit_plan(jid,body,approve=True)


class Approval(BaseModel):
    approved:bool


@app.post('/api/jobs/{jid}/outputs/{index}/approve')
def approve(jid:str,index:int,body:Approval):
    try:
        if index<0:
            raise ValueError('Invalid output')
        store.approve(jid,index,body.approved)
    except (ValueError,IndexError):
        raise HTTPException(409,'Output unavailable or not complete') from None
    return get_job(jid)


@app.post('/api/jobs/{jid}/metadata')
def generate_metadata(jid:str, force_overwrite:bool=False):
    from .drafts import regenerate
    from .inference import HEAVY_LOCK
    if not HEAVY_LOCK.acquire(False):raise HTTPException(409,'The processing slot is busy. Metadata regeneration can run after the current GPU task finishes.')
    try:return regenerate(jid, force_overwrite=force_overwrite)
    except (ValueError,KeyError,IndexError) as error:raise HTTPException(409,str(error)) from None
    finally:HEAVY_LOCK.release()


@app.post('/api/jobs/{jid}/outputs/{index}/hooks')
def suggest_hooks(jid:str,index:int):
    from .drafts import contexts_for_job
    from .hooks import suggest
    from .heavy import HEAVY_LOCK
    if not HEAVY_LOCK.acquire(False):raise HTTPException(409,'Wait for the current processing stage')
    try:
        job=get_job(jid)
        if index<0:raise ValueError('Invalid output')
        ctx=contexts_for_job(job)[index]
        return suggest(ctx['transcript'],ctx['metadata_language'],job['request'].get('llm_model'),ctx['source_title'])
    except (ValueError,IndexError,KeyError) as error:raise HTTPException(409,str(error)) from None
    finally:HEAVY_LOCK.release()


class MetadataEdit(BaseModel):
    title:str|None=Field(default=None,min_length=1,max_length=100)
    alternative_titles:list[str]|None=None
    description:str|None=Field(default=None,min_length=1,max_length=5000)
    tags:list[str]|None=None
    hashtags:list[str]|None=None
    category_id:str|None=None
    metadata_language:str|None=None
    thumbnail_text:str|None=Field(default=None,max_length=60)


@app.patch('/api/jobs/{jid}/outputs/{index}/metadata')
def edit_metadata(jid:str,index:int,body:MetadataEdit):
    from .drafts import revise
    try:return revise(jid,index,body.model_dump(exclude_none=True))
    except (ValueError,KeyError,IndexError) as error:raise HTTPException(409,str(error)) from None


@app.get('/api/jobs/{jid}/outputs/{index}/metadata/history')
def metadata_history(jid:str,index:int):
    get_job(jid)
    return store.metadata_history(jid,index)


@app.get('/api/jobs/{jid}/files/{name}')
def output(jid:str,name:str,request:Request):
    job=get_job(jid)
    manifests={o['name']:o.get('drive') for o in job['result'].get('outputs',[])}
    for item in job['result'].get('outputs',[]):
        if item.get('thumbnail'):
            manifests[item['thumbnail']['name']]=item['thumbnail'].get('drive')
    manifests.update(job['result'].get('artifacts',{}))
    edit_manifests = {segment['edit_manifest'] for segment in job['result'].get('timeline', {}).get('segments', [])
                      if re.fullmatch(r'clip-\d+\.manifest\.json', segment.get('edit_manifest', ''))}
    allowed=set(manifests)|edit_manifests|{'transcript.json','transcript.srt','transcript.vtt','timeline.json'}
    if name not in allowed:
        raise HTTPException(404,'Artifact not found')
    root=config.SCRATCH/jid
    path=root/name
    if path.is_file():
        return FileResponse(path)
    remote=manifests.get(name)
    if not remote:
        raise HTTPException(410,'Local artifact expired or is not available; inspect the job and Drive link')
    from .drive import Drive,BASE,CHUNK
    size=int(remote['size'])
    first,last=0,size-1
    ranged=request.headers.get('Range')
    if ranged:
        match=re.fullmatch(r'bytes=(\d+)-(\d*)',ranged)
        if not match:
            raise HTTPException(416,'Use a single forward byte range')
        first=int(match.group(1))
        last=min(int(match.group(2)) if match.group(2) else size-1,first+CHUNK-1,size-1)
        if first>last:
            raise HTTPException(416,'Range outside artifact')
    drive=Drive()
    context=drive.client.stream('GET',BASE+'/files/'+remote['id'],params={'alt':'media','supportsAllDrives':'true'},headers={'Range':f'bytes={first}-{last}'})
    response=context.__enter__()
    if response.status_code!=206 or response.headers.get('Content-Range')!=f'bytes {first}-{last}/{size}':
        context.__exit__(None,None,None)
        drive.close()
        raise HTTPException(502,'Drive did not honor the requested artifact range')
    def chunks():
        try:
            remaining=last-first+1
            for chunk in response.iter_bytes(256*1024):
                if len(chunk)>remaining:
                    raise ValueError('Drive artifact exceeded verified size')
                remaining-=len(chunk)
                yield chunk
        finally:
            context.__exit__(None,None,None)
            drive.close()
    headers={'Accept-Ranges':'bytes','Content-Length':str(last-first+1)}
    if ranged:
        headers['Content-Range']=f'bytes {first}-{last}/{size}'
    return StreamingResponse(chunks(),status_code=206 if ranged else 200,headers=headers,media_type=mimetypes.guess_type(name)[0] or 'application/octet-stream')


class Worker(BaseModel):
    worker:str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    resources:dict=Field(default_factory=dict)


@app.post('/api/workers/heartbeat')
def heartbeat(body:Worker):
    store.heartbeat(body.worker,body.resources)
    return {'ok':True}


@app.post('/api/workers/claim')
def claim(body:Worker):
    store.heartbeat(body.worker,body.resources)
    claimed=store.claim(body.worker,remote=True)
    if claimed:
        from .assets import manifest
        try:
            result=claimed['job']['result']
            if 'asset_manifest' not in result:
                result['asset_manifest']=manifest(claimed['job']['request'])
                store.update(claimed['job']['id'],claimed['lease'],result={'asset_manifest':result['asset_manifest']})
            claimed['assets']=result['asset_manifest']
        except ValueError as error:
            store.update(claimed['job']['id'],claimed['lease'],state='BLOCKED',error=str(error))
            return None
    return claimed


@app.get('/api/workers/assets/{kind}/{name}')
def worker_asset(kind:str,name:str,sha256:str):
    import hashlib
    from .assets import asset_path
    try:path=asset_path(kind,name)
    except ValueError:raise HTTPException(404,'Approved asset unavailable') from None
    with path.open('rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
    if not hmac.compare_digest(digest,sha256):
        raise HTTPException(409,'Asset changed; retry to fetch the current approved version')
    return FileResponse(path)


class Checkpoint(BaseModel):
    lease:str=Field(max_length=64)
    state:str|None=None
    result:dict|None=None
    error:str|None=Field(default=None,max_length=500)
    current:float|None=None
    total:float|None=None
    stage_update:dict|None=None


@app.post('/api/workers/jobs/{jid}')
def checkpoint(jid:str,body:Checkpoint):
    states={'QUEUED','PROBING','DOWNLOADING','TRANSCRIBING','ANALYZING','RENDERING','UPLOADING','READY','FAILED','BLOCKED','REVIEW'}
    if body.state and body.state not in states:
        raise HTTPException(422,'Invalid worker state')
    try:
        store.update(jid,**body.model_dump())
    except InterruptedError:
        raise HTTPException(409,'Worker lease lost or job cancelled') from None
    except ValueError as error:
        raise HTTPException(422,str(error)) from None
    if body.state=='READY':
        from .retention import register
        register(store.get(jid))
    return {'ok':True}


@app.get('/api/publisher')
def publisher():
    from .publisher import channels, history
    from .youtube_channels import profiles
    return {'channels':channels(),'publications':history(),'profiles':profiles()}


@app.post('/api/publisher/discover')
def discover_channels():
    from .publisher import discover_channels
    return discover_channels()


@app.get('/api/youtube/profiles')
def youtube_profiles():
    from .youtube_channels import profiles
    return profiles()


@app.post('/api/youtube/profiles/{key}/verify')
def youtube_verify(key: str):
    from .youtube_channels import PROFILES, discover, profiles
    if key not in PROFILES:
        raise HTTPException(404,'Unknown managed YouTube profile')
    discover(key)
    return next(row for row in profiles() if row['channel_key'] == key)


class YouTubeProfileState(BaseModel):
    enabled: bool


@app.post('/api/youtube/profiles/{key}/state')
def youtube_profile_state(key: str, body: YouTubeProfileState):
    from .youtube_channels import PROFILES, enable, profiles
    if key not in PROFILES:
        raise HTTPException(404,'Unknown managed YouTube profile')
    try:
        enable(key,body.enabled)
    except ValueError as error:
        raise HTTPException(409,str(error)) from None
    return next(row for row in profiles() if row['channel_key'] == key)


class YouTubeLegacyBinding(BaseModel):
    alias: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')


@app.post('/api/youtube/profiles/{key}/legacy-binding')
def youtube_legacy_binding(key: str, body: YouTubeLegacyBinding):
    from .youtube_channels import PROFILES, discover, profiles
    if key not in PROFILES:
        raise HTTPException(404,'Unknown managed YouTube profile')
    try:
        discover(key,legacy_alias=body.alias)
    except ValueError as error:
        raise HTTPException(409,str(error)) from None
    profile = next(row for row in profiles() if row['channel_key'] == key)
    if profile['status'] != 'CONNECTED':
        raise HTTPException(409,profile['error'] or 'Verified legacy channel binding failed')
    return profile


@app.get('/api/analytics')
def analytics_dashboard():
    from .analytics import snapshot
    return snapshot()


@app.post('/api/analytics/discover')
def discover_analytics(profile_key: Literal['leonida_mayhem', 'thevicescope'] | None = None):
    from .analytics import discover
    return discover(profile_key)


@app.post('/api/analytics/{channel}/sync')
def sync_analytics(channel:str):
    from .analytics import start_sync
    try:return start_sync(channel)
    except ValueError as error:raise HTTPException(409,str(error)) from None


class ChannelPreferences(BaseModel):
    profile_name:str=Field(default='',max_length=100)
    target_region:str=Field(default='',max_length=20)
    content_category:str=Field(default='auto',max_length=40)
    editing_preset:str=Field(default='auto',pattern=r'^(auto|smart|crop|blur|gameplay)$')
    metadata_language:str|None=Field(default=None,pattern=r'^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$')
    category_id:str|None=Field(default=None,pattern=r'^\d{1,3}$')
    description_footer:str=Field(default='',max_length=500)
    review_first:bool=True
    auto_approve:bool=False
    max_silence_fraction:float=Field(default=.65,ge=.1,le=.8)
    timezone:str=Field(default='Europe/Kyiv',max_length=80)


class RetentionPolicy(BaseModel):
    enabled:bool=False
    maximum_packages:int=Field(default=50,ge=1,le=10000)
    grace_days:int=Field(default=7,ge=1,le=365)


@app.get('/api/storage/retention')
def retention_status():
    from .retention import snapshot
    return snapshot()


@app.post('/api/storage/retention/settings')
def retention_settings(body:RetentionPolicy):
    from .retention import configure
    return configure(body.model_dump())


@app.post('/api/storage/retention/run')
def retention_run(dry_run:bool=True):
    from .retention import cleanup
    try:return cleanup(dry_run)
    except ValueError as error:raise HTTPException(409,str(error)) from None


@app.post('/api/storage/retention/{jid}/protect')
def retention_protect(jid:str,body:Approval):
    from .retention import protect
    try:protect(jid,body.approved)
    except ValueError as error:raise HTTPException(404,str(error)) from None
    return {'protected':body.approved}


class Policy(BaseModel):
    daily_limit:int=Field(default=3,ge=1,le=20)
    public_allowed:bool=False
    automatic:bool=False
    interval_minutes:int=Field(default=120,ge=30,le=1440)
    preferences:ChannelPreferences|None=None


@app.post('/api/publisher/channels/{channel}/policy')
def policy(channel:str,body:Policy):
    from .publisher import set_policy
    try:
        if body.preferences:
            from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
            try:ZoneInfo(body.preferences.timezone)
            except (ZoneInfoNotFoundError,ValueError):raise ValueError('Choose a valid IANA timezone, for example Europe/Kyiv') from None
            from .metadata import CATEGORIES
            if body.preferences.category_id and body.preferences.category_id not in CATEGORIES:raise ValueError('Choose a supported YouTube category')
        set_policy(channel,**body.model_dump())
        return {'ok':True}
    except ValueError as error:
        raise HTTPException(409,str(error)) from None


class Publication(BaseModel):
    job:str=Field(pattern=r'^[a-f0-9]{32}$')
    output:int=Field(ge=0)
    channel:str=Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    title:str|None=Field(default=None,min_length=1,max_length=100)
    description:str|None=Field(default=None,max_length=5000)
    privacy:str='private'
    publish_at:str|None=None
    publish_at_local:str|None=None


@app.post('/api/publisher/queue')
def queue_publication(body:Publication):
    from .publisher import enqueue
    values=body.model_dump()
    local=values.pop('publish_at_local')
    values['jid']=values.pop('job')
    values['index']=values.pop('output')
    try:
        if local:
            if values['publish_at']:raise ValueError('Choose either UTC or channel-local time')
            from .calendar import local_time
            values['publish_at']=local_time(local,store.preferences(body.channel).get('timezone','Europe/Kyiv'))
        return {'id':enqueue(**values)}
    except ValueError as error:
        raise HTTPException(409,str(error)) from None


@app.post('/api/publisher/{pid}/retry')
def retry_publication(pid:str):
    from .publisher import retry
    if not retry(pid):
        raise HTTPException(409,'Only failed uploads can resume. An UNCERTAIN outcome requires YouTube Studio reconciliation.')
    return {'ok':True}


@app.post('/api/publisher/{pid}/reconcile')
def reconcile_publication(pid:str):
    from .publisher import reconcile
    try:
        reconcile(pid)
    except ValueError as error:
        raise HTTPException(409,str(error)) from None
    return {'ok':True}


class PublicationEdit(BaseModel):
    title:str=Field(min_length=1,max_length=100)
    description:str=Field(default='',max_length=5000)
    privacy:str='private'
    publish_at:str|None=None


@app.patch('/api/publisher/{pid}')
def edit_publication(pid:str,body:PublicationEdit):
    from .publisher import edit
    try:edit(pid,**body.model_dump())
    except ValueError as error:raise HTTPException(409,str(error)) from None
    return {'ok':True}
