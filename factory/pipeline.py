"""One checkpointed clipping pipeline for local and authenticated remote workers."""
import json
import math
import os
import re
import sys
import threading
from . import config,store,media
from .models import JobRequest
from .highlights import analyze
from .resources import preflight
from .cleanup import remove_workspace
from .progress import Progress
from jsonfile import write_json_atomic


class NeedsReview(ValueError):
    pass


def transcribe_chunks(source,request,root,metadata,end,resolution,progress,update):
    path=root/'transcript.json'
    if path.is_file():
        progress.done('extraction',message='Reused transcript checkpoint');progress.done('transcription',message='Reused transcript checkpoint')
        return json.loads(path.read_text(encoding='utf-8'))
    duration=end-request.start
    segments=[];language=request.language
    silent_short=request.audio_mode=='silent' and not request.captions and request.minimum<=duration<=request.maximum
    if metadata.get('has_audio',True) and not (request.intervals and not request.captions) and not silent_short:
        # Keep the original chunk size on retry: changing it would reuse partial
        # transcripts for different intervals. Pre-manifest checkpoints used 900s.
        plan=root/'asr-chunks.json'
        if plan.is_file():
            chunk_seconds=int(json.loads(plan.read_text(encoding='utf-8'))['seconds'])
            if not 60<=chunk_seconds<=900:raise ValueError('Invalid ASR chunk checkpoint')
        else:
            legacy=any(p.stem.removeprefix('transcript-').isdigit() for p in root.glob('transcript-*.json'))
            chunk_seconds=900 if legacy else config.AUDIO_CHUNK_SECONDS
            write_json_atomic(plan,{'seconds':chunk_seconds})
        chunks=math.ceil(duration/chunk_seconds)
        for index in range(chunks):
            offset=index*chunk_seconds;chunk_end=min(offset+chunk_seconds,duration)
            partial=root/f'transcript-{offset}.json'
            if not partial.is_file():
                progress.begin('extraction','DOWNLOADING',message=f'Audio chunk {index+1}/{chunks}')
                audio=root/f'audio-{offset}.mka'
                media.download(source,audio,request.start+offset,request.start+chunk_end,resolution,True,progress.tick)
                progress.begin('transcription','TRANSCRIBING',duration,'seconds',f'Chunk {index+1}/{chunks}')
                progress.tick(offset,duration)
                def asr_tick(current=None,total=None):progress.tick(offset+current if current is not None else None,duration)
                media.run([sys.executable,'-m','factory.transcribe',str(audio),str(partial),language or 'auto'],asr_tick,duration=chunk_end-offset,env={**os.environ,'WHISPER_MODEL':request.asr_model or config.ASR_MODEL})
                audio.unlink(missing_ok=True)
            part=json.loads(partial.read_text(encoding='utf-8'))
            if part['segments']:language=language or part.get('language')
            for segment in part['segments']:
                segment['start']+=offset;segment['end']+=offset
                for word in segment.get('words',[]):
                    word['start']+=offset;word['end']+=offset
                segments.append(segment)
            update(result={'audio_chunks_completed':index+1,'audio_chunks_total':chunks})
            progress.tick(chunk_end,duration)
        progress.done('extraction');progress.done('transcription')
    else:
        progress.done('extraction','skipped','No audio, manual intervals without captions, or an explicitly silent short source')
        progress.done('transcription','skipped','No speech analysis required')
    transcript={'language':language if segments or request.language else None,'segments':segments,'text':' '.join(s['text'] for s in segments)}
    write_json_atomic(path,transcript,ensure_ascii=True)
    return transcript


def process(claim,assets=None):
    job=claim['job'];jid=job['id'];lease=claim['lease']
    root=config.SCRATCH/jid;stage='PROBING'
    cancel_event=threading.Event()
    def update(state=None,**kwargs):
        nonlocal stage
        if state:stage=state
        store.update(jid,lease,state=state,**kwargs)
    progress=Progress(update)
    def renew():
        while not cancel_event.wait(15):
            try:store.update(jid,lease)
            except Exception:return
    heartbeat=threading.Thread(target=renew,daemon=True);heartbeat.start()
    try:
        request=JobRequest.model_validate(job['request'])
        root.mkdir(parents=True,exist_ok=True)
        if job['result'].get('cloud_verified'):
            remove_workspace(root);update('READY');return
        preflight();source=request.source
        if source.startswith('edit:'):
            _,origin,index=source.split(':')
            origin_job=store.get(origin)
            original=origin_job['result']['outputs'][int(index)]
            if not original.get('edit_source'):raise NeedsReview('This older output has no preserved editable source. Re-upload the original once.')
            editable=original['edit_source']
            if editable.get('local'):
                import shutil
                name=editable['local']
                if not re.fullmatch(r'(selected-\d+|long-source)\.mp4',name):raise ValueError('Invalid preserved local source')
                path=config.SCRATCH/origin/name
                preflight(path.stat().st_size)
                shutil.copyfile(path,root/'edit-source.mp4')
            else:media.fetch_drive(editable,root/'edit-source.mp4',progress.tick)
            source='job-local:'+jid
        source_url=request.source if request.source.startswith('https://') else origin_job['result'].get('source_url') if request.source.startswith('edit:') else None
        progress.begin('source','PROBING')
        metadata=media.probe(source);end=min(request.end or metadata['duration'],metadata['duration'])
        if end<=request.start:raise ValueError('Selected interval begins after the end of the source')
        if end-request.start>86400:raise ValueError('Processing is bounded to 24 hours per job; select a shorter source interval')
        available=[r for r in metadata['resolutions'] if request.resolution=='best' or r<=request.resolution]
        if media.local_source(source):available=[max(metadata['resolutions']) if request.resolution=='best' else min(max(metadata['resolutions']),request.resolution)]
        if not available:raise ValueError('No source resolution at or below the selected quality is available')
        resolution=max(available)
        update(result={'source':metadata,'source_url':source_url,'actual_resolution':resolution,'source_start':request.start,'processing_end':end,'ingest_strategy':'audio-first bounded chunks' if metadata.get('independent_audio',True) else 'bounded muxed segments: independent audio stream unavailable'})
        progress.done()
        background=config.DATA/'backgrounds'/request.background if request.background else next(iter(sorted((config.DATA/'backgrounds').glob('*.mp4'))),None)
        if request.background_mode=='none':background=None
        if request.background_mode=='random':
            import random
            choices=sorted((config.DATA/'backgrounds').glob('*.mp4'))
            background=random.Random(jid).choice(choices) if choices else None
        if assets is not None:background=assets.get('backgrounds')
        if request.background_mode=='none':background=None
        if request.preset=='gameplay' and (not background or not background.is_file()):raise NeedsReview('Add an authorized background asset or select Automatic / Blurred Background')
        transcript=transcribe_chunks(source,request,root,metadata,end,resolution,progress,update)
        progress.begin('language','TRANSCRIBING');update(result={'language':transcript['language']});progress.done(message='Detected '+str(transcript['language']) if transcript['language'] else 'No speech detected')
        from subtitles import generate_srt
        if not generate_srt(transcript,0,end-request.start,str(root/'transcript.srt')):(root/'transcript.srt').write_text('',encoding='utf-8')
        srt=(root/'transcript.srt').read_text(encoding='utf-8')
        (root/'transcript.vtt').write_text('WEBVTT\n\n'+re.sub(r'(\d\d:\d\d:\d\d),(\d{3})',r'\1.\2',srt.lstrip('\ufeff')),encoding='utf-8')
        progress.begin('highlights','ANALYZING')
        analysis_checkpoint=root/'analysis.json'
        if analysis_checkpoint.is_file():
            result=json.loads(analysis_checkpoint.read_text(encoding='utf-8'));candidates=result['candidates']
        elif job['result'].get('candidates'):
            # Another worker may own the old scratch volume. The controller's
            # saved selection still fences clip identity across that handoff.
            result={key:job['result'][key] for key in ('candidates','analysis','model','analysis_outcome','analysis_diagnostics','selection_summary') if key in job['result']}
            candidates=result['candidates']
        elif request.intervals:
            if any(b>end for a,b in request.intervals):raise ValueError('Manual interval extends beyond the actual source duration')
            candidates=[{'start_seconds':a-request.start,'end_seconds':b-request.start,'title':metadata['title']} for a,b in (request.intervals if request.mode=='longs' else sorted(request.intervals))]
            result={'candidates':candidates,'analysis':'manual chronological intervals'}
        elif not transcript['segments'] and request.minimum<=end-request.start<=request.maximum and request.audio_mode=='silent':
            candidates=[{'start_seconds':0,'end_seconds':end-request.start,'title':metadata['title']}]
            result={'candidates':candidates,'analysis':'whole short visual source; no AI claim'}
        else:
            result=analyze(transcript,request,end-request.start,progress.tick);candidates=result['candidates']
            if request.mode=='longs':candidates.sort(key=lambda c:c['start_seconds'])
        if request.series_mode=='connected' and not result.get('series_applied'):
            from .timeline import connected
            candidates,summary=connected(candidates,request,end-request.start)
            result.update(candidates=candidates,selection_summary=summary,series_applied=True)
        if request.mode=='longs' and request.target_duration and not analysis_checkpoint.is_file() and not request.intervals:
            selected=[];remaining=request.target_duration
            for candidate in candidates:
                if remaining<request.minimum:break
                item=dict(candidate)
                length=item['end_seconds']-item['start_seconds']
                if length>remaining:
                    item['end_seconds']=item['start_seconds']+remaining;item['editorial_review_required']=True
                selected.append(item);remaining-=min(length,remaining)
            candidates=selected;result['candidates']=candidates
        if candidates:write_json_atomic(analysis_checkpoint,result,ensure_ascii=True)
        update(result=result);progress.done()
        if not candidates:raise NeedsReview('No suitable spoken highlight. '+('Visual/no-speech source: choose Silent Video with a short selected interval or set manual intervals.' if not transcript['segments'] else 'Inspect the suitability explanation or select a manual interval in Editor.'))
        outputs=[];raw_sources={}
        if request.mode=='longs':
            from .timeline import manifest
            write_json_atomic(root/'timeline.json',manifest(candidates,request),ensure_ascii=True)
        for i,candidate in enumerate(candidates):
            clip_request=request.model_copy()
            if request.mode=='longs' and i<len(candidates)-1:clip_request.cta_text=''
            if request.mode=='longs' and i>0:clip_request.hook=''
            hook_candidates=None
            if request.text_mode in ('static_hook','hook_subtitles') and not request.hook and (request.mode!='longs' or i==0):
                from .hooks import suggest
                progress.begin('hooks','ANALYZING',message=f'Clip {i+1}/{len(candidates)}')
                evidence=' '.join(s['text'] for s in transcript['segments'] if s['end']>candidate['start_seconds'] and s['start']<candidate['end_seconds'])
                hook_candidates=suggest(evidence,request.metadata_language or transcript['language'] or 'en',request.llm_model,metadata['title'])
                clip_request.hook=hook_candidates['candidates'][0]['text']
                progress.done(message=hook_candidates['method'])
            duration=candidate['end_seconds']-candidate['start_seconds']
            output=root/f'clip-{i+1}.mp4';clip=root/f'selected-{i+1}.mp4'
            progress.begin('retrieval','RENDERING',message=f'Clip {i+1}/{len(candidates)}')
            if clip.is_file():
                try:media.validate_output(clip,duration)
                except Exception:clip.unlink()
            if not clip.is_file():media.download(source,clip,request.start+candidate['start_seconds'],request.start+candidate['end_seconds'],resolution,False,progress.tick)
            raw_sources[output.name]=clip;progress.done()
            def render_stage(key):
                if progress.active in ('rendering','captions_audio'):progress.done()
                progress.begin(key,'RENDERING',duration if key=='rendering' else None,'seconds' if key=='rendering' else None,f'Clip {i+1}/{len(candidates)}')
            if output.is_file():
                try:
                    checkpoint=json.loads(output.with_suffix('.validation.json').read_text(encoding='utf-8'))
                    refreshed=media.validate_output(output,checkpoint['duration'],request=request)
                    output.with_suffix('.validation.json').write_text(json.dumps({**checkpoint,**refreshed}),encoding='utf-8')
                    progress.done('captions_audio','skipped','Reused validated render');progress.done('rendering',message='Reused validated render')
                    progress.begin('validation','RENDERING')
                except Exception:output.unlink()
            if not output.is_file():media.render(clip,output,transcript,candidate['start_seconds'],candidate['end_seconds'],clip_request,background,progress.tick,stage=render_stage,music_path=assets.get('music') if assets is not None else None,logo_path=assets.get('logos') if assets is not None else None)
            progress.done('validation')
            thumbnail=output.with_suffix('.jpg')
            from .thumbnails import create as make_thumbnail
            thumbnail_report=make_thumbnail(output,thumbnail,tick=progress.tick) if not thumbnail.is_file() else {'method':'reused_existing_frame'}
            validation=json.loads(output.with_suffix('.validation.json').read_text()) if output.with_suffix('.validation.json').is_file() else {}
            outputs.append({'name':output.name,'title':candidate['title'],'approved':False,'duration':validation.get('duration',duration),'edit_duration':duration,'thumbnail':{'name':thumbnail.name,**thumbnail_report},'validation':validation})
            import hashlib
            with output.open('rb') as rendered:digest=hashlib.file_digest(rendered,'sha256').hexdigest()
            outputs[-1].update(render_id=f'{jid}:{i+1}',render_version=1,output_hash=digest,hook=clip_request.hook,hook_candidates=hook_candidates,series={'id':jid,'title':candidate.get('series_title',candidate['title']),'part':candidate['part'],'parts':candidate['parts'],'previous_part':candidate['part']-1 if candidate['part']>1 else None,'next_part':candidate['part']+1 if candidate['part']<candidate['parts'] else None} if candidate.get('part') else None)
            outputs[-1].update(job['result'].get('edit_revision', {}))
            update(result={'outputs':outputs,'clips_completed':i+1,'clips_total':len(candidates)})
        if request.mode=='longs':
            timeline=manifest(candidates,request);position=0
            for segment,output in zip(timeline['segments'],outputs):
                segment['output_start_ms']=round(position*1000)
                position+=output['duration'];segment['output_end_ms']=round(position*1000)
            timeline['duration_ms']=round(position*1000)
            write_json_atomic(root/'timeline.json',timeline,ensure_ascii=True)
        if request.mode=='longs' and len(outputs)>1:
            final=root/'long.mp4';media.concatenate([root/o['name'] for o in outputs],final,progress.tick)
            validation=media.validate_output(final,sum(o['duration'] for o in outputs),request=request)
            raw=root/'long-source.mp4';media.concatenate(list(raw_sources.values()),raw,progress.tick)
            raw_sources[final.name]=raw
            thumb=final.with_suffix('.jpg');thumbnail_report=make_thumbnail(final,thumb,tick=progress.tick)
            outputs=[{'name':final.name,'title':metadata['title'],'approved':False,'duration':validation['duration'],'edit_duration':sum(c['end_seconds']-c['start_seconds'] for c in candidates),'validation':validation,'thumbnail':{'name':thumb.name,**thumbnail_report}}]
            with final.open('rb') as rendered:digest=hashlib.file_digest(rendered,'sha256').hexdigest()
            outputs[0].update(render_id=f'{jid}:long',render_version=1,output_hash=digest)
            outputs[0].update(job['result'].get('edit_revision', {}))
        from . import metadata as publishing_metadata
        from .editorial import assess
        contexts=[publishing_metadata.context(transcript,request,metadata,candidates if request.mode=='longs' else candidates[i:i+1],output,source_url) for i,output in enumerate(outputs)]
        previous=[o.get('publishing_metadata') for o in job['result'].get('outputs',[])]
        progress.begin('metadata','ANALYZING',len(outputs),'clips')
        drafts=publishing_metadata.generate_outputs(contexts,request.llm_model,previous,progress.tick)
        if request.series_mode=='connected' and drafts:
            for output in outputs:output['series']['title']=drafts[0]['title'].rsplit(' · ',1)[0]
        progress.done();progress.begin('editorial','ANALYZING',len(outputs),'clips')
        assessed=[]
        for i,(output,draft,ctx) in enumerate(zip(outputs,drafts,contexts)):
            output['publishing_metadata']=draft;output['title']=draft['title']
            if draft.get('thumbnail_text'):
                output['thumbnail'].update(make_thumbnail(root/output['name'],root/output['thumbnail']['name'],draft['thumbnail_text'],progress.tick))
            output['editorial']=assess(output,transcript,candidates if request.mode=='longs' else candidates[i:i+1],request,draft,assessed,request.metadata_preferences)
            assessed.append(output['editorial']);progress.tick(i+1,len(outputs))
        progress.done()
        update(result={'outputs':outputs,'publishing_state':'AWAITING_EDITORIAL_REVIEW'})
        metadata_obj={'job_id':jid,'request':request.model_dump(),'source':metadata,'language':transcript['language'],'candidates':candidates,'outputs':outputs}
        if request.save_drive:
            artifact_paths=[root/'transcript.json',root/'metadata.json',*root.glob('timeline.json'),*root.glob('*.srt'),*root.glob('*.vtt'),*root.glob('*.ass'),*root.glob('*.validation.json')]
            total_files=sum(1+bool(o.get('thumbnail'))+int(o['name'] in raw_sources) for o in outputs)+sum(p.name=='metadata.json' or (p.is_file() and p.stat().st_size>0) for p in artifact_paths)
            progress.begin('drive','UPLOADING',total_files,'files');progress.tick(0,total_files)
            from .drive import Drive
            drive=Drive()
            try:
                folders=drive.job_folders(jid);uploaded=[]
                uploaded_files=0
                def upload_file(path,folder,key):
                    nonlocal uploaded_files
                    progress.counters['drive']['message']='Verifying '+path.name
                    def bytes_tick(current=None,total=None):update(result={'upload_asset':{'name':path.name,'current':current,'total':total}})
                    remote=drive.upload(path,folder,key,bytes_tick)
                    uploaded_files+=1;progress.tick(uploaded_files,total_files)
                    return remote
                for output in outputs:
                    remote=upload_file(root/output['name'],folders[request.mode],jid+'-'+output['name'])
                    if output.get('thumbnail'):
                        thumb=output['thumbnail']['name'];output['thumbnail']['drive']=upload_file(root/thumb,folders['thumbnails'],jid+'-'+thumb)
                    if output['name'] in raw_sources:
                        raw=raw_sources[output['name']];output['edit_source']=upload_file(raw,folders['sources'],jid+'-edit-'+output['name'])
                    uploaded.append({**output,'drive':remote});update(result={'outputs':uploaded+outputs[len(uploaded):]})
                metadata_obj['outputs']=uploaded
                write_json_atomic(root/'metadata.json',metadata_obj,ensure_ascii=True)
                artifacts={}
                for path in artifact_paths:
                    if not path.stat().st_size:continue
                    folder=folders['job'] if path.suffix=='.json' else folders['subtitles']
                    artifacts[path.name]=upload_file(path,folder,jid+'-'+path.name);update(result={'artifacts':artifacts})
                update(result={'outputs':uploaded,'artifacts':artifacts,'cloud_verified':True})
            finally:drive.close()
            progress.done();remove_workspace(root)
            if request.source.startswith('upload:'):
                original=config.SCRATCH/'uploads'/request.source[7:]
                if original.is_file() and not store.source_in_use(request.source,jid):original.unlink()
        else:
            for output in outputs:
                if output['name'] in raw_sources:output['edit_source']={'local':raw_sources[output['name']].name}
            update(result={'outputs':outputs})
            write_json_atomic(root/'metadata.json',metadata_obj,ensure_ascii=True);progress.done('drive','skipped','Local export selected')
        update('READY')
        if request.save_drive:
            from .retention import register
            register(store.get(jid))
    except InterruptedError:return
    except Exception as error:
        safe=str(error) if isinstance(error,(ValueError,RuntimeError,TimeoutError)) else f'{type(error).__name__} in {stage}; inspect configuration and retry'
        try:
            progress.done(status='failed',message=safe[:500]);update('BLOCKED' if stage=='UPLOADING' or isinstance(error,NeedsReview) else 'FAILED',error=safe[:500])
        except InterruptedError:pass
    finally:
        cancel_event.set();heartbeat.join(timeout=1)
