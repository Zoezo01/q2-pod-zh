#!/usr/bin/env python3
"""Publish this fork's own GitHub Release so Network Update can find it.

Upstream's tools/release.py is its ZIP-input, English-changelog pipeline: it
runs twenty test suites and builds a PDF guide, none of which this fork ships.
This one only does what patch/netupdate.c actually needs --

  * a latest, non-draft release whose tag is exactly RELEASE_TAG (the bare
    VERSION for a line's first build, then VERSION point releases, 1.8.3.2 …),
  * one asset named update.tar, with GitHub's own size and sha256 digest,
  * release notes taken from docs/changelog-zh.md's newest VERSION entry.

Usage:
    GITHUB_TOKEN=... python3 tools/release_zh.py ~/q2-build-v183c
    python3 tools/release_zh.py ~/q2-build-v183c --dry-run   # verify + show notes only

The token is read from the environment, never from a file, argument or URL.
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import tarfile
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build import ROOT, REPO, VERSION, TAG, RELEASE_TAG, ZIP_SHA, TAR_SHA, DEMO_SHA, sha  # noqa: E402

ASSET = 'update.tar'
API = 'https://api.github.com'
UPLOADS = 'https://uploads.github.com'
MEMBERS = ['firmware_v20.info', 'recovery-update', 'recovery-update/xImage',
           'recovery-update/rootfs.squashfs']


def need(ok, message):
    if not ok:
        raise SystemExit(message)


def notes():
    """The newest VERSION section of the Chinese changelog, as markdown."""
    lines = (ROOT / 'docs/changelog-zh.md').read_text(encoding='utf-8').splitlines()
    head = f'## {VERSION}'
    i = next((i for i, line in enumerate(lines) if line.startswith(head)), None)
    need(i is not None, f'docs/changelog-zh.md has no {head} entry')
    body = []
    for line in lines[i + 1:]:
        if line.startswith('## ') or line.strip() == '---':
            break
        body.append(line)
    text = '\n'.join(body).strip()
    need(text, f'{head} has no release notes')
    return f'{lines[i]}\n\n{text}\n'


def verify(directory):
    """The build is really this fork's, this version's, and the pinned V1.36 stock."""
    m = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    need(m.get('version') == TAG, f"Wrong build identity: {m.get('version')} != {TAG}")
    need(m.get('update_repo') == REPO,
         f"Network Update fetches {m.get('update_repo')}'s releases, not {REPO}'s")
    need(m.get('input_sha256') in (TAR_SHA, ZIP_SHA), 'Wrong stock input hash')
    need(m.get('stock_demo_sha256') == DEMO_SHA, 'Wrong stock demo hash')
    data = (directory / ASSET).read_bytes()
    need(sha(data) == m['update_sha256'], 'update.tar: hash mismatch')
    with tarfile.open(directory / ASSET) as t:
        need(t.getnames() == MEMBERS, 'Unexpected update members')
        info = t.extractfile('firmware_v20.info').read().decode().splitlines()
        need(info[:2] == ['Shanling Q2', TAG] and len(info) == 4, 'Wrong update identity')
        for line, name in zip(info[2:], MEMBERS[2:]):
            blob = t.extractfile(name).read()
            need(line.split() == [hashlib.md5(blob).hexdigest(), name], f'{name}: md5 mismatch')
    return data, info[0]


def api(method, path, payload=None, raw=None, host=API):
    token = os.environ.get('GITHUB_TOKEN')
    need(token, 'Set GITHUB_TOKEN to a token that can create releases in ' + REPO)
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'q2-pod-zh-release'}
    body = None
    if raw is not None:
        headers['Content-Type'] = 'application/octet-stream'
        body = raw
    elif payload is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(host + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            out = response.read()
            return json.loads(out) if out else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and method == 'GET':
            return None
        raise SystemExit(f'GitHub {method} {path}: {exc.code} {exc.read().decode()[:400]}')
    except urllib.error.URLError as exc:
        raise SystemExit(f'GitHub {method} {path}: {exc.reason}')


def publish(directory, data, text):
    existing = api('GET', f'/repos/{REPO}/releases/tags/{RELEASE_TAG}')
    need(not existing, f'Tag {RELEASE_TAG} already has a release: delete or edit it by hand first')
    release = api('POST', f'/repos/{REPO}/releases',
                  dict(tag_name=RELEASE_TAG, name=RELEASE_TAG, body=text, draft=True,
                       prerelease=False, make_latest='false'))
    rid = release['id']
    print(f'Draft {RELEASE_TAG} created: {release["html_url"]}')
    asset = api('POST', f'/repos/{REPO}/releases/{rid}/assets?name={ASSET}',
                raw=data, host=UPLOADS)
    need(asset.get('name') == ASSET, 'Upload did not return update.tar')
    listed = api('GET', f'/repos/{REPO}/releases/{rid}/assets')
    found = next((a for a in listed if a['name'] == ASSET), {})
    need(found.get('size') == len(data) and found.get('digest') == 'sha256:' + sha(data),
         "GitHub's record of update.tar has no matching size and digest: Network Update would refuse it")
    api('PATCH', f'/repos/{REPO}/releases/{rid}', dict(draft=False, make_latest='true'))
    print(f'Published {RELEASE_TAG}: https://github.com/{REPO}/releases/tag/{RELEASE_TAG}')
    print(f'  {ASSET}: {len(data)} bytes, sha256 {sha(data)}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('build', type=pathlib.Path, help='tools/build.py --out directory')
    ap.add_argument('--dry-run', action='store_true', help='verify the build and show the notes, publish nothing')
    a = ap.parse_args()
    directory = a.build.resolve()
    need(directory.is_dir(), f'No such build directory: {directory}')
    data, _ = verify(directory)
    text = notes()
    print(f'Verified {directory / ASSET}: {TAG}, {REPO}, {len(data)} bytes, sha256 {sha(data)}')
    if a.dry_run:
        print('--- release notes ---\n' + text)
        return
    publish(directory, data, text)


if __name__ == '__main__':
    main()
