"""Wikimedia Commons discovery and operator-reviewed CC0 video import."""
import html
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
from . import config, media
from .resources import preflight

API = 'https://commons.wikimedia.org/w/api.php'
CC0 = 'https://creativecommons.org/publicdomain/zero/1.0/'
MAX_BYTES = 100 * 1024**2
HEADERS = {'User-Agent': 'AIContentFactory/7.1 (local operator video library; https://github.com/Eclipse-XS/youtube-temka)'}


def verified_url(url, host):
    value = urlsplit(url)
    return value.scheme == 'https' and value.netloc == host


def metadata(page):
    info = next(iter(page.get('imageinfo', [])), {})
    fields = info.get('extmetadata', {})
    license_url = fields.get('LicenseUrl', {}).get('value', '')
    source = info.get('descriptionurl', '')
    url = info.get('url', '')
    eligible = (fields.get('LicenseShortName', {}).get('value') == 'CC0'
                and bool(re.fullmatch(r'https?://creativecommons\.org/publicdomain/zero/1\.0(?:/deed\.[\w-]+)?/?',license_url))
                and fields.get('AttributionRequired', {}).get('value') == 'false'
                and info.get('mime', '').startswith('video/')
                and verified_url(url, 'upload.wikimedia.org')
                and verified_url(source, 'commons.wikimedia.org')
                and 0 < info.get('size', 0) <= MAX_BYTES)
    return {'id': page['pageid'], 'title': page['title'], 'source': source, 'url': url,
            'author': html.unescape(re.sub(r'<[^>]+>', '', fields.get('Artist', {}).get('value', '')))[:500],
            'license': fields.get('LicenseShortName', {}).get('value', 'Unknown'),
            'license_url': license_url, 'license_status': 'eligible_pending_review' if eligible else 'manual_review',
            'eligible': eligible}


def _query(client, **params):
    try:
        response = client.get(API, params={'action': 'query', 'format': 'json', 'formatversion': 2,
            'prop': 'imageinfo', 'iiprop': 'url|mime|size|extmetadata', **params})
        response.raise_for_status()
        return [metadata(page) for page in response.json().get('query', {}).get('pages', []) if 'pageid' in page]
    except (httpx.HTTPError, ValueError) as error:
        raise ValueError('Wikimedia Commons metadata is unavailable') from error


def search(query):
    query = query.strip()
    if not 2 <= len(query) <= 100:raise ValueError('Enter 2–100 search characters')
    with httpx.Client(headers=HEADERS,timeout=20,trust_env=False) as client:
        return _query(client,generator='search',gsrnamespace=6,gsrlimit=12,gsrsearch=query+' filetype:video')


def import_video(pageid, category):
    import av
    import uuid
    from .library import register

    config.initialize()
    folder=config.DATA/'backgrounds'
    folder.mkdir(exist_ok=True)
    with httpx.Client(headers=HEADERS,timeout=30,trust_env=False) as client:
        rows=_query(client,pageids=pageid)
        if not rows or not rows[0]['eligible']:
            raise ValueError('Asset is ineligible for automatic import; review actual media and licensing')
        item=rows[0]
        with TemporaryDirectory(dir=folder) as work:
            downloaded=Path(work)/'download'
            try:
                with client.stream('GET',item['url']) as response:
                    response.raise_for_status()
                    size=0
                    with downloaded.open('wb') as stream:
                        for chunk in response.iter_bytes(256*1024):
                            size+=len(chunk)
                            if size>MAX_BYTES:raise ValueError('Provider video exceeds 100 MiB')
                            preflight(len(chunk));stream.write(chunk)
            except httpx.HTTPError as error:
                raise ValueError('Wikimedia Commons video could not be downloaded') from error
            with av.open(str(downloaded)) as container:
                duration=(container.duration or 0)/av.time_base
                if not 1 <= duration <= 300 or not container.streams.video:
                    raise ValueError('Secondary video must be 1–300 seconds')
            temporary=Path(work)/'normalized.mp4'
            media.run([media.ffmpeg(),'-hide_banner','-loglevel','error','-y','-threads','2','-i',str(downloaded),
                       '-map','0:v:0','-vf',"scale='min(720,iw)':-2,fps=24",'-an','-map_metadata','-1',
                       '-c:v','libx264','-pix_fmt','yuv420p','-threads','2','-movflags','+faststart',str(temporary)],timeout=240)
            media.validate_output(temporary,duration,expected_audio=False)
            final=folder/(uuid.uuid4().hex+'.mp4')
            temporary.replace(final)
            try:
                name,duplicate=register(final,'backgrounds','CC0-1.0',item['source'],category,{
                    'author':item['author'],'license_url':CC0,'license_status':'provider_verified_operator_reviewed',
                    'attribution_required':False,'provider':'Wikimedia Commons','provider_id':pageid,
                })
            except Exception:
                final.unlink(missing_ok=True)
                raise
    return {'name':name,'duplicate':duplicate}
