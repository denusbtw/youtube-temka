"""Bounded failure retention; only terminal, unleased job workspaces expire."""
import json
import os
import re
import shutil
import time
from . import config,store


def remove_workspace(target):
    if target.is_symlink() or target.resolve().parent != config.SCRATCH.resolve() or not re.fullmatch('[a-f0-9]{32}',target.name):
        raise ValueError('Refusing cleanup outside a verified job scratch directory')
    shutil.rmtree(target)


def sweep(now=None):
    now=now or time.time()
    retention=max(3600,float(os.environ.get('FAILED_RETENTION_HOURS','72'))*3600)
    with store.connect() as db:
        rows=db.execute("SELECT id,result FROM jobs WHERE lease IS NULL AND state IN ('FAILED','BLOCKED','CANCELLED') AND coalesce(seen,created)<?",(now-retention,)).fetchall()
        for row in rows:
            if not re.fullmatch('[a-f0-9]{32}',row['id']):
                continue
            target=config.SCRATCH/row['id']
            if target.is_dir() and not target.is_symlink():
                # Resolve and verify the boundary before recursive deletion.
                if target.resolve().parent != config.SCRATCH.resolve():
                    continue
                # Fresh files mean a process still touched recovery data.
                if any(p.stat().st_mtime>now-retention for p in target.rglob('*') if p.is_file()):
                    continue
                remove_workspace(target)
                result=json.loads(row['result'])
                result['local_expired']=True
                db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps(result),row['id']))
        referenced=set()
        for row in db.execute('SELECT request FROM jobs'):
            request=json.loads(row['request'])
            referenced.add(request['source'])
            referenced.update(item['source'] for item in request.get('sources',[]))
        for path in (config.SCRATCH/'uploads').glob('*'):
            if path.is_file() and not path.is_symlink() and 'upload:'+path.name not in referenced and path.stat().st_mtime<now-retention:
                path.unlink()
    from .retention import settings,cleanup
    if settings()['enabled']:cleanup(dry_run=False)
