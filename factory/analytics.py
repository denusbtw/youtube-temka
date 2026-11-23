"""Channel-bound, non-monetary YouTube analytics and recommendations."""
from collections import defaultdict
from datetime import date,datetime,timedelta,timezone
import json
import os
from pathlib import Path
import re
from statistics import median
import threading
import time
import httpx
from . import store
from .drive import Drive
from . import youtube_channels as managed

YT='https://www.googleapis.com/youtube/v3'
REPORTS='https://youtubeanalytics.googleapis.com/v2/reports'
READONLY='https://www.googleapis.com/auth/youtube.readonly'
SCOPES={READONLY,'https://www.googleapis.com/auth/yt-analytics.readonly'}
PUBLISHING_FALLBACK_PROFILES={'leonida_mayhem'}
METRICS=['views','estimatedMinutesWatched','averageViewDuration','averageViewPercentage','likes','comments','subscribersGained','subscribersLost']
TOTAL_METRICS=['views','estimatedMinutesWatched','likes','comments','subscribersGained','subscribersLost']
_lock=threading.Lock()


def token_file(alias):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',alias):raise ValueError('Invalid Analytics credential alias')
    root = managed.directory(analytics=True) if alias in managed.PROFILES else Path(os.environ.get('YOUTUBE_ANALYTICS_TOKEN_DIR','secrets/analytics'))
    path=root/(alias+'.json')
    if not path.is_file():raise ValueError('ANALYTICS_AUTH_REQUIRED: complete separate read-only consent')
    try:scopes=set(json.loads(path.read_text(encoding='utf-8-sig')).get('scopes',[]))
    except (OSError,ValueError):raise ValueError('Analytics credential is invalid; reconnect using a new alias') from None
    if not SCOPES<=scopes:raise ValueError('ANALYTICS_AUTH_REQUIRED: youtube.readonly and yt-analytics.readonly are required')
    return path


def connections():
    with store.connect() as db:return [dict(row) for row in db.execute('SELECT * FROM analytics_connections')]


def reporting_credential(alias, profile):
    """Prefer dedicated consent. A malformed/revoked dedicated grant fails closed."""
    dedicated = managed.directory(analytics=True)/(alias+'.json')
    if (alias not in PUBLISHING_FALLBACK_PROFILES or dedicated.exists() or dedicated.is_symlink()
        or not profile or profile['channel_key'] != alias):
        return token_file(alias), 'dedicated_analytics'
    if not profile['enabled'] or profile['status'] != 'CONNECTED' or not profile['channel_id']:
        raise ValueError('ANALYTICS_AUTH_REQUIRED: verify the managed publishing channel first')
    from .publisher import token_file as publishing_file
    path = publishing_file(alias)
    try:
        record = json.loads(path.read_text(encoding='utf-8-sig'))
        scopes = record.get('scopes')
        if record.get('youtube_channel_id') != profile['channel_id'] or not isinstance(scopes,list) or READONLY not in scopes:
            raise ValueError
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError('The publishing credential must match the verified channel and include youtube.readonly') from None
    return path, 'verified_publishing_readonly'


def discover(profile_key=None):
    errors=[]
    legacy = (p for p in Path(os.environ.get('YOUTUBE_ANALYTICS_TOKEN_DIR','secrets/analytics')).glob('*.json') if p.stem not in managed.PROFILES)
    profiles = {p['channel_key']:p for p in managed.profiles()}
    keys = profiles if profile_key is None else (profile_key,)
    files = [*(legacy if profile_key is None else ()),
             *(managed.directory(analytics=True)/(key+'.json') for key in keys)]
    for path in files:
        profile = profiles.get(path.stem)
        if profile and (not profile['enabled'] or (not path.is_file() and
            (path.stem not in PUBLISHING_FALLBACK_PROFILES or not profile['channel_id']))):
            continue
        if profile and not path.is_file() and any(
            row['channel']==profile['channel_id'] and row['alias']!=path.stem for row in connections()
        ):
            continue
        client=None
        try:
            credential,_=reporting_credential(path.stem,profile)
            client=Drive(credential)
            channels=client.request('GET',YT+'/channels',params={'part':'snippet','mine':'true'}).json().get('items',[])
            if profile:
                bound = json.loads(credential.read_text(encoding='utf-8-sig')).get('youtube_channel_id')
                if not profile['channel_id'] or bound != profile['channel_id']:
                    raise ValueError('Verify the corresponding publishing identity first.')
                channels = [managed.identity(channels,path.stem,bound)]
            for channel in channels:
                with store.connect() as db:
                    db.execute("""INSERT INTO analytics_connections(channel,alias,title) VALUES(?,?,?)
                        ON CONFLICT(channel) DO UPDATE SET alias=excluded.alias,title=excluded.title,error=NULL,
                            state=CASE WHEN analytics_connections.state='READY' AND analytics_connections.alias=excluded.alias
                                       THEN 'READY' ELSE 'CONNECTED' END
                        WHERE analytics_connections.alias=excluded.alias OR ?""",
                               (channel['id'],path.stem,channel['snippet']['title'],bool(profile)))
        except (ValueError,KeyError,OSError,httpx.HTTPError):errors.append({'alias':path.stem,'error':'Read-only Analytics authorization/channel lookup failed. Check consent and enabled APIs.'})
        finally:
            if client:client.close()
    return {'connections':connections(),'errors':errors}


def _request(client,url,params):
    if url == REPORTS:
        metrics = set(params.get('metrics','').split(','))
        if not metrics <= (set(METRICS) | {'engagedViews'}):
            raise ValueError('Unsupported Analytics metric. Only approved non-monetary metrics are available.')
    elif url not in {YT+'/channels',YT+'/playlistItems',YT+'/videos'}:
        raise ValueError('Unsupported Analytics operation.')
    response=client.client.get(url,params=params)
    if response.status_code>=400:
        try:reason=response.json()['error']['errors'][0]['reason']
        except (ValueError,KeyError,IndexError,TypeError):reason='unavailable'
        known={'insufficientPermissions','forbidden','accessNotConfigured','serviceDisabled','quotaExceeded','invalidParameter','badRequest','notFound','backendError'}
        raise ValueError(f'Analytics HTTP {response.status_code}: {reason if reason in known else "unavailable"}')
    return response.json()


def _rows(report):
    columns=[h['name'] for h in report.get('columnHeaders',[])]
    return [dict(zip(columns,row)) for row in report.get('rows',[])]


def metric_rows(client,params,diagnostics):
    try:return _rows(_request(client,REPORTS,params))
    except ValueError as error:
        if not str(error).startswith('Analytics HTTP 400:'):raise
    # Some channel/report combinations reject a metric in an otherwise valid
    # request. Retain supported fields and leave unsupported fields absent/null.
    dimension=params['dimensions'];merged={}
    for metric in params['metrics'].split(','):
        try:
            for row in _rows(_request(client,REPORTS,{**params,'metrics':metric})):
                merged.setdefault(row[dimension],{dimension:row[dimension]}).update(row)
        except ValueError as error:
            if not str(error).startswith('Analytics HTTP 400:'):raise
            diagnostics.append(metric+' unavailable for '+dimension+' report')
    return list(merged.values())


def duration_seconds(value):
    match=re.fullmatch(r'PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?',value or '')
    return sum(float(v or 0)*factor for v,factor in zip(match.groups(),(3600,60,1))) if match else None


def _checkpoint(channel,**fields):
    allowed={'state','error','progress','total','last_sync','last_attempt'}
    if not set(fields)<=allowed:raise ValueError('Invalid analytics checkpoint')
    with store.connect() as db:db.execute('UPDATE analytics_connections SET '+','.join(k+'=?' for k in fields)+' WHERE channel=?',(*fields.values(),channel))


def _lineage(channel,video):
    with store.connect() as db:
        row=db.execute('SELECT p.job,p.output,p.metadata,j.request,j.result FROM publications p JOIN jobs j ON j.id=p.job WHERE p.channel=? AND p.video_id=?',(channel,video)).fetchone()
        if not row:return None
        request=json.loads(row['request']);result=json.loads(row['result']);output=result.get('outputs',[])[row['output']]
        return {'job':row['job'],'output':row['output'],'source':request['source'],'language':output.get('publishing_metadata',{}).get('audio_language') or result.get('language'),'metadata_language':output.get('publishing_metadata',{}).get('metadata_language'),'content_category':request.get('source_category'),'preset':request.get('preset'),'audio_mode':request.get('audio_mode','original'),'duration':output.get('duration'),'metadata_version':json.loads(row['metadata']).get('metadata_version'),
                'edit_configuration':output.get('edit_configuration')}


def sync(channel):
    connection=next((c for c in connections() if c['channel']==channel),None)
    if not connection:raise ValueError('ANALYTICS_AUTH_REQUIRED: discover a separately authorized channel first')
    profile = next((p for p in managed.profiles() if p['channel_id'] == channel), None)
    if profile or connection['alias'] in managed.PROFILES:
        if not profile or not profile['enabled'] or (
            connection['alias'] in managed.PROFILES and connection['alias'] != profile['channel_key']
        ):
            raise ValueError('Enable the matching managed YouTube profile before syncing analytics.')
    _checkpoint(channel,state='SYNCING',last_attempt=time.time(),error=None,progress=0,total=None)
    client=None
    try:
        credential, strategy=reporting_credential(connection['alias'],profile)
        if connection['alias'] in managed.PROFILES and json.loads(credential.read_text(encoding='utf-8-sig')).get('youtube_channel_id') != channel:
            raise ValueError('Analytics credential binding does not match the selected channel')
        client=Drive(credential)
        channel_data=_request(client,YT+'/channels',{'part':'id,contentDetails','mine':'true'}).get('items',[])
        if profile:
            managed.identity(channel_data,profile['channel_key'],channel)
        identity=next((c for c in channel_data if c['id']==channel),None)
        if not identity:raise ValueError('Analytics token belongs to a different channel')
        ids=[]
        playlist=identity.get('contentDetails',{}).get('relatedPlaylists',{}).get('uploads')
        if playlist:
            items=_request(client,YT+'/playlistItems',{'part':'contentDetails','playlistId':playlist,'maxResults':50}).get('items',[])
            ids=[p['contentDetails']['videoId'] for p in items]
        with store.connect() as db:ids+= [r['video_id'] for r in db.execute('SELECT video_id FROM publications WHERE channel=? AND video_id IS NOT NULL ORDER BY created DESC LIMIT 200',(channel,))]
        ids=list(dict.fromkeys(ids))[:250]
        today=datetime.now(timezone.utc).date();end=today-timedelta(days=2);start=end-timedelta(days=29)
        common={'ids':'channel=='+channel,'startDate':start.isoformat(),'endDate':end.isoformat(),'metrics':','.join(METRICS)}
        diagnostics=[]
        daily=metric_rows(client,{**common,'dimensions':'day','sort':'day'},diagnostics)
        totals = None
        if strategy == 'verified_publishing_readonly':
            aggregate = _rows(_request(client, REPORTS, {**common,'metrics':','.join(TOTAL_METRICS)}))
            totals = aggregate[0] if len(aggregate)==1 else None
        videos=[];_checkpoint(channel,total=len(ids))
        for offset in range(0,len(ids),50):
            batch=ids[offset:offset+50]
            details=_request(client,YT+'/videos',{'part':'snippet,contentDetails,statistics,status','id':','.join(batch)}).get('items',[])
            metrics={r['video']:r for r in metric_rows(client,{**common,'dimensions':'video','filters':'video=='+','.join(batch),'maxResults':200},diagnostics)}
            # Engaged views are queried separately: unavailable metrics stay null.
            try:engaged={r['video']:r.get('engagedViews') for r in _rows(_request(client,REPORTS,{**common,'metrics':'engagedViews','dimensions':'video','filters':'video=='+','.join(batch),'maxResults':200}))}
            except ValueError:engaged={};diagnostics.append('engagedViews unavailable for this report/format/account')
            for item in details:
                snippet=item.get('snippet',{})
                if snippet.get('channelId')!=channel:continue
                lineage=_lineage(channel,item['id']);seconds=duration_seconds(item.get('contentDetails',{}).get('duration'))
                row={k:metrics.get(item['id'],{}).get(k) for k in METRICS}
                row['engagedViews']=engaged.get(item['id'])
                statistics=item.get('statistics',{})
                video={'video_id':item['id'],'channel':channel,'title':snippet.get('title',''),'published_at':snippet.get('publishedAt'),'duration':seconds,'language':(lineage or {}).get('language') or snippet.get('defaultAudioLanguage') or 'und','category_id':snippet.get('categoryId'),'video_type':'unknown','lineage':lineage,'metrics':row,'lifetime_statistics':{key:int(statistics[key]) if key in statistics else None for key in ('viewCount','likeCount','commentCount')},'period':{'start':start.isoformat(),'end':end.isoformat()},'impressions':None,'ctr':None,'retention_curve':None}
                # Duration alone cannot classify a platform Short. App mode is known.
                video['privacy_status'] = item.get('status', {}).get('privacyStatus')
                video['upload_status'] = item.get('status', {}).get('uploadStatus')
                if lineage:
                    with store.connect() as db:
                        request=json.loads(db.execute('SELECT request FROM jobs WHERE id=?',(lineage['job'],)).fetchone()['request'])
                    video['video_type']=request.get('mode','unknown')
                else:video['video_type']='unknown'
                videos.append(video)
            _checkpoint(channel,progress=min(offset+len(batch),len(ids)))
        report={'period':{'start':start.isoformat(),'end':end.isoformat()},'daily':daily,'diagnostics':sorted(set(diagnostics)),'unavailable_metrics':['impressions','ctr','retention_curve'],'recent_video_limit':250,'recommendations':recommendations(videos),'experiments':experiment_comparisons(videos)}
        if strategy == 'verified_publishing_readonly':
            report.update(credential_strategy=strategy,totals=totals)
        with store.connect() as db:
            db.execute('DELETE FROM analytics_videos WHERE channel=?',(channel,))
            db.executemany('INSERT INTO analytics_videos VALUES(?,?,?,?)',[(channel,v['video_id'],json.dumps(v,ensure_ascii=False),time.time()) for v in videos])
            db.execute('INSERT OR REPLACE INTO analytics_reports VALUES(?,?,?)',(channel,json.dumps(report,ensure_ascii=False),time.time()))
        _checkpoint(channel,state='READY',last_sync=time.time(),error=None)
        return snapshot(channel)
    except (ValueError,KeyError,OSError,httpx.HTTPError) as error:
        safe=str(error) if isinstance(error,ValueError) else 'Analytics sync unavailable; check API access, consent and connectivity'
        _checkpoint(channel,state='FAILED',error=safe[:300]);raise ValueError(safe) from None
    finally:
        if client:client.close()


def experiment_comparisons(videos):
    groups=defaultdict(list)
    for video in videos:
        edit=(video.get('lineage') or {}).get('edit_configuration') or {}
        period=video.get('period') or {}
        published=(video.get('published_at') or '')[:10]
        parent=edit.get('experiment_parent')
        variant=edit.get('experiment_variant')
        if not parent or not variant or not published or published >= (period.get('start') or ''):
            continue
        metrics=video.get('metrics') or {}
        if metrics.get('views') is None:
            continue
        key=(parent,video.get('channel'),period['start'],period['end'])
        groups[key].append({'variant':variant,'title':video['title'],'video_id':video['video_id'],
                            'duration':video['duration'],'metrics':metrics})
    return [{'parent':parent,'start':start,'end':end,'variants':items}
            for (parent,_,start,end),items in groups.items()
            if len({item['variant'] for item in items})>=2]


def recommendations(videos):
    eligible=[v for v in videos if (v.get('metrics',{}).get('views') or 0)>=30 and v.get('metrics',{}).get('averageViewPercentage') is not None]
    if len(eligible)<20:return {'status':'INSUFFICIENT_DATA','eligible_videos':len(eligible),'minimum_videos':20,'items':[],'notice':'No confident scheduling/content recommendation from a small sample. Policies are not changed.'}
    result=[]
    for dimension in ('language','video_type','duration_bucket','content_category','preset','publishing_period'):
        groups=defaultdict(list)
        for video in eligible:
            lineage=video.get('lineage') or {}
            if dimension=='duration_bucket':key='<=30s' if (video.get('duration') or 0)<=30 else '31-60s' if video['duration']<=60 else '>60s'
            elif dimension=='publishing_period':
                try:key=str(datetime.fromisoformat(video['published_at'].replace('Z','+00:00')).hour//6*6)+'–'+str(datetime.fromisoformat(video['published_at'].replace('Z','+00:00')).hour//6*6+5)+' UTC'
                except (TypeError,ValueError,KeyError):continue
            else:key=video.get(dimension) or lineage.get(dimension)
            if key:groups[key].append(video['metrics']['averageViewPercentage'])
        supported={k:v for k,v in groups.items() if len(v)>=5}
        if len(supported)>=2:
            best=max(supported,key=lambda k:median(supported[k]));result.append({'dimension':dimension,'observed_group':best,'videos':len(supported[best]),'median_average_percentage_viewed':round(median(supported[best]),2),'reason':'Observed association in the available sample, not a causal result or guaranteed reach. Review before changing policy.'})
    return {'status':'OBSERVATIONAL','eligible_videos':len(eligible),'items':result,'policies_changed':False}


def snapshot(channel=None):
    result=[]
    with store.connect() as db:
        for connection in db.execute('SELECT * FROM analytics_connections'):
            if channel and connection['channel']!=channel:continue
            saved=db.execute('SELECT report,seen FROM analytics_reports WHERE channel=?',(connection['channel'],)).fetchone()
            videos=[json.loads(r['data']) for r in db.execute('SELECT data FROM analytics_videos WHERE channel=? ORDER BY seen DESC LIMIT 250',(connection['channel'],))]
            result.append({'connection':dict(connection),'report':json.loads(saved['report']) if saved else None,'videos':videos,'data_age_seconds':time.time()-saved['seen'] if saved else None})
    return {'state':'ANALYTICS_AUTH_REQUIRED' if not result else 'CONNECTED','channels':result}


def start_sync(channel):
    if not any(c['channel']==channel for c in connections()):raise ValueError('ANALYTICS_AUTH_REQUIRED')
    if not _lock.acquire(False):return {'started':False,'state':'SYNC_IN_PROGRESS'}
    def work():
        try:sync(channel)
        except ValueError:pass
        finally:_lock.release()
    threading.Thread(target=work,daemon=True).start()
    return {'started':True,'state':'SYNCING'}


def loop(stop):
    while not stop.wait(60):
        for connection in connections():
            if not connection.get('last_attempt') or time.time()-connection['last_attempt']>6*3600:start_sync(connection['channel'])
