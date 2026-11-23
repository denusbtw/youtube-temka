import json
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from factory import analytics,config,store


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path);monkeypatch.setattr(config,'SCRATCH',tmp_path/'scratch');monkeypatch.setattr(config,'DB',tmp_path/'jobs.db')
    monkeypatch.setenv('YOUTUBE_ANALYTICS_TOKEN_DIR',str(tmp_path/'analytics'))
    return tmp_path


def test_analytics_requires_separate_read_consent(isolated):
    directory=isolated/'analytics';directory.mkdir()
    (directory/'upload.json').write_text(json.dumps({'scopes':['https://www.googleapis.com/auth/youtube.upload']}))
    with pytest.raises(ValueError,match='AUTH_REQUIRED'):analytics.token_file('upload')
    with pytest.raises(ValueError):analytics.token_file('../secret')
    assert analytics.snapshot()['state']=='ANALYTICS_AUTH_REQUIRED'


def test_real_api_protocol_preserves_unknown_metrics_and_channel_identity(isolated,monkeypatch):
    directory=isolated/'analytics';directory.mkdir();(directory/'read.json').write_text(json.dumps({'scopes':list(analytics.SCOPES)}))
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/channels'):return httpx.Response(200,json={'items':[{'id':'channel-fixture','snippet':{'title':'Fixture channel'},'contentDetails':{'relatedPlaylists':{'uploads':'fixture-playlist'}}}]})
        if request.url.path.endswith('/playlistItems'):return httpx.Response(200,json={'items':[{'contentDetails':{'videoId':'video-fixture'}}]})
        if request.url.path.endswith('/videos'):return httpx.Response(200,json={'items':[{'id':'video-fixture','snippet':{'channelId':'channel-fixture','title':'Actual title','publishedAt':'2026-01-01T00:00:00Z','defaultAudioLanguage':'uk','categoryId':'27'},'contentDetails':{'duration':'PT1M20S'},'statistics':{'viewCount':'100','likeCount':'8'}}]})
        if request.url.params.get('metrics')=='engagedViews':return httpx.Response(400,json={'error':{'errors':[{'reason':'invalidParameter'}]}})
        dimension=request.url.params['dimensions']
        return httpx.Response(200,json={'columnHeaders':[{'name':dimension},{'name':'views'},{'name':'estimatedMinutesWatched'},{'name':'averageViewDuration'},{'name':'averageViewPercentage'}],'rows':[['video-fixture' if dimension=='video' else '2026-01-01',80,40,30,37.5]]})
    class Client:
        def __init__(self,*args):self.client=httpx.Client(transport=httpx.MockTransport(handler))
        def request(self,*args,**kw):return self.client.request(*args,**kw)
        def close(self):self.client.close()
    monkeypatch.setattr(analytics,'Drive',Client)
    discovered=analytics.discover();assert len(discovered['connections'])==1
    result=analytics.sync('channel-fixture');channel=result['channels'][0];video=channel['videos'][0]
    assert channel['connection']['state']=='READY' and channel['connection']['last_sync']
    assert video['metrics']['views']==80 and video['lifetime_statistics']['viewCount']==100
    assert video['metrics']['engagedViews'] is None and video['impressions'] is None and video['ctr'] is None
    assert video['video_type']=='unknown' and video['duration']==80
    assert all(r.method=='GET' for r in calls)
    assert channel['report']['recommendations']['status']=='INSUFFICIENT_DATA'


def test_recommendations_do_not_confuse_small_samples_with_strategy():
    assert analytics.recommendations([{'metrics':{'views':1000,'averageViewPercentage':99}}])['status']=='INSUFFICIENT_DATA'
    videos=[{'language':'uk' if i<10 else 'en','video_type':'shorts','duration':25 if i<10 else 55,'published_at':'2026-01-01T12:00:00Z','metrics':{'views':100,'averageViewPercentage':80 if i<10 else 50}} for i in range(20)]
    result=analytics.recommendations(videos)
    assert result['status']=='OBSERVATIONAL' and result['policies_changed'] is False
    assert any(r['dimension']=='language' and r['observed_group']=='uk' for r in result['items'])
    assert all('not a causal' in r['reason'] for r in result['items'])


def test_analytics_failure_retains_previous_success_timestamp(isolated,monkeypatch):
    with store.connect() as db:db.execute('INSERT INTO analytics_connections(channel,alias,title,last_sync) VALUES(?,?,?,?)',('fixture','missing','Fixture',123))
    with pytest.raises(ValueError,match='AUTH_REQUIRED'):analytics.sync('fixture')
    saved=analytics.connections()[0]
    assert saved['last_sync']==123 and saved['state']=='FAILED'


def test_duration_parsing_does_not_infer_short_format():
    assert analytics.duration_seconds('PT2H17M30S')==8250
    assert analytics.duration_seconds('bad') is None


def test_unsupported_metric_does_not_hide_supported_report_columns():
    def handler(request):
        if request.url.params['metrics']!='views':return httpx.Response(400,json={})
        return httpx.Response(200,json={'columnHeaders':[{'name':'day'},{'name':'views'}],'rows':[['2026-11-01',12]]})
    class Client:pass
    client=Client();client.client=httpx.Client(transport=httpx.MockTransport(handler))
    diagnostics=[]
    try:rows=analytics.metric_rows(client,{'dimensions':'day','metrics':'views,averageViewDuration'},diagnostics)
    finally:client.client.close()
    assert rows==[{'day':'2026-11-01','views':12}]
    assert 'averageViewDuration' not in rows[0] and diagnostics


@pytest.mark.parametrize('dedicated',[False,True])
def test_managed_reporting_preference_and_channel_isolation(isolated,monkeypatch,dedicated):
    key='leonida_mayhem'
    channel='leonida-fixture'
    publish=isolated/'publish'
    separate=isolated/'separate'
    monkeypatch.setenv('FACTORY_MANAGED_YOUTUBE_DIR',str(publish))
    monkeypatch.setenv('FACTORY_MANAGED_ANALYTICS_DIR',str(separate))
    publish.mkdir()
    separate.mkdir()
    fallback=publish/(key+'.json')
    preferred=separate/(key+'.json')
    fallback.write_text(json.dumps({'youtube_channel_id':channel,'scopes':[analytics.READONLY]}))
    if dedicated:preferred.write_text(json.dumps({'youtube_channel_id':channel,'scopes':list(analytics.SCOPES)}))
    with store.connect() as db:
        db.execute("UPDATE youtube_profiles SET channel_id=?,status='CONNECTED' WHERE channel_key=?",(channel,key))
        db.execute("INSERT INTO analytics_reports VALUES(?,?,?)",('vice-fixture','{}',123))
    calls=[]
    remote_id=channel
    fail_refresh=False
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/channels'):
            return httpx.Response(200,json={'items':[{'id':remote_id,'snippet':{'title':'Leonida Mayhem'}}]})
        assert request.method=='GET' and request.url.params['ids']=='channel=='+channel
        columns=[{'name':m} for m in request.url.params['metrics'].split(',')]
        daily='dimensions' in request.url.params
        return httpx.Response(200,json={'columnHeaders':([{'name':'day'}] if daily else [])+columns,
                                        'rows':[] if daily else [[0]*len(columns)]})
    class Client:
        def __init__(self,path):
            assert path==(preferred if dedicated else fallback)
            if fail_refresh: raise ValueError('Refresh failed')
            self.client=httpx.Client(transport=httpx.MockTransport(handler))
        def request(self,*a,**kw): return self.client.request(*a,**kw)
        def close(self):self.client.close()
    monkeypatch.setattr(analytics,'Drive',Client)
    assert not analytics.discover()['errors']
    report=analytics.sync(channel)['channels'][0]['report']
    assert report['daily']==[]
    if not dedicated:
        assert report['totals']['views']==0
        assert report['credential_strategy']=='verified_publishing_readonly'
    remote_id='vice-fixture'
    calls.clear()
    with pytest.raises(ValueError,match='different channel'):analytics.sync(channel)
    assert len(calls)==1 and calls[0].url.path.endswith('/channels')
    fail_refresh=True
    with pytest.raises(ValueError,match='Refresh failed'):analytics.sync(channel)
    assert analytics.snapshot(channel)['channels'][0]['report']==report
    with store.connect() as db:
        assert db.execute("SELECT seen FROM analytics_reports WHERE channel='vice-fixture'").fetchone()[0]==123
    if not dedicated:
        fallback.write_text(json.dumps({'youtube_channel_id':channel,'scopes':['https://www.googleapis.com/auth/youtube.upload']}))
        with pytest.raises(ValueError,match='youtube.readonly'):analytics.sync(channel)


def test_reporting_blocks_monetary_metrics_and_out_of_scope_endpoints():
    with pytest.raises(ValueError,match='non-monetary'):
        analytics._request(None,analytics.REPORTS,{'metrics':'estimatedRevenue'})
    with pytest.raises(ValueError,match='Unsupported Analytics operation'):
        analytics._request(None,'https://youtubeanalytics.googleapis.com/v2/groups',{})
