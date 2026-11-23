"""Direct Drive -> YouTube bounded transfer, review gate and durable idempotency.

No public visibility is possible without a per-channel policy and an explicit
public/scheduled queue request. Uncertain outcomes cannot start a new upload.
"""
import json
import os
from pathlib import Path
import re
import time
import uuid
from datetime import datetime,timezone,timedelta
import httpx
from . import config,store
from .drive import Drive, OAuthRefreshError, BASE, CHUNK, acknowledged
from . import youtube_channels as managed

YT='https://www.googleapis.com/youtube/v3'


def token_file(alias):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',alias):
        raise ValueError('Invalid YouTube credential alias')
    root=(managed.directory() if alias in managed.PROFILES else
          Path(os.environ.get('YOUTUBE_TOKEN_DIR','secrets/youtube'))).resolve()
    path=root/(alias+'.json')
    if not path.is_file():
        raise ValueError('YouTube OAuth missing. Mount the existing authorized-user JSON in YOUTUBE_TOKEN_DIR.')
    return path


def discover_channels():
    root=Path(os.environ.get('YOUTUBE_TOKEN_DIR','secrets/youtube'))
    errors=[]
    for path in root.glob('*.json'):
        if path.stem in managed.PROFILES or not re.fullmatch(r'[A-Za-z0-9_-]+',path.stem):
            continue
        client=None
        try:
            client=Drive(token_file(path.stem))
            response=client.request('GET',YT+'/channels',params={'part':'snippet','mine':'true'})
            for channel in response.json()['items']:
                with store.connect() as db:
                    db.execute('INSERT INTO channels(id,alias,title) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title WHERE channels.alias=excluded.alias',(channel['id'],path.stem,channel['snippet']['title']))
        except Exception:
            errors.append({'alias':path.stem,'error':'Authorization/channel lookup failed. Check OAuth scopes, API access and token validity.'})
        finally:
            if client:
                client.close()
    for key in managed.PROFILES:
        managed.discover(key)
    return {'channels':channels(),'profiles':managed.profiles(),'errors':errors}


def channels():
    profiles={p['channel_id']:p for p in managed.profiles() if p['channel_id']}
    with store.connect() as db:
        return [{**dict(row),'preferences':store.preferences(row['id']),'managed_profile':profiles.get(row['id'])}
                for row in db.execute('SELECT c.*,coalesce(s.interval_minutes,120) interval_minutes FROM channels c LEFT JOIN channel_schedules s ON s.channel=c.id')]


def set_policy(channel,daily_limit,public_allowed,automatic,interval_minutes=120,preferences=None):
    if automatic and not public_allowed:
        raise ValueError('Automatic publishing requires explicit public publishing authorization for this channel')
    with store.connect() as db:
        if (public_allowed or automatic) and db.execute('SELECT 1 FROM youtube_profiles WHERE channel_id=?',(channel,)).fetchone():
            raise ValueError('Managed YouTube channels accept manually approved private uploads only.')
        if not db.execute('UPDATE channels SET daily_limit=?,public_allowed=?,automatic=? WHERE id=?',(daily_limit,public_allowed,automatic,channel)).rowcount:
            raise ValueError('Connect this authorized channel first')
        db.execute('INSERT OR REPLACE INTO channel_schedules VALUES(?,?)',(channel,interval_minutes))
        if preferences is not None:
            db.execute('INSERT OR REPLACE INTO channel_preferences VALUES(?,?)',(channel,json.dumps(preferences,ensure_ascii=False)))


def history():
    with store.connect() as db:
        rows=[]
        for row in db.execute('SELECT * FROM publications ORDER BY created DESC LIMIT 200'):
            value=dict(row)
            value.pop('session',None)
            value['editable']=row['session'] is None and row['video_id'] is None and row['state'] in ('QUEUED','FAILED')
            value['metadata']=json.loads(value['metadata'])
            artifact=db.execute('SELECT state,error FROM publication_artifacts WHERE publication=?',(row['id'],)).fetchone()
            value['thumbnail_status']=dict(artifact) if artifact else None
            rows.append(value)
        return rows


def publication_status(privacy,publish_at):
    if privacy not in ('private','public','unlisted'):
        raise ValueError('Invalid privacy mode')
    status={'privacyStatus':privacy,'selfDeclaredMadeForKids':False}
    if publish_at:
        when=datetime.fromisoformat(publish_at.replace('Z','+00:00'))
        if when.tzinfo is None or when<=datetime.now(timezone.utc):
            raise ValueError('Schedule must be a future timezone-aware timestamp')
        status={'privacyStatus':'private','publishAt':when.astimezone(timezone.utc).isoformat(),'selfDeclaredMadeForKids':False}
    return status


def credited_description(description,attribution):
    """Preserve the operator's explicit credit, including when metadata is edited."""
    credit=(attribution or '').strip()
    if credit and credit not in description:
        description=(description.rstrip()+'\n\n' if description.strip() else '')+credit
    if len(description.encode('utf-8'))>5000:
        raise ValueError('YouTube descriptions, including source credit, are limited to 5000 UTF-8 bytes. Shorten the description.')
    if '<' in description or '>' in description:
        raise ValueError('YouTube descriptions cannot contain < or >. Remove HTML markup.')
    return description


def valid_title(title):
    if not title.strip() or len(title)>100 or '<' in title or '>' in title:
        raise ValueError('Provide a nonempty title up to 100 characters without < or >')
    return title


def needs_editorial_review(job,index):
    output=job['result'].get('outputs',[])[index]
    if output.get('editorial',{}).get('status') in ('REJECTED','NEEDS_REVIEW'):return True
    if output.get('publishing_metadata',{}).get('review_required'):return True
    candidates=job['result'].get('candidates',[])
    selected=candidates if job['request'].get('mode')=='longs' else candidates[index:index+1]
    return any(c.get('editorial_review_required') or c.get('selection_method')=='heuristic' or c.get('score',100)<50 or c.get('confidence',1)<.6 for c in selected)


def edit(pid,title,description,privacy='private',publish_at=None):
    """Edit only before remote effects; an expired schedule can be corrected safely."""
    status=publication_status(privacy,publish_at)
    valid_title(title)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM publications WHERE id=?',(pid,)).fetchone()
        if not row or row['state'] not in ('QUEUED','FAILED') or row['session'] or row['video_id']:
            raise ValueError('Only queued/failed items without an upload session or video ID can be edited')
        policy=db.execute('SELECT * FROM channels WHERE id=?',(row['channel'],)).fetchone()
        if managed.require_ready(row['channel'], db) and (privacy != 'private' or publish_at):
            raise ValueError('Managed YouTube channels accept private uploads only.')
        if not policy or ((privacy=='public' or publish_at) and not policy['public_allowed']):
            raise ValueError('Public publishing has not been explicitly enabled for this channel')
        metadata=json.loads(row['metadata'])
        if metadata.get('strict_metadata') and (title!=metadata['snippet']['title'] or description!=metadata['snippet']['description']):
            raise ValueError('Edit and approve the versioned metadata in Editor before queueing the revision')
        if publish_at:
            from .calendar import schedule
            cadence=db.execute('SELECT interval_minutes FROM channel_schedules WHERE channel=?',(row['channel'],)).fetchone()
            schedule(db,row['channel'],datetime.fromisoformat(status['publishAt']),policy['daily_limit'],cadence['interval_minutes'] if cadence else 120,store.preferences(row['channel']).get('timezone','UTC'),exclude=pid,manual=True)
        job=db.execute('SELECT request FROM jobs WHERE id=?',(row['job'],)).fetchone()
        attribution=json.loads(job['request']).get('source_attribution','') if job else ''
        metadata['snippet'].update(title=title,description=credited_description(description,attribution))
        metadata['status']=status
        metadata['automatic']=False
        db.execute("UPDATE publications SET metadata=?,state='QUEUED',error=NULL WHERE id=?",(json.dumps(metadata,ensure_ascii=False),pid))


def enqueue(jid,index,channel,title=None,description=None,privacy='private',publish_at=None,automatic=False):
    if automatic and not config.AUTOMATIC_PUBLISHING:
        raise ValueError('Automatic publishing is disabled; approve this exact render and metadata manually')
    job=store.get(jid)
    if not job or job['state']!='READY' or index<0:
        raise ValueError('Only READY outputs can be queued')
    if job['request'].get('publish_channel') not in (None, channel):
        raise ValueError('The job is bound to a different YouTube channel.')
    try:
        output=job['result']['outputs'][index]
    except (KeyError,IndexError):
        raise ValueError('Output not found') from None
    if not output.get('drive') or not job['result'].get('cloud_verified'):
        raise ValueError('Verify the Drive upload before publishing')
    if output.get('editorial',{}).get('technical_valid') is False or output.get('editorial',{}).get('status')=='REJECTED':raise ValueError('Output failed hard technical or duplicate checks; create a corrected version')
    if not output.get('approved') and not automatic:
        raise ValueError('Approve the output in Editor before publication')
    if output.get('approved') and output.get('render_id') and output.get('approved_render_id')!=output['render_id']:
        raise ValueError('Approval belongs to a different render version; review and approve this render')
    if automatic and not output.get('approved') and needs_editorial_review(job,index):
        raise ValueError('Weak or ungrounded selection requires editorial approval before automatic publication')
    status=publication_status(privacy,publish_at)
    draft=output.get('publishing_metadata')
    if draft:
        from .metadata import PublishingMetadata
        validated=PublishingMetadata.model_validate({k:v for k,v in draft.items() if k in PublishingMetadata.model_fields})
        if title is not None and title!=validated.title or description is not None and description!=validated.description:raise ValueError('Save and approve the metadata revision in Editor before queueing it')
        snippet={'title':validated.title,'description':credited_description(validated.description,job['request'].get('source_attribution','')),'tags':validated.tags,'categoryId':validated.category_id}
        if validated.metadata_language!='und':snippet['defaultLanguage']=validated.metadata_language
        if validated.audio_language and validated.audio_language!='und':snippet['defaultAudioLanguage']=validated.audio_language
    else:
        snippet={'title':valid_title(title or output.get('title','')),'description':credited_description(description or '',job['request'].get('source_attribution','')),'categoryId':'22'}
        language=job['result'].get('language')
        if language:snippet.update(defaultLanguage=language,defaultAudioLanguage=language)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT state,result FROM jobs WHERE id=?',(jid,)).fetchone()
        if not current or current['state']!='READY' or json.loads(current['result'])['outputs'][index]!=output:
            raise ValueError('Output or approval changed while queueing; refresh and review the current revision')
        package=db.execute('SELECT state FROM retention_packages WHERE job=?',(jid,)).fetchone()
        if package and package['state'] in ('DELETION_PENDING','DELETED'):
            raise ValueError('The Drive package is being removed or has expired')
        policy=db.execute('SELECT * FROM channels WHERE id=?',(channel,)).fetchone()
        if not policy:
            raise ValueError('Select an authenticated YouTube channel')
        key = managed.require_ready(channel, db)
        if job['request'].get('youtube_channel_key') not in (None, key):
            raise ValueError('The job is bound to a different YouTube profile.')
        if key and (privacy != 'private' or publish_at or automatic):
            raise ValueError('Managed YouTube channels accept manually approved private uploads only.')
        if (privacy=='public' or publish_at) and not policy['public_allowed']:
            raise ValueError('Public publishing has not been explicitly enabled for this channel')
        if automatic and not policy['automatic']:
            raise ValueError('Automatic mode has not been enabled for this channel')
        if automatic and not output.get('approved'):
            prefs=store.preferences(channel)
            if not prefs.get('auto_approve') or output.get('editorial',{}).get('review_required',True):raise ValueError('Explicit editorial approval or channel auto-approval safeguards are required')
        prior=db.execute('SELECT id,state FROM publications WHERE job=? AND output=? AND channel=?',(jid,index,channel)).fetchone()
        if publish_at:
            from .calendar import schedule
            cadence=db.execute('SELECT interval_minutes FROM channel_schedules WHERE channel=?',(channel,)).fetchone()
            schedule(db,channel,datetime.fromisoformat(status['publishAt']),policy['daily_limit'],cadence['interval_minutes'] if cadence else 120,store.preferences(channel).get('timezone','UTC'),exclude=prior['id'] if prior else None,manual=True)
        if prior and prior['state']!='DRAFT':return prior['id']
        if automatic and not publish_at:
            cadence=db.execute('SELECT interval_minutes FROM channel_schedules WHERE channel=?',(channel,)).fetchone()
            minutes=cadence['interval_minutes'] if cadence else 120
            from .calendar import schedule
            next_time=schedule(db,channel,datetime.now(timezone.utc)+timedelta(hours=2),policy['daily_limit'],minutes,store.preferences(channel).get('timezone','UTC'),exclude=prior['id'] if prior else None)
            status={'privacyStatus':'private','publishAt':next_time.isoformat(),'selfDeclaredMadeForKids':False}
        pid=uuid.uuid4().hex
        metadata={'snippet':snippet,'status':status,'drive':output['drive'],'automatic':automatic,'metadata_version':draft.get('version') if draft else None,'thumbnail':output.get('thumbnail'),'format':job['request'].get('mode','shorts'),'strict_metadata':bool(draft),'channel_key':key}
        if prior:
            db.execute("UPDATE publications SET state='QUEUED',metadata=?,error=NULL WHERE id=? AND state='DRAFT' AND session IS NULL AND video_id IS NULL",(json.dumps(metadata,ensure_ascii=False),prior['id']))
            return prior['id']
        db.execute('INSERT INTO publications(id,job,output,channel,state,metadata,created) VALUES(?,?,?,?,?,?,?)',(pid,jid,index,channel,'QUEUED',json.dumps(metadata,ensure_ascii=False),time.time()))
        return pid


def claim():
    now=time.time()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # A process can resume only the saved session, never insert a second video.
        db.execute("UPDATE publications SET state=CASE WHEN session IS NULL THEN 'FAILED' ELSE 'RETRYING' END,error='Publisher restarted; retry the saved session',seen=? WHERE state='PUBLISHING' AND seen<?",(now,now-180))
        for row in db.execute("SELECT p.*,c.alias,c.daily_limit FROM publications p JOIN channels c ON c.id=p.channel WHERE p.state='QUEUED' OR (p.state='RETRYING' AND (p.seen IS NULL OR p.seen<?)) ORDER BY p.created",(now-30,)).fetchall():
            try:
                managed.require_ready(row['channel'], db)
            except ValueError:
                continue
            from zoneinfo import ZoneInfo
            local=datetime.fromtimestamp(now,timezone.utc).astimezone(ZoneInfo(store.preferences(row['channel']).get('timezone','UTC')))
            day=local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
            used=db.execute("SELECT count(*) FROM publications WHERE channel=? AND state IN ('PUBLISHED','SCHEDULED','PUBLISHING') AND seen>=?",(row['channel'],day)).fetchone()[0]
            if used>=row['daily_limit']:
                continue
            db.execute("UPDATE publications SET state='PUBLISHING',seen=?,attempts=attempts+1,error=NULL WHERE id=?",(now,row['id']))
            return dict(row)


def checkpoint(pid,**fields):
    allowed={'state','session','video_id','current','total','error'}
    if not set(fields)<=allowed:
        raise ValueError('Invalid publication checkpoint')
    with store.connect() as db:
        pairs=','.join(k+'=?' for k in fields)
        db.execute('UPDATE publications SET '+pairs+',seen=? WHERE id=?',(*fields.values(),time.time(),pid))


def retry(pid):
    with store.connect() as db:
        # UNCERTAIN requires remote reconciliation, never an automatic new insert.
        return bool(db.execute("UPDATE publications SET state='RETRYING',seen=NULL WHERE id=? AND state IN ('FAILED','RETRYING')",(pid,)).rowcount)


def reconcile(pid):
    """Verify an existing video identity using GET only; never create an upload."""
    with store.connect() as db:
        row=db.execute('SELECT p.*,c.alias FROM publications p JOIN channels c ON c.id=p.channel WHERE p.id=?',(pid,)).fetchone()
        if not row or not row['video_id'] or row['state'] not in ('UNCERTAIN','FAILED'):
            raise ValueError('Reconciliation requires an existing video ID and an uncertain or failed result')
        publication=dict(row)
    body=json.loads(publication['metadata'])
    youtube=Drive(token_file(publication['alias']))
    try:
        complete(pid,{'id':publication['video_id']},body,youtube,publication['channel'])
        checkpoint(pid,error=None)
    except Uncertain as error:
        checkpoint(pid,state='UNCERTAIN',error=str(error))
        raise
    finally:
        youtube.close()


def validate_session(url):
    if not url.startswith('https://www.googleapis.com/'):
        raise ValueError('Invalid upload session destination')


class Uncertain(ValueError):
    pass


class PublicationError(ValueError):
    pass


def upload_error(response,operation):
    """Expose known API reasons, never raw Google payloads or session URLs."""
    reasons={
        'quotaExceeded':'YouTube API quota is exhausted; wait for its reset',
        'dailyLimitExceeded':'YouTube API daily limit is exhausted; wait for its reset',
        'uploadLimitExceeded':'This channel reached its upload limit; wait before retrying',
        'insufficientPermissions':'Reauthorize this channel with youtube.upload and youtube.readonly scopes',
        'forbidden':'The API project or channel does not permit this upload; check authorization',
        'invalidTitle':'Correct the title before retrying',
        'invalidDescription':'Correct the description before retrying; maximum 5000 UTF-8 bytes and no HTML angle brackets',
        'invalidPublishAt':'Correct the future publication time before retrying',
    }
    try:reason=response.json()['error']['errors'][0]['reason']
    except (ValueError,KeyError,IndexError,TypeError):reason=None
    advice=reasons.get(reason,'Check channel authorization, API project access and network connectivity')
    return PublicationError(f'{operation} failed (HTTP {response.status_code}). {advice}. Existing upload identity is preserved.')


def upload(publication):
    pid=publication['id']
    youtube=None
    drive=None
    key=None
    try:
        job=store.get(publication['job'])
        if not job or job['state']!='READY':raise ValueError('Publication source is no longer ready')
        current=job['result']['outputs'][publication['output']]
        queued=json.loads(publication['metadata'])
        if job['request'].get('publish_channel') not in (None, publication['channel']):
            raise PublicationError('Job target does not match the stored publication channel.')
        with store.connect() as db:
            key = managed.require_ready(publication['channel'], db)
            binding = db.execute('SELECT alias FROM channels WHERE id=?',(publication['channel'],)).fetchone()
        if job['request'].get('youtube_channel_key') not in (None, key):
            raise PublicationError('Job profile does not match the stored publication channel.')
        if key and (not binding or publication['alias'] != binding['alias'] or queued.get('channel_key') != key or
                    queued['status']['privacyStatus'] != 'private' or queued['status'].get('publishAt') or queued.get('automatic')):
            raise PublicationError('Managed publishing requires its original channel binding and private visibility.')
        if queued.get('automatic') and not config.AUTOMATIC_PUBLISHING:
            if publication.get('session'):
                raise Uncertain('Automatic publishing is disabled; reconcile the existing upload session manually')
            raise ValueError('Automatic publishing is disabled; nothing uploaded')
        if not current.get('approved'):
            if not queued.get('automatic') or needs_editorial_review(job,publication['output']) or not store.preferences(publication['channel']).get('auto_approve'):
                raise ValueError('Editorial approval was withdrawn; nothing uploaded')
        if current.get('render_id') and current.get('approved') and current.get('approved_render_id')!=current['render_id']:raise ValueError('Render approval changed; nothing uploaded')
        if queued.get('metadata_version') and current.get('publishing_metadata',{}).get('version')!=queued['metadata_version']:raise ValueError('Metadata version changed; nothing uploaded')
        youtube=Drive(token_file(publication['alias']))
        authorized=youtube.request('GET',YT+'/channels',params={'part':'id','mine':'true'}).json()['items']
        if key:
            try:
                managed.identity(authorized,key,publication['channel'])
            except ValueError as error:
                managed.record_status(key,'ERROR','OAuth identifies a different channel. Reconnect the intended identity.')
                raise PublicationError(str(error)) from None
        elif publication['channel'] not in [c['id'] for c in authorized]:
            raise ValueError('OAuth token is authorized for a different channel; nothing uploaded')
        drive=Drive()
        body=json.loads(publication['metadata'])
        remote=body.pop('drive')
        automatic=body.pop('automatic',False)
        internal={k:body.pop(k,None) for k in ('metadata_version','thumbnail','format','strict_metadata','channel_key')}
        with store.connect() as db:
            policy=db.execute('SELECT * FROM channels WHERE id=?',(publication['channel'],)).fetchone()
            if not policy or ((body['status']['privacyStatus']=='public' or body['status'].get('publishAt')) and not policy['public_allowed']) or (automatic and not policy['automatic']):
                raise ValueError('Publishing policy was revoked; nothing uploaded')
        scheduled=body['status'].get('publishAt')
        if scheduled and datetime.fromisoformat(scheduled.replace('Z','+00:00'))<=datetime.now(timezone.utc):
            if publication['session']:
                raise Uncertain('Schedule elapsed during an upload session; reconcile remotely before continuing')
            if not automatic:
                raise ValueError('Scheduled time has elapsed; edit the queue item before upload')
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                cadence=db.execute('SELECT interval_minutes FROM channel_schedules WHERE channel=?',(publication['channel'],)).fetchone()
                minutes=cadence['interval_minutes'] if cadence else 120
                from .calendar import schedule
                next_time=schedule(db,publication['channel'],datetime.now(timezone.utc)+timedelta(hours=2),policy['daily_limit'],minutes,store.preferences(publication['channel']).get('timezone','UTC'),exclude=pid)
                body['status']['publishAt']=next_time.isoformat()
                db.execute('UPDATE publications SET metadata=? WHERE id=?',(json.dumps({**body,**internal,'drive':remote,'automatic':automatic},ensure_ascii=False),pid))
        if internal.get('strict_metadata') and not publication['session'] and not publication.get('video_id'):
            categories=youtube.request('GET',YT+'/videoCategories',params={'part':'snippet','id':body['snippet']['categoryId']}).json().get('items',[])
            if not any(c.get('id')==body['snippet']['categoryId'] and c.get('snippet',{}).get('assignable') for c in categories):raise ValueError('YouTube category is not assignable; correct publishing metadata before retry')
        if publication.get('video_id'):
            # A returned video identity is authoritative even if a later GET failed.
            # Reconcile it directly; never depend on a possibly expired session.
            complete(pid,{'id':publication['video_id']},body,youtube,publication['channel'])
            return
        file_id=remote['id']
        verified=drive.request('GET',BASE+'/files/'+file_id,params={'fields':'id,size,md5Checksum','supportsAllDrives':'true'}).json()
        if verified.get('md5Checksum')!=remote.get('md5Checksum') or verified.get('size')!=remote.get('size'):
            raise ValueError('Drive artifact changed after verification; publication blocked')
        total=int(verified['size'])
        checkpoint(pid,total=total)
        session=publication['session']
        offset=0
        response=None
        if session:
            validate_session(session)
            response=youtube.client.put(session,headers={'Content-Length':'0','Content-Range':f'bytes */{total}'},content=b'')
            if response.status_code in (200,201):
                complete(pid,response.json(),body,youtube,publication['channel'])
                return
            if response.status_code in (404,410):
                raise Uncertain('Upload session expired; reconcile with YouTube Studio before any new upload')
            if response.status_code!=308:
                raise upload_error(response,'YouTube session query')
            offset=acknowledged(response)
        else:
            response=youtube.client.post('https://www.googleapis.com/upload/youtube/v3/videos',params={'uploadType':'resumable','part':'snippet,status'},json=body,headers={'X-Upload-Content-Length':str(total),'X-Upload-Content-Type':'video/mp4'})
            if response.status_code not in (200,201):
                raise upload_error(response,'YouTube session creation')
            session=response.headers['Location']
            validate_session(session)
            checkpoint(pid,session=session)
        while offset<total:
            end=min(offset+CHUNK,total)-1
            # Bound memory even if Drive ignores Range. Never materialize a full MP4.
            with drive.client.stream('GET',BASE+'/files/'+file_id,params={'alt':'media','supportsAllDrives':'true'},headers={'Range':f'bytes={offset}-{end}'}) as source:
                if source.status_code!=206:
                    raise ValueError('Drive did not honor a bounded Range request; upload stopped without a local copy')
                if source.headers.get('Content-Range')!=f'bytes {offset}-{end}/{total}':
                    raise ValueError('Drive returned an unexpected byte range')
                content=bytearray()
                for chunk in source.iter_bytes(256*1024):
                    if len(content)+len(chunk)>CHUNK:
                        raise ValueError('Drive response exceeded the bounded transfer size')
                    content.extend(chunk)
                if len(content)!=end-offset+1:
                    raise ValueError('Drive returned an incomplete chunk; retry the saved upload session')
            response=youtube.client.put(session,content=bytes(content),headers={'Content-Type':'video/mp4','Content-Length':str(len(content)),'Content-Range':f'bytes {offset}-{end}/{total}'})
            if response.status_code in (200,201):
                complete(pid,response.json(),body,youtube,publication['channel'])
                return
            if response.status_code!=308:
                raise upload_error(response,'YouTube chunk upload')
            next_offset=acknowledged(response)
            if next_offset<=offset or next_offset>total:
                raise ValueError('YouTube upload acknowledgment made no valid progress')
            offset=next_offset
            checkpoint(pid,current=offset)
        raise Uncertain('All bytes acknowledged without a video ID; reconcile the existing session')
    except Uncertain as error:
        checkpoint(pid,state='UNCERTAIN',error=str(error))
    except PublicationError as error:
        checkpoint(pid,state='FAILED',error=str(error))
    except OAuthRefreshError as error:
        if key and youtube is None:
            managed.record_status(key,error.status,'Verify or reconnect this YouTube authorization before retrying.')
        checkpoint(pid,state='FAILED',error=str(error))
    except (httpx.HTTPError,ValueError,KeyError,OSError):
        # A retry will query the persisted session first, including after final PUT timeout.
        checkpoint(pid,state='FAILED',error='Upload interrupted or authorization/quota validation failed. Retry resumes the saved session; check channel authorization and Drive access.')
    finally:
        if drive:
            drive.close()
        if youtube:
            youtube.close()


def complete(pid,response,metadata,youtube,channel):
    if not response.get('id'):
        raise Uncertain('YouTube returned no video ID; reconcile the existing session')
    # Persist identity before verification: a verification outage must never
    # erase knowledge of a remotely created video.
    checkpoint(pid,video_id=response['id'])
    remote=youtube.request('GET',YT+'/videos',params={'part':'snippet,status','id':response['id']}).json()['items']
    if (not remote or remote[0].get('id',response['id']) != response['id']
        or remote[0]['snippet']['channelId'] != channel
        or remote[0]['status']['privacyStatus'] != metadata['status']['privacyStatus']):
        raise Uncertain('Video ID exists but channel/privacy verification failed; inspect YouTube Studio')
    if remote[0]['status'].get('uploadStatus') not in ('uploaded','processed'):
        raise Uncertain('Video ID exists but YouTube did not confirm a successful upload status; inspect YouTube Studio')
    expected=metadata['status'].get('publishAt')
    if expected:
        actual=remote[0]['status'].get('publishAt')
        if not actual or datetime.fromisoformat(actual.replace('Z','+00:00'))!=datetime.fromisoformat(expected.replace('Z','+00:00')):
            raise Uncertain('Video ID exists but native schedule was not confirmed; inspect YouTube Studio')
    with store.connect() as db:
        saved=db.execute('SELECT total,metadata FROM publications WHERE id=?',(pid,)).fetchone()
        total=saved['total']
        strict=json.loads(saved['metadata']).get('strict_metadata')
    if strict:
        actual=remote[0]['snippet'];expected=metadata['snippet']
        for key in ('title','description','categoryId','defaultLanguage','defaultAudioLanguage'):
            if key in expected and actual.get(key)!=expected[key]:raise Uncertain('Video exists but '+key+' metadata verification failed; inspect YouTube Studio')
        if sorted(actual.get('tags',[]))!=sorted(expected.get('tags',[])):raise Uncertain('Video exists but tags verification failed; inspect YouTube Studio')
    checkpoint(pid,state='SCHEDULED' if metadata['status'].get('publishAt') else 'PUBLISHED',video_id=response['id'],current=total)


def loop(stop):
    last_schedule_check=0
    while not stop.is_set():
        if time.monotonic()-last_schedule_check>=60:
            refresh_scheduled()
            last_schedule_check=time.monotonic()
        from .youtube_thumbnails import pending
        pending()
        auto_queue()
        publication=claim()
        if publication:
            upload(publication)
        else:
            stop.wait(5)


def refresh_scheduled(now=None):
    """Observe native schedule completion with GET only; never reupload a video."""
    now=now or datetime.now(timezone.utc)
    with store.connect() as db:
        rows=[dict(r) for r in db.execute("SELECT p.*,c.alias FROM publications p JOIN channels c ON c.id=p.channel WHERE p.state IN ('SCHEDULED','UNCERTAIN') AND p.video_id IS NOT NULL AND json_extract(p.metadata,'$.status.publishAt') IS NOT NULL ORDER BY json_extract(p.metadata,'$.status.publishAt') LIMIT 50")]
    for item in rows:
        metadata=json.loads(item['metadata']);scheduled=metadata['status']['publishAt']
        if datetime.fromisoformat(scheduled.replace('Z','+00:00'))>now:continue
        youtube=None
        try:
            youtube=Drive(token_file(item['alias']))
            videos=youtube.request('GET',YT+'/videos',params={'part':'snippet,status','id':item['video_id']}).json().get('items',[])
            if not videos or videos[0]['snippet'].get('channelId')!=item['channel']:raise ValueError('Scheduled channel/video identity is not confirmed')
            status=videos[0]['status']
            if status.get('privacyStatus')=='public' and status.get('uploadStatus')=='processed':
                metadata['scheduled_for']=metadata['status'].pop('publishAt')
                metadata['status']['privacyStatus']='public'
                with store.connect() as db:
                    db.execute("UPDATE publications SET state='PUBLISHED',metadata=?,error=NULL,seen=? WHERE id=? AND state IN ('SCHEDULED','UNCERTAIN')",(json.dumps(metadata,ensure_ascii=False),time.time(),item['id']))
            elif now-datetime.fromisoformat(scheduled.replace('Z','+00:00'))>timedelta(minutes=10):
                checkpoint(item['id'],state='UNCERTAIN',error='Scheduled time passed but public/processed status was not confirmed. Check YouTube Studio, channel permissions and API project restrictions; no new upload was created.')
        except (ValueError,KeyError,OSError,httpx.HTTPError):
            checkpoint(item['id'],state='UNCERTAIN',error='Scheduled publication verification unavailable; existing video identity preserved for the next read-only check')
        finally:
            if youtube:youtube.close()


def auto_queue():
    """Reconcile READY automatic jobs after restarts; enqueue remains idempotent."""
    if not config.AUTOMATIC_PUBLISHING:return
    for job in store.list_jobs():
        if job['state']!='READY' or not job['request'].get('publish_automatic'):
            continue
        channel=job['request'].get('publish_channel')
        try:
            for index,output in enumerate(job['result'].get('outputs',[])):
                if not output.get('approved') and needs_editorial_review(job,index):continue
                enqueue(job['id'],index,channel,automatic=True)
        except ValueError:
            # A revoked policy does not change READY rendering state or publish.
            continue
