#!/usr/bin/env python3
"""Put a Last.fm session key in a card's .scrobble.ini, in the password's place.

    python3 lastfm_key.py /path/to/card/.scrobble.ini

Reads [LASTFM]'s USER, API_KEY and API_SECRET, signs in to Last.fm once from this computer (auth.getMobileSession
over HTTPS, the call the Q2 otherwise makes with the password at every upload) and writes the SESSION_KEY Last.fm
returns where the PASSWORD line was. Without a PASSWORD line it asks for the password, which then never reaches the
card. Run it again for a new key.

The key lets whoever holds the card scrobble to the account, but not sign in to last.fm or change the account. It
doesn't expire; Last.fm's Settings > Applications withdraws it. Neither the password nor the key is shown, and the
file is replaced whole or left as it was. A password the card has held is not wiped from the card's free space,
where recovery tools may still find it: change it on last.fm if that matters. Python's standard library only.
(docs/internals.md#scrobbling)"""
import contextlib
import getpass
import hashlib
import http.client
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

URL = 'https://ws.audioscrobbler.com/2.0/'
ENC, ERR = 'utf-8', 'surrogateescape'  # a byte that isn't UTF-8 goes back as it came
REPLY_MAX = 1 << 16  # Last.fm's answers are a few hundred bytes
KEY = re.compile(r'[0-9A-Za-z_-]{1,63}')  # what goes in the file: one line's worth, the 63 the Q2 keeps of its own


def fields(lines):
    """([LASTFM]'s line, its (line, key, value)s), read as the Q2 reads the file: a section runs to the next heading
    and its lines are KEY=value, the spaces around each trimmed. A second [LASTFM] section's lines are listed too."""
    start, found, inside = None, [], False
    for i, line in enumerate(lines):
        text = line.strip()
        if text.startswith('['):
            inside = text == '[LASTFM]'
            start = i if inside and start is None else start
        elif inside and '=' in text:
            key, value = text.split('=', 1)
            found.append((i, key.strip(), value.strip()))
    return start, found


def form(params, secret):
    """A signed call's body, as patch/scrobble.c's lastfm() sends it: the parameters by name, then api_sig, the MD5
    of those names and values run together and the secret, then format, which isn't signed."""
    signed = sorted(params.items())
    digest = hashlib.md5((''.join(k + v for k, v in signed) + secret).encode(ENC, ERR), usedforsecurity=False)
    body = signed + [('api_sig', digest.hexdigest()), ('format', 'json')]
    return urllib.parse.urlencode(body, encoding=ENC, errors=ERR).encode()


def session_key(user, password, key, secret, urlopen=urllib.request.urlopen):
    """The account's session key. SystemExit says why there is none: Last.fm's reason, or the network's."""
    call = {'method': 'auth.getMobileSession', 'username': user, 'password': password, 'api_key': key}
    answer = None
    try:
        try:
            with urlopen(urllib.request.Request(URL, form(call, secret)), timeout=30) as reply:
                raw = reply.read(REPLY_MAX)
        except urllib.error.HTTPError as e:  # a refusal comes as 403 with the reason in its body
            raw = e.read(REPLY_MAX)
    except (OSError, http.client.HTTPException) as e:
        raise SystemExit(f"Couldn't reach Last.fm: {getattr(e, 'reason', e)}")
    try:
        answer = json.loads(raw)
        session = answer['session']['key']
    except (ValueError, KeyError, TypeError):
        session = None
    if not isinstance(session, str) or not KEY.fullmatch(session):
        reason = answer.get('message') if isinstance(answer, dict) else None
        if not isinstance(reason, str) or not reason.isprintable():
            reason = 'its answer was not the one expected'
        raise SystemExit(f'Last.fm gave no session key: {reason[:200]}')
    return session


def swap(text, key):
    """text with SESSION_KEY=key where [LASTFM]'s first PASSWORD or SESSION_KEY line was (under the heading when it
    had neither) and none of its other PASSWORD or SESSION_KEY lines. Every other byte, and the line endings, stay."""
    lines = text.splitlines(keepends=True)
    start, found = fields(lines)
    old = [i for i, k, _ in found if k in ('PASSWORD', 'SESSION_KEY')]
    at = old[0] if old else start + 1
    for i in reversed(old):
        del lines[i]
    lines.insert(at, f'SESSION_KEY={key}' + ('\r\n' if lines[start].endswith('\r\n') else '\n'))
    return ''.join(lines)


def main(argv, urlopen=urllib.request.urlopen, ask=getpass.getpass):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    path = os.path.realpath(argv[1])  # the file itself, when the name given is a link to it
    try:
        with open(path, encoding=ENC, errors=ERR, newline='') as f:
            text = f.read()
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError as e:
        raise SystemExit(f"Can't read {path}: {e.strerror or e}")
    value = {}
    for _, k, v in fields(text.splitlines(keepends=True))[1]:
        value.setdefault(k, v)  # a key's first line counts
    missing = [k for k in ('USER', 'API_KEY', 'API_SECRET') if not value.get(k)]
    if missing:
        raise SystemExit(f"{path}: [LASTFM] needs {', '.join(missing)}")
    password = value.get('PASSWORD') or ask(f"Last.fm password for {value['USER']} (not shown, not saved): ")
    key = session_key(value['USER'], password, value['API_KEY'], value['API_SECRET'], urlopen)
    tmp = path + '.tmp'
    try:  # the new file: its owner's alone while it is written, whole and on the disk before it takes the old one's place
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        with open(fd, 'w', encoding=ENC, errors=ERR, newline='') as f:
            f.write(swap(text, key))
            f.flush()
            os.fsync(fd)
        os.replace(tmp, path)
    except OSError as e:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise SystemExit(f"{path} is as it was: the new file couldn't be written ({e.strerror or e})")
    with contextlib.suppress(OSError):
        os.chmod(path, mode)  # as the old file's
    print(f"{path}: SESSION_KEY is in and no password is. Last.fm's Settings > Applications withdraws the key.")


if __name__ == '__main__':
    main(sys.argv)
