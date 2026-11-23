import json
import httpx
import pytest

from factory import config
from factory.models import JobRequest
from factory.shorts import identity, plan, split
from factory.commons import metadata, CC0


def request(**kwargs):
    return JobRequest(source='upload:owned.mp4', rights_confirmed=True,
                      **({'count':3,'minimum':35,'maximum':60,'selection_version':2} | kwargs))


def transcript(segments=()):
    return {'language':'en','segments':list(segments)}


def speech(start,end,text):
    return {'start':start,'end':end,'text':text}


def test_sequential_coverage_and_overlap():
    req=request(series_mode='sequential')
    parts=plan(transcript(),req,150,[],lambda *_:None)['candidates']
    assert [(c['start_seconds'],c['end_seconds']) for c in parts]==[(0,52),(49,101),(98,150)]
    assert all(c['schema_version']==2 for c in parts)


@pytest.mark.parametrize('duration',[7,60,200])
def test_sequential_insufficiency_is_explicit(duration):
    parts,reason=split(0,duration,request(),transcript())
    assert not parts and 'Required source span' in reason


def test_split_prefers_scene_boundary_with_exact_overlap():
    parts,_=split(0,150,request(),transcript(),[50,103])
    assert [p['end_seconds'] for p in parts]==[50,103,150]
    assert [p['start_seconds'] for p in parts]==[0,47,100]


def test_visual_only_distinct_intervals():
    features=[{'time':point,'motion':.2,'cut':False} for point in (24,25,87,161)]
    result=plan(transcript(),request(),190,features,lambda *_:None)
    assert len(result['candidates'])==3
    assert all(c['visual_review_required'] and not c['narrative_completeness'] for c in result['candidates'])


def test_independent_returns_three_separate_speech_incidents(monkeypatch):
    from factory import inference
    monkeypatch.setattr(inference,'generate',lambda *_args,**_kw: (_ for _ in ()).throw(httpx.ConnectError('offline fixture')))
    passages=[
        'The ball struck the post, bounced back, and the players watched it roll across the goal line.',
        'A repair crew tested the new motor and watched the conveyor begin to move at normal speed.',
        'The chef mixed the batter, took it out of the oven and cut the first slice.',
    ]
    data=transcript([speech(i,i+41,passage) for i,passage in zip((0,70,140),passages)])
    result=plan(data,request(),190,[],lambda *_:None)
    assert [c['start_seconds'] for c in result['candidates']]==[0,70,140]


def test_repeated_spoken_highlights_are_not_counted_twice(monkeypatch):
    from factory import inference
    monkeypatch.setattr(inference,'generate',lambda *_args,**_kw: (_ for _ in ()).throw(httpx.ConnectError('offline fixture')))
    line='A yellow ball struck the brick wall, bounced twice, then rolled back to the edge of the track.'
    data=transcript([speech(i,i+41,line) for i in (0,70,140)])
    result=plan(data,request(),190,[],lambda *_:None)
    assert len(result['candidates'])==1 and '1/3' in result['selection_summary']


def test_no_useful_speech_reports_insufficiency():
    result=plan(transcript([speech(0,7,'Hello.')]),request(),7,[],lambda *_:None)
    assert not result['candidates'] and '0/3' in result['selection_summary']


@pytest.mark.parametrize('invalid',[None,'malformed','short','count','timestamp'])
def test_connected_requires_feasible_grounded_event(monkeypatch,invalid):
    from factory import inference
    from contextlib import nullcontext
    monkeypatch.setattr(inference,'model_session',lambda *_:nullcontext())
    event={'start':0,'end':150,'title':'Repairing one machine','evidence':['The drive belt broke'],
           'beats':[dict(setup='The motor stopped',development='Repair the drive belt',ending='Test',connection='Continue the repair') for i in range(3)]}
    if invalid=='short':event['end']=7
    if invalid=='count':event['beats'].pop()
    if invalid=='timestamp':event['start']=-10
    raw='{' if invalid=='malformed' else json.dumps(event)
    monkeypatch.setattr(inference,'generate',lambda *_args,**_kw: httpx.Response(
        200,json={'response':raw},request=httpx.Request('POST','http://test')))
    data=transcript([speech(0,7,'The drive belt broke.'),speech(52,54,'Replace the belt.'),
                     speech(100,101,'Test the new belt.'),speech(145,150,'The belt runs normally.')])
    result=plan(data,request(series_mode='connected'),150,[],lambda *_:None)
    if invalid:
        assert not result['candidates'] and result['analysis_diagnostics']['rejected_intervals']==1
    else:
        assert len(result['candidates'])==3
        assert result['candidates'][0]['start_seconds']==0
        assert result['candidates'][-1]['end_seconds']==150


def test_checkpoint_settings_are_versioned(monkeypatch):
    import factory.shorts as shorts
    req=request()
    asr=transcript()
    baseline=identity(req,150,asr)
    for key,value in [('count',2),('series_mode','sequential'),('minimum',30),('maximum',59),('series_overlap',0)]:
        assert identity(req.model_copy(update={key:value}),150,asr)!=baseline
    monkeypatch.setattr(shorts,'VERSION',99)
    assert identity(req,150,asr)!=baseline


def test_commons_requires_actual_cc0_metadata():
    page={'pageid':17,'title':'File:Animation.webm','imageinfo':[{
        'mime':'video/webm','size':500,'url':'https://upload.wikimedia.org/animation.webm',
        'descriptionurl':'https://commons.wikimedia.org/wiki/File:Animation.webm',
        'extmetadata':{'LicenseShortName':{'value':'CC0'},'LicenseUrl':{'value':CC0},
                       'AttributionRequired':{'value':'false'}}}]}
    assert metadata(page)['eligible']
    page['imageinfo'][0]['extmetadata']['LicenseUrl']['value']='https://creativecommons.org/licenses/by-sa/4.0/'
    assert metadata(page)['license_status']=='manual_review'


def test_assets_remain_registered_when_removed_from_selection(tmp_path,monkeypatch):
    from factory import library,media
    monkeypatch.setattr(config,'DATA',tmp_path)
    monkeypatch.setattr(config,'DB',tmp_path/'db.sqlite3')
    root=tmp_path/'backgrounds';root.mkdir()
    video=root/'test.mp4'
    media.run([media.ffmpeg(),'-loglevel','error','-y','-f','lavfi','-i',
               'testsrc2=size=160x90:rate=10','-t','1','-an','-c:v','libx264','-threads','1',str(video)])
    assert library.register(video,'backgrounds','CC0-1.0','Generated fixture')==('test.mp4',False)
    second=root/'duplicate.mp4';second.write_bytes(video.read_bytes())
    assert library.register(second,'backgrounds','CC0-1.0','Generated fixture')==('test.mp4',True)
    library.preferences(video.name,favorite=True,removed=True)
    asset,=library.entries()
    assert asset['favorite'] and asset['removed'] and asset['availability']=='AVAILABLE'
    assert video.is_file() and not second.exists()


def test_experiment_variants_share_the_payoff_and_require_context_evidence():
    from factory.shorts import variants
    clip={'start_seconds':0,'end_seconds':58,'context_required':False,'payoff':'The team finds the lost dog.'}
    assert variants(clip,[15,32,44])==[('short',32,58),('standard',15,58),('extended',0,58)]
    assert variants({**clip,'context_required':True},[15,32,44])==[]


def test_variant_analytics_compares_equal_windows():
    from factory.analytics import experiment_comparisons
    item={'channel':'c','published_at':'2026-09-29T14:00:00Z','duration':48,'title':'Test',
          'period':{'start':'2026-10-01','end':'2026-10-30'},'metrics':{'views':50}}
    one={**item,'video_id':'one','lineage':{'edit_configuration':{'experiment_parent':'job:0','experiment_variant':'standard'}}}
    two={**item,'video_id':'two','lineage':{'edit_configuration':{'experiment_parent':'job:0','experiment_variant':'extended'}}}
    assert len(experiment_comparisons([one,two]))==1
    assert not experiment_comparisons([one,{**two,'published_at':'2026-10-15T14:00:00Z'}])

def test_event_windows_vary_with_activity_and_preserve_anchor():
    from factory.event_ranking import event_window
    isolated=event_window(80,150,35,60,[80],[])
    active=event_window(80,150,35,60,[72,80,88],[])
    assert isolated[0]<80<isolated[1]
    assert active[1]-active[0]>isolated[1]-isolated[0]
    assert event_window(20,30,35,60,[20],[]) is None


@pytest.mark.parametrize('speech',[[],[{'start':6,'end':8,'text':'Here it comes.'},
                                        {'start':34,'end':37,'text':'That worked!'}]])
def test_fine_inspection_preserves_coherent_multishot_and_late_payoff(monkeypatch,speech):
    from factory import visual_analysis
    monkeypatch.setattr(visual_analysis,'inspect_local_interval',
                        lambda *args:{'frames':30,'possible_cuts':[8,17,26,35]})
    data={'candidates':[{'start_seconds':0,'end_seconds':45,'content_type':'visual_activity'}],
          'selection_summary':'coarse'}
    result=visual_analysis.refine_local_plan('fixture.mp4',0,request(),data,{'segments':speech},45)
    clip,=result['candidates']
    assert (clip['start_seconds'],clip['end_seconds'])==(0,45)
    assert clip['editorial_review_required'] and clip['fine_review']['speech_segments']==len(speech)
    assert result['fine_decisions'][0]['status']=='review_required'


def test_fine_inspection_rejects_corroborated_break_but_keeps_manual_recovery(monkeypatch):
    from factory import visual_analysis
    observation=[{'time':t,'motion':.002,'audio_rms':.001} for t in (8,10,12,14)]
    observation.append({'time':15,'motion':.3,'audio_rms':.1})
    monkeypatch.setattr(visual_analysis,'inspect_local_interval',
        lambda *args:{'frames':30,'possible_cuts':[15,31],'observations':observation})
    data={'candidates':[{'start_seconds':0,'end_seconds':45,'content_type':'visual_activity'}],
          'selection_summary':'coarse'}
    result=visual_analysis.refine_local_plan('fixture.mp4',0,request(),data,{'segments':[]},45)
    assert result['candidates']==[]
    assert len(result['review_alternatives'])==1
    assert result['fine_decisions'][0]['reason']=='Audiovisual discontinuity'


def test_fine_inspection_rejects_invalid_interval_without_opening_media():
    from factory import visual_analysis
    data={'candidates':[{'start_seconds':10,'end_seconds':55,'content_type':'visual_activity'}],
          'selection_summary':'coarse'}
    result=visual_analysis.refine_local_plan('missing.mp4',0,request(),data,duration=45)
    assert not result['candidates']
    assert result['fine_decisions'][0]['reason']=='Invalid timestamps or duration'


def test_short_source_explicitly_cannot_satisfy_three_distinct_clips():
    result=plan(transcript(),request(),45,[{'time':20,'motion':.3,'cut':False}],lambda *_:None)
    assert len(result['candidates'])==1
    assert 'needs 105s' in result['selection_summary']
