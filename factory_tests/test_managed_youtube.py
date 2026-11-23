import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from factory import analytics, api, config, publisher, store, youtube_channels, youtube_oauth
from factory.drive import Drive, OAuthRefreshError
from factory.legacy_drive_oauth import DriveError, OAuthClient, receive_code


A, B = 'leonida_mayhem', 'thevicescope'


def channel(key, id):
    profile = youtube_channels.PROFILES[key]
    return {'id': id, 'snippet': {'title':profile['title'],'customUrl':profile['handle']}}


@pytest.fixture
def registry(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path)
    monkeypatch.setattr(config,'DB',tmp_path/'jobs.sqlite3')
    monkeypatch.setattr(config,'SCRATCH',tmp_path/'scratch')
    monkeypatch.setattr(config,'TOKEN','fixture-controller-token-with-32-characters')
    monkeypatch.setenv('FACTORY_MANAGED_YOUTUBE_DIR',str(tmp_path/'upload'))
    monkeypatch.setenv('FACTORY_MANAGED_ANALYTICS_DIR',str(tmp_path/'analytics'))
    monkeypatch.setenv('YOUTUBE_TOKEN_DIR',str(tmp_path/'legacy'))
    monkeypatch.setenv('YOUTUBE_ANALYTICS_TOKEN_DIR',str(tmp_path/'legacy-analytics'))
    return tmp_path


def test_independent_oauth_reconnect_and_atomic_storage(registry,monkeypatch):
    client = registry/'client.json'
    client.write_text(json.dumps({'installed':{'client_id':'test','client_secret':'fixture-only'}}))
    monkeypatch.setattr(youtube_oauth,'receive_code',lambda *args,**kw:('fixture-code','http://localhost/callback'))
    responses = iter([
        {'refresh_token':'first-test-refresh','access_token':'a'},
        {'refresh_token':'second-test-refresh','access_token':'b'},
        {'refresh_token':'wrong-test-refresh','access_token':'b'},
        {'refresh_token':'new-test-refresh','access_token':'renamed-a'},
    ])
    monkeypatch.setattr(OAuthClient,'exchange',lambda *args:next(responses))
    renamed = channel(A,'channel-a')
    renamed['snippet']['title'] = 'New display title'
    results = {'a':channel(A,'channel-a'),'b':channel(B,'channel-b'),'renamed-a':renamed}
    client_type = httpx.Client
    def handler(request):
        assert request.url.params['mine'] == 'true'
        return httpx.Response(200,json={'items':[results[request.headers['Authorization'].split()[-1]]]})
    monkeypatch.setattr(youtube_oauth.httpx,'Client',lambda **kw:client_type(transport=httpx.MockTransport(handler),**kw))
    a,b = (youtube_channels.directory()/(k+'.json') for k in (A,B))
    for key,path in [(A,a),(B,b)]:
        youtube_oauth.authorize(client,path,channel_key=key,confirm_identity=lambda _:True)
    original = a.read_bytes()
    assert json.loads(original)['youtube_channel_id'] == 'channel-a'
    assert json.loads(b.read_bytes())['youtube_channel_id'] == 'channel-b'
    with pytest.raises(DriveError,match='identity check failed'):
        youtube_oauth.authorize(client,a,channel_key=A,reconnect=True)
    assert a.read_bytes() == original
    youtube_oauth.authorize(client,a,channel_key=A,reconnect=True)
    assert next(a.parent.glob(A+'.*.bak')).read_bytes() == original
    assert json.loads(a.read_bytes())['refresh_token'] == 'new-test-refresh'
    assert json.loads(b.read_bytes())['refresh_token'] == 'second-test-refresh'


def test_state_rejects_wrong_callback_and_accepts_legitimate_state(monkeypatch):
    from threading import Thread
    from urllib.request import urlopen
    from urllib.error import HTTPError
    import webbrowser
    codes = []
    def browser(url):
        params = parse_qs(urlsplit(url).query)
        redirect,state = params['redirect_uri'][0],params['state'][0]
        def visit():
            try:
                urlopen(redirect+'?state=invalid&code=wrong',timeout=4).close()
            except HTTPError as error:
                codes.append(error.code)
            with urlopen(redirect+'?state='+state+'&code=expected',timeout=4) as response:
                codes.append(response.status)
        Thread(target=visit,daemon=True).start()
        return True
    monkeypatch.setattr(webbrowser,'open',browser)
    code,_ = receive_code(OAuthClient('test','fixture-only'),'fixture-verifier',open_browser=True)
    assert code == 'expected' and codes[0] == 400


@pytest.mark.parametrize(('status','error','expected'),[(400,'invalid_grant','REAUTHORIZATION_REQUIRED'),(503,'unavailable','TOKEN_EXPIRED')])
def test_refresh_state(registry,monkeypatch,status,error,expected):
    path = registry/'token.json'
    path.write_text(json.dumps({'client_id':'test','client_secret':'fixture-only','refresh_token':'fixture-only'}))
    client_type = httpx.Client
    def handler(request):
        assert 'grant_type=refresh_token' in request.content.decode()
        return httpx.Response(status,json={'error':error})
    monkeypatch.setattr(httpx,'Client',lambda **kw:client_type(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(OAuthRefreshError) as raised:
        Drive(path)
    assert raised.value.status == expected


def test_registry_rejects_legacy_collision_and_keeps_existing_credentials(registry,monkeypatch):
    root = youtube_channels.directory()
    root.mkdir()
    (root/(A+'.json')).write_text(json.dumps({'youtube_channel_id':'channel-a'}))
    class Client:
        def __init__(self,*_): pass
        def request(self,*_,**__): return httpx.Response(200,json={'items':[channel(A,'channel-a')]})
        def close(self): pass
    monkeypatch.setattr('factory.drive.Drive',Client)
    with store.connect() as db:
        db.execute('INSERT INTO channels(id,alias,title) VALUES(?,?,?)',('channel-a','main','Legacy'))
    youtube_channels.discover(A)
    assert youtube_channels.profiles()[0]['status'] == 'ERROR'
    assert publisher.channels()[0]['alias'] == 'main'
    assert youtube_channels.profiles()[0]['channel_id'] is None


def test_adopt_verified_legacy_without_copy_or_public_policy(registry,monkeypatch):
    legacy = registry/'legacy'
    legacy.mkdir()
    token = legacy/'main.json'
    token.write_text('fixture original legacy credential')
    class Client:
        def __init__(self,path): assert path == token
        def request(self,*_,**__): return httpx.Response(200,json={'items':[channel(B,'channel-b')]})
        def close(self): pass
    monkeypatch.setattr('factory.drive.Drive',Client)
    with store.connect() as db:
        db.execute('INSERT INTO channels(id,alias,title) VALUES(?,?,?)',('channel-b','main','Old Title'))
    youtube_channels.discover(B,legacy_alias='main')
    youtube_channels.discover(B)
    assert publisher.channels()[0]['managed_profile']['channel_key'] == B
    assert publisher.channels()[0]['title'] == 'TheViceScope'
    assert token.read_text() == 'fixture original legacy credential'
    assert not youtube_channels.directory().exists()
    with pytest.raises(ValueError,match='private'):
        publisher.set_policy('channel-b',3,True,False)


@pytest.mark.parametrize(('key','mode','explicit'),[(A,'shorts',False),(B,'longs',False),(B,'shorts',True)])
def test_profiles_and_format_overrides_through_http(registry,key,mode,explicit):
    body = {'source':'https://www.youtube.com/watch?v=abcdefghijk','rights_confirmed':True,'youtube_channel_key':key}
    if explicit: body['mode']=mode
    client = TestClient(api.app,headers={'Authorization':'Bearer '+config.TOKEN})
    profiles = client.get('/api/youtube/profiles').json()
    assert {p['channel_key'] for p in profiles} == {A,B}
    assert all(p['status']=='NOT_CONNECTED' and p['channel_id'] is None for p in profiles)
    with patch.object(api.media,'validate_url'), patch.object(api,'preflight'):
        result = client.post('/api/jobs',json=body)
    assert result.status_code == 201, result.text
    request = result.json()['request']
    assert (request['youtube_channel_key'],request['mode'],request['metadata_language']) == (key,mode,'en')


def setup_channel(key,id):
    with store.connect() as db:
        db.execute('INSERT INTO channels(id,alias,title) VALUES(?,?,?)',(id,key,youtube_channels.PROFILES[key]['title']))
        db.execute("UPDATE youtube_profiles SET channel_id=?,status='CONNECTED' WHERE channel_key=?",(id,key))


def test_destination_locked_private_and_retries_are_fenced(registry):
    setup_channel(A,'channel-a')
    setup_channel(B,'channel-b')
    job = store.create({'source':'upload:clip.mp4','youtube_channel_key':A,'publish_channel':'channel-a','mode':'shorts'})
    lease = store.claim('test')['lease']
    store.update(job['id'],lease,state='READY',result={'cloud_verified':True,'outputs':[{'name':'clip.mp4','title':'Test','approved':True,'drive':{'id':'fixture'}}]})
    with pytest.raises(ValueError,match='different YouTube'):
        publisher.enqueue(job['id'],0,'channel-b')
    with pytest.raises(ValueError,match='private'):
        publisher.enqueue(job['id'],0,'channel-a',privacy='unlisted')
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = set(pool.map(lambda _:publisher.enqueue(job['id'],0,'channel-a'),range(4)))
    assert len(ids) == 1
    youtube_channels.enable(A,False)
    assert publisher.claim() is None
    youtube_channels.enable(A,True)
    publication = publisher.claim()
    assert publication['channel'] == 'channel-a' and json.loads(publication['metadata'])['channel_key'] == A
    with pytest.raises(ValueError,match='upload is active'):
        youtube_channels.enable(A,False)
    publisher.checkpoint(publication['id'],state='UNCERTAIN')
    assert not publisher.retry(publication['id'])
    with pytest.raises(ValueError,match='OAuth missing'):
        publisher.token_file(A)


def test_analytics_requires_channel_specific_credentials(registry,monkeypatch):
    setup_channel(A,'channel-a')
    path = youtube_channels.directory(analytics=True)/(A+'.json')
    path.parent.mkdir()
    path.write_text(json.dumps({'youtube_channel_id':'channel-a','scopes':list(analytics.SCOPES)}))
    class Wrong:
        def __init__(self,*_): pass
        def request(self,*_,**__): return httpx.Response(200,json={'items':[channel(B,'channel-b')]})
        def close(self): pass
    monkeypatch.setattr(analytics,'Drive',Wrong)
    assert analytics.discover()['connections'] == []


def test_upload_rejects_cross_channel_token_before_any_write(registry,monkeypatch):
    setup_channel(A,'channel-a')
    path=youtube_channels.directory()/(A+'.json')
    path.parent.mkdir()
    path.write_text('{}')
    job=store.create({'source':'upload:clip.mp4','youtube_channel_key':A,'publish_channel':'channel-a'})
    lease=store.claim('test')['lease']
    store.update(job['id'],lease,state='READY',result={'cloud_verified':True,'outputs':[{'name':'clip.mp4','title':'Test','approved':True,'drive':{'id':'fixture'}}]})
    pid=publisher.enqueue(job['id'],0,'channel-a')
    calls=[]
    class Wrong:
        def __init__(self,credential): assert credential == path
        def request(self,method,url,**kw):
            calls.append((method,url))
            return httpx.Response(200,json={'items':[channel(B,'channel-b')]})
        def close(self): pass
    monkeypatch.setattr(publisher,'Drive',Wrong)
    publisher.upload(publisher.claim())
    assert calls == [('GET',publisher.YT+'/channels')]
    entry=next(p for p in publisher.history() if p['id']==pid)
    assert entry['state']=='FAILED' and entry['video_id'] is None
