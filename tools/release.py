#!/usr/bin/env python3
"""Package the release locally; optionally upload and publish it, verified."""
import argparse
import hashlib
import importlib.util
import itertools
import json
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
from build import ROOT, REPO, VERSION, TAG, ZIP_SHA, DEMO_SHA, build, check, run, sha, source_sha256

# Network Update (patch/netupdate.c) fetches releases/latest/download/<ASSET>, so the name can't
# carry the version.
ASSET = 'update.tar'
# Rebuilt from docs/manual every release; the README links releases/latest/download/<GUIDE>.
GUIDE = 'Q2-Pod-User-Guide.pdf'


def notes():
    """This version's entry from docs/changelog.md, so a release edits only VERSION and the changelog:
    its bold version heading, then one bullet a sentence, or its nested bullets as written."""
    prefix = f'- **{VERSION}**'  # entries up to V1.0 read **V1.0**, up to V7.6 **V7.6R / V7.6I**
    lines = (ROOT/'docs/changelog.md').read_text().splitlines()
    i = next((i for i, l in enumerate(lines) if l.startswith(prefix)), None)
    check(i is not None, f'docs/changelog.md has no {prefix} entry')
    title, text = lines[i][2:].split(':', 1)
    if not text.strip():  # a nested entry: its indented bullets, one level out
        return '\n'.join([title, *(l[2:] for l in itertools.takewhile(lambda l: l.startswith('  '), lines[i+1:]))])
    return '\n'.join([title, *('- '+s for s in re.split(r'(?<=[.!?]) +(?=[A-Z*"]|iPod)', text.strip()))])


def release_body(out, record):
    """The version heading and its bullets plus the SHA-256 block used by previous releases."""
    return '\n'.join([notes(), '', 'SHA-256:', f'- {ASSET} ({TAG}): `{record["assets"][ASSET]}`']) + '\n'


def validate(directory):
    m = json.loads((directory/'manifest.json').read_text())
    check(m['version'] == TAG, 'Wrong build identity')
    check(m['update_repo'] == REPO, f"Network Update fetches {m['update_repo']}'s releases, not {REPO}'s")
    check(m['input_zip_sha256'] == ZIP_SHA and m['stock_demo_sha256'] == DEMO_SHA, 'Wrong stock identity')
    # test/patch.py (below) checks the sources and the demo, stock-demo and patch.bin hashes.
    for name, key in [('rootfs.squashfs', 'rootfs_sha256'), ('update.tar', 'update_sha256')]:
        check(sha((directory/name).read_bytes()) == m[key], f'{directory/name}: hash mismatch')
    with tarfile.open(directory/'update.tar') as t:
        check(t.getnames() == ['firmware_v20.info', 'recovery-update', 'recovery-update/xImage',
                               'recovery-update/rootfs.squashfs'], 'Unexpected update members')
        info = t.extractfile('firmware_v20.info').read().decode().splitlines()
        check(info[:2] == ['Shanling Q2', TAG] and len(info) == 4, 'Wrong update identity')
        for line, name, key in zip(info[2:], ['recovery-update/xImage', 'recovery-update/rootfs.squashfs'],
                                  ['kernel_sha256', 'rootfs_sha256']):
            data = t.extractfile(name).read()
            check(line.split() == [hashlib.md5(data).hexdigest(), name] and sha(data) == m[key], 'Update payload mismatch')
    for test, *args in (('peq',), ('build', '--build', directory), ('patch', directory),
                        ('playback', directory), ('m4b', directory), ('ldac', directory),
                        ('id3', directory), ('headset_io', directory), ('bluealsa', directory),
                        ('coverflow',), ('mp3',), ('mp3_gapless',),
                        ('mp3_gapless_player', directory), ('bt_resample', directory), ('xfade_player', directory), ('remote',)):
        subprocess.run([sys.executable, ROOT/f'test/{test}.py', *map(str, args)], check=True)


def package(stock, out):
    check(not out.exists(), 'Release output must be a fresh directory')
    check(not run('git', '-C', ROOT, 'status', '--porcelain', '--', 'patch', 'tools', 'assets', 'docs', 'test', 'requirements.txt').strip(),
          'Commit the build inputs before packaging')
    out.mkdir(parents=True)
    source = source_sha256()
    revision = run('git', '-C', ROOT, 'rev-parse', 'HEAD').strip()
    for suffix in ('', '-repeat'):
        build(stock, out/('build'+suffix), ROOT/'assets/boot-logo.jpg')
    # The byte comparison below proves the repeat build; validate the first only.
    validate(out/'build')
    data = (out/'build/update.tar').read_bytes()
    check(data == (out/'build-repeat/update.tar').read_bytes(), 'Non-reproducible update')
    (out/ASSET).write_bytes(data)
    checksums = {ASSET: sha(data)}
    subprocess.run([sys.executable, str(ROOT/'docs/manual/build.py'), str(out/GUIDE)], check=True)
    check(source == source_sha256() and revision == run('git', '-C', ROOT, 'rev-parse', 'HEAD').strip(),
          'Source changed during release build')
    record = dict(tag=VERSION, revision=revision, source_sha256=source, assets=checksums,
                  guide_sha256=sha((out/GUIDE).read_bytes()))
    (out/'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in checksums.items()))
    (out/'release-notes.md').write_text(release_body(out, record))
    (out/'release.json').write_text(json.dumps(record, indent=2)+'\n')
    print(f'Validated and reproducible: {out}')


def upload(out, publish=False):
    record = json.loads((out/'release.json').read_text())
    check(record['tag'] == VERSION and record['source_sha256'] == source_sha256(), 'Wrong release source/tag')
    check(set(record['assets']) == {ASSET}, f'The release is {ASSET} alone')
    # package() validated this tree; the update bytes and source hash prove it is unchanged.
    data = (out/ASSET).read_bytes()
    check(sha(data) == record['assets'][ASSET] and data == (out/'build/update.tar').read_bytes(),
          'Release update mismatch')
    check(sha((out/GUIDE).read_bytes()) == record['guide_sha256'], 'User guide mismatch')
    sums = f'{record["assets"][ASSET]}  {ASSET}\n'
    check((out/'SHA256SUMS').read_text() == sums, 'Release checksums mismatch')
    (out/'release-notes.md').write_text(release_body(out, record))
    gh = ['gh', '--repo', REPO, 'release']
    # Listing distinguishes an absent tag from an authentication/network failure.
    releases = json.loads(run(*gh, 'list', '--limit', '1000', '--json', 'tagName,isDraft'))
    existing = next((r for r in releases if r['tagName'] == VERSION), None)
    if existing:
        check(existing['isDraft'], 'Refusing to overwrite a published release')
    run(*gh, 'edit' if existing else 'create', VERSION, '--draft', '--target', record['revision'], '--title', VERSION,
        '--notes-file', out/'release-notes.md')
    files = [ASSET, GUIDE]
    run(*gh, 'upload', VERSION, *(out/name for name in files), '--clobber')
    with tempfile.TemporaryDirectory(prefix='q2-release-verify-') as tmp:
        run(*gh, 'download', VERSION, '--dir', tmp)
        for name in files:
            check((pathlib.Path(tmp)/name).read_bytes() == (out/name).read_bytes(), f'Remote asset mismatch: {name}')
    if publish:
        run(*gh, 'edit', VERSION, '--draft=false')
    print(f'{"Published" if publish else "Verified draft"}: https://github.com/{REPO}/releases/tag/{VERSION}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest='command', required=True)
    p = commands.add_parser('package')
    p.add_argument('zip', type=pathlib.Path)
    p.add_argument('--out', type=pathlib.Path, required=True)
    p = commands.add_parser('upload')
    p.add_argument('out', type=pathlib.Path)
    p.add_argument('--publish', action='store_true')
    a = ap.parse_args()
    try:
        if a.command == 'package':
            # Validation runs the MIPS suite, so fail before two builds, not after them.
            check(importlib.util.find_spec('unicorn') is not None,
                  'Release validation needs the requirements.txt environment: pip install -r requirements.txt')
            package(a.zip, a.out.resolve())
        else:
            upload(a.out.resolve(), publish=a.publish)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        ap.error(str(exc))
