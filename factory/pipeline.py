"""One checkpointed clipping pipeline for local and authenticated remote workers."""
import hashlib
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


def source_plan(source, request, root, progress):
    progress.begin('source','PROBING',message=source if source.startswith('upload:') else None)
    checkpoint = root/'source.json'
    if checkpoint.is_file():
        metadata = json.loads(checkpoint.read_text(encoding='utf-8'))
    else:
        metadata = media.probe(source)
        write_json_atomic(checkpoint, metadata, ensure_ascii=True)
    end = min(request.end or metadata['duration'],metadata['duration'])
    if end <= request.start:
        raise ValueError('Selected interval begins after the end of the source')
    if end-request.start > 86400:
        raise ValueError('Processing is bounded to 24 hours per source; select a shorter interval')
    available = [r for r in metadata['resolutions'] if request.resolution=='best' or r<=request.resolution]
    if media.local_source(source):
        height = max(metadata['resolutions'])
        available = [height if request.resolution=='best' else min(height,request.resolution)]
    if not available:
        raise ValueError('No source resolution at or below the selected quality is available')
    progress.done()
    return metadata,end,max(available)


def media_signals(source, request, metadata, end, resolution, root, progress):
    from .signals import scan

    path = root/'signals.json'
    if path.is_file():
        data = json.loads(path.read_text(encoding='utf-8'))
        return data['events'],data['scenes'],data['coverage']
    events,scenes,coverage = scan(source,request,metadata,end,resolution,root,progress.tick)
    write_json_atomic(path,{'events':events,'scenes':scenes,'coverage':coverage})
    return events,scenes,coverage


def analysis_input(source, request, root, end, resolution, progress):
    local = media.local_source(source)
    if local is not None:
        return local, request
    local = root / 'connected-source.mp4'
    if not local.is_file():
        partial = root / 'connected-source.partial.mp4'
        media.download(source, partial, request.start, end, resolution, False, progress.tick)
        partial.replace(local)
    return local, request.model_copy(update={'start': 0})


def compilation_plan(request, root, progress, update):
    from .compilation import select
    from .shorts import plan_v8

    sources = {}
    groups = []
    for index, item in enumerate(request.sources,1):
        key = f'source-{index}'
        folder = root/key
        folder.mkdir(exist_ok=True)
        options = request.model_copy(update={'source':item.source,'start':item.start,'end':item.end})
        try:
            metadata,end,resolution = source_plan(item.source,options,folder,progress)
            checkpoint = folder/'analysis.json'
            if checkpoint.is_file():
                result = json.loads(checkpoint.read_text(encoding='utf-8'))
            else:
                transcript = transcribe_chunks(item.source,options,folder,metadata,end,resolution,progress,lambda **_:None)
                progress.begin('highlights','ANALYZING',message=f'Source {index}/{len(request.sources)}')
                if request.workflow_version == 2:
                    local, analysis_options = analysis_input(item.source, options, folder, end, resolution, progress)
                    result = plan_v8(local, analysis_options, transcript, end-item.start, root=folder,
                                     source_title=metadata.get('title', ''), progress=progress.tick,
                                     include_activity=True)
                else:
                    from .signals import propose
                    events, scenes, coverage = media_signals(item.source, options, metadata, end, resolution, folder, progress)
                    visual = propose(events, scenes, options, end-item.start, metadata.get('title'))
                    result = analyze(transcript, options, end-item.start, progress.tick, visual)
                    result['signal_coverage'] = coverage
                write_json_atomic(checkpoint,result,ensure_ascii=True)
                del transcript
                progress.done()
        except (ValueError, RuntimeError) as error:
            update(result={'failed_source_id':key})
            if isinstance(error, media.MediaFailure):
                raise media.MediaFailure(error.code, f'Source {index}: {error}', error.retryable) from error
            raise
        sources[key] = {'source':item.source,'metadata':metadata,'resolution':resolution,'end':end,
                        'signal_coverage':result.get('signal_coverage')}
        groups.append([{**c,'source_id':key,'source_offset':item.start}
                       for c in result['candidates']])
        update(result={'sources':sources,'failed_source_id':None})
    target = request.target_duration or 900
    selected,duration = select(groups,request.count,target)
    result = {'candidates':selected, 'analysis':'source_aware_compilation',
              'selection_summary':f'Selected {duration:.1f}s for a {target:g}s target from {len({c["source_id"] for c in selected})} sources.',
              'target_duration':target,'planned_duration':round(duration,3),
              'duration_shortfall':duration<target*.95}
    if result['duration_shortfall']:
        result['selection_summary'] += ' Add more suitable material or adjust the target/clip constraints.'
    return sources,result


def transcript_artifacts(root, transcript, duration):
    from subtitles import generate_srt

    srt = root/'transcript.srt'
    if not srt.is_file():
        if not generate_srt(transcript,0,duration,str(srt)):
            srt.write_text('',encoding='utf-8')
    vtt = root/'transcript.vtt'
    if not vtt.is_file():
        with srt.open(encoding='utf-8-sig') as source, vtt.open('w',encoding='utf-8') as output:
            output.write('WEBVTT\n\n')
            for line in source:
                output.write(re.sub(r'(\d\d:\d\d:\d\d),(\d{3})',r'\1.\2',line))


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
            (root/f'audio-{offset}.mka').unlink(missing_ok=True)
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
        compiling = request.workflow=='compilation'
        source_url=request.source if request.source.startswith('https://') else origin_job['result'].get('source_url') if request.source.startswith('edit:') else None
        if compiling:
            source_url = None
            sources,result = compilation_plan(request,root,progress,update)
            metadata = {'title':'Highlights compilation','platform':'compilation'}
            update(result={'source':metadata,'source_url':None})
            end = request.start + result['planned_duration']
        else:
            metadata,end,resolution = source_plan(source,request,root,progress)
            if request.workflow=='story' and request.story_mode=='chronological':
                from .timeline import story_part_count
                story_part_count(request,end-request.start)
            update(result={'source':metadata,'source_url':source_url,'actual_resolution':resolution,'source_start':request.start,'processing_end':end,'ingest_strategy':'audio-first bounded chunks' if metadata.get('independent_audio',True) else 'bounded muxed segments: independent audio stream unavailable'})
        background=config.DATA/'backgrounds'/request.background if request.background else next(iter(sorted((config.DATA/'backgrounds').glob('*.mp4'))),None)
        if request.background_mode=='none':background=None
        if request.background_mode=='random':
            import random
            choices=sorted((config.DATA/'backgrounds').glob('*.mp4'))
            background=random.Random(jid).choice(choices) if choices else None
        if assets is not None:background=assets.get('backgrounds')
        if request.background_mode=='none':background=None
        if request.background and request.background_mode!='none' and (not background or not background.is_file()):
            raise NeedsReview('Selected secondary asset is unavailable. Choose another asset explicitly.')
        if request.mode=='shorts' and request.preset=='gameplay' and (not background or not background.is_file()):raise NeedsReview('Add an authorized background asset or select Automatic / Blurred Background')
        if compiling:
            transcript = {'language':None,'segments':[]}
        else:
            transcript=transcribe_chunks(source,request,root,metadata,end,resolution,progress,update)
            progress.begin('language','TRANSCRIBING')
            update(result={'language':transcript['language']})
            progress.done(message='Detected '+str(transcript['language']) if transcript['language'] else 'No speech detected')
            transcript_artifacts(root,transcript,end-request.start)
        progress.begin('highlights','ANALYZING')
        render_source, render_offset = source, request.start
        analysis_checkpoint=root/'analysis.json'
        from .shorts import identity, plan
        modern = request.mode == 'shorts' and (request.selection_version == 2 or request.series_mode == 'sequential')
        duration = end-request.start
        selection_id = identity(request,duration,transcript) if modern else None
        frozen = json.loads(analysis_checkpoint.read_text(encoding='utf-8')) if analysis_checkpoint.is_file() else {}
        if compiling:
            candidates = result['candidates']
        elif frozen and (not modern or frozen.get('selection_id') == selection_id):
            result=frozen;candidates=result['candidates']
        elif job['result'].get('candidates') and (not modern or job['result'].get('selection_id') == selection_id):
            # Another worker may own the old scratch volume. The controller's
            # saved selection still fences clip identity across that handoff.
            result={key:job['result'][key] for key in ('candidates','analysis','model','analysis_outcome','analysis_diagnostics','selection_summary','selection_id','series_applied','signal_coverage') if key in job['result']}
            candidates=result['candidates']
        elif request.intervals:
            if any(b>end for a,b in request.intervals):raise ValueError('Manual interval extends beyond the actual source duration')
            candidates=[{'start_seconds':a-request.start,'end_seconds':b-request.start,'title':metadata['title']} for a,b in (request.intervals if request.mode=='longs' or modern else sorted(request.intervals))]
            result={'candidates':candidates,'analysis':'manual chronological intervals'}
        elif request.workflow=='story' and request.story_mode=='chronological':
            from .timeline import story
            _, scenes, coverage = media_signals(source,request,metadata,end,resolution,root,progress)
            candidates,summary = story(transcript,request,duration,metadata['title'],scenes)
            result = {'candidates':candidates,'analysis':'chronological_source_split','selection_summary':summary,'series_applied':True,'signal_coverage':coverage}
        elif modern:
            local_path=media.local_source(source)
            analysis_request = request
            if local_path is None and (request.series_mode == 'connected' or request.workflow == 'highlights'):
                local_path, analysis_request = analysis_input(source, request, root, end, resolution, progress)
                render_source, render_offset = local_path, 0
            use_visual = request.series_mode != 'sequential' and (
                not transcript['segments'] or request.series_mode == 'connected' or
                request.source_category in ('visual','gameplay','stream','auto')
            )
            if local_path and request.series_mode != 'sequential':
                from .shorts import plan_v8
                result = plan_v8(
                    local_path,
                    analysis_request,
                    transcript,
                    duration,
                    root=root,
                    source_title=metadata.get('title', ''),
                    progress=progress.tick,
                    include_activity=request.workflow == 'highlights',
                )
            else:
                features = []
                if use_visual:
                    from .visual_analysis import sample
                    features = sample(source,request.start,end,root,progress.tick)
                result=plan(transcript,request,duration,features,progress.tick)
            candidates=result['candidates']
        elif request.workflow == 'highlights':
            from .signals import propose
            events, scenes, coverage = media_signals(source, request, metadata, end, resolution, root, progress)
            visual = propose(events, scenes, request, duration, metadata.get('title'))
            result = analyze(transcript, request, duration, progress.tick, visual)
            result['signal_coverage'] = coverage
            candidates = result['candidates']
        elif not transcript['segments'] and request.minimum<=end-request.start<=request.maximum and request.audio_mode=='silent':
            candidates=[{'start_seconds':0,'end_seconds':end-request.start,'title':metadata['title']}]
            result={'candidates':candidates,'analysis':'whole short visual source; no AI claim'}
        else:
            result=analyze(transcript,request,end-request.start,progress.tick);candidates=result['candidates']
            if request.mode=='longs':candidates.sort(key=lambda c:c['start_seconds'])
        if not modern and request.series_mode=='connected' and not result.get('series_applied'):
            from .timeline import connected
            candidates,summary=connected(candidates,request,end-request.start)
            result.update(candidates=candidates,selection_summary=summary,series_applied=True)
        if request.mode=='longs' and not compiling and request.target_duration and not analysis_checkpoint.is_file() and not request.intervals:
            selected=[];remaining=request.target_duration
            for candidate in candidates:
                if remaining<request.minimum:break
                item=dict(candidate)
                length=item['end_seconds']-item['start_seconds']
                if length>remaining:
                    item['end_seconds']=item['start_seconds']+remaining;item['editorial_review_required']=True
                selected.append(item);remaining-=min(length,remaining)
            candidates=selected;result['candidates']=candidates
        if modern:
            result['selection_id']=selection_id
            if request.intervals:
                from .shorts import planned, PLATFORM_MAXIMUM
                if any(c['end_seconds']-c['start_seconds'] > min(request.maximum,PLATFORM_MAXIMUM) for c in candidates):
                    raise NeedsReview('A manual Shorts interval exceeds the configured or platform maximum.')
                candidates=job['result'].get('approved_selection') or [
                    planned(c['start_seconds'],c['end_seconds'],c['title'],'Operator-selected source interval.',transcript,'source_sequence')
                    for c in candidates
                ]
                result.update(candidates=candidates,selection_summary=f'{len(candidates)} operator-selected source intervals.')
            if frozen and frozen.get('selection_id') != selection_id:
                for pattern in ('clip-*', 'selected-*'):
                    for stale in root.glob(pattern):
                        if stale.is_file():stale.unlink()
                result['outputs']=[]
        if candidates and not compiling:write_json_atomic(analysis_checkpoint,result,ensure_ascii=True)
        cached_source = root / 'connected-source.mp4'
        if cached_source.is_file() and (request.series_mode == 'connected' or request.workflow == 'highlights'):
            render_source, render_offset = cached_source, 0
        update(result=result)
        if candidates:
            progress.done()
        else:
            progress.done(status='blocked', message='Analysis finished with zero selected clips. Review the selection diagnostics.')
        if modern and not candidates:raise NeedsReview(result.get('selection_summary','No usable source interval was found.'))
        if request.plan_only:
            boundaries=[s['end'] for s in transcript['segments'] if s['text'].rstrip().endswith(('.', '?', '!'))]
            update('READY',result={'planning_only':True,'experiment_boundaries':boundaries})
            return
        if not candidates:raise NeedsReview('No suitable spoken highlight. '+('Visual/no-speech source: choose Silent Video with a short selected interval or set manual intervals.' if not transcript['segments'] else 'Inspect the suitability explanation or select a manual interval in Editor.'))
        if request.review_before_render and request.workflow in ('highlights','compilation'):
            review = job['result'].get('edit_plan')
            if review and not review['approved']:
                update('REVIEW')
                return
            if not review:
                review_sources = ({key:{'title':item['metadata']['title'],'source':item['source'],'end':item['end']}
                                   for key,item in sources.items()} if compiling else
                                  {'source-1':{'title':metadata['title'],'source':request.source,'end':end}})
                review = {'version':1,'revision':1,'approved':False,'sources':review_sources,
                          'target_duration':result.get('target_duration'),
                          'segments':[{**candidate,'id':i+1,'source_id':candidate.get('source_id','source-1'),
                                       'source_offset':candidate.get('source_offset',request.start),
                                       'transition':request.transition} for i,candidate in enumerate(candidates)]}
                update('REVIEW',result={'edit_plan':review})
                return
            candidates = review['segments']
            result['candidates'] = candidates
            if compiling:
                selected = sum(c['end_seconds']-c['start_seconds'] for c in candidates)
                target = review['target_duration']
                result.update(planned_duration=round(selected,3),target_duration=target,
                              duration_shortfall=selected<target*.95,
                              selection_summary=f'Edited plan: {selected:.1f}s for a {target:g}s target.')
            update(result=result)
        outputs=[];raw_sources={}
        last_source = None
        compilation_position = 0
        compilation_ranges = []
        if request.mode=='longs':
            from .timeline import manifest
            write_json_atomic(root/'timeline.json',manifest(candidates,request),ensure_ascii=True)
        for i,candidate in enumerate(candidates):
            clip_request=request.model_copy()
            if modern and (request.series_mode != 'independent' or candidate.get('visual_review_required')):
                clip_request.silence_removal=False
            clip_request.transition = candidate.get('transition',request.transition)
            clip_transcript = transcript
            clip_source = render_source
            offset = render_offset
            if compiling:
                from .compilation import append_transcript
                key = candidate['source_id']
                clip_source = sources[key]['source']
                resolution = sources[key]['resolution']
                offset = candidate['source_offset']
                cached_input = root/key/'connected-source.mp4'
                if cached_input.is_file():
                    clip_source, offset = cached_input, 0
                if key!=last_source:
                    source_transcript = json.loads((root/key/'transcript.json').read_text(encoding='utf-8'))
                    last_source = key
                clip_transcript = source_transcript
                length = candidate['end_seconds']-candidate['start_seconds']
                append_transcript(transcript,clip_transcript,candidate['start_seconds'],candidate['end_seconds'],compilation_position)
                compilation_ranges.append({**candidate,'start_seconds':compilation_position,'end_seconds':compilation_position+length})
                compilation_position += length
            if request.mode=='longs' and i<len(candidates)-1:clip_request.cta_text=''
            if request.mode=='longs' and i>0:clip_request.hook=''
            hook_candidates=None
            if request.text_mode in ('static_hook','hook_subtitles') and request.hook_mode!='none' and not request.hook and (request.mode!='longs' or i==0):
                from .hooks import suggest
                progress.begin('hooks','ANALYZING',message=f'Clip {i+1}/{len(candidates)}')
                evidence=' '.join(s['text'] for s in clip_transcript['segments'] if s['end']>candidate['start_seconds'] and s['start']<candidate['end_seconds'])
                hook_candidates=suggest(evidence,request.metadata_language or clip_transcript['language'] or 'en',request.llm_model,sources[key]['metadata']['title'] if compiling else metadata['title'],request.confirmed_subject,request.clip_observation)
                if hook_candidates['method']!='neutral_fallback':
                    clip_request.hook=hook_candidates['candidates'][0]['text']
                progress.done(message=hook_candidates['method'])
            duration=candidate['end_seconds']-candidate['start_seconds']
            output=root/f'clip-{i+1}.mp4';clip=root/f'selected-{i+1}.mp4'
            if request.workflow:
                checkpoint = output.with_suffix('.checkpoint.json')
                source_key = {'id': candidate.get('source_id', 'source-1'),
                              'reference': sources[key]['source'] if compiling else request.source,
                              'start': offset + candidate['start_seconds'],
                              'end': offset + candidate['end_seconds'], 'resolution': resolution}
                source_path = media.local_source(clip_source)
                if source_path is not None:
                    from .source_intelligence import file_fingerprint
                    source_key['fingerprint'] = file_fingerprint(source_path)
                raw_key = hashlib.sha256(json.dumps(source_key, sort_keys=True).encode()).hexdigest()
                render_input = {'version': 1, 'source': raw_key, 'request': clip_request.model_dump(mode='json'),
                                'background': str(background or ''), 'candidate': candidate}
                render_key = hashlib.sha256(json.dumps(render_input, sort_keys=True).encode()).hexdigest()
                prior = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.is_file() else {}
                if prior.get('source') != raw_key:
                    clip.unlink(missing_ok=True)
                if prior.get('render') != render_key:
                    for stale in (output, output.with_suffix('.jpg'), output.with_suffix('.validation.json')):
                        stale.unlink(missing_ok=True)
                write_json_atomic(checkpoint, {'source': raw_key, 'render': render_key})
            progress.begin('retrieval','RENDERING',message=f'Clip {i+1}/{len(candidates)}')
            if clip.is_file():
                try:media.validate_output(clip,duration)
                except Exception:clip.unlink()
            if not clip.is_file():
                if compiling:
                    temporary = clip.with_suffix('.input.mp4')
                    try:
                        media.download(clip_source,temporary,offset+candidate['start_seconds'],offset+candidate['end_seconds'],resolution,False,progress.tick)
                        media.normalize_compilation(temporary,clip,duration,request.output_height,progress.tick)
                    finally:
                        temporary.unlink(missing_ok=True)
                else:
                    media.download(clip_source,clip,offset+candidate['start_seconds'],offset+candidate['end_seconds'],resolution,False,progress.tick)
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
            if not output.is_file():
                media.render(
                    clip, output, clip_transcript, candidate['start_seconds'], candidate['end_seconds'],
                    clip_request, background, progress.tick, stage=render_stage,
                    music_path=assets.get('music') if assets is not None else None,
                    logo_path=assets.get('logos') if assets is not None else None,
                    candidate_meta=candidate,
                )
            progress.done('validation')
            thumbnail=output.with_suffix('.jpg')
            from .thumbnails import create as make_thumbnail
            thumbnail_report=make_thumbnail(output,thumbnail,tick=progress.tick) if not thumbnail.is_file() else {'method':'reused_existing_frame'}
            validation=json.loads(output.with_suffix('.validation.json').read_text()) if output.with_suffix('.validation.json').is_file() else {}
            outputs.append({'name':output.name,'title':candidate['title'],'approved':False,'duration':validation.get('duration',duration),'edit_duration':duration,'thumbnail':{'name':thumbnail.name,**thumbnail_report},'validation':validation})
            with output.open('rb') as rendered:digest=hashlib.file_digest(rendered,'sha256').hexdigest()
            outputs[-1].update(render_id=f'{jid}:{i+1}',render_version=1,output_hash=digest,hook=clip_request.hook,hook_candidates=hook_candidates,series={'id':jid,'title':candidate.get('series_title',candidate['title']),'part':candidate['part'],'parts':candidate['parts'],'previous_part':candidate['part']-1 if candidate['part']>1 else None,'next_part':candidate['part']+1 if candidate['part']<candidate['parts'] else None} if candidate.get('part') else None)
            if modern:
                outputs[-1]['edit_configuration'] = {
                    'selection_strategy': 'reviewed_source_intervals' if request.intervals else request.series_mode,
                    'selection_id': selection_id,
                    'source_start': request.start + candidate['start_seconds'],
                    'source_end': request.start + candidate['end_seconds'],
                    'duration': outputs[-1]['duration'], 'layout': validation.get('framing'),
                    'experiment_parent':request.experiment_parent,
                    'experiment_variant':next((name for name,low,high in (
                        ('short',20,30),('standard',35,45),('extended',50,60)
                    ) if low<=duration<=high),None) if request.experiment_parent else None,
                    'hook_type': clip_request.text_mode, 'secondary_asset': request.background,
                    'render_version': 1, 'channel_profile': request.publish_channel,
                }
            outputs[-1].update(job['result'].get('edit_revision', {}))
            update(result={'outputs':outputs,'clips_completed':i+1,'clips_total':len(candidates)})
        if compiling:
            transcript['text'] = ' '.join(s['text'] for s in transcript['segments'])
            write_json_atomic(root/'transcript.json',transcript,ensure_ascii=True)
            transcript_artifacts(root,transcript,compilation_position)
            update(result={'language':transcript['language']})
        if request.mode=='longs' or request.workflow:
            from .timeline import manifest
            timeline = manifest(candidates, request)
            combined = request.mode == 'longs' and (compiling or len(outputs) > 1)
            position = 0
            for segment, output in zip(timeline['segments'], outputs):
                segment['output_name'] = 'long.mp4' if combined else output['name']
                segment['edit_manifest'] = output['name'].replace('.mp4', '.manifest.json')
                segment['segment_output_hash'] = output['output_hash']
                segment['output_start_ms'] = round(position * 1000) if combined else 0
                position += output['duration']
                segment['output_end_ms'] = (round(position * 1000) if combined
                                            else round(output['duration'] * 1000))
            timeline['duration_ms'] = round(position * 1000)
            timeline['plan_revision'] = job['result'].get('edit_plan', {}).get('revision')
            if compiling:
                timeline['target_duration_ms'] = round(result['target_duration'] * 1000)
            write_json_atomic(root/'timeline.json', timeline, ensure_ascii=True)
            update(result={'timeline': timeline})
        if request.mode=='longs' and (compiling or len(outputs)>1):
            final=root/'long.mp4';media.concatenate([root/o['name'] for o in outputs],final,progress.tick)
            validation=media.validate_output(final,sum(o['duration'] for o in outputs),request=request)
            raw=root/'long-source.mp4';media.concatenate(list(raw_sources.values()),raw,progress.tick)
            raw_sources[final.name]=raw
            thumb=final.with_suffix('.jpg');thumbnail_report=make_thumbnail(final,thumb,tick=progress.tick)
            outputs=[{'name':final.name,'title':metadata['title'],'approved':False,'duration':validation['duration'],'edit_duration':sum(c['end_seconds']-c['start_seconds'] for c in candidates),'validation':validation,'thumbnail':{'name':thumb.name,**thumbnail_report}}]
            with final.open('rb') as rendered:digest=hashlib.file_digest(rendered,'sha256').hexdigest()
            outputs[0].update(render_id=f'{jid}:long',render_version=1,output_hash=digest)
            outputs[0].update(job['result'].get('edit_revision', {}))


        if compiling:
            actual = outputs[0]['duration']
            target = result['target_duration']
            update(result={'actual_duration':actual,'duration_shortfall':actual<target*.95,
                           'within_target_duration':target*.95<=actual<=target*1.05})
        from . import metadata as publishing_metadata
        from .editorial import assess
        evidence_ranges = compilation_ranges if compiling else candidates
        contexts=[publishing_metadata.context(transcript,request,metadata,evidence_ranges if request.mode=='longs' else evidence_ranges[i:i+1],output,source_url) for i,output in enumerate(outputs)]
        previous=[o.get('publishing_metadata') for o in job['result'].get('outputs',[])]
        progress.begin('metadata','ANALYZING',len(outputs),'clips')
        drafts=publishing_metadata.generate_outputs(contexts,request.llm_model,previous,progress.tick)
        if request.series_mode=='connected' and drafts:
            for output in outputs:
                if output.get('series'):
                    output['series']['title']=drafts[0]['title'].rsplit(' · ',1)[0]
        progress.done();progress.begin('editorial','ANALYZING',len(outputs),'clips')
        assessed=[]
        for i,(output,draft,ctx) in enumerate(zip(outputs,drafts,contexts)):
            output['publishing_metadata']=draft;output['title']=draft['title']
            if draft.get('thumbnail_text'):
                output['thumbnail'].update(make_thumbnail(root/output['name'],root/output['thumbnail']['name'],draft['thumbnail_text'],progress.tick))
            output['editorial']=assess(output,transcript,evidence_ranges if request.mode=='longs' else evidence_ranges[i:i+1],request,draft,assessed,request.metadata_preferences)
            assessed.append(output['editorial']);progress.tick(i+1,len(outputs))
        progress.done()
        update(result={'outputs':outputs,'publishing_state':'AWAITING_EDITORIAL_REVIEW'})
        metadata_obj={'job_id':jid,'request':request.model_dump(),'source':metadata,'language':transcript['language'],'candidates':candidates,'outputs':outputs}
        if request.save_drive:
            artifact_paths=[root/'transcript.json',root/'metadata.json',*root.glob('timeline.json'),*root.glob('*.srt'),*root.glob('*.vtt'),*root.glob('*.ass'),*root.glob('*.validation.json'),*root.glob('clip-*.manifest.json')]
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
        try:
            diag = result.get('analysis_diagnostics', {})
            vlm_st = diag.get('vlm_stats', {}) if isinstance(diag, dict) else {}
            store.record_production_metrics(jid, {
                'candidate_count': len(candidates),
                'approved_count': sum(1 for o in outputs if o.get('approved')),
                'model_calls': int(diag.get('requests', 0)) if isinstance(diag, dict) else 0,
                'vlm_calls': int(vlm_st.get('calls_made', 0)),
                'peak_vram_mib': int(vlm_st.get('peak_vram_mib', 0)),
            })
        except Exception:
            pass
        if request.save_drive:
            from .retention import register
            register(store.get(jid))
    except InterruptedError:return
    except Exception as error:
        retryable = isinstance(error, media.MediaFailure) and error.retryable and stage!='UPLOADING'
        if isinstance(error, media.MediaFailure):
            code = error.code
        elif isinstance(error, NeedsReview):
            code = 'EDITORIAL_REVIEW_REQUIRED'
        elif isinstance(error, ValueError):
            code = 'INVALID_INPUT'
        else:
            code = 'STAGE_FAILED'
        safe=(str(error) if isinstance(error,(ValueError,RuntimeError,TimeoutError))
              else f'{type(error).__name__} in {stage}; inspect configuration and retry')[:500]
        failure={'code':code,'stage':progress.active or stage,'message':safe,'retryable':retryable}
        try:
            progress.done(status='blocked' if isinstance(error, NeedsReview) else 'failed',message=safe)
            state = 'QUEUED' if retryable else 'BLOCKED' if stage=='UPLOADING' or isinstance(error,NeedsReview) else 'FAILED'
            update(state,error=safe,result={'failure':failure})
        except InterruptedError:pass
    finally:
        cancel_event.set();heartbeat.join(timeout=1)
