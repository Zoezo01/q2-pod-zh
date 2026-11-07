"""patch/netupdate.c: GitHub's redirects and release JSON, sources.list, unzip's listing, and installing,
removing and checking apps, with the card and the network stubbed."""
import ctypes as C
import pathlib
import random

import pytest

import host
from conftest import block

# stock.h's Q2POD_TAG: tools/build.py's VERSION (host.py builds as a release, not --dev)
TAG = host.tools_build.VERSION.encode()
# the next single-digit tag (1.6.9 -> 1.7.0), as netupdate's strcmp orders them
_n = int(TAG.replace(b'.', b'')) + 1
NEWER = b'%d.%d.%d' % (_n // 100, _n // 10 % 10, _n % 10)
OLDER = b'0.9.9'

SH_MAX = 16
SH_CAP = 1 << 30
SH_OK, SH_NET, SH_SPACE, SH_CARD, SH_MISSING, SH_BAD, SH_CANCELLED, SH_BUSY = range(8)
NU_IDLE, NU_RUNNING, NU_DONE, NU_LATEST, NU_NET, NU_MISSING, NU_CARD, NU_CANCELLED = range(8)
JOB_CHECK, JOB_INSTALL, JOB_REMOVE, JOB_ERASE = 1, 2, 3, 4
ST_INSTALL, ST_UPDATE, ST_INSTALLED = range(3)
ROW_NONE = SH_MAX
STAGING = '/mnt/mmc/.q2/staging'
JSON_PATH = b'/tmp/q2-shelf.json'
SHELF = pathlib.Path(__file__).resolve().parents[1]/'shelf'


class App(C.Structure):
    _fields_ = [('name', C.c_char * 32), ('id', C.c_char * 16), ('repo', C.c_char * 80), ('dir', C.c_char * 17),
                ('bin', C.c_char * 40), ('latest', C.c_char * 32), ('keep', C.c_char_p)]


class Sh(C.Structure):
    _fields_ = [('thread', C.c_ulong), *((n, C.c_int) for n in ('running', 'cancel', 'percent', 'stage', 'result', 'job', 'app', 'armed', 'rows', 'n')),
                ('map', C.c_int * SH_MAX), ('need', C.c_uint), ('checked', C.c_ulonglong), ('next', App * SH_MAX),
                ('page', C.c_void_p), ('row', C.c_void_p * SH_MAX)]


class Nu(C.Structure):
    _fields_ = [('thread', C.c_ulong), ('state', C.c_int), ('cancel', C.c_int), ('percent', C.c_int), ('label', C.c_void_p)]


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
    rows = ','.join('{"url":"u","id":1,"name":"%s","label":"","uploader":{"login":"x","id":2},"content_type":"z","size":%s,'
                    '"browser_download_url":"https://h/%s"}' % (n, s, n) for n, s in assets)
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
    assert gone(5, None) == 0 and not nu.label and nu.cancel == 1


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
    """libcurl: each transfer answered by the first of its URL's replies: (result, code, location), or the body written."""
    def __init__(self, fw, replies):
        self.replies, self.urls, self.opts = replies, [], {}
        self.location = None
        def setopt(c, opt, value, *_):
            self.opts[opt] = value
        def perform(c, *_):
            url = C.string_at(self.opts[10002])
            self.urls.append(url)
            self.result, self.code, location = next(v for k, v in self.replies.items() if url.startswith(k))
            self.location = C.create_string_buffer(location) if location else None
            return self.result
        def info(c, what, out, *_):
            if what == 0x200002:
                C.cast(out, C.POINTER(C.c_long))[0] = self.code
            elif what == 0x10001f:
                C.cast(out, C.POINTER(C.c_void_p))[0] = C.addressof(self.location) if self.location else 0
        fw.mock('fw_curl_easy_init', lambda *_: self.opts.clear() or 1)
        fw.mock('fw_curl_easy_setopt', setopt)
        fw.mock('fw_curl_easy_perform', perform)
        fw.mock('fw_curl_easy_getinfo', info)


LATEST = b'https://github.com/owner/repo/releases/latest'
ASSET = LATEST + b'/download/update.tar'
PART = b'/mnt/mmc/update.tar.part'


def update(fw, code=302, location=b'https://github.com/owner/repo/releases/tag/' + NEWER, asset=(0, 200, None), card=None):
    net = Net(fw, {ASSET: asset, LATEST: (0, code, location)})
    card = card or Card(fw)
    nu = fw.var('netupdate__nu', Nu)
    fw.fn('netupdate__worker', C.c_void_p, C.c_void_p)(None)
    return nu.state, net, card


def test_a_newer_tag_downloads_and_renames_once_whole(fw):
    state, net, card = update(fw)
    assert state == NU_DONE and net.urls == [LATEST, ASSET]
    assert card.of('rename') == [(PART, b'/mnt/mmc/update.tar')]


@pytest.mark.parametrize('tag', [TAG, OLDER])
def test_nothing_older_or_equal_is_ever_flashed(fw, tag):
    state, net, _ = update(fw, location=b'https://github.com/owner/repo/releases/tag/' + tag)
    assert state == NU_LATEST and net.urls == [LATEST]


@pytest.mark.parametrize('code,location', [(200, None), (302, b'https://github.com/owner/repo/releases'), (302, b''), (404, None)])
def test_no_tag_is_no_network(fw, code, location):
    assert update(fw, code, location)[0] == NU_NET


def test_a_long_redirect_is_held_to_the_tag_buffer(fw):
    state, net, card = update(fw, location=b'https://x/releases/tag/9' + b'9' * 200)
    assert state == NU_DONE


def test_a_missing_asset_leaves_no_part(fw):
    state, _, card = update(fw, asset=(0, 404, None))
    assert state == NU_MISSING and card.of('unlink') == [(PART,)] and not card.of('rename')


def test_a_failed_transfer_says_network_a_write_error_card(fw):
    assert update(fw, asset=(28, 0, None))[0] == NU_NET
    fw.mock('fw_ferror', lambda *_: 1)
    assert update(fw, asset=(23, 0, None))[0] == NU_CARD


def test_a_card_that_cant_open_the_part(fw):
    card = Card(fw)
    fw.mock('fw_fopen', lambda *_: 0)
    assert update(fw, card=card)[0] == NU_CARD


def test_a_cancel_during_the_check_downloads_nothing(fw):
    fw.var('netupdate__nu', Nu).cancel = 1
    state, net, _ = update(fw)
    assert state == NU_CANCELLED and net.urls == [LATEST]


def fetch(fw, url=b'https://x', tag_size=64, file=None):
    tag, code = block(fw, size=tag_size), C.c_long(0)
    xfer = (C.c_int * 2)()
    fn = fw.fn('netupdate__fetch', C.c_int, C.c_char_p, C.c_void_p, C.c_void_p, C.c_uint, C.POINTER(C.c_long), C.c_void_p)
    err = fn(url, file, tag if not file else None, tag_size, C.byref(code), xfer)
    return err, C.string_at(tag), code.value


def test_a_header_fetch_sends_no_body_and_keeps_the_tag(fw):
    net = Net(fw, {b'https': (0, 302, b'https://g/releases/tag/1.2.3')})
    assert fetch(fw) == (0, b'1.2.3', 302)
    assert net.opts[44] == 1 and 52 not in net.opts  # NOBODY, no FOLLOWLOCATION


def test_a_download_follows_redirects_to_its_file_and_verifies(fw):
    net = Net(fw, {b'https': (0, 200, None)})
    assert fetch(fw, file=7)[0] == 0
    assert net.opts[52] == 1 and net.opts[10001] == 7 and net.opts[64] == 1 and net.opts[81] == 2


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
    return fw.fn('netupdate__room', C.c_int, C.c_uint)(bytes_), fw.var('netupdate__sh', Sh).need


RESERVE = 64 << 20


def test_room_for_the_bytes_and_the_reserve(fw):
    assert room(fw, 4096, (RESERVE + 4096) // 4096)[0] == SH_OK


def test_one_block_short_says_how_many_mb(fw):
    assert room(fw, 8192, (RESERVE + 4096) // 4096) == (SH_SPACE, 1)
    assert room(fw, 100 << 20, 0) == (SH_SPACE, 164)


def test_a_huge_card_doesnt_overflow(fw):
    assert room(fw, 1 << 30, 1 << 31, 32768)[0] == SH_OK


def test_no_statfs_is_a_card_error(fw):
    assert room(fw, 1, 1 << 20, ok=-1)[0] == SH_CARD


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
    """The Apps page: its rows' text and toasts, with no thread run."""
    def __init__(self, fw, *lines):
        self.fw, self.text, self.toasts = fw, {}, []
        self.table = apps(fw, *lines)
        self.card = Card(fw)
        self.sh = fw.var('netupdate__sh', Sh)
        self.wifi = 1
        fw.mock('get_wifisignal', lambda *_: self.wifi)
        fw.mock('window_create', lambda *_: 77)
        fw.mock('widget_get_child', lambda row, i, *_: row)
        fw.mock('list_item_create', lambda view, x, y, *_: 1000 + y)
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        self.sh.page = 77
        fw.fn('netupdate__sh_rows', None)()

    def label(self, r):
        return self.text[self.sh.row[r]]

    def click(self, r=0):
        self.fw.fn('netupdate__sh_click', C.c_int, C.c_void_p, C.c_void_p)(r, None)


def test_rows_say_install(fw):
    p = Page(fw, b'me/foo', b'me/bar')
    assert p.sh.rows == 2 and p.label(0) == 'Install' and p.label(1) == 'Install'


def test_a_press_installs_at_once(fw):
    p = Page(fw, b'me/foo')
    p.click()
    assert p.sh.job == JOB_INSTALL and p.sh.app == 0 and p.sh.running == 1 and p.label(0) == 'Checking…'


def test_a_press_without_wifi_says_so(fw):
    p = Page(fw, b'me/foo')
    p.wifi = 0
    p.click()
    assert p.sh.job == 0 and p.toasts == ['Connect to Wi-Fi first']


def test_rockbox_asks_twice_as_it_becomes_the_default(fw):
    p = Page(fw, b'x/q2-rockbox')
    p.click()
    assert p.sh.job == 0 and p.label(0).endswith('Press again to install')
    p.click()
    assert p.sh.job == JOB_INSTALL


def test_an_installed_app_is_removed_on_a_second_press(fw):
    p = Page(fw, b'me/foo')
    p.card.files[b'/mnt/mmc/.q2/apps/foo.tag'] = b'1.0'
    p.click()
    assert p.sh.armed == 1 and p.label(0) == 'Press again to remove'
    p.click()
    assert p.sh.job == JOB_REMOVE and p.sh.stage == 4


def test_a_press_on_the_running_install_cancels_it_until_it_moves(fw):
    p = Page(fw, b'me/foo', b'me/bar')
    p.click(0)
    p.click(1)
    assert p.toasts == ['Wait for the one in progress'] and p.sh.cancel == 0
    p.click(0)
    assert p.sh.cancel == 1 and p.label(0) == 'Cancelling…'
    p.sh.cancel, p.sh.stage = 0, 3
    p.click(0)
    assert p.sh.cancel == 0


def test_download_progress_on_the_row(fw):
    p = Page(fw, b'me/foo')
    p.click()
    p.sh.stage, p.sh.percent = 1, 42
    fw.fn('netupdate__sh_refresh', None)()
    assert p.label(0) == 'Downloading 42%'


def test_no_list_shows_one_row_to_load_it(fw):
    p = Page(fw)
    assert p.sh.rows == 1 and p.sh.map[0] == ROW_NONE and p.label(0) == 'Connect to Wi-Fi to load the list'
    p.click()
    assert p.sh.job == JOB_CHECK and p.sh.app == 0


def test_rockbox_themes_show_only_with_rockbox(fw):
    p = Page(fw, b'x/q2-rockbox', b'x/q2-rockbox-themes')
    assert p.sh.rows == 1
    p.card.files[b'/mnt/mmc/.rockbox/rockbox'] = b''
    fw.fn('netupdate__sh_rows', None)()
    assert p.sh.rows == 2


def test_a_press_while_checking_stops_the_check(fw):
    p = Page(fw, b'me/foo')
    p.sh.job = JOB_CHECK
    p.click()
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
