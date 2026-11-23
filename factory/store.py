"""Single SQLite queue with atomic claims, fencing and persisted results."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from . import config


@contextmanager
def connect():
    config.initialize()
    with sqlite3.connect(config.DB, timeout=15) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, state TEXT NOT NULL, request TEXT NOT NULL,
            result TEXT NOT NULL DEFAULT '{}', error TEXT,
            worker TEXT, lease TEXT, seen REAL, created REAL NOT NULL,
            stage TEXT, current REAL, total REAL, cancelled INTEGER DEFAULT 0)''')
        db.execute('''CREATE TABLE IF NOT EXISTS workers (
            id TEXT PRIMARY KEY, seen REAL NOT NULL, resources TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY, alias TEXT NOT NULL, title TEXT NOT NULL,
            daily_limit INTEGER NOT NULL DEFAULT 3, public_allowed INTEGER NOT NULL DEFAULT 0,
            automatic INTEGER NOT NULL DEFAULT 0)''')
        db.execute('''CREATE TABLE IF NOT EXISTS youtube_profiles (
            channel_key TEXT PRIMARY KEY, channel_id TEXT UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'NOT_CONNECTED',
            verified_at REAL, error TEXT)''')
        if db.execute('SELECT count(*) FROM youtube_profiles').fetchone()[0] < 2:
            db.executemany('INSERT OR IGNORE INTO youtube_profiles(channel_key) VALUES(?)',
                           [('leonida_mayhem',), ('thevicescope',)])
            db.commit()
        db.execute('''CREATE TABLE IF NOT EXISTS publications (
            id TEXT PRIMARY KEY, job TEXT NOT NULL, output INTEGER NOT NULL, channel TEXT NOT NULL,
            state TEXT NOT NULL, metadata TEXT NOT NULL, session TEXT, video_id TEXT,
            current INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
            error TEXT, created REAL NOT NULL, seen REAL, attempts INTEGER NOT NULL DEFAULT 0,
            UNIQUE(job,output,channel))''')
        db.execute('''CREATE TABLE IF NOT EXISTS channel_schedules (
            channel TEXT PRIMARY KEY, interval_minutes INTEGER NOT NULL DEFAULT 120)''')
        db.execute('''CREATE TABLE IF NOT EXISTS events (
            job TEXT NOT NULL, at REAL NOT NULL, state TEXT NOT NULL, message TEXT)''')
        db.execute('''CREATE TABLE IF NOT EXISTS job_stages (
            job TEXT NOT NULL, key TEXT NOT NULL, status TEXT NOT NULL,
            started REAL, finished REAL, current REAL, total REAL, unit TEXT,
            message TEXT, PRIMARY KEY(job,key))''')
        db.execute('''CREATE TABLE IF NOT EXISTS metadata_versions (
            job TEXT NOT NULL, output INTEGER NOT NULL, version TEXT NOT NULL,
            metadata TEXT NOT NULL, provenance TEXT NOT NULL, created REAL NOT NULL,
            manual INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(job,output,version))''')
        db.execute('''CREATE TABLE IF NOT EXISTS channel_preferences (
            channel TEXT PRIMARY KEY, preferences TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS analytics_connections (
            channel TEXT PRIMARY KEY, alias TEXT NOT NULL, title TEXT NOT NULL,
            last_sync REAL, last_attempt REAL, state TEXT NOT NULL DEFAULT 'CONNECTED',
            error TEXT, progress INTEGER NOT NULL DEFAULT 0, total INTEGER)''')
        db.execute('''CREATE TABLE IF NOT EXISTS analytics_videos (
            channel TEXT NOT NULL, video TEXT NOT NULL, data TEXT NOT NULL,
            seen REAL NOT NULL, PRIMARY KEY(channel,video))''')
        db.execute('''CREATE TABLE IF NOT EXISTS analytics_reports (
            channel TEXT PRIMARY KEY, report TEXT NOT NULL, seen REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS publication_artifacts (
            publication TEXT PRIMARY KEY, state TEXT NOT NULL, error TEXT, seen REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS media_assets (
            hash TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, metadata TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS retention_packages (
            job TEXT PRIMARY KEY, files TEXT NOT NULL, created REAL NOT NULL,
            protected INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'ACTIVE', error TEXT)''')
        db.execute('''CREATE TABLE IF NOT EXISTS retention_history (
            job TEXT NOT NULL, at REAL NOT NULL, state TEXT NOT NULL, detail TEXT)''')
        db.execute('''CREATE TABLE IF NOT EXISTS factory_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS render_lineages (
            id TEXT PRIMARY KEY, last_version INTEGER NOT NULL)''')
        db.execute('CREATE TABLE IF NOT EXISTS job_visibility (job TEXT PRIMARY KEY, status TEXT NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS editorial_feedback (
            id TEXT PRIMARY KEY, job TEXT NOT NULL, candidate_index INTEGER,
            decision TEXT NOT NULL, rejection_reasons TEXT NOT NULL DEFAULT '[]',
            original_start REAL, original_end REAL, corrected_start REAL, corrected_end REAL,
            original_score REAL, selected_layout TEXT, edited_hook TEXT,
            notes TEXT NOT NULL DEFAULT '', created REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS source_rights (
            job TEXT PRIMARY KEY, source_url TEXT, original_creator TEXT,
            permission_status TEXT NOT NULL DEFAULT 'operator_confirmed',
            license TEXT NOT NULL DEFAULT '', attribution_required INTEGER NOT NULL DEFAULT 0,
            commercial_restrictions TEXT NOT NULL DEFAULT '',
            reused_content_acknowledged INTEGER NOT NULL DEFAULT 0, recorded_at REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS production_metrics (
            job TEXT PRIMARY KEY, analysis_seconds REAL, render_seconds REAL,
            candidate_count INTEGER, approved_count INTEGER, model_calls INTEGER,
            vlm_calls INTEGER, peak_vram_mib INTEGER, updated REAL NOT NULL)''')
        yield db


def decode(row,db=None):
    if row is None:
        return None
    item = dict(row)
    for k in ('request', 'result'):
        item[k] = json.loads(item[k])
    item.pop('lease', None)
    visibility=db.execute('SELECT status FROM job_visibility WHERE job=?',(item['id'],)).fetchone() if db else None
    item['visibility']=visibility['status'] if visibility else 'recent'
    if db is not None:
        item['stages']=[dict(r) for r in db.execute('SELECT key,status,started,finished,current,total,unit,message FROM job_stages WHERE job=? ORDER BY started',(item['id'],))]
    return item


def create(request, seed=None, approved=None):
    jid = uuid.uuid4().hex
    result = {'approved_selection':approved} if approved else {}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if request.get('source','').startswith('edit:'):
            _, origin, index_text = request['source'].split(':')
            index = int(index_text)
            package=db.execute('SELECT state FROM retention_packages WHERE job=?',(origin,)).fetchone()
            if package and package['state'] in ('DELETION_PENDING','DELETED'):raise ValueError('Source package is being removed or has expired; restore it before editing')
            parent = db.execute("SELECT result FROM jobs WHERE id=? AND state='READY'", (origin,)).fetchone()
            outputs = json.loads(parent['result']).get('outputs', []) if parent else []
            if not 0 <= index < len(outputs):
                raise ValueError('Editable source output is unavailable')
            parent_output = outputs[index]
            parent_id = parent_output.get('render_id') or f'{origin}:{index+1}'
            lineage = parent_output.get('render_lineage_id') or parent_id
            version = db.execute('''INSERT INTO render_lineages(id,last_version) VALUES(?,?)
                ON CONFLICT(id) DO UPDATE SET last_version=MAX(render_lineages.last_version+1,excluded.last_version)
                RETURNING last_version''', (lineage, (parent_output.get('render_version') or 1)+1)).fetchone()[0]
            result['edit_revision'] = {'render_lineage_id': lineage, 'parent_render_id': parent_id, 'render_version': version}
        if seed:
            from shutil import copyfile
            result['local_checkpoint_seed']=True
            root = config.SCRATCH / jid
            root.mkdir()
            copyfile(seed,root/'transcript.json')
            visual=seed.parent/'visual-v2.json'
            if visual.is_file():copyfile(visual,root/visual.name)
        db.execute('INSERT INTO jobs(id,state,request,result,created) VALUES(?,?,?,?,?)',
                   (jid, 'QUEUED', json.dumps(request, ensure_ascii=False), json.dumps(result), time.time()))
        db.execute('INSERT INTO events VALUES(?,?,?,?)',(jid,time.time(),'QUEUED','Job queued'))
    return get(jid)


def get(jid):
    with connect() as db:
        return decode(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone(),db)


def list_jobs(limit=200,offset=0):
    with connect() as db:
        return [decode(r,db) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT ? OFFSET ?',(min(200,max(1,limit)),max(0,offset))).fetchall()]


def can_claim_remote(payload):
    request = json.loads(payload)
    urls = [item['source'] for item in request.get('sources', [])] or [request['source']]
    return request['save_drive'] and all(not url.startswith('upload:') for url in urls)


def claim(worker, remote=False):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        expired = db.execute("SELECT id,state,result FROM jobs WHERE lease IS NOT NULL AND seen < ?", (now-120,))
        for item in expired:
            result = json.loads(item['result'])
            # Drive may have accepted a write before the worker lost its lease.
            retryable = item['state'] != 'UPLOADING' or bool(result.get('cloud_verified'))
            message = ('Worker lease expired; checkpoints retained' if retryable else
                       'Worker lease expired during upload; inspect remote outputs before retry')
            failure = {'code':'WORKER_LEASE_EXPIRED','stage':result.get('active_stage') or item['state'],
                       'message':message,'retryable':retryable}
            state = schedule_retry(result, failure, now) if retryable else 'BLOCKED'
            result['failure'] = failure
            db.execute('UPDATE jobs SET state=?,error=?,result=?,lease=NULL,seen=? WHERE id=?',
                       (state,message,json.dumps(result,ensure_ascii=False),now,item['id']))
            db.execute('INSERT INTO events VALUES(?,?,?,?)',(item['id'],now,state,message))
            db.execute("UPDATE job_stages SET status='failed',finished=?,message=? WHERE job=? AND status='running'",(now,message,item['id']))
        if db.execute('SELECT 1 FROM jobs WHERE worker=? AND lease IS NOT NULL',(worker,)).fetchone():
            return None
        rows = db.execute("""SELECT id,request,result FROM jobs WHERE state='QUEUED' AND cancelled=0
                          AND coalesce(json_extract(result,'$.retry_at'),0)<=? ORDER BY created""", (now,))
        row = next((r for r in rows if not remote or (can_claim_remote(r['request']) and not json.loads(r['request']).get('plan_only') and not json.loads(r['result']).get('local_checkpoint_seed'))),None)
        if not row:
            return None
        lease = uuid.uuid4().hex
        db.execute("UPDATE jobs SET state='PROBING',error=NULL,worker=?,lease=?,seen=?,result=json_remove(result,'$.retry_at') WHERE id=?", (worker,lease,now,row['id']))
        db.execute('INSERT INTO events VALUES(?,?,?,?)',(row['id'],time.time(),'PROBING','Lease assigned to '+worker))
        return {'job': decode(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()), 'lease': lease}


def schedule_retry(result, failure, now):
    used = result.get('automatic_retries', 0)
    failure.update(attempt=used+1, max_attempts=config.AUTOMATIC_RETRIES+1)
    if used >= config.AUTOMATIC_RETRIES:
        return 'FAILED'
    result['automatic_retries'] = used+1
    result['retry_at'] = now + (30 if failure['code']=='DOWNLOAD_RATE_LIMITED' else 5) * 2**used
    return 'QUEUED'


def update(jid, lease, state=None, result=None, error=None, current=None, total=None,stage_update=None):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=? AND lease=?', (jid,lease)).fetchone()
        if not row or row['cancelled']:
            raise InterruptedError('Job cancelled or worker lease lost')
        value = json.loads(row['result'])
        if result:
            result=json.loads(json.dumps(result,ensure_ascii=False))
            for index,output in enumerate(result.get('outputs',[])):
                metadata=output.get('publishing_metadata')
                if metadata:
                    provenance=metadata.pop('_provenance',{})
                    db.execute('INSERT OR IGNORE INTO metadata_versions VALUES(?,?,?,?,?,?,?)',(jid,index,metadata['version'],json.dumps(metadata,ensure_ascii=False),json.dumps(provenance,ensure_ascii=False),time.time(),0))
            value.update(result)
        if state=='QUEUED':
            failure=value.get('failure')
            if row['state']=='UPLOADING' or not failure or not failure.get('retryable'):
                raise ValueError('Automatic retry requires a retryable processing failure')
            state=schedule_retry(value,failure,time.time())
        if state=='READY':
            value.pop('failure',None)
        if state is None:
            if current is None:
                current=row['current']
            if total is None:
                total=row['total']
        terminal = state in ('READY','FAILED','BLOCKED','CANCELLED','QUEUED','REVIEW')
        db.execute('UPDATE jobs SET state=?,result=?,error=?,current=?,total=?,seen=?,lease=? WHERE id=?',
                   (state or row['state'], json.dumps(value, ensure_ascii=False), error,current,total,time.time(),None if terminal else lease,jid))
        db.execute('UPDATE workers SET seen=? WHERE id=?',(time.time(),row['worker']))
        if state and (state!=row['state'] or error):
            db.execute('INSERT INTO events VALUES(?,?,?,?)',(jid,time.time(),state,error))
        if stage_update:
            s=stage_update
            status=s.get('status','running')
            if status not in ('running','completed','skipped','failed','blocked','pending'):
                raise ValueError('Invalid stage status')
            db.execute('''INSERT INTO job_stages VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job,key) DO UPDATE SET status=excluded.status,
                finished=excluded.finished,current=excluded.current,total=excluded.total,
                unit=excluded.unit,message=excluded.message''',
                (jid,s['key'],status,time.time(),time.time() if status in ('completed','skipped','failed','blocked') else None,s.get('current'),s.get('total'),s.get('unit'),s.get('message')))


def heartbeat(worker, resources):
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO workers VALUES(?,?,?)', (worker,time.time(),json.dumps(resources)))


def workers():
    with connect() as db:
        return [{**dict(r),'resources':json.loads(r['resources'])} for r in db.execute('SELECT * FROM workers')]


def events(jid):
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT at,state,message FROM events WHERE job=? ORDER BY at DESC LIMIT 100',(jid,))]


def cancel(jid):
    with connect() as db:
        cur=db.execute("UPDATE jobs SET cancelled=1,state='CANCELLED',lease=NULL WHERE id=? AND state NOT IN ('READY','PUBLISHED')", (jid,))
        if cur.rowcount:
            db.execute('INSERT INTO events VALUES(?,?,?,?)',(jid,time.time(),'CANCELLED','Cancelled by operator'))


def retry(jid):
    with connect() as db:
        # A retry resumes files/checkpoints; verified Drive outputs are retained.
        cur = db.execute("""UPDATE jobs SET state='QUEUED',error=NULL,cancelled=0,lease=NULL,
                         result=json_remove(result,'$.automatic_retries','$.retry_at','$.failure')
                         WHERE id=? AND state IN ('FAILED','BLOCKED','CANCELLED')""", (jid,))
        if cur.rowcount:
            db.execute('INSERT INTO events VALUES(?,?,?,?)',(jid,time.time(),'QUEUED','Manual retry; checkpoints retained'))
        return cur.rowcount == 1


def edit_plan(jid, revision, edits, target, approve=False):
    from .models import JobRequest

    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT state,request,result FROM jobs WHERE id=?',(jid,)).fetchone()
        if row is None:
            raise KeyError(jid)
        result = json.loads(row['result'])
        plan = result.get('edit_plan')
        if row['state']!='REVIEW' or not plan or plan['revision']!=revision:
            raise InterruptedError('The edit plan changed or was submitted. Reload and try again.')
        request = JobRequest.model_validate_json(row['request'])
        original = {segment['id']:segment for segment in plan['segments']}
        if len({edit['id'] for edit in edits})!=len(edits):
            raise ValueError('Choose each segment at most once')
        selected = []
        for edit in edits:
            segment = original.get(edit['id'])
            if segment is None:
                raise ValueError('Unknown segment in edit plan')
            start,end = edit['start_seconds'],edit['end_seconds']
            source = plan['sources'][segment['source_id']]
            if not request.minimum<=end-start<=request.maximum or end+segment['source_offset']>source['end']+1e-8:
                raise ValueError(f'Segment {edit["id"]}: choose a range within the source and the clip duration limits')
            selected.append({**segment,**edit})
            if (start,end)!=(segment['start_seconds'],segment['end_seconds']):
                selected[-1].update(selection_method='manual_boundaries',reason='Boundaries edited by the operator')
                selected[-1].pop('score',None)
        if target is not None and request.workflow!='compilation':
            raise ValueError('A timeline target is available for compilations')
        plan.update(revision=revision+1,segments=selected,approved=approve)
        if target is not None:
            plan['target_duration']=target
        state = 'QUEUED' if approve else 'REVIEW'
        db.execute('UPDATE jobs SET state=?,result=?,seen=? WHERE id=?',
                   (state,json.dumps(result,ensure_ascii=False),time.time(),jid))
        if approve:
            db.execute('INSERT INTO events VALUES(?,?,?,?)',(jid,time.time(),state,'Edit plan approved for rendering'))
        return plan


def approve(jid,index,value):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT state,result FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row or row['state']!='READY':
            raise ValueError('Only a completed output can be approved')
        result=json.loads(row['result'])
        editorial=result['outputs'][index].get('editorial',{})
        if value and editorial.get('technical_valid') is False:raise ValueError('Hard technical validations failed; re-render before approval')
        if value and editorial.get('status')=='REJECTED':raise ValueError('Rejected or duplicate output cannot be approved; create a corrected version')
        result['outputs'][index]['approved']=value
        result['outputs'][index]['approved_render_id']=result['outputs'][index].get('render_id') if value else None
        if editorial:
            editorial['human_reviewed']=bool(value)
            editorial['status']='READY_FOR_PUBLISHING' if value and result.get('cloud_verified') else 'APPROVED' if value else 'NEEDS_REVIEW' if editorial.get('issues') else 'READY_FOR_REVIEW'
        db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps(result,ensure_ascii=False),jid))
        if not value:
            db.execute("UPDATE publications SET state='DRAFT',error='Approval withdrawn; review and queue again' WHERE job=? AND output=? AND state IN ('QUEUED','FAILED','RETRYING') AND session IS NULL AND video_id IS NULL",(jid,index))


def metadata_history(jid,index):
    with connect() as db:
        return [{'version':r['version'],'metadata':json.loads(r['metadata']),'provenance':json.loads(r['provenance']),'created':r['created'],'manual':bool(r['manual'])} for r in db.execute('SELECT * FROM metadata_versions WHERE job=? AND output=? ORDER BY created DESC',(jid,index))]


def revise_metadata(jid,index,metadata,manual=True):
    """A revision invalidates old approval; queued remote effects are immutable."""
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT state,result FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row or row['state']!='READY':raise ValueError('Only completed outputs can be edited')
        if db.execute("SELECT 1 FROM publications WHERE job=? AND output=? AND (session IS NOT NULL OR video_id IS NOT NULL OR state='PUBLISHING')",(jid,index)).fetchone():raise ValueError('Publication already has external effects; its metadata cannot be changed')
        result=json.loads(row['result']);output=result['outputs'][index]
        metadata=json.loads(json.dumps(metadata,ensure_ascii=False));provenance=metadata.pop('_provenance',{})
        db.execute('INSERT INTO metadata_versions VALUES(?,?,?,?,?,?,?)',(jid,index,metadata['version'],json.dumps(metadata,ensure_ascii=False),json.dumps(provenance,ensure_ascii=False),time.time(),int(manual)))
        output['publishing_metadata']=metadata;output['title']=metadata['title'];output['approved']=False
        output['editorial']={**output.get('editorial',{}),'status':'NEEDS_REVIEW','review_required':True,'human_reviewed':False}
        db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps(result,ensure_ascii=False),jid))
        # Do not silently change an already reviewed publication. Stop its queue
        # entry until the operator approves/requeues the revised draft.
        db.execute("UPDATE publications SET state='DRAFT',error='Output metadata changed; approve the revision and queue again' WHERE job=? AND output=? AND session IS NULL AND video_id IS NULL",(jid,index))


def preferences(channel):
    with connect() as db:
        row=db.execute('SELECT preferences FROM channel_preferences WHERE channel=?',(channel,)).fetchone()
        return json.loads(row['preferences']) if row else {}


def source_in_use(source,exclude):
    # Never use the paginated UI list to decide whether an original may go away.
    with connect() as db:
        return db.execute("""SELECT 1 FROM jobs WHERE id<>? AND state<>'READY' AND
                            (json_extract(request,'$.source')=? OR EXISTS (
                              SELECT 1 FROM json_each(jobs.request,'$.sources')
                              WHERE json_extract(value,'$.source')=?)) LIMIT 1""",
                          (exclude,source,source)).fetchone() is not None


def record_feedback(job_id, payload):
    fid = uuid.uuid4().hex
    now = time.time()
    with connect() as db:
        db.execute(
            '''INSERT INTO editorial_feedback(
                id, job, candidate_index, decision, rejection_reasons,
                original_start, original_end, corrected_start, corrected_end,
                original_score, selected_layout, edited_hook, notes, created
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                fid,
                job_id,
                payload.get('candidate_index'),
                payload['decision'],
                json.dumps(payload.get('rejection_reasons', []), ensure_ascii=False),
                payload.get('original_start'),
                payload.get('original_end'),
                payload.get('corrected_start'),
                payload.get('corrected_end'),
                payload.get('original_score'),
                payload.get('selected_layout'),
                payload.get('edited_hook'),
                payload.get('notes', '')[:500],
                now,
            ),
        )
    return {'id': fid, 'job': job_id, 'created': now, **payload}


def list_feedback(job_id=None, limit=200):
    with connect() as db:
        if job_id:
            rows = db.execute(
                'SELECT * FROM editorial_feedback WHERE job=? ORDER BY created DESC LIMIT ?',
                (job_id, min(500, max(1, limit))),
            ).fetchall()
        else:
            rows = db.execute(
                'SELECT * FROM editorial_feedback ORDER BY created DESC LIMIT ?',
                (min(500, max(1, limit)),),
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item['rejection_reasons'] = json.loads(item['rejection_reasons'] or '[]')
            out.append(item)
        return out


def feedback_summary():
    from collections import Counter
    items = list_feedback(limit=500)
    decisions = Counter(i['decision'] for i in items)
    reasons = Counter(r for i in items for r in i.get('rejection_reasons', []))
    total = len(items)
    approved = decisions.get('approved', 0)
    return {
        'total_decisions': total,
        'approved_count': approved,
        'approval_rate': round(approved / total, 4) if total else None,
        'decisions': dict(decisions),
        'rejection_reasons': dict(reasons),
        'automatic_training_enabled': False,
        'note': 'Feedback is persisted for editorial audit and offline calibration; weights are not silently mutated online.',
    }


def save_source_rights(job_id, rights):
    with connect() as db:
        db.execute(
            '''INSERT OR REPLACE INTO source_rights(
                job, source_url, original_creator, permission_status, license,
                attribution_required, commercial_restrictions, reused_content_acknowledged, recorded_at
            ) VALUES (?,?,?,?,?,?,?,?,?)''',
            (
                job_id,
                rights.get('source_url'),
                rights.get('original_creator', ''),
                rights.get('permission_status', 'operator_confirmed'),
                rights.get('license', ''),
                int(bool(rights.get('attribution_required', False))),
                rights.get('commercial_restrictions', ''),
                int(bool(rights.get('reused_content_acknowledged', False))),
                time.time(),
            ),
        )


def get_source_rights(job_id):
    with connect() as db:
        row = db.execute('SELECT * FROM source_rights WHERE job=?', (job_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item['attribution_required'] = bool(item['attribution_required'])
        item['reused_content_acknowledged'] = bool(item['reused_content_acknowledged'])
        return item


def record_production_metrics(job_id, metrics):
    with connect() as db:
        db.execute(
            '''INSERT OR REPLACE INTO production_metrics(
                job, analysis_seconds, render_seconds, candidate_count,
                approved_count, model_calls, vlm_calls, peak_vram_mib, updated
            ) VALUES (?,?,?,?,?,?,?,?,?)''',
            (
                job_id,
                metrics.get('analysis_seconds'),
                metrics.get('render_seconds'),
                metrics.get('candidate_count'),
                metrics.get('approved_count'),
                metrics.get('model_calls'),
                metrics.get('vlm_calls'),
                metrics.get('peak_vram_mib'),
                time.time(),
            ),
        )


def production_summary():
    with connect() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM production_metrics ORDER BY updated DESC LIMIT 200').fetchall()]
    fb = feedback_summary()
    if not rows:
        return {'tracked_jobs': 0, 'feedback': fb, 'note': 'No tracked production jobs yet.'}
    n = len(rows)
    return {
        'tracked_jobs': n,
        'avg_analysis_seconds': round(sum(r['analysis_seconds'] or 0 for r in rows) / n, 2),
        'avg_render_seconds': round(sum(r['render_seconds'] or 0 for r in rows) / n, 2),
        'total_candidates': sum(r['candidate_count'] or 0 for r in rows),
        'total_model_calls': sum(r['model_calls'] or 0 for r in rows),
        'total_vlm_calls': sum(r['vlm_calls'] or 0 for r in rows),
        'max_peak_vram_mib': max((r['peak_vram_mib'] or 0 for r in rows), default=0),
        'feedback': fb,
    }


def editorial_workflow_state(job):
    """Explicit editorial workflow state distinct from technical job READY."""
    state = job.get('state', '')
    if state in ('QUEUED', 'PROBING', 'DOWNLOADING', 'TRANSCRIBING', 'ANALYZING'):
        return 'ANALYSIS_IN_PROGRESS'
    if state in ('RENDERING', 'UPLOADING'):
        return 'RENDERING_IN_PROGRESS'
    if state in ('FAILED', 'BLOCKED', 'CANCELLED'):
        return 'RENDER_VALIDATION_FAILED' if state == 'FAILED' else state
    result = job.get('result') or {}
    if result.get('planning_only'):
        return 'CANDIDATES_READY'
    outputs = result.get('outputs') or []
    if not outputs:
        return 'CANDIDATES_READY' if result.get('candidates') else 'NEEDS_EDITORIAL_REVIEW'
    if any(o.get('editorial', {}).get('technical_valid') is False for o in outputs):
        return 'RENDER_VALIDATION_FAILED'
    if all(o.get('approved') for o in outputs):
        return 'READY_FOR_PUBLISHING_REVIEW' if result.get('cloud_verified') else 'APPROVED'
    return 'NEEDS_EDITORIAL_REVIEW'
