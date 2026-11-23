from fastapi.testclient import TestClient
from factory.api import app
from factory import config
import pytest


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('FACTORY_LOCAL_WORKER','0')
    monkeypatch.setenv('FACTORY_PUBLISHER','0')
    monkeypatch.setattr(config,'TOKEN','a'*40)
    monkeypatch.setattr(config,'DATA',tmp_path)
    monkeypatch.setattr(config,'SCRATCH',tmp_path/'scratch')
    monkeypatch.setattr(config,'DB',tmp_path/'db.sqlite3')
    monkeypatch.setattr(config,'MODEL',config.MODEL)
    monkeypatch.setattr(config,'ASR_MODEL',config.ASR_MODEL)
    with TestClient(app) as client:
        yield client


def test_control_endpoints_require_auth(client):
    assert client.get('/api/jobs').status_code==401
    assert client.post('/api/session',json={'token':'wrong'}).status_code==401
    assert client.post('/api/session',json={'token':'a'*40}).status_code==200
    assert client.get('/api/settings').status_code==200
    assert client.get('/api/jobs').json()==[]
    assert client.delete('/api/session').status_code==200
    assert client.get('/api/jobs').status_code==401


def test_readiness_json_is_compatible_with_case_insensitive_clients(client,monkeypatch):
    from factory import deployment
    monkeypatch.setattr(deployment,'readiness',lambda:{'Analytics':'AUTHORIZATION_REQUIRED','SQLite':'READY'})
    assert client.get('/api/readiness').status_code==401
    client.headers['Authorization']='Bearer '+'a'*40
    result=client.get('/api/readiness').json()
    assert len({key.casefold() for key in result})==len(result)
    assert set(result['services'])=={'local_worker','publisher','analytics'}


def test_compilation_preflight_reports_each_source(client,monkeypatch):
    from factory import media
    def probe(source):
        if source.endswith('missing.mp4'):
            raise ValueError('Uploaded source is missing')
        return {'duration':600,'resolutions':[720],'platform':'local','title':'Owned fixture'}
    monkeypatch.setattr(media,'probe',probe)
    body={'source':'upload:owned.mp4','sources':[{'source':'upload:owned.mp4'},{'source':'upload:missing.mp4'}],
          'rights_confirmed':True,'workflow':'compilation'}
    assert client.post('/api/preflight',json=body).status_code==401
    client.headers['Authorization']='Bearer '+'a'*40
    response=client.post('/api/preflight',json=body)
    assert response.status_code==200
    assert response.json()['ready'] is False
    assert response.json()['sources'][0]['selected_seconds']==600
    assert response.json()['sources'][1]['error']=='Uploaded source is missing'


def test_invalid_sources_and_rights(client):
    client.headers['Authorization']='Bearer '+'a'*40
    for url in ['http://127.0.0.1','https://youtube.com.attacker.invalid/x','file:///etc/passwd','https://twitch.tv@127.0.0.1/x']:
        assert client.post('/api/jobs',json={'source':url,'rights_confirmed':True}).status_code==422
    assert client.post('/api/jobs',json={'source':'anything','rights_confirmed':False}).status_code==422


def test_missing_drive_is_actionable(client,monkeypatch):
    monkeypatch.delenv('DRIVE_TOKEN_FILE',raising=False)
    client.headers['Authorization']='Bearer '+'a'*40
    assert client.post('/api/settings/check-drive').status_code==409
    assert client.get('/api/settings').json()['drive_configured'] is False


def test_invalid_worker_lease(client):
    client.headers['Authorization']='Bearer '+'a'*40
    assert client.post('/api/workers/jobs/absent',json={'lease':'wrong','state':'READY'}).status_code==409


def test_settings_model_selection_is_persisted_without_credentials(client):
    client.headers['Authorization']='Bearer '+'a'*40
    assert client.post('/api/settings/models',json={'ollama_model':'qwen2.5:3b','whisper_model':'tiny'}).status_code==200
    assert client.get('/api/settings').json()['whisper_model']=='tiny'
    assert (config.DATA/'settings.json').is_file()


def test_two_isolated_workers_claim_independent_jobs_and_are_fenced(client):
    from factory import store
    client.headers['Authorization']='Bearer '+'a'*40
    for i in range(2):
        store.create({'source':f'https://www.youtube.com/watch?v=fixture{i}','save_drive':True})
    a=client.post('/api/workers/claim',json={'worker':'isolated-a'}).json()
    b=client.post('/api/workers/claim',json={'worker':'isolated-b'}).json()
    assert a['job']['id']!=b['job']['id']
    assert len(store.workers())==2
    wrong=client.post('/api/workers/jobs/'+a['job']['id'],json={'lease':b['lease'],'state':'READY'})
    assert wrong.status_code==409
    assert client.post('/api/workers/jobs/'+a['job']['id'],json={'lease':a['lease'],'state':'READY'}).status_code==200


def test_worker_asset_transfer_is_authenticated_and_versioned(client):
    from factory import store
    folder=config.DATA/'backgrounds';folder.mkdir()
    (folder/'approved.mp4').write_bytes(b'approved')
    assert client.get('/api/workers/assets/backgrounds/approved.mp4?sha256=wrong').status_code==401
    client.headers['Authorization']='Bearer '+'a'*40
    store.create({'source':'https://www.youtube.com/watch?v=fixture','save_drive':True,'preset':'auto'})
    claim=client.post('/api/workers/claim',json={'worker':'remote'}).json()
    item=claim['assets'][0]
    url=f"/api/workers/assets/backgrounds/approved.mp4?sha256={item['sha256']}"
    assert client.get(url).content==b'approved'
    (folder/'approved.mp4').write_bytes(b'new-version')
    assert client.get(url).status_code==409
    assert client.get('/api/workers/assets/secrets/approved.mp4?sha256=wrong').status_code==404


def test_remote_store_supports_source_cleanup_query(client):
    from factory.worker import RemoteStore
    client.headers['Authorization']='Bearer '+'a'*40
    assert RemoteStore(client).list_jobs()==[]


def test_publication_edit_requires_auth_and_reports_unavailable_item(client):
    assert client.patch('/api/publisher/absent',json={'title':'Changed'}).status_code==401
    client.headers['Authorization']='Bearer '+'a'*40
    assert client.patch('/api/publisher/absent',json={'title':'Changed'}).status_code==409


def test_retry_keeps_original_asset_version_and_automatic_choice(client):
    from factory import store
    folder=config.DATA/'backgrounds';folder.mkdir()
    (folder/'original.mp4').write_bytes(b'approved-original-version')
    client.headers['Authorization']='Bearer '+'a'*40
    job=store.create({'source':'https://www.youtube.com/watch?v=fixture','save_drive':True,'preset':'auto'})
    first=client.post('/api/workers/claim',json={'worker':'remote'}).json()
    store.cancel(job['id']);assert store.retry(job['id'])
    (folder/'original.mp4').write_bytes(b'changed-version')
    (folder/'another.mp4').write_bytes(b'another-automatic-choice')
    second=client.post('/api/workers/claim',json={'worker':'remote'}).json()
    assert second['assets']==first['assets']
    assert second['assets'][0]['name']=='original.mp4'
    assert store.get(job['id'])['result']['asset_manifest']==first['assets']

def test_job_visibility_bulk_restore_and_active_protection(client):
    from factory import store
    client.headers['Authorization']='Bearer '+'a'*40
    requests={'source':'https://www.youtube.com/watch?v=fixture','rights_confirmed':True}
    first=store.create(requests)
    second=store.create(requests)
    first_claim=store.claim('fixture')
    store.update(first['id'],first_claim['lease'],state='READY')
    response=client.post('/api/jobs/visibility',json={'ids':[first['id'],second['id']],'status':'archived'})
    assert response.status_code==409
    assert client.get('/api/jobs/'+first['id']).json()['visibility']=='recent'
    second_claim=store.claim('fixture')
    store.update(second['id'],second_claim['lease'],state='FAILED')
    for status in ('archived','trash','recent'):
        assert client.post('/api/jobs/visibility',json={'ids':[first['id'],second['id']],'status':status}).status_code==200
        assert client.get('/api/jobs/'+first['id']).json()['visibility']==status
    preview=client.get('/api/jobs/'+first['id']+'/deletion-preview').json()
    assert preview['permanent_delete_available'] is False
    assert preview['publications']==preview['dependent_edits']==0

def test_empty_trashed_record_requires_confirmation_and_protects_workspace(client):
    from factory import store,config
    client.headers['Authorization']='Bearer '+'a'*40
    job=store.create({'source':'https://www.youtube.com/watch?v=fixture','rights_confirmed':True})
    jid=job['id']
    claim=store.claim('fixture')
    store.update(jid,claim['lease'],state='FAILED')
    assert client.post('/api/jobs/visibility',json={'ids':[jid],'status':'trash'}).is_success
    endpoint='/api/jobs/'+jid
    assert client.post(endpoint+'/delete-record',json={'confirmation':'no'}).status_code==422
    workspace=config.SCRATCH/jid
    workspace.mkdir()
    assert client.post(endpoint+'/delete-record',json={'confirmation':jid}).status_code==409
    workspace.rmdir()
    assert client.get(endpoint+'/deletion-preview').json()['permanent_delete_available']
    assert client.post(endpoint+'/delete-record',json={'confirmation':jid}).status_code==200
    assert client.get(endpoint).status_code==404

def test_history_paginates_and_filters_archived_rows(client):
    from factory import store
    client.headers['Authorization']='Bearer '+'a'*40
    source={'source':'https://www.youtube.com/watch?v=history-fixture','rights_confirmed':True}
    for _ in range(3):
        job=store.create(source)
        claim=store.claim('fixture')
        store.update(job['id'],claim['lease'],state='READY')
    assert client.post('/api/jobs/visibility',json={'ids':[job['id']],'status':'archived'}).is_success
    page=client.get('/api/job-history?limit=1&search=HISTORY-FIXTURE').json()
    assert len(page['items'])==1 and page['has_more']
    assert page['items'][0]['id']!=job['id']
    archive=client.get('/api/job-history?scope=archived').json()
    assert [row['id'] for row in archive['items']]==[job['id']]


def test_story_can_fork_manual_replan_using_the_saved_transcript(client, monkeypatch):
    from factory import media, store
    from factory.models import JobRequest

    client.headers['Authorization'] = 'Bearer ' + 'a'*40
    source = 'upload:owned.mp4'
    request = JobRequest(source=source, rights_confirmed=True, workflow='story',
                         workflow_version=2, story_mode='chronological', save_drive=False)
    original = store.create(request.model_dump())
    workspace = config.SCRATCH / original['id']
    workspace.mkdir(parents=True)
    (workspace/'transcript.json').write_text('{"language":null,"segments":[]}', encoding='utf-8')
    claim = store.claim('fixture')
    store.update(original['id'], claim['lease'], state='READY')
    monkeypatch.setattr(media, 'local_source', lambda _: workspace/'owned.mp4')
    response = client.post('/api/jobs/'+original['id']+'/replan', json={'intervals':[[0,35]]})
    assert response.status_code == 201, response.text
    job = response.json()
    assert job['request']['intervals'] == [[0,35]]
    assert job['request']['workflow'] is None
    assert (config.SCRATCH/job['id']/'transcript.json').is_file()
