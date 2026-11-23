"""Bounded, repeatable thumbnail updates on an already identified video."""
import hashlib
import json
import time
from . import store
from .drive import Drive,BASE


def record(pid,state,error=None):
    with store.connect() as db:db.execute('INSERT OR REPLACE INTO publication_artifacts VALUES(?,?,?,?)',(pid,state,error,time.time()))


def apply(publication):
    from .publisher import token_file,YT
    from .youtube_channels import require_ready
    metadata=json.loads(publication['metadata'])
    remote=(metadata.get('thumbnail') or {}).get('drive')
    if not remote or not publication.get('video_id'):return
    size=int(remote.get('size',0))
    if not 0<size<=4*1024**2:
        record(publication['id'],'ASSET_ONLY','Thumbnail exceeds the bounded 4 MiB transfer limit');return
    youtube=drive=None
    try:
        with store.connect() as db:
            require_ready(publication['channel'],db)
        youtube=Drive(token_file(publication['alias']))
        video=youtube.request('GET',YT+'/videos',params={'part':'snippet,processingDetails','id':publication['video_id']}).json().get('items',[])
        if not video or video[0]['snippet']['channelId']!=publication['channel']:raise ValueError('Thumbnail channel verification failed')
        # Shorts thumbnails can be set only after processing succeeds.
        if video[0].get('processingDetails',{}).get('processingStatus')!='succeeded':
            record(publication['id'],'WAITING_PROCESSING');return
        drive=Drive();content=bytearray()
        with drive.client.stream('GET',BASE+'/files/'+remote['id'],params={'alt':'media','supportsAllDrives':'true'}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(64*1024):
                if len(content)+len(chunk)>size:raise ValueError('Thumbnail response exceeded verified size')
                content.extend(chunk)
        if len(content)!=size or hashlib.md5(content).hexdigest()!=remote.get('md5Checksum'):raise ValueError('Thumbnail checksum verification failed')
        response=youtube.client.post('https://www.googleapis.com/upload/youtube/v3/thumbnails/set',params={'videoId':publication['video_id'],'uploadType':'media'},content=bytes(content),headers={'Content-Type':'image/jpeg'})
        if response.status_code in (400,403):
            record(publication['id'],'ASSET_ONLY','YouTube rejected custom thumbnail for this account/video. The verified JPEG remains on Drive for Studio.');return
        response.raise_for_status()
        if not response.json().get('items'):raise ValueError('YouTube returned no thumbnail confirmation')
        record(publication['id'],'APPLIED')
    except Exception:
        record(publication['id'],'RETRYING','Thumbnail update unavailable; video identity and Drive JPEG preserved')
    finally:
        if drive:drive.close()
        if youtube:youtube.close()


def pending():
    with store.connect() as db:
        rows=db.execute("SELECT p.*,c.alias FROM publications p JOIN channels c ON c.id=p.channel LEFT JOIN publication_artifacts a ON a.publication=p.id WHERE p.state IN ('PUBLISHED','SCHEDULED') AND p.video_id IS NOT NULL AND (a.state IS NULL OR (a.state IN ('WAITING_PROCESSING','RETRYING') AND a.seen<?)) ORDER BY p.created DESC LIMIT 20",(time.time()-300,)).fetchall()
    for row in rows:
        if (json.loads(row['metadata']).get('thumbnail') or {}).get('drive'):apply(dict(row))
