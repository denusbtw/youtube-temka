import json

import pytest

from factory import config, media, store
from factory.models import JobRequest
from factory.pipeline import process
from factory.timeline import story


def test_story_covers_twelve_minutes_and_prefers_nearby_boundary():
    request=JobRequest(source='upload:story.mp4',rights_confirmed=True,workflow='story',
                       destination='video',minimum=180,maximum=270,story_count=3,series_overlap=4)
    parts,_=story({'segments':[]},request,720,'Story',[243,480])
    assert parts[0]['start_seconds']==0
    assert parts[-1]['end_seconds']==720
    assert parts[0]['end_seconds']==243
    assert all(a['end_seconds']-b['start_seconds']==4 for a,b in zip(parts,parts[1:]))
    assert all(180<=p['end_seconds']-p['start_seconds']<=270 for p in parts)
    with pytest.raises(ValueError,match='YouTube Shorts'):
        JobRequest(**{**request.model_dump(),'destination':'youtube_shorts'})


@pytest.fixture
def owned_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path)
    monkeypatch.setattr(config,'SCRATCH',tmp_path/'scratch')
    monkeypatch.setattr(config,'DB',tmp_path/'db')
    monkeypatch.setattr(config,'RESERVE_BYTES',0)
    monkeypatch.setenv('FFMPEG_ENCODER','x264')
    uploads=config.SCRATCH/'uploads'
    uploads.mkdir(parents=True)
    inputs=[]
    for i,(size,rate) in enumerate([('160x90',24),('128x96',25),('128x128',30)]):
        name=f'fixture-{i}.mp4'
        args=[media.ffmpeg(),'-hide_banner','-loglevel','error','-y','-f','lavfi',
              '-i',f'color=black:s={size}:r={rate}',
              '-vf',"drawbox=color=white:t=fill:enable='gte(t,3)'",
              '-t','6','-c:v','libx264','-threads','2',str(uploads/name)]
        media.run(args)
        inputs.append({'source':f'upload:{name}'})
    return inputs


def test_smart_highlights_renders_silent_visual_event(owned_sources):
    request=JobRequest(source=owned_sources[0]['source'],rights_confirmed=True,
                       workflow='highlights',minimum=5,maximum=6,count=1,
                       save_drive=False,audio_mode='silent',captions=False,preset='crop')
    job=store.create(request.model_dump())
    process(store.claim('fixture'))
    done=store.get(job['id'])
    assert done['state']=='READY',done['error']
    assert done['result']['candidates'][0]['selection_method']=='sampled_media_signals'
    assert done['result']['signal_coverage']['scanned_seconds']==6
    media.validate_output(config.SCRATCH/job['id']/'clip-1.mp4',5.5,request,expected_audio=False)


def test_compilation_reviews_and_renders_three_different_sources(owned_sources,monkeypatch):
    request=JobRequest(source=owned_sources[0]['source'],sources=owned_sources,
                       rights_confirmed=True,workflow='compilation',destination='video',
                       minimum=5,maximum=6,count=3,target_duration=16.5,save_drive=False,captions=False,
                       review_before_render=True)
    job=store.create(request.model_dump())
    assert store.claim('remote',remote=True) is None
    process(store.claim('fixture'))
    pending=store.get(job['id'])
    assert pending['state']=='REVIEW',pending['error']
    plan=pending['result']['edit_plan']
    segments=[{key:segment[key] for key in ('id','start_seconds','end_seconds','transition')}
              for segment in reversed(plan['segments'])]
    segments[0]['transition']='fade'
    saved=store.edit_plan(job['id'],plan['revision'],segments,16.5)
    with pytest.raises(InterruptedError):
        store.edit_plan(job['id'],plan['revision'],segments,None,approve=True)
    store.edit_plan(job['id'],saved['revision'],segments,None,approve=True)
    from factory import pipeline
    monkeypatch.setattr(pipeline,'transcribe_chunks',lambda *_:pytest.fail('Reran ASR after plan approval'))
    concatenate = media.concatenate

    def interrupt(*args, **kwargs):
        raise media.MediaFailure('SOURCE_UNAVAILABLE', 'Injected compilation interruption', True)

    monkeypatch.setattr(media, 'concatenate', interrupt)
    process(store.claim('fixture'))
    interrupted = store.get(job['id'])
    assert interrupted['state'] == 'QUEUED'
    assert interrupted['result']['clips_completed'] == 3
    completed_hashes = [item['output_hash'] for item in interrupted['result']['outputs']]
    with store.connect() as db:
        db.execute("UPDATE jobs SET result=json_remove(result,'$.retry_at') WHERE id=?", (job['id'],))
    monkeypatch.setattr(media, 'concatenate', concatenate)
    monkeypatch.setattr(media, 'render', lambda *_args, **_kwargs: pytest.fail('Reran a completed segment'))
    process(store.claim('fixture'))
    done=store.get(job['id'])
    assert done['state']=='READY',done['error']
    assert [item['segment_output_hash'] for item in done['result']['timeline']['segments']] == completed_hashes
    root=config.SCRATCH/job['id']
    assert [output['name'] for output in done['result']['outputs']]==['long.mp4']
    media.validate_output(root/'long.mp4',16.5,request,expected_audio=True)
    timeline=json.loads((root/'timeline.json').read_text(encoding='utf-8'))
    assert [segment['source_id'] for segment in timeline['segments']]==[
        segment['source_id'] for segment in reversed(plan['segments'])]
    assert timeline['segments'][0]['transition']=='fade'
    assert done['result']['duration_shortfall'] is False


def test_render_recovers_after_transient_failure_without_rerendering_first_clip(owned_sources,monkeypatch):
    request=JobRequest(source=owned_sources[0]['source'],rights_confirmed=True,
                       workflow='highlights',minimum=5,maximum=6,intervals=[(0,5),(1,6)],
                       save_drive=False,audio_mode='silent',captions=False,preset='crop')
    job=store.create(request.model_dump())
    real_render = media.render
    rendered = []
    def render(source,output,*args,**kwargs):
        if output.name=='clip-2.mp4' and len(rendered)==1:
            rendered.append('interrupted')
            raise media.MediaFailure('SOURCE_UNAVAILABLE','injected failure',True)
        rendered.append(output.name)
        return real_render(source,output,*args,**kwargs)
    monkeypatch.setattr(media,'render',render)
    process(store.claim('fixture'))
    queued=store.get(job['id'])
    assert queued['state']=='QUEUED'
    assert queued['result']['failure']['code']=='SOURCE_UNAVAILABLE'
    with store.connect() as db:
        db.execute("UPDATE jobs SET result=json_remove(result,'$.retry_at') WHERE id=?",(job['id'],))
    process(store.claim('fixture'))
    done=store.get(job['id'])
    assert done['state']=='READY',done['error']
    assert rendered==['clip-1.mp4','interrupted','clip-2.mp4']


def test_workflow_versions_preserve_legacy_story_and_require_explicit_v2_mode():
    fields = {'source': 'upload:story.mp4', 'rights_confirmed': True, 'workflow': 'story'}
    legacy = JobRequest(**fields)
    assert legacy.story_mode == 'chronological'
    with pytest.raises(ValueError, match='Choose chronological'):
        JobRequest(**fields, workflow_version=2)
    semantic = JobRequest(**fields, workflow_version=2, story_mode='ai_verified', story_count=2)
    assert semantic.series_mode == 'connected'
    assert semantic.selection_version == 2
    assert semantic.count == 2
    assert JobRequest(source=fields['source'], rights_confirmed=True,
                      series_mode='connected').workflow is None
