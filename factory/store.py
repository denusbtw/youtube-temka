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
        yield db


def decode(row,db=None):
    if row is None:
        return None
    item = dict(row)
    for k in ('request', 'result'):
        item[k] = json.loads(item[k])
    item.pop('lease', None)
    if db is not None:
        item['stages']=[dict(r) for r in db.execute('SELECT key,status,started,finished,current,total,unit,message FROM job_stages WHERE job=? ORDER BY started',(item['id'],))]
    return item


def create(request):
    jid = uuid.uuid4().hex
    result = {}
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


def claim(worker, remote=False):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # Uncertain external effects must be reviewed, never replayed blindly.
        expired=db.execute("UPDATE jobs SET state='BLOCKED',error='Worker lease expired; inspect outputs before retry',lease=NULL WHERE lease IS NOT NULL AND seen < ? RETURNING id", (time.time()-120,)).fetchall()
        for item in expired:
            db.execute('INSERT INTO events VALUES(?,?,?,?)',(item['id'],time.time(),'BLOCKED','Worker lease expired; inspect outputs before retry'))
            db.execute("UPDATE job_stages SET status='failed',finished=?,message='Worker lease expired; checkpoints retained' WHERE job=? AND status='running'",(time.time(),item['id']))
        if db.execute('SELECT 1 FROM jobs WHERE worker=? AND lease IS NOT NULL',(worker,)).fetchone():
            return None
        rows = db.execute("SELECT id,request FROM jobs WHERE state='QUEUED' AND cancelled=0 ORDER BY created")
        row = next((r for r in rows if not remote or (not json.loads(r['request'])['source'].startswith('upload:') and json.loads(r['request'])['save_drive'])),None)
        if not row:
            return None
        lease = uuid.uuid4().hex
        db.execute("UPDATE jobs SET state='PROBING',worker=?,lease=?,seen=? WHERE id=?", (worker,lease,time.time(),row['id']))
        db.execute('INSERT INTO events VALUES(?,?,?,?)',(row['id'],time.time(),'PROBING','Lease assigned to '+worker))
        return {'job': decode(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()), 'lease': lease}


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
        if state is None:
            if current is None:
                current=row['current']
            if total is None:
                total=row['total']
        terminal = state in ('READY','FAILED','BLOCKED','CANCELLED')
        db.execute('UPDATE jobs SET state=?,result=?,error=?,current=?,total=?,seen=?,lease=? WHERE id=?',
                   (state or row['state'], json.dumps(value, ensure_ascii=False), error,current,total,time.time(),None if terminal else lease,jid))
        db.execute('UPDATE workers SET seen=? WHERE id=?',(time.time(),row['worker']))
        if state and (state!=row['state'] or error):
            db.execute('INSERT INTO events VALUES(?,?,?,?)',(jid,time.time(),state,error))
        if stage_update:
            s=stage_update
            status=s.get('status','running')
            if status not in ('running','completed','skipped','failed','pending'):
                raise ValueError('Invalid stage status')
            db.execute('''INSERT INTO job_stages VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job,key) DO UPDATE SET status=excluded.status,
                finished=excluded.finished,current=excluded.current,total=excluded.total,
                unit=excluded.unit,message=excluded.message''',
                (jid,s['key'],status,time.time(),time.time() if status in ('completed','skipped','failed') else None,s.get('current'),s.get('total'),s.get('unit'),s.get('message')))


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
        cur = db.execute("UPDATE jobs SET state='QUEUED',error=NULL,cancelled=0,lease=NULL WHERE id=? AND state IN ('FAILED','BLOCKED','CANCELLED')", (jid,))
        if cur.rowcount:
            db.execute('INSERT INTO events VALUES(?,?,?,?)',(jid,time.time(),'QUEUED','Manual retry; checkpoints retained'))
        return cur.rowcount == 1


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
        return db.execute("SELECT 1 FROM jobs WHERE id<>? AND json_extract(request,'$.source')=? AND state<>'READY' LIMIT 1",(exclude,source)).fetchone() is not None
