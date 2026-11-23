"""Managed destinations layered over the legacy YouTube channel registry."""
import json
import os
from pathlib import Path
import time

import httpx

YT = 'https://www.googleapis.com/youtube/v3'
PROFILES = {
    'leonida_mayhem': dict(title='Leonida Mayhem', handle='@leo_may',
                           language='en', profile='entertainment', mode='shorts'),
    'thevicescope': dict(title='TheViceScope', handle=None,
                         language='en', profile='editorial', mode='longs'),
}


def directory(analytics: bool = False) -> Path:
    name = 'FACTORY_MANAGED_ANALYTICS_DIR' if analytics else 'FACTORY_MANAGED_YOUTUBE_DIR'
    default = 'secrets/youtube-managed/analytics' if analytics else 'secrets/youtube-managed/upload'
    return Path(os.environ.get(name, default))


def identity(items: list[dict], key: str, expected: str | None = None) -> dict:
    """Ambiguous consent never establishes a managed channel binding."""
    if len(items) != 1 or not isinstance(items[0].get('id'), str) or not items[0]['id']:
        raise ValueError('Choose exactly one intended YouTube channel in Google consent.')
    item = items[0]
    if expected:
        if item['id'] != expected:
            raise ValueError('OAuth identifies a different channel. Switch YouTube identity and reconnect.')
    else:
        profile = PROFILES[key]
        snippet = item.get('snippet', {})
        handle = str(snippet.get('customUrl', '')).casefold().lstrip('@')
        if str(snippet.get('title', '')).casefold() != profile['title'].casefold() or (
            profile['handle'] and handle != profile['handle'].casefold().lstrip('@')
        ):
            raise ValueError('OAuth identifies a different channel. Switch YouTube identity and reconnect.')
    return item


def profiles() -> list[dict]:
    from . import store
    with store.connect() as db:
        rows = db.execute('SELECT * FROM youtube_profiles ORDER BY channel_key').fetchall()
    return [{**dict(row), **PROFILES[row['channel_key']], 'enabled': bool(row['enabled'])} for row in rows]


def record_status(key: str, status: str, error: str | None = None) -> None:
    from . import store
    with store.connect() as db:
        db.execute('UPDATE youtube_profiles SET status=?,error=? WHERE channel_key=?',
                   (status, error, key))


def discover(key: str, *, legacy_alias: str | None = None) -> None:
    from . import store
    from .drive import Drive, OAuthRefreshError
    from .publisher import token_file

    profile = next(row for row in profiles() if row['channel_key'] == key)
    path = directory() / (key + '.json')
    with store.connect() as db:
        prior = db.execute('SELECT alias FROM channels WHERE id=?',(profile['channel_id'],)).fetchone()
    prior_alias = prior['alias'] if prior else None
    alias = legacy_alias or (prior_alias if prior_alias and not path.is_file() else key)
    if legacy_alias in PROFILES:
        raise ValueError('Select an existing legacy credential alias.')
    if not path.is_file() and alias == key:
        record_status(key, 'AUTHORIZATION_REQUIRED' if profile['channel_id'] else 'NOT_CONNECTED')
        return
    client = None
    verified = False
    try:
        is_managed_file = alias == key
        bound = json.loads(path.read_text(encoding='utf-8-sig'))['youtube_channel_id'] if is_managed_file else profile['channel_id']
        if is_managed_file and (not isinstance(bound, str) or not bound or profile['channel_id'] not in (None, bound)):
            raise ValueError('Credential binding does not match the registry.')
        client = Drive(path if is_managed_file else token_file(alias))
        remote = client.request('GET', YT + '/channels', params={'part': 'id,snippet', 'mine': 'true'})
        item = identity(remote.json()['items'], key, profile['channel_id'])
        if bound and item['id'] != bound:
            raise ValueError('Credential binding does not match the authenticated channel.')
        bound = item['id']
        verified = True
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            binding = db.execute('SELECT channel_id FROM youtube_profiles WHERE channel_key=?',(key,)).fetchone()
            collision = db.execute('SELECT channel_key FROM youtube_profiles WHERE channel_id=? AND channel_key<>?', (bound,key)).fetchone()
            existing = db.execute('SELECT id,alias FROM channels WHERE id=? OR alias=?', (bound,alias)).fetchall()
            if binding['channel_id'] not in (None,bound) or collision or any(
                row['id'] != bound or row['alias'] not in (alias,prior_alias) for row in existing
            ):
                raise ValueError('The authenticated channel is already associated with another connection.')
            if legacy_alias and not any(row['id'] == bound and row['alias'] == alias for row in existing):
                raise ValueError('The legacy alias must already be registered for this channel.')
            db.execute('INSERT INTO channels(id,alias,title) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET alias=excluded.alias,title=excluded.title',
                       (bound,alias,item['snippet']['title']))
            db.execute("UPDATE youtube_profiles SET channel_id=?,status='CONNECTED',error=NULL,verified_at=? WHERE channel_key=?",
                       (bound,time.time(),key))
    except OAuthRefreshError as error:
        record_status(key,error.status,'Run the channel-specific OAuth reconnect command.'
                      if error.status == 'REAUTHORIZATION_REQUIRED' else 'Check connectivity and verify again.')
    except (ValueError, KeyError, TypeError, OSError, httpx.HTTPError):
        record_status(key,'ERROR','Channel already bound elsewhere.' if verified else 'Could not verify this channel binding. Check the selected YouTube identity and reconnect.')
    finally:
        if client:
            client.close()


def enable(key: str, enabled: bool) -> None:
    from . import store
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not enabled and db.execute("""SELECT 1 FROM publications p JOIN youtube_profiles y ON y.channel_id=p.channel
                                         WHERE y.channel_key=? AND p.state='PUBLISHING'""",(key,)).fetchone():
            raise ValueError('An upload is active. Wait for it to finish before disconnecting.')
        if not db.execute('UPDATE youtube_profiles SET enabled=? WHERE channel_key=?',(enabled,key)).rowcount:
            raise ValueError('Unknown managed YouTube profile.')


def require_ready(channel: str, db) -> str | None:
    row = db.execute('SELECT channel_key,enabled,status FROM youtube_profiles WHERE channel_id=?',(channel,)).fetchone()
    if row and (not row['enabled'] or row['status'] != 'CONNECTED'):
        raise ValueError('The target YouTube channel is disconnected or requires authorization.')
    return row['channel_key'] if row else None
