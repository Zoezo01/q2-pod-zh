#!/usr/bin/env python3
"""Release failure paths without network access or proprietary firmware."""
import json
import pathlib
import subprocess
import tempfile
from unittest.mock import patch
import sys; sys.path.insert(0, sys.path[0] + '/../tools')  # tools/ first
import release

# Changing either guide input must invalidate an already packaged release.
source = release.source_sha256()
read_bytes = pathlib.Path.read_bytes
for name in ('build.py', 'guide.html'):
    target = release.ROOT/'docs/manual'/name
    with patch.object(pathlib.Path, 'read_bytes', lambda p: read_bytes(p) + (b'\n' if p == target else b'')):
        assert release.source_sha256() != source, name

with tempfile.TemporaryDirectory() as tmp:
    out = pathlib.Path(tmp)/'package'
    # This version's changelog entry, in the format since V7.7; older entries are history.
    (pathlib.Path(tmp)/'docs').mkdir()
    (pathlib.Path(tmp)/'docs/changelog.md').write_text(
        f'# Changelog\n\n- **{release.VERSION}**: One. iPod: two.\n- **V7.6R / V7.6I**: Old.\n')
    patch('release.ROOT', pathlib.Path(tmp)).start()
    with patch('release.run', return_value=' M tools/build.py\n'):
        try:
            release.package(pathlib.Path('stock.zip'), out)
        except ValueError as e:
            assert 'Commit' in str(e)
        else:
            raise AssertionError('Packaged uncommitted build inputs')
    assert not out.exists()
    # Fail the repeat build, so the first one succeeds before the failure.
    def fail_repeat(stock, directory, logo):
        if directory.name.endswith('-repeat'):
            raise ValueError('simulated repeat build failure')
        directory.mkdir()
        (directory/'update.tar').write_bytes(b'pod')
        (directory/'manifest.json').write_text('{}')
    with patch('release.build', side_effect=fail_repeat), patch('release.validate'), patch('release.run', side_effect=lambda *a: '' if 'status' in a else 'revision'):
        try:
            release.package(pathlib.Path('stock.zip'), out)
        except ValueError as e:
            assert 'repeat build failure' in str(e)
        else:
            raise AssertionError('Accepted a failed build')
    assert not (out/'release.json').exists()
    (out/'build/manifest.json').write_text(json.dumps(dict(version=release.TAG, update_sha256=release.sha(b'pod'))))
    (out/release.ASSET).write_bytes(b'pod')
    assets = {release.ASSET: release.sha(b'pod')}
    (out/release.GUIDE).write_bytes(b'%PDF')
    record = dict(tag=release.VERSION, revision='revision', source_sha256=release.source_sha256(), assets=assets,
                  guide_sha256=release.sha(b'%PDF'))
    (out/'release.json').write_text(json.dumps(record))
    (out/'release-notes.md').write_text(release.release_body(out, record))
    # The heading, then bullets, for this version only.
    body = (out/'release-notes.md').read_text()
    assert body.startswith(f'**{release.VERSION}**\n- One.\n- iPod: two.\n\nSHA-256:\n- update.tar ({release.TAG}): ')
    # A nested entry keeps its bullets as written, one level out, up to the next version.
    changelog = pathlib.Path(tmp)/'docs/changelog.md'
    single = changelog.read_text()
    changelog.write_text(f'- **{release.VERSION}**:\n  - A.\n    - B.\n- **V7.6R / V7.6I**: Old.\n')
    assert release.notes() == f'**{release.VERSION}**\n- A.\n  - B.'
    changelog.write_text(single)
    (out/'SHA256SUMS').write_text(''.join(f'{v}  {k}\n' for k, v in assets.items()))
    for failure in ('upload', 'download', 'corrupt', None):
        calls = []
        def gh(*args):
            calls.append(args)
            command = args[4]
            if command == 'list': return '[]'
            if command == failure:
                raise subprocess.CalledProcessError(1, args)
            if command == 'download':
                target = pathlib.Path(args[-1])
                for name in [*assets, release.GUIDE]:
                    (target/name).write_bytes((out/name).read_bytes() if failure != 'corrupt' else b'bad')
            return ''
        with patch('release.run', side_effect=gh):
            try:
                release.upload(out, True)
            except (ValueError, subprocess.CalledProcessError):
                assert failure
            else:
                assert failure is None
        assert any('--draft=false' in c for c in calls) == (failure is None)
        # Network Update's fixed names and the guide, no ZIPs.
        sent = {pathlib.Path(a).name for c in calls if c[4] == 'upload' for a in c[6:]}
        assert failure == 'upload' or sent == {'update.tar', release.GUIDE, '--clobber'}, sent
    # An asset that isn't the build's update.tar must fail before contacting GitHub.
    (out/release.ASSET).write_bytes(b'other')
    with patch('release.run') as gh:
        try: release.upload(out)
        except ValueError: pass
        else: raise AssertionError('Accepted a mismatched update')
        gh.assert_not_called()
    # A missing one must too.
    (out/release.ASSET).unlink()
    with patch('release.run') as gh:
        try: release.upload(out)
        except OSError: pass
        else: raise AssertionError('Accepted a missing update')
        gh.assert_not_called()
print('Release build/upload failure checks passed.')
