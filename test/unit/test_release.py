"""tools/release.py with fake builds and a fake gh: nothing is packaged for real or sent anywhere."""
import hashlib
import io
import json
import pathlib
import subprocess
import tarfile

import pytest

import release


def sha(data):
    return hashlib.sha256(data).hexdigest()


def md5(data):
    return hashlib.md5(data).hexdigest()


MEMBERS = ['firmware_v20.info', 'recovery-update', 'recovery-update/xImage', 'recovery-update/rootfs.squashfs']
KERNEL, ROOTFS = b'kernel', b'rootfs'


def update_tar(path, info=None, members=None, payload=None):
    info = info if info is not None else (f'Shanling Q2\n{release.TAG}\n{md5(KERNEL)} recovery-update/xImage\n'
                                          f'{md5(ROOTFS)} recovery-update/rootfs.squashfs\n')
    contents = {'firmware_v20.info': info.encode(), 'recovery-update/xImage': KERNEL,
                'recovery-update/rootfs.squashfs': ROOTFS, **(payload or {})}
    with tarfile.open(path, 'w') as t:
        for name in members or MEMBERS:
            entry = tarfile.TarInfo(name)
            if name == 'recovery-update':
                entry.type = tarfile.DIRTYPE
                t.addfile(entry)
            else:
                entry.size = len(contents[name])
                t.addfile(entry, io.BytesIO(contents[name]))


def built(directory, manifest=None, **tar):
    directory.mkdir(exist_ok=True)
    (directory/'rootfs.squashfs').write_bytes(ROOTFS)
    update_tar(directory/'update.tar', **tar)
    fields = dict(version=release.TAG, update_repo=release.REPO, input_zip_sha256=release.ZIP_SHA,
                  stock_demo_sha256=release.DEMO_SHA, rootfs_sha256=sha(ROOTFS), kernel_sha256=sha(KERNEL),
                  update_sha256=sha((directory/'update.tar').read_bytes()), **(manifest or {}))
    (directory/'manifest.json').write_text(json.dumps(fields))
    return directory


@pytest.fixture
def scripts(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda args, **kwargs: calls.append((args, kwargs)))
    return calls


def test_validate_runs_every_suite(tmp_path, scripts):
    d = built(tmp_path/'b')
    release.validate(d)
    names = [(pathlib.Path(a[1]).name, a[2:]) for a, _ in scripts]
    s = str(d)
    assert names == [('peq.py', []), ('build.py', ['--build', s]), ('patch.py', [s]), ('playback.py', [s]), ('m4b.py', [s]),
                     ('ldac.py', [s]), ('id3.py', [s]), ('headset_io.py', [s]), ('bluealsa.py', [s]), ('coverflow.py', []),
                     ('mp3.py', []), ('mp3_gapless.py', []), ('mp3_gapless_player.py', [s]), ('bt_resample.py', [s]),
                     ('xfade_player.py', [s]), ('remote.py', [])]
    assert all(k == dict(check=True) for _, k in scripts)
    assert all(a[0] == release.sys.executable and a[1] == release.ROOT/'test'/n for (a, _), (n, _) in zip(scripts, names))


@pytest.mark.parametrize('field,value,message', [
    ('version', 'V000I', '^Wrong build identity$'),
    ('update_repo', 'someone/fork', "^Network Update fetches someone/fork's releases, not DiamondBond/q2-pod's$"),
    ('input_zip_sha256', 'x', '^Wrong stock identity$'),
    ('stock_demo_sha256', 'x', '^Wrong stock identity$'),
    ('rootfs_sha256', 'x', 'rootfs.squashfs: hash mismatch$'),
    ('update_sha256', 'x', 'update.tar: hash mismatch$'),
    ('kernel_sha256', 'x', '^Update payload mismatch$'),
])
def test_validate_refuses_a_manifest(tmp_path, scripts, field, value, message):
    d = built(tmp_path/'b', {})
    fields = json.loads((d/'manifest.json').read_text())
    fields[field] = value
    (d/'manifest.json').write_text(json.dumps(fields))
    with pytest.raises(ValueError, match=message):
        release.validate(d)
    assert not scripts


def test_validate_refuses_extra_members(tmp_path, scripts):
    d = built(tmp_path/'b', members=MEMBERS[::-1])
    with pytest.raises(ValueError, match='^Unexpected update members$'):
        release.validate(d)


@pytest.mark.parametrize('info', [
    'Shanling Q3\nV\nx\ny\n',
    f'Shanling Q2\nV000I\n{md5(KERNEL)} recovery-update/xImage\n{md5(ROOTFS)} recovery-update/rootfs.squashfs\n',
    f'Shanling Q2\n{release.TAG}\n{md5(KERNEL)} recovery-update/xImage\n',
    f'Shanling Q2\n{release.TAG}\n{md5(KERNEL)} recovery-update/xImage\n{md5(ROOTFS)} recovery-update/rootfs.squashfs\nmore\n',
])
def test_validate_refuses_a_wrong_identity(tmp_path, scripts, info):
    d = built(tmp_path/'b', info=info)
    with pytest.raises(ValueError, match='^Wrong update identity$'):
        release.validate(d)


@pytest.mark.parametrize('line2,line3', [
    (f'{md5(b"x")} recovery-update/xImage', f'{md5(ROOTFS)} recovery-update/rootfs.squashfs'),
    (f'{md5(KERNEL)} other', f'{md5(ROOTFS)} recovery-update/rootfs.squashfs'),
    (f'{md5(KERNEL)} recovery-update/xImage', f'{md5(ROOTFS)} recovery-update/xImage'),
])
def test_validate_refuses_wrong_checksum_lines(tmp_path, scripts, line2, line3):
    d = built(tmp_path/'b', info=f'Shanling Q2\n{release.TAG}\n{line2}\n{line3}\n')
    with pytest.raises(ValueError, match='^Update payload mismatch$'):
        release.validate(d)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """release's module globals pointed at a temporary checkout with this version's changelog entry."""
    (tmp_path/'docs').mkdir()
    (tmp_path/'docs/changelog.md').write_text(f'- **{release.VERSION}**: One.\n')
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    monkeypatch.setattr(release, 'source_sha256', lambda: 'source')
    return tmp_path


class Packaging:
    def __init__(self, monkeypatch, sources=('source',) * 2, revisions=('rev',) * 2, status='', repeat=b'tar'):
        self.calls, self.builds, self.validated, self.scripts = [], [], [], []
        sources, revisions = iter(sources), iter(revisions)
        def run(*args):
            self.calls.append(args)
            return status if 'status' in args else next(revisions)
        def builder(stock, directory, logo):
            self.builds.append((stock, directory, logo))
            directory.mkdir()
            (directory/'update.tar').write_bytes(repeat if directory.name.endswith('-repeat') else b'tar')
        def script(args, check):
            self.scripts.append((args, check))
            pathlib.Path(args[2]).write_bytes(b'%PDF')
        monkeypatch.setattr(release, 'run', run)
        monkeypatch.setattr(release, 'build', builder)
        monkeypatch.setattr(release, 'validate', self.validated.append)
        monkeypatch.setattr(subprocess, 'run', script)
        monkeypatch.setattr(release, 'source_sha256', lambda: next(sources))


def test_package(repo, monkeypatch, tmp_path, capsys):
    p = Packaging(monkeypatch)
    out = tmp_path/'nested/release'
    release.package(pathlib.Path('stock.zip'), out)
    assert p.calls[0] == ('git', '-C', repo, 'status', '--porcelain', '--', 'patch', 'tools', 'assets', 'docs', 'test', 'requirements.txt')
    assert p.calls[1] == p.calls[2] == ('git', '-C', repo, 'rev-parse', 'HEAD')
    logo = repo/'assets/boot-logo.jpg'
    assert p.builds == [(pathlib.Path('stock.zip'), out/'build', logo), (pathlib.Path('stock.zip'), out/'build-repeat', logo)]
    assert p.validated == [out/'build']
    assert (out/release.ASSET).read_bytes() == b'tar'
    assert p.scripts == [([release.sys.executable, str(repo/'docs/manual/build.py'), str(out/release.GUIDE)], True)]
    assert (out/'SHA256SUMS').read_text() == f'{sha(b"tar")}  update.tar\n'
    record = json.loads((out/'release.json').read_text())
    assert record == dict(tag=release.VERSION, revision='rev', source_sha256='source', assets={'update.tar': sha(b'tar')},
                          guide_sha256=sha(b'%PDF'))
    assert (out/'release.json').read_text().endswith('}\n') and '\n  "tag"' in (out/'release.json').read_text()
    assert (out/'release-notes.md').read_text() == release.release_body(out, record)
    assert capsys.readouterr().out == f'Validated and reproducible: {out}\n'


def test_package_wants_a_fresh_directory(repo, monkeypatch, tmp_path):
    p = Packaging(monkeypatch)
    with pytest.raises(ValueError, match='^Release output must be a fresh directory$'):
        release.package(pathlib.Path('stock.zip'), tmp_path)
    assert not p.calls


def test_package_wants_committed_inputs(repo, monkeypatch, tmp_path):
    p = Packaging(monkeypatch, status=' M tools/build.py\n')
    with pytest.raises(ValueError, match='^Commit the build inputs before packaging$'):
        release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert not (tmp_path/'out').exists() and not p.builds


def test_package_ignores_blank_status(repo, monkeypatch, tmp_path):
    p = Packaging(monkeypatch, status=' \n')
    release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert p.builds


def test_package_wants_identical_builds(repo, monkeypatch, tmp_path):
    p = Packaging(monkeypatch, repeat=b'other')
    with pytest.raises(ValueError, match='^Non-reproducible update$'):
        release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert not (tmp_path/'out/update.tar').exists()


@pytest.mark.parametrize('kwargs', [dict(sources=('a', 'b')), dict(revisions=('a', 'b'))])
def test_package_wants_a_steady_source(repo, monkeypatch, tmp_path, kwargs):
    Packaging(monkeypatch, **kwargs)
    with pytest.raises(ValueError, match='^Source changed during release build$'):
        release.package(pathlib.Path('stock.zip'), tmp_path/'out')
    assert not (tmp_path/'out/release.json').exists()


def packaged(root):
    out = root/'package'
    (out/'build').mkdir(parents=True)
    (out/'build/update.tar').write_bytes(b'pod')
    (out/release.ASSET).write_bytes(b'pod')
    (out/release.GUIDE).write_bytes(b'%PDF')
    (out/'SHA256SUMS').write_text(f'{sha(b"pod")}  {release.ASSET}\n')
    (out/'release.json').write_text(json.dumps(dict(tag=release.VERSION, revision='rev', source_sha256='source',
                                                    assets={release.ASSET: sha(b'pod')}, guide_sha256=sha(b'%PDF'))))
    return out


class Gh:
    def __init__(self, monkeypatch, out, releases='[]', corrupt=None):
        self.calls, self.out = [], out
        self.releases, self.corrupt = releases, corrupt
        monkeypatch.setattr(release, 'run', self)

    def __call__(self, *args):
        self.calls.append(args)
        if args[3:5] == ('release', 'list') or args[4:5] == ('list',):
            return self.releases
        if 'download' in args:
            target = pathlib.Path(args[-1])
            for name in (release.ASSET, release.GUIDE):
                (target/name).write_bytes(b'bad' if name == self.corrupt else (self.out/name).read_bytes())
        return ''


def test_upload_draft(repo, monkeypatch, capsys):
    out = packaged(repo)
    gh = Gh(monkeypatch, out)
    release.upload(out)
    prefix = ('gh', '--repo', release.REPO, 'release')
    assert gh.calls[0] == (*prefix, 'list', '--limit', '1000', '--json', 'tagName,isDraft')
    assert gh.calls[1] == (*prefix, 'create', release.VERSION, '--draft', '--target', 'rev', '--title', release.VERSION,
                           '--notes-file', out/'release-notes.md')
    assert gh.calls[2] == (*prefix, 'upload', release.VERSION, out/release.ASSET, out/release.GUIDE, '--clobber')
    assert gh.calls[3][:6] == (*prefix, 'download', release.VERSION) and gh.calls[3][6] == '--dir' and len(gh.calls) == 4
    record = json.loads((out/'release.json').read_text())
    assert (out/'release-notes.md').read_text() == release.release_body(out, record)
    assert capsys.readouterr().out == f'Verified draft: https://github.com/{release.REPO}/releases/tag/{release.VERSION}\n'


def test_upload_publish(repo, monkeypatch, capsys):
    out = packaged(repo)
    gh = Gh(monkeypatch, out)
    release.upload(out, publish=True)
    assert gh.calls[-1] == ('gh', '--repo', release.REPO, 'release', 'edit', release.VERSION, '--draft=false')
    assert capsys.readouterr().out.startswith('Published: https://github.com/')


def test_upload_updates_a_draft(repo, monkeypatch):
    out = packaged(repo)
    gh = Gh(monkeypatch, out, json.dumps([{'tagName': 'old', 'isDraft': False}, {'tagName': release.VERSION, 'isDraft': True}]))
    release.upload(out)
    assert gh.calls[1][4] == 'edit'


def test_upload_never_overwrites_a_published_release(repo, monkeypatch):
    out = packaged(repo)
    gh = Gh(monkeypatch, out, json.dumps([{'tagName': release.VERSION, 'isDraft': False}]))
    with pytest.raises(ValueError, match='^Refusing to overwrite a published release$'):
        release.upload(out)
    assert len(gh.calls) == 1


@pytest.mark.parametrize('name', [release.ASSET, release.GUIDE])
def test_upload_checks_what_landed(repo, monkeypatch, name):
    out = packaged(repo)
    gh = Gh(monkeypatch, out, corrupt=name)
    with pytest.raises(ValueError, match=f'^Remote asset mismatch: {name}$'):
        release.upload(out, publish=True)
    assert not any('--draft=false' in c for c in gh.calls)


@pytest.mark.parametrize('change,message', [
    (dict(tag='0.0.0'), '^Wrong release source/tag$'),
    (dict(source_sha256='other'), '^Wrong release source/tag$'),
    (dict(assets={release.ASSET: 'x', 'extra.zip': 'y'}), '^The release is update.tar alone$'),
    (dict(assets={release.ASSET: 'x'}), '^Release update mismatch$'),
    (dict(guide_sha256='x'), '^User guide mismatch$'),
])
def test_upload_checks_the_record_first(repo, monkeypatch, change, message):
    out = packaged(repo)
    record = json.loads((out/'release.json').read_text())
    (out/'release.json').write_text(json.dumps({**record, **change}))
    gh = Gh(monkeypatch, out)
    with pytest.raises(ValueError, match=message):
        release.upload(out)
    assert not gh.calls


def test_upload_wants_the_build_tree_unchanged(repo, monkeypatch):
    out = packaged(repo)
    (out/'build/update.tar').write_bytes(b'other')
    gh = Gh(monkeypatch, out)
    with pytest.raises(ValueError, match='^Release update mismatch$'):
        release.upload(out)
    assert not gh.calls


def test_upload_wants_matching_checksums(repo, monkeypatch):
    out = packaged(repo)
    (out/'SHA256SUMS').write_text('wrong\n')
    gh = Gh(monkeypatch, out)
    with pytest.raises(ValueError, match='^Release checksums mismatch$'):
        release.upload(out)
    assert not gh.calls


def test_release_body(repo):
    assert release.release_body(pathlib.Path('.'), {'assets': {release.ASSET: 'abc'}}) == (
        f'**{release.VERSION}**\n- One.\n\nSHA-256:\n- update.tar ({release.TAG}): `abc`\n')
