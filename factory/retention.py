"""Package-level opt-in Drive retention; no folders or untracked files deleted."""
import json
import time
from . import store
from .drive import Drive,BASE
from .publisher import YT,token_file
from .heavy import HostSlot

_slot=HostSlot()
DEFAULTS={'enabled':False,'maximum_packages':50,'grace_days':7}


def settings():
    with store.connect() as db:
        row=db.execute("SELECT value FROM factory_settings WHERE key='retention'").fetchone()
    return {**DEFAULTS,**(json.loads(row['value']) if row else {})}


def configure(value):
    result={**DEFAULTS,**value}
    if not isinstance(result['enabled'],bool) or not 1<=result['maximum_packages']<=10000 or not 1<=result['grace_days']<=365:
        raise ValueError('Use a package limit of 1-10000 and a grace period of 1-365 days')
    with store.connect() as db:db.execute("INSERT OR REPLACE INTO factory_settings VALUES('retention',?)",(json.dumps(result),))
    return result


def register(job):
    files=[]
    def add(value):
        if isinstance(value,dict):
            if all(k in value for k in ('id','md5Checksum','size')):files.append({k:value[k] for k in ('id','md5Checksum','size')})
            else:
                for item in value.values():add(item)
        elif isinstance(value,list):
            for item in value:add(item)
    add(job['result'].get('outputs',[]));add(job['result'].get('artifacts',{}))
    files=list({f['id']:f for f in files}.values())
    if files and job['result'].get('cloud_verified'):
        with store.connect() as db:
            db.execute('INSERT OR IGNORE INTO retention_packages(job,files,created) VALUES(?,?,?)',(job['id'],json.dumps(files),time.time()))


def protect(jid,value):
    with store.connect() as db:
        if not db.execute("UPDATE retention_packages SET protected=? WHERE job=? AND state='ACTIVE'",(int(value),jid)).rowcount:
            raise ValueError('Protection can only be changed for an active package')


def candidates(now=None):
    now=time.time() if now is None else now
    policy=settings();rows=[]
    with store.connect() as db:
        active=[json.loads(r['request']).get('source','') for r in db.execute("SELECT request FROM jobs WHERE state<>'READY'")]
        for row in db.execute("SELECT * FROM retention_packages WHERE state!='DELETED' ORDER BY created,job"):
            value=dict(row);value.pop('files')
            pubs=db.execute('SELECT * FROM publications WHERE job=?',(row['job'],)).fetchall()
            job=db.execute('SELECT state,result FROM jobs WHERE id=?',(row['job'],)).fetchone()
            output_count=len(json.loads(job['result']).get('outputs',[])) if job else 0
            completed={p['output'] for p in pubs if p['state']=='PUBLISHED' and p['video_id'] and json.loads(p['metadata'])['status'].get('privacyStatus')=='public' and not json.loads(p['metadata'])['status'].get('publishAt') and now-(p['seen'] or now)>=policy['grace_days']*86400}
            reasons=[]
            if row['protected']:reasons.append('protected')
            if not job or job['state']!='READY':reasons.append('job_not_ready')
            if any(s.startswith('edit:'+row['job']+':') for s in active):reasons.append('active_reference')
            if any(p['state']!='PUBLISHED' for p in pubs):reasons.append('publication_pending_or_uncertain')
            if not output_count or completed!=set(range(output_count)):reasons.append('public_verification_or_grace_required')
            value.update(eligible=not reasons,reasons=reasons);rows.append(value)
    return rows


def snapshot():
    with store.connect() as db:history=[dict(r) for r in db.execute('SELECT * FROM retention_history ORDER BY at DESC LIMIT 100')]
    return {'settings':settings(),'packages':candidates(),'history':history}


def cleanup(dry_run=True):
    # Serialize with heavy work too: no cleanup can race a cooperating local
    # renderer acquiring a preserved edit source. Remote references are rechecked.
    with _slot:
        policy=settings();items=candidates();eligible=[i for i in items if i['eligible']]
        limit=policy['maximum_packages']
        pending=[i for i in eligible if i['state']=='DELETION_PENDING']
        active=[i for i in eligible if i['state']!='DELETION_PENDING']
        chosen=pending+active[:max(0,len(items)-len(pending)-limit)]
        remaining=len(items)-len(chosen) if dry_run else len(items)
        report={'dry_run':dry_run,'selected':[i['job'] for i in chosen] if dry_run else [],'deleted':[],'errors':[],
                'remaining':remaining,'over_limit':max(0,remaining-limit)}
        if dry_run:return report
        if not policy['enabled']:raise ValueError('Enable Drive retention explicitly before applying cleanup')
        for item in pending+active:
            if item['state']!='DELETION_PENDING' and len(items)-len(report['deleted'])<=limit:break
            jid=item['job']
            if not next((i['eligible'] for i in candidates() if i['job']==jid),False):continue
            report['selected'].append(jid)
            try:
                with store.connect() as db:
                    pubs=[dict(r) for r in db.execute('SELECT p.*,c.alias FROM publications p JOIN channels c ON c.id=p.channel WHERE p.job=?',(jid,))]
                    package=db.execute('SELECT files FROM retention_packages WHERE job=?',(jid,)).fetchone()
                for pub in pubs:
                    youtube=Drive(token_file(pub['alias']))
                    try:
                        identity=youtube.request('GET',YT+'/channels',params={'mine':'true','part':'id'}).json().get('items',[])
                        videos=youtube.request('GET',YT+'/videos',params={'id':pub['video_id'],'part':'snippet,status'}).json().get('items',[])
                        if len(identity)!=1 or identity[0]['id']!=pub['channel'] or not videos or videos[0]['snippet']['channelId']!=pub['channel'] or videos[0]['status'].get('privacyStatus')!='public' or videos[0]['status'].get('uploadStatus')!='processed':raise ValueError('External publication or channel cannot be confirmed')
                    finally:youtube.close()
                if not pubs:raise ValueError('Verified publication missing')
                drive=Drive()
                try:
                    files=json.loads(package['files'])
                    # Verify the complete package before touching its first file.
                    for file in files:
                        metadata=drive.request('GET',BASE+'/files/'+file['id'],params={'fields':'id,appProperties,size,md5Checksum,trashed','supportsAllDrives':'true'}).json()
                        if not metadata.get('appProperties',{}).get('factoryKey','').startswith(jid+'-') or metadata.get('md5Checksum')!=file['md5Checksum'] or str(metadata.get('size'))!=str(file['size']):raise ValueError('Drive package ownership/integrity not confirmed')
                    with store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        protected=db.execute('SELECT protected FROM retention_packages WHERE job=?',(jid,)).fetchone()['protected']
                        active=db.execute("SELECT request FROM jobs WHERE state<>'READY'").fetchall()
                        if protected or any(json.loads(r['request']).get('source','').startswith('edit:'+jid+':') for r in active):raise ValueError('Package acquired a protected/active reference')
                        current_pubs=[dict(r) for r in db.execute('SELECT p.*,c.alias FROM publications p JOIN channels c ON c.id=p.channel WHERE p.job=?',(jid,))]
                        if {p['id']:p for p in current_pubs}!={p['id']:p for p in pubs}:
                            raise ValueError('Publication changed during cleanup verification')
                        db.execute("UPDATE retention_packages SET state='DELETION_PENDING',error=NULL WHERE job=?",(jid,))
                    for file in files:
                        drive.request('PATCH',BASE+'/files/'+file['id'],params={'supportsAllDrives':'true'},json={'trashed':True})
                        check=drive.request('GET',BASE+'/files/'+file['id'],params={'fields':'trashed','supportsAllDrives':'true'}).json()
                        if check.get('trashed') is not True:raise ValueError('Drive removal was not confirmed')
                finally:drive.close()
                with store.connect() as db:
                    db.execute("UPDATE retention_packages SET state='DELETED',error=NULL WHERE job=?",(jid,))
                    db.execute('INSERT INTO retention_history VALUES(?,?,?,?)',(jid,time.time(),'DELETED','Factory-owned package moved to Drive trash; removal verified'))
                report['deleted'].append(jid)
            except Exception:
                detail='Cleanup not completed; check channel verification, Drive ownership/permissions and active references, then retry'
                with store.connect() as db:
                    db.execute('UPDATE retention_packages SET error=? WHERE job=?',(detail,jid))
                    db.execute('INSERT INTO retention_history VALUES(?,?,?,?)',(jid,time.time(),'FAILED',detail))
                report['errors'].append({'job':jid,'error':detail})
        report['remaining']=len(items)-len(report['deleted'])
        report['over_limit']=max(0,report['remaining']-limit)
        return report
