"""Bounded ingest and rendering, using OpenShorts caption/encoder modules."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import signal
import textwrap
from collections import deque
from urllib.parse import urlsplit
from . import config
from .resources import preflight


class MediaFailure(RuntimeError):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def ffmpeg():
    if os.environ.get('FFMPEG_BINARY'):
        return os.environ['FFMPEG_BINARY']
    if shutil.which('ffmpeg'):
        return shutil.which('ffmpeg')
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args, tick=lambda *_:None, duration=None, timeout=7200, env=None, cwd=None):
    group={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env=env,cwd=cwd,**group)
    # Backpressure bounds logs even if a child is noisy or progress persistence
    # is slow. readline's size also bounds a child that never writes a newline.
    lines=queue.Queue(maxsize=128)
    stopped=threading.Event()
    def enqueue(line):
        while not stopped.is_set():
            try:
                lines.put(line,timeout=.1)
                return True
            except queue.Full:pass
        return False
    def read():
        try:
            while not stopped.is_set():
                line=process.stdout.readline(4096)
                if not line or not enqueue(line):break
        finally:enqueue(None)
    thread=threading.Thread(target=read,daemon=True)
    thread.start()
    started=time.monotonic()
    next_check=started
    tail=deque(maxlen=12)
    progress=None
    eof=False
    try:
        while True:
            now=time.monotonic()
            if now-started > timeout:
                raise MediaFailure('MEDIA_TIMEOUT', 'Media operation timed out; check the source connection or shorten the interval.', True)
            if now>=next_check:
                preflight()
                tick(None,duration)
                next_check=time.monotonic()+1
            if eof:
                if process.poll() is not None:break
                time.sleep(.1)
                continue
            try:
                line=lines.get(timeout=.25)
            except queue.Empty:
                continue
            if line is None:
                eof=True
                continue
            tail.append(line)
            if line.startswith('out_time_us='):
                try:
                    progress=float(line.split('=')[1])/1e6
                    tick(progress,duration)
                except ValueError:
                    pass
            elif 'Transcribing' in line and duration:
                import re
                measured=re.search(r'Transcribing.*?(\d+)%',line)
                if measured:
                    progress=min(100,int(measured[1]))/100*duration
                    tick(progress,duration)
        if process.returncode != 0:
            # Do not return raw downloader logs: URLs may carry credentials.
            diagnostic=' '.join(tail).casefold()
            code='MEDIA_PROCESS_FAILED';retryable=False
            if any(value in diagnostic for value in ('cuda out of memory','cuda_error_out_of_memory')):
                code,reason='GPU_MEMORY', 'GPU memory exhausted; use a smaller model or wait for GPU memory to be released'
            elif 'out of memory' in diagnostic or 'cannot allocate memory' in diagnostic:
                code,reason='SYSTEM_MEMORY', 'System/container memory exhausted; shorten the chunk or choose a smaller model'
            elif 'no space left on device' in diagnostic:
                code,reason='DISK_SPACE', 'Disk space exhausted; free scratch storage and retry'
            elif any(value in diagnostic for value in ('http error 429','429 too many requests','server returned 429')):
                code,reason,retryable='DOWNLOAD_RATE_LIMITED','Source rate limited the request; wait before retrying',True
            elif any(value in diagnostic for value in ('connection timed out','connection reset','temporarily unavailable','http error 503','server returned 5')):
                code,reason,retryable='SOURCE_UNAVAILABLE','Source connection was interrupted; verify the connection if retries fail',True
            elif any(value in diagnostic for value in ('downloaded range is empty', 'downloaded range is not decodable', 'downloaded range contains no decodable media')):
                code,reason,retryable='SOURCE_RANGE_INCOMPLETE','Source interval returned empty or incomplete media; check VOD availability',True
            elif process.returncode in (-9,137):
                code,reason='MEDIA_PROCESS_KILLED','Process killed; inspect container OOM diagnostics and available RAM before retrying'
            else:
                reason='Check codec support, source accessibility and available disk space'
            raise MediaFailure(code, f'Media process failed (exit {process.returncode}). {reason}. Checkpoints preserved.', retryable)
        preflight()
        tick(None,duration)
    finally:
        stopped.set()
        if process.poll() is None:
            if os.name=='nt':
                try:
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=10)
                except subprocess.TimeoutExpired:pass
                # Restricted Windows sessions may reject taskkill. Still reap
                # the direct child rather than waiting forever after cancellation.
                if process.poll() is None:process.kill()
            else:
                os.killpg(process.pid,signal.SIGKILL)
        process.wait()
        thread.join(timeout=2)
        if not thread.is_alive():process.stdout.close()


def local_source(source):
    if isinstance(source, Path):
        return source if source.is_file() else None
    if source.startswith('job-local:'):
        import re
        jid=source.split(':',1)[1]
        if not re.fullmatch('[a-f0-9]{32}',jid):raise ValueError('Invalid editable source')
        return config.SCRATCH/jid/'edit-source.mp4'
    if source.startswith('upload:'):
        name=source[7:]
        if not name or Path(name).name != name:
            raise ValueError('Invalid uploaded source identifier')
        path=config.SCRATCH/'uploads'/name
        if not path.is_file():
            raise ValueError('Uploaded source is missing')
        return path
    return None


def fetch_drive(metadata,path,tick=lambda *_:None):
    import hashlib
    from .drive import Drive,BASE,CHUNK
    if path.is_file():
        with path.open('rb') as source:
            if hashlib.file_digest(source,'md5').hexdigest()==metadata['md5Checksum']:return path
    preflight(int(metadata['size']))
    drive=Drive()
    try:
        offset=0;total=int(metadata['size'])
        with path.open('wb') as output:
            while offset<total:
                end=min(total,offset+CHUNK)-1
                with drive.client.stream('GET',BASE+'/files/'+metadata['id'],params={'alt':'media','supportsAllDrives':'true'},headers={'Range':f'bytes={offset}-{end}'}) as response:
                    if response.status_code!=206 or response.headers.get('Content-Range')!=f'bytes {offset}-{end}/{total}':raise ValueError('Drive did not honor bounded editable-source retrieval')
                    count=0
                    for data in response.iter_bytes(256*1024):
                        count+=len(data)
                        if count>end-offset+1:raise ValueError('Drive response exceeded the requested range')
                        preflight(len(data));output.write(data)
                    if count!=end-offset+1:raise ValueError('Incomplete editable-source transfer; retry safely')
                offset=end+1;tick(offset,total)
        with path.open('rb') as source:
            if hashlib.file_digest(source,'md5').hexdigest()!=metadata['md5Checksum']:raise ValueError('Editable source checksum mismatch')
    finally:drive.close()
    return path


def validate_url(url):
    host=urlsplit(url).hostname or ''
    allowed=('youtube.com','youtu.be','twitch.tv')
    if urlsplit(url).scheme != 'https' or not any(host==h or host.endswith('.'+h) for h in allowed) or urlsplit(url).username:
        raise ValueError('Use an HTTPS YouTube/completed Twitch VOD URL or upload an MP4/MKV/MOV')
    from security_utils import assert_public_url
    assert_public_url(url)


def probe(source):
    path=local_source(source)
    if path:
        import av
        with av.open(str(path)) as c:
            video=next(iter(c.streams.video),None)
            if video is None or c.duration is None:
                raise ValueError('Source must have a video stream and known duration')
            return {'title':path.stem,'platform':'local','duration':c.duration/av.time_base,'resolutions':[video.height],'size':path.stat().st_size,'has_audio':bool(c.streams.audio)}
    validate_url(source)
    import yt_dlp
    import ssrf_guard
    try:
        with ssrf_guard.guarded(), yt_dlp.YoutubeDL({'quiet':True,'noplaylist':True,'socket_timeout':20,'skip_download':True}) as ydl:
            item=ydl.extract_info(source,download=False)
    except yt_dlp.utils.DownloadError as error:
        detail=str(error).casefold()
        if '429' in detail and ('http' in detail or 'too many requests' in detail):
            raise MediaFailure('DOWNLOAD_RATE_LIMITED','Source rate limited the request; wait before retrying.',True) from None
        if any(word in detail for word in ('timed out','connection reset','http error 503')):
            raise MediaFailure('SOURCE_UNAVAILABLE','Could not reach the source. Check the connection.',True) from None
        raise MediaFailure('SOURCE_ACCESS_FAILED','Could not access the media. Verify the URL, privacy and source availability.') from None
    if item.get('is_live') or item.get('live_status') in ('is_live','is_upcoming'):
        raise ValueError('Only completed VODs are supported')
    if not item.get('duration'):
        raise ValueError('Unknown source duration; bounded processing cannot be guaranteed')
    formats=item.get('formats',[])
    return {'title':item.get('title'),'platform':item.get('extractor_key'),'duration':item['duration'],'resolutions':sorted({int(f['height']) for f in formats if f.get('height') and f.get('vcodec') not in ('none',None,'images') and f.get('ext')!='mhtml'}),'size':item.get('filesize'),'thumbnail':item.get('thumbnail'),'has_audio':any(f.get('acodec') not in ('none',None) for f in formats),'independent_audio':any(f.get('vcodec')=='none' and f.get('acodec') not in ('none',None) for f in formats)}


def download(source,path,start,end,resolution,audio=False,tick=lambda *_:None):
    """Retrieve a bounded interval from a validated URL or internal local file."""
    preflight()
    local=source if isinstance(source,Path) else local_source(source)
    if local:
        args=[ffmpeg(),'-hide_banner','-loglevel','error','-y','-threads','2','-ss',str(start),'-i',str(local),'-t',str(end-start),'-threads','2','-filter_threads','1']
        if audio:
            args+=['-vn','-ac','1','-ar','16000','-c:a','pcm_s16le']
        else:
            args+=['-vf',f'scale=-2:min(ih\\,{resolution})','-c:v','libx264','-preset','fast','-c:a','aac']
        run(args+['-progress','pipe:1',str(path)],tick,end-start)
        return path
    validate_url(source)
    # yt-dlp's range downloader uses remote FFmpeg seeking. No unbounded retry.
    # A separate process allows the parent to enforce scratch/lease/time limits.
    import sys
    fmt='bestaudio/best' if audio else f'bv*[height<={resolution}]+ba/b[height<={resolution}]'
    args=[sys.executable,'-m','factory.range_download',source,str(path),str(start),str(end),fmt,ffmpeg()]
    run(args,tick,end-start)
    return path


def render(source,output,transcript,start,end,request,background=None,tick=lambda *_:None,stage=None,music_path=None,logo_path=None,candidate_meta=None,design_preset=None):
    from .heavy import HEAVY_LOCK
    with HEAVY_LOCK:
        return _render(source,output,transcript,start,end,request,background,tick,stage,music_path,logo_path,candidate_meta=candidate_meta,design_preset=design_preset)


def _render(source,output,transcript,start,end,request,background=None,tick=lambda *_:None,stage=None,music_path=None,logo_path=None,candidate_meta=None,design_preset=None):
    from subtitles import generate_ass, generate_srt
    from ffmpeg_utils import video_encode_args, escape_filter_value, LOUDNORM_FILTER
    from .edit_manifest import build_edit_manifest, facecam_crop_filter
    # Single-pass loudnorm ramps up slowly when a clip starts with quiet speech or ambient
    # dialogue (leaving Part 1 ~4 LU quieter than Part 2). A gentle dynaudnorm pre-stage
    # levels speech/action dynamics before loudnorm, and the final alimiter prevents AAC
    # inter-sample peak overshoot with makeup gain disabled.
    audio_filter=f'dynaudnorm=f=250:g=15:p=0.9:m=10.0,{LOUDNORM_FILTER},aformat=sample_rates=48000:channel_layouts=stereo,alimiter=limit=0.707945:level=false:latency=true'
    duration=end-start
    transcript={
        'language':transcript.get('language'),
        'segments':[segment for segment in transcript['segments']
                    if segment['end']>start and segment['start']<end],
    }
    import av
    with av.open(str(source)) as container:
        has_audio=bool(container.streams.audio)
        vstream=next(iter(container.streams.video),None)
        src_w=int(vstream.width) if vstream and vstream.width else 1280
        src_h=int(vstream.height) if vstream and vstream.height else 720
    if stage:stage('captions_audio')
    if request.silence_removal and request.audio_mode!='voiceover':
        from .audio_edit import remove_pauses
        from .audio_edit import speech_timeline
        _, _, _, trimmed_duration = speech_timeline(transcript,start,end)
        floor = min(duration,request.minimum) if request.mode=='shorts' and request.selection_version == 2 else 0
        if trimmed_duration >= floor:
            source,transcript,start,end=remove_pauses(source,output.with_suffix('.trimmed.mp4'),transcript,start,end,has_audio,tick)
            duration=end-start
    narration=None
    caption_start,caption_end=start,end
    if request.audio_mode=='voiceover':
        from .tts import synthesize
        text=request.tts_text or ' '.join(s['text'] for s in transcript['segments'] if s['end']>start and s['start']<end)
        narration=output.with_suffix('.voice.wav')
        synthesize(text,request.tts_voice,request.tts_speed,transcript.get('language') or request.language or 'en',narration,tick)
        with av.open(str(narration)) as audio_container:
            voice_duration=audio_container.duration/av.time_base
        if voice_duration>duration+.1:
            raise ValueError('Voice-over is longer than the clip. Shorten its text or select a longer interval; words were not truncated.')
        if request.captions:
            import sys
            timed=output.with_suffix('.voice.json')
            run([sys.executable,'-m','factory.transcribe',str(narration),str(timed),transcript.get('language') or request.language or 'en'],tick)
            transcript=json.loads(timed.read_text(encoding='utf-8'))
            caption_start,caption_end=0,duration
    ass=output.with_suffix('.ass')
    srt=output.with_suffix('.srt')
    if request.captions and request.subtitle_language and transcript.get('language')!=request.subtitle_language:
        from .captions import translate
        translated=output.with_suffix('.translated.json')
        if translated.is_file():transcript=json.loads(translated.read_text(encoding='utf-8'))
        else:
            transcript=translate(transcript,request.subtitle_language,request.llm_model)
            from jsonfile import write_json_atomic
            write_json_atomic(translated,transcript,ensure_ascii=True)
    generate_srt(transcript,caption_start,caption_end,str(srt))

    # Build deterministic EditManifest driving layout, safe-area hook, captions, and scene-aware cropping
    cand_meta = candidate_meta or {}
    shot_cuts = [float(c) - start for c in cand_meta.get('internal_cuts', []) if start < float(c) < end]
    has_explicit_bg = bool(
        background and background.is_file() and (
            request.preset == 'gameplay'
            or (request.preset == 'auto' and (request.selection_version != 2 or request.background))
        )
    )
    trajectory = None
    tracking = {}
    if request.mode == 'shorts' and request.preset in ('auto', 'smart') and not has_explicit_bg:
        from .framing import trajectory as track, dedupe_sendcmd_lines
        try:
            trajectory, tracking = track(source, duration, shot_cuts=shot_cuts) if shot_cuts else track(source, duration)
        except TypeError:
            trajectory, tracking = track(source, duration)

    manifest = build_edit_manifest(
        source_start=start,
        source_end=end,
        request=request,
        source_width=src_w,
        source_height=src_h,
        facecam_corners=cand_meta.get('face_corners', []),
        has_center_face=bool(trajectory),
        two_speakers_detected=bool(tracking.get('two_speakers')),
        internal_cuts=cand_meta.get('internal_cuts', []),
        climax_time=cand_meta.get('climax_time'),
        design_preset=design_preset,
        has_explicit_background=has_explicit_bg,
    )
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest.model_dump(), indent=2), encoding='utf-8')

    from .composition import GeometryRequest,geometry
    split = manifest.layout == 'gameplay_split'
    scene = geometry(GeometryRequest(ratio=request.split_ratio, secondary_position=request.background_position,
                                     split=bool(split), hook_position=request.hook_position, caption_margin=request.caption_margin))
    caption_margin = manifest.caption_margin if request.caption_margin is None else request.caption_margin
    generate_ass(transcript,caption_start,caption_end,str(ass),fontsize=18,font_name='Noto Sans',max_chars=30,effect='pop' if request.caption_style=='pop' else 'none',highlight_color='#FFFFFF' if request.caption_style=='clean' else '#FFD700',
                 **({'margin_v':round(caption_margin*288)} if caption_margin is not None else {}))
    # Source is already a precisely extracted selected interval.
    args=[ffmpeg(),'-hide_banner','-loglevel','error','-y','-threads','2','-i',str(source)]
    width,height=(manifest.output_width, manifest.output_height)
    framing = {
        'strategy': manifest.layout,
        'reason': manifest.layout_reason,
        'manifest_schema_version': manifest.schema_version,
        **tracking,
    }
    if manifest.layout == 'fullscreen_smart_crop' and trajectory:
        from .framing import dedupe_sendcmd_lines
        crop_w, crop_h, positions = trajectory
        commands = output.with_suffix('.camera.txt')
        commands.write_text('\n'.join(dedupe_sendcmd_lines(positions, 2)), encoding='utf-8')

    if request.mode=='longs' or manifest.layout == 'landscape_standard':
        graph=f'[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v]'
    elif manifest.layout == 'gameplay_split':
        if not background or not background.is_file():
            raise ValueError('Gameplay requires an authorized background asset; choose Blur or upload an asset')
        with av.open(str(background)) as secondary:
            secondary_duration=(secondary.duration or 0)/av.time_base
        if request.background_offset >= secondary_duration:
            raise ValueError('Secondary start offset must be inside the selected video')
        if request.background_loop:args+=['-stream_loop','-1']
        args+=['-ss',str(request.background_offset),'-threads','2','-i',str(background)]
        main_height=scene['primary']['height']
        secondary_height=scene['secondary']['height']
        def panel(source_tag, name, pheight):
            fill=f'scale=1080:{pheight}:force_original_aspect_ratio=increase,crop=1080:{pheight},setsar=1'
            fit=f'scale=1080:{pheight}:force_original_aspect_ratio=decrease,setsar=1'
            if request.split_crop=='fill':
                return f'{source_tag}{fill}[{name}]'
            if request.split_crop=='fit':
                return f'{source_tag}{fit},pad=1080:{pheight}:(ow-iw)/2:(oh-ih)/2[{name}]'
            return (f'{source_tag}split[{name}base][{name}fg];'
                    f'[{name}base]{fill},boxblur=24:2[{name}bg];'
                    f'[{name}fg]{fit}[{name}fit];'
                    f'[{name}bg][{name}fit]overlay=(W-w)/2:(H-h)/2[{name}]')
        order='[main][secondary]' if request.background_position=='bottom' else '[secondary][main]'
        framing.update(geometry=scene,secondary_position=request.background_position,primary_ratio=request.split_ratio,crop=request.split_crop)
        graph=(panel('[0:v]','main',main_height)+';'+
               f'[1:v]setpts=PTS/{request.background_speed},tpad=stop_mode=clone:stop_duration={duration}[secondary_input];'+
               panel('[secondary_input]','secondary',secondary_height)+';'+f'{order}vstack=shortest=1[v]')
    elif manifest.layout == 'gameplay_facecam' and manifest.facecam_corner and manifest.secondary_panel:
        cam_h = manifest.secondary_panel.height
        game_h = manifest.primary_panel.height
        cam_filter = facecam_crop_filter(manifest.facecam_corner, 1080, cam_h)
        framing.update(facecam_corner=manifest.facecam_corner, cam_height=cam_h, game_height=game_h)
        graph = (
            f'[0:v]split=3[cam_in][g_bg_in][g_fg_in];'
            f'[cam_in]{cam_filter}[cam_panel];'
            f'[g_bg_in]scale=1080:{game_h}:force_original_aspect_ratio=increase,crop=1080:{game_h},boxblur=24:2,eq=brightness=-0.08,setsar=1[g_bg];'
            f'[g_fg_in]scale=1080:{game_h}:force_original_aspect_ratio=decrease,setsar=1[g_fg];'
            f'[g_bg][g_fg]overlay=(W-w)/2:(H-h)/2[game_panel];'
            f'[cam_panel][game_panel]vstack=inputs=2,setsar=1[v]'
        )
    elif manifest.layout == 'two_speaker_split' and tracking.get('speaker_centres'):
        left_c, right_c = tracking['speaker_centres']
        half_h = 960
        crop_h = max(2, int(round(src_h * 0.85)) // 2 * 2)
        crop_w = min(src_w, max(2, int(round(crop_h * (1080.0 / half_h))) // 2 * 2))
        lx = max(0, min(src_w - crop_w, int(round(left_c[0] - crop_w / 2.0)))) // 2 * 2
        ly = max(0, min(src_h - crop_h, int(round(left_c[1] - crop_h * 0.42)))) // 2 * 2
        rx = max(0, min(src_w - crop_w, int(round(right_c[0] - crop_w / 2.0)))) // 2 * 2
        ry = max(0, min(src_h - crop_h, int(round(right_c[1] - crop_h * 0.42)))) // 2 * 2
        graph = (
            f'[0:v]split=2[ta][ba];'
            f'[ta]crop={crop_w}:{crop_h}:{lx}:{ly},scale=1080:{half_h},setsar=1[top];'
            f'[ba]crop={crop_w}:{crop_h}:{rx}:{ry},scale=1080:{half_h},setsar=1[bot];'
            f'[top][bot]vstack=inputs=2,setsar=1[v]'
        )
    elif manifest.layout == 'fullscreen_smart_crop' and trajectory:
        graph=f"[0:v]sendcmd=f='{escape_filter_value(str(commands.resolve()))}',crop@c={crop_w}:{crop_h}:{positions[0]}:0,scale=1080:1920,setsar=1[v]"
    elif manifest.layout == 'fullscreen_smart_crop' and src_w / float(max(1, src_h)) <= 0.62:
        graph='[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v]'
    elif manifest.layout == 'center_crop':
        # Explicit center framing fallback; no unverified face-tracking claim.
        graph='[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v]'
    else:
        # Source-fit over darkened blurred extension (preserves full gameplay HUD & action width cleanly)
        graph='[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2,eq=brightness=-0.08[bg];[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]'
    if request.captions and ass.exists():
        graph+=f";[v]ass=filename='{escape_filter_value(str(ass.resolve()))}'[captioned]"
        video='[captioned]'
    else:
        video='[v]'
    if request.hook and request.hook_mode!='none':
        hookfile=output.with_suffix('.hook.txt')
        fontsize=request.hook_font_size
        while True:
            hooklines=textwrap.wrap(request.hook,width=max(1,min(26,int(width*.84/(fontsize*1.1)))))
            if len(hooklines)*fontsize*1.2<=height*.55 or fontsize<=18:break
            fontsize-=2
        hookfile.write_text('\n'.join(hooklines),encoding='utf-8')
        # Windows FFmpeg builds may have no fontconfig configuration. An explicit
        # installed font avoids drawtext terminating the entire render process.
        font="font='"+{'sans':'Noto Sans','serif':'Noto Serif','mono':'DejaVu Sans Mono'}[request.hook_font]+"':"
        if os.name=='nt':
            fontpath=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/{'sans':'arial.ttf','serif':'times.ttf','mono':'consola.ttf'}[request.hook_font]
            if fontpath.is_file():font=f"fontfile='{escape_filter_value(str(fontpath.resolve()))}':"
        hook_y={'top':'h*0.12','center':'(h-th)/2','bottom':'h*0.75-th'}[request.hook_position]
        hook_mode=request.hook_mode or ('persistent' if request.text_mode in ('static_hook','hook_subtitles') else 'intro')
        timing={'persistent':'1','intro':f'lt(t,{request.hook_duration})',
                'timed':f'gte(t,{request.hook_start})*lt(t,{request.hook_end})'}[hook_mode]
        if request.mode=='shorts':
            anchor=scene['hook_anchor_y'] if manifest.layout=='gameplay_split' else manifest.hook_anchor_y
            correction={'top':'','center':'-th/2','bottom':'-th'}[request.hook_position]
            hook_y=f'{anchor}{correction}'
        framing.update(hook_mode=hook_mode,hook_enable=timing,hook_anchor_y=manifest.hook_anchor_y)
        graph+=f";{video}drawtext={font}textfile='{escape_filter_value(str(hookfile.resolve()))}':expansion=none:fontcolor={request.hook_color}:fontsize={fontsize}:borderw={request.hook_outline}:bordercolor=black:shadowx={2 if request.hook_shadow else 0}:shadowy={2 if request.hook_shadow else 0}:box={int(request.hook_box)}:boxcolor=black@0.65:boxborderw=14:x=(w-tw)/2:y={hook_y}:enable='{timing}'[hooked]"
        video='[hooked]'
    audio_map=None
    next_input=2 if manifest.layout=='gameplay_split' and request.mode=='shorts' else 1
    if request.logo_asset:
        logo=logo_path or config.DATA/'logos'/request.logo_asset
        if not logo.is_file():raise ValueError('Selected logo is unavailable')
        args+=['-threads','1','-i',str(logo)]
        graph+=f';[{next_input}:v]scale={max(32,width//8)}:{max(32,height//8)}:force_original_aspect_ratio=decrease[logo];{video}[logo]overlay=W-w-32:32[branded]'
        video='[branded]';next_input+=1
    if request.cta_text:
        ctafile=output.with_suffix('.cta.txt');ctafile.write_text('\n'.join(textwrap.wrap(request.cta_text,width=max(1,int(width*.84/(32*1.1))))),encoding='utf-8')
        ctafont=''
        if os.name=='nt':
            ctafontpath=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'arial.ttf'
            if ctafontpath.is_file():ctafont=f"fontfile='{escape_filter_value(str(ctafontpath.resolve()))}':"
        graph+=f";{video}drawtext={ctafont}textfile='{escape_filter_value(str(ctafile.resolve()))}':expansion=none:fontcolor=white:fontsize=32:box=1:boxcolor=black@0.65:boxborderw=10:x=(w-tw)/2:y=h*0.8-th:enable='gte(t,{max(0,duration-4)})'[cta]"
        video='[cta]'
    music=music_path or (config.DATA/'music'/request.music_asset if request.music_asset else None)
    if request.audio_mode=='music' and music and not music.is_file():raise ValueError('Selected music asset is missing; choose an available authorized asset')
    if request.audio_mode=='voiceover':
        args+=['-i',str(narration)]
        graph+=f';[{next_input}:a]apad,atrim=duration={duration},{audio_filter}[audio]'
        audio_map='[audio]'
    elif request.audio_mode=='music' and music and music.is_file():
        args+=['-stream_loop','-1','-i',str(music)]
        graph+=f';[{next_input}:a]atrim=duration={duration},afade=t=in:d=0.3,afade=t=out:st={max(0,duration-.5)}:d=0.5,volume=0.12[bed]'
        if has_audio:
            graph+=f';[0:a]asplit[original][side];[bed][side]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[duck];[original][duck]amix=inputs=2:normalize=0,{audio_filter}[audio]'
        else:graph+=f';[bed]{audio_filter}[audio]'
        audio_map='[audio]'
    elif has_audio and request.audio_mode!='silent':
        graph+=f';[0:a]{audio_filter}[audio]';audio_map='[audio]'
    if request.transition=='fade':
        fade_duration=min(.25,duration/4)
        fade_end=duration-fade_duration
        graph+=f';{video}fade=t=in:d={fade_duration},fade=t=out:st={fade_end}:d={fade_duration}[faded]'
        video='[faded]'
        if audio_map:
            graph+=f';{audio_map}afade=t=in:d={fade_duration},afade=t=out:st={fade_end}:d={fade_duration}[faded_audio]'
            audio_map='[faded_audio]'
    args+=['-filter_complex_threads','1','-filter_complex',graph,'-map',video]
    args+=['-map',audio_map,'-c:a','aac','-b:a','192k','-ar','48000'] if audio_map else ['-an']
    args+=[*video_encode_args(),'-maxrate',str(request.bitrate),'-bufsize',str(request.bitrate*2),'-threads','2','-pix_fmt','yuv420p']
    if not request.preserve_fps:
        args+=['-r','30']
    temporary=output.with_suffix('.rendering.mp4')
    args+=['-t',str(duration),'-movflags','+faststart','-progress','pipe:1',str(temporary)]
    if stage:stage('rendering')
    try:
        run(args,tick,duration)
        if stage:stage('validation')
        report=validate_output(temporary,duration,request=request,expected_audio=audio_map is not None)
        temporary.replace(output)
    finally:temporary.unlink(missing_ok=True)
    from jsonfile import write_json_atomic
    write_json_atomic(output.with_suffix('.validation.json'), {**report,'framing':framing})
    return output


def validate_output(path,duration,request=None,expected_audio=None):
    import av
    with av.open(str(path)) as container:
        if not container.streams.video or not container.duration or abs(container.duration/av.time_base-duration)>1:
            raise RuntimeError('Rendered video validation failed (stream/duration)')
        stream=container.streams.video[0]
        stream.thread_count=2
        if request:
            expected=(1080,1920) if request.mode=='shorts' else (request.output_height*16//9//2*2,request.output_height)
            if (stream.width,stream.height)!=expected:raise RuntimeError('Rendered resolution/aspect ratio does not match the requested output')
            if request.destination=='youtube_shorts' and container.duration/av.time_base>config.SHORTS_MAX_SECONDS:
                raise RuntimeError(f'Rendered output exceeds the {config.SHORTS_MAX_SECONDS}s YouTube Shorts limit')
        if expected_audio is not None and bool(container.streams.audio)!=expected_audio:
            raise RuntimeError('Rendered audio track does not match the selected audio mode')
        frames=0;black_samples=0;sampled_frames=0;video_end=None
        try:
            for frame in container.decode(video=0):
                if frame.pts is not None:video_end=float(frame.pts*frame.time_base)+1/float(stream.average_rate or 30)
                if frames % max(1,round(float(stream.average_rate or 30)))==0:
                    pixels=frame.reformat(width=64,height=64,format='gray').to_ndarray()
                    sampled_frames+=1;black_samples+=int((pixels<16).mean()>.95)
                frames+=1
        except Exception:
            raise RuntimeError('Rendered container has a corrupted video packet') from None
        if not frames:
            raise RuntimeError('Rendered video has no decodable frame')
        report={'duration':container.duration/av.time_base,'width':stream.width,'height':stream.height,'frames_decoded':frames,'audio':bool(container.streams.audio),'validated':True,'black_frame_fraction':black_samples/sampled_frames,'black_frames_sampled':sampled_frames,'video_end_seconds':video_end}
    if report['audio']:
        import numpy as np
        peak=0.;samples=clipped=silent=0;audio_end=None
        with av.open(str(path)) as container:
            try:
                for frame in container.decode(audio=0):
                    if frame.pts is not None:audio_end=float(frame.pts*frame.time_base)+frame.samples/frame.sample_rate
                    values=frame.to_ndarray()
                    scale=max(abs(np.iinfo(values.dtype).min),np.iinfo(values.dtype).max) if np.issubdtype(values.dtype,np.integer) else 1
                    values=values.astype(np.float32)/scale
                    peak=max(peak,float(np.abs(values).max()));samples+=values.size
                    clipped+=int((np.abs(values)>=.9999).sum())
                    if float(np.sqrt(np.mean(values**2)))<.001:silent+=values.size
            except Exception:
                raise RuntimeError('Rendered container has a corrupted audio packet') from None
        if not samples:raise RuntimeError('Rendered audio has no decodable samples')
        report.update(audio_peak=peak,audio_clipped_fraction=clipped/samples,audio_silence_fraction=silent/samples,audio_samples_decoded=samples,audio_silence_threshold_db=-60)
        if video_end is not None and audio_end is not None:
            delta=abs(video_end-audio_end);report['audio_video_end_delta_seconds']=delta
            if delta>.25:raise RuntimeError('Rendered audio/video duration differs by more than 250 ms; inspect synchronization')
    return report


def concatenate(paths,output,tick=lambda *_:None):
    # Only application-generated file paths, never source-supplied strings.
    manifest=output.with_suffix('.concat.txt')
    manifest.write_text(''.join("file '"+p.name+"'\n" for p in paths),encoding='utf-8')
    temporary=output.with_suffix('.assembling.mp4')
    try:
        run([ffmpeg(),'-y','-hide_banner','-loglevel','error','-f','concat','-safe','1','-i',manifest.name,
             '-c','copy','-movflags','+faststart',temporary.name],tick,cwd=output.parent)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def normalize_compilation(source,output,duration,height,tick):
    """Make stream-copy assembly safe across source dimensions, frame rates and audio layouts."""
    import av

    width=height*16//9//2*2
    with av.open(str(source)) as container:
        has_audio=bool(container.streams.audio)
    args=[ffmpeg(),'-y','-hide_banner','-loglevel','error','-threads','2','-i',str(source)]
    if not has_audio:
        args+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
    args+=['-map','0:v:0','-map','0:a:0' if has_audio else '1:a:0',
           '-vf',f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1',
           '-af','apad','-t',str(duration),'-r','30','-c:v','libx264','-preset','fast',
           '-threads','2','-filter_threads','1','-pix_fmt','yuv420p','-c:a','aac',
           '-ac','2','-ar','48000','-movflags','+faststart','-progress','pipe:1']
    temporary=output.with_suffix('.normalizing.mp4')
    try:
        run([*args,str(temporary)],tick,duration)
        validate_output(temporary,duration,expected_audio=True)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
