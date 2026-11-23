"""One-time host-side YouTube consent; no upload or publication is performed."""
import argparse
import json
import os
from pathlib import Path
import secrets
from tempfile import NamedTemporaryFile
from collections.abc import Callable
import httpx
from .legacy_drive_oauth import OAuthClient, DriveError, receive_code
from .youtube_channels import PROFILES, YT, directory, identity

SCOPES='https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly'
ANALYTICS_SCOPES='https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly'


def read_youtube_client(path: Path) -> OAuthClient:
    """A legacy authorized-user file can supply client fields, never its grant."""
    try:
        return OAuthClient.read(path)
    except DriveError:
        try:
            record = json.loads(path.read_text(encoding='utf-8-sig'))
            if record.get('type') != 'authorized_user':
                raise ValueError
            client_id, secret = record['client_id'], record['client_secret']
            if not all(isinstance(value,str) and value for value in (client_id,secret)):
                raise ValueError
            return OAuthClient(client_id,secret)
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            raise DriveError('youtube_desktop_client_config_invalid') from None


def authorize(client_file, destination, *, analytics=False, open_browser=False,
              channel_key: str | None = None, reconnect: bool = False,
              confirm_identity: Callable[[dict], bool] | None = None):
    destination=Path(destination)
    previous = None
    expected = None
    if destination.exists():
        if not reconnect or not channel_key:
            raise DriveError('youtube_token_already_exists_choose_another_alias')
        previous = destination.read_bytes()
        expected = json.loads(previous).get('youtube_channel_id')
        if not isinstance(expected, str) or not expected:
            raise DriveError('youtube_reconnect_requires_existing_verified_binding')
    original=read_youtube_client(Path(client_file))
    scopes=ANALYTICS_SCOPES if analytics else SCOPES
    client=OAuthClient(original.client_id,original.client_secret,scopes)
    verifier=secrets.token_urlsafe(64)
    code,redirect=receive_code(client,verifier,open_browser=True) if open_browser else receive_code(client,verifier)
    with httpx.Client(timeout=30,trust_env=False) as http:
        token=client.exchange(http,{'grant_type':'authorization_code','code':code,'redirect_uri':redirect,'code_verifier':verifier})
        channel = None
        if channel_key:
            try:
                response = http.get(YT+'/channels',params={'part':'id,snippet','mine':'true'},
                                    headers={'Authorization':'Bearer '+token['access_token']})
                response.raise_for_status()
                channel = identity(response.json()['items'], channel_key, expected)
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                raise DriveError('YouTube identity check failed. Switch to the intended channel in YouTube, then retry consent.') from None
    if not isinstance(token.get('refresh_token'),str) or not token['refresh_token']:
        raise DriveError('oauth_refresh_token_missing')
    if channel and not expected and confirm_identity and not confirm_identity(channel):
        raise DriveError('YouTube channel was not confirmed. Credentials were not saved.')
    destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    record = {'type':'authorized_user','client_id':client.client_id,'client_secret':client.client_secret,
              'refresh_token':token['refresh_token'],'scopes':scopes.split()}
    if channel:
        record['youtube_channel_id'] = channel['id']
    lock = destination.parent / '.youtube-credentials.lock'
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        if (destination.read_bytes() if destination.exists() else None) != previous:
            raise DriveError('Credential changed during consent. Verify the existing connection and retry.')
        for other in destination.parent.glob('*.json') if channel else ():
            if other != destination and json.loads(other.read_text(encoding='utf-8-sig')).get('youtube_channel_id') == channel['id']:
                raise DriveError('YouTube channel already has credentials for another profile.')
        with NamedTemporaryFile(mode='w',encoding='utf-8',dir=destination.parent,delete=False) as output:
            temp = Path(output.name)
            try:
                json.dump(record,output)
                output.flush()
                os.fsync(output.fileno())
            except Exception:
                output.close()
                temp.unlink(missing_ok=True)
                raise
        try:
            if previous is None:
                os.link(temp, destination)
            else:
                os.link(destination, destination.with_suffix('.' + secrets.token_hex(8) + '.bak'))
                os.replace(temp,destination)
        finally:
            temp.unlink(missing_ok=True)
    finally:
        lock.unlink(missing_ok=True)
    return channel


def main():
    parser=argparse.ArgumentParser(description='Authorize a YouTube channel without uploading. Run on the host, then Discover in Publisher.')
    parser.add_argument('--client',required=True,type=Path,help='Desktop OAuth client JSON or existing YouTube authorized-user JSON for its client fields')
    parser.add_argument('--output',type=Path,help='Explicit legacy output path or managed <channel-key>.json')
    parser.add_argument('--channel-key',choices=sorted(PROFILES))
    parser.add_argument('--reconnect',action='store_true',help='Replace only a verified same-channel credential and retain a backup')
    parser.add_argument('--analytics',action='store_true',help='Separate read-only Analytics consent; save in secrets/analytics/<alias>.json')
    parser.add_argument('--open-browser',action='store_true',help='Open the system browser without printing the authorization URL')
    args=parser.parse_args()
    if not args.output and not args.channel_key:
        parser.error('Specify --channel-key or the legacy --output option')
    if args.channel_key and args.output and args.output.name != args.channel_key+'.json':
        parser.error('Managed output filename must match --channel-key')
    if args.reconnect and not args.channel_key:
        parser.error('--reconnect requires --channel-key')
    destination = args.output or directory(args.analytics)/(args.channel_key+'.json')
    def confirm(channel: dict) -> bool:
        print(f"Authenticated YouTube channel ID: {channel['id']}. Compare it with the intended channel in YouTube Studio.")
        return input('Type that exact channel ID to save this connection: ').strip() == channel['id']
    try:channel=authorize(args.client,destination,analytics=args.analytics,open_browser=args.open_browser or bool(args.channel_key),channel_key=args.channel_key,reconnect=args.reconnect,confirm_identity=confirm)
    except (DriveError,OSError,ValueError,EOFError) as error:
        raise SystemExit(error.code if isinstance(error,DriveError) else 'youtube_authorization_storage_failed') from None
    if channel:
        print(f"Verified YouTube channel ID: {channel['id']}. Use Discover in Factory.")
    else:
        print('YouTube authorization saved. Use Publisher Discover to verify the intended channel.')


if __name__=='__main__':main()
