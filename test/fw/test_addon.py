"""patch/addon.c, addon_library.c and library_scope.c: card apps' manifests, playlists, sleep timers, the page
they draw over Q2SPOT/1, their QR codes, library requests, and the database.ignore markers."""
import ctypes as C
import random

import pytest

from conftest import block, text

APPS_MAX = 8
REPLY_MAX = 64 << 10
ADDON_ROWS, ADDON_PAGES, ADDON_DEPTH = 50, 10, 6
M3U_MAX = 256 << 10
QR_HEX = 640
LIB_MAX = 32 << 10
LIB_DIR = '/mnt/mmc/_explaylist_data/'
SCOPE_MAX = 131072
RULES = b'/mnt/data/q2-library-scope'
TOKEN = b'/*q2-library-scope*/1'
K_LOAD, K_READY, K_ERROR, K_WAIT = range(4)
REQ_OK, REQ_NOSOCK, REQ_FAIL = range(3)
S_WAIT, S_PLAY, S_DONE = range(3)
L_IDLE, L_WAIT, L_SCAN, L_IMPORT = range(4)
W_W, W_H, CANVAS_LCD = 0x08, 0x0c, 0x38


class App(C.Structure):
    _fields_ = [('id', C.c_char * 16), ('name', C.c_char * 32), ('tips', C.c_char * 32), ('bin', C.c_char * 48), ('run', C.c_char * 48),
                ('pid', C.c_char * 32), ('state', C.c_char * 40), ('browse', C.c_char * 48), ('icon', C.c_char * 56),
                *((n, C.c_int) for n in ('qr', 'game', 'present', 'launched', 'hold', 'relaunch', 'restart')),
                *((n, C.c_uint) for n in ('polled', 'launch_at', 'restart_at', 'play'))]


class Ap(C.Structure):
    _fields_ = [('a', App * APPS_MAX), ('n', C.c_int), ('shown', C.c_int), ('order', C.c_int * APPS_MAX),
                ('games', C.c_int), ('game', C.c_int * APPS_MAX)]


class Sm(C.Structure):
    _fields_ = [('app', C.c_void_p), ('dir', C.c_char * 160), ('last', C.c_char * 192), ('playing', C.c_char * 512), ('state', C.c_int),
                ('timer', C.c_uint), ('seen', C.c_long), ('m3u', C.c_int), ('next', C.c_int), ('more', C.c_int), ('first', C.c_char * 512),
                ('want', C.c_char * 512), ('loads', C.c_uint)]


class Sl(C.Structure):
    _fields_ = [('seen', C.c_uint * APPS_MAX), ('at', C.c_long), ('end', C.c_int), ('owner', C.c_int), ('pos', C.c_uint), ('nexts', C.c_uint),
                ('sec', C.c_int), ('total', C.c_int), ('stopped', C.c_uint)]


class Req(C.Structure):
    _fields_ = [('thread', C.c_ulong), ('running', C.c_int), ('fail', C.c_int), ('cancel', C.c_int), ('done', C.c_int), ('id', C.c_uint),
                ('n', C.c_uint), ('len', C.c_uint), ('buf', C.c_void_p), ('path', C.c_void_p)]


class Lv(C.Structure):
    _fields_ = [*((n, C.c_int) for n in ('state', 'n', 'sel', 'live', 'retry', 'nosock', 'slow')),
                *((n, C.c_uint) for n in ('since', 'base', 'id', 'hash')),
                ('title', C.c_char * 128), ('note', C.c_char * 128), ('op', C.c_char * 16), ('args', C.c_char * 400)]


class Pg(C.Structure):
    _fields_ = [*((n, C.c_void_p) for n in ('page', 'view', 'edit')), *((n, C.c_uint) for n in ('timer', 'go', 'leave', 'again')),
                *((n, C.c_int) for n in ('depth', 'act', 'next', 'arg', 'a', 'code')),
                ('term', C.c_char * 64), ('aop', C.c_char * 16), ('lv', C.POINTER(Lv)), ('buf', C.c_void_p * ADDON_PAGES),
                ('f', C.POINTER(C.c_char_p)), ('pages', C.c_int), ('paging', C.c_int), ('moved', C.c_byte)]


class Qp(C.Structure):
    _fields_ = [*((n, C.c_void_p) for n in ('page', 'qr', 'col', 'msg', 'addr', 'pin', 'note', 'say', 'sub')),
                *((n, C.c_uint) for n in ('timer', 'leave', 'seen')), ('state', C.c_void_p), ('size', C.c_int), ('hex', C.c_void_p)]


class Taken(C.Structure):
    _fields_ = [('id', C.c_char * 16), ('seq', C.c_char * 24)]


class Libup(C.Structure):
    _fields_ = [('only', C.c_void_p), ('busy', C.c_int), ('seq', C.c_char * 24), ('done', C.c_char * 48), ('list', C.c_void_p),
                ('state', C.c_int), ('scan', C.c_int), ('seen', C.c_int), ('polled', C.c_uint), ('taken', Taken * APPS_MAX)]


class Rule(C.Structure):
    pass


Rule._fields_ = [('next', C.POINTER(Rule)), ('length', C.c_uint), ('visible', C.c_int)]


class Scope(C.Structure):
    _fields_ = [('rules', C.POINTER(Rule)), ('sql', C.c_void_p), ('bytes', C.c_uint), ('ready', C.c_int), ('failed', C.c_int)]


class Dirent(C.Structure):  # test/fw/host.py's
    _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]


def hostile(seed, samples, count=1500, alphabet=b'%\t\n=/. \r'):
    """Random bytes, every truncation of the samples, and mutations of them."""
    r = random.Random(seed)
    for _ in range(count):
        yield r.randbytes(r.randrange(120)).replace(b'\0', b'\n')
    for s in samples:
        for k in range(len(s)):
            yield s[:k]
    for _ in range(count):
        b = bytearray(r.choice(samples))
        for _ in range(r.randrange(1, 6)):
            b[r.randrange(len(b))] = r.choice(alphabet)
        yield bytes(b)


class World:
    """The card, /tmp and the clocks: files by path, folders listed as given, every effect logged."""
    def __init__(self, fw, files=None, dirs=None, now=1000):
        self.fw, self.files, self.dirs, self.now, self.ms = fw, dict(files or {}), dict(dirs or {}), now, 1000
        self.log, self.at, self.names, self.written, self.lists, self.mtime = [], {}, {}, {}, {}, {}
        self.fail_write = lambda name: False
        fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
        for stub, fn in (('fw_fopen', self.fopen), ('fw_fread', self.fread), ('fw_fwrite', self.fwrite), ('fw_rename', self.rename),
                         ('fw_unlink', self.unlink), ('fw_system', self.system), ('fw_access', self.access), ('fw___xstat', self.stat),
                         ('fw_opendir', self.opendir), ('fw_readdir', self.readdir)):
            fw.mock(stub, fn)
        fw.mock('fw_time', lambda *_: self.now)
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.mock('fw_clock_gettime', self.clock)

    def clock(self, which, ts, *_):
        t = C.cast(ts, C.POINTER(C.c_long))  # now_ms's and boot_s's: a struct of two longs
        t[0], t[1] = self.now, 0

    def fopen(self, path, mode, *_):
        name, how = C.string_at(path), C.string_at(mode)
        if b'r' in how and name not in self.files:
            return 0
        f = len(self.names) + 1
        self.names[f], self.at[f] = name, 0
        if b'w' in how:
            self.written[name] = b''
        return f

    def fread(self, buf, size, n, f, *_):
        piece = self.files[self.names[f]][self.at[f]:self.at[f] + size * n]
        C.memmove(buf, piece, len(piece))
        self.at[f] += len(piece)
        return len(piece) // size

    def fwrite(self, buf, size, n, f, *_):
        if self.fail_write(self.names[f]):
            return 0
        self.written[self.names[f]] += C.string_at(buf, size * n)
        return n

    def rename(self, a, b, *_):
        a, b = C.string_at(a), C.string_at(b)
        self.log.append(('rename', a, b))
        self.files[b] = self.written.get(a, self.files.get(a, b''))

    def unlink(self, p, *_):
        self.log.append(('unlink', C.string_at(p)))
        self.files.pop(C.string_at(p), None)

    def system(self, cmd, *_):
        self.log.append(('system', C.string_at(cmd)))

    def access(self, p, *_):
        name = C.string_at(p)
        return 0 if name in self.files or name in self.dirs else -1

    def stat(self, version, path, info, *_):
        name = C.string_at(path)
        if name not in self.files and name not in self.dirs:
            return -1
        st = C.cast(info, C.POINTER(C.c_uint))
        st[5] = 0o100644 if name in self.files else 0o040755
        st[16] = self.mtime.get(name, 1)

    def opendir(self, path, *_):
        name = C.string_at(path)
        if name not in self.dirs:
            return 0
        d = len(self.lists) + 1
        self.lists[d] = [e if isinstance(e, Dirent) else Dirent(d_name=e) for e in self.dirs[name]]
        return d

    def readdir(self, d, *_):
        rows = self.lists[d]
        if not rows:
            return 0
        self.keep = rows.pop(0)
        return C.addressof(self.keep)

    def of(self, kind):
        return [e[1:] for e in self.log if e[0] == kind]


def ap(fw):
    return fw.var('addon__ap', Ap)


def valid_id(fw, s):
    return fw.fn('addon__valid_id', C.c_int, C.c_void_p)(block(fw, s))


@pytest.mark.parametrize('s,ok', [
    (b'a', 1), (b'q2-deezer', 1), (b'a' * 15, 1), (b'0-9', 1), (b'-', 1),
    (b'', 0), (b'a' * 16, 0), (b'A', 0), (b'a b', 0), (b'a;b', 0), (b'a/b', 0), (b"a'b", 0), (b'a.b', 0), (b'$(x)', 0), (b'\xc3\xa9', 0),
])
def test_an_id_goes_into_shell_commands(fw, s, ok):
    assert valid_id(fw, s) == ok


def test_every_byte_an_id_may_hold(fw):
    allowed = set(b'abcdefghijklmnopqrstuvwxyz0123456789-')
    for c in range(1, 256):
        assert valid_id(fw, bytes([c])) == (c in allowed), c


def dotdot(fw, s):
    return fw.fn('addon__dotdot', C.c_int, C.c_void_p)(block(fw, s))


@pytest.mark.parametrize('path,yes', [
    (b'..', 1), (b'../a', 1), (b'/mnt/mmc/../x', 1), (b'/mnt/mmc/a/..', 1), (b'a/../b', 1), (b'/..', 1), (b'../..', 1),
    (b'', 0), (b'.', 0), (b'/', 0), (b'a..', 0), (b'..a', 0), (b'/a/..b/c', 0), (b'/a/b../c', 0), (b'Hello... (Live).flac', 0),
    (b'...', 0), (b'/a/.../b', 0), (b'.../', 0), (b'a. .b', 0), (b'a/./b', 0),
])
def test_a_dotdot_segment(fw, path, yes):
    assert dotdot(fw, path) == yes


def slots(fw, *ids):
    world = World(fw)
    fn = fw.fn('addon__slot', C.POINTER(App), C.c_char_p)
    return [fn(i) for i in ids], world


def test_a_slots_paths(fw):
    (a,), _ = slots(fw, b'foo')
    a = a.contents
    assert (a.id, a.bin, a.run, a.pid, a.state, a.browse) == (b'foo', b'/mnt/mmc/.foo/q2-foo', b'/bin/sh /mnt/mmc/.foo/run', b'/tmp/q2-foo',
                                                              b'/tmp/q2-foo.state', b'/tmp/q2-foo.browse.sock')


def test_the_longest_id_fits_every_path(fw):
    i = b'a' * 15
    (a,), _ = slots(fw, i)
    a = a.contents
    assert a.bin == b'/mnt/mmc/.' + i + b'/q2-' + i and a.run.endswith(i + b'/run') and a.state.endswith(i + b'.state')
    assert a.pid == b'/tmp/q2-' + i and a.browse.endswith(i + b'.browse.sock')


def test_a_slot_stays_its_apps_and_slots_run_out(fw):
    table, _ = slots(fw, *[b'a%d' % i for i in range(APPS_MAX)], b'a3', b'extra')
    assert C.addressof(table[3].contents) == C.addressof(table[8].contents)
    assert not table[9] and ap(fw).n == APPS_MAX


def card(fw, **apps):
    """A card of apps: each a manifest's text, or (manifest, extra files)."""
    files, names = {}, []
    for name, manifest in apps.items():
        extra = ()
        if isinstance(manifest, tuple):
            manifest, extra = manifest
        names.append(b'.' + name.encode())
        files[f'/mnt/mmc/.{name}/q2app'.encode()] = manifest
        files[f'/mnt/mmc/.{name}/q2-{name}'.encode()] = b'ELF'
        files.update({f'/mnt/mmc/.{name}/{e}'.encode(): b'' for e in extra})
    return World(fw, files, {b'/mnt/mmc': names})


def scan(fw):
    return fw.fn('addon_scan', C.c_int)()


def names(fw, fn):
    get = fw.fn(fn, C.c_char_p, C.c_int)
    return [get(k) for k in range(-1, ap(fw).shown + 1)]


def test_scan_finds_apps_by_name(fw):
    card(fw, one=b'name=zed\n', two=b'name=Alpha\n', three=b'name=beta\n')
    assert scan(fw) == 3
    assert names(fw, 'addon_name') == [None, b'Alpha', b'beta', b'zed', None]
    assert names(fw, 'addon_id') == [None, b'two', b'three', b'one', None]


def test_a_manifests_keys_and_defaults(fw):
    card(fw, one=b'name=N\nsearch=Find it\nqr=1\n', two=b'other=1\n')
    scan(fw)
    one, two = ap(fw).a[0], ap(fw).a[1]
    assert (one.name, one.tips, one.qr) == (b'N', b'Find it', 1)
    assert (two.name, two.tips, two.qr) == (b'two', b'Search', 0)


def test_names_are_cut_at_a_whole_character(fw):
    card(fw, one='name={}\n'.format('é' * 40).encode())
    scan(fw)
    assert ap(fw).a[0].name == 'é'.encode() * 15


def test_what_is_no_app(fw):
    world = card(fw, good=b'name=G\n')
    world.dirs[b'/mnt/mmc'] += [b'Music', b'.', b'..', b'.Bad', b'.a b', b'.' + b'a' * 16, b'.nomanifest']
    assert scan(fw) == 1 and ap(fw).n == 1


def test_an_empty_manifest_is_no_app(fw):
    card(fw, one=b'')
    assert scan(fw) == 0 and ap(fw).n == 0


def test_an_app_without_its_binary_is_not_shown(fw):
    world = card(fw, one=b'name=N\n')
    del world.files[b'/mnt/mmc/.one/q2-one']
    assert scan(fw) == 0 and ap(fw).a[0].present == 0


def test_icons_prefer_the_rows_size(fw):
    card(fw, a=(b'x=1\n', ['icon-40.png', 'icon.png']), b=(b'x=1\n', ['icon.png']), c=b'x=1\n')
    scan(fw)
    assert [ap(fw).a[i].icon for i in range(3)] == [b'file:///mnt/mmc/.a/icon-40.png', b'file:///mnt/mmc/.b/icon.png', b'stream_app']
    assert names(fw, 'addon_icon')[1] == b'file:///mnt/mmc/.a/icon-40.png'


def test_the_longest_ids_icon_fits(fw):
    i = 'a' * 15
    card(fw, **{i: (b'x=1\n', ['icon-40.png'])})
    scan(fw)
    assert ap(fw).a[0].icon == f'file:///mnt/mmc/.{i}/icon-40.png'.encode()


def test_a_slot_keeps_its_app_across_scans_and_an_app_gone_goes_from_the_list(fw):
    world = card(fw, one=b'name=N\n', two=b'name=M\n')
    scan(fw)
    ap(fw).a[0].launched = 1
    world.dirs[b'/mnt/mmc'].remove(b'.two')
    assert scan(fw) == 1 and ap(fw).n == 2 and ap(fw).a[0].launched == 1 and ap(fw).a[1].present == 0


def test_more_apps_than_slots(fw):
    card(fw, **{f'a{i}': b'x=1\n' for i in range(APPS_MAX + 3)})
    assert scan(fw) == APPS_MAX


def test_the_features_are_published_at_every_scan(fw):
    world = card(fw)
    scan(fw)
    assert world.written[b'/tmp/q2-addon.caps.tmp'] == b'playlist progress pages library goto sleep game\n'
    assert world.of('rename') == [(b'/tmp/q2-addon.caps.tmp', b'/tmp/q2-addon.caps')]


def test_hostile_manifests(fw):
    for raw in hostile(41, [b'name=Foo Bar\nsearch=Find\nqr=1\ngoto=1\n'], 600):
        world = card(fw, one=raw)
        ap(fw).n = 0
        n = scan(fw)
        assert n in (0, 1)
        if n:
            assert len(ap(fw).a[0].name) < 32 and len(ap(fw).a[0].tips) < 32


@pytest.mark.parametrize('name,yes', [
    (b'file:///mnt/mmc/.foo/icon.png', 1), (b'file:///mnt/mmc/.foo/icon-40.png', 1), (b'file:///mnt/mmc/./icon.png', 1),
    (b'file:///mnt/mmc/.foo/other.png', 0), (b'file:///mnt/mmc/foo/icon.png', 0), (b'/mnt/mmc/.foo/icon.png', 0), (b'stream_app', 0), (b'', 0),
])
def test_a_card_apps_icon_name(fw, name, yes):
    World(fw)
    assert fw.fn('addon_is_icon', C.c_int, C.c_char_p)(name) == yes


def test_no_name_is_no_icon(fw):
    World(fw)
    assert fw.fn('addon_is_icon', C.c_int, C.c_char_p)(None) == 0


def test_stopping_an_app_asks_its_loop_or_clears_a_stale_pid(fw):
    out = block(fw, size=96)
    fw.fn('addon__stop_cmd', None, C.c_char_p, C.c_void_p, C.c_uint)(b'/tmp/q2-foo', out, 96)
    assert C.string_at(out) == b'kill $(cat /tmp/q2-foo) 2>/dev/null || rm -f /tmp/q2-foo'


def test_the_longest_stop_command_fits_its_buffer(fw):
    pid = b'/tmp/q2-' + b'a' * 15
    out = block(fw, size=96)
    fw.fn('addon__stop_cmd', None, C.c_char_p, C.c_void_p, C.c_uint)(pid, out, 96)
    assert C.string_at(out).endswith(b'rm -f ' + pid)


def test_a_stop_command_is_cut_to_its_buffer(fw):
    out = block(fw, size=8)
    fw.fn('addon__stop_cmd', None, C.c_char_p, C.c_void_p, C.c_uint)(b'/tmp/q2-foo', out, 8)
    assert C.string_at(out) == b'kill $('


def test_the_apps_page_forgets_only_the_app_in_its_folder(fw):
    a = ap(fw)
    a.n, a.a[0].id, a.a[1].id = 2, b'foo', b'bar'
    for i in (0, 1):
        a.a[i].launched = a.a[i].hold = a.a[i].relaunch = a.a[i].restart = 1
    forget = fw.fn('addon_forget', None, C.c_char_p)
    forget(b'.foo')
    assert (a.a[0].launched, a.a[0].hold, a.a[0].relaunch, a.a[0].restart) == (0, 0, 0, 0)
    assert a.a[1].launched == 1
    for other in (b'bar', b'.baz', b'', None):
        forget(other)
    assert a.a[1].launched == 1


def test_quit_asks_and_waits_for_the_loop_to_go(fw):
    world = World(fw, {b'/tmp/q2-foo': b'1'})
    sleeps = []
    def usleep(us, *_):
        sleeps.append(us)
        if len(sleeps) == 3:
            del world.files[b'/tmp/q2-foo']
    fw.mock('fw_usleep', usleep)
    fw.fn('addon_quit', None, C.c_char_p)(b'.foo')
    assert world.of('system') == [(b'kill $(cat /tmp/q2-foo) 2>/dev/null || rm -f /tmp/q2-foo',)] and sleeps == [50000] * 3


def test_quit_gives_up_after_its_wait(fw):
    world = World(fw, {b'/tmp/q2-foo': b'1'})
    fw.mock('fw_usleep', lambda *_: setattr(world, 'now', world.now + 1))
    fw.fn('addon_quit', None, C.c_char_p)(b'.foo')
    assert world.now == 1003


@pytest.mark.parametrize('folder', [b'foo', b'.', b'..', b'.a;b', b".a'b", b'.' + b'a' * 16, b'', None])
def test_quit_never_builds_a_command_from_a_bad_folder(fw, folder):
    world = World(fw)
    fw.fn('addon_quit', None, C.c_char_p)(folder)
    assert world.of('system') == [] and not fw.calls('fw_access')


def test_quit_with_no_loop_running_stops_nothing(fw):
    world = World(fw)
    fw.fn('addon_quit', None, C.c_char_p)(b'.foo')
    assert world.of('system') == []


def sm(fw):
    return fw.var('addon__sm', Sm)


def changed(fw, path=b'/mnt/mmc/.a'):
    return fw.fn('addon__changed', C.c_int, C.c_char_p)(path)


def test_a_change_is_seen_once_when_it_is_old_enough(fw):
    world = World(fw, dirs={b'/mnt/mmc/.a': []}, now=200)
    world.mtime[b'/mnt/mmc/.a'] = 100
    assert changed(fw) == 1 and sm(fw).seen == 100 and changed(fw) == 0


def test_a_change_in_the_last_second_is_looked_at_again(fw):
    world = World(fw, dirs={b'/mnt/mmc/.a': []}, now=200)
    world.mtime[b'/mnt/mmc/.a'] = 199
    assert changed(fw) == 1 and sm(fw).seen == 0 and changed(fw) == 1
    world.now = 201
    assert changed(fw) == 1 and sm(fw).seen == 199 and changed(fw) == 0


def test_a_missing_path_never_changed(fw):
    World(fw)
    assert changed(fw) == 0


def newer(fw, entries, last=b''):
    world = World(fw, dirs={b'/mnt/mmc/.a': entries}, now=500)
    world.mtime[b'/mnt/mmc/.a'] = 100
    sm(fw).seen = 0
    return fw.fn('addon__stream_newer', C.c_int, C.c_char_p, C.c_char_p)(b'/mnt/mmc/.a', last)


@pytest.mark.parametrize('entries,last,yes', [
    ([b'01.flac'], b'', 1), ([], b'', 0), ([b'.', b'..', b'.hidden'], b'', 0), ([b'a.part', b'b.tmp', b'c.PART', b'd.Tmp'], b'', 0),
    ([b'a.tmp.flac'], b'', 1), ([b'noext'], b'', 1),
    ([b'01.flac', b'02.flac'], b'02.flac', 0), ([b'01.flac', b'03.flac'], b'02.flac', 1), ([b'03.tmp'], b'02.flac', 0),
])
def test_a_song_after_the_last_one(fw, entries, last, yes):
    assert newer(fw, entries, last) == yes


def test_an_unchanged_folder_is_not_listed(fw):
    world = World(fw, dirs={b'/mnt/mmc/.a': [b'01.flac']}, now=500)
    world.mtime[b'/mnt/mmc/.a'] = 100
    sm(fw).seen = 100
    assert fw.fn('addon__stream_newer', C.c_int, C.c_char_p, C.c_char_p)(b'/mnt/mmc/.a', b'') == 0 and not fw.calls('fw_opendir')


LIST = b'/mnt/mmc/.app/q.m3u'
SONG = b'/mnt/mmc/Music/1.flac'


def m3u(fw, raw, world=None, entries=0):
    """m3u_songs over raw from entry `entries`: (sm.next, sm.want, sm.more)."""
    world = world or World(fw)
    world.files[LIST] = raw
    s = sm(fw)
    s.dir, s.next = LIST, entries
    fw.fn('addon__m3u_songs', C.c_void_p)()
    return s.next, s.want, s.more


def test_the_first_song_not_there_is_waited_for(fw):
    assert m3u(fw, b'#EXTM3U\n' + SONG + b'\n/mnt/mmc/Music/2.flac\n') == (0, SONG, 0)


@pytest.mark.parametrize('raw,entry', [
    (b'\xef\xbb\xbf' + SONG, 0), (SONG + b'\r\n', 0), (b'\r\n\n#x\n' + SONG + b'\n', 0),
    (b'/tmp/off-card\n' + SONG, 1), (b'relative.flac\n' + SONG, 1), (b'/mnt/mmcx/a\n' + SONG, 1),
    (b'/mnt/mmc/../etc/passwd\n' + SONG, 1), (b'/mnt/mmc/a/..\n' + SONG, 1),
    (b'/mnt/mmc/' + b'a' * 512 + b'\n' + SONG, 1),     # too long to be a path here
])
def test_entries_passed_over_and_counted(fw, raw, entry):
    assert m3u(fw, raw) == (entry, SONG, 0)


def test_the_longest_path_is_still_waited_for(fw):
    long = b'/mnt/mmc/' + b'a' * 502
    assert m3u(fw, long + b'\n')[1] == long
    assert m3u(fw, long + b'a\n') == (1, b'', 0)


def test_counted_entries_are_not_looked_at_again(fw):
    assert m3u(fw, b'/mnt/mmc/one\n#note\n' + SONG, entries=1) == (1, SONG, 0)


def test_an_empty_list_ends(fw):
    assert m3u(fw, b'') == (0, b'', 0) and m3u(fw, b'#only comments\n') == (0, b'', 0)


def test_no_list_ends(fw):
    World(fw)
    sm(fw).dir = b'/mnt/mmc/none.m3u'
    fw.fn('addon__m3u_songs', C.c_void_p)()
    assert sm(fw).next == 0 and sm(fw).want == b''


def test_a_list_past_its_limit_drops_its_cut_last_line(fw):
    head = b'/tmp/x\n' * 100
    raw = head + b'#' * (M3U_MAX - len(head) - 1) + b'\n' + SONG + b'\n'
    assert m3u(fw, raw) == (100, b'', 0)


def test_a_list_past_its_limit_with_no_line_end_is_empty(fw):
    assert m3u(fw, b'/mnt/mmc/' + b'a' * M3U_MAX) == (0, b'', 0)


def test_a_list_of_exactly_the_limit_keeps_its_last_line(fw):
    tail = SONG
    assert m3u(fw, b'#' * (M3U_MAX - len(tail) - 1) + b'\n' + tail)[1] == SONG


def test_hostile_playlists(fw):
    world = World(fw)
    samples = [b'#EXTM3U\n\xef\xbb\xbf' + SONG + b'\r\n/tmp/x\n/mnt/mmc/../y\n#c\n']
    for raw in hostile(42, samples, 800, b'\n\r#/.\xef\xbb\xbf'):
        next_, want, _ = m3u(fw, raw, world)
        assert want == b'' or (want.startswith(b'/mnt/mmc/') and b'\n' not in want)
        world.lists.clear(), world.names.clear()


def test_m3u_wakes_for_a_batch_a_rewrite_or_the_song_it_wants(fw):
    world = World(fw, {LIST: b'x'}, now=500)
    world.mtime[LIST] = 100
    ready = fw.fn('addon__m3u_ready', C.c_int)
    s = sm(fw)
    s.dir, s.seen = LIST, 100
    assert ready() == 0
    s.more = 1
    assert ready() == 1
    s.more = 0
    s.want = SONG
    assert ready() == 0
    world.files[SONG] = b''
    assert ready() == 1
    s.want = b''
    world.mtime[LIST] = 101
    assert ready() == 1


def sl(fw):
    return fw.var('addon__sl', Sl)


class Sleep:
    def __init__(self, fw, now=1000000):
        self.world = World(fw, now=now)
        self.fw = fw
        fw.mock('mclGetPlayStatus', lambda *_: 2)
        for i, name in enumerate((b'foo', b'bar')):
            ap(fw).a[i].id = name
        ap(fw).n = 2
        self.boot = 5000

        def clock(which, ts, *_):
            assert which == 7
            t = C.cast(ts, C.POINTER(C.c_long))
            t[0], t[1] = self.boot, 0
        fw.mock('fw_clock_gettime', clock)

    def read(self, raw, slot=0):
        name = b'/tmp/q2-%s.sleep' % ap(self.fw).a[slot].id
        if raw is None:
            self.world.files.pop(name, None)
        else:
            self.world.files[name] = raw
        self.fw.fn('addon__sleep_read', None, C.POINTER(App))(C.pointer(ap(self.fw).a[slot]))

    def paused(self):
        return len(self.fw.calls('player_play_pause'))


def test_a_timer_runs_on_the_boot_clock(fw):
    s = Sleep(fw)
    s.read(b'until %d\n' % (s.world.now + 600))
    assert sl(fw).at == s.boot + 600 and sl(fw).owner == 1 and s.paused() == 0


def test_the_furthest_timer_allowed_and_one_past_it(fw):
    s = Sleep(fw)
    week = 7 * 24 * 3600
    s.read(b'until %d' % (s.world.now + week))
    assert sl(fw).at == s.boot + week
    s.read(b'until %d' % (s.world.now + week + 1))
    assert sl(fw).at == 0 and sl(fw).owner == 0 and (b'/tmp/q2-foo.sleep',) in s.world.of('unlink')


@pytest.mark.parametrize('raw', [b'until ', b'until x', b'until 12x', b'until 1 2', b'until 123456789012', b'whatever', b'untilx 5'])
def test_a_request_this_app_cant_mean_is_dropped(fw, raw):
    s = Sleep(fw)
    s.read(raw)
    assert sl(fw).at == 0 and sl(fw).owner == 0 and s.paused() == 0
    assert s.world.of('unlink') == [(b'/tmp/q2-foo.sleep',)]


def test_a_time_just_past_pauses_at_once(fw):
    s = Sleep(fw)
    s.read(b'until %d' % (s.world.now - 59))
    assert s.paused() == 1 and sl(fw).owner == 0


def test_a_stale_time_is_let_go(fw):
    s = Sleep(fw)
    s.read(b'until %d' % (s.world.now - 60))
    assert s.paused() == 0 and sl(fw).owner == 0


def test_the_same_file_read_again_changes_nothing(fw):
    s = Sleep(fw)
    s.read(b'until %d' % (s.world.now + 600))
    s.boot += 300
    s.read(b'until %d' % (s.world.now + 600))
    assert sl(fw).at == 5600


def test_removing_the_file_cancels_it(fw):
    s = Sleep(fw)
    s.read(b'until %d' % (s.world.now + 600))
    s.read(None)
    assert sl(fw).at == 0 and sl(fw).owner == 0 and s.world.of('unlink') == []


def test_another_apps_timer_gives_way_its_file_removed(fw):
    s = Sleep(fw)
    s.read(b'until %d' % (s.world.now + 600))
    s.read(b'until %d' % (s.world.now + 900), slot=1)
    assert sl(fw).owner == 2 and sl(fw).at == s.boot + 900 and s.world.of('unlink') == [(b'/tmp/q2-foo.sleep',)]


def test_an_app_removing_a_file_that_isnt_the_timer_leaves_it(fw):
    s = Sleep(fw)
    s.read(b'until %d' % (s.world.now + 600), slot=1)
    s.read(None, slot=0)
    assert sl(fw).owner == 2


def test_hostile_sleep_files(fw):
    s = Sleep(fw)
    for raw in hostile(43, [b'until 1000600\n', b'until 99999999999'], 800, b' 0159\n'):
        if raw.startswith(b'end'):  # reads the stock queue's position, at its address on the Q2
            continue
        s.read(raw)
        assert sl(fw).at == 0 or sl(fw).at <= s.boot + 7 * 24 * 3600


def check(fw):
    fw.fn('addon__sleep_check', None)()


def test_a_timer_due_pauses_and_ends(fw):
    s = Sleep(fw)
    s.read(b'until %d' % (s.world.now + 600))
    s.boot += 599
    check(fw)
    assert s.paused() == 0 and sl(fw).owner == 1
    s.boot += 1
    check(fw)
    assert s.paused() == 1 and sl(fw).at == 0 and s.world.of('unlink') == [(b'/tmp/q2-foo.sleep',)]


def test_a_timer_slept_through_is_let_go(fw):
    s = Sleep(fw)
    s.read(b'until %d' % (s.world.now + 600))
    s.boot += 660
    check(fw)
    assert s.paused() == 0 and sl(fw).at == 0 and sl(fw).owner == 0


def test_a_restart_waits_for_the_loop_to_go(fw):
    world = World(fw, {b'/tmp/q2-foo': b'1'})
    a = ap(fw).a[0]
    a.pid, a.run, a.id, a.restart, a.restart_at = b'/tmp/q2-foo', b'run', b'foo', 1, 1000
    poll = fw.fn('addon__poll_one', None, C.POINTER(App))
    world.ms = 2000
    poll(C.pointer(a))
    assert a.restart == 1 and a.launched == 0
    del world.files[b'/tmp/q2-foo']
    world.ms = 3000
    poll(C.pointer(a))
    assert a.restart == 0 and a.launched == 1 and world.of('system') == [(b'run',)]


def test_a_restart_gives_up_on_the_old_loop(fw):
    world = World(fw, {b'/tmp/q2-foo': b'1'})
    a = ap(fw).a[0]
    a.pid, a.run, a.restart, a.restart_at = b'/tmp/q2-foo', b'run', 1, 1000
    world.ms = 21001
    fw.fn('addon__poll_one', None, C.POINTER(App))(C.pointer(a))
    assert a.launched == 1


def test_an_app_stopped_for_usb_starts_when_the_card_is_back(fw):
    world = World(fw, {b'/bin': b''})
    a = ap(fw).a[0]
    a.bin, a.run, a.relaunch = b'/bin', b'run', 1
    window = [1]
    fw.mock('navigator_window_is_exist', lambda *_: window[0])
    poll = fw.fn('addon__poll_one', None, C.POINTER(App))
    world.ms = 2000
    poll(C.pointer(a))
    assert a.relaunch == 1
    window[0], world.ms = 0, 3000
    poll(C.pointer(a))
    assert a.relaunch == 0 and a.launched == 1


def test_polls_are_paced(fw):
    world = World(fw)
    a = ap(fw).a[0]
    a.restart, a.polled, a.run = 1, 1000, b'run'
    world.ms = 1499
    fw.fn('addon__poll_one', None, C.POINTER(App))(C.pointer(a))
    assert a.launched == 0 and a.polled == 1000


def test_a_stopped_app_is_left_alone(fw):
    world = World(fw)
    world.ms = 5000
    a = ap(fw).a[0]
    fw.fn('addon__poll_one', None, C.POINTER(App))(C.pointer(a))
    assert a.polled == 0


def test_launch_waits_for_the_apps_page(fw):
    world = World(fw)
    a = ap(fw).a[0]
    a.id, a.run = b'foo', b'run'
    fw.fn('addon__launch', None, C.POINTER(App))(C.pointer(a))
    assert a.launched == 1 and world.of('system') == [(b'run',)] and a.launch_at == 1000


def test_stop_all_asks_each_running_app(fw):
    world = World(fw, {b'/tmp/q2-b': b'1'})
    a = ap(fw)
    a.n = 3
    a.a[0].pid, a.a[0].launched, a.a[0].hold = b'/tmp/q2-a', 1, 1
    a.a[1].pid = b'/tmp/q2-b'
    a.a[2].pid, a.a[2].relaunch = b'/tmp/q2-c', 1
    fw.fn('addon_stop', None)()
    assert len(world.of('system')) == 2 and a.a[0].launched == 0 and a.a[0].hold == 0 and a.a[2].relaunch == 0


def test_the_card_going_away_stops_apps_and_marks_them_to_start_again(fw):
    world = World(fw, {b'/tmp/q2-b': b'1'})
    a = ap(fw)
    a.n = 3
    a.a[0].pid, a.a[0].launched = b'/tmp/q2-a', 1
    a.a[1].pid = b'/tmp/q2-b'
    a.a[2].pid = b'/tmp/q2-c'
    fw.mock('sleep_ms', lambda *_: world.files.clear())
    fw.fn('addon_card_away', None)()
    assert [(a.a[i].relaunch, a.a[i].launched) for i in range(3)] == [(1, 0), (1, 0), (0, 0)]
    assert len(world.of('system')) == 1 and len(fw.calls('sleep_ms')) == 1


def test_the_card_going_away_waits_three_seconds_at_most(fw):
    world = World(fw, {b'/tmp/q2-a': b'1'})
    a = ap(fw)
    a.n, a.a[0].pid = 1, b'/tmp/q2-a'
    fw.mock('sleep_ms', lambda ms, *_: setattr(world, 'ms', world.ms + ms))
    fw.fn('addon_card_away', None)()
    assert len(fw.calls('sleep_ms')) == 60


def card_id(fw, path):
    out = block(fw, size=16)
    return C.string_at(out) if fw.fn('addon__card_id', C.c_int, C.c_char_p, C.c_void_p)(path, out) else None


@pytest.mark.parametrize('path,found', [
    (b'/mnt/mmc/.foo/a.flac', b'foo'), (b'/mnt/mmc/.' + b'a' * 15 + b'/x', b'a' * 15), (b'/mnt/mmc/.a-b/', b'a-b'),
    (b'/mnt/mmc/.' + b'a' * 16 + b'/x', None), (b'/mnt/mmc/.foo', None), (b'/mnt/mmc/./x', None), (b'/mnt/mmc/foo/x', None),
    (b'/mnt/mmc/.Foo/x', None), (b'/mnt/mmc/.a b/x', None), (b'', None), (b'/mnt/mmc/.', None),
])
def test_the_app_a_path_is_in(fw, path, found):
    World(fw)
    assert card_id(fw, path) == found


class Page:
    """A card app's page driven as the UI drives it. The worker never runs: answer() puts its reply
    where the pthread would and the page's tick takes it."""
    def __init__(self, fw, manifest=b'name=Foo\n', opens=True):
        self.fw, self.sel, self.timers, self.drawn, self.toasts, self.backs = fw, 0, 0, 0, [], 0
        self.world = card(fw, foo=manifest)
        self.world.files[b'/tmp/q2-foo'] = b'1'
        self.page, self.view = block(fw, size=4096), block(fw, size=4096)  # widgets' bytes, all zero
        self.ap, self.pg, self.req = ap(fw), fw.var('addon__pg', Pg), fw.var('addon__req', Req)
        def timer(*_):
            self.timers += 1
            return self.timers
        def lists(*_):
            self.drawn = 0
            return self.view
        def row(*_):
            self.drawn += 1
            return self.view
        def toast(path, info, *_):
            self.toasts.append(C.string_at(info + 8))
        def prop(w, name, default, *_):
            return self.sel if C.string_at(name) == b'_ringnav_index' else default
        fw.mock('fw_pthread_create', lambda *_: 0)
        fw.mock('timer_add', timer)
        fw.mock('window_create', lambda *_: self.page)
        fw.mock('scroll_view_create', lists)
        fw.mock('list_item_create', row)
        fw.mock('widget_get_child', lambda *_: self.view)
        fw.mock('widget_lookup', lambda *_: self.view)
        fw.mock('navigator_to_with_context', toast)
        fw.mock('navigator_back', lambda *_: setattr(self, 'backs', self.backs + 1))
        fw.mock('widget_get_prop_int', prop)
        scan(fw)
        if opens:
            fw.fn('addon_open', C.c_int, C.c_void_p, C.c_void_p)(0, None)

    @property
    def top(self):
        return self.pg.lv[self.pg.depth - 1]

    def request(self):
        return C.string_at(self.req.buf)[:self.req.len]

    def tick(self):
        self.fw.fn('addon__tick', C.c_int, C.c_void_p)(None)

    def answer(self, status=b'ok', msg=b'', cursor=b'', rows=(), id=None, fail=REQ_OK, raw=None):
        data = raw if raw is not None else b'Q2SPOT/1\n%d\t%s\t%s\t%s\n' % (self.req.id if id is None else id, status, msg, cursor) + \
            b''.join(b'\t'.join(r) + b'\n' for r in rows)
        C.memmove(self.req.buf, data + b'\0', len(data) + 1)
        self.req.n, self.req.fail, self.req.done = len(data), fail, 1
        self.tick()

    def press(self, i):
        self.fw.fn('addon__clicked', C.c_int, C.c_void_p, C.c_void_p)(i, None)

    def click(self, i):
        self.press(i)
        self.fw.fn('addon__go', C.c_int, C.c_void_p)(None)

    def back(self):
        self.fw.fn('addon__leave', C.c_int, C.c_void_p)(None)

    def cell(self, i, k):
        return self.fw.fn('addon__cell', C.c_char_p, C.c_int, C.c_int)(i, k)


def item(n, kind=b'action', ctx=b'', hold=b''):
    return (kind, b'op%d' % n, b'Row %d' % n, b'sub', ctx, hold)


def test_a_page_opens_on_the_apps_menu_and_starts_it(fw):
    p = Page(fw)
    assert p.request() == b'Q2SPOT/1\n1\tmenu\n' and C.string_at(p.req.path) == b'/tmp/q2-foo.browse.sock'
    assert p.ap.a[0].launched == 1 and p.top.title == b'Foo'
    assert p.world.of('system') == [(b'/bin/sh /mnt/mmc/.foo/run',)]


def test_rows_show_with_a_trailing_more_when_theres_a_cursor(fw):
    p = Page(fw)
    p.answer(rows=[item(1), item(2)])
    assert p.top.state == K_READY and p.top.n == 2 and p.drawn == 2
    p.back()
    p.fw.fn('addon__fetch', None, C.POINTER(Lv))(p.pg.lv)
    p.answer(cursor=b'c', rows=[item(1), item(2)])
    assert p.drawn == 3


def test_a_list_row_opens_its_level_with_its_context(fw):
    p = Page(fw)
    p.answer(rows=[item(1, b'list', b'c ctx')])
    p.click(0)
    assert p.pg.depth == 2 and p.top.op == b'op1' and p.top.args == b'\tc ctx' and p.top.title == b'Row 1'
    assert p.request().endswith(b'\top1\tc ctx\n')


def test_a_level_under_another_lets_its_reply_go_and_asks_again_on_back(fw):
    p = Page(fw)
    p.answer(rows=[item(1, b'list')])
    p.click(0)
    assert p.pg.lv[0].state == K_LOAD and p.pg.lv[0].n == 0 and p.pg.pages == 0
    p.answer(rows=[item(2)])
    p.back()
    assert p.pg.depth == 1 and p.top.state == K_LOAD and p.request().endswith(b'\tmenu\n')


def test_an_action_sends_its_context_toasts_and_asks_again(fw):
    p = Page(fw)
    p.answer(rows=[item(1, b'action', b'a%25b')])
    assert p.cell(0, 4) == b'a%b'
    p.click(0)
    assert p.request().endswith(b'\top1\ta%25b\n') and p.pg.act == 1
    p.answer(msg=b'Done')
    assert p.toasts == [b'Done'] and p.request().endswith(b'\tmenu\n')


def test_an_action_refused_toasts_why(fw):
    p = Page(fw)
    p.answer(rows=[item(1)])
    p.click(0)
    p.answer(status=b'error')
    assert p.toasts == [b"Foo didn't answer"]


def test_the_stop_action_leaves_the_page(fw):
    p = Page(fw)
    p.answer(rows=[(b'action', b'stop', b'Stop', b'', b'', b'')])
    p.click(0)
    p.answer()
    assert p.ap.a[0].launched == 0 and p.backs == 1


def test_an_action_reply_with_rows_is_malformed(fw):
    p = Page(fw)
    p.answer(rows=[item(1)])
    p.click(0)
    p.answer(rows=[item(2)])
    assert p.toasts == [b"Foo didn't answer"]


def test_info_rows_do_nothing(fw):
    p = Page(fw)
    p.answer(rows=[item(1, b'info')])
    calls = len(fw.calls('fw_pthread_create'))
    p.click(0)
    assert len(fw.calls('fw_pthread_create')) == calls and p.pg.depth == 1


def test_an_arg_too_long_for_its_level_opens_nothing(fw):
    p = Page(fw)
    p.answer(rows=[item(1, b'list', b'c' * 399), item(2, b'list', b'c' * 398)])
    p.click(0)
    assert p.pg.depth == 1
    p.click(1)
    assert p.pg.depth == 2 and len(p.top.args) == 399


def test_a_request_too_long_is_never_sent(fw):
    p = Page(fw)
    calls = len(fw.calls('fw_pthread_create'))
    fw.fn('addon__req_send', None, C.c_char_p, C.c_char_p, C.c_char_p)(b'/s', b'op', b'\t' + b'x' * (8 << 10))
    assert p.req.len == 0 and p.req.running == 0 and p.req.done == 1 and len(fw.calls('fw_pthread_create')) == calls


def test_an_error_offers_retry_and_a_stop(fw):
    p = Page(fw)
    p.answer(status=b'error', msg=b'Nope')
    assert p.top.state == K_ERROR and p.top.note == b'Nope' and p.drawn == 2
    p.click(0)
    assert p.top.state == K_LOAD


def test_a_malformed_reply_is_an_error_naming_the_app(fw):
    p = Page(fw)
    p.answer(raw=b'nonsense')
    assert p.top.state == K_ERROR and p.top.note == b"Foo didn't answer"


def test_a_reply_to_another_request_is_dropped(fw):
    p = Page(fw)
    p.answer(id=p.req.id + 1, rows=[item(1)])
    assert p.top.state == K_ERROR


def test_no_socket_while_it_starts_waits(fw):
    p = Page(fw)
    p.world.ms += 500
    p.answer(fail=REQ_NOSOCK, raw=b'')
    assert p.top.state == K_WAIT and p.pg.again == p.world.ms + 500


def test_no_socket_long_after_says_so_and_retry_restarts_it(fw):
    p = Page(fw)
    p.world.ms += 20000
    p.answer(fail=REQ_NOSOCK, raw=b'')
    assert p.top.state == K_ERROR and p.top.nosock == 1 and p.top.note == b"Foo isn't running"
    p.click(0)
    assert p.ap.a[0].restart == 1 and p.top.state == K_LOAD
    assert p.world.of('system')[-1] == (b'kill $(cat /tmp/q2-foo) 2>/dev/null || rm -f /tmp/q2-foo',)


def test_a_slow_start_offers_to_stop_it(fw):
    p = Page(fw)
    p.world.ms += 2999  # since is now | 1
    p.tick()
    assert p.top.slow == 0
    p.world.ms += 2
    p.tick()
    assert p.top.slow == 1 and p.drawn == 1
    p.click(0)
    assert p.toasts == [b'Foo stopped'] and p.backs == 1


def test_a_stop_on_an_error_page_is_its_second_row(fw):
    p = Page(fw)
    p.answer(status=b'error')
    p.click(1)
    assert p.backs == 1 and p.ap.a[0].launched == 0


def test_more_is_a_level_of_its_own_at_the_newest_cursor(fw):
    p = Page(fw)
    p.answer(rows=[item(1, b'list', b'x')])
    p.click(0)
    p.answer(cursor=b'c1', rows=[item(2)])
    p.click(1)
    assert p.pg.depth == 3 and p.top.op == b'op1' and p.top.args == b'\tx\tc1' and p.top.base == 2
    p.answer(cursor=b'c2', rows=[item(3)])
    p.click(1)
    assert p.top.args == b'\tx\tc2'


def rows_of(start, count):
    return [item(k) for k in range(start, start + count)]


def test_pages_are_followed_and_appended_up_to_their_limit(fw):
    p = Page(fw)
    p.answer(msg=b'pages', cursor=b'c0', rows=rows_of(0, ADDON_ROWS))
    for page in range(1, ADDON_PAGES):
        assert p.pg.paging == 1 and p.request().endswith(b'\tmenu\tc%d\n' % (page - 1))
        p.answer(msg=b'pages', cursor=b'c%d' % page, rows=rows_of(page * ADDON_ROWS, ADDON_ROWS))
    assert p.pg.paging == 0 and p.pg.pages == ADDON_PAGES and p.top.n == ADDON_PAGES * ADDON_ROWS
    assert p.cell(0, 1) == b'op0' and p.cell(499, 1) == b'op499'
    assert not (p.req.running or p.req.done)  # the list ends at More…


def test_a_failed_page_keeps_the_rows_and_their_more(fw):
    p = Page(fw)
    p.answer(msg=b'pages', cursor=b'c0', rows=rows_of(0, 2))
    p.answer(status=b'error')
    assert p.top.state == K_READY and p.top.n == 2 and p.pg.paging == 0 and p.pg.f[3] == b'c0'


def test_loading_does_nothing_and_a_press_on_it_is_dropped(fw):
    p = Page(fw)
    p.answer(msg=b'pages', cursor=b'c0', rows=rows_of(0, 2))
    p.press(2)
    assert p.pg.go == 0


def test_a_list_without_pages_keeps_more(fw):
    p = Page(fw)
    p.answer(msg=b'', cursor=b'c0', rows=rows_of(0, 2))
    assert p.pg.paging == 0 and p.drawn == 3


def live(fw, rows):
    p = Page(fw)
    fw.mock('window_manager_get_top_window', lambda *_: p.page)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.answer(msg=b'refresh', rows=rows)
    return p


def test_a_live_level_asks_again_on_its_time(fw):
    p = live(fw, rows_of(0, 1))
    assert p.top.live == 1 and p.pg.again == p.world.ms + 2000
    p.world.ms += 1999
    p.tick()
    assert not p.req.done and not p.req.running
    p.world.ms += 1
    p.tick()
    assert p.req.running == 1 and p.request().endswith(b'\tmenu\n')


def test_a_refresh_with_the_same_rows_draws_nothing(fw):
    p = live(fw, rows_of(0, 2))
    drawn = len(fw.calls('list_item_create'))
    p.world.ms += 2000
    p.tick()
    p.answer(msg=b'refresh', rows=rows_of(0, 2))
    assert len(fw.calls('list_item_create')) == drawn and p.cell(1, 1) == b'op1'


def test_a_refresh_with_new_rows_keeps_the_selection(fw):
    p = live(fw, rows_of(0, 2))
    p.sel = 1
    p.world.ms += 2000
    p.tick()
    p.answer(msg=b'refresh', rows=rows_of(5, 3))
    assert p.top.n == 3 and p.top.sel == 1 and p.cell(2, 1) == b'op7'


def test_a_failed_refresh_shows_the_error(fw):
    p = live(fw, rows_of(0, 2))
    p.world.ms += 2000
    p.tick()
    p.answer(status=b'error')
    assert p.top.state == K_ERROR and p.top.live == 1


def fire(p):
    """The page's next timer pass: a queued click's go(), if it is still queued."""
    if p.pg.go:
        p.fw.fn('addon__go', C.c_int, C.c_void_p)(None)


def queued(p, row, **reply):
    """A click on row of a live level's five, then, in the same timer pass and first, its refresh."""
    p.world.ms += 2000
    p.tick()
    p.press(row)
    p.answer(**reply)
    fire(p)


@pytest.mark.parametrize('rows', [rows_of(0, 1), rows_of(5, 5)])
def test_a_refresh_with_new_rows_drops_a_click_on_its_old_ones(fw, rows):
    p = live(fw, rows_of(0, 5))
    queued(p, 4, msg=b'refresh', rows=rows)
    assert p.pg.go == 0 and not p.req.running  # neither op4 nor what row 4 holds now (op9) was asked


def test_an_error_page_drops_a_click_on_the_rows_before_it(fw):
    p = live(fw, rows_of(0, 5))
    queued(p, 1, status=b'error')
    # Row 1 is now [Retry, Stop]'s Stop: the click doesn't end the app
    assert p.top.state == K_ERROR and p.pg.go == 0
    assert b'Foo stopped' not in p.toasts and p.backs == 0 and p.ap.a[0].launched == 1


def test_a_refresh_with_the_same_rows_keeps_the_click(fw):
    p = live(fw, rows_of(0, 5))
    queued(p, 4, msg=b'refresh', rows=rows_of(0, 5))
    assert p.req.running and p.request().endswith(b'\top4\n')  # row 4's action


def test_a_search_box_s_redraw_stays_queued_over_a_refresh(fw):
    p = live(fw, rows_of(0, 5))
    p.world.ms += 2000
    p.tick()
    p.press(4)
    p.pg.next = 1  # G_SHOW: a search box's blur
    p.answer(msg=b'refresh', rows=rows_of(0, 1))
    assert p.pg.go != 0


def test_a_held_play_sends_a_rows_hold_op(fw):
    p = Page(fw)
    p.answer(rows=[item(1, b'list', b'c', b'dl'), item(2), (b'progress', b'u', b't', b's', b'', b'500')])
    hold = fw.fn('addon_hold', C.c_int, C.c_void_p)
    p.sel = 0
    assert hold(p.page) == 1 and p.request().endswith(b'\tdl\tc\n')
    p.answer()
    p.answer(rows=[item(1, b'list', b'c', b'dl'), item(2), (b'progress', b'u', b't', b's', b'', b'500')])
    p.sel = 1
    assert hold(p.page) == 0
    p.sel = 2
    assert hold(p.page) == 0     # a progress row's position is its bar
    p.sel = 3
    assert hold(p.page) == 0
    assert hold(None) == 0


def test_the_search_box_opens_a_level_for_its_text(fw):
    p = Page(fw)
    p.answer(rows=[(b'search', b'', b'Search', b'', b'', b'')])
    p.click(0)
    assert p.pg.view is None  # the box alone
    def typed(w, out, n, *_):
        C.memmove(out, b'a b\0', 4)
    fw.mock('widget_get_text_utf8', typed)
    fw.fn('addon__searched', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    assert p.top.op == b'search' and p.top.args == b'\ta b' and p.top.title == b'a b' and p.top.base == 4


def test_an_empty_search_goes_back_to_the_menu(fw):
    p = Page(fw)
    p.answer(rows=[(b'search', b'', b'Search', b'', b'', b'')])
    p.click(0)
    fw.fn('addon__searched', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    assert p.pg.depth == 1


def test_levels_past_the_depth_forget_the_oldest(fw):
    p = Page(fw)
    p.answer(rows=[item(0, b'list')])
    for n in range(1, 12):
        p.click(0)
        assert p.pg.depth <= ADDON_DEPTH
        p.answer(rows=[item(n, b'list')])
        assert p.cell(0, 1) == b'op%d' % n
    assert p.pg.depth == ADDON_DEPTH and p.pg.lv[0].op == b'menu'


def test_closing_frees_it_all(fw):
    p = Page(fw)
    p.answer(rows=[item(1)])
    fw.fn('addon__closed', C.c_int, C.c_void_p, C.c_void_p)(p.page, None)
    assert not p.pg.page and p.pg.depth == 0 and p.pg.pages == 0


def test_a_replaced_pages_close_is_ignored(fw):
    p = Page(fw)
    fw.fn('addon__closed', C.c_int, C.c_void_p, C.c_void_p)(p.view, None)
    assert p.pg.page


def test_a_cursor_too_long_for_its_level_opens_nothing(fw):
    p = Page(fw)
    p.answer(cursor=b'c' * 395, rows=[item(1)])
    p.click(1)
    assert p.pg.depth == 2                # fits: the base is empty
    p.answer(cursor=b'c' * 399, rows=[item(1)])
    p.click(1)
    assert p.pg.depth == 2                # doesn't


def test_top_app_cell_and_put(fw):
    p = Page(fw)
    p.answer(rows=[item(1)])
    assert fw.fn('addon__top', C.POINTER(Lv))().contents.op == b'menu'
    assert fw.fn('addon__app', C.POINTER(App))().contents.id == b'foo'
    assert p.cell(0, 2) == b'Row 1' and p.cell(0, 3) == b'sub'
    put = fw.fn('addon__put', C.c_int, C.c_void_p, C.c_uint, C.c_char_p)
    out = block(fw, size=8)
    assert put(out, 8, b'a%') == 1 and C.string_at(out) == b'\ta%25'
    assert put(block(fw, size=5), 5, b'abc') == 1 and put(block(fw, size=4), 4, b'abc') == 0 and put(block(fw, size=1), 1, b'') == 0


@pytest.mark.parametrize('kind,uri,yes', [
    (b'list', b'x', 1), (b'remote', b'', 1), (b'local', b'/mnt/mmc/a/', 1), (b'local', b'/mnt/mmc/a.flac', 0), (b'local', b'', 0),
    (b'action', b'x/', 0), (b'info', b'', 0), (b'', b'', 0),
])
def test_a_row_that_opens(fw, kind, uri, yes):
    p = Page(fw)
    p.answer(rows=[(kind, uri, b't', b's', b'', b'')])
    assert fw.fn('addon__opens', C.c_int, C.c_int)(0) == yes


def test_drop_frees_every_page(fw):
    p = Page(fw)
    p.answer(msg=b'pages', cursor=b'c', rows=rows_of(0, 1))
    p.answer(rows=rows_of(1, 1))
    assert p.pg.pages == 2
    p.pg.paging = 1
    fw.fn('addon__drop', None)()
    assert p.pg.pages == 0 and p.pg.paging == 0


@pytest.mark.parametrize('position,permille', [(b'500', 500), (b'0', 0), (b'1000', 1000), (b'1001', 1000), (b'-5', 0), (b'abc', 0), (b'', 0),
                                               (b'99999999999999999999999', 1000), (b'-99999999999999999999999', 0)])
def test_a_progress_rows_bar_is_clamped(fw, position, permille):
    p = Page(fw)
    set_props = []
    fw.mock('widget_set_prop_int', lambda w, name, value, *_: set_props.append((C.string_at(name), value)))
    p.answer(rows=[(b'progress', b'u', b't', b's', b'', position)])
    assert (b'_pml', permille) in set_props


def test_a_progress_row_does_nothing(fw):
    p = Page(fw)
    p.answer(rows=[(b'progress', b'u', b't', b's', b'', b'5')])
    calls = len(fw.calls('fw_pthread_create'))
    p.click(0)
    assert fw.fn('addon_hold', C.c_int, C.c_void_p)(p.page) == 0
    assert len(fw.calls('fw_pthread_create')) == calls and p.pg.depth == 1


def test_a_progress_bar_paints_its_share(fw):
    p = Page(fw)
    world = p.world
    lcd, canvas, bar = block(fw, size=4096), block(fw, size=4096), block(fw, size=4096)
    C.c_void_p.from_address(canvas + CANVAS_LCD).value = lcd
    C.c_int.from_address(bar + W_W).value, C.c_int.from_address(bar + W_H).value = 300, 8
    fw.mock('widget_get_prop_int', lambda w, name, default, *_: 250 if C.string_at(name) == b'_pml' else default)
    fills = []
    fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: fills.append((x, y, w, h)))
    fw.fn('addon_paint', None, C.c_void_p, C.c_void_p)(bar, canvas)
    assert fills == [(0, 0, 300, 8), (0, 0, 75, 8)]


KINDS = [b'list', b'action', b'search', b'remote', b'info', b'progress', b'local', b'junk', b'']
STATUS = [b'ok', b'ok', b'ok', b'ok', b'error', b'']


def noise(r):
    return bytes(r.choice(b'abc,:/. 0123456789') for _ in range(r.choice((0, 1, 8, 30, 500))))


def hostile_row(r):
    kind = r.choice(KINDS)
    uri = noise(r)
    if kind == b'local':
        uri = b'/mnt/mmc/x/'  # a folder's page; songs_open itself is navigation.c's
    return (kind, uri, noise(r), noise(r), r.choice((b'', noise(r))), r.choice((b'', b'hold', noise(r))))


def test_hostile_replies_through_the_page(fw):
    """The whole page under what a broken app might send, presses and Backs between."""
    r = random.Random(44)
    p = Page(fw, b'name=Foo\nqr=1\n')
    fw.mock('fw_access', lambda *_: r.randrange(2))
    hold = fw.fn('addon_hold', C.c_int, C.c_void_p)
    for _ in range(2500):
        p.world.ms += r.choice((1, 100, 2500))
        if p.req.running or p.req.done:
            kind = r.random()
            if kind < 0.1:
                p.answer(raw=bytes(r.choice(b'Q2SPOT/1\n\t%0a') for _ in range(40)))
            elif kind < 0.15:
                p.answer(fail=r.choice((REQ_FAIL, REQ_NOSOCK)), raw=b'')
            elif kind < 0.2:
                p.answer(id=p.req.id + 1, rows=[hostile_row(r)])
            elif p.pg.act:
                p.answer(status=r.choice(STATUS), msg=noise(r))
            else:
                p.answer(status=r.choice(STATUS), msg=r.choice((b'', b'pages', b'refresh', noise(r))), cursor=r.choice((b'', noise(r))),
                         rows=[hostile_row(r) for _ in range(r.choice((0, 1, 3, ADDON_ROWS)))])
        elif p.pg.depth > 1 and r.random() < 0.15:
            p.back()
        elif p.drawn:
            p.sel = r.randrange(p.drawn)
            if r.random() < 0.1:
                hold(p.page)
            else:
                p.click(p.sel)
        else:
            p.tick()
            if not (p.req.running or p.req.done) and p.pg.depth > 1:
                p.back()
    fw.fn('addon__closed', C.c_int, C.c_void_p, C.c_void_p)(p.page, None)


def goes(fw, path):
    return fw.fn('addon_goes', C.c_int, C.c_char_p)(path)


def test_go_to_outside_an_apps_folder_is_stocks(fw):
    Page(fw, opens=False)
    assert goes(fw, b'/mnt/mmc/Music/a.flac') == 1


def test_go_to_in_an_apps_folder_needs_its_say_so(fw):
    Page(fw, b'name=Foo\ngoto=1\n', opens=False)
    assert goes(fw, b'/mnt/mmc/.foo/a.flac') == 1
    assert goes(fw, b'/mnt/mmc/.nope/a.flac') == 0


def test_no_goto_in_the_manifest_no_go_to(fw):
    Page(fw, b'name=Foo\n', opens=False)
    assert goes(fw, b'/mnt/mmc/.foo/a.flac') == 0


def goto(fw, path, title=b'T', album=1):
    return fw.fn('addon_goto', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(path, title, album)


def test_go_to_opens_the_apps_page_at_the_song(fw):
    p = Page(fw, b'name=Foo\ngoto=1\n', opens=False)
    assert goto(fw, b'/mnt/mmc/.foo/a%b.flac') == 1
    assert p.top.op == b'goto' and p.top.args == b'\talbum\t/mnt/mmc/.foo/a%25b.flac' and p.top.title == b'T' and p.top.base == 31


def test_go_to_artist_with_no_title_is_titled_by_the_app(fw):
    p = Page(fw, b'name=Foo\ngoto=1\n', opens=False)
    assert goto(fw, b'/mnt/mmc/.foo/a.flac', b'', 0) == 1
    assert p.top.args.startswith(b'\tartist\t') and p.top.title == b'Foo'


def test_go_to_replaces_the_page_already_open(fw):
    p = Page(fw, b'name=Foo\ngoto=1\n')
    p.answer(rows=[item(1)])
    assert goto(fw, b'/mnt/mmc/.foo/a.flac') == 1
    assert p.pg.depth == 1 and p.top.op == b'goto' and len(fw.calls('window_manager_close_window_force')) == 1


def test_go_to_a_path_too_long_for_a_level_opens_nothing(fw):
    p = Page(fw, b'name=Foo\ngoto=1\n', opens=False)
    assert goto(fw, b'/mnt/mmc/.foo/' + b'a' * 400) == 0 and not p.pg.page


def test_an_app_gone_from_the_card_says_so(fw):
    p = Page(fw, opens=False)
    del p.world.files[b'/mnt/mmc/.foo/q2-foo']
    fw.fn('addon_open', C.c_int, C.c_void_p, C.c_void_p)(0, None)
    assert p.top.state == K_ERROR and p.top.note == b"Foo isn't on the card" and p.world.of('system') == []


def test_a_row_past_the_list_opens_nothing(fw):
    p = Page(fw, opens=False)
    fw.fn('addon_open', C.c_int, C.c_void_p, C.c_void_p)(5, None)
    fw.fn('addon_open', C.c_int, C.c_void_p, C.c_void_p)(-1, None)
    assert not p.pg.page


def test_a_qr_app_shows_its_code_at_the_first_answer(fw):
    p = Page(fw, b'name=Foo\nqr=1\n')
    del p.world.files[b'/tmp/q2-foo']  # not running yet, so it starts
    qp = fw.var('addon__qp', Qp)
    p.answer(rows=[item(1)])
    assert qp.page and p.pg.code == 0


class Qr:
    def __init__(self, fw, raw):
        self.fw, self.texts, self.shown = fw, [], {}
        self.world = World(fw, {} if raw is None else {b'/tmp/q2-a.state': raw})
        self.ids = [100]
        def widget(*_):
            self.ids[0] += 8
            return self.ids[0]
        fw.mock('window_create', lambda *_: 99)
        fw.mock('widget_factory_create_widget', widget)
        fw.mock('hscroll_label_create', widget)
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.append(C.string_at(s)))
        fw.mock('widget_set_visible', lambda w, on, *_: self.shown.__setitem__(w, on))
        self.qp = fw.var('addon__qp', Qp)
        fw.fn('addon__qr_open', None, C.c_char_p, C.c_char_p)(b'/tmp/q2-a.state', b'Title')

    def again(self, raw):
        self.world.files[b'/tmp/q2-a.state'] = raw
        self.fw.fn('addon__qr_refresh', None)()

    @property
    def code(self):
        return self.shown.get(self.qp.qr)


HEX21 = b'f' * (6 * 21)


def qr_state(size=21, code=HEX21, addr=b'192.168.1.2:8080', pin=b'548001', note=b'Hi'):
    return b'addr=%s\nhold=1\npin=%s\nnote=%s\nqrsize=%d\nqr=%s\n' % (addr, pin, note, size, code)


def test_a_code_shows_with_its_address_and_spaced_pin(fw):
    q = Qr(fw, qr_state())
    assert q.code == 1 and C.string_at(q.qp.hex) == HEX21 and q.qp.size == 21
    assert b'192.168.1.2:8080' in q.texts and b'548 001' in q.texts and b'Hi' in q.texts


def test_a_pin_of_another_length_shows_as_it_is(fw):
    assert b'12345' in Qr(fw, qr_state(pin=b'12345')).texts


@pytest.mark.parametrize('raw,say', [
    (None, b'Not running'), (b'', b'Not running'),
    (b'hold=1\n', b'Connect the Q2 to Wi-Fi'),
    (b'addr=x\n', b'No code yet'),
    (qr_state(size=20), b'No code yet'), (qr_state(size=178), b'No code yet'),
    (qr_state(code=HEX21[:-1]), b'No code yet'), (qr_state(code=HEX21 + b'f'), b'No code yet'),
    (qr_state(addr=b''), b'Connect the Q2 to Wi-Fi'),
])
def test_no_code_says_why(fw, raw, say):
    q = Qr(fw, raw)
    assert not q.code and say in q.texts


def test_a_code_longer_than_its_buffer_is_cut_and_never_shown(fw):
    q = Qr(fw, qr_state(size=61, code=b'f' * (16 * 61)))
    assert not q.code and len(C.string_at(q.qp.hex)) < QR_HEX


def test_the_same_file_again_writes_nothing(fw):
    q = Qr(fw, qr_state())
    count = len(q.texts)
    fw.fn('addon__qr_refresh', None)()
    assert len(q.texts) == count


def test_closing_the_code_frees_it(fw):
    q = Qr(fw, qr_state())
    fw.fn('addon__qr_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert not q.qp.page and not q.qp.hex


def qr_rects(fw, q):
    lcd, canvas = block(fw, size=4096), block(fw, size=4096)
    C.c_void_p.from_address(canvas + CANVAS_LCD).value = lcd
    rects = []
    fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: rects.append((x, y, w, h)))
    fw.fn('addon_paint', None, C.c_void_p, C.c_void_p)(q.qp.qr, canvas)
    return rects


def test_a_full_row_of_modules_is_one_rectangle(fw):
    q = Qr(fw, qr_state())
    m, o = 186 // 25, (186 - 186 // 25 * 21) // 2
    assert qr_rects(fw, q) == [(o, o + y * m, 21 * m, m) for y in range(21)]


def test_modules_read_from_the_high_bit_and_a_bad_digit_is_blank(fw):
    code = b'800000' + b'000000' * 19 + b'0z8000'
    q = Qr(fw, qr_state(code=code))
    m, o = 7, (186 - 7 * 21) // 2
    assert qr_rects(fw, q) == [(o, o, m, m), (o + 8 * m, o + 20 * m, m, m)]


def test_hostile_status_files(fw):
    q = Qr(fw, b'')
    samples = [qr_state(), qr_state(size=25, code=b'a' * (7 * 25)), qr_state(size=49, code=b'0f' * 318 + b'0')]
    lcd, canvas = block(fw, size=4096), block(fw, size=4096)
    C.c_void_p.from_address(canvas + CANVAS_LCD).value = lcd
    paint = fw.fn('addon_paint', None, C.c_void_p, C.c_void_p)
    for raw in hostile(45, samples, 800, b'0af=z\n '):
        q.again(raw)
        if q.code:
            paint(q.qp.qr, canvas)  # a hidden widget isn't painted


def worker(fw, script, connect=0):
    """The app's worker over a socket whose recv hands out script; the reply buffer is req_send's size."""
    world = World(fw)
    req = fw.var('addon__req', Req)
    path = text(b'/tmp/q2-foo.browse.sock')
    req.buf, req.path, req.n, req.len, req.cancel = block(fw, b'Q2SPOT/1\n1\tmenu\n', REPLY_MAX + 2), C.addressof(path), 0, 16, 0
    script = list(script)
    def recv(fd, buf, n, flags, *_):
        piece = script.pop(0) if script else b''
        assert len(piece) <= n and n <= REPLY_MAX + 1 - req.n
        C.memmove(buf, piece, len(piece))
        return len(piece)
    def clock(which, ts, *_):
        world.now += 1
        t = C.cast(ts, C.POINTER(C.c_long))
        t[0], t[1] = world.now, 0
    fw.mock('fw_clock_gettime', clock)
    fw.mock('fw_socket', lambda *_: 5)
    fw.mock('fw_connect', lambda *_: connect)
    fw.mock('fw_select', lambda *_: 1)
    fw.mock('fw_send', lambda fd, buf, n, *_: n)
    fw.mock('fw_recv', recv)
    fw.fn('addon__worker', C.c_void_p, C.c_void_p)(None)
    return req


def test_a_reply_read_to_eof(fw):
    req = worker(fw, [b'Q2SPOT/1\n', b'1\tok\t\t\n'])
    assert req.done == 1 and req.fail == REQ_OK and C.string_at(req.buf) == b'Q2SPOT/1\n1\tok\t\t\n'


def test_no_socket(fw):
    assert worker(fw, [], connect=-1).fail == REQ_NOSOCK


def test_a_reply_of_exactly_the_limit_is_whole(fw):
    req = worker(fw, [b'x' * 32768, b'x' * 32768])
    assert req.fail == REQ_OK and req.n == REPLY_MAX


def test_a_reply_past_the_limit_fails_and_stays_in_its_buffer(fw):
    req = worker(fw, [b'x' * 32768, b'x' * 32768, b'y'])
    assert req.fail == REQ_FAIL and req.n == REPLY_MAX + 1


def line_end(fw, s):
    buf = block(fw, s)
    next_ = fw.fn('addon_library__line_end', C.c_void_p, C.c_void_p)(buf)
    return C.string_at(buf), next_ - buf


@pytest.mark.parametrize('s,line,at', [
    (b'a\nb', b'a', 2), (b'a\r\nb', b'a', 3), (b'a', b'a', 1), (b'a\r', b'a', 2), (b'', b'', 0), (b'\n', b'', 1), (b'\r', b'', 1), (b'\r\n', b'', 2),
    (b'a\r\r\nb', b'a\r', 4), (b'a\rb\nc', b'a\rb', 4), (b'\n\nb', b'', 1),
])
def test_a_lines_end(fw, s, line, at):
    assert line_end(fw, s) == (line, at)


def libup(fw):
    return fw.var('addon_library__libup', Libup)


class Requests:
    def __init__(self, fw, *playlists):
        self.fw = fw
        self.world = World(fw, {b'/tmp/mmc_add': b''})
        for name in playlists:
            self.world.files[(LIB_DIR + name).encode()] = b''

    def take(self, raw, app=b'foo'):
        if raw is None:
            self.world.files.pop(b'/tmp/q2-%s.library' % app, None)
        else:
            self.world.files[b'/tmp/q2-%s.library' % app] = raw
        self.fw.fn('addon_library__library_take', None, C.c_char_p)(app)

    def answer(self, app=b'foo'):
        return self.world.files.get(b'/tmp/q2-%s.library.done' % app)

    def files(self):
        out, p = [], libup(self.fw).list
        while C.string_at(p):
            s = C.string_at(p)
            out.append(s)
            p += len(s) + 1
        return out


PL = LIB_DIR + 'a.m3u'


def test_a_scan_is_taken(fw):
    q = Requests(fw)
    q.take(b'7\nscan\n')
    up = libup(fw)
    assert up.seq == b'7' and up.state == L_WAIT and up.scan == 1 and up.done == b'/tmp/q2-foo.library.done'
    assert up.taken[0].id == b'foo' and up.taken[0].seq == b'7' and q.answer() is None


def test_files_move_to_the_list_with_their_ops(fw):
    q = Requests(fw, 'a.m3u', 'b.M3U')
    q.take(f'7\r\nimport {PL}\r\nadd {LIB_DIR}b.M3U\r\n'.encode())
    assert libup(fw).state == L_IMPORT and libup(fw).scan == 0 and q.files() == [b'ia.m3u', b'ab.M3U']


def test_a_scan_with_files_scans_first(fw):
    q = Requests(fw, 'a.m3u')
    q.take(f'7\nimport {PL}\nscan\n'.encode())
    assert libup(fw).state == L_WAIT and q.files() == [b'ia.m3u']


def test_a_seq_alone_is_answered_at_once(fw):
    q = Requests(fw)
    q.take(b'7\n')
    assert q.answer() == b'7 ok\n' and libup(fw).state == L_IDLE


def test_a_seq_with_no_newline(fw):
    q = Requests(fw)
    q.take(b'7')
    assert q.answer() == b'7 ok\n'


@pytest.mark.parametrize('line,why', [
    ('fetch', 'Unknown request: fetch'),
    ('import ', f'Not a playlist in /mnt/mmc/_explaylist_data: '),
    ('import /mnt/mmc/a.m3u', 'Not a playlist in /mnt/mmc/_explaylist_data: /mnt/mmc/a.m3u'),
    (f'import {LIB_DIR}sub/a.m3u', f'Not a playlist in /mnt/mmc/_explaylist_data: {LIB_DIR}sub/a.m3u'),
    (f'add {LIB_DIR}.m3u', f'Not a playlist in /mnt/mmc/_explaylist_data: {LIB_DIR}.m3u'),
    (f'add {LIB_DIR}a.txt', f'Not a playlist in /mnt/mmc/_explaylist_data: {LIB_DIR}a.txt'),
    (f'add {LIB_DIR}../a.m3u', f'Not a playlist in /mnt/mmc/_explaylist_data: {LIB_DIR}../a.m3u'),
    (f'import {LIB_DIR}gone.m3u', f'Not found: {LIB_DIR}gone.m3u'),
])
def test_any_bad_line_is_the_answer_and_nothing_runs(fw, line, why):
    q = Requests(fw, 'a.m3u')
    q.take(f'7\nscan\nimport {PL}\n{line}\n'.encode())
    assert q.answer() == f'7 error {why}\n'.encode() and libup(fw).state == L_IDLE


def test_the_longest_playlist_name_and_one_past_it(fw):
    q = Requests(fw, 'n' * 63 + '.m3u')
    q.take(f'7\nimport {LIB_DIR}{"n" * 63}.m3u\n'.encode())
    assert libup(fw).state == L_IMPORT and q.files() == [b'i' + b'n' * 63 + b'.m3u']
    libup(fw).state = L_IDLE
    q.take(f'8\nimport {LIB_DIR}{"n" * 64}.m3u\n'.encode())
    assert q.answer() == b'8 error Name too long: ' + b'n' * 64 + b'\n'


def test_no_card_is_the_answer(fw):
    q = Requests(fw)
    del q.world.files[b'/tmp/mmc_add']
    q.take(b'7\nscan\n')
    assert q.answer() == b'7 error No memory card\n'


def test_a_request_too_long(fw):
    q = Requests(fw)
    q.take(b'7\n' + b'#' * (LIB_MAX - 4))
    assert q.answer() == b'7 error Request too long\n'


def test_the_longest_request_is_read(fw):
    q = Requests(fw)
    q.take(b'7\nscan' + b'\n' * (LIB_MAX - 9))
    assert libup(fw).state == L_WAIT


def test_a_seq_is_taken_once(fw):
    q = Requests(fw)
    q.take(b'7\n')
    q.world.files.pop(b'/tmp/q2-foo.library.done')
    q.take(b'7\n')
    assert q.answer() is None


def test_a_seq_answered_before_a_restart_is_not_run_again(fw):
    q = Requests(fw)
    q.world.files[b'/tmp/q2-foo.library.done'] = b'7 ok\n'
    q.take(b'7\nscan\n')
    assert libup(fw).state == L_IDLE and libup(fw).taken[0].seq == b'7'


def test_a_longer_seq_is_not_the_one_answered(fw):
    q = Requests(fw)
    q.world.files[b'/tmp/q2-foo.library.done'] = b'77 ok\n'
    q.take(b'7\nscan\n')
    assert libup(fw).state == L_WAIT


def test_the_longest_seq_and_one_past_it(fw):
    q = Requests(fw)
    q.take(b'a' * 23 + b'\nscan\n')
    assert libup(fw).seq == b'a' * 23
    libup(fw).state = L_IDLE
    q.take(b'b' * 24 + b'\nscan\n')
    assert q.answer() == b'b' * 24 + b' error Seq too long\n' and libup(fw).state == L_IDLE


def test_a_refusal_is_written_once(fw):
    q = Requests(fw)
    q.take(b'\nscan\n')
    assert q.answer() == b' error Missing seq\n'
    renames = len(q.world.of('rename'))
    q.take(b'\nscan\n')
    assert len(q.world.of('rename')) == renames


def test_a_long_first_line_is_refused_whole(fw):
    q = Requests(fw)
    q.take(b'x' * 500 + b'\n')
    assert q.answer() == b'x' * 62 + b' error Seq too long\n'  # the head read's 64 bytes, less state_read's two


def test_a_file_replaced_between_the_reads_waits_for_the_next_poll(fw):
    q = Requests(fw)
    world = q.world
    world.files[b'/tmp/q2-foo.library'] = b'7\nscan\n'
    original = world.fread
    reads = []
    def fread(buf, size, n, f, *_):
        reads.append(n)
        if n > 100 and world.names[f] == b'/tmp/q2-foo.library':  # the whole file's read: it has moved on
            world.files[b'/tmp/q2-foo.library'] = b'8\nscan\n'
        return original(buf, size, n, f)
    fw.mock('fw_fread', fread)
    fw.fn('addon_library__library_take', None, C.c_char_p)(b'foo')
    assert libup(fw).state == L_IDLE and libup(fw).taken[0].seq == b'' and q.answer() is None


def test_no_request_no_answer(fw):
    q = Requests(fw)
    q.take(None)
    assert q.answer() is None and libup(fw).done == b''


def test_each_app_keeps_its_own_seq(fw):
    q = Requests(fw)
    q.take(b'7\n', b'one')
    q.take(b'7\n', b'two')
    assert q.answer(b'one') == q.answer(b'two') == b'7 ok\n'
    assert [libup(fw).taken[i].id for i in range(2)] == [b'one', b'two']


def test_more_apps_than_slots_are_left_alone(fw):
    q = Requests(fw)
    for i in range(APPS_MAX):
        q.take(b'7\n', b'a%d' % i)
    q.take(b'7\n', b'late')
    assert q.answer(b'late') is None


def done(fw, why=None):
    fw.fn('addon_library__library_done', None, C.c_char_p)(why)


def listing(fw, q, raw):
    """A request's list as library_parse left it, made by hand; the buffer library_take allocates."""
    up = libup(fw)
    up.list = block(fw, size=LIB_MAX + LIB_MAX + LIB_MAX // 8)
    C.memmove(up.list, raw, len(raw))
    up.seq, up.done, up.state = b'7', b'/tmp/q2-foo.library.done', L_IMPORT


def test_the_answer_names_each_add_left_alone(fw):
    q = Requests(fw)
    listing(fw, q, b'ia.m3u\0eb.m3u\0ec.m3u\0\0')
    done(fw)
    assert q.answer() == f'7 ok\nexists {LIB_DIR}b.m3u\nexists {LIB_DIR}c.m3u\n'.encode() and libup(fw).state == L_IDLE


def test_an_error_has_no_exists_lines_of_its_own_but_keeps_them(fw):
    q = Requests(fw)
    listing(fw, q, b'eb.m3u\0\0')
    done(fw, b'Bad')
    assert q.answer() == f'7 error Bad\nexists {LIB_DIR}b.m3u\n'.encode()


def test_the_largest_answer_fits_its_buffer(fw):
    q = Requests(fw)
    count = LIB_MAX // 36  # each add's line is 36 bytes at least
    listing(fw, q, b'ex.m3u\0' * count + b'\0')
    done(fw)
    assert q.answer().count(b'\n') == count + 1


def test_a_write_that_fails_says_so_alone(fw):
    q = Requests(fw)
    listing(fw, q, b'eb.m3u\0\0')
    writes = []
    def fail_first(name):
        writes.append(name)
        return len(writes) == 1
    q.world.fail_write = fail_first
    done(fw)
    assert q.answer() == b"7 error Couldn't write the answer\n"


def test_no_answer_at_all_cleans_up(fw):
    q = Requests(fw)
    listing(fw, q, b'\0')
    q.world.fail_write = lambda name: True
    done(fw)
    assert q.answer() is None and (b'/tmp/q2-library.tmp',) in q.world.of('unlink') and libup(fw).state == L_IDLE


def test_adds_with_no_playlist_of_their_name_are_imported(fw):
    q = Requests(fw)
    listing(fw, q, b'aa.m3u\0ib.m3u\0\0')
    assert fw.fn('addon_library__library_adds', C.c_int)() == 1 and q.files() == [b'ia.m3u', b'ib.m3u']


def test_nothing_to_import(fw):
    q = Requests(fw)
    listing(fw, q, b'eb.m3u\0\0')
    assert fw.fn('addon_library__library_adds', C.c_int)() == 0


def test_an_imported_file_with_no_playlist_is_named_without_its_extension(fw):
    q = Requests(fw)
    listing(fw, q, b'ea.m3u\0ibig one.m3u\0\0')
    why = block(fw, size=300)
    assert fw.fn('addon_library__library_missing', C.c_char_p, C.c_void_p, C.c_uint)(why, 300) == b"Couldn't import big one"


def test_a_list_with_nothing_imported_misses_nothing(fw):
    q = Requests(fw)
    listing(fw, q, b'ea.m3u\0\0')
    assert fw.fn('addon_library__library_missing', C.c_char_p, C.c_void_p, C.c_uint)(block(fw, size=300), 300) is None


def test_the_import_sees_only_the_files_it_named(fw):
    q = Requests(fw)
    listing(fw, q, b'ia.m3u\0eb.m3u\0\0')
    up = libup(fw)
    up.only = up.list
    named = fw.fn('addon_library__listed', C.c_int, C.c_char_p)
    assert named(f'{LIB_DIR}a.m3u'.encode()) == 1
    assert named(f'{LIB_DIR}b.m3u'.encode()) == 0     # left alone
    assert named(f'{LIB_DIR}c.m3u'.encode()) == 0
    assert named(b'/mnt/mmc/a.m3u') == 0


def test_stocks_own_import_gets_the_whole_folder(fw):
    World(fw)
    fw.mock('toolsGetDirectoryM3u8', lambda *_: 5)
    assert fw.fn('addon_m3u_dir', C.c_int, C.c_char_p, C.c_void_p)(b'/x', None) == 5


def test_hostile_requests(fw):
    q = Requests(fw, 'a.m3u')
    samples = [f'7\r\nscan\nimport {PL}\nadd {PL}\n'.encode(), f'8\nadd {LIB_DIR}{"n" * 80}.m3u\nimport {LIB_DIR}x/y\n'.encode()]
    for k, raw in enumerate(hostile(46, samples, 800, b'\n\r /.\xff')):
        libup(fw).state = L_IDLE
        q.world.names.clear()
        q.take(raw)
        if libup(fw).state != L_IDLE:
            for f in q.files():
                assert f[:1] in (b'a', b'i') and f[1:].lower().endswith(b'.m3u') and b'/' not in f and len(f) <= 1 + 63 + 4
    assert libup(fw).list


def scope(fw):
    return fw.var('library_scope__scope', Scope)


def add(fw, path, visible=0):
    fw.fn('library_scope__scope_add', None, C.c_char_p, C.c_int)(path, visible)


def rules(fw):
    out, r = [], scope(fw).rules
    while r:
        out.append((C.string_at(C.addressof(r.contents) + C.sizeof(Rule)), r.contents.length, r.contents.visible))
        r = r.contents.next
    return out


def test_a_rule_is_its_folder_with_a_slash(fw):
    scope(fw).bytes = 32
    add(fw, b'/mnt/mmc/A', 1)
    assert rules(fw) == [(b'/mnt/mmc/A/', 11, 1)] and scope(fw).bytes == 32 + 10 * 2 + 96


def test_descendants_come_before_ancestors(fw):
    scope(fw).bytes = 32
    for path in (b'/a', b'/a/b/c', b'/a/b', b'/z'):
        add(fw, path)
    assert [r[0] for r in rules(fw)] == [b'/a/b/c/', b'/a/b/', b'/z/', b'/a/']


def test_the_budget_is_held_to_the_byte(fw):
    s = scope(fw)
    s.bytes = SCOPE_MAX - (5 * 2 + 96)
    add(fw, b'/abcd')
    assert s.failed == 0 and s.bytes == SCOPE_MAX and len(rules(fw)) == 1
    add(fw, b'')
    assert s.failed == 1 and len(rules(fw)) == 1


def query(fw, sql):
    p = fw.fn('library_scope_query', C.c_void_p, C.c_char_p)(sql)
    return C.string_at(p) if p else None


def predicate(fw):
    return query(fw, TOKEN)


def hidden(fw, path):
    return fw.fn('library_scope_hidden', C.c_int, C.c_char_p)(path)


def test_a_rule_as_sql(fw):
    World(fw, {RULES: b'0/mnt/mmc/A\n'})
    assert predicate(fw) == b"case when substr(fileurl,1,11)='/mnt/mmc/A/' collate nocase then 0 else 1 end"


def test_the_closest_marker_is_asked_first(fw):
    World(fw, {RULES: b'0/mnt/mmc/A\n1/mnt/mmc/A/B\n'})
    assert predicate(fw) == (b"case when substr(fileurl,1,13)='/mnt/mmc/A/B/' collate nocase then 1 "
                             b"when substr(fileurl,1,11)='/mnt/mmc/A/' collate nocase then 0 else 1 end")


def test_a_quote_in_a_folders_name_is_doubled(fw):
    World(fw, {RULES: b"0/mnt/mmc/It's\n"})
    assert predicate(fw) == b"case when substr(fileurl,1,14)='/mnt/mmc/It''s/' collate nocase then 0 else 1 end"


def test_substr_counts_characters_not_bytes(fw):
    World(fw, {RULES: '0/mnt/mmc/é好\n'.encode()})
    assert b'substr(fileurl,1,12)' in predicate(fw)


def test_no_rules_show_everything(fw):
    World(fw)
    assert predicate(fw) == b'1'
    assert query(fw, b'select 1') == b'select 1'


def test_a_query_is_expanded_where_its_token_is(fw):
    World(fw)
    assert query(fw, b'select * from t where ' + TOKEN + b' and x') == b'select * from t where 1 and x'


def test_no_query_no_copy(fw):
    assert fw.fn('library_scope_query', C.c_void_p, C.c_char_p)(None) is None


@pytest.mark.parametrize('raw', [b'x/mnt\n', b'2/mnt\n', b'0mnt\n', b'0\n', b'\n', b'0/mnt/mmc/A', b'0/a\n1'])
def test_a_bad_rules_file_ignores_the_markers(fw, raw):
    World(fw, {RULES: raw})
    assert predicate(fw) == b'1' and hidden(fw, b'/mnt/mmc/A/x') == 0


def test_rules_too_big_for_their_budget_ignore_the_markers(fw):
    line = b'0/mnt/mmc/' + b'a' * 200 + b'\n'  # 514 bytes of budget each, after the first 32
    world = World(fw, {RULES: line * 254})
    assert predicate(fw).count(b'when') == 254
    world.files[RULES] = line * 255
    scope(fw).ready = 0
    assert predicate(fw) == b'1'


def test_the_worst_rules_fit_their_sql(fw):
    line = b"0/mnt/mmc/" + b"'" * 1000 + b'\n'
    World(fw, {RULES: line * 40})
    sql = predicate(fw)
    assert sql.startswith(b'case when') and sql.endswith(b'else 1 end') and sql.count(b'when') == 40


def test_a_file_as_big_as_its_buffer_is_refused(fw):
    World(fw, {RULES: (b'0/a\n' * SCOPE_MAX)[:SCOPE_MAX]})
    assert predicate(fw) == b'1'


@pytest.mark.parametrize('path,yes', [
    (b'/mnt/mmc/A/x.flac', 1), (b'/MNT/MMC/a/x.flac', 1), (b'/mnt/mmc/A/B/x.flac', 0), (b'/mnt/mmc/A/B/C/x', 0), (b'/mnt/mmc/Ab/x', 0),
    (b'/mnt/mmc/A', 0), (b'/mnt/mmc/x.flac', 0), (b'/other/A/x', 0), (b'', 0),
])
def test_a_path_under_the_closest_marker(fw, path, yes):
    World(fw, {RULES: b'0/mnt/mmc/A\n1/mnt/mmc/A/B\n'})
    assert hidden(fw, path) == yes


def test_no_path_is_not_hidden(fw):
    assert hidden(fw, None) == 0


def test_rules_are_read_once_until_a_scan(fw):
    world = World(fw, {RULES: b'0/mnt/mmc/A\n'})
    assert hidden(fw, b'/mnt/mmc/A/x') == 1
    world.files[RULES] = b''
    assert hidden(fw, b'/mnt/mmc/A/x') == 1
    fw.fn('library_scope_scan', None)()
    assert hidden(fw, b'/mnt/mmc/A/x') == 0


def walked(fw, dirs, files=()):
    world = World(fw, {f: b'' for f in files}, dirs)
    fw.fn('library_scope_scan', None)()
    return world


def d(name, kind=4):
    return Dirent(d_name=name, d_type=kind)


def test_a_scan_saves_each_marker_folder(fw):
    world = walked(fw, {b'/mnt/mmc': [d(b'A'), d(b'B'), d(b'.hidden'), d(b'file', 8)], b'/mnt/mmc/A': [], b'/mnt/mmc/B': [d(b'C')], b'/mnt/mmc/B/C': []},
                   [b'/mnt/mmc/A/database.ignore', b'/mnt/mmc/B/C/.database.unignore'])
    assert world.written[RULES + b'.tmp'] == b'0/mnt/mmc/A\n1/mnt/mmc/B/C\n'
    assert world.of('rename') == [(RULES + b'.tmp', RULES)]


def test_unignore_wins_in_one_folder(fw):
    world = walked(fw, {b'/mnt/mmc': []}, [b'/mnt/mmc/database.ignore', b'/mnt/mmc/database.unignore'])
    assert world.written[RULES + b'.tmp'] == b'1/mnt/mmc\n'


def test_a_folder_of_that_name_is_no_marker(fw):
    world = walked(fw, {b'/mnt/mmc': [d(b'database.ignore')], b'/mnt/mmc/database.ignore': []})
    assert world.written[RULES + b'.tmp'] == b''


def test_an_unknown_entry_is_looked_at(fw):
    world = walked(fw, {b'/mnt/mmc': [d(b'A', 0), d(b'f', 0)], b'/mnt/mmc/A': []}, [b'/mnt/mmc/f', b'/mnt/mmc/A/database.ignore'])
    assert world.written[RULES + b'.tmp'] == b'0/mnt/mmc/A\n'


def failed(world):
    return (RULES + b'.tmp',) in world.of('unlink') and (RULES,) in world.of('unlink') and not world.of('rename')


def test_a_card_too_deep_leaves_no_rules(fw):
    path, dirs = b'/mnt/mmc', {}
    for _ in range(70):
        dirs[path] = [d(b'x')]
        path += b'/x'
    assert failed(walked(fw, dirs))


def test_a_path_too_long_leaves_no_rules(fw):
    name = b'n' * 255
    path, dirs = b'/mnt/mmc', {}
    for _ in range(6):
        dirs[path] = [d(name)]
        path += b'/' + name
    assert failed(walked(fw, dirs))


def test_a_rules_file_that_cant_open_leaves_none(fw):
    world = World(fw, dirs={b'/mnt/mmc': []})
    fw.mock('fw_fopen', lambda *_: 0)
    fw.fn('library_scope_scan', None)()
    assert failed(world)


def test_a_failed_write_leaves_no_rules(fw):
    world = World(fw, {b'/mnt/mmc/database.ignore': b''}, {b'/mnt/mmc': []})
    world.fail_write = lambda name: True
    fw.fn('library_scope_scan', None)()
    assert failed(world)


def test_hostile_rules_files(fw):
    world = World(fw)
    samples = [b"0/mnt/mmc/A\n1/mnt/mmc/A/It's\n0/mnt/mmc/\xc3\xa9\n"]
    set_ = scope(fw)
    for raw in hostile(47, samples, 800, b"'\n01/"):
        world.files[RULES] = raw
        world.names.clear()
        set_.ready = 0
        sql = predicate(fw)
        assert sql == b'1' or (sql.startswith(b'case ') and sql.endswith(b'else 1 end'))
        hidden(fw, b'/mnt/mmc/A/x')
