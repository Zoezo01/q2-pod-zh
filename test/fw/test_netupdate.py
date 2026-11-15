"""patch/netupdate.c: GitHub's redirects and release JSON, sources.list, unzip's listing, and installing,
removing and checking apps, with the card and the network stubbed; then System Update's online row and the App
Store's jobs end to end, a press to the toast, on a card whose busybox runs on its files."""
import ctypes as C
import fnmatch
import hashlib
import io
import pathlib
import random
import re
import shlex
import zipfile

import pytest

import host
from conftest import O, block

# stock.h's Q2POD_TAG: tools/build.py's VERSION (host.py builds as a release, not --dev)
TAG = host.tools_build.VERSION.encode()
# the next single-digit tag (1.6.9 -> 1.7.0), as netupdate's strcmp orders them
_n = int(TAG.replace(b'.', b'')) + 1
NEWER = b'%d.%d.%d' % (_n // 100, _n // 10 % 10, _n % 10)
OLDER = b'0.9.9'

SH_MAX = 16
SH_CAP = 1 << 30
SH_OK, SH_NET, SH_SPACE, SH_CARD, SH_MISSING, SH_BAD, SH_CANCELLED, SH_BUSY = range(8)
NU_IDLE, NU_RUNNING, NU_DONE, NU_LATEST, NU_NET, NU_MISSING, NU_CARD, NU_CANCELLED, NU_BUSY, NU_SPACE, NU_BAD = range(11)
JOB_CHECK, JOB_INSTALL, JOB_REMOVE, JOB_ERASE = 1, 2, 3, 4
ST_INSTALL, ST_UPDATE, ST_INSTALLED = range(3)
ROW_CHECK = SH_MAX
STAGING = '/mnt/mmc/.q2/staging'
JSON_PATH = b'/tmp/q2-shelf.json'
SHELF = pathlib.Path(__file__).resolve().parents[1]/'shelf'
RELEASE = pathlib.Path(__file__).resolve().parents[1]/'fixtures/q2-pod-release.json'  # GitHub's JSON for 1.8.1, its notes cut


class App(C.Structure):
    _fields_ = [('name', C.c_char * 32), ('id', C.c_char * 16), ('repo', C.c_char * 80), ('dir', C.c_char * 17),
                ('bin', C.c_char * 40), ('latest', C.c_char * 32), ('dropped', C.c_char), ('keep', C.c_char_p)]


class Sh(C.Structure):
    _fields_ = [('thread', C.c_ulong), *((n, C.c_int) for n in ('running', 'cancel', 'percent', 'stage', 'result', 'job', 'app', 'armed', 'rows', 'n')),
                ('map', C.c_int * (SH_MAX + 1)), ('manual', C.c_int), ('need', C.c_uint), ('checked', C.c_ulonglong), ('next', App * SH_MAX),
                ('page', C.c_void_p), ('row', C.c_void_p * (SH_MAX + 1))]


class Nu(C.Structure):
    _fields_ = [('thread', C.c_ulong), ('state', C.c_int), ('cancel', C.c_int), ('percent', C.c_int), ('stage', C.c_int),
                ('base', C.c_int), ('need', C.c_uint), ('label', C.c_void_p)]


def hostile(seed, samples, count=2000, alphabet=b'"\\:/ \n{}[],\x00.'):
    """Random bytes, truncations of the samples, and mutations of them."""
    r = random.Random(seed)
    for _ in range(count):
        yield r.randbytes(r.randrange(200))
    for s in samples:
        for k in sorted(r.sample(range(len(s)), min(len(s), 400))):
            yield s[:k]
    for _ in range(count):
        b = bytearray(r.choice(samples))
        for _ in range(r.randrange(1, 6)):
            b[r.randrange(len(b))] = r.choice(alphabet)
        yield bytes(b)


@pytest.mark.parametrize('location,tag', [
    (b'https://github.com/o/r/releases/tag/1.3.4', b'1.3.4'),
    (b'/releases/tag/x', b'x'),
    (b'https://github.com/o/r/releases/tag/v1/rc', b'rc'),      # what follows the last '/'
    (b'https://github.com/o/r/releases/tag/', None),
    (b'https://github.com/o/r/releases', None), (b'', None), (b'tag', None), (b'/', None),
    (b'https://github.com/o/r/releases/latest', None),
])
def test_the_tag_in_a_redirect(fw, location, tag):
    assert fw.fn('netupdate_tag', C.c_char_p, C.c_char_p)(location) == tag


def test_no_redirect_no_tag(fw):
    assert fw.fn('netupdate_tag', C.c_char_p, C.c_char_p)(None) is None


@pytest.mark.parametrize('name,yes', [(b'rockbox', 1), (b'themes', 1), (b'spotify', 1), (b'q2-rockbox', 0), (b'rock', 0), (b'', 0), (b'deezer', 0)])
def test_q2_pods_own_apps(fw, name, yes):
    assert fw.fn('netupdate__special_id', C.c_int, C.c_char_p)(name) == yes


def test_the_bytes_each_kind_allows(fw):
    only = fw.fn('netupdate__only', C.c_int, C.c_char_p, C.c_uint, C.c_int)
    id_ = set(b'abcdefghijklmnopqrstuvwxyz0123456789-')
    owner = id_ | set(b'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    repo = owner | set(b'._')
    for c in range(1, 256):
        assert only(bytes([c]), 1, 0) == (c in id_), c
        assert only(bytes([c]), 1, 1) == (c in owner), c
        assert only(bytes([c]), 1, 2) == (c in repo), c


def test_only_looks_at_n_bytes_and_none_is_not_enough(fw):
    only = fw.fn('netupdate__only', C.c_int, C.c_char_p, C.c_uint, C.c_int)
    assert only(b'abc!', 3, 0) == 1 and only(b'abc!', 4, 0) == 0 and only(b'abc', 0, 0) == 0


def sources(fw, text, count=SH_MAX):
    """shelf_sources over an exact table of count apps."""
    out = block(fw, size=count * C.sizeof(App))
    n = fw.fn('shelf_sources', C.c_int, C.c_void_p, C.c_void_p, C.c_int)(block(fw, text), out, count)
    table = C.cast(out, C.POINTER(App))
    return [(a.repo, a.name, a.id, a.dir, a.bin, a.keep) for a in table[:n]]


def test_a_card_app_from_its_line(fw):
    assert sources(fw, b'me/q2-foo Foo Bar\n') == [(b'me/q2-foo', b'Foo Bar', b'foo', b'.foo', b'.foo/q2-foo', b'')]


def test_the_id_is_the_repo_without_its_q2(fw):
    assert sources(fw, b'me/plain\n') == [(b'me/plain', b'plain', b'plain', b'.plain', b'.plain/q2-plain', b'')]


def test_q2_pods_own_repos_by_any_owner(fw):
    table = sources(fw, b'fork/q2-rockbox\nfork/q2-rockbox-themes T\nfork/q2-librespot\n')
    assert [(a[2], a[3], a[4]) for a in table] == [(b'rockbox', b'.rockbox', b'.rockbox/rockbox'), (b'themes', b'.rockbox', b''),
                                                   (b'spotify', b'.spotify', b'.spotify/librespot')]
    assert [a[5] for a in table] == [b'.rockbox/config.cfg', b'.rockbox/config.cfg', b"'.spotify/cache/*' .spotify/.cache-size"]


@pytest.mark.parametrize('line', [
    b'', b'   ', b'# comment', b'  # comment', b'#me/q2-foo',
    b'me', b'/q2-x', b'me/', b'/', b'me/q2-x/y', b'm e/q2-x', b'a.b/q2-x', b'me!/q2-x',
    b'me/q2-Foo', b'me/q2-a;b', b"me/q2-a'b", b'me/q2-a$b', b'me/..', b'me/q2-..', b'me/.', b'me/q2-a b\x01c',
    b'me/q2-x bad\x7fname',
    b'a' * 40 + b'/q2-x',                    # GitHub's longest owner is 39
    b'me/q2-' + b'a' * 16,                   # an id is 15 at most
    b'me/q2-x ' + b'n' * 32,                 # a name, 31
    b'me/rockbox', b'me/q2-spotify', b'me/themes',          # another repo's "spotify" isn't Q2 Pod's
    b'me/q2-books', b'me/coverflow', b'me/q2-downpic', b'me/photos', b'me/q2', b'me/sldp', b'me/q2-tidal',
])
def test_a_bad_line_is_passed_over_and_the_next_read(fw, line):
    assert [a[2] for a in sources(fw, line + b'\nme/good\n')] == [b'good']


def test_the_longest_allowed_of_each(fw):
    owner, id_, name = b'a' * 39, b'b' * 15, b'n' * 31
    (repo, shown, got, folder, binary, _), = sources(fw, owner + b'/q2-' + id_ + b' ' + name)
    assert repo == owner + b'/q2-' + id_ and shown == name and got == id_
    assert folder == b'.' + id_ and binary == b'.' + id_ + b'/q2-' + id_


def test_lines_end_and_space_as_they_may(fw):
    table = sources(fw, b'\r\n  \t me/a \t  Name Two \r\nme/b\r\nme/c')
    assert [(a[0], a[1]) for a in table] == [(b'me/a', b'Name Two'), (b'me/b', b'b'), (b'me/c', b'c')]


def test_a_utf8_name_is_kept(fw):
    assert sources(fw, 'me/x Café\n'.encode())[0][1] == 'Café'.encode()


def test_a_repeated_id_keeps_the_first(fw):
    assert [a[0] for a in sources(fw, b'a/q2-x\nb/x\nc/y\n')] == [b'a/q2-x', b'c/y']


def test_two_q2_pod_repos_of_one_id_keep_the_first(fw):
    assert [a[0] for a in sources(fw, b'a/q2-librespot\nb/q2-librespot\n')] == [b'a/q2-librespot']


def test_the_table_is_never_overrun(fw):
    text = b''.join(b'me/app%d\n' % i for i in range(40))
    assert len(sources(fw, text)) == SH_MAX and len(sources(fw, text, 3)) == 3


def test_no_list_is_no_apps(fw):
    assert fw.fn('shelf_sources', C.c_int, C.c_void_p, C.c_void_p, C.c_int)(None, None, SH_MAX) == 0
    assert sources(fw, b'') == []


def test_the_repo_ships_a_list_that_parses(fw):
    lines = [x for x in (SHELF.parent.parent/'sources.list').read_bytes().splitlines() if x.strip() and not x.startswith(b'#')]
    assert len(sources(fw, b'\n'.join(lines))) == len(lines)


def test_hostile_sources(fw):
    samples = [(SHELF.parent.parent/'sources.list').read_bytes(), b'me/q2-foo Foo Bar\n# c\nx/q2-librespot\n\n']
    for text in hostile(31, samples, alphabet=b'/ \t\n\r#.-\x01Q'):
        for a in sources(fw, text.split(b'\0')[0]):
            assert 1 <= len(a[2]) <= 15 and set(a[2]) <= set(b'abcdefghijklmnopqrstuvwxyz0123456789-')
            assert a[3][:1] == b'.' and set(a[0]) <= set(b'abcdefghijklmnopqrstuvwxyz0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ._/')


def json_at(fw, s, key):
    buf = block(fw, s)
    at = fw.fn('netupdate__json_at', C.c_void_p, C.c_void_p, C.c_char_p)(buf, key)
    return C.string_at(at) if at else None


@pytest.mark.parametrize('s,value', [
    (b'{"k":1}', b'1}'), (b'{"k" : "v"}', b'"v"}'), (b'{"k"   :::  2', b'2'), (b'"k"', b''), (b'{}', None), (b'', None),
])
def test_the_value_after_a_key(fw, s, value):
    assert json_at(fw, s, b'"k"') == value


def test_no_json_no_value(fw):
    assert fw.fn('netupdate__json_at', C.c_char_p, C.c_char_p, C.c_char_p)(None, b'"k"') is None


def json_string(fw, s, n=16):
    out = block(fw, size=n)
    ok = fw.fn('netupdate__json_string', C.c_int, C.c_void_p, C.c_void_p, C.c_uint)(block(fw, s), out, n)
    return C.string_at(out) if ok else None


@pytest.mark.parametrize('s,value', [
    (b'"abc"', b'abc'), (b'"abc",x', b'abc'), (b'""', None), (b'"', None), (b'abc', None), (b'', None), (b'"abc', None),
    (b'"a\\"b"', None),      # GitHub's have no escapes; any is refused
])
def test_a_json_string(fw, s, value):
    assert json_string(fw, s) == value


def test_a_json_string_fits_to_the_byte(fw):
    assert json_string(fw, b'"abcd"', 5) == b'abcd' and json_string(fw, b'"abcd"', 4) is None
    assert fw.fn('netupdate__json_string', C.c_int, C.c_void_p, C.c_void_p, C.c_uint)(None, None, 0) == 0


def release(fw, json, tn=32, un=256):
    tag, url, size = block(fw, size=tn), block(fw, size=un), C.c_uint(0)
    fn = fw.fn('shelf_release', C.c_int, C.c_void_p, C.c_void_p, C.c_uint, C.c_void_p, C.c_uint, C.POINTER(C.c_uint))
    ok = fn(block(fw, json), tag, tn, url, un, C.byref(size))
    return (C.string_at(tag), C.string_at(url), size.value) if ok else None


def github(tag='1.0', *assets):
    """GitHub's release JSON, trimmed: each asset (name, size[, digest]), the digest as its JSON value."""
    rows = ','.join('{"url":"u","id":1,"name":"%s","label":"","uploader":{"login":"x","id":2},"content_type":"z","size":%s,%s'
                    '"browser_download_url":"https://h/%s"}' % (n, s, ''.join('"digest":%s,' % d for d in digest), n)
                    for n, s, *digest in assets)
    return ('{"url":"x","assets_url":"y","tag_name":"%s","name":"Release","assets":[%s],"body":"notes"}' % (tag, rows)).encode()


def test_the_first_zip_of_a_release(fw):
    assert release(fw, github('1.0', ('notes.txt', 9), ('a.zip', 5), ('b.zip', 6))) == (b'1.0', b'https://h/a.zip', 5)


def test_the_releases_own_name_is_no_asset(fw):
    assert release(fw, b'{"tag_name":"1","name":"x.zip","assets":[]}') is None


@pytest.mark.parametrize('name', ['.zip', 'a.ZIP', 'a.zip.sig', 'zip', 'azip'])
def test_what_is_no_zip(fw, name):
    assert release(fw, github('1.0', (name, 5))) is None


@pytest.mark.parametrize('json', [
    b'', b'{}', b'{"assets":[]}', b'{"tag_name":"","assets":[]}', github('1.0'), github('a\\"b', ('a.zip', 5)),
    github('1.0', ('a.zip', 0)), github('1.0', ('a.zip', 'x')),
    b'{"tag_name":"1","assets":[{"name":"a.zip","browser_download_url":"https://h"}]}',
    b'{"tag_name":"1","assets":[{"name":"a.zip","size":5}]}',
])
def test_a_release_with_something_missing(fw, json):
    assert release(fw, json) is None


def test_an_asset_name_too_long_is_passed_over(fw):
    assert release(fw, github('1.0', ('a' * 124 + '.zip', 5), ('b.zip', 6)))[1] == b'https://h/b.zip'
    assert release(fw, github('1.0', ('a' * 123 + '.zip', 5), ('b.zip', 6)))[1].endswith(b'a.zip')


def test_tag_and_url_fit_to_the_byte(fw):
    json = github('t' * 31, ('a.zip', 5))
    assert release(fw, json)[0] == b't' * 31
    assert release(fw, json, tn=31) is None
    url = b'https://h/a.zip'
    assert release(fw, github('1', ('a.zip', 5)), un=len(url) + 1)[1] == url
    assert release(fw, github('1', ('a.zip', 5)), un=len(url)) is None


def test_hostile_releases(fw):
    samples = [github('1.0', ('notes', 9), ('a.zip', 5)), *[p.read_bytes()[:3000] for p in sorted(SHELF.glob('*.json'))]]
    for json in hostile(32, samples):
        release(fw, json.split(b'\0')[0])


def test_each_real_release_truncated(fw):
    for path in sorted(SHELF.glob('*.json')):
        data = path.read_bytes()
        for k in range(0, len(data), max(1, len(data) // 200)):
            release(fw, data[:k])


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(data):
    return '"sha256:%s"' % sha(data)


def asset(fw, json, name=b'update.tar'):
    size, out = C.c_uint(0), block(fw, size=65)
    fn = fw.fn('netupdate_asset', C.c_int, C.c_void_p, C.c_char_p, C.POINTER(C.c_uint), C.c_void_p)
    return (size.value, C.string_at(out).decode()) if fn(block(fw, json), name, C.byref(size), out) else None


def test_the_tar_s_size_and_digest_by_its_name(fw):
    json = github('1.0', ('Q2-Pod-User-Guide.pdf', 9, digest(b'guide')), ('update.tar', 5, digest(b'tar')), ('z', 7, digest(b'z')))
    assert asset(fw, json) == (5, sha(b'tar'))
    assert asset(fw, json, b'z') == (7, sha(b'z')) and asset(fw, json, b'update') is None


def test_github_s_own_json_for_a_release(fw):
    assert asset(fw, RELEASE.read_bytes()) == (53964800, 'a58ac483393a733aa3070d5959d888f3eec918dc2daa8571c57585a066d82ac3')


@pytest.mark.parametrize('value', [
    'null', '""', '"sha256:"', '"sha512:%s"' % ('a' * 64), '"sha256:%s"' % ('a' * 63), '"sha256:%s"' % ('a' * 65),
    '"sha256:%s"' % ('A' * 64), '"sha256:%sg"' % ('a' * 63), '"SHA256:%s"' % ('a' * 64), '"%s"' % ('a' * 71), '7',
])
def test_a_digest_that_is_not_64_hex_of_sha256_is_none(fw, value):
    assert asset(fw, github('1.0', ('update.tar', 5, value))) is None


def test_an_asset_without_its_own_digest_or_size_never_takes_the_next_one_s(fw):
    """The first asset's keys end at its browser_download_url: what follows is another file's."""
    other = ('other.bin', 9, digest(b'other'))
    assert asset(fw, github('1.0', ('update.tar', 5), other)) is None
    assert asset(fw, github('1.0', ('update.tar', 5, 'null'), other)) is None
    assert asset(fw, github('1.0', ('update.tar', 5, digest(b'tar')), other).replace(b'"size":5,', b'')) is None


@pytest.mark.parametrize('json', [
    b'', b'{}', b'{"assets":[]}', github('1.0'), github('1.0', ('update.tar', 0, digest(b''))),
    github('1.0', ('update.tar', 'x', digest(b''))), github('1.0', ('Update.tar', 5, digest(b''))),
    github('1.0', ('update.tar.sig', 5, digest(b''))), b'{"name":"update.tar","size":5,"digest":%s}' % digest(b'').encode(),
    b'{"assets":[{"name":"update.tar","size":5,"digest":%s}]}' % digest(b'').encode(),  # cut before its last key
])
def test_a_release_without_the_tar_whole_is_none(fw, json):
    assert asset(fw, json) is None


def test_a_cut_or_hostile_release_is_none_or_the_same_answer(fw):
    whole = RELEASE.read_bytes()
    right = asset(fw, whole)
    for k in range(0, len(whole), 7):
        assert asset(fw, whole[:k]) in (None, right)
    for json in hostile(33, [whole[:3000], github('1.0', ('update.tar', 5, digest(b'tar')))], count=500):
        asset(fw, json.split(b'\0')[0])


SHA_CHUNK = 1 << 14


def sha256(fw, card, path=b'/mnt/mmc/x'):
    out, size = block(fw, size=65), C.c_uint(7)
    ok = fw.fn('netupdate_sha256', C.c_int, C.c_char_p, C.c_void_p, C.POINTER(C.c_uint))(path, out, C.byref(size))
    return ok, C.string_at(out).decode(), size.value


@pytest.mark.parametrize('n', [0, 1, 3, 55, 56, 57, 63, 64, 65, 119, 120, 128, SHA_CHUNK - 64, SHA_CHUNK - 9, SHA_CHUNK - 8, SHA_CHUNK - 1,
                               SHA_CHUNK, SHA_CHUNK + 1, 2 * SHA_CHUNK, 3 * SHA_CHUNK + 55, 100003])
def test_a_file_s_sha256_at_every_edge_of_its_padding(fw, n):
    data = random.Random(n).randbytes(n)
    assert sha256(fw, Card(fw, {b'/mnt/mmc/x': data})) == (1, sha(data), n)


def test_the_known_answers(fw):
    card = Card(fw, {b'/mnt/mmc/x': b'abc', b'/mnt/mmc/y': b'abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq'})
    assert sha256(fw, card)[1] == 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'  # FIPS 180-4's examples
    assert sha256(fw, card, b'/mnt/mmc/y')[1] == '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1'


def test_a_file_that_is_missing_or_fails_to_read_has_no_sha256(fw):
    card = Card(fw, {b'/mnt/mmc/x': b'abc'})
    assert sha256(fw, card, b'/mnt/mmc/none')[0] == 0
    fw.mock('fw_ferror', lambda *_: 1)
    assert sha256(fw, card)[0] == 0


def row(size, name):
    return f'{size:9}  10-06-2026 13:18   {name}\n'.encode()


HEAD = b'  Length      Date    Time    Name\n---------  ---------- -----   ----\n'
FOOT = b'---------                     -------\n 19786468                     3 files\n'


def unpacked(fw, listing, cap=SH_CAP):
    total = C.c_uint(77)
    n = fw.fn('shelf_unpacked', C.c_int, C.c_void_p, C.c_uint, C.POINTER(C.c_uint))(block(fw, listing), cap, C.byref(total))
    return (n, total.value) if n >= 0 else None


def test_entries_and_their_bytes(fw):
    assert unpacked(fw, HEAD + row(100, '.a/b') + row(20, '.a/c d') + row(0, '.a/') + FOOT) == (3, 120)


def test_nothing_listed(fw):
    assert unpacked(fw, b'') == (0, 0) and unpacked(fw, HEAD + FOOT) == (0, 0)


def test_the_last_line_needs_no_newline(fw):
    assert unpacked(fw, row(5, '.a/b').rstrip(b'\n')) == (1, 5)


def test_the_cap_is_a_most_not_a_less_than(fw):
    assert unpacked(fw, row(10, 'a') + row(5, 'b'), 15) == (2, 15)
    assert unpacked(fw, row(10, 'a') + row(6, 'b'), 15) is None


def test_a_huge_entry_is_past_the_cap(fw):
    assert unpacked(fw, row(4000000000, 'a')) is None


@pytest.mark.parametrize('name', [
    '/etc/passwd', '/', '..', '../x', 'a/..', 'a/../b', 'a/b/../../c', '.spotify/..', '.a/./../b',
])
def test_a_name_leaving_the_card_refuses_the_whole_zip(fw, name):
    assert unpacked(fw, HEAD + row(1, '.a/ok') + row(1, name) + FOOT) is None


@pytest.mark.parametrize('name', ['..a', 'a..', '...', 'a/...', 'a/..b/c', 'a/b..', '.', './x', '. .', '.. ', ' ..', 'a b/c d'])
def test_names_that_only_look_it(fw, name):
    assert unpacked(fw, row(1, name)) == (1, 1)


@pytest.mark.parametrize('line', [
    b'garbage\n', b'123\n', b'123  \n', b'123  x 2026\n', b'123   10-06-2026 13:18   a\n',
    b'   100  10-06-2026 13:18   \n',     # no name
])
def test_lines_that_are_no_entry(fw, line):
    assert unpacked(fw, line) == (0, 0)


def test_hostile_listings(fw):
    samples = [HEAD + row(19783496, '.spotify/librespot') + row(2149, '.spotify/../run') + FOOT, row(1, 'a'), row(10, '/x') + row(9, '..')]
    for listing in hostile(33, samples, alphabet=b' 0123./\n\r'):
        got = unpacked(fw, listing.split(b'\0')[0])
        assert got is None or 0 <= got[1] <= SH_CAP


def listed(fw, lines, path, n=None):
    return fw.fn('shelf_listed', C.c_int, C.c_void_p, C.c_uint, C.c_char_p)(block(fw, lines), len(lines) if n is None else n, path)


def test_a_path_in_a_list_of_lines(fw):
    lines = b'.a/b\0.a/c d\0.e\0'
    assert listed(fw, lines, b'.a/b') == 1 and listed(fw, lines, b'.a/c d') == 1 and listed(fw, lines, b'.e') == 1
    assert listed(fw, lines, b'.a') == 0 and listed(fw, lines, b'.a/c') == 0 and listed(fw, lines, b'') == 0


def test_a_last_line_without_its_nul_is_read_to_the_end(fw):
    assert listed(fw, b'.a\0.b', b'.b') == 1  # read_all's NUL ends it


def test_a_blank_line_matches_an_empty_path_only(fw):
    assert listed(fw, b'\0.a\0', b'') == 1 and listed(fw, b'\0.a\0', b'.a') == 1


def test_lines_past_n_are_not_looked_at(fw):
    assert listed(fw, b'.a\0.b\0', b'.b', 3) == 0
    assert listed(fw, b'', b'.a') == 0


def test_no_list_is_no_match(fw):
    assert fw.fn('shelf_listed', C.c_int, C.c_void_p, C.c_uint, C.c_char_p)(None, 0, b'.a') == 0


def apps(fw, *lines):
    """The Apps page's list, as shelf_sources filled it."""
    table = (App * SH_MAX).in_dll(fw.lib, 'shelf_apps')
    n = fw.fn('shelf_sources', C.c_int, C.c_char_p, C.c_void_p, C.c_int)(b'\n'.join(lines), table, SH_MAX)
    fw.var('shelf_n', C.c_int).value = n
    return table


def test_a_staged_path_and_an_app_file(fw):
    apps(fw, b'me/foo')
    out = block(fw, size=96)
    fw.fn('netupdate__staged', None, C.c_void_p, C.c_uint, C.c_int, C.c_char_p)(out, 96, 0, b'.part')
    assert C.string_at(out).decode() == STAGING + '/foo.part'
    fw.fn('netupdate__app_file', None, C.c_void_p, C.c_uint, C.c_int, C.c_char_p)(out, 96, 0, b'.tag')
    assert C.string_at(out) == b'/mnt/mmc/.q2/apps/foo.tag'


def test_a_staged_path_is_cut_to_its_buffer(fw):
    apps(fw, b'me/foo')
    out = block(fw, size=8)
    fw.fn('netupdate__staged', None, C.c_void_p, C.c_uint, C.c_int, C.c_char_p)(out, 8, 0, b'')
    assert C.string_at(out) == b'/mnt/mm'


def test_every_card_path_fits_the_longest_id(fw):
    apps(fw, b'me/q2-' + b'a' * 15)
    out = block(fw, size=96)
    fw.fn('netupdate__staged', None, C.c_void_p, C.c_uint, C.c_int, C.c_char_p)(out, 96, 0, b'.files')
    assert C.string_at(out).decode().endswith('a' * 15 + '.files')


def test_progress_is_percent_of_kilobytes(fw):
    progress = fw.fn('netupdate__progress', C.c_int, C.c_void_p, C.c_longlong, C.c_longlong, C.c_longlong, C.c_longlong)
    x = (C.c_int * 2)(0, -1)
    assert progress(x, 1023, 500, 0, 0) == 0 and x[1] == -1      # no length yet
    assert progress(x, 1024, 512, 0, 0) == 0 and x[1] == 0
    assert progress(x, 200 << 20, 50 << 20, 0, 0) == 0 and x[1] == 25
    assert progress(x, 200 << 20, 200 << 20, 0, 0) == 0 and x[1] == 100
    x[0] = 1
    assert progress(x, 0, 0, 0, 0) == 1


def test_leaving_the_page_cancels_its_own_download(fw):
    gone = fw.fn('netupdate__label_gone', C.c_int, C.c_void_p, C.c_void_p)
    nu = fw.var('netupdate__nu', Nu)
    nu.label = 5
    assert gone(6, None) == 0 and nu.label == 5 and nu.cancel == 0
    assert gone(5, None) == 0 and not nu.label and nu.cancel == 2  # not a press's cancel: the part stays
    nu.label, nu.cancel = 5, 1
    assert gone(5, None) == 0 and nu.cancel == 1  # a press cancelled first: its part goes


def test_an_app_by_id(fw):
    apps(fw, b'me/foo', b'me/bar')
    is_ = fw.fn('netupdate__is', C.c_int, C.c_int, C.c_char_p)
    find = fw.fn('netupdate__sh_find', C.c_int, C.c_char_p)
    assert is_(1, b'bar') == 1 and is_(0, b'bar') == 0 and is_(-1, b'foo') == 0 and is_(2, b'') == 0 and is_(SH_MAX, b'') == 0
    assert find(b'bar') == 1 and find(b'foo') == 0 and find(b'baz') == -1 and find(b'') == -1


def test_the_folder_a_job_is_replacing(fw):
    apps(fw, b'me/foo', b'me/bar')
    sh = fw.var('netupdate__sh', Sh)
    busy = fw.fn('shelf_busy', C.c_char_p)
    assert busy() is None
    sh.running, sh.job, sh.app = 1, JOB_INSTALL, 1
    assert busy() == b'.bar'
    sh.job = JOB_REMOVE
    assert busy() == b'.bar'
    for job in (JOB_CHECK, JOB_ERASE):
        sh.job = job
        assert busy() is None
    sh.job, sh.running = JOB_INSTALL, 0
    assert busy() is None
    moving = fw.fn('shelf_moving', C.c_char_p)  # once its files move: a remove's whole run, an install's stage 3
    for job, stage, folder in ((JOB_INSTALL, 1, None), (JOB_INSTALL, 2, None), (JOB_INSTALL, 3, b'.bar'),
                               (JOB_REMOVE, 4, b'.bar'), (JOB_CHECK, 4, None), (JOB_ERASE, 4, None)):
        sh.running, sh.job, sh.stage = 1, job, stage
        assert moving() == folder
    sh.running = 0
    assert moving() is None


def test_closing_the_page_forgets_its_rows_only(fw):
    sh = fw.var('netupdate__sh', Sh)
    sh.page, sh.rows, sh.armed, sh.job = 9, 3, 2, JOB_INSTALL
    assert fw.fn('netupdate__sh_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
    assert not sh.page and sh.rows == 0 and sh.armed == 0 and sh.job == JOB_INSTALL


class Card:
    """fopen, fread, fseek, ftell, fclose, __xstat, mkdir, rename, unlink, rmdir and system over a dict of files."""
    def __init__(self, fw, files=None, dirs=()):
        self.files, self.dirs, self.log, self.at, self.names = dict(files or {}), set(dirs), [], {}, {}
        self.written, self.system = {}, lambda cmd: 0
        def fopen(path, mode, *_):
            name, how = C.string_at(path), C.string_at(mode)
            if b'r' in how and name not in self.files:
                return 0
            f = len(self.names) + 1
            self.names[f], self.at[f] = name, 0
            if b'w' in how:
                self.written[name] = b''
            return f
        def fread(buf, size, n, f, *_):
            piece = self.files[self.names[f]][self.at[f]:self.at[f] + size * n]
            C.memmove(buf, piece, len(piece))
            self.at[f] += len(piece)
            return len(piece) // size
        def fseek(f, at, whence, *_):
            self.at[f] = len(self.files[self.names[f]]) if whence == 2 else at
        def fwrite(buf, size, n, f, *_):
            self.written[self.names[f]] += C.string_at(buf, size * n)
            return n
        def rename(a, b, *_):
            a, b = C.string_at(a), C.string_at(b)
            self.log.append(('rename', a, b))
            if a in self.written and a not in self.files:
                self.files[a] = self.written[a]
            self.files[b] = self.files.pop(a, b'')
        def unlink(p, *_):
            self.log.append(('unlink', C.string_at(p)))
            self.files.pop(C.string_at(p), None)
        def mkdir(p, mode, *_):
            self.log.append(('mkdir', C.string_at(p)))
        def rmdir(p, *_):
            name = C.string_at(p)
            self.log.append(('rmdir', name))
            if any(f.startswith(name + b'/') for f in self.files):
                return -1
            self.dirs.discard(name)
        def system(cmd, *_):
            self.log.append(('system', C.string_at(cmd)))
            return self.system(C.string_at(cmd))
        def stat(version, path, info, *_):
            name = C.string_at(path)
            if name not in self.files:
                return -1
            C.cast(info, C.POINTER(C.c_uint))[5] = 0o100644
        for stub, fn in (('fw_fopen', fopen), ('fw_fread', fread), ('fw_fseek', fseek), ('fw_fwrite', fwrite), ('fw_rename', rename),
                         ('fw_unlink', unlink), ('fw_mkdir', mkdir), ('fw_rmdir', rmdir), ('fw_system', system), ('fw___xstat', stat)):
            fw.mock(stub, fn)
        fw.mock('fw_ftell', lambda f, *_: self.at[f])
        fw.mock('fw_access', lambda p, *_: 0 if C.string_at(p) in self.files else -1)

    def of(self, kind):
        return [e[1:] for e in self.log if e[0] == kind]


def test_a_file_of_lines_is_nul_ended_in_place(fw):
    Card(fw, {b'/l': b'a\nb c\n\nd'})
    n = C.c_uint(0)
    p = fw.fn('netupdate__lines', C.c_void_p, C.c_char_p, C.POINTER(C.c_uint))(b'/l', C.byref(n))
    assert n.value == 8 and C.string_at(p, 8) == b'a\0b c\0\0d'


def test_a_missing_file_of_lines_is_none(fw):
    Card(fw)
    n = C.c_uint(9)
    assert fw.fn('netupdate__lines', C.c_void_p, C.c_char_p, C.POINTER(C.c_uint))(b'/l', C.byref(n)) is None and n.value == 0


def test_a_file_of_lines_over_4mb_is_refused(fw):
    Card(fw, {b'/l': b'a' * ((4 << 20) + 1)})
    n = C.c_uint(9)
    assert fw.fn('netupdate__lines', C.c_void_p, C.c_char_p, C.POINTER(C.c_uint))(b'/l', C.byref(n)) is None


def move(fw, rel):
    return fw.fn('netupdate__move', C.c_int, C.c_int, C.c_char_p)(0, rel)


def test_a_file_moves_with_its_folders_made(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {f'{STAGING}/foo/.foo/a/b.bin'.encode(): b'x'})
    assert move(fw, b'.foo/a/b.bin') == 0
    assert card.log == [('mkdir', b'/mnt/mmc/.foo'), ('mkdir', b'/mnt/mmc/.foo/a'),
                        ('rename', f'{STAGING}/foo/.foo/a/b.bin'.encode(), b'/mnt/mmc/.foo/a/b.bin')]


def test_a_path_too_long_moves_nothing(fw):
    apps(fw, b'me/foo')
    card = Card(fw)
    assert move(fw, b'a' * 600) == 1 and card.log == []


def test_the_longest_path_fits_exactly(fw):
    apps(fw, b'me/foo')
    Card(fw)
    room = 511 - len(f'{STAGING}/foo/')
    assert move(fw, b'a' * room) == 0 and move(fw, b'a' * (room + 1)) == 1


def place(fw):
    return fw.fn('shelf_place', C.c_int, C.c_int)(0)


def staged_list(*names):
    return ''.join(name + '\n' for name in names).encode()


def test_files_are_placed_with_the_binary_last_then_listed(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {f'{STAGING}/foo.files'.encode(): staged_list('.foo/q2-foo', '.foo/a', '.foo/b')})
    assert place(fw) == SH_OK
    moved = [e[0].rsplit(b'/')[-1] for e in card.of('rename')]
    assert moved == [b'a', b'b', b'q2-foo', b'foo.files']
    assert card.of('rename')[-1] == (f'{STAGING}/foo.files'.encode(), b'/mnt/mmc/.q2/apps/foo.files')
    assert card.of('system')[-1] == (b'sync',)


def test_a_failed_move_keeps_the_old_binary(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {f'{STAGING}/foo.files'.encode(): staged_list('.foo/q2-foo', '.foo/a')})
    tried = []
    def rename(a, b, *_):
        tried.append(C.string_at(a))
        return -1 if tried[-1].endswith(b'/.foo/a') else 0
    fw.mock('fw_rename', rename)
    assert place(fw) == SH_CARD
    assert not [p for p in tried if p.endswith(b'q2-foo')]


def test_nothing_to_place_is_a_failure(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {f'{STAGING}/foo.files'.encode(): b''})
    assert place(fw) == SH_CARD


def test_a_failed_find_places_nothing(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {f'{STAGING}/foo.files'.encode(): staged_list('.foo/a')})
    card.system = lambda cmd: 1 if b'find' in cmd else 0
    assert place(fw) == SH_CARD and not card.of('rename')


def remove(fw, i):
    fw.fn('shelf_remove', None, C.c_int)(i)


def test_a_card_app_goes_whole_with_its_list_and_tag(fw):
    apps(fw, b'me/foo')
    files = {b'/mnt/mmc/.q2/apps/foo.files': b'.foo/q2-foo\n.foo/a\n', b'/mnt/mmc/.foo/q2-foo': b'', b'/mnt/mmc/.foo/a': b'',
             b'/mnt/mmc/.q2/apps/foo.tag': b'1.0'}
    card = Card(fw, files)
    remove(fw, 0)
    assert (b"rm -rf '/mnt/mmc/.foo'",) in card.of('system')
    assert card.of('unlink')[0] == (b'/mnt/mmc/.foo/q2-foo',)  # first: a part remove still boots Q2 Pod
    assert (b'/mnt/mmc/.q2/apps/foo.files',) in card.of('unlink') and (b'/mnt/mmc/.q2/apps/foo.tag',) in card.of('unlink')


def test_a_file_two_apps_list_stays_and_q2_pods_own_folder_is_never_deleted_whole(fw):
    apps(fw, b'x/q2-rockbox', b'x/q2-rockbox-themes')
    files = {b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/rockbox\n.rockbox/fonts/a.fnt\n',
             b'/mnt/mmc/.q2/apps/themes.files': b'.rockbox/fonts/a.fnt\n.rockbox/wps/b\n',
             b'/mnt/mmc/.rockbox/fonts/a.fnt': b'', b'/mnt/mmc/.rockbox/wps/b': b'', b'/mnt/mmc/.rockbox/rockbox': b''}
    card = Card(fw, files)
    remove(fw, 1)
    assert (b'/mnt/mmc/.rockbox/wps/b',) in card.of('unlink') and (b'/mnt/mmc/.rockbox/fonts/a.fnt',) not in card.of('unlink')
    assert not [c for c in card.of('system') if c[0].startswith(b'rm')]


def test_empty_folders_go_up_to_the_cards_root_and_no_further(fw):
    apps(fw, b'x/q2-rockbox')
    card = Card(fw, {b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/a/b/c\n'})
    remove(fw, 0)
    assert card.of('rmdir') == [(b'/mnt/mmc/.rockbox/a/b',), (b'/mnt/mmc/.rockbox/a',), (b'/mnt/mmc/.rockbox',)]


def test_a_folder_left_with_files_stops_the_climb(fw):
    apps(fw, b'x/q2-rockbox')
    card = Card(fw, {b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/a/b/c\n', b'/mnt/mmc/.rockbox/a/keep': b''})
    remove(fw, 0)
    assert card.of('rmdir') == [(b'/mnt/mmc/.rockbox/a/b',), (b'/mnt/mmc/.rockbox/a',)]


def test_a_remove_with_no_list_still_goes(fw):
    apps(fw, b'me/foo')
    card = Card(fw)
    remove(fw, 0)
    assert (b"rm -rf '/mnt/mmc/.foo'",) in card.of('system')


def test_unzip_is_listed_then_checked(fw):
    apps(fw, b'me/foo')
    listing = f'{STAGING}/foo.list'.encode()
    card = Card(fw, {listing: row(5, '.foo/a') + row(6, '.foo/b')})
    total = C.c_uint(0)
    assert fw.fn('shelf_list', C.c_int, C.c_int, C.POINTER(C.c_uint))(0, C.byref(total)) == SH_OK and total.value == 11
    assert card.of('system') == [(f"busybox unzip -l -qq {STAGING}/foo.part '.foo/*' -x '*/._*'  > {STAGING}/foo.list".encode(),)]
    assert card.of('unlink') == [(listing,)]


@pytest.mark.parametrize('listing', [b'', row(1, '../x'), HEAD + FOOT])
def test_a_zip_with_nothing_safe_is_bad(fw, listing):
    apps(fw, b'me/foo')
    Card(fw, {f'{STAGING}/foo.list'.encode(): listing})
    total = C.c_uint(0)
    assert fw.fn('shelf_list', C.c_int, C.c_int, C.POINTER(C.c_uint))(0, C.byref(total)) == SH_BAD


def test_a_failed_listing_is_bad(fw):
    apps(fw, b'me/foo')
    card = Card(fw)
    card.system = lambda cmd: 1
    total = C.c_uint(0)
    assert fw.fn('shelf_list', C.c_int, C.c_int, C.POINTER(C.c_uint))(0, C.byref(total)) == SH_BAD


def test_unpacking_keeps_a_special_apps_kept_files(fw):
    apps(fw, b'x/q2-librespot')
    card = Card(fw)
    assert fw.fn('shelf_unpack', C.c_int, C.c_int)(0) == SH_OK
    cmd = card.of('system')[0][0].decode()
    assert cmd == f"busybox unzip -o -qq -d {STAGING}/spotify {STAGING}/spotify.part '.spotify/*' -x '*/._*' '.spotify/cache/*' .spotify/.cache-size"


def test_a_failed_unpack_is_a_card_error(fw):
    apps(fw, b'me/foo')
    Card(fw).system = lambda cmd: 1
    assert fw.fn('shelf_unpack', C.c_int, C.c_int)(0) == SH_CARD


class Net:
    """libcurl: each transfer answered by the first of its URL's replies: (result, code, location[, body]), or a list
    of them, one a transfer and the last from then on. One carried on from a byte (RESUME_FROM) gets the body from
    there as a 206, unless the reply's fifth is 'whole': a server that ignores the range. A body goes
    through the transfer's own write callback in CHUNKS pieces, its progress callback told after each and during(k)
    run then, as a UI timer might: a short write ends it as curl's CURLE_WRITE_ERROR (23), a progress callback's
    non-zero as CURLE_ABORTED_BY_CALLBACK (42). only: the URLs' start whose transfers run during."""
    def __init__(self, fw, replies, during=None, only=b''):
        self.replies, self.urls, self.opts, self.during, self.only = replies, [], {}, during, only
        self.froms = []  # each transfer's RESUME_FROM, 0 without
        self.location = None
        def setopt(c, opt, value, *_):
            self.opts[opt] = value
        def perform(c, *_):
            url = C.string_at(self.opts[10002])
            self.urls.append(url)
            reply = next(v for k, v in self.replies.items() if url.startswith(k))
            if isinstance(reply, list):
                reply = reply.pop(0) if len(reply) > 1 else reply[0]
            self.result, self.code, location, *body = reply
            self.froms.append(self.opts.get(21, 0))
            if body and self.froms[-1] and body[1:] != ['whole']:
                body, self.code = [body[0][self.froms[-1]:]], 206 if self.code == 200 else self.code
            self.location = C.create_string_buffer(location) if location else None
            return self.transfer(body[0]) if body and 20011 in self.opts else self.result
        def info(c, what, out, *_):
            if what == 0x200002:
                C.cast(out, C.POINTER(C.c_long))[0] = self.code
            elif what == 0x10001f:
                C.cast(out, C.POINTER(C.c_void_p))[0] = C.addressof(self.location) if self.location else 0
        fw.mock('fw_curl_easy_init', lambda *_: self.opts.clear() or 1)
        fw.mock('fw_curl_easy_setopt', setopt)
        fw.mock('fw_curl_easy_perform', perform)
        fw.mock('fw_curl_easy_getinfo', info)

    def transfer(self, body):
        write = C.CFUNCTYPE(C.c_uint, C.c_void_p, C.c_uint, C.c_uint, C.c_void_p)(self.opts[20011])  # WRITEFUNCTION
        progress = C.CFUNCTYPE(C.c_int, C.c_void_p, *[C.c_longlong] * 4)(self.opts[20219]) if 20219 in self.opts else None
        step = max(1, -(-len(body) // CHUNKS))
        for k, at in enumerate(range(0, len(body), step)):
            piece = body[at:at + step]
            if write(C.create_string_buffer(piece, len(piece)), 1, len(piece), self.opts[10001]) != len(piece):
                return 23
            if progress and progress(self.opts[10057], len(body), at + len(piece), 0, 0):
                return 42
            if self.during and self.urls[-1].startswith(self.only):
                self.during(k)
        return self.result


CHUNKS = 4


LATEST = b'https://github.com/owner/repo/releases/latest'
API = b'https://api.github.com/repos/owner/repo/releases/tags/'
GET = b'https://github.com/owner/repo/releases/download/'
ASSET = GET + NEWER + b'/update.tar'
TAG_URL = b'https://github.com/owner/repo/releases/tag/'
TAR = b'/mnt/mmc/update.tar'
PART = TAR + b'.part'
NU_JSON = b'/tmp/q2-update.json'
BODY = bytes(range(256)) * 1024  # 256 KB: CHUNKS pieces of 64 KB, KB-exact so progress reads 25/50/75/100
RESERVE = 64 << 20


def tar_json(tar=BODY, tag=NEWER, size=None):
    """GitHub's record of a release: the guide, then update.tar with its size and digest."""
    return github(tag.decode(), ('Q2-Pod-User-Guide.pdf', 9, digest(b'guide')), ('update.tar', len(tar) if size is None else size, digest(tar)))


def replies(tar=BODY, tag=NEWER, latest=None, api=None, asset=None):
    """An update's three transfers: the redirect that names the tag, that tag's release and its update.tar."""
    return {GET + tag + b'/update.tar': asset or (0, 200, None, tar), API + tag: api or (0, 200, None, tar_json(tar, tag)),
            LATEST: latest or (0, 302, TAG_URL + tag)}


def slot(fw, files=None, free=1 << 30, mounted=True):
    """A Disk as the Q2's card slot: in (card_mounted reads /proc/mounts) unless not mounted, with free bytes on it."""
    card = Disk(fw, {**({b'/proc/mounts': b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'} if mounted else {}), **(files or {})})
    def statfs(path, buf, *_):
        words = C.cast(buf, C.POINTER(C.c_uint))
        words[1], words[7] = 4096, free // 4096
    fw.mock('fw_statfs', statfs)
    return card


def update(fw, card=None, during=None, only=GET, **kind):
    net = Net(fw, replies(**kind), during=during, only=only)
    card = card or slot(fw)
    nu = fw.var('netupdate__nu', Nu)
    fw.fn('netupdate__worker', C.c_void_p, C.c_void_p)(None)
    return nu.state, net, card


def test_a_newer_tag_is_checked_against_its_release_and_renamed_once_whole(fw):
    state, net, card = update(fw, card=slot(fw, {TAR: b'old'}))
    assert state == NU_DONE and net.urls == [LATEST, API + NEWER, ASSET]
    assert card.of('rename') == [(PART, TAR)] and card.files[TAR] == BODY and NU_JSON not in card.files


@pytest.mark.parametrize('tag', [TAG, OLDER])
def test_nothing_older_or_equal_is_ever_flashed(fw, tag):
    state, net, _ = update(fw, tag=tag)
    assert state == NU_LATEST and net.urls == [LATEST]


@pytest.mark.parametrize('code,location', [(200, None), (302, b'https://github.com/owner/repo/releases'), (302, b''), (404, None)])
def test_no_tag_is_no_network(fw, code, location):
    assert update(fw, latest=(0, code, location))[0] == NU_NET


def test_a_long_redirect_is_held_to_the_tag_buffer(fw):
    state, net, _ = update(fw, tag=b'9' * 63, latest=(0, 302, TAG_URL + b'9' * 201))
    assert state == NU_DONE and net.urls[1:] == [API + b'9' * 63, GET + b'9' * 63 + b'/update.tar']


@pytest.mark.refusal('update:hash')
def test_one_wrong_byte_is_never_named_update_tar(fw):
    """The tar as the card gives it back isn't the one the release's digest names: it goes, and the old one stays."""
    wrong = BODY[:1000] + b'\xff' + BODY[1001:]
    state, _, card = update(fw, card=slot(fw, {TAR: b'old'}), asset=(0, 200, None, wrong))
    assert state == NU_BAD and card.written[PART] == wrong
    assert card.files[TAR] == b'old' and PART not in card.files and not card.of('rename')


@pytest.mark.parametrize('size', [len(BODY) - 1, len(BODY) + 1])
@pytest.mark.refusal('update:changed')
def test_a_tar_of_another_size_than_the_release_says_is_bad(fw, size):
    state, _, card = update(fw, api=(0, 200, None, tar_json(size=size)))
    assert state == NU_BAD and TAR not in card.files and PART not in card.files


@pytest.mark.parametrize('json', [
    github(NEWER.decode()), github(NEWER.decode(), ('update.tar', len(BODY))), github(NEWER.decode(), ('update.tar', len(BODY), 'null')),
    github(NEWER.decode(), ('update.zip', len(BODY), digest(BODY))), b'{}',
])
def test_a_release_that_doesnt_say_what_its_tar_is_downloads_nothing(fw, json):
    state, net, card = update(fw, api=(0, 200, None, json))
    assert state == NU_MISSING and net.urls == [LATEST, API + NEWER] and PART not in card.written


@pytest.mark.parametrize('reply,state', [
    ((0, 403, None, b'{}'), NU_BUSY), ((0, 429, None, b'{}'), NU_BUSY), ((0, 404, None, b'{}'), NU_MISSING),
    ((0, 500, None, b'{}'), NU_NET), ((7, 0, None), NU_NET),
])
def test_githubs_refusals_of_the_release(fw, reply, state):
    got, net, card = update(fw, api=reply)
    assert got == state and net.urls == [LATEST, API + NEWER] and PART not in card.written
    assert card.of('unlink') == [(NU_JSON,)]  # its own temp file, not the App Store's


def test_no_card_downloads_nothing(fw):
    state, net, card = update(fw, card=slot(fw, mounted=False))
    assert state == NU_CARD and net.urls == [LATEST, API + NEWER] and PART not in card.written


def test_room_for_the_tar_and_its_unpacking_or_nothing_is_downloaded(fw):
    need = 2 * len(BODY) + RESERVE
    state, net, card = update(fw, card=slot(fw, free=need - 4096))
    assert state == NU_SPACE and fw.var('netupdate__nu', Nu).need == 1
    assert net.urls == [LATEST, API + NEWER] and PART not in card.written
    assert update(fw, card=slot(fw, free=need))[0] == NU_DONE


def test_a_resumed_press_needs_room_for_the_rest_only(fw):
    """The part's bytes are already out of the card's free room: asking 2 x size again refused a press on cards the first one fit."""
    have = 16 * 4096
    need = 2 * len(BODY) - have + RESERVE
    state, net, _ = update(fw, card=slot(fw, {PART: BODY[:have]}, free=need - 4096))
    assert state == NU_SPACE and fw.var('netupdate__nu', Nu).need == 1 and net.urls == [LATEST, API + NEWER]
    card = slot(fw, {PART: BODY[:have]}, free=need)
    assert update(fw, card=card)[0] == NU_DONE and card.files[TAR] == BODY
    # a part that isn't this release's (longer than the tar) counts for nothing
    assert update(fw, card=slot(fw, {PART: BODY + b'x'}, free=need))[0] == NU_SPACE


HALF = len(BODY) // 2


def test_a_part_left_on_the_card_is_carried_on_from(fw):
    """What a lost connection, a power cut or leaving the page left: only the rest is asked for."""
    card = slot(fw, {TAR: b'old', PART: BODY[:70000]})
    state, net, _ = update(fw, card=card)
    assert state == NU_DONE and net.urls == [LATEST, API + NEWER, ASSET] and net.froms == [0, 0, 70000]
    assert card.files[TAR] == BODY and PART not in card.files


def test_a_part_already_whole_is_only_read_back(fw):
    card = slot(fw, {PART: BODY})
    state, net, _ = update(fw, card=card)
    assert state == NU_DONE and net.urls == [LATEST, API + NEWER] and card.files[TAR] == BODY


def test_a_part_longer_than_the_release_is_started_again(fw):
    card = slot(fw, {PART: BODY + b'more'})
    state, net, _ = update(fw, card=card)
    assert state == NU_DONE and net.froms == [0, 0, 0] and card.files[TAR] == BODY


def test_a_part_of_another_release_is_found_damaged_and_the_next_press_starts_again(fw):
    card = slot(fw, {TAR: b'old', PART: b'x' * 70000})
    state, net, _ = update(fw, card=card)
    assert state == NU_BAD and net.froms[-1] == 70000 and card.files[TAR] == b'old' and PART not in card.files
    state, net, _ = update(fw, card=card)
    assert state == NU_DONE and net.froms[-1] == 0 and card.files[TAR] == BODY


def test_a_connection_lost_twice_is_carried_on_each_time(fw):
    slept = []
    fw.mock('fw_usleep', lambda us, *_: slept.append(us))
    asset = [(18, 200, None, BODY[:50000]), (28, 200, None, BODY[:120000]), (0, 200, None, BODY)]
    state, net, card = update(fw, asset=asset)
    assert state == NU_DONE and net.urls[2:] == [ASSET] * 3 and net.froms[2:] == [0, 50000, 120000]
    assert card.files[TAR] == BODY and sum(slept) == 2 * 5_000_000  # 5 s before each new try
    assert [e for e in card.log if e[0] == 'fsync'][:2] == [('fsync', PART)] * 2  # a part cut short is on the card too


def test_tries_that_add_nothing_end_the_press_and_keep_the_part(fw):
    card = slot(fw, {TAR: b'old'})
    state, net, _ = update(fw, card=card, asset=[(18, 200, None, BODY[:50000]), (7, 0, None)])
    assert state == NU_NET and net.urls[2:] == [ASSET] * 4  # the first added bytes, so three more
    assert card.files[PART] == BODY[:50000] and card.files[TAR] == b'old' and not card.of('rename')


def test_an_http_error_on_a_resumed_try_keeps_the_part(fw):
    """curl's 22 (FAILONERROR) writes no body: the part is carried on from after the pause."""
    asset = [(18, 200, None, BODY[:50000]), (22, 503, None), (0, 200, None, BODY)]
    state, net, card = update(fw, asset=asset)
    assert state == NU_DONE and net.froms[2:] == [0, 50000, 50000] and card.files[TAR] == BODY
    assert (PART,) not in card.of('unlink')


@pytest.mark.parametrize('ignored', [(33, 200, None), (0, 200, None, BODY, 'whole'), (0, 416, None, b'')])
def test_a_reply_that_isnt_the_range_asked_for_starts_the_part_again(fw, ignored):
    """curl's own refusal (CURLE_RANGE_ERROR), or a reply that came without it and isn't a 206."""
    state, net, card = update(fw, asset=[(18, 200, None, BODY[:50000]), ignored, (0, 200, None, BODY)])
    assert state == NU_DONE and net.froms[2:] == [0, 50000, 0] and card.files[TAR] == BODY
    assert (PART,) in card.of('unlink')


@pytest.mark.parametrize('cancel, kept', [(1, False), (2, True)])
def test_a_cancel_between_tries_ends_the_wait(fw, cancel, kept):
    """A press's cancel deletes the part; leaving the page keeps it."""
    nu = fw.var('netupdate__nu', Nu)
    slept = []
    fw.mock('fw_usleep', lambda us, *_: slept.append(us) or setattr(nu, 'cancel', cancel))
    state, net, card = update(fw, asset=(18, 200, None, BODY[:50000]))
    assert state == NU_CANCELLED and len(slept) == 1 and net.urls[2:] == [ASSET]
    assert (PART in card.files) == kept


def test_a_missing_asset_leaves_no_part(fw):
    state, _, card = update(fw, asset=(0, 404, None))
    assert state == NU_MISSING and card.of('unlink') == [(NU_JSON,), (PART,)] and not card.of('rename')


def test_a_failed_transfer_says_network_a_write_error_card(fw):
    assert update(fw, asset=(28, 0, None))[0] == NU_NET
    fw.mock('fw_ferror', lambda *_: 1)
    assert update(fw, asset=(23, 0, None))[0] == NU_CARD


def test_a_card_that_cant_open_the_part(fw):
    card = slot(fw)
    fw.mock('fw_fopen', lambda path, mode, *_: 0 if C.string_at(path) == PART else card.fopen(path, mode))
    state, net, _ = update(fw, card=card)
    assert state == NU_CARD and net.urls == [LATEST, API + NEWER]


def test_a_cancel_during_the_check_downloads_nothing(fw):
    fw.var('netupdate__nu', Nu).cancel = 1
    state, net, _ = update(fw)
    assert state == NU_CANCELLED and net.urls == [LATEST]


def test_a_cancel_while_the_release_is_read_downloads_nothing(fw):
    nu = fw.var('netupdate__nu', Nu)
    state, net, card = update(fw, during=lambda k: setattr(nu, 'cancel', 1), only=API)
    assert state == NU_CANCELLED and net.urls == [LATEST, API + NEWER] and PART not in card.written


def test_a_cancel_while_the_card_takes_the_last_bytes_is_a_cancel(fw):
    """The press lands during the fsync of the whole part, seconds on a slow card with the label at 100%: the part
    goes and the old tar stays, as for any cancel, and the word is Download cancelled."""
    card = slot(fw, {TAR: b'old'})
    nu = fw.var('netupdate__nu', Nu)
    fw.mock('fw_fsync', lambda fd, *_: setattr(nu, 'cancel', 1) or 0)
    state, _, card = update(fw, card=card)
    assert state == NU_CANCELLED and card.files[TAR] == b'old' and PART not in card.files and not card.of('rename')


def read_back(fw, card, then):
    """then() run as the part is opened to be read back (not to be sized, before a try); what it returned, each time."""
    seen, nu = [], fw.var('netupdate__nu', Nu)
    def fopen(path, mode, *_):
        if C.string_at(path) == PART and b'r' in C.string_at(mode) and nu.stage == 2:
            seen.append(then())
        return card.fopen(path, mode)
    fw.mock('fw_fopen', fopen)
    return seen


def test_a_cancel_while_the_part_is_read_back_is_a_cancel(fw):
    card = slot(fw, {TAR: b'old'})
    nu = fw.var('netupdate__nu', Nu)
    seen = read_back(fw, card, lambda: setattr(nu, 'cancel', 1) or nu.stage)
    state, _, card = update(fw, card=card)
    assert state == NU_CANCELLED and seen == [2]
    assert card.files[TAR] == b'old' and PART not in card.files and not card.of('rename')


def test_a_part_the_card_cant_give_back_is_a_card_error(fw):
    card = slot(fw, {TAR: b'old'})
    fw.mock('fw_fopen', lambda path, mode, *_: 0 if C.string_at(path) == PART and b'r' in C.string_at(mode) else card.fopen(path, mode))
    state, _, _ = update(fw, card=card)
    assert state == NU_CARD and card.files[TAR] == b'old' and PART not in card.files and not card.of('rename')


def fetch(fw, url=b'https://x', tag_size=64, file=None, start=0):
    tag, code = block(fw, size=tag_size), C.c_long(0)
    xfer = (C.c_int * 2)()
    fn = fw.fn('netupdate__fetch', C.c_int, C.c_char_p, C.c_void_p, C.c_void_p, C.c_uint, C.POINTER(C.c_long), C.c_void_p, C.c_long)
    err = fn(url, file, tag if not file else None, tag_size, C.byref(code), xfer, start)
    return err, C.string_at(tag), code.value


def test_a_header_fetch_sends_no_body_and_keeps_the_tag(fw):
    net = Net(fw, {b'https': (0, 302, b'https://g/releases/tag/1.2.3')})
    assert fetch(fw) == (0, b'1.2.3', 302)
    assert net.opts[44] == 1 and 52 not in net.opts  # NOBODY, no FOLLOWLOCATION


def test_a_download_follows_redirects_to_its_file_and_verifies(fw):
    net = Net(fw, {b'https': (0, 200, None)})
    assert fetch(fw, file=7)[0] == 0
    assert net.opts[52] == 1 and net.opts[10001] == 7 and net.opts[64] == 1 and net.opts[81] == 2


def test_a_download_carried_on_asks_from_its_byte_and_a_new_one_asks_for_no_range(fw):
    net = Net(fw, {b'https': (0, 206, None)})
    assert fetch(fw, file=7, start=70000) == (0, b'', 206) and net.opts[21] == 70000  # RESUME_FROM
    assert fetch(fw, file=7)[0] == 0 and 21 not in net.opts


def test_a_resumed_transfer_fails_on_an_http_error_and_a_new_one_does_not(fw):
    net = Net(fw, {b'https': (0, 206, None)})
    fetch(fw, file=7, start=70000)
    assert net.opts[45] == 1  # FAILONERROR: a 403/429/5xx is curl's 22, not an error page in the part
    fetch(fw, file=7)
    assert 45 not in net.opts  # a first try's 404 must stay "No update for this build"


def test_a_failed_transfer_is_curl_s_own_code(fw):
    Net(fw, {b'https': (33, 0, None)})
    assert fetch(fw, file=7, start=5)[0] == 33  # CURLE_RANGE_ERROR, which update() tells from a lost connection


def test_no_curl_handle_is_an_error(fw):
    assert fetch(fw)[0] == 1


def test_a_redirect_with_no_location_clears_the_tag(fw):
    Net(fw, {b'https': (0, 302, None)})
    assert fetch(fw)[:2] == (0, b'')


def json_get(fw, card, body, code=200, result=0):
    Net(fw, {b'https://api': (result, code, None)})
    card.files[JSON_PATH] = body
    table = apps(fw, b'me/foo')
    tag, url, size = block(fw, size=32), block(fw, size=256), C.c_uint(0)
    r = fw.fn('netupdate__release', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_uint))(C.byref(table[0]), tag, url, C.byref(size))
    return r, table[0].latest


def test_a_release_read_through_its_temp_file(fw):
    card = Card(fw)
    assert json_get(fw, card, github('2.0', ('a.zip', 5))) == (SH_OK, b'2.0')
    assert (JSON_PATH,) in card.of('unlink')


@pytest.mark.parametrize('code,r', [(404, SH_MISSING), (403, SH_BUSY), (429, SH_BUSY), (500, SH_NET)])
def test_githubs_refusals(fw, code, r):
    assert json_get(fw, Card(fw), b'{}', code) == (r, b'')


def test_a_release_without_a_zip_is_missing(fw):
    assert json_get(fw, Card(fw), github('2.0'))[0] == SH_MISSING


def test_a_failed_transfer_is_no_network(fw):
    assert json_get(fw, Card(fw), b'', result=7)[0] == SH_NET


def test_a_reply_over_a_megabyte_is_refused(fw):
    assert json_get(fw, Card(fw), b' ' * ((1 << 20) + 1))[0] == SH_NET


def room(fw, bytes_, blocks, size=4096, ok=0):
    def statfs(path, buf, *_):
        words = C.cast(buf, C.POINTER(C.c_uint))
        words[1], words[7] = size, blocks
        return ok
    fw.mock('fw_statfs', statfs)
    need = C.c_uint(0)
    return fw.fn('netupdate__room', C.c_int, C.c_ulonglong, C.POINTER(C.c_uint))(bytes_, C.byref(need)), need.value


def test_room_for_the_bytes_and_the_reserve(fw):
    assert room(fw, 4096, (RESERVE + 4096) // 4096)[0] == SH_OK


def test_one_block_short_says_how_many_mb(fw):
    assert room(fw, 8192, (RESERVE + 4096) // 4096) == (SH_SPACE, 1)
    assert room(fw, 100 << 20, 0) == (SH_SPACE, 164)


def test_a_huge_card_doesnt_overflow(fw):
    assert room(fw, 1 << 30, 1 << 31, 32768)[0] == SH_OK


def test_no_statfs_is_a_card_error(fw):
    assert room(fw, 1, 1 << 20, ok=-1)[0] == SH_CARD


def test_more_than_4gb_is_asked_for_whole(fw):
    assert room(fw, 5 << 30, (4 << 30) // 4096) == (SH_SPACE, 1024 + 64)


def install(fw, card, net=None, size=5, blocks=1 << 24):
    net = net or Net(fw, {b'https://api': (0, 200, None), b'https://h': (0, 200, None)})
    card.files[JSON_PATH] = github('2.0', ('a.zip', size))
    table = apps(fw, b'me/foo')
    def statfs(path, buf, *_):
        words = C.cast(buf, C.POINTER(C.c_uint))
        words[1], words[7] = 4096, blocks
    fw.mock('fw_statfs', statfs)
    return fw.fn('netupdate__install', C.c_int, C.c_int)(0), fw.var('netupdate__sh', Sh), table


def installing(fw):
    card = Card(fw, {f'{STAGING}/foo.list'.encode(): row(5, '.foo/q2-foo'), f'{STAGING}/foo.files'.encode(): b'.foo/q2-foo\n'})
    return card


def test_a_whole_install(fw):
    card = installing(fw)
    r, sh, table = install(fw, card)
    assert r == SH_OK and sh.stage == 3 and table[0].latest == b'2.0'
    assert b'/mnt/mmc/.foo/q2-foo' in card.files and b'/mnt/mmc/.q2/apps/foo.files' in card.files
    assert card.of('rename')[-1] == (b'/mnt/mmc/.q2/apps/foo.tag.tmp', b'/mnt/mmc/.q2/apps/foo.tag')
    assert card.written[b'/mnt/mmc/.q2/apps/foo.tag.tmp'] == b'2.0'
    assert card.of('system')[-1] == (b'rm -rf ' + STAGING.encode(),)


def test_a_cancel_before_moving_leaves_the_app_alone(fw):
    card = installing(fw)
    fw.var('netupdate__sh', Sh).cancel = 1
    assert install(fw, card)[0] == SH_CANCELLED
    assert b'/mnt/mmc/.foo/q2-foo' not in card.files and not card.of('rename')


def test_no_release_stops_it(fw):
    card = installing(fw)
    net = Net(fw, {b'https://api': (0, 404, None)})
    assert install(fw, card, net)[0] == SH_MISSING and not card.of('system')


@pytest.mark.refusal('addon:traversal')
def test_a_bad_zip_changes_nothing(fw):
    card = Card(fw, {f'{STAGING}/foo.list'.encode(): row(5, '../x')})
    assert install(fw, card)[0] == SH_BAD
    assert b'/mnt/mmc/.foo/q2-foo' not in card.files


def test_no_room_for_the_zip(fw):
    card = installing(fw)
    r, sh, _ = install(fw, card, size=900 << 20, blocks=1)
    assert r == SH_SPACE and sh.need == 964 and not [e for e in card.log if e[0] == 'rename']


def test_a_failed_download_says_why(fw):
    card = installing(fw)
    net = Net(fw, {b'https://api': (0, 200, None), b'https://h': (7, 0, None)})
    assert install(fw, card, net)[0] == SH_NET
    net.replies[b'https://h'] = (0, 404, None)
    assert install(fw, card, net)[0] == SH_MISSING


def state(fw, card, latest=b'', i=0):
    (App * SH_MAX).in_dll(fw.lib, 'shelf_apps')[i].latest = latest
    return fw.fn('netupdate__sh_state', C.c_int, C.c_int)(i)


def test_installed_with_a_tag(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {b'/mnt/mmc/.q2/apps/foo.tag': b'2.0'})
    assert state(fw, card) == ST_INSTALLED and state(fw, card, b'2.0') == ST_INSTALLED
    assert state(fw, card, b'2.1') == ST_UPDATE


def test_a_binary_without_a_tag_is_a_manual_install(fw):
    apps(fw, b'me/foo')
    assert state(fw, Card(fw, {b'/mnt/mmc/.foo/q2-foo': b''})) == ST_UPDATE


def test_a_folder_where_the_binary_should_be_is_not_installed(fw):
    apps(fw, b'me/foo')
    card = Card(fw, {b'/mnt/mmc/.foo/q2-foo': b''})
    fw.mock('fw___xstat', lambda v, p, info, *_: C.cast(info, C.POINTER(C.c_uint)).__setitem__(5, 0o040755))
    assert state(fw, card) == ST_INSTALL


def test_nothing_on_the_card(fw):
    apps(fw, b'me/foo', b'x/q2-rockbox-themes')
    card = Card(fw)
    assert state(fw, card) == ST_INSTALL and state(fw, card, i=1) == ST_INSTALL  # the themes have no binary


def test_a_tag_file_too_long_is_held_to_its_buffer(fw):
    apps(fw, b'me/foo')
    assert state(fw, Card(fw, {b'/mnt/mmc/.q2/apps/foo.tag': b'x' * 500}), b'x' * 31) == ST_UPDATE


def test_file_mode_is_its_type_bits(fw):
    mode = fw.fn('netupdate__file_mode', C.c_uint, C.c_char_p)
    Card(fw, {b'/f': b''})
    assert mode(b'/f') == 0o100000 and mode(b'/missing') == 0


class Page:
    """The Apps page: its rows' text and toasts, with no thread run. Row 0 is Check for Updates."""
    def __init__(self, fw, *lines):
        self.fw, self.text, self.toasts = fw, {}, []
        self.table = apps(fw, *lines)
        self.card = Card(fw)
        self.sh = fw.var('netupdate__sh', Sh)
        self.wifi, self.top, self.now = 1, 77, 3600
        fw.mock('get_wifisignal', lambda *_: self.wifi)
        fw.mock('window_create', lambda *_: 77)
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('fw_clock_gettime', lambda which, ts, *_: C.cast(ts, C.POINTER(C.c_long)).__setitem__(0, self.now))
        fw.mock('widget_get_child', lambda row, i, *_: row)
        fw.mock('list_item_create', lambda view, x, y, *_: 1000 + y)
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        self.sh.page = 77
        fw.fn('netupdate__sh_rows', None)()

    def label(self, r):
        return self.text[self.sh.row[r]]

    def click(self, r=1):
        self.fw.fn('netupdate__sh_click', C.c_int, C.c_void_p, C.c_void_p)(r, None)

    def checked(self, *latest, manual=0, result=SH_OK):
        """A check that found these latest tags, one an app, ended: sh_wait's toasts."""
        self.sh.next = self.table
        for i, tag in enumerate(latest):
            self.sh.next[i].latest = tag
        self.sh.n, self.sh.job, self.sh.manual, self.sh.result = self.fw.var('shelf_n', C.c_int).value, JOB_CHECK, manual, result
        self.fw.fn('netupdate__sh_wait', C.c_int, C.c_void_p)(None)
        return self.toasts


def test_rows_say_install_under_check_for_updates(fw):
    p = Page(fw, b'me/foo', b'me/bar')
    assert p.sh.rows == 3 and p.sh.map[0] == ROW_CHECK and p.label(0) == ''
    assert p.label(1) == 'Install' and p.label(2) == 'Install'


def test_a_press_installs_at_once(fw):
    p = Page(fw, b'me/foo')
    p.click()
    assert p.sh.job == JOB_INSTALL and p.sh.app == 0 and p.sh.running == 1 and p.label(1) == 'Checking…'


def test_a_press_without_wifi_says_so(fw):
    p = Page(fw, b'me/foo')
    p.wifi = 0
    p.click()
    assert p.sh.job == 0 and p.toasts == ['Connect to Wi-Fi first']


def test_rockbox_asks_twice_as_it_becomes_the_default(fw):
    p = Page(fw, b'x/q2-rockbox')
    p.click()
    assert p.sh.job == 0 and p.label(1).endswith('Press again to install')
    p.click()
    assert p.sh.job == JOB_INSTALL


def test_an_installed_app_is_removed_on_a_second_press(fw):
    p = Page(fw, b'me/foo')
    p.card.files[b'/mnt/mmc/.q2/apps/foo.tag'] = b'1.0'
    p.click()
    assert p.sh.armed == 2 and p.label(1) == 'Press again to remove'
    p.click()
    assert p.sh.job == JOB_REMOVE and p.sh.stage == 4


def test_a_press_on_the_running_install_cancels_it_until_it_moves(fw):
    p = Page(fw, b'me/foo', b'me/bar')
    p.click(1)
    p.click(2)
    assert p.toasts == ['Wait for the one in progress'] and p.sh.cancel == 0
    p.click(1)
    assert p.sh.cancel == 1 and p.label(1) == 'Cancelling…'
    p.sh.cancel, p.sh.stage = 0, 3
    p.click(1)
    assert p.sh.cancel == 0


def test_download_progress_on_the_row(fw):
    p = Page(fw, b'me/foo')
    p.click()
    p.sh.stage, p.sh.percent = 1, 42
    fw.fn('netupdate__sh_refresh', None)()
    assert p.label(1) == 'Downloading 42%'


def test_with_no_list_check_for_updates_loads_it(fw):
    p = Page(fw)
    assert p.sh.rows == 1 and p.sh.map[0] == ROW_CHECK and p.label(0) == 'Connect to Wi-Fi to load the list'
    p.click(0)
    assert p.sh.job == JOB_CHECK and p.sh.app == 0 and p.sh.manual == 1 and p.label(0) == 'Checking…'


def test_check_for_updates_checks_inside_the_ten_minutes(fw):
    p = Page(fw, b'me/foo')
    p.sh.checked = (p.now - 120) * 1000
    p.click(0)
    assert p.sh.job == JOB_CHECK and p.sh.manual == 1 and not p.toasts


def test_check_for_updates_within_a_minute_says_the_last_count_again(fw):
    p = Page(fw, b'me/foo')
    p.card.files[b'/mnt/mmc/.q2/apps/foo.tag'] = b'1.0'
    p.table[0].latest = b'2.0'
    p.sh.checked = (p.now - 30) * 1000
    p.click(0)
    assert p.sh.job == 0 and p.toasts == ['1 app can be updated']
    p.table[0].latest = b'1.0'
    p.click(0)
    assert p.sh.job == 0 and p.toasts[-1] == 'All apps are up to date'


def test_check_for_updates_while_checking_or_busy(fw):
    p = Page(fw, b'me/foo')
    p.sh.job = JOB_CHECK
    p.click(0)
    assert p.sh.cancel == 0 and p.sh.manual == 1 and not p.toasts  # checking already: its end answers
    p.sh.job = JOB_INSTALL
    p.click(0)
    assert p.toasts == ['Wait for the one in progress']
    p.sh.job, p.wifi = 0, 0
    p.click(0)
    assert p.sh.job == 0 and p.toasts[-1] == 'Connect to Wi-Fi first'


def test_a_check_says_how_many_apps_have_a_newer_release(fw):
    p = Page(fw, b'me/a', b'me/b', b'me/c', b'me/d')
    for app in b'abc':
        p.card.files[b'/mnt/mmc/.q2/apps/%c.tag' % app] = b'1.0'
    p.card.files[b'/mnt/mmc/.d/q2-d'] = b''  # a manual install: Update, not counted
    assert p.checked(b'2.0', b'2.0', b'2.0') == ['3 apps can be updated']
    assert p.label(4) == 'Update'
    p.toasts.clear()
    assert p.checked(b'2.0', b'1.0', b'1.0') == ['1 app can be updated']


def test_none_to_update_is_said_only_after_check_for_updates(fw):
    p = Page(fw, b'me/foo')
    assert p.checked() == []
    assert p.checked(manual=1) == ['All apps are up to date']


def test_a_failed_check_is_said_only_after_check_for_updates(fw):
    p = Page(fw, b'me/foo')
    assert p.checked(result=SH_BUSY) == []
    assert p.checked(result=SH_BUSY, manual=1) == ['GitHub is busy, try later']
    p.toasts.clear()
    assert p.checked(result=SH_NET, manual=1) == ["Can't reach GitHub"]
    p.card.files[b'/mnt/mmc/.q2/apps/foo.tag'] = b'1.0'
    p.toasts.clear()
    assert p.checked(b'2.0', result=SH_BUSY, manual=1) == ['1 app can be updated']  # what it found first


def test_no_toast_when_the_page_is_left_covered_or_the_check_stopped(fw):
    p = Page(fw, b'me/foo')
    p.top = 99
    assert p.checked(manual=1) == []
    p.top, p.sh.cancel = 77, 1
    assert p.checked(manual=1) == []
    p.sh.cancel = 0
    p.fw.fn('netupdate__sh_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert p.checked(manual=1) == []


def test_the_next_check_isnt_check_for_updates_unless_pressed_again(fw):
    p = Page(fw, b'me/foo')
    p.click(0)
    p.sh.running = 0
    assert p.checked(manual=p.sh.manual) == ['All apps are up to date']
    p.fw.fn('netupdate__sh_start', None, C.c_int, C.c_int)(JOB_CHECK, 0)  # the opening check
    assert p.sh.manual == 0


def test_rockbox_themes_show_only_with_rockbox(fw):
    p = Page(fw, b'x/q2-rockbox', b'x/q2-rockbox-themes')
    assert p.sh.rows == 2
    p.card.files[b'/mnt/mmc/.rockbox/rockbox'] = b''
    fw.fn('netupdate__sh_rows', None)()
    assert p.sh.rows == 3


def test_a_press_while_checking_stops_the_check(fw):
    p = Page(fw, b'me/foo')
    p.sh.job = JOB_CHECK
    p.click(1)
    assert p.sh.cancel == 1 and p.toasts == ['Stopped checking for updates']


def test_erase_runs_unless_a_job_does(fw):
    p = Page(fw, b'me/foo')
    erase = fw.fn('shelf_erase', C.c_int, C.c_int)
    assert erase(0) == 0
    assert erase(1) == 1 and p.sh.job == JOB_ERASE
    p.sh.job = JOB_INSTALL
    assert erase(1) == 0 and p.toasts == ['Wait for the one in progress']


def check(fw):
    return fw.fn('netupdate__check', C.c_int)()


def test_the_check_takes_a_fresh_list_and_each_release(fw):
    card = Card(fw)
    Net(fw, {b'https://raw': (0, 200, None), b'https://api': (0, 200, None)})
    card.files[JSON_PATH] = b'me/foo Foo\nme/bar\n'
    sh = fw.var('netupdate__sh', Sh)
    fw.mock('fw_fopen', fw_fopen_list(card))
    assert check(fw) == 1
    assert sh.n == 2 and sh.next[0].id == b'foo' and sh.next[0].latest == b'1.0'
    assert b'/mnt/mmc/.q2/sources.list' in [c[1] for c in card.of('rename')]
    assert card.written[b'/mnt/mmc/.q2/sources.list.tmp'] == b'me/foo Foo\nme/bar bar\n'


def fw_fopen_list(card):
    """The first read of the temp file is the list; the rest are releases."""
    release_json = github('1.0', ('a.zip', 5))
    reads = [0]
    def fopen(path, mode, *_):
        name, how = C.string_at(path), C.string_at(mode)
        if name == JSON_PATH and b'r' in how:
            reads[0] += 1
            if reads[0] > 1:
                card.files[JSON_PATH] = release_json
        if b'r' in how and name not in card.files:
            return 0
        f = len(card.names) + 1
        card.names[f], card.at[f] = name, 0
        if b'w' in how:
            card.written[name] = b''
        return f
    return fopen


def test_a_check_keeps_why_a_release_failed_but_not_a_missing_one(fw):
    card = Card(fw)
    apps(fw, b'me/foo', b'me/bar')
    sh = fw.var('netupdate__sh', Sh)
    net = Net(fw, {b'https://raw': (7, 0, None), b'https://api.github.com/repos/me/foo': (0, 404, None),
                   b'https://api': (0, 403, None)})
    assert check(fw) == 0 and sh.result == SH_BUSY
    sh.result = SH_OK
    net.replies = {b'https://raw': (7, 0, None), b'https://api': (0, 404, None)}
    assert check(fw) == 1 and sh.result == SH_OK  # no release yet: checked, the minute's gap holds


def test_no_list_anywhere_is_a_failed_check(fw):
    Card(fw)
    fw.var('shelf_n', C.c_int).value = 0
    sh = fw.var('netupdate__sh', Sh)
    Net(fw, {b'https://raw': (7, 0, None)})
    assert check(fw) == 0 and sh.result == SH_NET and sh.n == 0


def test_a_bad_list_never_replaces_the_one_there(fw):
    card = Card(fw)
    apps(fw, b'me/kept')
    Net(fw, {b'https://raw': (0, 200, None), b'https://api': (0, 200, None)})
    card.files[JSON_PATH] = b'# nothing\n'
    sh = fw.var('netupdate__sh', Sh)
    check(fw)
    assert sh.n == 1 and sh.next[0].id == b'kept' and b'/mnt/mmc/.q2/sources.list.tmp' not in card.written


def test_an_app_dropped_from_the_list_but_installed_stays_removable(fw):
    card = Card(fw, {b'/mnt/mmc/.q2/apps/old.tag': b'1'})
    apps(fw, b'me/old', b'me/gone')
    Net(fw, {b'https://raw': (0, 200, None), b'https://api': (0, 200, None)})
    fw.mock('fw_fopen', fw_fopen_list(card))
    card.files[JSON_PATH] = b'me/new\n'
    sh = fw.var('netupdate__sh', Sh)
    check(fw)
    assert [sh.next[i].id for i in range(sh.n)] == [b'new', b'old']


def test_a_dropped_app_gets_no_release_and_stays_dropped_on_the_card(fw):
    """Dropping an app from the list is what stops a taken-over repo's releases (GHSA-8wpr-pmqx-538x): its release is
    never asked, an earlier check's tag is let go, and the card's list marks it "-" so a later offline page knows."""
    card = Card(fw, {b'/mnt/mmc/.q2/apps/old.tag': b'1'})
    table = apps(fw, b'me/old')
    table[0].latest = b'2.0'
    net = Net(fw, {b'https://raw': (0, 200, None), b'https://api': (0, 200, None)})
    fw.mock('fw_fopen', fw_fopen_list(card))
    card.files[JSON_PATH] = b'me/new\n'
    sh = fw.var('netupdate__sh', Sh)
    check(fw)
    assert sh.next[1].dropped == b'\x01' and sh.next[1].latest == b''
    assert not [u for u in net.urls if b'repos/me/old' in u]
    assert card.written[b'/mnt/mmc/.q2/sources.list.tmp'] == b'me/new new\n-me/old old\n'
    reloaded = apps(fw, b'me/new new', b'-me/old old')
    assert [(a.id, a.dropped) for a in reloaded[:2]] == [(b'new', b'\x00'), (b'old', b'\x01')]
    net.urls.clear()
    assert check(fw) == 1 and not [u for u in net.urls if b'repos/me/old' in u]  # the list failed, the mark held


def test_a_dropped_app_never_installs_and_shows_only_while_installed(fw):
    card = installing(fw)
    net = Net(fw, {b'https://api': (0, 200, None), b'https://h': (0, 200, None)})
    apps(fw, b'-me/foo')
    assert fw.fn('netupdate__install', C.c_int, C.c_int)(0) == SH_MISSING and not net.urls
    p = Page(fw, b'me/bar', b'-me/foo')
    assert p.sh.rows == 2  # Check for Updates and bar: foo isn't installed
    p.card.files[b'/mnt/mmc/.q2/apps/foo.tag'] = b'1.0'
    fw.fn('netupdate__sh_rows', None)()
    assert p.sh.rows == 3 and p.label(2) == 'Installed'


def test_a_full_list_keeps_the_cards_list_inside_its_buffer(fw):
    card = Card(fw)
    Net(fw, {b'https://raw': (0, 200, None), b'https://api': (0, 200, None)})
    lines = b''.join(b'%s/%s%02d %s\n' % (b'o' * 39, b'a' * 13, i, b'n' * 31) for i in range(SH_MAX))
    card.files[JSON_PATH] = lines
    fw.mock('fw_fopen', fw_fopen_list(card))
    check(fw)
    assert card.written[b'/mnt/mmc/.q2/sources.list.tmp'] == lines


def test_the_page_opens_on_the_cards_last_list(fw):
    card = Card(fw, {b'/mnt/mmc/.q2/sources.list': b'me/foo Foo\n'})
    fw.mock('window_create', lambda *_: 77)
    fw.mock('get_wifisignal', lambda *_: 0)
    fw.fn('shelf_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert fw.var('shelf_n', C.c_int).value == 1 and (App * SH_MAX).in_dll(fw.lib, 'shelf_apps')[0].id == b'foo'


def test_the_page_opens_with_wifi_and_checks(fw):
    Card(fw)
    fw.mock('window_create', lambda *_: 77)
    fw.mock('get_wifisignal', lambda *_: 1)
    fw.fn('shelf_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert fw.var('netupdate__sh', Sh).job == JOB_CHECK


def test_a_hostile_list_on_the_card_opens_the_page_safely(fw):
    for raw in hostile(34, [b'me/foo Foo\nme/q2-bar\n'], 300, b'/ \n\xff'):
        fw.var('shelf_n', C.c_int).value = 0
        fw.var('netupdate__sh', Sh).page = None
        Card(fw, {b'/mnt/mmc/.q2/sources.list': raw})
        fw.mock('window_create', lambda *_: 77)
        fw.mock('get_wifisignal', lambda *_: 0)
        fw.fn('shelf_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)


# End to end: a press to the toast, the card's files after.

EVT_CLICK, EVT_DESTROY = O['EVT_CLICK'], O['EVT_DESTROY']
RET_REPEAT = 8
SOURCES = b'/mnt/mmc/.q2/sources.list'
MOUNTS = b'/proc/mounts'


class Disk(Card):
    """Card, with what a whole job needs: a file written whole at its fclose, folders that open (the card's root, for
    its fsync) and list (addon.c's scan), each fsync logged by its file's name, and busybox's commands run on the
    files: unzip's listing and unpacking of the downloaded zip (fnmatch without FNM_PATHNAME, as busybox's), find
    and rm -rf. The rest (kill, sync) is only logged."""
    def __init__(self, fw, files=None):
        super().__init__(fw, files)
        self.writing, self.listing, self.unpack_fails = set(), {}, 0
        self.system = self.shell
        fw.mock('fw_fopen', self.fopen)
        fw.mock('fw_fclose', self.fclose)
        fw.mock('fw_fileno', lambda f, *_: f + 100)
        fw.mock('fw_fsync', lambda fd, *_: self.log.append(('fsync', self.names[fd - 100])))
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)

    def folder(self, name):
        return any(f.startswith(name + b'/') for f in self.files)

    def fopen(self, path, mode, *_):
        name, how = C.string_at(path), C.string_at(mode)
        if b'r' in how and name not in self.files and not self.folder(name):
            return 0
        f = len(self.names) + 1
        self.names[f], self.at[f] = name, 0
        if b'w' in how or b'a' in how:
            self.written[name] = b'' if b'w' in how else self.files.get(name, b'')
            self.writing.add(f)
        return f

    def fclose(self, f, *_):
        if f in self.writing:
            self.writing.discard(f)
            self.files[self.names[f]] = self.written[self.names[f]]
        return 0

    def opendir(self, path, *_):
        base = C.string_at(path) + b'/'
        names = sorted({f[len(base):].split(b'/')[0] for f in self.files if f.startswith(base)})
        self.listing[len(self.listing) + 0x3000] = iter([(n, 4 if self.folder(base + n) else 8) for n in names])
        return len(self.listing) + 0x2fff

    def readdir(self, d, *_):
        class Dirent(C.Structure):  # host.py's struct dirent
            _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]
        for name, kind in self.listing[d]:
            self.entry = Dirent(0, 0, 0, kind, name)
            return C.addressof(self.entry)
        return 0

    def shell(self, cmd):
        line = cmd.decode()
        if line.startswith('busybox unzip '):
            return self.unzip(shlex.split(line[len('busybox unzip '):]))
        found = re.fullmatch(r'cd (\S+) && find (\S+) -type f > (\S+)', line)
        if found:
            top, folder, out = (g.encode() for g in found.groups())
            names = sorted(f[len(top) + 1:] for f in self.files if f.startswith(top + b'/' + folder + b'/'))
            self.files[out] = b''.join(n + b'\n' for n in names)
        elif line.startswith('rm -rf '):
            for path in shlex.split(line[len('rm -rf '):]):
                for f in [f for f in self.files if f == path.encode() or f.startswith(path.encode() + b'/')]:
                    del self.files[f]
        return 0

    def unzip(self, args):
        out = args[args.index('>') + 1].encode() if '>' in args else None
        args = args[:args.index('>')] if out else args
        part = next(a for a in args if a.endswith('.part'))
        patterns = args[args.index(part) + 1:]
        want, skip = patterns[:patterns.index('-x')], patterns[patterns.index('-x') + 1:]
        with zipfile.ZipFile(io.BytesIO(self.files[part.encode()])) as z:
            entries = [e for e in z.infolist() if any(fnmatch.fnmatchcase(e.filename, p) for p in want)
                       and not any(fnmatch.fnmatchcase(e.filename, p) for p in skip)]
            if '-l' in args:
                self.files[out] = b''.join(row(e.file_size, e.filename) for e in entries)
                return 0
            folder = args[args.index('-d') + 1]
            for e in entries:
                self.files[f'{folder}/{e.filename}'.encode()] = z.read(e)
        return self.unpack_fails

    def moves(self, into):
        return [e[2] for e in self.log if e[0] == 'rename' and e[2].startswith(into)]

    def commands(self):
        return [e[1] for e in self.log if e[0] == 'system']


class UpdateRow:
    """System Update's page as stock lays it out (stand-in handles), hooked by ringnav_fwupdate: its second row's button
    and label, what they show, the toasts, the wait timer, the worker and stock's card update."""
    VIEW, ROW, BUTTON, LABEL = 0x7100, 0x7200, 0x7300, 0x7400

    def __init__(self, fw, wifi=1, children=2, thread=0, win=0x7000, battery=80, cable=0):
        self.fw, self.wifi, self.flashed = fw, wifi, 0
        fw.var('fw_ctx_g_power_capacity', C.c_int).value, fw.var('g_usbdet_value', C.c_ubyte).value = battery, cable
        self.names, self.text, self.tr, self.handlers, self.toasts, self.timers, self.workers = {}, {}, {}, [], [], [], []
        tree = {(self.VIEW, 1): self.ROW, (self.ROW, 0): self.BUTTON, (self.BUTTON, 0): self.LABEL}
        fw.mock('stock_fwupdate_trampoline', lambda *_: 7)
        fw.mock('widget_lookup', lambda w, name, *_: self.VIEW if C.string_at(name) == b'scroll_view_fwupdate' else 0)
        fw.mock('widget_count_children', lambda w, *_: children if w == self.VIEW else 0)
        fw.mock('widget_get_child', lambda w, i, *_: tree.get((w, i), 0))
        fw.mock('widget_set_name', lambda w, s, *_: self.names.__setitem__(w, C.string_at(s)))
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: self.handlers.append((w, evt, fn, ctx)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_set_tr_text', lambda w, s, *_: self.tr.__setitem__(w, C.string_at(s).decode()))
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        fw.mock('get_wifisignal', lambda *_: self.wifi)
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append(fn) or len(self.timers))
        fw.mock('fw_pthread_create', lambda t, a, fn, arg, *_: thread or self.workers.append(fn) or 0)
        fw.mock('fwupdate_tf', lambda *_: setattr(self, 'flashed', self.flashed + 1))
        self.nu = fw.var('netupdate__nu', Nu)
        self.result = fw.fn('ringnav_fwupdate', C.c_int, C.c_void_p, C.c_void_p)(win, 0x7001)

    def on(self, w, event):
        return [lambda f=fn, c=ctx: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(f)(c, None)
                for ww, evt, fn, ctx in self.handlers if ww == w and evt == event]

    def press(self):
        click, = self.on(self.BUTTON, EVT_CLICK)
        assert click() == 0

    def leave(self):
        gone, = self.on(self.LABEL, EVT_DESTROY)
        gone()

    def wait(self):
        """wait_done, as its timer runs it."""
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(self.timers[-1])(None)

    def run(self):
        assert C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.workers.pop())(None) is None

    def label(self):
        return self.text.get(self.LABEL)


def test_system_update_s_second_row_becomes_the_online_update(fw):
    row = UpdateRow(fw)
    assert row.result == 7 and row.names == {row.BUTTON: b'2'}  # a name stock's own click ignores
    assert len(row.on(row.BUTTON, EVT_CLICK)) == 1 and len(row.on(row.LABEL, EVT_DESTROY)) == 1 and row.nu.label == row.LABEL
    assert row.label() is None and not row.workers


@pytest.mark.parametrize('children, win', [(1, 0x7000), (3, 0x7000), (2, 0)])
def test_a_page_laid_out_otherwise_stays_stock_s(fw, children, win):
    row = UpdateRow(fw, children=children, win=win)
    assert row.result == 7 and row.names == {} and row.handlers == [] and not row.nu.label


def test_a_press_downloads_a_newer_release_and_hands_it_to_stock_s_card_update(fw):
    card = slot(fw, {TAR: b'old'})
    row = UpdateRow(fw)
    row.press()
    assert row.nu.state == NU_RUNNING and row.label() == 'Checking…' and row.wait() == RET_REPEAT
    seen = []
    Net(fw, replies(), during=lambda k: seen.append((row.wait(), row.label())), only=GET)
    checked = read_back(fw, card, lambda: (row.wait(), row.label()))
    row.run()
    assert seen == [(RET_REPEAT, f'Downloading {p}%') for p in (25, 50, 75, 100)]
    assert checked == [(RET_REPEAT, 'Verifying…')]
    assert {args[:3] for _, args in fw.calls('reset_poweroptions_timer')} == {(1, 1, 0)}  # standby held, not the screen
    assert card.files[TAR] == BODY and PART not in card.files
    # the part on the card before it replaces the old tar, then the rename's own entry before stock flashes it
    assert [e for e in card.log if e[0] in ('rename', 'fsync')] == [('fsync', PART), ('rename', PART, TAR), ('fsync', b'/mnt/mmc')]
    assert row.wait() == 0 and row.flashed == 1 and row.toasts == [] and row.tr == {row.LABEL: 'update_online'}
    assert row.nu.state == NU_IDLE


def test_the_release_is_read_under_checking_not_a_percentage(fw):
    slot(fw)
    row = UpdateRow(fw)
    row.press()
    seen = []
    Net(fw, replies(api=(0, 200, None, tar_json() + b' ' * 4096)), during=lambda k: seen.append(row.wait() and row.label()), only=API)
    row.run()
    assert seen == ['Checking…'] * CHUNKS and row.nu.percent == 100  # the tar's, the release's own dropped


def test_a_second_press_cancels_mid_download_and_keeps_the_old_tar(fw):
    card = slot(fw, {TAR: b'old'})
    row = UpdateRow(fw)
    row.press()
    seen = []

    def during(k):
        if k == 1:
            row.press()
            seen.append((row.wait(), row.label()))  # the timer says so too, not the percentage
    Net(fw, replies(), during=during, only=GET)
    row.run()
    assert seen == [(RET_REPEAT, 'Cancelling…')] and len(card.written[PART]) < len(BODY)  # ended at the next progress call
    assert row.wait() == 0 and row.toasts == ['Download cancelled'] and not row.flashed
    assert card.files[TAR] == b'old' and PART not in card.files


def test_leaving_the_page_mid_download_cancels_it_and_keeps_the_part(fw):
    card = slot(fw, {TAR: b'old'})
    row = UpdateRow(fw)
    row.press()
    Net(fw, replies(), during=lambda k: k == 1 and row.leave(), only=GET)
    row.run()
    assert row.wait() == 0 and row.toasts == ['Download cancelled'] and row.tr == {} and not row.flashed
    assert card.files[TAR] == b'old' and card.files[PART] == BODY[:3 * len(BODY) // CHUNKS]  # ended at the next progress call


def test_a_press_carries_on_from_the_part_and_says_how_far_the_whole_is(fw):
    card = slot(fw, {TAR: b'old', PART: BODY[:HALF]})
    row = UpdateRow(fw)
    row.press()
    seen = []
    net = Net(fw, replies(), during=lambda k: seen.append(row.wait() and row.label()), only=GET)
    row.run()
    base = (HALF >> 10) * 100 // ((len(BODY) >> 10) + 1)  # the half it began with
    assert net.froms[-1] == HALF and seen == [f'Downloading {base + p * (100 - base) // 100}%' for p in (25, 50, 75, 100)]
    assert 48 <= base <= 50 and row.wait() == 0 and row.flashed == 1 and card.files[TAR] == BODY


def test_the_row_says_how_far_it_was_and_then_that_it_is_reconnecting(fw):
    card = slot(fw, {PART: BODY[:HALF]})
    row = UpdateRow(fw)
    row.press()
    seen = []
    fw.mock('fw_usleep', lambda us, *_: seen.append(row.wait() and row.label()))
    Net(fw, replies(asset=[(7, 0, None), (0, 200, None, BODY)]))
    row.run()
    assert set(seen) == {'Reconnecting…'} and len(seen) == 50 and card.files[TAR] == BODY


@pytest.mark.parametrize('how', ['press', 'leave'])
def test_a_cancel_after_the_last_byte_still_keeps_the_old_tar(fw, how):
    card = slot(fw, {TAR: b'old'})
    row = UpdateRow(fw)
    row.press()
    Net(fw, replies(), during=lambda k: k == CHUNKS - 1 and getattr(row, how)(), only=GET)
    row.run()
    assert card.written[PART] == BODY  # no progress call left to end it: the transfer itself came whole
    assert row.wait() == 0 and row.toasts == ['Download cancelled'] and not row.flashed
    assert card.files[TAR] == b'old' and not card.of('rename')
    assert card.files.get(PART) == (BODY if how == 'leave' else None)  # left: there for the next press


def test_a_part_the_card_can_t_sync_never_replaces_the_tar(fw):
    card = slot(fw, {TAR: b'old'})
    fw.mock('fw_fsync', lambda fd, *_: -1 if card.names[fd - 100] == PART else 0)
    row = UpdateRow(fw)
    row.press()
    Net(fw, replies())
    row.run()
    assert row.wait() == 0 and row.toasts == ["Can't write to the card"] and not row.flashed
    assert card.files[TAR] == b'old' and PART not in card.files and not card.of('rename')


def test_a_download_finished_off_the_page_waits_for_the_card_update(fw):
    card = slot(fw)
    row = UpdateRow(fw)
    row.press()
    Net(fw, replies())
    row.run()
    row.leave()
    assert row.wait() == 0 and row.toasts == ['Update downloaded: use TF card update'] and not row.flashed
    assert card.files[TAR] == BODY


@pytest.mark.parametrize('answers, said', [
    ({LATEST: (0, 302, TAG_URL + TAG)}, 'Latest firmware installed'),
    ({LATEST: (6, 0, None)}, "Can't reach GitHub"),  # CURLE_COULDNT_RESOLVE_HOST
    ({LATEST: (0, 200, None)}, "Can't reach GitHub"),  # no redirect
    (replies(api=(0, 403, None, b'{}')), 'GitHub is busy, try later'),  # its API's hourly limit
    (replies(api=(0, 200, None, github(NEWER.decode(), ('update.tar', len(BODY))))), 'No update for this build'),  # no digest
    (replies(asset=(0, 404, None, b'Not Found')), 'No update for this build'),
    (replies(asset=(28, 0, None)), "Can't reach GitHub"),  # timed out, each try
    (replies(asset=(0, 200, None, BODY[:-1] + b'!')), 'The download was damaged, try again'),
])
def test_each_ending_says_why_and_keeps_the_old_tar(fw, answers, said):
    card = slot(fw, {TAR: b'old'})
    row = UpdateRow(fw)
    row.press()
    Net(fw, answers)
    row.run()
    assert row.wait() == 0 and row.toasts == [said] and not row.flashed and row.tr == {row.LABEL: 'update_online'}
    assert card.files[TAR] == b'old' and not card.files.get(PART)


def test_a_card_without_the_room_says_how_much_more(fw):
    card = slot(fw, {TAR: b'old'}, free=RESERVE)
    row = UpdateRow(fw)
    row.press()
    net = Net(fw, replies())
    row.run()
    assert row.wait() == 0 and row.toasts == ['Needs 1 MB more space'] and ASSET not in net.urls  # 2 x 256 KB short
    assert card.files[TAR] == b'old' and PART not in card.written


def test_a_full_card_fails_the_write_and_says_so(fw):
    card = slot(fw, {TAR: b'old'})
    def fwrite(buf, size, n, f, *_):
        if card.names[f] == PART:
            return 0
        card.written[card.names[f]] += C.string_at(buf, size * n)
        return n
    fw.mock('fw_fwrite', fwrite)
    fw.mock('fw_ferror', lambda f, *_: card.names[f] == PART)
    row = UpdateRow(fw)
    row.press()
    net = Net(fw, replies())
    row.run()
    assert net.urls == [LATEST, API + NEWER, ASSET] and row.wait() == 0 and row.toasts == ["Can't write to the card"]
    assert card.files[TAR] == b'old' and PART not in card.files


@pytest.mark.parametrize('battery, cable, starts', [(15, 0, 0), (16, 0, 1), (0, 1, 1), (100, 0, 1)])
def test_a_battery_stock_s_updater_would_refuse_downloads_nothing(fw, battery, cable, starts):
    """Under 16% off a charger stock's own check ends the card update: said before the download, not after it."""
    row = UpdateRow(fw, battery=battery, cable=cable)
    row.press()
    assert len(row.workers) == starts and row.toasts == ([] if starts else ['Charge the battery first'])
    assert row.nu.state == (NU_RUNNING if starts else NU_IDLE)


def test_a_press_without_wifi_or_a_thread_starts_nothing(fw):
    row = UpdateRow(fw, wifi=0)
    row.press()
    assert row.toasts == ['Connect to Wi-Fi first'] and not row.workers and row.nu.state == NU_IDLE
    row = UpdateRow(fw, thread=11)
    row.press()
    assert row.toasts == ["Can't start the download"] and not row.timers and row.nu.state == NU_IDLE
    row.press()  # idle again: a press tries again
    assert row.toasts[-1] == "Can't start the download"


def test_presses_between_the_end_and_the_join_do_nothing(fw):
    Disk(fw)
    row = UpdateRow(fw)
    row.press()
    Net(fw, {LATEST: (0, 302, TAG_URL + TAG)})
    row.run()
    row.press()
    assert not row.workers and row.toasts == [] and row.nu.state == NU_LATEST and len(row.timers) == 1
    assert row.wait() == 0 and row.toasts == ['Latest firmware installed']


def test_system_update_opened_mid_download_says_so(fw):
    fw.var('netupdate__nu', Nu).state = NU_RUNNING
    row = UpdateRow(fw)
    assert row.label() == 'Downloading…'


# The App Store.

def app_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


FOO = {'.foo/q2-foo': b'\x7fELF foo', '.foo/q2app': b'name=Foo\n', '.foo/lib/x.so': b'lib', '.foo/._q2app': b'macOS',
       'INSTALL.txt': b'read me', '__MACOSX/.foo/._q2-foo': b'macOS'}


def store_net(fw, files=FOO, listed=b'me/q2-foo Foo\n', size=None, **replies):
    """The list, every release's JSON (tag 2.0, its zip of files) and the zip; api= or zip= replace those replies."""
    data = app_zip(files)
    return Net(fw, {b'https://raw': (0, 200, None, listed),
                    b'https://api': replies.get('api', (0, 200, None, github('2.0', ('app.zip', size or len(data))))),
                    b'https://h/': replies.get('zip', (0, 200, None, data))})


class Nc(C.Structure):
    _fields_ = [('at', C.c_uint), ('polled', C.c_uint), ('busy', C.c_int), ('ok', C.c_int), ('read', C.c_int),
                ('tag', C.c_char * 16), ('told', C.c_char * 16)]


TOLD = b'/mnt/data/q2-update.told'
NOTICE = f'Q2 Pod {NEWER.decode()} is out: System Update → Network update'
HOUR, DAY = 60 * 60 * 1000, 24 * 60 * 60 * 1000
NU_POLL = 2000  # netupdate.c's NU_POLL_MS


class Poll:
    """power_poll's netupdate_poll at self.ms: the top window (by name, None for none), the screen, Wi-Fi, the
    check's thread (run by run()) and the toasts stubbed; the told tag on card."""
    def __init__(self, fw, card, top=b'home_page', lit=1, wifi=1):
        self.fw, self.card, self.wifi, self.ms, self.workers, self.toasts = fw, card, wifi, 1000, [], []
        self.top(top)
        self.lit = fw.var('g_backlight_status', C.c_ubyte)
        self.lit.value = lit
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.mock('window_manager_get_top_window', lambda *_: C.addressof(self.name) if self.name else 0)
        fw.mock('widget_get_prop_str', lambda w, key, default, *_: w if self.name and w == C.addressof(self.name) else default)  # the top window here is its name
        fw.mock('get_wifisignal', lambda *_: self.wifi)
        fw.mock('fw_pthread_create', lambda t, a, fn, arg, *_: self.workers.append(fn) or 0)
        fw.mock('fw_pthread_detach', lambda *_: 0)
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        self.nc = fw.var('netupdate__nc', Nc)

    def top(self, name):
        self.name = C.create_string_buffer(name) if name else None

    def poll(self, after=NU_POLL):
        self.ms += after
        self.fw.fn('netupdate_poll', None)()

    def run(self):
        assert C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.workers.pop())(None) is None



def test_a_newer_release_is_told_once_on_home(fw):
    card = slot(fw)
    p = Poll(fw, card)
    net = Net(fw, {LATEST: (0, 302, TAG_URL + NEWER)})
    p.poll()
    assert len(p.workers) == 1 and p.nc.busy and p.toasts == []
    p.poll()  # while it checks: nothing more
    assert len(p.workers) == 1
    p.run()
    assert net.urls == [LATEST] and net.opts[44] == 1 and 52 not in net.opts  # one header-only request, no API
    assert p.nc.tag == NEWER and p.nc.ok and not p.nc.busy
    p.poll()
    assert p.toasts == [NOTICE] and card.files[TOLD][:len(NEWER) + 1] == NEWER + b'\0'
    for _ in range(3):
        p.poll()
    assert p.toasts == [NOTICE] and not p.workers  # once, and no new check within the day


def remind_daily_off(fw):
    """[Q2POD] REMINDDAILY=0 in config.ini; every read of it counted."""
    reads = []
    def read(path, section, key, out, default, *_):
        reads.append(C.string_at(key))
        if C.string_at(key) == b'REMINDDAILY':
            C.memmove(out, b'0\0', 2)
    fw.mock('toolsReadConfig', read)
    return reads


def test_remind_daily_off_checks_nothing_tells_nothing_and_takes_back_the_tag(fw):
    reads = remind_daily_off(fw)
    card = slot(fw)
    p = Poll(fw, card)
    net = Net(fw, {LATEST: (0, 302, TAG_URL + NEWER)})
    p.nc.tag, p.nc.at = NEWER, 1  # found while it was On
    for _ in range(3):
        p.poll(DAY)
    assert p.workers == [] and net.urls == [] and p.toasts == [] and TOLD not in card.files
    assert p.nc.tag == b'' and not p.nc.busy and not p.nc.polled and not p.nc.at  # On again: checks at once
    assert reads.count(b'REMINDDAILY') == 1  # read once, not at every poll


def test_remind_daily_off_leaves_the_tag_to_a_check_thread_that_owns_it(fw):
    remind_daily_off(fw)
    p = Poll(fw, slot(fw))
    p.nc.tag, p.nc.busy = NEWER, 1
    p.poll()
    assert p.nc.tag == NEWER and p.workers == []
    p.nc.busy = 0
    p.poll()
    assert p.nc.tag == b''


def test_a_release_told_before_a_restart_isn_t_told_again(fw):
    card = slot(fw, {TOLD: NEWER.ljust(16, b'\0')})
    p = Poll(fw, card)
    Net(fw, {LATEST: (0, 302, TAG_URL + NEWER)})
    p.poll()
    p.run()
    p.poll()
    p.poll()
    assert p.toasts == [] and p.nc.tag == NEWER  # still the row's
    # the next release is told
    _n = int(NEWER.replace(b'.', b'')) + 1
    later = b'%d.%d.%d' % (_n // 100, _n // 10 % 10, _n % 10)
    Net(fw, {LATEST: (0, 302, TAG_URL + later)})
    p.poll(DAY)
    p.run()
    p.poll()
    assert p.toasts == [NOTICE.replace(NEWER.decode(), later.decode())] and card.files[TOLD][:len(later)] == later


@pytest.mark.parametrize('tag', [TAG, OLDER])
def test_this_build_s_release_or_an_older_one_is_no_notice(fw, tag):
    p = Poll(fw, slot(fw))
    Net(fw, {LATEST: (0, 302, TAG_URL + tag)})
    p.poll()
    p.run()
    p.poll()
    assert p.nc.tag == b'' and p.nc.ok and p.toasts == []


def test_it_waits_for_home_with_the_screen_on(fw):
    card = slot(fw)
    p = Poll(fw, card, top=b'playing_page')
    Net(fw, {LATEST: (0, 302, TAG_URL + NEWER)})
    p.poll()
    p.run()
    p.poll()
    p.top(None)
    p.poll()
    p.top(b'home_page')
    p.lit.value = 0
    p.poll()
    assert p.toasts == [] and TOLD not in card.files
    p.lit.value = 1
    p.poll()
    assert p.toasts == [NOTICE]


def test_no_check_without_wi_fi_or_while_a_press_downloads(fw):
    p = Poll(fw, slot(fw), wifi=-1)
    p.poll()
    assert not p.workers and p.nc.at == 0  # Smart Wi-Fi's radio isn't woken: checked once Wi-Fi is on
    p.wifi = 2
    nu = fw.var('netupdate__nu', Nu)
    nu.state = NU_RUNNING
    p.poll()
    assert not p.workers
    nu.state = NU_IDLE
    p.poll()
    assert len(p.workers) == 1


def test_polls_are_two_seconds_apart(fw):
    p = Poll(fw, slot(fw), wifi=-1)
    p.poll()
    calls = len(fw.calls('get_wifisignal'))
    p.poll(NU_POLL - 1)
    assert len(fw.calls('get_wifisignal')) == calls
    p.wifi = 1
    p.poll(1)
    assert len(fw.calls('get_wifisignal')) == calls + 1 and len(p.workers) == 1


@pytest.mark.parametrize('reply', [(6, 0, None), (0, 200, None), (0, 302, None), (0, 404, None)])
def test_a_check_that_fails_is_tried_an_hour_later_one_that_worked_a_day(fw, reply):
    p = Poll(fw, slot(fw), top=b'playing_page')
    net = Net(fw, {LATEST: reply})
    p.poll()
    p.run()
    assert not p.nc.ok and p.nc.tag == b''
    p.poll(HOUR - NU_POLL - 1)
    assert not p.workers
    p.poll(NU_POLL + 1)
    assert len(p.workers) == 1
    net.replies[LATEST] = (0, 302, TAG_URL + NEWER)
    p.run()
    assert p.nc.ok and p.nc.tag == NEWER
    p.poll(DAY - NU_POLL)  # a day from the check's start
    assert not p.workers
    p.poll(NU_POLL)
    assert len(p.workers) == 1


def test_a_failed_check_keeps_the_release_found_before(fw):
    p = Poll(fw, slot(fw), top=b'playing_page')
    net = Net(fw, {LATEST: (0, 302, TAG_URL + NEWER)})
    p.poll()
    p.run()
    net.replies[LATEST] = (6, 0, None)
    p.poll(DAY)
    p.run()
    assert not p.nc.ok and p.nc.tag == NEWER


def test_system_update_s_row_names_the_newer_release_until_a_press_finds_none(fw):
    p = Poll(fw, slot(fw), top=b'playing_page')
    Net(fw, {LATEST: (0, 302, TAG_URL + NEWER)})
    p.poll()
    p.run()
    row = UpdateRow(fw)
    assert row.label() == f'Update to {NEWER.decode()}'
    Net(fw, {LATEST: (0, 302, TAG_URL + TAG)})
    row.press()
    row.run()
    assert row.wait() == 0 and row.toasts == ['Latest firmware installed']
    assert p.nc.tag == b'' and row.tr[row.LABEL] == 'update_online'


def test_without_a_newer_release_the_row_keeps_its_name(fw):
    row = UpdateRow(fw)
    assert row.label() is None and row.tr == {}  # stock's own, untouched



class Store:
    """The App Store opened for real on a Disk, on top while open: each row's detail, the toasts, the wait timer, the
    worker, the standby holds, the clock (now_ms), the card's free bytes (statfs) and the card mounted (/proc/mounts,
    card_mounted). Row 0 is Check for Updates, the list's apps under it."""
    def __init__(self, fw, card, wifi=1, thread=0, free=1 << 30):
        self.fw, self.card, self.wifi, self.free, self.seconds = fw, card, wifi, free, 5000
        self.text, self.toasts, self.timers, self.workers, self.held = {}, [], [], [], []
        fw.mock('get_wifisignal', lambda *_: self.wifi)
        fw.mock('window_create', lambda *_: 77)
        fw.mock('window_manager_get_top_window', lambda *_: 77)
        fw.mock('widget_get_child', lambda row, i, *_: row)
        fw.mock('list_item_create', lambda view, x, y, *_: 1000 + y)
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append(fn) or len(self.timers))
        fw.mock('fw_pthread_create', lambda t, a, fn, arg, *_: thread or self.workers.append(fn) or 0)
        fw.mock('reset_poweroptions_timer', lambda *args: self.held.append(args[:3]))
        fw.mock('fw_clock_gettime', self.clock)
        fw.mock('fw_statfs', self.statfs)
        card.files[MOUNTS] = b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'
        self.sh = fw.var('netupdate__sh', Sh)
        self.open()

    def clock(self, kind, ts, *_):
        C.c_long.from_address(ts).value, C.c_long.from_address(ts + 8).value = self.seconds, 0

    def statfs(self, path, buf, *_):
        words = C.cast(buf, C.POINTER(C.c_uint))
        words[1], words[7] = 4096, self.free // 4096

    def open(self):
        self.fw.fn('shelf_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    def close(self):
        self.fw.fn('netupdate__sh_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    def wait(self):
        """sh_wait, as its timer runs it."""
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(self.timers[-1])(None)

    def run(self):
        """The job's worker to its end, then the wait that joins it."""
        assert C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.workers.pop())(None) is None
        return self.wait()

    def click(self, r=1):
        self.fw.fn('netupdate__sh_click', C.c_int, C.c_void_p, C.c_void_p)(r, None)

    def check_label(self):
        return self.text.get(self.sh.row[0])

    def labels(self):
        """The apps' rows, under Check for Updates."""
        assert self.sh.map[0] == ROW_CHECK
        return [self.text.get(self.sh.row[r]) for r in range(1, self.sh.rows)]


def test_the_store_loads_its_list_then_not_again_for_ten_minutes(fw):
    card = Disk(fw)
    net = store_net(fw)
    s = Store(fw, card)
    assert s.sh.job == JOB_CHECK and s.labels() == [] and s.check_label() == 'Checking…'
    s.click(0)  # Check for Updates while the list loads: no second check, the one running answers it
    assert s.toasts == [] and s.sh.cancel == 0 and s.sh.manual == 1 and len(s.workers) == 1
    assert s.wait() == RET_REPEAT and s.held == []  # a check holds no standby
    assert s.run() == 0 and s.sh.job == 0 and s.toasts == ['All apps are up to date']  # none installed has a newer one
    assert s.labels() == ['Install'] and card.files[SOURCES] == b'me/q2-foo Foo\n' and s.sh.next[0].latest == b'2.0'
    assert s.check_label().strip() == ''  # its detail blank again, the list loaded
    assert net.urls == [b'https://raw.githubusercontent.com/DiamondBond/q2-pod/main/sources.list',
                        b'https://api.github.com/repos/me/q2-foo/releases/latest']
    s.close()
    s.seconds += 600
    s.open()
    assert not s.workers and s.sh.job == 0  # checked ten minutes ago: not yet
    s.close()
    s.seconds += 1
    s.open()
    assert s.sh.job == JOB_CHECK and len(s.workers) == 1


def test_a_check_stopped_part_way_is_done_again_at_the_next_open(fw):
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    net = store_net(fw)
    s = Store(fw, card)
    s.click()  # an app's row
    assert s.sh.cancel == 1 and s.toasts == ['Stopped checking for updates']
    assert s.run() == 0 and net.urls[-1].startswith(b'https://raw')  # the list, then no release
    assert s.toasts == ['Stopped checking for updates']  # and nothing said at its end
    s.close()
    s.seconds += 1
    s.open()
    assert s.sh.job == JOB_CHECK


# An install goes by the list as it is now, fetched as the install starts. The page's rows may be the card's
# list of days ago, and a repo dropped since then must not install (GHSA-8wpr-pmqx-538x): on that list its
# release was fetched and unpacked onto the card in each of the three ways below.
LIST_URL = b'https://raw.githubusercontent.com/DiamondBond/q2-pod/main/sources.list'
FOO_APP = b'/mnt/mmc/.foo/q2-foo'


def test_an_app_dropped_since_the_cards_list_is_not_installed_with_no_check_run(fw):
    """Opened with no Wi-Fi: no check runs and the rows are the card's list. The link comes up and the row is pressed."""
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    net = store_net(fw, listed=b'me/q2-bar Bar\n')
    s = Store(fw, card, wifi=0)
    assert s.sh.job == 0 and s.labels() == ['Install']
    s.wifi = 1
    s.click()
    assert s.sh.job == JOB_INSTALL and s.check_label().strip() == '' and s.labels() == ['Checking…']
    assert s.run() == 0 and s.toasts == ['No update for this build']
    assert net.urls == [LIST_URL] and FOO_APP not in card.files and not card.of('mkdir')


def test_an_app_dropped_since_the_cards_list_is_not_installed_after_the_check_is_stopped(fw):
    """A row pressed while the opening check loads the list stops the check; the next press acts on the card's list."""
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    net = store_net(fw, listed=b'me/q2-bar Bar\n')
    s = Store(fw, card)
    s.click()
    assert s.run() == 0 and s.labels() == ['Install']
    s.click()
    assert s.sh.job == JOB_INSTALL and s.run() == 0 and s.toasts[-1] == 'No update for this build'
    assert not [u for u in net.urls if not u.startswith(b'https://raw')] and FOO_APP not in card.files


def test_no_app_installs_while_the_list_cant_be_fetched(fw):
    """The list's host out of reach with the API answering: the check keeps the card's list and asks its releases."""
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    net = store_net(fw)
    net.replies[b'https://raw'] = (7, 0, None)
    s = Store(fw, card)
    assert s.run() == 0 and s.labels() == ['Install'] and s.sh.next[0].latest == b'2.0'
    s.click()
    assert s.run() == 0 and s.toasts[-1] == "Can't reach GitHub"
    assert not [u for u in net.urls if u.startswith(b'https://h/')] and FOO_APP not in card.files


@pytest.mark.parametrize('listed', [b'other/q2-foo Foo\n', b'-me/q2-foo Foo\n', b'# me/q2-foo Foo\nme/q2-bar Bar\n'])
@pytest.mark.refusal('addon:changed')
def test_an_app_installs_only_from_the_repo_the_list_names_now(fw, listed):
    """Another owner's repo under the same name, the app marked dropped, and its line commented out."""
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    net = store_net(fw, listed=listed)
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    s.click()
    assert s.run() == 0 and s.toasts == ['No update for this build']
    assert net.urls == [LIST_URL] and FOO_APP not in card.files


def test_an_install_asks_for_the_list_then_the_release_and_its_zip(fw):
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    net = store_net(fw, listed=b'# apps\nme/q2-bar Bar\nme/q2-foo Foo renamed\n')
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    s.click()
    assert s.run() == 0 and s.toasts == ['Foo is ready in Streaming'] and card.files[FOO_APP] == FOO['.foo/q2-foo']
    assert [u.split(b'/')[2] for u in net.urls] == [b'raw.githubusercontent.com', b'api.github.com', b'h']
    assert card.files[SOURCES] == b'me/q2-foo Foo\n' and s.labels() == ['Installed']  # the page's list is the check's to change


def test_an_install_cancelled_while_it_asks_for_the_list_installs_nothing(fw):
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    net = store_net(fw)
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    s.click()
    s.click()  # the same row again, before the list is in
    assert s.sh.cancel == 1 and s.run() == 0 and s.toasts == ['Download cancelled']
    assert net.urls == [LIST_URL] and FOO_APP not in card.files


@pytest.mark.parametrize('q2app, where', [(b'name=Foo\n', 'Streaming'), (b'name=Foo\ngame=1\n', "Library's Games")])
def test_an_app_installs_into_place_and_removes_without_a_trace(fw, q2app, where):
    card = Disk(fw)
    store_net(fw, {**FOO, '.foo/q2app': q2app})
    s = Store(fw, card)
    s.run()

    def on_card():  # addon.c's scan writes its /tmp caps file: only the card counts
        return {k: v for k, v in card.files.items() if k.startswith(b'/mnt/mmc/')}
    before = on_card()
    s.click()
    assert s.sh.job == JOB_INSTALL and s.labels() == ['Checking…']
    assert s.wait() == RET_REPEAT and s.held == [(1, 1, 0)]  # standby and power-off held through a job
    assert s.run() == 0 and s.toasts == [f'Foo is ready in {where}'] and s.labels() == ['Installed']
    placed = {k: v for k, v in card.files.items() if k.startswith(b'/mnt/mmc/.foo/')}
    assert placed == {b'/mnt/mmc/.foo/q2-foo': b'\x7fELF foo', b'/mnt/mmc/.foo/q2app': q2app, b'/mnt/mmc/.foo/lib/x.so': b'lib'}
    assert card.files[b'/mnt/mmc/.q2/apps/foo.files'] == b''.join(sorted(k[len(b'/mnt/mmc/'):] + b'\n' for k in placed))
    assert card.files[b'/mnt/mmc/.q2/apps/foo.tag'] == b'2.0'
    assert card.moves(b'/mnt/mmc/.foo/')[-1] == b'/mnt/mmc/.foo/q2-foo'  # the binary last
    assert not [k for k in card.files if b'staging' in k or b'INSTALL' in k or b'MACOSX' in k]
    s.click()
    assert s.labels() == ['Press again to remove'] and not s.workers
    s.click()
    assert s.labels() == ['Removing…']
    assert s.run() == 0 and s.toasts[-1] == 'Foo removed' and s.labels() == ['Install']
    assert on_card() == before


# A remove leaves no login behind, even from an app that saves it on its TERM, after 3 s too.
@pytest.mark.parametrize('save_ms', [1000, 3500])
def test_a_remove_leaves_nothing_an_app_saves_on_its_way_out(fw, save_ms):
    card = Disk(fw)
    store_net(fw)
    s = Store(fw, card)
    s.run()
    s.click()
    assert s.run() == 0 and s.toasts == ['Foo is ready in Streaming']
    card.files[b'/tmp/q2-foo'] = b'4321'  # its loop's pid: running
    now, term = [s.seconds * 1000], []

    def app_saves():  # its TERM handler: the login written back, then the app and its loop gone
        if term and now[0] - term[0] >= save_ms and b'/tmp/q2-foo' in card.files:
            card.files[b'/mnt/mmc/.foo/login'] = b'token'
            del card.files[b'/tmp/q2-foo']

    def clock(kind, ts, *_):
        C.c_long.from_address(ts).value, C.c_long.from_address(ts + 8).value = now[0] // 1000, now[0] % 1000 * 1000000

    def usleep(us, *_):
        now[0] += us // 1000
        app_saves()

    def system(cmd):
        if cmd.startswith(b'kill $(cat /tmp/q2-foo)'):
            term.append(now[0])
        return card.shell(cmd)
    card.system = system
    fw.mock('fw_clock_gettime', clock)
    fw.mock('fw_usleep', usleep)
    s.click()
    s.click()
    assert s.labels() == ['Removing…']
    assert s.run() == 0 and s.toasts[-1] == 'Foo removed' and term
    now[0] = term[0] + 60000  # the app's own time runs on after the remove
    app_saves()
    assert not [k for k in card.files if k.startswith(b'/mnt/mmc/.foo')]


# USB storage started during a remove's wait for the app: the card is gone, so nothing is removed and it says so, not
# "Foo removed" with the app still there to start again once the card is back.
def test_a_remove_cut_off_by_usb_storage_says_the_card_was_not_written(fw):
    card = Disk(fw)
    store_net(fw)
    s = Store(fw, card)
    s.run()
    s.click()
    assert s.run() == 0 and s.toasts == ['Foo is ready in Streaming']
    card.files[b'/tmp/q2-foo'] = b'4321'  # its loop's pid: running
    installed = {k for k in card.files if k.startswith(b'/mnt/mmc/')}
    card.log.clear()

    def usleep(*_):  # during the wait: USB storage takes the card, and the app ends
        card.files[MOUNTS] = b'/dev/root / squashfs ro 0 0\n'
        card.files.pop(b'/tmp/q2-foo', None)
    fw.mock('fw_usleep', usleep)
    s.click()
    s.click()
    assert s.run() == 0 and s.toasts[-1] == "Can't write to the card" and s.labels() == ['Installed']
    assert installed <= set(card.files) and not card.of('unlink') and not card.of('rmdir')


# The same for Rockbox: its themes go with it only once the card is known to be there.
def test_a_rockbox_remove_cut_off_by_usb_storage_keeps_the_themes(fw):
    card = Disk(fw, {SOURCES: b'x/q2-rockbox Rockbox\nx/q2-rockbox-themes Rockbox Themes\n',
                     b'/mnt/mmc/.rockbox/rockbox': b'bin', b'/mnt/mmc/.rockbox/wps/a.wps': b'wps',
                     b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/rockbox\n', b'/mnt/mmc/.q2/apps/rockbox.tag': b'2.0',
                     b'/mnt/mmc/.q2/apps/themes.files': b'.rockbox/wps/a.wps\n', b'/mnt/mmc/.q2/apps/themes.tag': b'2.0'})
    store_net(fw, {'.rockbox/wps/a.wps': b'wps'})
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    card.files[b'/tmp/q2-rockbox'] = b'4321'  # running
    card.log.clear()

    def usleep(*_):  # during the wait: USB storage takes the card, and the app ends
        card.files[MOUNTS] = b'/dev/root / squashfs ro 0 0\n'
        card.files.pop(b'/tmp/q2-rockbox', None)
    fw.mock('fw_usleep', usleep)
    s.click()
    s.click()
    assert s.run() == 0 and s.toasts[-1] == "Can't write to the card"
    assert not card.of('unlink') and b'/mnt/mmc/.rockbox/wps/a.wps' in card.files


def test_a_cancel_during_the_release_request_downloads_nothing(fw):
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    net = store_net(fw)
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    net.during = lambda k: net.urls[-1].startswith(b'https://api') and k == CHUNKS - 1 and s.click()  # as it ends
    s.click()
    assert s.run() == 0 and s.toasts == ['Download cancelled'] and s.labels() == ['Install']
    assert not [u for u in net.urls if u.startswith(b'https://h/')] and sorted(card.files) == [SOURCES, MOUNTS]


def test_a_cancel_while_unpacking_installs_nothing(fw):
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    store_net(fw)
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    shell, seen = card.shell, []

    def system(cmd):
        if cmd.startswith(b'busybox unzip -l'):  # the download checked: pressed as the row says so
            s.wait()
            seen.append(s.labels())
            s.click()
            seen.append(s.labels())
        return shell(cmd)
    card.system = system
    s.click()
    assert s.run() == 0 and seen == [['Unpacking…'], ['Cancelling…']]
    assert s.toasts == ['Download cancelled'] and s.labels() == ['Install']
    assert not card.moves(b'/mnt/mmc/.foo/') and sorted(card.files) == [SOURCES, MOUNTS]  # nothing placed, no staging left


ROOM = 64 << 20  # left free beyond what a download or an unpack needs


@pytest.mark.parametrize('case, said', [
    (dict(api=(0, 404, None, b'{}')), 'No update for this build'),
    (dict(api=(0, 403, None, b'{}')), 'GitHub is busy, try later'),
    (dict(api=(0, 429, None, b'{}')), 'GitHub is busy, try later'),
    (dict(api=(7, 0, None)), "Can't reach GitHub"),
    (dict(zip=(0, 404, None, b'Not Found')), 'No update for this build'),
    (dict(zip=(28, 200, None, b'PK partial')), "Can't reach GitHub"),
    (dict(files={'INSTALL.txt': b'x', 'other/q2-foo': b'y'}), 'The download is not a usable app'),
    (dict(files={**FOO, '.foo/../x': b'up'}), 'The download is not a usable app'),
    (dict(unpack=1), "Can't write to the card"),
    (dict(free=ROOM, size=1), 'Needs 1 MB more space'),  # no room for the zip
    (dict(free=ROOM + (1 << 20), files={'.foo/q2-foo': bytes(8 << 20)}), 'Needs 7 MB more space'),  # nor for what it unpacks to
])
def test_an_install_that_fails_says_why_and_leaves_the_card_alone(fw, case, said):
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    card.unpack_fails = case.get('unpack', 0)
    store_net(fw, case.get('files', FOO), **{k: v for k, v in case.items() if k in ('api', 'zip', 'size')})
    s = Store(fw, card, wifi=0, free=case.get('free', 1 << 30))
    s.wifi = 1
    s.click()
    assert s.run() == 0 and s.toasts == [said] and s.labels() == ['Install']
    assert sorted(card.files) == [SOURCES, MOUNTS]


def test_rockbox_installs_on_a_second_press_keeping_its_config(fw):
    card = Disk(fw, {SOURCES: b'x/q2-rockbox Rockbox\n', b'/mnt/mmc/.rockbox/config.cfg': b'mine'})
    store_net(fw, {'.rockbox/rockbox': b'bin', '.rockbox/config.cfg': b'defaults', '.rockbox/fonts/a.fnt': b'font'},
              listed=b'x/q2-rockbox Rockbox\n')
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    s.click()
    label, = s.labels()
    assert label.endswith('Press again to install') and not s.workers
    s.click()
    assert s.run() == 0 and s.toasts == ['Rockbox is ready. Find it on Home'] and s.labels() == ['Installed']
    assert card.files[b'/mnt/mmc/.rockbox/config.cfg'] == b'mine' and card.files[b'/mnt/mmc/.rockbox/rockbox'] == b'bin'
    assert card.moves(b'/mnt/mmc/.rockbox/') == [b'/mnt/mmc/.rockbox/fonts/a.fnt', b'/mnt/mmc/.rockbox/rockbox']


def test_the_themes_come_with_rockbox_and_go_before_it(fw):
    card = Disk(fw, {SOURCES: b'x/q2-rockbox Rockbox\nx/q2-rockbox-themes Rockbox Themes\n',
                     b'/mnt/mmc/.rockbox/rockbox': b'bin', b'/mnt/mmc/.rockbox/fonts/a.fnt': b'font',
                     b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/rockbox\n.rockbox/fonts/a.fnt\n',
                     b'/mnt/mmc/.q2/apps/rockbox.tag': b'2.0'})
    store_net(fw, {'.rockbox/wps/a.wps': b'wps', '.rockbox/fonts/a.fnt': b'font'}, listed=card.files[SOURCES])
    s = Store(fw, card, wifi=0)
    assert s.labels() == ['Installed', 'Install']
    s.wifi = 1
    s.click(2)
    assert s.run() == 0 and s.toasts == ["Rockbox Themes are ready in Rockbox's Theme Settings"]
    assert s.labels() == ['Installed', 'Installed'] and card.files[b'/mnt/mmc/.rockbox/wps/a.wps'] == b'wps'
    s.click(1)
    s.click(1)
    assert s.run() == 0 and s.toasts[-1] == 'Rockbox removed'
    gone = [e[1] for e in card.log if e[0] == 'unlink']
    assert gone.index(b'/mnt/mmc/.rockbox/wps/a.wps') < gone.index(b'/mnt/mmc/.rockbox/rockbox')  # the themes first
    # both apps' files, lists and tags gone: the card holds its list alone
    assert sorted(k for k in card.files if k.startswith(b'/mnt/mmc/')) == [SOURCES]
    assert s.labels() == ['Install']  # and the themes' row with Rockbox


def test_another_app_s_remove_leaves_rockbox_and_its_themes(fw):
    rockbox = {b'/mnt/mmc/.rockbox/rockbox': b'bin', b'/mnt/mmc/.rockbox/wps/a.wps': b'wps',
               b'/mnt/mmc/.q2/apps/rockbox.files': b'.rockbox/rockbox\n', b'/mnt/mmc/.q2/apps/rockbox.tag': b'2.0',
               b'/mnt/mmc/.q2/apps/themes.files': b'.rockbox/wps/a.wps\n', b'/mnt/mmc/.q2/apps/themes.tag': b'2.0'}
    card = Disk(fw, {SOURCES: b'x/q2-rockbox Rockbox\nx/q2-rockbox-themes Rockbox Themes\nme/q2-foo Foo\n', **rockbox,
                     FOO_APP: b'bin', b'/mnt/mmc/.q2/apps/foo.files': b'.foo/q2-foo\n', b'/mnt/mmc/.q2/apps/foo.tag': b'2.0'})
    s = Store(fw, card, wifi=0)
    assert s.labels() == ['Installed', 'Installed', 'Installed']
    s.click(3)
    s.click(3)
    assert s.run() == 0 and s.toasts[-1] == 'Foo removed' and FOO_APP not in card.files
    assert {path: card.files.get(path) for path in rockbox} == rockbox  # the themes go with Rockbox alone
    assert s.labels() == ['Installed', 'Installed', 'Install']


def test_the_themes_pressed_once_rockbox_left_the_card_ask_for_it(fw):
    card = Disk(fw, {SOURCES: b'x/q2-rockbox Rockbox\nx/q2-rockbox-themes Rockbox Themes\n', b'/mnt/mmc/.rockbox/rockbox': b'bin'})
    s = Store(fw, card, wifi=0)
    assert len(s.labels()) == 2  # Rockbox and its themes
    del card.files[b'/mnt/mmc/.rockbox/rockbox']
    s.wifi = 1
    s.click(2)
    assert s.toasts == ['Install Rockbox first'] and s.sh.job == 0


def test_spotify_installs_with_librespot_stopped_and_its_cache_kept(fw):
    kill = b'kill -9 $(cat /tmp/q2-librespot)'
    card = Disk(fw, {SOURCES: b'x/q2-librespot Spotify\n', b'/mnt/mmc/.spotify/cache/x': b'mine', b'/mnt/mmc/.spotify/.cache-size': b'2G'})
    store_net(fw, {'.spotify/librespot': b'bin', '.spotify/run': b'sh', '.spotify/cache/x': b'theirs', '.spotify/.cache-size': b'0'},
              listed=b'x/q2-librespot Spotify\n')
    s = Store(fw, card, wifi=0)
    s.wifi = 1
    s.click()
    assert s.run() == 0 and s.toasts == ['Spotify is ready in Streaming']
    assert card.files[b'/mnt/mmc/.spotify/cache/x'] == b'mine' and card.files[b'/mnt/mmc/.spotify/.cache-size'] == b'2G'
    assert card.files[b'/mnt/mmc/.spotify/librespot'] == b'bin'
    steps = []
    for e in card.log:
        if e[0] == 'system' and e[1].startswith(kill):
            steps.append('stop')
        elif e[0] == 'system' and e[1].startswith(b'busybox unzip -o'):
            steps.append('unpack')
        elif e[0] == 'rename' and e[2].startswith(b'/mnt/mmc/.spotify/') and 'move' not in steps:
            steps.append('move')
    # stopped on the UI thread as the job starts, again before .spotify is touched (it may have started since), and
    # as the job ends, so spotify.c forgets the old one
    assert steps == ['stop', 'unpack', 'stop', 'move', 'stop']


def test_erasing_spotify_s_data_runs_as_a_job(fw):
    card = Disk(fw, {SOURCES: b'x/q2-librespot Spotify\n', b'/mnt/mmc/.spotify/librespot': b'bin',
                     b'/mnt/mmc/.spotify/cache/x': b'mine', b'/mnt/mmc/.spotify/.cache-size': b'2G'})
    s = Store(fw, card, wifi=0)
    erase = fw.fn('shelf_erase', C.c_int, C.c_int)
    assert erase(1) == 1 and erase(0) == 1
    assert s.run() == 0 and s.toasts == ["Spotify's login and cache erased"] and erase(0) == 0
    assert [k for k in card.files if b'.spotify' in k] == [b'/mnt/mmc/.spotify/librespot']
    assert ('rmdir', b'/mnt/mmc/.spotify') in card.log  # tried: still holds librespot


SPOT_LOGS = [b'/mnt/mmc/.spotify/spot.log', b'/mnt/mmc/.spotify/aplay.log', b'/mnt/mmc/.spotify/load.log']


def test_erasing_spotify_s_data_takes_the_add_on_s_logs_and_leaves_its_settings(fw):
    """q2-librespot's launcher appends librespot's messages to spot.log, which so names the account at each login and
    every track played; aplay.log is its sink's and load.log its debug file's. .quality and debug are settings the
    user put there, and a spot.log elsewhere on the card is not Spotify's."""
    kept = [b'/mnt/mmc/.spotify/.quality', b'/mnt/mmc/.spotify/debug', b'/mnt/mmc/.spotify/librespot',
            b'/mnt/mmc/Music/spot.log']
    card = Disk(fw, {SOURCES: b'x/q2-librespot Spotify\n', b'/mnt/mmc/.spotify/cache/credentials.json': b'{}',
                     **dict.fromkeys(kept + SPOT_LOGS, b"Authenticated as 'me' !\n")})
    s = Store(fw, card, wifi=0)
    assert fw.fn('shelf_erase', C.c_int, C.c_int)(1) == 1 and s.run() == 0
    assert sorted(k for k in card.files if b'/.spotify/' in k or k.endswith(b'spot.log')) == sorted(kept)


def test_erasing_spotify_s_data_after_removing_spotify_leaves_no_folder(fw):
    """Remove takes only the files the add-on's zip listed, so the logs, cache/ and .cache-size stay after it;
    the erase must take every one of them for the folder to go."""
    card = Disk(fw, {SOURCES: b'x/q2-librespot Spotify\n', b'/mnt/mmc/.spotify/cache/credentials.json': b'{}',
                     **dict.fromkeys(SPOT_LOGS, b'log')})
    card.dirs.add(b'/mnt/mmc/.spotify')
    s = Store(fw, card, wifi=0)
    assert fw.fn('shelf_erase', C.c_int, C.c_int)(1) == 1 and s.run() == 0
    assert not [k for k in card.files if b'.spotify' in k] and b'/mnt/mmc/.spotify' not in card.dirs


def test_erase_while_checking_stops_the_check(fw):
    card = Disk(fw)
    store_net(fw)
    s = Store(fw, card)
    assert fw.fn('shelf_erase', C.c_int, C.c_int)(1) == 0
    assert s.sh.cancel == 1 and s.toasts == ['Stopped checking for updates'] and s.sh.job == JOB_CHECK


def test_a_job_that_can_t_start_says_so_and_the_row_stays(fw):
    card = Disk(fw, {SOURCES: b'me/q2-foo Foo\n'})
    s = Store(fw, card, wifi=0, thread=11)
    s.wifi = 1
    s.click()
    assert s.toasts == ["Can't start the download"] and s.sh.job == 0 and not s.sh.running and s.labels() == ['Install']


# No byte a peer chooses is the shell's. Of a list's line only the id reaches a command (1 to 15 of a-z0-9-, there after
# a "/" or its folder's ".", so a leading "-" starts no word); the names in a zip reach none: find's lines are renamed
# and unlinked, never run. Each value as an app's id, its repo's whole name, its owner, the name shown and a file in
# Foo's zip; then each ASCII byte an id may not hold, inside one, a line each.
@pytest.mark.parametrize('put', [
    *(where % value for where in ('me/q2-%s Evil', 'me/%s Evil', '%s/q2-evil Evil', 'me/q2-evil %s', '.foo/%s')
      for value in ("'; reboot #", '$(reboot)', '`reboot`', '.', '..', '../x', '-rf', 'a\nreboot', 'a reboot', 'a"reboot', "a'reboot")),
    '\n'.join(f'me/q2-a{chr(c)}z Evil' for c in range(1, 128) if not re.match('[a-z0-9-]', chr(c)))],
    ids=lambda put: None if len(put) < 40 else 'every other byte')
@pytest.mark.hostile('netupdate.c:unzip', 'netupdate.c:shelf_place', 'netupdate.c:shelf_remove')
def test_only_a_checked_id_reaches_the_shell_from_a_list_or_a_zip(fw, put):
    """The list and the zip as fetched, each app the page then lists installed and removed, and every command read as
    system() got it."""
    entry = put.startswith('.foo/')
    files = {f'.{app}/q2-{app}': b'bin' for app in ('foo', 'evil', 'a', '-rf')}  # a folder for each id a line here comes to
    if entry:
        files[put] = b'x'
    card = Disk(fw)
    store_net(fw, files, listed=(b'' if entry else put.encode() + b'\n') + b'me/q2-foo Foo\n')
    s = Store(fw, card)
    s.run()
    for r in range(1, s.sh.rows):
        s.click(r)
        s.run()
        if s.labels()[r - 1] == 'Installed':
            s.click(r)
            s.click(r)
            s.run()
    shell = [re.escape(c).replace('ID', '([a-z0-9-]{1,15})', 1).replace('ID', r'\1') for c in (
        f'rm -rf {STAGING}', 'sync',
        f"busybox unzip -l -qq {STAGING}/ID.part '.ID/*' -x '*/._*'  > {STAGING}/ID.list",
        f"busybox unzip -o -qq -d {STAGING}/ID {STAGING}/ID.part '.ID/*' -x '*/._*' ",
        f'cd {STAGING}/ID && find .ID -type f > {STAGING}/ID.files', "rm -rf '/mnt/mmc/.ID'")]
    ran = [c.decode() for c in card.commands()]
    assert not [c for c in ran if not any(re.fullmatch(form, c) for form in shell)]
    if entry and '..' in put.split('/'):  # a ".." entry refuses the whole zip (shelf_unpacked): listed, nothing unpacked
        assert s.toasts == ['The download is not a usable app'] and len(ran) == 3 and ran[1].startswith('busybox unzip -l ')
    elif not (entry and '\n' in put):  # a zip's name with a newline (no FAT card holds one) is held to the forms alone; else
        # Foo went in and out, so each call was made; a zip's file with it, moved by its name
        assert s.toasts[-2:] == ['Foo is ready in Streaming', 'Foo removed']
        assert all(any(re.fullmatch(form, c) for c in ran) for form in shell)
        assert not entry or (b'/mnt/mmc/' + put.encode()) in card.moves(b'/mnt/mmc/.foo/')
    # -rf, the one value that is an id, goes the whole way too
    assert ("rm -rf '/mnt/mmc/.-rf'" in ran) == (put in ('me/q2--rf Evil', 'me/-rf Evil'))
