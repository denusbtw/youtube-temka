import json
import threading
import httpx
import pytest
from factory.models import JobRequest
from factory.highlights import analyze
from factory.audio_edit import speech_timeline
from factory import config,store


def req(**kw):return JobRequest(source='upload:test.mp4',rights_confirmed=True,save_drive=False,**kw)


@pytest.mark.parametrize('decision',['unsuitable','visual_required'])
def test_explicit_suitability_decision_is_not_replaced_by_heuristics(monkeypatch,decision):
    def respond(*a,**kw):return httpx.Response(200,request=httpx.Request('POST','http://fixture.invalid'),json={'response':json.dumps({'candidates':[],'decision':decision,'explanation':'No standalone context in this excerpt'})})
    monkeypatch.setattr(httpx.Client,'post',respond)
    result=analyze({'language':'en','segments':[{'start':0,'end':25,'text':'This sentence has enough actual words to make a deterministic heuristic candidate but context is still missing here.'}]},req(),25)
    assert result['analysis']=='ollama' and result['candidates']==[]
    assert result['analysis_diagnostics']['requests']==1


def test_empty_unexplained_ranking_retries_and_produces_reviewable_fallback(monkeypatch):
    def respond(*a,**kw):return httpx.Response(200,request=httpx.Request('POST','http://fixture.invalid'),json={'response':'{"candidates":[]}'})
    monkeypatch.setattr(httpx.Client,'post',respond)
    result=analyze({'language':'en','segments':[{'start':0,'end':25,'text':'This sentence has enough actual words to make a deterministic heuristic candidate with context and a natural ending.'}]},req(),25)
    assert result['analysis_outcome']=='empty_llm_result' and result['candidates']
    assert result['analysis_diagnostics']['requests']==2


def test_malformed_window_does_not_skip_semantic_analysis_of_later_speech(monkeypatch):
    calls=[]
    def respond(*a,**kw):
        if 'prompt' not in kw['json']:
            return httpx.Response(200,request=httpx.Request('POST','http://fixture.invalid'),json={'done':True})
        calls.append(kw['json'])
        candidate={'start_seconds':30,'end_seconds':55,'score':90,'title':'Later complete thought','language':'en','reason':'fixture','confidence':.8}
        result={'candidates':[]} if len(calls)<=2 else {'candidates':[candidate]}
        return httpx.Response(200,request=httpx.Request('POST','http://fixture.invalid'),json={'response':json.dumps(result)})
    monkeypatch.setattr(httpx.Client,'post',respond)
    transcript={'language':'en','segments':[{'start':0,'end':25,'text':'word '*720+'.'},{'start':30,'end':55,'text':'A later self contained thought has enough words to be considered independently and ends naturally.'}]}
    result=analyze(transcript,req(),60)
    assert len(calls)==3 and result['analysis_diagnostics']['windows']==2
    later=next(c for c in result['candidates'] if c['start_seconds']==30)
    assert later['selection_method']=='ollama' and later['analysis_model']
    assert result['analysis']=='heuristic'  # The mixed fallback remains visible.


def test_invented_title_is_replaced_with_source_quote_and_requires_review(monkeypatch):
    def respond(*a,**kw):
        candidate={'start_seconds':0,'end_seconds':25,'score':9,'title':"Leo's Vision for the Space Station",'language':'en','reason':'fixture','confidence':1}
        return httpx.Response(200,request=httpx.Request('POST','http://fixture.invalid'),json={'response':json.dumps({'candidates':[candidate]})})
    monkeypatch.setattr(httpx.Client,'post',respond)
    text='The space station has been the cornerstone of low Earth orbit for over two decades and continues to support important research.'
    result=analyze({'language':'en','segments':[{'start':0,'end':25,'text':text}]},req(),25)
    candidate=result['candidates'][0]
    assert candidate['editorial_review_required'] and candidate['title_method']=='transcript_quote'
    assert "Leo" not in candidate['title'] and text.startswith(candidate['title'])
    assert candidate['suggested_title']=="Leo's Vision for the Space Station"


def test_pause_removal_maps_words_and_video_to_same_timeline():
    transcript={'language':'en','segments':[{'start':10,'end':18,'text':' one two','words':[{'word':' one','start':10.5,'end':11},{'word':' two','start':17,'end':17.5}]}]}
    ranges,timed,start,end=speech_timeline(transcript,10,18)
    assert len(ranges)==2 and end<2
    assert start==0 and timed['segments'][1]['words'][0]['end']<=end
    assert transcript['segments'][0]['start']==10


def test_multi_hour_checkpoints_keep_absolute_offsets_once(tmp_path,monkeypatch):
    from factory.pipeline import transcribe_chunks
    from factory.progress import Progress
    root=tmp_path/'job';root.mkdir()
    for offset in range(0,10800,900):
        (root/f'transcript-{offset}.json').write_text(json.dumps({'language':'en','segments':[{'start':1,'end':26,'text':'An actual checkpoint sentence.','words':[{'word':' sentence','start':2,'end':3}]}]}),encoding='utf-8')
    updates=[]
    progress=Progress(lambda *args,**kwargs:updates.append(kwargs))
    result=transcribe_chunks('never-fetched',req(end=None),root,{},10800,720,progress,lambda **kw:updates.append(kw))
    assert len(result['segments'])==12 and result['segments'][-1]['start']==9901
    assert result['segments'][-1]['words'][0]['start']==9902
    again=transcribe_chunks('never-fetched',req(end=None),root,{},10800,720,progress,lambda **kw:None)
    assert again==result


def test_worker_survives_first_job_and_processes_second(tmp_path,monkeypatch):
    from factory.worker import local_loop
    from factory import pipeline
    monkeypatch.setattr(config,'DATA',tmp_path);monkeypatch.setattr(config,'SCRATCH',tmp_path/'scratch');monkeypatch.setattr(config,'DB',tmp_path/'db')
    monkeypatch.setenv('WORKER_ID','two-job-test')
    for _ in range(2):store.create(req().model_dump())
    stop=threading.Event();done=[]
    def process(claim):
        done.append(claim['job']['id'])
        if len(done)==1:raise RuntimeError('Unexpected fault')
        store.update(claim['job']['id'],claim['lease'],state='READY')
        stop.set()
    monkeypatch.setattr(pipeline,'process',process)
    local_loop(stop)
    assert len(done)==2 and {j['state'] for j in store.list_jobs()}=={'FAILED','READY'}


def test_expired_processing_lease_requeues_and_fences_after_backoff(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path);monkeypatch.setattr(config,'SCRATCH',tmp_path/'scratch');monkeypatch.setattr(config,'DB',tmp_path/'db')
    job=store.create(req().model_dump());claim=store.claim('dead-worker')
    store.update(job['id'],claim['lease'],state='TRANSCRIBING',stage_update={'key':'transcription','status':'running','current':900,'total':1800,'unit':'seconds'})
    with store.connect() as db:db.execute('UPDATE jobs SET seen=0 WHERE id=?',(job['id'],))
    assert store.claim('replacement') is None
    recovering = store.get(job['id'])
    assert recovering['stages'][0]['current']==900
    assert recovering['state']=='QUEUED'
    assert recovering['result']['failure']['code']=='WORKER_LEASE_EXPIRED'
    monkeypatch.setattr(store.time,'time',lambda:recovering['result']['retry_at']+1)
    fresh=store.claim('replacement')
    with pytest.raises(InterruptedError):store.update(job['id'],claim['lease'],state='READY')
    assert fresh['lease']!=claim['lease']


def test_selection_stage_can_finish_blocked_without_becoming_failed(tmp_path, monkeypatch):
    from factory.progress import Progress

    monkeypatch.setattr(config, 'DATA', tmp_path)
    monkeypatch.setattr(config, 'SCRATCH', tmp_path / 'scratch')
    monkeypatch.setattr(config, 'DB', tmp_path / 'db')
    job = store.create(req().model_dump())
    claim = store.claim('worker')
    progress = Progress(lambda state=None, **kwargs: store.update(job['id'], claim['lease'], state, **kwargs))
    progress.begin('highlights', 'ANALYZING')
    progress.done(status='blocked', message='No selected clips')
    store.update(job['id'], claim['lease'], state='BLOCKED')
    saved = store.get(job['id'])
    assert saved['state'] == 'BLOCKED'
    assert saved['stages'][0]['status'] == 'blocked'
    assert saved['stages'][0]['finished'] is not None


def test_transient_stage_retries_are_persisted_and_bounded(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path)
    monkeypatch.setattr(config,'SCRATCH',tmp_path/'scratch')
    monkeypatch.setattr(config,'DB',tmp_path/'db')
    now = 100
    monkeypatch.setattr(store.time,'time',lambda:now)
    job=store.create(req().model_dump())
    failure={'code':'DOWNLOAD_RATE_LIMITED','stage':'retrieval','message':'rate limited','retryable':True}
    for expected in ('QUEUED','QUEUED','FAILED'):
        claim=store.claim('worker')
        store.update(job['id'],claim['lease'],state='QUEUED',result={'failure':failure})
        recovering=store.get(job['id'])
        assert recovering['state']==expected
        assert store.claim('worker') is None
        now = recovering['result'].get('retry_at',now)+1
    assert recovering['result']['failure']['attempt']==3


def test_asr_interruption_reuses_completed_chunks_without_duplication(tmp_path, monkeypatch):
    from factory import media, pipeline
    from factory.progress import Progress

    root = tmp_path / 'asr-job'
    root.mkdir()
    monkeypatch.setattr(config, 'AUDIO_CHUNK_SECONDS', 300)
    downloaded_offsets = []
    transcribed_offsets = []

    def fake_download(source, out_path, start, end, res, audio_only, tick):
        downloaded_offsets.append((start, end))
        out_path.write_bytes(b'audio-chunk')

    def fake_run(args, tick=None, duration=None, env=None):
        out_json = args[4]
        offset = int(out_json.split('transcript-')[1].split('.')[0])
        if offset == 300 and len(transcribed_offsets) == 1:
            transcribed_offsets.append('interrupted-300')
            raise media.MediaFailure('PROCESSING_TIMEOUT', 'Injected ASR chunk 2 interruption', True)
        transcribed_offsets.append(offset)
        from pathlib import Path
        Path(out_json).write_text(
            json.dumps({
                'language': 'en',
                'segments': [{'start': 1.0, 'end': 10.0, 'text': f'Chunk at {offset}', 'words': [{'word': 'Chunk', 'start': 1.0, 'end': 2.0}]}],
            }),
            encoding='utf-8',
        )

    monkeypatch.setattr(media, 'download', fake_download)
    monkeypatch.setattr(media, 'run', fake_run)
    progress = Progress(lambda *a, **kw: None)

    with pytest.raises(media.MediaFailure, match='Injected ASR chunk 2 interruption'):
        pipeline.transcribe_chunks('https://example.com/v', req(start=0, end=600), root, {'has_audio': True}, 600, 720, progress, lambda **kw: None)

    assert (root / 'transcript-0.json').is_file()
    assert not (root / 'transcript-300.json').is_file()

    # Resume after interruption: chunk 0 must NOT be re-downloaded or re-transcribed!
    result = pipeline.transcribe_chunks('https://example.com/v', req(start=0, end=600), root, {'has_audio': True}, 600, 720, progress, lambda **kw: None)
    assert transcribed_offsets == [0, 'interrupted-300', 300]
    assert downloaded_offsets == [(0, 300), (300, 600), (300, 600)]
    assert len(result['segments']) == 2
    assert result['segments'][0]['start'] == 1.0 and result['segments'][1]['start'] == 301.0


def test_cv_and_vlm_interruption_and_cache_version_invalidation(tmp_path, monkeypatch):
    from factory import media
    from factory.source_intelligence import analyze_source
    from factory.vision_vlm import LocalVisionAdapter

    # Build a small real MP4 fixture
    video = tmp_path / 'cv_fixture.mp4'
    media.run([
        media.ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=navy:s=160x90:r=10',
        '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video),
    ])

    cache_dir = tmp_path / 'cache'
    cache_dir.mkdir()
    # Write a stale/corrupt v1 cache with old ocr_backend version or mismatched fingerprint
    stale_cache = cache_dir / 'source-evidence-v1.json'
    stale_cache.write_text(json.dumps({
        'schema_version': 1,
        'context': {
            'source_id': str(video),
            'source_fingerprint': 'stale-fingerprint',
            'start': 0.0,
            'end': 3.0,
            'duration': 3.0,
            'ocr_backend': 'opencv_text_band_fallback',
        },
        'shots': [],
    }), encoding='utf-8')

    # 1. Inject interruption during CV analysis
    orig_extract = __import__('factory.source_intelligence', fromlist=['_extract_frame_features'])._extract_frame_features
    calls = {'n': 0}
    def interrupt_cv(rgb):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('Injected CV mid-pass interruption')
        return orig_extract(rgb)

    monkeypatch.setattr('factory.source_intelligence._extract_frame_features', interrupt_cv)
    with pytest.raises(RuntimeError, match='Injected CV mid-pass interruption'):
        analyze_source(video, 0.0, 3.0, {'segments': []}, cache_dir=cache_dir)

    # Restore CV and verify stale cache is invalidated and replaced with valid evidence
    monkeypatch.setattr('factory.source_intelligence._extract_frame_features', orig_extract)
    ev = analyze_source(video, 0.0, 3.0, {'segments': []}, cache_dir=cache_dir)
    assert ev.context.source_fingerprint != 'stale-fingerprint'
    assert ev.context.ocr_backend.endswith(':v2')
    assert len(ev.shots) >= 1

    # 2. VLM interruption and cache-version invalidation check
    vlm_cache = cache_dir / 'vlm-cache.json'
    vlm_cache.write_text(json.dumps({'old_version_key': {'coherent_single_event': True}}), encoding='utf-8')
    adapter = LocalVisionAdapter(model='qwen3-vl:2b-instruct', ollama_url='http://127.0.0.1:11434', cache_path=vlm_cache, max_calls=4, enabled=True)
    adapter._available = True

    vlm_net_calls = {'n': 0}
    class InterruptingThenWorkingClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, json):
            vlm_net_calls['n'] += 1
            if vlm_net_calls['n'] == 1:
                raise httpx.ReadTimeout('Injected VLM timeout')
            resp = __import__('unittest.mock').mock.MagicMock()
            resp.status_code = 200
            resp.raise_for_status = lambda: None
            resp.json.return_value = {
                'message': {
                    'content': '{"is_format_break": false, "break_kind": "none", "reason": "Clean gameplay", "confidence": 0.90}'
                }
            }
            return resp

    monkeypatch.setattr('factory.vision_vlm.httpx.Client', InterruptingThenWorkingClient)
    monkeypatch.setattr('factory.vision_vlm.vlm_gpu_layers', lambda m, u=None: 0)

    # First call fails with timeout -> honest cv_fallback, NOT cached as valid result!
    v_fail = adapter.detect_format_break([b'\xff\xd8frameA'], {'shot': 1})
    assert v_fail.verified is False
    assert adapter.failures == 1

    # Retry succeeds and writes to cache
    v_ok = adapter.detect_format_break([b'\xff\xd8frameA'], {'shot': 1})
    assert v_ok.verified is True
    assert vlm_net_calls['n'] == 2

    # Subsequent call hits cache (0 new network calls)
    v_cached = adapter.detect_format_break([b'\xff\xd8frameA'], {'shot': 1})
    assert v_cached.verified is True
    assert adapter.cache_hits == 1
    assert vlm_net_calls['n'] == 2

    # Changing PROMPT_VERSION invalidates old cache key and forces fresh inference
    monkeypatch.setattr('factory.vision_vlm.PROMPT_VERSION', 'vlm-8.0-r4-test')
    v_new_ver = adapter.detect_format_break([b'\xff\xd8frameA'], {'shot': 1})
    assert v_new_ver.verified is True
    assert vlm_net_calls['n'] == 3
