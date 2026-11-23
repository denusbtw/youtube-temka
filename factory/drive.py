"""Read-only OAuth credential reuse; resumable verified Drive storage.

Accepts the standard Google authorized-user JSON format. Original token files
are never overwritten. Upload sessions stay in private job scratch files.
"""
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import time
import re
import httpx

CHUNK=8*1024*1024
BASE='https://www.googleapis.com/drive/v3'

class OAuthRefreshError(ValueError):
    def __init__(self, status: str):
        super().__init__('OAuth access could not be refreshed. Check authorization and connectivity.')
        self.status = status


def acknowledged(response):
    value=response.headers.get('Range')
    if not value:
        return 0
    match=re.fullmatch(r'bytes=0-(\d+)',value)
    if not match:
        raise ValueError('Invalid upload acknowledgment; local data preserved')
    return int(match.group(1))+1


class Drive:
    def __init__(self,token_file=None):
        self.tokens=None
        legacy_client=os.environ.get('DRIVE_OAUTH_CLIENT_FILE','')
        legacy_token=os.environ.get('DRIVE_OAUTH_TOKEN_FILE','')
        if token_file is None and legacy_client and legacy_token:
            from .legacy_drive_oauth import OAuthClient,AccessTokens,SCOPE,DriveError
            try:
                oauth=OAuthClient.read(Path(legacy_client))
            except DriveError:
                raise ValueError('Existing Drive desktop client is unavailable or invalid; check the read-only mount') from None
            refresh=json.loads(Path(legacy_token).read_text(encoding='utf-8-sig'))
            if refresh.get('client_id')!=oauth.client_id or refresh.get('scope')!=SCOPE or not isinstance(refresh.get('refresh_token'),str) or not refresh['refresh_token']:
                raise ValueError('Existing Drive token/client pair or scope does not match; original files were preserved')
            self.client=httpx.Client(timeout=90,trust_env=False)
            self.tokens=AccessTokens(self.client,oauth,refresh['refresh_token'])
            try:
                self.client.headers['Authorization']='Bearer '+self.tokens.get()
            except Exception:
                self.client.close()
                raise ValueError('Existing Drive refresh authorization could not be verified; check connectivity and consent status') from None
            return
        path=token_file or os.environ.get('DRIVE_TOKEN_FILE','')
        if not path or not Path(path).is_file():
            raise ValueError('Drive OAuth missing. Set DRIVE_TOKEN_FILE to the existing authorized-user JSON; mount it read-only. Existing credentials have not been changed.')
        self.secret=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        self.client=httpx.Client(timeout=90,trust_env=False)
        try:
            try:
                response=self.client.post('https://oauth2.googleapis.com/token',data={'client_id':self.secret['client_id'],'client_secret':self.secret['client_secret'],'refresh_token':self.secret['refresh_token'],'grant_type':'refresh_token'})
            except httpx.RequestError:
                raise OAuthRefreshError('TOKEN_EXPIRED') from None
            if response.status_code != 200:
                try:
                    error = response.json().get('error')
                except (ValueError, AttributeError):
                    error = None
                status = ('REAUTHORIZATION_REQUIRED' if error in ('invalid_grant','invalid_client')
                          else 'TOKEN_EXPIRED' if response.status_code == 429 or response.status_code >= 500 else 'ERROR')
                raise OAuthRefreshError(status)
            self.client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
        except Exception:
            self.client.close()
            raise

    def close(self):
        self.client.close()

    def request(self,method,url,**kwargs):
        for attempt in range(4):
            try:
                if self.tokens:
                    try:
                        self.client.headers['Authorization']='Bearer '+self.tokens.get(refresh=attempt>0)
                    except Exception:
                        raise ValueError('Existing Drive authorization could not be refreshed; original OAuth files were preserved') from None
                response=self.client.request(method,url,**kwargs)
                if response.status_code not in (429,500,502,503,504):
                    if response.status_code >=400:
                        raise ValueError(f'Drive API error {response.status_code}; verify authorization, permissions and quota')
                    return response
            except httpx.TransportError:
                if attempt==3:
                    raise ValueError('Drive connection interrupted; retry resumes the saved upload session') from None
            time.sleep(2**attempt)
        raise ValueError('Drive retry limit exceeded; retry this job later')

    def folder(self,name,parent=None):
        # All folder names here are application-generated.
        query=f"trashed=false and mimeType='application/vnd.google-apps.folder' and name='{name}'"
        if parent:
            query+=f" and '{parent}' in parents"
        found=self.request('GET',BASE+'/files',params={'q':query,'fields':'files(id)','supportsAllDrives':'true','includeItemsFromAllDrives':'true'}).json()['files']
        if found:
            return found[0]['id']
        body={'name':name,'mimeType':'application/vnd.google-apps.folder'}
        if parent:
            body['parents']=[parent]
        return self.request('POST',BASE+'/files',params={'supportsAllDrives':'true'},json=body).json()['id']

    def job_folders(self,jid):
        parent=os.environ.get('DRIVE_PARENT_ID') or None
        if parent:
            self.private_destination(parent)
        root=self.folder('AIContentFactory',parent)
        self.private_destination(root)
        folders={name:self.folder(name,root) for name in ('sources','jobs','backgrounds','archive')}
        job=self.folder(jid,folders['jobs'])
        return {'job':job,**{name:self.folder(name,job) for name in ('subtitles','shorts','longs','thumbnails','sources')}}

    def private_destination(self,folder_id):
        item=self.request('GET',BASE+'/files/'+folder_id,params={'fields':'id,permissions(type)','supportsAllDrives':'true'}).json()
        if 'permissions' not in item or any(p.get('type') in ('anyone','domain') for p in item['permissions']):
            raise ValueError('Drive destination is public/domain-shared or its privacy could not be verified. Choose a private destination shared only with authorized users.')

    def upload(self,path,parent,key,tick=lambda *_:None):
        with path.open('rb') as source:
            digest=hashlib.file_digest(source,'md5').hexdigest()
        size=path.stat().st_size
        # Stable logical identity protects against crashes after the final PUT.
        prior=self.request('GET',BASE+'/files',params={'q':f"trashed=false and appProperties has {{ key='factoryKey' and value='{key}' }}",'fields':'files(id,size,md5Checksum,webViewLink)','supportsAllDrives':'true','includeItemsFromAllDrives':'true'}).json()['files']
        if prior:
            result=prior[0]
            if result.get('md5Checksum')!=digest or int(result.get('size',0))!=size:
                raise ValueError('Drive identity exists with different content; manual inspection required')
            return result
        checkpoint=path.with_suffix(path.suffix+'.upload-session')
        session=checkpoint.read_text() if checkpoint.exists() else None
        offset=0
        if session:
            if not session.startswith('https://www.googleapis.com/'):
                raise ValueError('Invalid persisted Drive session')
            response=self.request('PUT',session,headers={'Content-Length':'0','Content-Range':f'bytes */{size}'},content=b'')
            if response.status_code in (200,201):
                result=response.json()
                return self.verify(result['id'],size,digest)
            offset=acknowledged(response)
        else:
            response=self.request('POST','https://www.googleapis.com/upload/drive/v3/files',params={'uploadType':'resumable','supportsAllDrives':'true','fields':'id'},json={'name':path.name,'parents':[parent],'appProperties':{'factoryKey':key}},headers={'X-Upload-Content-Type':mimetypes.guess_type(path.name)[0] or 'application/octet-stream','X-Upload-Content-Length':str(size)})
            session=response.headers['Location']
            checkpoint.write_text(session)
            try:
                checkpoint.chmod(0o600)
            except OSError:
                pass
        with path.open('rb') as stream:
            stream.seek(offset)
            while offset<size:
                data=stream.read(CHUNK)
                response=self.request('PUT',session,content=data,headers={'Content-Type':'application/octet-stream','Content-Length':str(len(data)),'Content-Range':f'bytes {offset}-{offset+len(data)-1}/{size}'})
                if response.status_code==308:
                    new_offset=acknowledged(response)
                    if new_offset<=offset:
                        raise ValueError('Drive upload made no progress; retry later')
                    offset=new_offset
                    stream.seek(offset)
                else:
                    offset=size
                tick(offset,size)
        result=self.verify(response.json()['id'],size,digest)
        checkpoint.unlink(missing_ok=True)
        return result

    def verify(self,file_id,size,digest):
        result=self.request('GET',BASE+'/files/'+file_id,params={'fields':'id,size,md5Checksum,webViewLink','supportsAllDrives':'true'}).json()
        if int(result.get('size',0))!=size or result.get('md5Checksum')!=digest:
            raise ValueError('Drive integrity verification failed; local output has been preserved')
        return result
