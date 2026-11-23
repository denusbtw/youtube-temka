"""Persistent, hashed, licensed assets and six original procedural starter loops."""
import hashlib
import json
from pathlib import Path
from . import config,store,media
from .heavy import HEAVY_LOCK


def register(path,kind,license,source,category='custom',provenance=None):
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    import av
    with av.open(str(path)) as container:
        if (kind!='logos' and not container.duration) or (kind in ('backgrounds','logos') and not container.streams.video):raise ValueError('Asset has no valid playable video')
        video=container.streams.video[0] if container.streams.video else None
        if video and (video.width>4096 or video.height>4096) and kind=='logos':raise ValueError('Logo exceeds 4096 pixels')
        metadata={'id':digest,'name':path.name,'category':category,'duration':(container.duration or 0)/av.time_base,
                  'width':video.width if video else None,'height':video.height if video else None,
                  'fps':float(video.average_rate or 0) if video else None,'size_bytes':path.stat().st_size,
                  'source':source,'license':license,'attribution_required':None if category=='custom' else False,
                  'availability':'AVAILABLE','sha256':digest}
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT name FROM media_assets WHERE hash=? AND kind=?',(digest,kind)).fetchone()
        if prior and (config.DATA/kind/prior['name']).is_file():
            if path.name!=prior['name']:path.unlink()
            return prior['name'],True
        metadata.update(provenance or {})
        db.execute('INSERT OR REPLACE INTO media_assets VALUES(?,?,?,?)',(digest,kind,path.name,json.dumps(metadata)))
    return path.name,False


def display_name(item):
    if item.get('display_name'):return item['display_name']
    if item.get('provider')=='Wikimedia Commons':
        from urllib.parse import unquote,urlsplit
        title=unquote(urlsplit(item['source']).path.rsplit('/',1)[-1]).removeprefix('File:')
        return title.rsplit('.',1)[0].replace('_',' ')
    if item['name'].startswith('starter-'):
        return 'Demo · '+item['name'][8:-4].replace('-',' ').title()
    return 'Operator asset · '+(item.get('category') or 'custom').replace('-',' ')


def entries():
    from collections import Counter
    with store.connect() as db:
        usage=Counter((kind,row[field]) for row in db.execute(
            "SELECT json_extract(request,'$.background') background,json_extract(request,'$.music_asset') music,"
            "json_extract(request,'$.logo_asset') logo FROM jobs")
            for kind,field in (('backgrounds','background'),('music','music'),('logos','logo')) if row[field])
        result=[]
        for row in db.execute('SELECT * FROM media_assets'):
            item=json.loads(row['metadata'])
            key=(row['kind'],row['name'])
            result.append({**item,'kind':row['kind'],'display_name':display_name(item),
                           'job_references':usage[key],
                           'availability':'AVAILABLE' if (config.DATA/row['kind']/row['name']).is_file() else 'MISSING'})
        return result


def recommend(category=None):
    from collections import Counter
    import random

    assets=[item for item in entries() if item['kind']=='backgrounds' and
            item['availability']=='AVAILABLE' and not item.get('removed') and
            (not category or item['category']==category) and
            (item.get('license')=='CC0-1.0' or item.get('license_status')=='operator_documented')]
    if not assets:return None
    with store.connect() as db:
        recent=Counter(row[0] for row in db.execute(
            "SELECT json_extract(request,'$.background') FROM jobs ORDER BY created DESC LIMIT 15"))
    def rank(item):
        return (item['name'].startswith('starter-'),recent[item['name']],not item.get('favorite'),
                -min((item['width'] or 0)/720,(item['height'] or 0)/1280))
    best=min(rank(item) for item in assets)
    return random.choice([item['name'] for item in assets if rank(item)==best])


def preferences(name, favorite=None, removed=None, label=None):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT metadata FROM media_assets WHERE kind='backgrounds' AND name=?",(name,)).fetchone()
        if not row:raise ValueError('Secondary asset is unavailable')
        item=json.loads(row['metadata'])
        if label is not None:item['display_name']=label.strip() or None
        if favorite is not None:item['favorite']=favorite
        if removed is not None:item['removed']=removed
        db.execute("UPDATE media_assets SET metadata=? WHERE kind='backgrounds' AND name=?",(json.dumps(item),name))
    return item


def bootstrap():
    config.initialize()
    folder=config.DATA/'backgrounds';folder.mkdir(parents=True,exist_ok=True)
    # Expressions use frame time T; all motion is generated locally from math.
    patterns={
        'bouncing-ball':"if(lt((X-W/2-80*sin(T*PI/3))^2+(Y-H/2-160*cos(T*PI/3))^2,24^2),230,18)",
        'geometric-shapes':"if(lt(abs(X-W/2-70*cos(T*PI/3)),35)*lt(abs(Y-H/2-100*sin(T*PI/3)),35),220,30)",
        'obstacle-rings':"if(lt(mod(sqrt((X-W/2)^2+(Y-H/2)^2)+40*T,80),12),200,20)",
        'scrolling-pattern':"if(lt(mod(X+Y+40*T,80),40),180,35)",
        'particle-wave':"if(lt(abs(Y-H/2-130*sin(X/35+T*PI/3)),8),235,15)",
        'satisfying-loop':"128+100*sin(X/45+2*sin(T*PI/3))*cos(Y/60+2*cos(T*PI/3))",
    }
    with HEAVY_LOCK:
        for i,(name,expression) in enumerate(patterns.items()):
            path=folder/f'starter-{name}.mp4'
            if not path.is_file():
                temporary=path.with_suffix('.part.mp4')
                try:
                    graph=f"nullsrc=s=270x480:r=24,geq=lum='{expression}':cb='{90+i*15}':cr='{180-i*12}'"
                    media.run([media.ffmpeg(),'-hide_banner','-loglevel','error','-y','-f','lavfi','-i',graph,
                               '-t','6','-an','-c:v','libx264','-preset','fast','-crf','23','-threads','2',str(temporary)])
                    media.validate_output(temporary,6,expected_audio=False)
                    temporary.replace(path)
                finally:temporary.unlink(missing_ok=True)
            register(path,'backgrounds','CC0-1.0','Original procedural animation generated by AI Content Factory',name)
    return entries()


if __name__=='__main__':
    print(json.dumps({'starter_assets':len(bootstrap()),'resolution':'270x480','fps':24}))
