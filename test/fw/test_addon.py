"""patch/addon.c, addon_library.c and library_scope.c: card apps' manifests, stream sessions, playlists, sleep
timers, card games, the page they draw over Q2SPOT/1, their QR codes, library requests, and the database.ignore
markers."""
import ctypes as C
import random
import re
import struct

import pytest

import host
from conftest import O, Deques, Game, block, claim, high_halves, libc, text
from test_coverflow import png, scratch  # an icon's header; navigation.c's st by field
from test_netupdate import JOB_INSTALL, JOB_REMOVE, SH_CANCELLED, SH_CARD, SH_NET, SH_OK, Sh  # test_netupdate.py keeps netupdate.c's App Store mirror
from test_radio import Rd  # test_radio.py keeps the mirror
from test_tidal import Au, Queue, free_stubbed  # test_tidal.py keeps tidal.c's au mirror

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
G_FIND, G_SORT = 4, 5
REQ_OK, REQ_NOSOCK, REQ_FAIL = range(3)
S_WAIT, S_PLAY, S_DONE = range(3)
L_IDLE, L_WAIT, L_SCAN, L_IMPORT = range(4)
W_W, W_H, CANVAS_LCD = O['W_W'], O['W_H'], O['CANVAS_LCD']
REC_NAME, REC_PATH, REC_TYPE = O['REC_NAME'], O['REC_PATH'], O['REC_TYPE']
LIBRARY = 0xf001  # stock's library class, which a local queue plays under


class App(C.Structure):
    _fields_ = [('id', C.c_char * 16), ('name', C.c_char * 32), ('tips', C.c_char * 32), ('bin', C.c_char * 48), ('run', C.c_char * 48),
                ('pid', C.c_char * 32), ('state', C.c_char * 40), ('browse', C.c_char * 48), ('icon', C.c_char * 56), ('more', C.c_char * 48),
                *((n, C.c_int) for n in ('qr', 'game', 'audio', 'hidden', 'present', 'launched', 'hold', 'relaunch', 'restart', 'nostop')),
                *((n, C.c_uint) for n in ('polled', 'launch_at', 'restart_at', 'play'))]


class Ap(C.Structure):
    _fields_ = [('a', App * APPS_MAX), ('n', C.c_int), ('shown', C.c_int), ('order', C.c_int * APPS_MAX),
                ('games', C.c_int), ('game', C.c_int * APPS_MAX)]


class Sm(C.Structure):
    _fields_ = [('app', C.c_void_p), ('dir', C.c_char * 160), ('last', C.c_char * 192), ('playing', C.c_char * 512), ('state', C.c_int),
                ('timer', C.c_uint), ('seen', C.c_long), ('m3u', C.c_int), ('urls', C.c_int), ('next', C.c_int), ('more', C.c_int), ('first', C.c_char * 512),
                ('want', C.c_char * 512), ('loads', C.c_uint), ('seq', C.c_char * 24)]


class Sl(C.Structure):
    _fields_ = [('seen', C.c_uint * APPS_MAX), ('at', C.c_long), ('end', C.c_int), ('owner', C.c_int), ('pos', C.c_uint), ('nexts', C.c_uint),
                ('sec', C.c_int), ('total', C.c_int), ('stopped', C.c_uint)]


class Req(C.Structure):
    _fields_ = [('thread', C.c_ulong), ('running', C.c_int), ('fail', C.c_int), ('cancel', C.c_int), ('done', C.c_int), ('id', C.c_uint),
                ('n', C.c_uint), ('len', C.c_uint), ('buf', C.c_void_p), ('path', C.c_void_p), ('field', C.c_int)]


class Lv(C.Structure):
    _fields_ = [*((n, C.c_int) for n in ('state', 'n', 'sel', 'live', 'retry', 'nosock', 'slow', 'art', 'find', 'sort')),
                *((n, C.c_uint) for n in ('since', 'base', 'id', 'hash')),
                ('title', C.c_char * 128), ('note', C.c_char * 128), ('op', C.c_char * 16), ('args', C.c_char * 400)]


class Pg(C.Structure):
    _fields_ = [*((n, C.c_void_p) for n in ('page', 'view', 'edit')), *((n, C.c_uint) for n in ('timer', 'go', 'leave', 'again')),
                *((n, C.c_int) for n in ('depth', 'act', 'next', 'arg', 'a', 'code', 'arow', 'busy')), ('acted', C.c_uint),
                ('term', C.c_char * 64), ('aop', C.c_char * 16), ('lv', C.POINTER(Lv)), ('buf', C.c_void_p * ADDON_PAGES),
                ('f', C.POINTER(C.c_char_p)), ('pages', C.c_int), ('paging', C.c_int), ('moved', C.c_byte),
                ('sec', C.c_void_p), ('nsec', C.c_byte), ('cur', C.c_byte), ('sx', C.c_int), ('sy', C.c_int),
                *((n, C.c_byte) for n in ('sdown', 'sclaim')), ('art_lo', C.c_int), ('art_hi', C.c_int),
                ('sort', C.c_char_p * 6), ('field', C.c_int)]


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
        for stub, fn in (('fw_fopen', self.fopen), ('fw_fread', self.fread), ('fw_fseek', self.fseek), ('fw_fwrite', self.fwrite), ('fw_rename', self.rename),
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
        elif b'a' in how:
            self.written.setdefault(name, self.files.get(name, b''))
        return f

    def fread(self, buf, size, n, f, *_):
        piece = self.files[self.names[f]][self.at[f]:self.at[f] + size * n]
        C.memmove(buf, piece, len(piece))
        self.at[f] += len(piece)
        return len(piece) // size

    def fseek(self, f, off, *_):
        self.at[f] = off
        return 0

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
        files.update({f'/mnt/mmc/.{name}/{e}'.encode(): png(40, 40) if e.endswith('.png') else b'' for e in extra})
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
    card(fw, one=b'name=N\nsearch=Find it\nqr=1\ngame=1\naudio=1\n', two=b'other=1\n')
    scan(fw)
    one, two = ap(fw).a[0], ap(fw).a[1]
    assert (one.name, one.tips, one.qr, one.game, one.audio) == (b'N', b'Find it', 1, 1, 1)
    assert (two.name, two.tips, two.qr, two.game, two.audio) == (b'two', b'Search', 0, 0, 0)
    assert (one.nostop, one.more, two.nostop, two.more) == (0, b'', 0, b'')


@pytest.mark.parametrize('manifest,nostop,more', [
    (b'name=N\nstop=0\nmore=playlists Playlists\n', 1, b'playlists Playlists'),
    (b'name=N\r\nstop=0\r\nmore=playlists\r\n', 1, b'playlists'),  # a Windows editor's line ends
    (b'name=N\nstop=1\n', 0, b''),
    (b'name=N\nstop=no\n', 0, b''),  # as text: only 0 drops the Stop row
])
def test_a_manifest_can_drop_the_stop_row_and_take_now_playings_more(fw, manifest, nostop, more):
    card(fw, one=manifest)
    scan(fw)
    assert (ap(fw).a[0].nostop, ap(fw).a[0].more) == (nostop, more)


def test_a_hidden_app_has_no_streaming_row(fw):
    card(fw, one=b'name=One\n', airplay=b'name=AirPlay\nhidden=1\n')  # opened by id instead (addon_open_id)
    assert scan(fw) == 1 and names(fw, 'addon_id') == [None, b'one', None]
    assert (ap(fw).a[1].id, ap(fw).a[1].hidden, ap(fw).a[1].present) == (b'airplay', 1, 1)


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


@pytest.mark.parametrize('w,h,shown', [(40, 40, 1), (128, 128, 1), (129, 40, 0), (40, 129, 0), (4000, 4000, 0), (0, 0, 0)])
def test_an_icon_over_art_max_is_the_shared_one(fw, w, h, shown):
    world = card(fw, a=(b'x=1\n', ['icon-40.png']))
    world.files[b'/mnt/mmc/.a/icon-40.png'] = png(w, h)
    scan(fw)
    assert ap(fw).a[0].icon == (b'file:///mnt/mmc/.a/icon-40.png' if shown else b'stream_app')


def test_a_too_big_row_icon_falls_back_to_the_other_then_the_shared_one(fw):
    world = card(fw, a=(b'x=1\n', ['icon-40.png', 'icon.png']), b=(b'x=1\n', ['icon.png']), c=(b'x=1\n', ['icon-40.png', 'icon.png']))
    world.files[b'/mnt/mmc/.a/icon-40.png'] = png(4000, 4000)
    world.files[b'/mnt/mmc/.b/icon.png'] = b'not a picture'
    world.files[b'/mnt/mmc/.c/icon.png'] = png(4000, 4000)
    scan(fw)
    assert [ap(fw).a[i].icon for i in range(3)] == [b'file:///mnt/mmc/.a/icon.png', b'stream_app', b'file:///mnt/mmc/.c/icon-40.png']


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
    assert world.written[b'/tmp/q2-addon.caps.tmp'] == b'playlist progress pages library goto sleep game audio sections art window urls sort field more fav now\n'
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
    assert world.now == 1020  # QUIT_MS, 20 s


@pytest.mark.parametrize('folder', [b'foo', b'.', b'..', b'.a;b', b".a'b", b'.' + b'a' * 16, b'', None])
@pytest.mark.hostile('addon.c:addon_quit')
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


class Songs:
    """Stock's song records (stSongInfo) with a name and a path both, as a folder's listing has them. On the Q2
    each is a 4-byte pointer; here P() reads 8, so REC_NAME's high half is REC_PATH's low half. A name below
    4 GB has a zero high half and a path at a multiple of 4 GB a zero low half: the names share a MAP_32BIT
    block, wherever the kernel finds room under 2 GB, each path has a page of its own in AddressSanitizer's high
    memory, at a high half of this file's (conftest.high_halves)."""
    PATHS = high_halves(0x100)

    def __init__(self):
        self.maps, self.records, self.keep, self.used = [], {}, [], 0
        self.names = libc.mmap(0, 0x10000, 3, 0x62, -1, 0)  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT
        assert self.names and self.names < 1 << 32
        self.maps.append((self.names, 0x10000))

    def map(self, address, size):
        assert libc.mmap(address, size, 3, 0x100022, -1, 0) == address  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
        self.maps.append((address, size))
        return address

    def free(self):
        munmap = libc.munmap
        munmap.argtypes = [C.c_void_p, C.c_size_t]
        for address, size in self.maps:
            munmap(address, size)

    def __call__(self, path):
        """The record of the song at path: the same one at each listing."""
        if path not in self.records:
            name = path.rsplit(b'/', 1)[-1]
            at, high = self.names + self.used, self.PATHS[len(self.records)]
            C.memmove(at, name + b'\0', len(name) + 1)
            self.used += len(name) + 1
            C.memmove(self.map(high << 32, 0x1000), path + b'\0', len(path) + 1)
            r = C.create_string_buffer(0x60)
            struct.pack_into('<Q', r, REC_NAME, at)
            struct.pack_into('<I', r, REC_PATH + 4, high)  # the path's high half; its low half is the name's, 0
            struct.pack_into('<i', r, REC_TYPE, 8)         # a song
            self.keep.append(r)
            self.records[path] = C.addressof(r)
            assert (string_at(self.records[path], REC_NAME), string_at(self.records[path], REC_PATH)) == (name, path)
        return self.records[path]


def string_at(record, offset):
    return C.string_at(C.c_uint64.from_address(record + offset).value)


@pytest.fixture
def songs():
    made = Songs()
    yield made
    made.free()


SESSION = b'/mnt/mmc/.foo/s1'
PLAYLIST = b'/mnt/mmc/.foo/queue.m3u'
M3U_BATCH = 32


class Stream:
    """Stream mode end to end: card apps whose .play names a folder (or playlist) they fill; stock's listing of a
    folder (toolsLoadDirectory); Now Playing loading into stock's queue what play_folder hands it, which
    playback_loads counts, as playback_groups does; and the UI loop's passes, addon_poll and then its timers."""
    def __init__(self, fw, mcl, songs, **apps):
        self.fw, self.mcl, self.songs = fw, mcl, songs
        self.world = card(fw, **(apps or {'foo': b'name=Foo\n'}))
        scan(fw)
        for i in range(ap(fw).n):  # each started, its loop running
            ap(fw).a[i].launched = 1
            self.world.files[ap(fw).a[i].pid] = b'1'
        self.deques = Deques(fw)
        self.queue, self.pool, self.cells = self.deques.new(), self.deques.new(), []
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.queue
        C.c_uint.from_address(O['MCL_POOL']).value = self.pool  # stock's shuffle pool, which an add grows
        fw.mock('fw__deque_push_back', self.push)
        self.loaded = fw.var('playback_loads', C.c_uint)
        self.listings, self.timers, self.loads, self.windows, self.ids = {}, {}, [], set(), 0
        fw.mock('toolsLoadDirectory', self.listing)
        fw.mock('timer_add', self.timer_add)
        fw.mock('timer_remove', lambda t, *_: self.timers.pop(t, None))
        fw.mock('navigator_to_with_context', self.navigate)
        fw.mock('navigator_window_is_exist', lambda name, *_: C.string_at(name) in self.windows)

    def push(self, q, value, *_):
        if q & 0xffffffff == self.pool:  # a deque of ints: deque_at gives an element's address
            self.cells.append(C.c_int(value))
            value = C.addressof(self.cells[-1])
        self.deques[q].append(value)

    def listing(self, path, *_):
        rows = [self.songs(p) for p in self.listings.get(C.string_at(path), [])]
        self.deques[self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value][:] = rows
        return len(rows)

    def timer_add(self, fn, *_):
        self.ids += 1
        self.timers[self.ids] = fn
        return self.ids

    def navigate(self, name, ctx, *_):
        if C.string_at(name) == b'playing_page':  # Now Playing copies the queue it is handed, at once
            self.deques[self.queue][:] = self.deques[C.c_void_p.from_address(ctx).value]
            self.mcl.pos = C.c_int.from_address(ctx + 8).value
            self.loaded.value += 1
            self.loads.append((C.c_int.from_address(ctx + 12).value, self.fw.var('press_natural', C.c_int).value))

    def land(self, *paths):
        """Songs downloaded: on the card, in their folder's listing, its mtime moved on."""
        for path in paths:
            folder, name = path.rsplit(b'/', 1)
            self.world.files[path] = b''
            self.world.dirs.setdefault(folder, []).append(name)
            self.listings.setdefault(folder, []).append(path)
            self.world.mtime[folder] = self.world.mtime.get(folder, 1) + 1

    def play(self, raw, app=b'foo'):
        self.world.files[b'/tmp/q2-%s.play' % app] = raw

    def poll(self):
        self.world.ms += 500
        self.fw.fn('addon_poll', None)()

    def fire(self):
        due, self.timers = self.timers, {}
        for fn in due.values():
            C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)

    def step(self):
        """A UI loop pass: the poll, then the timers it set."""
        self.poll()
        self.fire()

    def queued(self):
        return [string_at(r, REC_PATH) for r in self.deques[self.queue]]

    def playing(self, app=b'foo'):
        return self.world.files.get(b'/tmp/q2-%s.playing' % app)


def started(fw, mcl, songs, first=SESSION + b'/01 - One [1].flac'):
    """A folder session playing its first song."""
    s = Stream(fw, mcl, songs)
    s.land(first)
    s.play(b'1 ' + SESSION + b'\n')
    s.step()
    s.step()
    assert s.queued() == [first]
    return s


def test_a_stream_session_plays_its_folder_as_it_fills(fw, mcl, songs):
    s = Stream(fw, mcl, songs)
    s.world.dirs[SESSION] = []
    s.play(b'1 ' + SESSION + b'\n')
    s.step()
    assert s.loads == [] and s.queued() == []  # its folder empty: it waits
    first = SESSION + b'/01 - One [1].flac'
    s.land(first)
    s.step()
    # played from its first song, Now Playing opening as for a press, but no press stamped (nobody pressed)
    assert s.loads == [(LIBRARY, 1)] and s.queued() == [first] and fw.var('press_natural', C.c_int).value == 0
    s.step()
    assert s.playing() == first + b'\n' and (b'/tmp/q2-stream.tmp', b'/tmp/q2-foo.playing') in s.world.of('rename')
    later = [SESSION + b'/02 - Two [2].flac', SESSION + b'/03 - Three [3].flac']
    s.land(*reversed(later))  # downloaded in any order: they play in their names'
    s.step()
    assert s.queued() == [first, *later] and len(s.loads) == 1  # joined its end, as Add to Queue adds them
    renames = len(s.world.of('rename'))
    s.step()
    assert len(s.world.of('rename')) == renames  # the same song playing: .playing left as it is
    mcl.pos = 1
    s.step()
    assert s.playing() == later[0] + b'\n'


def test_downloads_in_progress_and_hidden_files_list_nothing(fw, mcl, songs):
    s = started(fw, mcl, songs)
    listed = len(fw.calls('toolsLoadDirectory'))
    for name in (b'02 - Two [2].flac.part', b'02 - Two [2].tmp', b'.nomedia'):
        s.world.dirs[SESSION].append(name)
        s.world.mtime[SESSION] += 1
        s.step()
    assert len(fw.calls('toolsLoadDirectory')) == listed and len(s.queued()) == 1


PLAY_READ = 192 - 2  # stream_poll's raw[192] less state_read's leading newline and NUL: .play's bytes read


@pytest.mark.parametrize('raw', [
    b'1 /tmp/s1\n', b'1 /mnt/mmc/Music\n', b'1 /mnt/mmc/.foo/../Music\n', b'1 /mnt/mmcx/.foo\n', b'1' + SESSION + b'\n',
    b'1 /tmp/queue.m3u\n',
    b'1 /mnt/mmc/.foo/' + b'a' * 160 + b'\n',  # longer than the session's folder can hold
    b'1 /mnt/mmc/.foo/' + b'b' * 180 + b'\n',  # longer than its read of .play, and than the folder
    # the read ends exactly where the session's folder name does: whole in sm.dir, but .play names s12
    b'1' * (PLAY_READ - 1 - len(SESSION)) + b' ' + SESSION + b'2\n',
])
def test_a_play_file_naming_no_hidden_card_folder_plays_nothing(fw, mcl, songs, raw):
    s = Stream(fw, mcl, songs)
    named = raw.partition(b' ')[2].rstrip(b'\n') or SESSION
    read = raw[:PLAY_READ].partition(b' ')[2].rstrip(b'\n')  # what the read of .play holds of it
    if named.endswith(b'.m3u'):
        s.world.files[named] = SESSION + b'/01.flac\n'
        s.land(SESSION + b'/01.flac')
    else:
        s.land(*{p + b'/01.flac' for p in (named[:159], read, named) if p})  # nor what a cut path names
    s.play(raw)
    s.step()
    s.step()
    assert s.loads == [] and s.queued() == [] and not fw.calls('toolsLoadDirectory')


def test_a_play_file_one_byte_short_of_its_read_is_whole_and_plays(fw, mcl, songs):
    s = Stream(fw, mcl, songs)
    raw = b'1' * (PLAY_READ - 3 - len(SESSION)) + b' ' + SESSION + b'\n'
    assert len(raw) == PLAY_READ - 1
    s.land(SESSION + b'/01.flac')
    s.play(raw)
    s.step()
    assert s.loads == [(LIBRARY, 1)] and s.queued() == [SESSION + b'/01.flac']


def test_a_session_is_left_once_something_else_plays(fw, mcl, songs):
    s = started(fw, mcl, songs)
    first = s.queued()[0]
    s.step()
    other = SESSION + b'2/x.flac'  # a folder whose name only starts with the session's
    s.deques[s.queue][:] = [songs(other)]
    s.step()
    s.land(SESSION + b'/02.flac')
    s.step()
    assert s.queued() == [other] and s.playing() == first + b'\n'


def test_a_library_scan_puts_the_first_song_off_until_its_window_closes(fw, mcl, songs):
    s = Stream(fw, mcl, songs)
    s.land(SESSION + b'/01.flac')
    s.play(b'1 ' + SESSION + b'\n')
    s.windows.add(b'updatemusic_dialog')  # a scan, whose thread fills the deque a folder's listing does
    s.step()
    s.step()
    assert s.loads == [] and not fw.calls('toolsLoadDirectory')
    s.windows.clear()
    s.step()
    assert len(s.loads) == 1


@pytest.mark.parametrize('window', [b'updatemusic_page', b'updatemusic_dialog'])
def test_a_scan_starting_before_the_first_song_plays_puts_it_off(fw, mcl, songs, window):
    s = Stream(fw, mcl, songs)
    s.land(SESSION + b'/01.flac')
    s.play(b'1 ' + SESSION + b'\n')
    s.poll()  # its song seen: a timer to play it
    s.windows.add(window)
    s.fire()
    assert s.loads == [] and not fw.calls('toolsLoadDirectory')
    s.step()
    assert s.loads == []
    s.windows.clear()
    s.step()
    assert len(s.loads) == 1


def test_songs_landing_during_a_scan_join_after_it(fw, mcl, songs):
    s = started(fw, mcl, songs)
    s.windows.add(b'updatemusic_dialog')
    s.land(SESSION + b'/02.flac')
    s.step()
    assert len(s.queued()) == 1
    s.windows.clear()
    s.step()
    assert s.queued()[1:] == [SESSION + b'/02.flac']


def test_another_apps_play_file_left_in_tmp_never_takes_the_queue_back(fw, mcl, songs):
    s = Stream(fw, mcl, songs, foo=b'name=Foo\n', bar=b'name=Bar\n')
    one, two = b'/mnt/mmc/.foo/s/01.flac', b'/mnt/mmc/.bar/s/01.flac'
    s.land(one, two)
    s.play(b'1 /mnt/mmc/.foo/s\n', b'foo')
    s.step()
    s.play(b'1 /mnt/mmc/.bar/s\n', b'bar')
    s.step()
    assert s.queued() == [two] and len(s.loads) == 2
    s.land(b'/mnt/mmc/.foo/s/02.flac', b'/mnt/mmc/.bar/s/02.flac')
    s.step()
    s.step()
    assert s.queued() == [two, b'/mnt/mmc/.bar/s/02.flac'] and len(s.loads) == 2
    s.play(b'2 /mnt/mmc/.foo/s\n', b'foo')  # a new session of its own does
    s.step()
    assert s.queued() == [one, b'/mnt/mmc/.foo/s/02.flac'] and len(s.loads) == 3


def playlist(fw, mcl, songs, *entries):
    s = Stream(fw, mcl, songs)
    s.world.files[PLAYLIST] = b'#EXTM3U\n' + b''.join(e + b'\n' for e in entries)
    return s


A, B, C3 = b'/mnt/mmc/.foo/A/1.flac', b'/mnt/mmc/.foo/B/2.flac', b'/mnt/mmc/.foo/A/3.flac'


def test_a_playlist_session_queues_its_songs_in_its_order_as_they_land(fw, mcl, songs):
    s = playlist(fw, mcl, songs, A, B, C3)
    s.land(A)
    s.play(b'1 ' + PLAYLIST + b'\n')
    s.step()
    assert s.queued() == [A] and s.loads == [(LIBRARY, 1)]  # what is there, up to the first song that isn't
    s.step()
    assert s.queued() == [A] and s.playing() == A + b'\n'
    s.land(C3, B)
    s.step()
    assert s.queued() == [A, B, C3] and len(s.loads) == 1  # its order, not the folders'


def test_a_playlist_anywhere_on_the_card_plays_whatever_its_extensions_case(fw, mcl, songs):
    s = Stream(fw, mcl, songs)
    listed = b'/mnt/mmc/Playlists/Mix.M3U'  # not in an app's hidden folder: a playlist may be any card file
    s.world.files[listed] = A + b'\n'
    s.land(A)
    s.play(b'1 ' + listed + b'\n')
    s.step()
    assert s.loads == [(LIBRARY, 1)] and s.queued() == [A]


def test_a_playlist_rewritten_past_its_entries_so_far_adds_the_new_ones(fw, mcl, songs):
    s = playlist(fw, mcl, songs, A)
    s.land(A, B)
    s.play(b'1 ' + PLAYLIST + b'\n')
    s.step()
    s.world.files[PLAYLIST] += B + b'\n'
    s.world.mtime[PLAYLIST] = 2
    s.step()
    assert s.queued() == [A, B]


def test_a_long_playlist_lists_a_batch_of_folders_a_pass(fw, mcl, songs):
    paths = [b'/mnt/mmc/.foo/%02d/x.flac' % i for i in range(M3U_BATCH + 8)]
    s = playlist(fw, mcl, songs, *paths)
    s.land(*paths)
    s.play(b'1 ' + PLAYLIST + b'\n')
    s.step()
    assert s.queued() == paths[:M3U_BATCH] and len(fw.calls('toolsLoadDirectory')) == M3U_BATCH
    s.step()
    assert s.queued() == paths and len(s.loads) == 1


def test_a_playlist_entry_stock_lists_no_song_of_is_passed_over(fw, mcl, songs):
    notes = b'/mnt/mmc/.foo/C/notes.txt'
    s = playlist(fw, mcl, songs, A, notes, B)
    s.land(A, B)
    s.world.files[notes] = b''  # on the card, no song in its folder's listing
    s.play(b'1 ' + PLAYLIST + b'\n')
    s.step()
    assert s.queued() == [A, B]


def test_a_song_missing_from_the_listing_an_earlier_entry_took_is_listed_again(fw, mcl, songs):
    s = playlist(fw, mcl, songs, A, C3)
    s.land(A)
    s.world.files[C3] = b''  # there by its access(), not yet in the listing taken for A
    s.play(b'1 ' + PLAYLIST + b'\n')
    s.step()
    assert s.queued() == [A]
    s.listings[b'/mnt/mmc/.foo/A'].append(C3)
    s.step()
    assert s.queued() == [A, C3]


@pytest.mark.parametrize('timer', [0, 1])
def test_a_queue_the_user_plays_before_a_playlists_first_song_ends_its_session(fw, mcl, songs, timer):
    s = playlist(fw, mcl, songs, A)
    s.play(b'1 ' + PLAYLIST + b'\n')
    if timer:  # its first song there, its timer set, and the user's play before it fires
        s.land(A)
        s.poll()
        s.loaded.value += 1
        s.fire()
    else:
        s.step()
        s.loaded.value += 1
        s.step()
        s.land(A)
    s.step()
    s.step()
    assert s.loads == [] and sm(fw).state == S_DONE


def test_the_users_own_play_of_the_same_song_ends_a_playlist_session(fw, mcl, songs):
    s = playlist(fw, mcl, songs, A, B)
    s.land(A)
    s.play(b'1 ' + PLAYLIST + b'\n')
    s.step()
    s.loaded.value += 1  # the same album played from Library: its queue starts with the same song
    s.land(B)
    s.step()
    assert s.queued() == [A] and sm(fw).state == S_DONE


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


class Track:
    """The local player's track, as sleep's "end" sees it: its place in stock's queue (MCL_POS), its elapsed and
    length (player_playtime_and_length), and auto_nexts, the tracks that ended by themselves (playback.c)."""
    def __init__(self, fw, mcl, total=200):
        self.fw, self.mcl, self.sec, self.total = fw, mcl, 0, total
        mcl.pos = 3
        fw.mock('player_playtime_and_length', self.times)

    def times(self, sec, total, *_):
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.sec, self.total

    def at(self, sec, pos=None):
        """A UI pass with the track at sec, at queue place pos (another track) if given."""
        self.sec = sec
        if pos is not None:
            self.mcl.pos = pos
        check(self.fw)

    def ends(self):
        self.fw.var('auto_nexts', C.c_uint).value += 1
        self.at(0, self.mcl.pos + 1)


def test_end_pauses_once_the_track_ends_by_itself(fw, mcl):
    s, t = Sleep(fw), Track(fw, mcl)
    s.read(b'end\n')
    assert sl(fw).end == 1 and sl(fw).owner == 1
    t.at(150)
    t.at(190)
    assert s.paused() == 0
    t.ends()
    assert s.paused() == 1 and sl(fw).end == 0 and s.world.of('unlink') == [(b'/tmp/q2-foo.sleep',)]


@pytest.mark.parametrize('last,paused', [(198, 1), (197, 0)])
def test_end_takes_the_next_track_from_its_last_two_seconds_as_its_end(fw, mcl, last, paused):
    s, t = Sleep(fw), Track(fw, mcl)
    s.read(b'end')
    t.at(last)
    t.at(0, pos=4)  # not counted as ended by itself: picked, or the next one's start seen first
    assert s.paused() == paused and sl(fw).end == 1 - paused


def test_end_waits_for_the_end_of_a_track_picked_instead(fw, mcl):
    s, t = Sleep(fw), Track(fw, mcl)
    s.read(b'end')
    t.at(50)
    t.at(5, pos=7)
    t.at(120)
    assert s.paused() == 0 and sl(fw).pos == 7
    t.ends()
    assert s.paused() == 1


def test_end_waits_again_after_a_seek_back(fw, mcl):
    s, t = Sleep(fw), Track(fw, mcl)
    s.read(b'end')
    t.at(150)
    t.at(10)
    t.at(11)
    assert s.paused() == 0 and sl(fw).end == 1
    t.at(190)
    t.ends()
    assert s.paused() == 1 and sl(fw).end == 0


def test_end_pauses_a_track_starting_over_under_repeat_one(fw, mcl):
    s, t = Sleep(fw), Track(fw, mcl)
    s.read(b'end')
    t.at(199)
    t.at(0)
    assert s.paused() == 1


def test_end_with_no_length_known_takes_any_move_on(fw, mcl):
    s, t = Sleep(fw), Track(fw, mcl, total=0)
    s.read(b'end')
    t.at(30)
    t.at(0, pos=4)
    assert s.paused() == 1


def test_between_tracks_end_decides_nothing(fw, mcl):
    s, t = Sleep(fw), Track(fw, mcl)
    s.read(b'end')
    t.at(199)
    t.at(-1, pos=4)
    assert s.paused() == 0 and sl(fw).pos == 3
    t.at(0)
    assert s.paused() == 1


def stopped(fw, *passes):
    """A pass at each (time_now_ms, play status)."""
    s = Sleep(fw)
    status = [2]
    s.read(b'end')
    fw.mock('mclGetPlayStatus', lambda *_: status[0])
    for ms, status[0] in passes:
        s.world.ms = ms
        check(fw)
    return s


def test_playback_stopped_three_seconds_lets_end_go(fw, mcl):
    Track(fw, mcl)
    s = stopped(fw, (1001, 1), (4000, 1))
    assert sl(fw).end == 1
    s.world.ms = 4001
    check(fw)
    assert sl(fw).end == 0 and s.paused() == 0 and s.world.of('unlink') == [(b'/tmp/q2-foo.sleep',)]


def test_a_moment_stopped_between_tracks_keeps_end(fw, mcl):
    Track(fw, mcl)
    stopped(fw, (1001, 1), (3001, 2), (3501, 1), (6000, 1))
    assert sl(fw).end == 1


def test_another_player_lets_end_go(fw, mcl):
    s, t = Sleep(fw), Track(fw, mcl)
    s.read(b'end')
    t.at(100)
    rd = fw.var('radio__rd', Rd)
    rd.pid, rd.active = 99, 1  # Internet Radio playing
    t.at(199)
    t.ends()
    assert sl(fw).end == 0 and s.paused() == 0


@pytest.mark.parametrize('status,radio,end', [(1, 0, 0), (2, 1, 0), (2, 0, 1), (3, 0, 1)])
def test_end_needs_the_local_player_playing_or_paused(fw, mcl, status, radio, end):
    s = Sleep(fw)
    Track(fw, mcl)
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.var('radio__rd', Rd).active = radio
    s.read(b'end')
    assert sl(fw).end == end and sl(fw).owner == end and len(s.world.of('unlink')) == 1 - end


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


def running(fw, state=None):
    """App foo started by its row and running, its status file state."""
    world = card(fw, foo=b'name=Foo\n')
    scan(fw)
    a = ap(fw).a[0]
    a.launched, a.launch_at = 1, world.ms
    world.files[b'/tmp/q2-foo'] = b'1'
    if state is not None:
        world.files[b'/tmp/q2-foo.state'] = state
    return world, a


def addon_poll(fw, world, ms=500):
    world.ms += ms
    fw.fn('addon_poll', None)()


def test_an_app_asking_to_hold_keeps_standby_and_power_off_away(fw):
    world, a = running(fw, b'addr=192.168.1.2:8080\nhold=1\n')
    addon_poll(fw, world)
    assert a.hold == 1 and [c[1][:3] for c in fw.calls('reset_poweroptions_timer')] == [(1, 1, 0)]  # not the screen's
    world.files[b'/tmp/q2-foo.state'] = b'hold=0\n'
    addon_poll(fw, world)
    assert a.hold == 0 and len(fw.calls('reset_poweroptions_timer')) == 1


@pytest.mark.parametrize('job, result, kept', [(JOB_INSTALL, SH_NET, 1), (JOB_INSTALL, SH_CANCELLED, 1), (JOB_INSTALL, SH_OK, 0), (JOB_REMOVE, SH_OK, 0), (JOB_REMOVE, SH_CARD, 1)])
def test_the_app_store_forgets_a_running_app_only_once_its_job_replaced_it(fw, job, result, kept):
    # an update that fails or is cancelled leaves the app running: still tracked, its hold still honoured
    world, a = running(fw, b'hold=1\n')
    table = C.addressof(C.c_char.in_dll(fw.lib, 'shelf_apps'))
    fw.var('shelf_n', C.c_int).value = fw.fn('shelf_sources', C.c_int, C.c_char_p, C.c_void_p, C.c_int)(b'me/foo', table, 1)
    fw.mock('fw_pthread_create', lambda *_: 0)  # the worker not run: its end set below
    fw.mock('timer_add', lambda *_: 1)
    fw.fn('netupdate__sh_start', None, C.c_int, C.c_int)(job, 0)
    addon_poll(fw, world)
    assert a.launched == 1 and a.hold == 1  # the app runs on until the job stops it
    a.relaunch = 1  # a remove cut off by USB storage: to start again once the card is back
    sh = fw.var('netupdate__sh', Sh)
    sh.running, sh.result = 0, result
    assert fw.fn('netupdate__sh_wait', C.c_int, C.c_void_p)(None) == 0
    assert (a.launched, a.hold, a.relaunch) == (kept, kept, kept)
    a.relaunch = 0
    holds = len(fw.calls('reset_poweroptions_timer'))
    addon_poll(fw, world)
    assert len(fw.calls('reset_poweroptions_timer')) == holds + kept


def test_an_app_that_stopped_by_itself_is_let_go_once_its_start_is_over(fw):
    world, a = running(fw, b'hold=1\n')
    del world.files[b'/tmp/q2-foo']  # its loop gone, or not there yet
    addon_poll(fw, world, 10000)
    assert a.launched == 1
    addon_poll(fw, world)
    assert a.launched == 0 and a.hold == 0
    addon_poll(fw, world)
    assert a.polled == world.ms - 500  # let go: no more polls


def test_an_apps_sleep_timer_pauses_through_the_poll(fw):
    world, a = running(fw)
    fw.mock('mclGetPlayStatus', lambda *_: 2)  # playing
    world.files[b'/tmp/q2-foo.sleep'] = b'until %d\n' % (world.now + 60)
    addon_poll(fw, world)
    world.now += 59
    addon_poll(fw, world)
    assert not fw.calls('player_play_pause')
    world.now += 1
    addon_poll(fw, world)
    assert len(fw.calls('player_play_pause')) == 1 and b'/tmp/q2-foo.sleep' not in world.files


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


KEY_DOWN, KEY_UP, KEY_LONG = O['EVT_KEY_DOWN'], O['EVT_KEY_UP'], O['EVT_KEY_LONG']
RETURN, CENTRE = O['KEY_RETURN'], 218
SNAKE_SOCK = b'/tmp/q2-snake.input.sock'


def settings(fw, **values):
    """config.ini as Q2 Pod reads its settings: these keys' values, every other its default."""
    wanted = {k.encode(): v for k, v in values.items()}
    def read(path, section, key, out, default, *_):
        value = wanted.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)
    fw.mock('toolsReadConfig', read)


class CardGame:
    """Card games as Library's Games runs them: fork, exec and waitpid stubbed, what reaches a game's input
    socket caught, toasts read, and the UI loop's addon_poll."""
    PID = 4242

    def __init__(self, fw, **games):
        self.fw = fw
        self.world = card(fw, **(games or {'snake': b'name=Snake\ngame=1\n'}))
        self.sent, self.toasts, self.status, self.exited = [], [], 0, False
        fw.mock('fw_fork', lambda *_: self.PID)
        fw.mock('fw_socket', lambda *_: 9)
        fw.mock('fw_sendto', lambda sock, buf, n, flags, to, *_: self.sent.append((C.string_at(to + 2), C.string_at(buf, n))))
        fw.mock('fw_waitpid', self.waitpid)
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8)))
        fw.fn('addon_games', C.c_int)()

    def waitpid(self, pid, status, options, *_):
        assert pid == self.PID and options == 1  # WNOHANG: the UI loop never waits for it
        if not self.exited:
            return 0
        C.c_int.from_address(status).value = self.status
        return pid

    def start(self, k=0):
        return self.fw.fn('addon_game_start', C.c_int, C.c_int)(k)

    def on(self):
        return self.fw.fn('addon_game_on', C.c_int)()

    def event(self, type, a, b=0):
        e = block(self.fw, size=0x40)
        C.c_int.from_address(e).value = type
        C.c_int.from_address(e + 0x18).value, C.c_int.from_address(e + 0x1c).value = a, b  # EVENT_KEY is EVENT_X
        return self.fw.fn('addon_game_input', C.c_int, C.c_void_p)(e)

    def key(self, type, code):
        return self.event(type, code)

    def touch(self, type, x, y):
        """A touch at the portrait panel's x, y."""
        return self.event(type, x, y)

    def poll(self, ms=16):
        addon_poll(self.fw, self.world, ms)

    def kills(self):
        return [c[0] for c in self.world.of('system')]


def test_card_games_are_games_rows_by_name_not_streaming_rows(fw):
    CardGame(fw, snake=b'name=Snake\ngame=1\n', chess=b'name=chess\ngame=1\n', foo=b'name=Foo\n')
    name = fw.fn('addon_game_name', C.c_char_p, C.c_int)
    assert fw.fn('addon_games_last', C.c_int)() == 2 and [name(k) for k in range(-1, 3)] == [None, b'chess', b'Snake', None]
    assert scan(fw) == 1 and names(fw, 'addon_name') == [None, b'Foo', None]
    is_game = fw.fn('addon_is_game', C.c_int, C.c_char_p)
    assert (is_game(b'snake'), is_game(b'chess'), is_game(b'foo'), is_game(b'none')) == (1, 1, 0, 0)


def test_a_game_runs_over_the_screen_taking_the_wheels_raw_travel(fw):
    g = CardGame(fw)
    travel = fw.var('wheel_travel', C.c_int)
    travel.value = 25  # the menu's spin before it
    assert g.on() == 0 and g.start() == 1 and g.on() == g.PID
    assert fw.var('wheel_raw', C.c_int).value == 1 and travel.value == 0
    assert g.start() == 0 and len(fw.calls('fw_fork')) == 1  # one at a time
    g.key(KEY_DOWN, CENTRE)
    assert g.sent == [(SNAKE_SOCK, b'k d 218')]


def test_the_child_runs_the_game_first_for_the_oom_killer_and_with_none_of_demos_files(fw):
    g = CardGame(fw)
    fw.mock('fw_fork', lambda *_: 0)
    fw.mock('fw_open', lambda *_: 5)
    env = []
    fw.mock('fw_setenv', lambda name, value, over, *_: env.append((C.string_at(name), C.string_at(value), over)) or 0)
    g.start()
    calls = fw.calls()
    order = [n for n, _ in calls]
    opened, wrote = fw.calls('fw_open')[0][1], fw.calls('fw_write')[0][1]
    assert C.string_at(opened[0]) == b'/proc/self/oom_score_adj' and wrote[0] == 5 and C.string_at(wrote[1], wrote[2]) == b'1000'
    execl = order.index('fw_execl')
    closed = [a[0] for n, a in calls[:execl] if n == 'fw_close']
    assert closed == [5, *range(3, 1024)]
    binary = b'/mnt/mmc/.snake/q2-snake'
    assert [C.string_at(p) if p else p for p in calls[execl][1][:3]] == [binary, binary, 0]
    assert order[execl + 1] == 'child_exit' and calls[execl + 1][1][0] == 127  # its exec failed
    (name, value, over), = env  # the child's own, after demo's files: the theme's pop-up (test_games)
    assert order.index('fw_setenv') == execl - 1 and name == b'Q2_POPUP' and over == 1
    assert value.split()[0] in (b'minimal', b'modern', b'classic') and len(value.split()) in (7, 71)


def test_a_game_off_the_card_says_so(fw):
    g = CardGame(fw)
    del g.world.files[b'/mnt/mmc/.snake/q2-snake']
    assert g.start() == 0 and g.toasts == [b"It isn't on the card"] and not fw.calls('fw_fork')


def test_a_game_the_app_store_is_replacing_waits_for_it(fw):
    g = CardGame(fw)
    table = C.addressof(C.c_char.in_dll(fw.lib, 'shelf_apps'))
    fw.var('shelf_n', C.c_int).value = fw.fn('shelf_sources', C.c_int, C.c_char_p, C.c_void_p, C.c_int)(b'me/snake', table, 1)
    sh = fw.var('netupdate__sh', Sh)
    sh.running, sh.job, sh.app = 1, JOB_INSTALL, 0  # installing app 0, snake
    assert g.start() == 0 and g.toasts == [b'Wait until the App Store finishes'] and not fw.calls('fw_fork')
    sh.running = 0
    assert g.start() == 1


def test_a_fork_that_fails_runs_nothing(fw):
    g = CardGame(fw)
    fw.mock('fw_fork', lambda *_: -1)
    assert g.start() == 0 and g.on() == 0 and fw.var('wheel_raw', C.c_int).value == 0


@pytest.mark.parametrize('type,code,sent', [
    (KEY_DOWN, RETURN, b'k d 170'), (KEY_UP, RETURN, b'k u 170'), (KEY_DOWN, CENTRE, b'k d 218'), (KEY_UP, CENTRE, b'k u 218'),
    (KEY_LONG, CENTRE, None), (KEY_LONG, RETURN, None),
])
def test_keys_reach_the_game_by_awtks_codes(fw, type, code, sent):
    g = CardGame(fw)
    g.start()
    assert g.key(type, code) == 1 and g.sent == ([(SNAKE_SOCK, sent)] if sent else [])


@pytest.mark.parametrize('type,what', [(0xff, b'd'), (0x100, b'd'), (0x101, b'm'), (0x102, b'm'), (0x103, b'u'), (0x104, b'u'), (0x107, b'u')])
def test_touches_reach_the_game_in_the_landscape_views_pixels(fw, type, what):
    g = CardGame(fw)
    g.start()
    # the view is the panel turned anticlockwise: the panel's top left (0, 0) is its bottom left (0, 319)
    assert g.touch(type, 0, 0) == 1 and g.touch(type, 319, 374) == 1 and g.touch(type, 10, 200) == 1
    assert g.sent == [(SNAKE_SOCK, b't %s %s' % (what, xy)) for xy in (b'0 319', b'374 0', b'200 309')]


def test_other_events_are_the_games_but_say_nothing(fw):
    g = CardGame(fw)
    g.start()
    assert g.event(0x105, 1, 2) == 1 and g.event(O['EVT_CLICK'], 1, 2) == 1 and g.sent == []


@pytest.mark.parametrize('code', [O['KEY_PLAY'], O['KEY_BACK_BTN'], O['KEY_FWD_BTN']])
@pytest.mark.parametrize('type', [KEY_DOWN, KEY_UP, KEY_LONG])
def test_play_and_the_side_buttons_stay_the_musics(fw, code, type):
    g = CardGame(fw)
    g.start()
    assert g.key(type, code) == 0 and g.sent == []


def test_with_no_game_running_input_is_stocks(fw):
    g = CardGame(fw)
    running_ = fw.fn('addon_game_input', C.c_int, C.c_void_p)
    assert g.key(KEY_DOWN, RETURN) == 0 and g.touch(0xff, 1, 1) == 0 and running_(None) == 0 and g.sent == []
    g.start()
    assert running_(None) == 1


def held(g):
    """Return held on a running game, from an odd ms (| 1 stamps it as it is), until its TERM: that ms."""
    g.start()
    g.world.ms = 1001
    g.key(KEY_DOWN, RETURN)
    g.key(KEY_DOWN, RETURN)  # its repeat: not a new press
    g.poll(699)
    assert g.kills() == []
    g.poll(1)
    assert g.kills() == [b'kill 4242']
    return g.world.ms


def test_return_held_ends_the_game_and_its_long_press_and_release_are_dropped(fw):
    g = CardGame(fw)
    held(g)
    assert g.key(KEY_LONG, RETURN) == 1 and g.key(KEY_UP, RETURN) == 1
    assert g.sent == [(SNAKE_SOCK, b'k d 170')] * 2  # neither sent: the game was told to end
    assert g.key(KEY_DOWN, RETURN) == 1 and g.sent[-1] == (SNAKE_SOCK, b'k d 170')  # still running: the next is its


def test_the_held_returns_release_after_the_game_has_gone_stays_out_of_stocks_games(fw):
    g = CardGame(fw)
    held(g)
    g.exited = True
    g.poll()
    assert g.on() == 0 and g.key(KEY_UP, RETURN) == 1 and g.key(KEY_DOWN, RETURN) == 0


def test_a_game_still_there_three_seconds_after_its_term_is_killed(fw):
    g = CardGame(fw)
    term = held(g)
    g.poll(2999)
    assert g.kills() == [b'kill 4242'] and g.world.ms == term + 2999
    g.poll(1)
    assert g.kills() == [b'kill 4242', b'kill -9 4242']


def test_the_wheels_travel_reaches_the_game_once_a_pass(fw):
    g = CardGame(fw)
    g.start()
    travel = fw.var('wheel_travel', C.c_int)
    travel.value = -37
    g.poll()
    assert g.sent == [(SNAKE_SOCK, b'w -37')] and travel.value == 0
    g.poll()
    assert len(g.sent) == 1


def test_a_game_left_idle_two_minutes_is_ended(fw):
    g = CardGame(fw)
    g.start()
    g.poll(119999)
    g.key(KEY_DOWN, CENTRE)  # input: idle from here
    g.poll(119999)
    assert g.kills() == []
    g.poll(1)
    assert g.kills() == [b'kill 4242']


def test_smart_awake_keeps_an_idle_game(fw):
    settings(fw, KEEPLIT=b'1')
    g = CardGame(fw)
    g.start()
    g.poll(120000)
    g.poll(120000)
    assert g.kills() == [] and g.on() == g.PID


@pytest.mark.parametrize('status,toast', [(0, []), (127 << 8, [b"It couldn't start"]), (9, [])])
def test_a_game_that_has_gone_gives_the_screen_back(fw, status, toast):
    g = CardGame(fw)
    g.start()
    g.exited, g.status = True, status
    g.poll()
    assert g.on() == 0 and fw.var('wheel_raw', C.c_int).value == 0 and fw.calls('fw_close')[-1][1][0] == 9
    assert len(fw.calls('widget_invalidate_force')) == 1 and g.toasts == toast  # its exec failed: the child's 127
    assert g.key(KEY_DOWN, RETURN) == 0  # input is stock's again


def test_the_screen_and_standby_stay_on_while_a_game_runs(fw):
    g = CardGame(fw)
    g.start()
    g.poll()
    assert [c[1][:3] for c in fw.calls('reset_poweroptions_timer')] == [(1, 1, 1)]
    g.exited = True
    g.poll()
    g.poll()
    assert len(fw.calls('reset_poweroptions_timer')) == 1


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
        self.fw, self.sel, self.y, self.timers, self.drawn, self.toasts, self.backs = fw, 0, 0, 0, 0, [], 0
        self.aborts: list = []  # sections()' swipe aborts
        self.tips: list = []  # field()'s box hints
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
            key = C.string_at(name)
            return self.sel if key == b'_ringnav_index' else self.y if key == b'yoffset' else default
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
    assert p.toasts == [b'Done'] and p.request().endswith(b'\tmenu\n') and p.top.state == K_LOAD


def test_an_action_refused_toasts_why(fw):
    p = Page(fw)
    p.answer(rows=[item(1)])
    p.click(0)
    p.answer(status=b'error')
    assert p.toasts == [b"Foo didn't answer"]


def test_a_second_action_press_while_one_is_in_flight_sends_nothing(fw):
    """A toggle pressed twice: the app has usually done the first, so a second would undo it and drop the first's
    reply. Only another action row's press waits for the reply."""
    p = Page(fw)
    p.answer(rows=[item(1), item(2)])
    p.click(0)
    sent, req = len(fw.calls('fw_pthread_create')), p.req.id
    p.click(0)
    p.click(1)
    assert len(fw.calls('fw_pthread_create')) == sent and p.req.id == req and p.pg.act == 1
    p.answer(msg=b'Added')
    assert p.toasts == [b'Added'] and p.request().endswith(b'\tmenu\n')  # one reply, one toast, asked again
    p.answer(rows=[item(1), item(2)])
    p.click(1)
    assert p.request().endswith(b'\top2\n')  # answered: the next press is sent


def test_a_list_row_while_an_action_is_in_flight_opens_as_before(fw):
    """Only action rows wait: a list row opens its level at once, its request in the action's place."""
    p = Page(fw)
    p.answer(rows=[item(1), item(2, b'list')])
    p.click(0)
    p.click(1)
    assert p.pg.depth == 2 and p.pg.act == 0 and p.request().endswith(b'\top2\n')


def test_a_held_play_pause_while_an_action_is_in_flight_sends_its_op_as_before(fw):
    p = Page(fw)
    p.answer(rows=[item(1), item(2, hold=b'h')])
    p.click(0)
    p.sel = 1
    assert fw.fn('addon_hold', C.c_int, C.c_void_p)(p.page) == 1 and p.request().endswith(b'\th\n')


def test_return_while_an_action_is_in_flight_still_leaves(fw):
    p = Page(fw)
    p.answer(rows=[item(1, b'list')])
    p.click(0)
    p.answer(rows=[item(2)])
    p.click(0)
    p.back()
    assert p.pg.depth == 1 and p.pg.act == 0 and p.request().endswith(b'\tmenu\n')
    p.back()
    assert p.backs == 1  # and leaves the page from the first level


@pytest.mark.parametrize('detail,label', [(b'sub', 1), (b'', 0)])
def test_an_unanswered_action_says_working_on_its_row_until_the_reply(fw, detail, label):
    """As the App Store's rows say Installing…: after BUSY_MS the pressed row's detail, or its title when it has
    none, says Working…; the reply puts its text back before its toast."""
    rows = [row_of(b'a', b'One', b'one'), row_of(b'b', b'Add to Sync', detail)]
    p = Page(fw)
    p.answer(msg=b'refresh', rows=rows)
    items, kids, written, _ = row_walk(p, rows)
    p.click(1)
    p.world.ms += 399
    p.tick()
    assert written == []
    p.world.ms += 1
    p.tick()
    p.tick()
    assert written == [(kids[items[1]][label], 'Working…'.encode())]
    p.answer(msg=b'Added')
    assert written[1:] == [(kids[items[1]][label], rows[1][3 - (not detail)])] and p.toasts == [b'Added']


def test_working_comes_back_on_a_list_drawn_again_while_the_action_waits(fw):
    """The search box opened and closed over it draws the list anew, its own texts: the tick says Working… again."""
    rows = [row_of(b'a', b'One', b'one')]
    p = Page(fw)
    p.answer(msg=b'refresh', rows=rows)
    items, kids, written, _ = row_walk(p, rows)
    p.click(0)
    p.world.ms += 400
    p.tick()
    p.pg.next = 1  # G_SHOW: the search box's blur
    fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    del written[:]
    p.tick()
    assert written == [(kids[items[0]][1], 'Working…'.encode())] and p.pg.busy == 1


def test_an_action_that_fails_puts_its_row_back_with_the_toast(fw):
    rows = [row_of(b'a', b'One', b'one')]
    p = Page(fw)
    p.answer(msg=b'refresh', rows=rows)
    items, kids, written, _ = row_walk(p, rows)
    p.click(0)
    p.world.ms += 400
    p.tick()
    p.answer(fail=REQ_FAIL)
    assert written[:2] == [(kids[items[0]][1], 'Working…'.encode()), (kids[items[0]][1], b'one')]  # then Loading…, as before
    assert p.toasts == [b"Foo didn't answer"] and p.pg.busy == 0


def test_an_action_answered_at_once_says_nothing_on_its_row(fw):
    rows = [row_of(b'a', b'One', b'one')]
    p = Page(fw)
    p.answer(msg=b'refresh', rows=rows)
    _, _, written, _ = row_walk(p, rows)
    p.click(0)
    p.world.ms += 100
    p.answer()
    assert written == [] and p.pg.busy == 0


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


NOSTOP = b'name=Foo\nstop=0\n'


def test_stop_0_keeps_a_slow_load_to_its_loading_page(fw):
    p = Page(fw, NOSTOP)
    p.world.ms += 3001
    p.tick()
    assert p.top.slow == 0 and p.drawn == 0 and p.top.state == K_LOAD


def test_stop_0_ends_an_error_page_in_cancel_which_leaves_the_page_the_app_running(fw):
    p = Page(fw, NOSTOP)
    texts: list = []
    fw.mock('widget_set_text_utf8', lambda w, t, *_: texts.append(C.string_at(t)))
    p.answer(status=b'error', msg=b'Nope')
    assert p.top.state == K_ERROR and p.drawn == 2
    assert texts[-2:] == [b'Retry', b'Cancel'] and b'Stop Foo' not in texts
    p.click(1)
    assert p.backs == 1 and p.ap.a[0].launched == 1 and p.toasts == []
    assert not [c for c in p.world.of('system') if c[0].startswith(b'kill')]


def test_stop_0_keeps_a_slow_start_to_its_waiting_page(fw):
    p = Page(fw, NOSTOP)
    p.world.ms += 500
    p.answer(fail=REQ_NOSOCK, raw=b'')
    assert p.top.state == K_WAIT
    p.world.ms += 3001
    p.tick()
    assert p.top.slow == 0 and p.drawn == 0 and p.top.state == K_WAIT


@pytest.mark.parametrize('leave', [lambda p: p.click(1), lambda p: p.back()], ids=['cancel', 'return'])
def test_cancel_gives_the_same_result_as_return(fw, leave):
    p = Page(fw, NOSTOP)
    p.answer(status=b'error')
    leave(p)
    assert (p.backs, p.pg.depth, p.ap.a[0].launched, p.toasts) == (1, 1, 1, [])


def test_cancel_on_a_deeper_levels_error_goes_back_to_the_level_below(fw):
    p = Page(fw, NOSTOP)
    p.answer(rows=[item(1, b'list')])
    p.click(0)
    p.answer(status=b'error')
    p.click(1)
    assert p.pg.depth == 1 and p.backs == 0 and p.request().endswith(b'\tmenu\n')


def test_stop_0_still_offers_cancel_on_an_app_that_isnt_running(fw):
    p = Page(fw, NOSTOP)
    p.ap.a[0].launched = 0
    del p.world.files[b'/tmp/q2-foo']
    p.answer(status=b'error')
    assert p.drawn == 2


def art_path(uri, ctx):
    h = 2166136261
    for c in uri + b'\t' + ctx:
        h = ((h ^ c) * 16777619) & 0xffffffff
    return b'/tmp/q2-foo.art/%08x.jpg' % h


def jpeg(w=64, h=64):
    """A baseline JPEG's start: the SOI and its frame header, all that picture_size reads."""
    return b'\xff\xd8\xff\xc0\x00\x11\x08' + struct.pack('>HH', h, w) + bytes(16)


@pytest.mark.parametrize('cover,kind,art', [
    (None, b'action', 0), (art_path(b'op0', b'x'), b'action', 0), (art_path(b'op1', b''), b'action', 1),
    (art_path(b'op0', b'c'), b'action', 1), (art_path(b'op0', b'c'), b'progress', 0)])
def test_a_cover_for_any_row_draws_the_list_with_covers(fw, cover, kind, art):
    """addon.c art_rows: a row's cover, keyed by its URI, a tab and its context, draws the list with
    covers; no file, another key, or a progress row in the list keeps the plain rows."""
    p = Page(fw)
    if cover:
        p.world.files[cover] = jpeg()
    p.answer(rows=[item(0, ctx=b'c'), item(1, kind)])
    assert p.top.art == art


@pytest.mark.parametrize('data,art', [
    (jpeg(128, 128), 1), (jpeg(129, 64), 0), (jpeg(64, 129), 0), (jpeg(0, 64), 0), (jpeg(64, 0), 0), (b'', 0), (b'not a picture' * 4, 0)])
def test_a_cover_over_128_px_or_of_no_known_size_is_none(fw, data, art):
    """addon.c art_file: the header's size decides, read before the list is drawn, not decoded."""
    p = Page(fw)
    p.world.files[art_path(b'op0', b'')] = data
    p.answer(rows=[item(0)])
    assert p.top.art == art


def test_a_uris_escapes_are_undone_before_the_key(fw):
    """addon.c art_file hashes the field as spot_reply left it: the app's value, not its wire form."""
    p = Page(fw)
    p.world.files[art_path(b'a%b', b'')] = jpeg()
    p.answer(rows=[(b'action', b'a%25b', b'One', b'', b'', b'')])
    assert p.top.art == 1


def unloads(fw):
    """Each image_manager_unload_bitmap_by_name's name, read as it is called (art_row's is on the stack)."""
    names = []
    fw.mock('image_manager_unload_bitmap_by_name', lambda _, name, *__: names.append(C.string_at(name)) or 0)
    return names


def test_a_rows_bitmap_is_let_go_before_its_image_and_at_the_level_end(fw):
    """addon.c art_row and drop: a file changed since decodes again; closing the page keeps none of its bitmaps,
    the file gone by then or not."""
    p = Page(fw)
    names = unloads(fw)
    C.c_int.from_address(p.view + W_H).value = 390
    url = b'file://' + art_path(b'op0', b'')
    p.world.files[url[7:]] = jpeg()
    p.answer(rows=[item(0), item(1, b'list')])
    assert names == [url]
    del p.world.files[url[7:]], names[:]
    fw.fn('addon__closed', C.c_int, C.c_void_p, C.c_void_p)(p.pg.page, None)
    assert names == [url, b'file://' + art_path(b'op1', b'')]


def scrolled(fw, count):
    """A list of count rows with a cover in row 0, 390 px high (five 78 px rows); its unload names; a row's URL."""
    p = Page(fw)
    names = unloads(fw)
    C.c_int.from_address(p.view + W_H).value = 390
    p.world.files[art_path(b'op0', b'')] = jpeg()
    p.answer(rows=[item(k) for k in range(count)])
    del names[:]
    return p, names, lambda k: b'file://' + art_path(b'op%d' % k, b'')


def test_scrolling_past_the_window_unloads_the_rows_left_behind(fw):
    """addon.c art_window: at most ART_KEEP rows' covers stay decoded around the yoffset; those left behind go,
    those within stay, and a row scrolled back keeps its image's name, to decode again when it paints."""
    p, names, url = scrolled(fw, 50)
    assert (p.pg.art_lo, p.pg.art_hi) == (0, 6)  # the rows in view and the one past, from the draw
    set_images = len(fw.calls('image_base_set_image'))
    p.y = 30 * 78
    p.tick()
    assert (p.pg.art_lo, p.pg.art_hi) == (20, 36) and names == [url(k) for k in range(20)]
    del names[:]
    p.y = 0  # back: rows 24.. of the old range leave, 20 to 23 stay
    p.tick()
    assert (p.pg.art_lo, p.pg.art_hi) == (0, 24) and names == [url(k) for k in range(24, 36)]
    assert len(fw.calls('image_base_set_image')) == set_images  # the rows keep their names, nothing rebuilt


def test_a_fling_past_the_window_leaves_nothing_decoded_behind(fw):
    """addon.c art_window: rows crossed in one tick, further than the window, are in the range and unloaded."""
    p, names, url = scrolled(fw, 50)
    p.y = 44 * 78  # 44 rows on, past the old range (0, 5) and past the window it left
    p.tick()
    assert (p.pg.art_lo, p.pg.art_hi) == (26, 50) and names == [url(k) for k in range(26)]


def test_a_drag_past_the_top_names_no_row_before_the_first(fw):
    """addon.c art_window: stock's rubber band reads a negative yoffset; art_url of row -1 would read before pg.f."""
    p, names, url = scrolled(fw, 50)
    for y in (-100, -390, -1000):
        p.y = y
        p.tick()
        assert (p.pg.art_lo, p.pg.art_hi) == (0, 6) and names == []


def test_leaving_within_a_tick_counts_the_rows_painted_since(fw):
    """addon.c drop: a list left before the next tick lets go of the rows a scroll painted since the last one."""
    p, names, url = scrolled(fw, 50)
    p.y = 30 * 78  # no tick before the close
    fw.fn('addon__closed', C.c_int, C.c_void_p, C.c_void_p)(p.pg.page, None)
    assert sorted(names) == sorted(url(k) for k in range(36)) and (p.pg.art_lo, p.pg.art_hi) == (0, 0)


def test_drop_unloads_only_the_range(fw):
    """addon.c drop: only the rows that may be decoded, with no dependence on art and no file read."""
    p, names, url = scrolled(fw, 50)
    p.y = 30 * 78
    p.tick()
    del names[:]
    p.top.art = 0
    del p.world.files[art_path(b'op0', b'')]
    fw.fn('addon__closed', C.c_int, C.c_void_p, C.c_void_p)(p.pg.page, None)
    assert names == [url(k) for k in range(20, 36)] and (p.pg.art_lo, p.pg.art_hi) == (0, 0)


def test_a_list_under_the_window_never_unloads(fw):
    p, names, url = scrolled(fw, 20)
    for row in (5, 15, 0):
        p.y = row * 78
        p.tick()
    assert not names


def test_a_later_pages_progress_row_draws_its_bar_in_a_cover_list(fw):
    """addon.c row: the art branch is the page's, the progress bar the row's."""
    p = Page(fw)
    p.world.files[art_path(b'op0', b'')] = jpeg()
    p.answer(rows=[item(0)], msg=b'pages', cursor=b'c1')
    created = []
    fw.mock('widget_factory_create_widget', lambda *a: created.append(C.string_at(a[1])) or block(fw, size=64))
    p.answer(rows=[(b'progress', b'u', b't', b's', b'', b'500')], msg=b'ok')
    assert p.top.art == 1 and created == [b'view']


def test_a_more_row_under_a_section_is_named_for_the_section(fw):
    """addon.c go: the level a section's More… pushes shows the section's plain name, not its position."""
    p = sections(fw)
    p.answer(rows=[item(0)], cursor=b'c1')
    assert p.top.title == b'S0 1/3'
    p.click(1)
    assert p.top.title == b'S0' and p.pg.depth == 2


@pytest.mark.parametrize('kind,sel', [(b'search', 2), (b'sort', 2), (b'action', 1)])
def test_a_next_window_selects_the_first_content_row(fw, kind, sel):
    """addon.c page_to: past Previous page, and past a Search or Sort row left as a row (here a sole one), as the
    reply has."""
    p = Page(fw)
    p.answer(rows=[(kind, b'op', b'Row', b'', b'', b'')])
    fw.fn('addon__page_to', None, C.c_void_p, C.c_char_p, C.c_int)(C.addressof(p.top), b'c', 1)
    assert p.top.sel == sel


def test_a_claimed_swipe_stops_the_lists_fling(fw):
    """addon.c swipe, as pull_event: the claim stops a scroll still animating the rows it took."""
    p = sections(fw)
    p.answer(rows=[item(0)])
    scroll_view = C.create_string_buffer(b'scroll_view')
    fw.mock('widget_get_type', lambda *_: C.addressof(scroll_view))
    C.c_byte.from_address(p.view + O['VIEW_VERTICAL']).value = 1
    C.c_int.from_address(p.view + W_H).value = 390
    C.c_void_p.from_address(p.view + O['VIEW_ANIMATOR']).value = block(fw, size=16)
    touch(p, 'EVT_POINTER_DOWN', 200, 100)
    touch(p, 'EVT_POINTER_MOVE_BEFORE', 150, 100)
    assert len(fw.calls('widget_animator_pause')) == 1 and len(fw.calls('widget_animator_destroy')) == 1


def test_a_swipe_redecides_covers_for_the_section_it_opens(fw):
    """addon.c sec_open: lv_t.art is the drawn list's, so a section (each calloc'd level) starts without one and
    the outgoing section's covers are let go before the level is replaced."""
    p = sections(fw)
    fw.mock('pointer_event_init', lambda *_: 0)
    fw.mock('widget_dispatch_event_to_target_recursive', lambda *_: 0)
    names = unloads(fw)
    C.c_int.from_address(p.view + W_H).value = 390
    url = b'file://' + art_path(b'op0', b'')
    p.world.files[url[7:]] = jpeg()
    p.answer(rows=[item(0)])
    assert p.top.art == 1
    del names[:]
    swipe(p, -100)
    assert p.pg.cur == 1 and p.top.art == 0 and names == [url]
    p.answer(rows=[item(1)])  # no cover in this one
    assert p.top.art == 0
    swipe(p, 100)
    assert p.pg.cur == 0 and p.top.art == 0
    p.answer(rows=[item(0)])  # back to the covered one, decided again
    assert p.top.art == 1


NEXT = (b'page', b'next', b'Next page', b'', b'c200', b'')


def windowed(fw):
    """A level whose list (op1, its own argument ctx) is on show, as a window's page row presses."""
    p = Page(fw)
    p.answer(rows=[item(1, b'list', b'ctx')])
    p.click(0)
    return p


def selects(fw):
    """Each _ringnav_index ringnav_select has written."""
    sel = []
    fw.mock('widget_set_prop_int', lambda _, key, v, *__: sel.append(v) if C.string_at(key) == b'_ringnav_index' else 0)
    return sel


@pytest.mark.parametrize('uri', [b'next', b'prev'])
def test_a_failed_window_selects_retry(fw, uri):
    """addon.c show: an error page selects its Retry, not the row the window would have selected (its Stop)."""
    p = windowed(fw)
    p.answer(rows=[item(2), (b'page', uri, b'Page', b'', b'c200', b'')])
    sel = selects(fw)
    p.click(1)
    p.sel = 7  # ringnav's prop only writes a change
    p.answer(status=b'error')
    assert p.top.state == K_ERROR and sel[-1] == 0


def test_an_empty_context_asks_for_the_lists_start(fw):
    """addon.c page_to: a Previous page back to the first window sends no cursor, not an empty last argument."""
    p = windowed(fw)
    p.answer(rows=[item(2), (b'page', b'prev', b'Previous page', b'', b'', b'')])
    p.click(1)
    assert p.request().endswith(b'\top1\tctx\n') and p.top.base == 4


def test_a_reload_asks_for_the_window_on_show(fw):
    """addon.c page_to replaces the level's arguments, so Back from a deeper level returns to this window."""
    p = windowed(fw)
    p.answer(rows=[item(2), NEXT])
    p.click(1)
    p.answer(rows=[item(3, b'list')])
    p.click(0)
    p.answer(rows=[item(4)])
    p.back()
    assert p.top.state == K_LOAD and p.request().endswith(b'\top1\tctx\tc200\n')


def test_a_press_with_a_page_in_flight_asks_for_the_window(fw):
    """addon.c page_to: the next page's request is cancelled and the new window's asked for in its place."""
    p = windowed(fw)
    p.answer(msg=b'pages', cursor=b'c1', rows=[item(2), NEXT])
    assert p.pg.paging == 1 and p.request().endswith(b'\top1\tctx\tc1\n')
    p.click(1)
    assert p.pg.paging == 0 and p.request().endswith(b'\top1\tctx\tc200\n') and p.top.id == p.req.id
    p.answer(rows=[item(3), item(4)])
    assert p.top.state == K_READY and p.top.n == 2 and p.cell(0, 2) == b'Row 3'


def test_a_page_row_in_a_list_with_covers_is_as_high_as_its_rows(fw):
    """addon.c row: a page row takes art_row's path, 78 px, its cover's room empty, as the trailing row."""
    p = Page(fw)
    p.world.files[art_path(b'op0', b'')] = jpeg()
    moved, images = [], []
    fw.mock('widget_move_resize', lambda _, x, y, w, h, *__: moved.append((x, y, w, h)) or 0)
    fw.mock('image_create', lambda *_: images.append(1) or 0)
    p.answer(rows=[item(0), NEXT])
    assert p.top.art == 1 and (0, 0, 375, 78) in moved and (0, 78, 375, 78) in moved and len(images) == 1


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


SEARCH = (b'search', b'', b'Search', b'', b'', b'')


def live(fw, rows, covers=()):
    p = Page(fw)
    fw.mock('window_manager_get_top_window', lambda *_: p.page)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    for cover in covers:
        p.world.files[cover] = jpeg()
    p.answer(msg=b'refresh', rows=rows)
    return p


def test_an_action_on_a_live_level_keeps_it_shown_until_the_new_rows_are_drawn(fw):
    """A choice page (refresh): no Loading… page between the press and the new rows. Selected moving from one row
    to another changes which rows have a detail, so the list is drawn again (same_rows), its row kept selected."""
    rows = [row_of(b'a', b'One', b'Selected', kind=b'action'), row_of(b'b', b'Two', b'', kind=b'action')]
    p = live(fw, rows)
    p.sel = 1  # the cursor on the row pressed, as the wheel or a tap leaves it
    p.click(1)
    p.answer()  # the action's reply: no message, so no toast
    assert p.top.state == K_READY and not p.toasts  # still shown: no Loading… page
    assert p.request().endswith(b'\tmenu\n')  # the level asked again at once, not after ADDON_LIVE_MS
    drawn = len(fw.calls('list_item_create'))
    p.answer(msg=b'refresh', rows=[row_of(b'a', b'One', b'', kind=b'action'), row_of(b'b', b'Two', b'Selected', kind=b'action')])
    assert p.top.state == K_READY and p.cell(1, 3) == b'Selected' and p.cell(0, 3) == b''
    assert len(fw.calls('list_item_create')) == drawn + 2 and p.top.sel == 1  # drawn again from the reply, not Loading…


def test_an_action_on_a_live_level_whose_rows_keep_their_shape_writes_them_in_place(fw):
    """Only texts changed (each row keeps a detail): written in place, no row drawn again."""
    rows = [row_of(b'a', b'One', b'Selected', kind=b'action'), row_of(b'b', b'Two', b'-', kind=b'action')]
    p = live(fw, rows)
    drawn = len(fw.calls('list_item_create'))
    p.click(1)
    p.answer()
    p.answer(msg=b'refresh', rows=[row_of(b'a', b'One', b'-', kind=b'action'), row_of(b'b', b'Two', b'Selected', kind=b'action')])
    assert len(fw.calls('list_item_create')) == drawn and p.cell(1, 3) == b'Selected' and p.cell(0, 3) == b'-'


def test_a_press_while_a_live_level_is_asked_again_takes_its_place(fw):
    """The level stays pressable while it is asked again: a second action cancels that request and is sent; its
    reply is taken as the action's, and the level is asked again after it."""
    rows = [row_of(b'a', b'One', b'', kind=b'action'), row_of(b'b', b'Two', b'', kind=b'action')]
    p = live(fw, rows)
    p.click(0)
    p.answer()
    assert p.request().endswith(b'\tmenu\n') and p.pg.act == 0  # the level asked again, not answered yet
    p.click(1)
    assert p.request().endswith(b'\tb\n') and p.pg.act == 1  # the second action in its place
    p.answer(msg=b'Done')
    assert p.toasts == [b'Done'] and p.request().endswith(b'\tmenu\n') and p.top.state == K_READY


def test_an_action_on_a_live_level_that_fails_reloads_it_as_before(fw):
    p = live(fw, [row_of(b'a', b'One', b'', kind=b'action')])
    p.click(0)
    p.answer(status=b'error')
    assert p.top.state == K_LOAD and p.toasts == [b"Foo didn't answer"]  # as before: asked again from Loading…


def test_a_failed_ask_after_an_action_on_a_live_level_keeps_the_pressed_row(fw):
    """Its Retry comes back on the row pressed, as the reload after an action does."""
    p = live(fw, [row_of(b'a', b'One', b'', kind=b'action'), row_of(b'b', b'Two', b'', kind=b'action')])
    p.sel = 1
    p.click(1)
    p.answer()
    p.answer(status=b'error')
    assert p.top.state == K_ERROR and p.top.sel == 1


def test_a_return_under_an_actions_toast_leaves_the_new_tops_request_alone(fw):
    """The toast runs the UI's loop, so the level can be popped under it: the level below's request stands."""
    p = live(fw, [item(1, b'list')])
    p.click(0)
    p.answer(msg=b'refresh', rows=[row_of(b'a', b'One', b'', kind=b'action')])
    p.click(0)
    fw.mock('navigator_to_with_context', lambda *_: p.back())
    p.answer(msg=b'Done')
    assert p.pg.depth == 1 and p.top.id == p.req.id and p.request().endswith(b'\tmenu\n')


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


def test_a_refresh_with_new_texts_in_the_same_rows_is_not_rebuilt_and_keeps_the_click(fw):
    """The same kinds, uris and contexts, only a title moved: written in place (relabel), not rebuilt."""
    p = live(fw, rows_of(0, 5))
    drawn = len(fw.calls('list_item_create'))
    moved = [*rows_of(0, 4), (b'action', b'op4', b'Row 4 moved', b'sub', b'', b'')]
    p.world.ms += 2000
    p.tick()
    p.press(4)
    p.answer(msg=b'refresh', rows=moved)
    assert len(fw.calls('list_item_create')) == drawn and p.cell(4, 2) == b'Row 4 moved'
    fire(p)
    assert p.req.running and p.request().endswith(b'\top4\n')


def row_of(uri, title=b'Row', detail=b'sub', kind=b'action', position=b'', ctx=b''):
    return (kind, uri, title, detail, ctx, position)


@pytest.fixture
def marq(fw, library):
    """st.marq_row, the selected row's marquee (navigation.c), as this build lays scratch_t out."""
    base = C.addressof(fw.var('st', C.c_char))
    return C.c_void_p.from_address(base + host.offsets(library.parent/'navigation.o', 'scratch_t')['marq_row'])


def row_walk(p, rows):
    """A live level's rows as a real child walk over mocked widgets: each its title and detail labels and, for
    a progress row, a bar. Returns the rows' items, their children, the texts written and the props set."""
    fw = p.fw
    kind_of = {True: block(fw, b'hscroll_label'), False: block(fw, b'view')}
    items, kids, texts = [], {}, {}
    for r in rows:
        item = block(fw, size=16)
        labels = [block(fw, size=16), block(fw, size=16)]
        texts[labels[0]], texts[labels[1]] = r[2], r[3]
        kids[item] = labels + ([block(fw, size=16)] if r[0] == b'progress' else [])
        items.append(item)
    kids[p.pg.view] = items
    bars = {c for item in items for c in kids[item][2:]}
    written, props = [], []
    def get_text(w, out, n, *_):
        C.memmove(out, texts[w] + b'\0', len(texts[w]) + 1)
    def set_text(w, s, *_):
        written.append((w, C.string_at(s)))
        texts[w] = C.string_at(s)
    def get_int(w, name, default, *_):
        return 1 if w in bars and C.string_at(name) == b'_pml' else p.sel if C.string_at(name) == b'_ringnav_index' else default
    fw.mock('widget_count_children', lambda w, *_: len(kids.get(w, ())))
    fw.mock('widget_get_child', lambda w, i, *_: kids[w][i])
    fw.mock('widget_get_type', lambda w, *_: kind_of[w in texts])
    fw.mock('widget_get_text_utf8', get_text)
    fw.mock('widget_set_text_utf8', set_text)
    fw.mock('widget_get_prop_int', get_int)
    fw.mock('widget_set_prop_int', lambda w, name, value, *_: props.append((w, C.string_at(name), value)))
    return items, kids, written, props


def refresh(p, rows, cursor=b''):
    p.world.ms += 2000
    p.tick()
    p.answer(msg=b'refresh', rows=rows, cursor=cursor)


def test_a_refresh_writes_only_the_labels_and_bars_that_changed(fw):
    """addon.c relabel: a changed title or detail is written and starts from its left, the row's rest untouched;
    a progress row's bar takes the new permille and repaints."""
    rows = [row_of(b'a', b'Same', b'same'), row_of(b'b', b'Old', b'same'), row_of(b'c', b'Same', b'old'), row_of(b'd', kind=b'progress', position=b'100')]
    p = live(fw, rows)
    drawn = len(fw.calls('list_item_create'))
    items, kids, written, props = row_walk(p, rows)
    bar = kids[items[3]][2]
    refresh(p, [rows[0], row_of(b'b', b'New', b'same'), row_of(b'c', b'Same', b'new'), row_of(b'd', kind=b'progress', position=b'420')])
    assert len(fw.calls('list_item_create')) == drawn
    assert written == [(kids[items[1]][0], b'New'), (kids[items[2]][1], b'new')]
    assert [(w, v) for w, name, v in props if name == b'xoffset'] == [(kids[items[1]][0], 0), (kids[items[2]][1], 0)]
    assert (bar, b'_pml', 420) in props and fw.calls('widget_invalidate_force')[-1][1][0] == bar


@pytest.mark.parametrize('selected,moved,cleared', [
    (1, row_of(b'b', b'New', b'sub'), True),   # the selected row's title
    (1, row_of(b'b', b'Old', b'new'), False),  # its detail only
    (0, row_of(b'b', b'New', b'sub'), False),  # another row's title
])
def test_a_refresh_restarts_the_selected_row_s_marquee_only_for_its_title(fw, marq, selected, moved, cleared):
    """A new title on the marquee's row: st.marq_row cleared, so the next paint re-arms it (paint_selection)."""
    rows = [row_of(b'a', b'Same', b'sub'), row_of(b'b', b'Old', b'sub')]
    p = live(fw, rows)
    items, _, _, _ = row_walk(p, rows)
    marq.value = items[selected]
    refresh(p, [rows[0], moved])
    assert (marq.value is None) == cleared


@pytest.mark.parametrize('rows,cursor', [
    (rows_of(0, 2), b''),
    ([item(0, b'info'), *rows_of(1, 2)], b''),
    ([(b'action', b'opX', b'Row 0', b'sub', b'', b''), *rows_of(1, 2)], b''),
    ([item(0, ctx=b'c'), *rows_of(1, 2)], b''),
    ([(b'action', b'op0', b'Row 0', b'', b'', b''), *rows_of(1, 2)], b''),
    (rows_of(0, 3), b'c1'),
    ([SEARCH, *rows_of(0, 3)], b''),
])
def test_a_refresh_with_other_rows_is_rebuilt(fw, rows, cursor):
    """addon.c same_rows: a row's kind, uri or context, a detail going, More… appearing, or a first Search row
    now taken (take_find) each draw the list again."""
    p = live(fw, rows_of(0, 3))
    drawn = len(fw.calls('list_item_create'))
    refresh(p, rows, cursor)
    assert len(fw.calls('list_item_create')) > drawn


def test_a_refresh_compares_the_rows_after_a_taken_search(fw):
    """The first Search row is the title bar's icon: the rows compared are the ones under it."""
    p = live(fw, [SEARCH, *rows_of(0, 2)])
    assert p.top.find == 1 and p.top.n == 2
    drawn = len(fw.calls('list_item_create'))
    refresh(p, [SEARCH, item(0), (b'action', b'op1', b'Row 1 moved', b'sub', b'', b'')])
    assert p.top.find == 1 and len(fw.calls('list_item_create')) == drawn and p.cell(1, 2) == b'Row 1 moved'


def test_a_refresh_in_place_keeps_the_covers_and_a_new_cover_rebuilds(fw):
    """Written in place, the shown covers stay in the image manager (drop isn't run); one more cover draws the
    list again."""
    names = unloads(fw)
    p = live(fw, rows_of(0, 2), covers=[art_path(b'op0', b'')])
    drawn = len(fw.calls('list_item_create'))
    del names[:]
    refresh(p, [item(0), (b'action', b'op1', b'Row 1 moved', b'sub', b'', b'')])
    assert p.top.art == 1 and not names and len(fw.calls('list_item_create')) == drawn
    p.world.files[art_path(b'op1', b'')] = jpeg()
    refresh(p, [item(0), (b'action', b'op1', b'Row 1 again', b'sub', b'', b'')])
    assert p.top.art == 2 and len(fw.calls('list_item_create')) > drawn


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


def edit_box(fw):
    """edit_create is peq_ui.c's, so its widget is mocked: the address the box gets."""
    box = C.create_string_buffer(256)
    fw.mock('widget_factory_create_widget', lambda *_: C.addressof(box))
    return C.addressof(box)


def tap_find(p):
    """The title bar's icon tapped: the box stands, alone on the page."""
    box = edit_box(p.fw)
    p.fw.fn('addon__bar_tapped', C.c_int, C.c_void_p, C.c_void_p)(G_FIND, None)
    p.fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    assert p.pg.edit == box and p.pg.view is None


def test_the_search_box_opens_a_level_for_its_text(fw):
    p = Page(fw)
    p.answer(rows=[SEARCH, item(0, b'list')])
    tap_find(p)
    def typed(w, out, n, *_):
        C.memmove(out, b'a b\0', 4)
    fw.mock('widget_get_text_utf8', typed)
    fw.fn('addon__searched', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    assert p.top.op == b'search' and p.top.args == b'\ta b' and p.top.title == b'a b' and p.top.base == 4


def test_an_empty_search_goes_back_to_the_menu(fw):
    p = Page(fw)
    p.answer(rows=[SEARCH, item(0, b'list')])  # a first search row: the title bar's icon
    tap_find(p)
    fw.fn('addon__searched', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    assert p.pg.depth == 1


SECRET = b's3cr%t'
USER = (b'field', b'user', b'Username:', b'alice', b'', b'')
PASSWORD = (b'field', b'login', b'Password:', b'***', b'pw', b'')


def field(p, i, typed=SECRET):
    # A field row pressed, its box's hint its title (read as it is set: edit_create's names are its stack's), and
    # typed's OK.
    box, p.tips = edit_box(p.fw), []
    p.fw.mock('widget_set_prop_str', lambda w, k, v, *_: C.string_at(k) == b'tips' and p.tips.append(C.string_at(v)))
    p.click(i)
    assert p.pg.edit == box and p.pg.field == i + 1
    # stock writes nothing when the text doesn't fit; Done sends EVT_IM_ACTION and EVT_VALUE_CHANGED, so twice
    p.fw.mock('widget_get_text_utf8', lambda w, out, n, *_: 1 if len(typed) >= n else C.memmove(out, typed + b'\0', len(typed) + 1) and 0)
    p.fw.fn('addon__searched', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    p.fw.fn('addon__searched', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    p.fw.fn('addon__go', C.c_int, C.c_void_p)(None)


def test_a_field_row_sends_its_text_and_takes_the_reply_in_the_levels_place(fw):
    p = Page(fw)
    texts: list = []  # read as they are set: a caption points into a reply let go since
    fw.mock('widget_set_text_utf8', lambda w, t, *_: texts.append(C.string_at(t)))
    p.answer(rows=[item(0, b'info'), USER, PASSWORD])
    p.sel = 2
    field(p, 2)
    assert p.tips == [b'Password:']
    assert p.request().endswith(b'\tlogin\tpw\ts3cr%25t\n') and p.pg.act == 0
    assert p.pg.depth == 1 and p.top.op == b'menu' and p.top.args == b'' and p.top.title == b'Foo'
    assert p.top.state == K_LOAD and p.top.id == p.req.id and p.top.sel == 2
    assert p.pg.term == b'' and p.pg.field == 0
    p.answer(rows=[item(0, b'info'), USER, (b'field', b'login', b'Password:', b'*****', b'pw', b'')])
    assert p.top.state == K_READY and p.top.n == 3 and p.cell(2, 3) == b'*****' and p.pg.depth == 1
    assert b'Username:' in texts and not [t for t in texts if SECRET in t]  # nothing on screen holds it


def test_a_field_text_of_63_bytes_is_sent_whole(fw):
    p = Page(fw)
    p.answer(rows=[USER])
    field(p, 0, b'a' * 63)
    assert p.request().endswith(b'\tuser\t' + b'a' * 63 + b'\n')


@pytest.mark.parametrize('n', [64, 100, 255])
def test_a_field_text_too_long_to_send_whole_is_dropped_and_says_so(fw, n):
    p = Page(fw)
    p.answer(rows=[USER])
    calls = len(fw.calls('fw_pthread_create'))
    field(p, 0, b'a' * n)  # searched runs twice, as stock's Done sends both events
    assert len(fw.calls('fw_pthread_create')) == calls and p.toasts == [b'Too long']
    assert p.pg.term == b'' and p.top.state == K_READY and p.pg.depth == 1 and p.top.title == b'Foo'


def test_a_search_text_too_long_to_send_whole_is_dropped_and_says_so(fw):
    p = Page(fw)
    p.answer(rows=[SEARCH, item(0, b'list')])
    tap_find(p)
    calls = len(fw.calls('fw_pthread_create'))
    text = b'a' * 99 + b'\xc3\xa9'
    fw.mock('widget_get_text_utf8', lambda w, out, n, *_: 1 if len(text) >= n else C.memmove(out, text + b'\0', len(text) + 1) and 0)
    for _ in range(2):
        fw.fn('addon__searched', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    assert len(fw.calls('fw_pthread_create')) == calls and p.toasts == [b'Too long']
    assert p.pg.depth == 1 and p.top.op != b'search' and text[:63] not in p.top.title


def test_a_field_sent_while_the_app_restarts_is_its_levels_error_and_retry_asks_the_level(fw):
    p = Page(fw, NOSTOP)
    p.answer(rows=[USER, PASSWORD])
    field(p, 1)
    p.ap.a[0].restart = 1
    p.answer(fail=REQ_NOSOCK, raw=b'')
    assert p.top.state == K_ERROR and p.top.note == b"Foo isn't running" and p.drawn == 2
    p.click(0)
    assert SECRET not in p.request() and p.request().endswith(b'\tmenu\n')


def test_a_level_asked_while_the_app_restarts_still_waits(fw):
    p = Page(fw)
    p.ap.a[0].restart = 1
    p.answer(fail=REQ_NOSOCK, raw=b'')
    assert p.top.state == K_WAIT


def test_a_request_with_no_worker_is_wiped_before_it_is_let_go(fw):
    p = Page(fw)
    fw.mock('fw_pthread_create', lambda *_: 11)  # EAGAIN: no worker
    fw.fn('addon__req_send', None, C.c_char_p, C.c_char_p, C.c_char_p)(b'/s', b'login', b'\ts3cret')
    assert p.req.done == 1 and p.req.running == 0 and C.string_at(p.req.buf, 64) == b'\0' * 64


def test_a_field_row_with_no_context_sends_the_text_alone(fw):
    p = Page(fw)
    p.answer(rows=[USER])
    field(p, 0, b'alice')
    assert p.request().endswith(b'\tuser\talice\n')


def test_a_fields_failure_is_its_levels_error_and_retry_never_sends_the_text_again(fw):
    p = Page(fw, NOSTOP)
    p.answer(rows=[USER, PASSWORD])
    field(p, 1)
    p.answer(status=b'refused', msg=b'Wrong password')
    assert p.top.state == K_ERROR and p.top.note == b'Wrong password' and p.top.title == b'Foo'
    p.click(0)
    assert p.request().endswith(b'\tmenu\n') and SECRET not in p.request()


def test_an_empty_fields_box_sends_nothing(fw):
    p = Page(fw)
    p.answer(rows=[USER])
    calls = len(fw.calls('fw_pthread_create'))
    field(p, 0, b'')
    assert len(fw.calls('fw_pthread_create')) == calls and p.top.state == K_READY


def test_a_field_whose_row_changed_under_the_box_sends_nothing(fw):
    p = Page(fw)
    p.answer(rows=[USER])
    box = edit_box(fw)
    p.click(0)
    p.fw.fn('addon__fetch', None, C.POINTER(Lv))(p.pg.lv)
    p.answer(rows=[item(0)])  # a reply landed while the box was open
    calls = len(fw.calls('fw_pthread_create'))
    fw.mock('widget_get_text_utf8', lambda w, out, n, *_: C.memmove(out, b'x\0', 2) and 0)
    fw.fn('addon__searched', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    assert len(fw.calls('fw_pthread_create')) == calls and p.pg.term == b''


def test_the_worker_keeps_none_of_a_requests_bytes_past_its_reply(fw):
    req = worker(fw, [b'Q2SPOT/1\n1\tok\n'])
    assert C.string_at(req.buf, 17) == b'Q2SPOT/1\n1\tok\n\0\0\0'


def test_a_sole_search_row_stays_a_row(fw):
    p = Page(fw)
    p.answer(rows=[SEARCH])
    assert p.top.n == 1 and not p.top.find
    box = edit_box(fw)
    p.click(0)
    assert p.pg.edit == box and p.pg.view is None


SORT = (b'sort', b'sorts', b'Sort', b'', b'by', b'')


def test_a_sort_row_after_a_taken_search_is_the_title_bar_s_sort_icon(fw):
    p = Page(fw)
    p.answer(rows=[SEARCH, SORT, item(0)])
    assert p.top.find == 1 and p.top.sort == 1 and p.top.n == 1 and p.cell(0, 1) == b'op0'
    assert p.pg.sort[0] == b'sort' and p.pg.sort[1] == b'sorts' and p.pg.sort[4] == b'by'


@pytest.mark.parametrize('rows,find,n', [
    ([SORT, item(0)], 0, 2),          # no search before it
    ([SEARCH, SORT], 1, 1),           # the last row left
    ([SEARCH, item(0), SORT], 1, 2),  # not the row after the search
])
def test_a_sort_row_stays_a_row(fw, rows, find, n):
    p = Page(fw)
    p.answer(rows=rows)
    assert p.top.find == find and not p.top.sort and p.top.n == n


def test_a_sort_row_stays_a_row_without_a_title_bar(fw, settings_read):
    boot(fw, settings_read, b'1')  # Classic: the name is in the status bar
    p = Page(fw)
    p.answer(rows=[SEARCH, SORT, item(0)])
    assert not p.top.find and not p.top.sort and p.top.n == 3 and p.cell(1, 0) == b'sort'


def test_under_classic_a_next_window_selects_past_a_search_and_a_sort_row(fw, settings_read):
    """addon.c page_to: both kept as rows (no title bar), the window's first content row is 3, past them and
    Previous page."""
    boot(fw, settings_read, b'1')
    p = Page(fw)
    p.answer(rows=[SEARCH, SORT, item(0)])
    fw.fn('addon__page_to', None, C.c_void_p, C.c_char_p, C.c_int)(C.addressof(p.top), b'c', 1)
    assert p.top.sel == 3


def test_a_sort_row_left_as_a_row_is_a_list_row(fw):
    """addon.c opens and go: its chevron, and a press opens its uri with its context, titled with its title."""
    p = Page(fw)
    p.answer(rows=[SORT, item(0)])
    assert fw.fn('addon__opens', C.c_int, C.c_int)(0) == 1
    p.click(0)
    assert p.pg.depth == 2 and p.top.op == b'sorts' and p.top.args == b'\tby' and p.top.title == b'Sort'


def tap_sort(p):
    p.fw.fn('addon__bar_tapped', C.c_int, C.c_void_p, C.c_void_p)(G_SORT, None)
    p.fw.fn('addon__go', C.c_int, C.c_void_p)(None)


def test_the_sort_icon_s_tap_opens_its_row_s_level(fw):
    p = Page(fw)
    p.answer(rows=[SEARCH, SORT, item(0)])
    tap_sort(p)
    assert p.pg.depth == 2 and p.top.op == b'sorts' and p.top.args == b'\tby' and p.top.title == b'Sort'
    p.answer(rows=[item(1)])  # a level without one: a tap does nothing
    tap_sort(p)
    assert p.pg.depth == 2


def test_a_sort_tap_on_a_level_that_isn_t_ready_does_nothing(fw):
    p = Page(fw)
    p.answer(rows=[SEARCH, SORT, item(0)])
    p.top.state = K_LOAD
    tap_sort(p)
    assert p.pg.depth == 1


def icons(fw, p, rows):
    """The title bar as find_icon leaves it in a 48 px title: the images' boxes and styles, the title's box and the icons' click codes."""
    C.c_int.from_address(p.view + W_H).value = 48
    made: list[list] = []
    moved: dict = {}
    def image(parent, x, y, w, h, *_):
        made.append([C.c_int(v).value for v in (x, y, w, h)])
        return block(fw, size=4096)
    fw.mock('image_create', image)
    fw.mock('widget_use_style', lambda w, s, *_: made[-1].append(C.string_at(s)) if made else 0)
    fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: moved.__setitem__(w, tuple(C.c_int(v).value for v in (x, y, ww, h))))
    p.answer(rows=rows)
    tapped = C.cast(fw.fn('addon__bar_tapped', C.c_int, C.c_void_p, C.c_void_p), C.c_void_p).value
    clicks = [a[3] for _, a in fw.calls('widget_on') if a[1] == O['EVT_CLICK'] and a[2] == tapped]
    return made, moved[p.view], clicks


def test_the_glass_alone_narrows_the_title_to_end_before_it(fw):
    made, title, clicks = icons(fw, Page(fw), [SEARCH, item(0)])
    # centred on the screen, ending at the glass's box, as before the sort icon
    assert made == [[302, 0, 50, 48, b's_img_search']] and title == (73, 0, 229, 48) and clicks == [G_FIND]


def test_the_sort_icon_is_left_of_the_glass_and_the_title_narrows_before_it(fw):
    made, title, clicks = icons(fw, Page(fw), [SEARCH, SORT, item(0)])
    # the sort style draws from its box's top left: a 50 px box centred on the title's line, as the glass's centring
    # the sort made first: its box overlaps the glass's at 302 to 315, and a tap goes to the last made
    assert made == [[265, -1, 50, 50, b's_img_order_navbar'], [302, 0, 50, 48, b's_img_search']]
    assert clicks == [G_SORT, G_FIND]
    x, _, w, _ = title
    assert title == (95, 0, 185, 48) and x + w / 2 == 375 / 2 and x + w == 280  # centred, to the sort's ink


def test_a_refresh_that_takes_or_leaves_the_sort_row_is_rebuilt(fw):
    p = live(fw, [SEARCH, SORT, *rows_of(0, 2)])
    assert p.top.sort == 1 and p.top.n == 2
    drawn = len(fw.calls('list_item_create'))
    refresh(p, [SEARCH, SORT, item(0), (b'action', b'op1', b'Row 1 moved', b'sub', b'', b'')])
    assert p.top.sort == 1 and len(fw.calls('list_item_create')) == drawn  # in place
    refresh(p, [SEARCH, *rows_of(0, 2)])
    assert not p.top.sort and len(fw.calls('list_item_create')) > drawn


def test_after_a_refresh_written_in_place_the_sort_icon_opens_the_new_reply_s_row(fw):
    """pg.sort points into the shown reply: written in place, that is the new one (the old is freed)."""
    p = live(fw, [SEARCH, SORT, *rows_of(0, 2)])
    drawn = len(fw.calls('list_item_create'))
    refresh(p, [SEARCH, (b'sort', b'sorts', b'Sort', b'Release Date', b'by2', b''), *rows_of(0, 2)])
    assert p.top.sort == 1 and len(fw.calls('list_item_create')) == drawn  # in place
    tap_sort(p)
    assert p.pg.depth == 2 and p.top.op == b'sorts' and p.top.args == b'\tby2'


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


@pytest.mark.parametrize('uri, listed', [(b'/mnt/mmc/a/', 1), (b'/tmp/', 0), (b'/mnt/mmc/../etc/', 0), (b'mnt/mmc/a/', 0)])
def test_a_local_row_opens_only_a_card_path(fw, uri, listed):
    """A folder a server named lists nothing off the card: songs_open (library_lists.c) bounds only the length, so
    the row's uri is checked here as a playlist's lines are."""
    p = Page(fw)
    p.answer(rows=[(b'local', uri, b't', b's', b'', b'')])
    p.click(0)
    assert len(fw.calls('toolsLoadDirectory')) == listed


def test_sections_are_a_first_replys_list_rows_up_to_eight(fw):
    p = Page(fw)
    rows = [(b'info', b'', b'Note', b'', b'', b''), (b'list', b'', b'No op', b'', b'', b''),
            (b'list', b'long', b'Too long', b'', b'x' * 500, b'')] + \
        [(b'list', b'sec%d' % i, b'Section %d' % i, b'', b'a b' if i == 0 else b'', b'') for i in range(10)]
    p.answer(msg=b'sections', rows=rows)
    assert p.pg.nsec == 8 and p.pg.cur == 0 and p.pg.depth == 1
    assert p.top.title == b'Section 0 1/8' and p.request() == b'Q2SPOT/1\n2\tsec0\ta b\n'


def test_a_reply_without_list_rows_is_no_sections(fw):
    p = Page(fw)
    p.answer(msg=b'sections', rows=[item(0)])
    assert p.pg.nsec == 0 and p.top.n == 1 and p.top.title == b'Foo'


def touch(p, kind, x, y):
    e = C.create_string_buffer(0x40)
    for field, v in (('EVENT_TYPE', O[kind]), ('EVENT_X', x), ('EVENT_Y', y)):
        struct.pack_into('<i', e, O[field], v)
    return p.fw.fn('addon__swipe', C.c_int, C.c_void_p, C.c_void_p)(None, e)


def sections(fw, n=3):
    """A page showing the first of n sections, its request in flight; the abort comes back to swipe, as the real
    dispatch's does (the emulator's mock doesn't)."""
    p = Page(fw)
    p.answer(msg=b'sections', rows=[(b'list', b'sec%d' % i, b'S%d' % i, b'', b'', b'') for i in range(n)])
    fw.mock('pointer_event_init', lambda *_: 0)
    def dispatch(*_):
        p.aborts.append((p.pg.sdown, p.pg.sclaim))
        touch(p, 'EVT_POINTER_ABORT', 0, 0)
        p.aborts.append((p.pg.sdown, p.pg.sclaim))
    fw.mock('widget_dispatch_event_to_target_recursive', dispatch)
    return p


def swipe(p, dx, dy=0):
    x, y = 200, 100
    touch(p, 'EVT_POINTER_DOWN', x, y)
    moved = touch(p, 'EVT_POINTER_MOVE_BEFORE', x + dx // 2, y + dy // 2)
    lifted = touch(p, 'EVT_POINTER_UP_BEFORE', x + dx, y + dy)
    p.fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    return moved, lifted


def test_a_claim_survives_its_own_abort_coming_back(fw):
    p = sections(fw)
    touch(p, 'EVT_POINTER_DOWN', 200, 100)
    assert touch(p, 'EVT_POINTER_MOVE_BEFORE', 150, 100) == 11
    # cleared by the abort from swipe's own dispatch, taken once that returns
    assert p.aborts == [(1, 0), (0, 0)] and (p.pg.sdown, p.pg.sclaim) == (1, 1)
    assert touch(p, 'EVT_POINTER_MOVE_BEFORE', 140, 100) == 11 and len(p.aborts) == 2  # once
    assert touch(p, 'EVT_POINTER_UP_BEFORE', 100, 100) == 11
    p.fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    assert p.pg.cur == 1


def test_another_touchs_abort_ends_a_claimed_swipe(fw):
    p = sections(fw)
    touch(p, 'EVT_POINTER_DOWN', 200, 100)
    touch(p, 'EVT_POINTER_MOVE_BEFORE', 150, 100)
    touch(p, 'EVT_POINTER_ABORT', 0, 0)
    assert touch(p, 'EVT_POINTER_UP_BEFORE', 100, 100) == 0 and p.pg.cur == 0


def test_a_swipe_while_the_sections_request_is_in_flight(fw):
    p = sections(fw)
    assert p.req.running and p.request() == b'Q2SPOT/1\n2\tsec0\n'
    assert swipe(p, -100) == (11, 11)
    assert p.pg.cur == 1 and p.top.id == p.req.id and p.request() == b'Q2SPOT/1\n3\tsec1\n' and p.top.state == K_LOAD
    p.answer(rows=[item(1)])
    assert p.top.state == K_READY and p.top.title == b'S1 2/3' and p.cell(0, 2) == b'Row 1'


@pytest.mark.parametrize('dx, dy, cur, why', [
    (-100, 0, 1, 'a swipe'),
    (-58, 0, 0, 'too short at the lift'),
    (-120, 60, 0, 'up or down reaches the claim on the way: the list keeps it'),
    (-120, 40, 1, 'claimed halfway, further sideways at the lift'),
    (-60, 20, 1, 'a claim that ends further sideways'),
])
def test_a_lift_changes_section_only_past_the_distance(fw, dx, dy, cur, why):
    p = sections(fw)
    swipe(p, dx, dy)
    assert p.pg.cur == cur, why


def test_a_claimed_swipe_that_ends_further_down_changes_nothing(fw):
    p = sections(fw)
    touch(p, 'EVT_POINTER_DOWN', 200, 100)
    assert touch(p, 'EVT_POINTER_MOVE_BEFORE', 170, 100) == 11
    assert touch(p, 'EVT_POINTER_UP_BEFORE', 130, 190) == 11
    p.fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    assert p.pg.cur == 0


def test_a_diagonal_move_up_or_down_is_the_lists_however_far_sideways(fw):
    p = sections(fw)
    touch(p, 'EVT_POINTER_DOWN', 200, 100)
    assert touch(p, 'EVT_POINTER_MOVE_BEFORE', 80, 124) == 0 and not p.pg.sdown and not p.aborts


def test_a_section_keeps_its_selection_context_and_title(fw):
    long_ctx, long_name = b'c' * 300, b'N' * 100
    p = Page(fw)
    p.answer(msg=b'sections', rows=[(b'list', b'a', long_name, b'', long_ctx, b''), (b'list', b'b', b'B', b'', b'', b'')])
    assert p.top.title == long_name + b' 1/2' and p.request().endswith(b'\ta\t' + long_ctx + b'\n')
    fw.mock('pointer_event_init', lambda *_: 0)
    fw.mock('widget_dispatch_event_to_target_recursive', lambda *_: 0)
    p.answer(rows=[item(i) for i in range(3)])
    p.sel = 2
    swipe(p, -100)
    p.answer(rows=[item(i) for i in range(5)])
    assert p.pg.cur == 1 and p.top.sel == 0
    p.sel = 4
    swipe(p, 100)
    assert p.pg.cur == 0 and p.top.sel == 2
    p.answer(rows=[item(i) for i in range(3)])
    assert p.top.sel == 2
    p.sel = 2
    swipe(p, -100)
    assert p.top.sel == 4


def test_one_section_has_no_position(fw):
    p = Page(fw)
    p.answer(msg=b'sections', rows=[(b'list', b'a', b'Only', b'', b'', b'')])
    assert p.pg.nsec == 1 and p.top.title == b'Only'


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
        uri = b'/mnt/mmc/x/'  # a folder's page; songs_open itself is library_lists.c's
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


def sized(p):
    """scroll_view_create as stock's: the view as tall as page_list asks (rows_view), each list made counted."""
    lists = []
    def create(parent, x, y, w, h, *_):
        C.c_int.from_address(p.view + W_H).value = h
        lists.append(h)
        p.drawn = 0
        return p.view
    p.fw.mock('scroll_view_create', create)
    return lists


@pytest.mark.parametrize('first,lists', [(2, 2), (ADDON_ROWS, 1)])
def test_a_short_first_page_is_drawn_again_with_the_next(fw, first, lists):
    p = Page(fw)
    made = sized(p)
    p.answer(msg=b'pages', cursor=b'c0', rows=rows_of(0, first))
    assert p.drawn == first + 1  # its rows and Loading…
    p.answer(rows=rows_of(first, 2))
    # a short page's list was shrunk to its rows: made again; a full one's rows are added in place of Loading…
    assert len(made) == lists and p.top.n == first + 2 and p.drawn == (first + 2 if lists == 2 else first + 3)
    assert len(fw.calls('widget_destroy')) == 2 - lists


def key_event(fw, code):
    e = block(fw, size=0x40)
    C.c_int.from_address(e + 0x18).value = code  # EVENT_KEY
    return e


def search_box(fw):
    """A page whose first row is a search, its box open from the title bar's icon, and the rows its level drew."""
    p = Page(fw)
    edit = block(fw, size=4096)
    fw.mock('widget_factory_create_widget', lambda f, kind, *_: edit if C.string_at(kind) == b'edit' else p.view)
    p.answer(rows=[SEARCH, item(1)])
    shown = p.drawn
    fw.fn('addon__bar_tapped', C.c_int, C.c_void_p, C.c_void_p)(G_FIND, None)
    fw.fn('addon__go', C.c_int, C.c_void_p)(None)
    assert p.pg.edit == edit and p.pg.view is None
    return p, edit, shown


def test_leaving_the_search_box_shows_its_level_again(fw):
    p, _, shown = search_box(fw)
    fw.fn('addon__search_left', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    fire(p)
    assert p.pg.view and not p.pg.edit and p.pg.depth == 1 and p.drawn == shown


def test_the_search_boxs_blur_after_its_ok_keeps_the_search(fw):
    p, _, _ = search_box(fw)
    fw.mock('widget_get_text_utf8', lambda w, out, n, *_: C.memmove(out, b'abc\0', 4) and 0)
    fw.fn('addon__searched', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    fw.fn('addon__search_left', C.c_int, C.c_void_p, C.c_void_p)(None, None)  # the keyboard's close blurs it
    fire(p)
    assert p.pg.depth == 2 and p.top.op == b'search' and p.top.args == b'\tabc'


def test_return_in_the_search_box_empties_it_and_stays(fw):
    p, edit, _ = search_box(fw)
    keyup = fw.fn('addon__keyup', C.c_int, C.c_void_p, C.c_void_p)
    timers = p.timers
    assert keyup(None, key_event(fw, CENTRE)) == 0 and p.timers == timers  # stock's
    assert keyup(None, key_event(fw, RETURN)) == 11 and p.timers == timers + 1  # RET_STOP: its leave, next pass
    p.back()
    texts = [C.string_at(a[1]) for _, a in fw.calls('widget_set_text_utf8') if a[0] == edit]
    assert texts[-1] == b'' and (edit, 0) in [a[:2] for _, a in fw.calls('widget_set_focused')]
    assert p.backs == 0 and p.pg.depth == 1


def test_a_remote_row_opens_the_apps_code_over_its_page(fw):
    p = Page(fw)
    p.world.files[b'/tmp/q2-foo.state'] = qr_state()
    p.answer(rows=[(b'remote', b'', b'Phone', b'', b'', b'')])
    code = block(fw, size=4096)
    fw.mock('window_create', lambda *_: code)
    p.click(0)
    qp = fw.var('addon__qp', Qp)
    assert qp.page == code and C.string_at(qp.state) == b'/tmp/q2-foo.state' and C.string_at(qp.hex) == HEX21
    assert p.pg.page == p.page and p.pg.depth == 1 and p.backs == 0


def test_a_page_that_cant_open_leaves_nothing_behind(fw):
    p = Page(fw, opens=False)
    fw.mock('window_create', lambda *_: 0)
    fw.fn('addon_open', C.c_int, C.c_void_p, C.c_void_p)(0, None)
    assert not p.pg.page and not p.pg.lv and not p.pg.f and p.world.of('system') == []  # nor the app started
    fw.mock('window_create', lambda *_: p.page)
    fw.fn('addon_open', C.c_int, C.c_void_p, C.c_void_p)(0, None)
    assert p.pg.page == p.page and p.top.op == b'menu'


@pytest.mark.parametrize('job,stage,running,opens', [
    (JOB_REMOVE, 4, True, False),   # its folder being deleted
    (JOB_INSTALL, 3, True, False),  # an update's files being moved into place
    (JOB_INSTALL, 1, False, False),  # a download for a stopped app: launch() would be refused, a dead page
    (JOB_INSTALL, 1, True, True),   # a download: the old app still runs, its page works
])
def test_a_page_the_app_store_is_replacing_waits_for_it(fw, job, stage, running, opens):
    p = Page(fw, opens=False)
    ap(fw).a[0].launched = int(running)
    table = C.addressof(C.c_char.in_dll(fw.lib, 'shelf_apps'))
    fw.var('shelf_n', C.c_int).value = fw.fn('shelf_sources', C.c_int, C.c_char_p, C.c_void_p, C.c_int)(b'me/foo', table, 1)
    sh = fw.var('netupdate__sh', Sh)
    sh.running, sh.job, sh.app, sh.stage = 1, job, 0, stage
    fw.fn('addon_open', C.c_int, C.c_void_p, C.c_void_p)(0, None)
    if opens:
        assert p.pg.page == p.page and p.top.op == b'menu' and p.toasts == []
    else:
        assert not p.pg.page and not p.pg.lv and p.toasts == [b'Wait until the App Store finishes']
    assert p.world.of('system') == []  # the app isn't started in its folder either way


@pytest.mark.parametrize('manifest,path,logs', [
    (b'name=Foo\nscrobble=0\n', b'/mnt/mmc/.foo/a.flac', 0),
    (b'name=Foo\r\nscrobble=0\r\n', b'/mnt/mmc/.foo/a.flac', 0),  # a Windows editor's line ends
    (b'name=Foo\nscrobble=1\n', b'/mnt/mmc/.foo/a.flac', 1),
    (b'name=Foo\n', b'/mnt/mmc/.foo/a.flac', 1),
    (b'name=Foo\nscrobble=no\n', b'/mnt/mmc/.foo/a.flac', 1),  # as text: only 0 keeps them out
    (b'name=Foo\nscrobble=0\n', b'/mnt/mmc/Music/a.flac', 1),  # outside any app's folder
    (b'name=Foo\nscrobble=0\n', b'/mnt/mmc/.bar/a.flac', 1),   # an app folder with no manifest
    (b'name=Foo\nscrobble=0\n', b'/mnt/mmc/.foo', 1),
])
def test_an_app_keeps_its_folders_songs_out_of_the_scrobble_log(fw, manifest, path, logs):
    card(fw, foo=manifest)
    assert fw.fn('addon_scrobbles', C.c_int, C.c_char_p)(path) == logs


SYNCED = b'/mnt/mmc/Jellyfin/Broadcast/Tender Buttons/01 - Tears.flac'


@pytest.mark.parametrize('manifest,path,told', [
    (b'name=Foo\nfavdir=Jellyfin\n', SYNCED, True),
    (b'name=Foo\r\nfavdir=Jellyfin\r\n', SYNCED, True),  # a Windows editor's line ends
    (b'name=Foo\n', SYNCED, False),  # no favdir: the app hears of no hearts
    (b'name=Foo\nfavdir=Jellyfin\n', b'/mnt/mmc/Music/a.flac', False),
    (b'name=Foo\nfavdir=Jellyfin\n', b'/mnt/mmc/Jellyfin2/a.flac', False),  # a folder that only starts the same
    (b'name=Foo\nfavdir=Jellyfin\n', b'/mnt/mmc/Jellyfin', False),
    (b'name=Foo\nfavdir=Jellyfin\n', b'/mnt/mmc/Jellyfin/../Music/a.flac', False),
    (b'name=Foo\nfavdir=Jellyfin\n', b'/tmp/Jellyfin/a.flac', False),
    (b'name=Foo\nfavdir=Jelly/fin\n', b'/mnt/mmc/Jelly/fin/a.flac', False),  # one folder name, no deeper
    (b'name=Foo\nfavdir=..\n', b'/mnt/mmc/../a.flac', False),
])
def test_a_heart_on_a_song_in_an_apps_favdir_is_told_to_it(fw, manifest, path, told):
    world = card(fw, foo=manifest)
    for on in (1, 0):
        assert fw.fn('addon_fav_path', C.c_int, C.c_char_p, C.c_int)(path, on) == 1
    want = b'\npath\t1\t%s\n\npath\t0\t%s\n' % (path, path)
    assert world.written.get(b'/mnt/mmc/.foo/fav.log') == (want if told else None)


def test_a_heart_is_appended_to_the_presses_the_app_hasnt_taken(fw):
    world = card(fw, foo=b'favdir=Jellyfin\n')
    world.files[b'/mnt/mmc/.foo/fav.log'] = b'item\t1\tabc\n'
    assert fw.fn('addon_fav', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_int)(b'foo', b'item', b'def', 7) == 1
    assert world.written[b'/mnt/mmc/.foo/fav.log'] == b'item\t1\tabc\n\nitem\t1\tdef\n'


def test_a_press_after_a_cut_one_starts_a_line_of_its_own(fw):
    world = card(fw, foo=b'favdir=Jellyfin\n')
    world.files[b'/mnt/mmc/.foo/fav.log'] = b'\nitem\t1\tab'  # a write the card cut short
    fw.fn('addon_fav', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_int)(b'foo', b'path', SYNCED, 0)
    assert world.written[b'/mnt/mmc/.foo/fav.log'].split(b'\n') == [b'', b'item\t1\tab', b'path\t0\t' + SYNCED, b'']


def test_a_heart_the_card_wont_take_says_so(fw):
    world = card(fw, foo=b'favdir=Jellyfin\n')
    world.fail_write = lambda name: True
    assert fw.fn('addon_fav_path', C.c_int, C.c_char_p, C.c_int)(SYNCED, 1) == 0


@pytest.mark.parametrize('app,key', [(b'../x', b'k'), (b'', b'k'), (b'foo', b'k' * 600)])
def test_a_heart_for_no_app_or_a_key_too_long_is_never_written(fw, app, key):
    world = card(fw, foo=b'favdir=Jellyfin\n')
    assert fw.fn('addon_fav', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_int)(app, b'item', key, 1) == 0
    assert not [n for n in world.written if n.endswith(b'fav.log')]


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


def test_go_to_during_the_apps_remove_waits_and_stock_s_go_to_doesnt_run(fw):
    # Go to on a song of an app the App Store is removing toasts and returns 1, so stock's own Go to doesn't run
    p = Page(fw, b'name=Foo\ngoto=1\n', opens=False)
    table = C.addressof(C.c_char.in_dll(fw.lib, 'shelf_apps'))
    fw.var('shelf_n', C.c_int).value = fw.fn('shelf_sources', C.c_int, C.c_char_p, C.c_void_p, C.c_int)(b'me/foo', table, 1)
    sh = fw.var('netupdate__sh', Sh)
    sh.running, sh.job, sh.app, sh.stage = 1, JOB_REMOVE, 0, 4
    assert goto(fw, b'/mnt/mmc/.foo/a.flac') == 1
    assert not p.pg.page and p.toasts == [b'Wait until the App Store finishes']


def test_go_to_artist_with_no_title_is_titled_by_the_app(fw):
    p = Page(fw, b'name=Foo\ngoto=1\n', opens=False)
    assert goto(fw, b'/mnt/mmc/.foo/a.flac', b'', 0) == 1
    assert p.top.args.startswith(b'\tartist\t') and p.top.title == b'Foo'


def test_go_to_replaces_the_page_already_open(fw):
    p = Page(fw, b'name=Foo\ngoto=1\n')
    p.answer(rows=[item(1)])
    assert goto(fw, b'/mnt/mmc/.foo/a.flac') == 1
    assert p.pg.depth == 1 and p.top.op == b'goto' and len(fw.calls('window_manager_close_window_force')) == 1


class Qa(C.Structure):
    _fields_ = [('app', C.c_void_p), ('loads', C.c_uint)]


def more(fw, manifest, loads=0):
    # An app's queue playing (its .play's start noted, loads queue loads since), and Now Playing's ⋯.
    p = Page(fw, manifest, opens=False)
    qa = fw.var('addon__qa', Qa)
    qa.app, qa.loads = C.addressof(p.ap.a[0]), fw.var('playback_loads', C.c_uint).value
    fw.var('playback_loads', C.c_uint).value += loads
    return p, fw.fn('addon_more', C.c_int)()


def test_now_playings_more_opens_the_apps_page_at_its_op(fw):
    p, opened = more(fw, b'name=Foo\nmore=playlists My Playlists\n')
    assert opened == 1 and p.pg.depth == 1 and p.top.op == b'playlists' and p.top.args == b'' and p.top.title == b'My Playlists'
    assert p.request().endswith(b'\tplaylists\n')


def test_more_with_no_title_is_titled_by_the_app(fw):
    p, opened = more(fw, b'name=Foo\nmore=playlists\n')
    assert opened == 1 and p.top.title == b'Foo'


def test_more_is_stocks_once_the_apps_binary_is_gone(fw):
    p = Page(fw, b'name=Foo\nmore=playlists\n', opens=False)
    qa = fw.var('addon__qa', Qa)
    qa.app, qa.loads = C.addressof(p.ap.a[0]), fw.var('playback_loads', C.c_uint).value
    del p.world.files[b'/mnt/mmc/.foo/q2-foo']
    assert fw.fn('addon_more', C.c_int)() == 0 and not p.pg.page


@pytest.mark.parametrize('manifest,loads', [
    (b'name=Foo\n', 0),                                 # no more=: stock's ⋯
    (b'name=Foo\nmore=playlists\n', 1),                 # another queue loaded since
    (b'name=Foo\nmore= Playlists\n', 0),                # no op
    (b'name=Foo\nmore=' + b'p' * 16 + b' P\n', 0),       # an op too long for a level
])
def test_more_is_stocks_unless_the_app_says_and_its_queue_plays(fw, manifest, loads):
    p, opened = more(fw, manifest, loads)
    assert opened == 0 and not p.pg.page


def test_more_with_no_app_queue_is_stocks(fw):
    Page(fw, b'name=Foo\nmore=playlists\n', opens=False)
    assert fw.fn('addon_more', C.c_int)() == 0


def test_more_replaces_the_apps_page_already_open(fw):
    p, _ = more(fw, b'name=Foo\nmore=playlists\n')
    p.answer(rows=[item(1, b'list')])
    p.click(0)
    assert fw.fn('addon_more', C.c_int)() == 1
    assert p.pg.depth == 1 and p.top.op == b'playlists' and len(fw.calls('window_manager_close_window_force')) == 1


@pytest.mark.parametrize('manifest,stock', [(b'name=Foo\nmore=playlists\n', 0), (b'name=Foo\n', 1)])
def test_now_playings_more_tapped_opens_the_apps_page_instead_of_stocks(fw, scratch, manifest, stock):
    # navigation.c ringnav_dispatch: the click on Now Playing's img_more (Page's lookup finds it at the view).
    p = Page(fw, manifest, opens=False)
    qa = fw.var('addon__qa', Qa)
    qa.app, qa.loads = C.addressof(p.ap.a[0]), fw.var('playback_loads', C.c_uint).value
    C.c_void_p.from_address(C.addressof(fw.var('st', C.c_char)) + scratch['np_win']).value = block(fw, size=4096)
    fw.mock('stock_dispatch_trampoline', lambda *_: 4)
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, O['EVENT_TYPE'], O['EVT_CLICK'])
    fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)(p.view, C.addressof(event))
    assert (p.pg.page == p.page) == (not stock) and len(fw.calls('stock_dispatch_trampoline')) == stock


def test_a_url_queue_marks_its_queue_the_apps(fw, mcl, songs):
    s = Stream(fw, mcl, songs)
    s.world.files[b'/tmp/q2-foo.urls'] = b'#Q2URLS/1\tstart=0\nhttps://x/a.flac\tA\n'
    s.play(b'1 /tmp/q2-foo.urls\n')
    loads = s.loaded.value
    # toolsFreeStSongInfo at its stock address (tidal.c's define): a ret there for the test, put back after
    free_rec = int(re.findall(r'toolsFreeStSongInfo \(\(void \(\*\)\(void \*\)\)(0x[0-9a-f]+)u\)',
                              (host.ROOT/'patch/tidal.c').read_text())[0], 16)
    claim(free_rec)
    saved = C.string_at(free_rec, 1)
    C.memmove(free_rec, b'\xc3', 1)
    try:
        s.step()
        s.step()
    finally:
        C.memmove(free_rec, saved, 1)
    qa = fw.var('addon__qa', Qa)
    assert s.loaded.value == loads + 1 and qa.app == C.addressof(ap(fw).a[0]) and qa.loads == s.loaded.value


def test_a_stream_session_marks_its_queue_the_apps(fw, mcl, songs):
    s = started(fw, mcl, songs)
    qa = fw.var('addon__qa', Qa)
    assert qa.app == C.addressof(ap(fw).a[0]) and qa.loads == s.loaded.value


def test_a_playlist_session_marks_its_queue_the_apps(fw, mcl, songs):
    s = playlist(fw, mcl, songs, A)
    s.land(A)
    s.play(b'1 ' + PLAYLIST + b'\n')
    s.step()
    qa = fw.var('addon__qa', Qa)
    assert s.loads and qa.app == C.addressof(ap(fw).a[0]) and qa.loads == s.loaded.value


def test_a_url_queue_the_app_cannot_play_marks_nothing(fw, mcl, songs):
    s = Stream(fw, mcl, songs)
    s.world.files[b'/tmp/q2-foo.urls'] = b'#Q2URLS/1\tstart=0\n'  # no songs: app_urls_play plays nothing
    s.play(b'1 /tmp/q2-foo.urls\n')
    s.step()
    s.step()
    assert not fw.var('addon__qa', Qa).app


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
        self.fw, self.texts, self.shown, self.widths = fw, [], {}, {}
        self.world = World(fw, {} if raw is None else {b'/tmp/q2-a.state': raw})
        self.ids = [100]
        def widget(*_):
            self.ids[0] += 8
            return self.ids[0]
        def view(factory, kind, parent, x, y, w, *_):  # its height is a seventh argument, past a stub's six
            made = widget()
            self.widths[made] = w
            return made
        fw.mock('window_create', lambda *_: 99)
        fw.mock('widget_factory_create_widget', view)
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


@pytest.mark.parametrize('state', [
    qr_state(size=49, code=b'f' * (13 * 49)).replace(b'hold=1\n', b'') + b'hold=1\n',  # after the longest qr=
    b'note=' + b'n' * 120 + b'\nhold=1\n',                                              # after a long note=
])
def test_hold_anywhere_in_the_status_file_holds(fw, state):
    world, a = running(fw, state)
    addon_poll(fw, world)
    assert a.hold == 1 and [c[1][:3] for c in fw.calls('reset_poweroptions_timer')] == [(1, 1, 0)]


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


def test_the_code_is_read_again_only_while_the_screen_is_lit(fw):
    q = Qr(fw, qr_state(pin=b'111111'))
    tick = fw.fn('addon__qr_tick', C.c_int, C.c_void_p)
    q.world.files[b'/tmp/q2-a.state'] = qr_state(pin=b'222222')
    lit = fw.var('g_backlight_status', C.c_ubyte)
    lit.value = 0
    assert tick(None) == 8 and b'222 222' not in q.texts  # RET_REPEAT
    lit.value = 1
    assert tick(None) == 8 and b'222 222' in q.texts


def test_return_on_the_code_goes_back(fw):
    q = Qr(fw, qr_state())
    timers = []
    fw.mock('timer_add', lambda fn, *_: timers.append(fn) or len(timers))
    keyup = fw.fn('addon__qr_keyup', C.c_int, C.c_void_p, C.c_void_p)
    assert keyup(None, key_event(fw, CENTRE)) == 0 and timers == []
    assert keyup(None, key_event(fw, RETURN)) == 11 and len(timers) == 1  # RET_STOP: its leave, next pass
    assert not fw.calls('navigator_back')
    C.CFUNCTYPE(C.c_int, C.c_void_p)(timers[0])(None)
    assert len(fw.calls('navigator_back')) == 1 and q.qp.leave == 0


def test_a_code_page_that_cant_open_keeps_nothing(fw):
    World(fw)
    fw.mock('window_create', lambda *_: 0)
    fw.fn('addon__qr_open', None, C.c_char_p, C.c_char_p)(b'/tmp/q2-a.state', b'Title')
    qp = fw.var('addon__qp', Qp)
    assert not qp.page and not qp.hex and not qp.timer


@pytest.fixture(scope='module')
def settings_read(library):
    """navigation.c's st.settings_read in this build (test_navigation.py's scratch): theme() reads config.ini once."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')['settings_read']


def boot(fw, settings_read, theme):
    """theme() as a boot under theme has it: config.ini read again at its next use."""
    settings(fw, THEME=theme)
    C.c_int.from_address(C.addressof(fw.var('st', C.c_char)) + settings_read).value = 0


def drawn(fw, paint, rounds=True):
    """What paint(canvas) draws: the rounded plates it asks for, (x, y, w, h, colour), and its fills, (x, y, w, h,
    the colour set then). A canvas that can't round (rounds False) leaves it to fill square ones."""
    lcd, canvas = block(fw, size=4096), block(fw, size=4096)
    C.c_void_p.from_address(canvas + CANVAS_LCD).value = lcd
    plates, fills, ink = [], [], [0]
    def rounded(c, r, g, colour, radius, *_):
        if rounds:
            plates.append((*C.cast(r, C.POINTER(C.c_int))[:4], C.c_uint.from_address(colour).value))
        return 0 if rounds else 1
    fw.mock('canvas_fill_rounded_rect', rounded)
    fw.mock('canvas_set_fill_color', lambda c, colour, *_: ink.__setitem__(0, colour & 0xffffffff))
    fw.mock('canvas_fill_rect', lambda c, *box: fills.append((*(C.c_int(v).value for v in box[:4]), ink[0])))  # ints
    paint(canvas)
    return plates, fills


def paint_of(fw, widget):
    return lambda canvas: fw.fn('addon_paint', None, C.c_void_p, C.c_void_p)(widget, canvas)


def code_in(plate, fills, n=21):
    """A code whose every module is dark (HEX21's), each row one rectangle, centred in the plate drawn inside a
    quiet zone of two modules, its modules as big as that leaves room for; their side."""
    x, y, side, high = plate[:4]
    m = fills[0][3]
    left = fills[0][0] - x
    right = side - left - n * m
    assert side == high and [f[:4] for f in fills] == [(x + left, y + left + r * m, n * m, m) for r in range(n)]
    assert abs(left - right) <= 1 and min(left, right) >= 2 * m and (n + 4) * (m + 1) > side
    return m


def test_classics_code_is_smaller_on_a_shadowed_plate_its_name_the_bars(fw, settings_read):
    looks = []
    for theme in (b'0', b'1'):  # Minimal, then Classic
        boot(fw, settings_read, theme)
        named = len(fw.calls('widget_set_name'))
        q = Qr(fw, qr_state())
        plates, fills = drawn(fw, paint_of(fw, q.qp.qr))
        looks.append(dict(titled=b'_page_title' in [C.string_at(a[1]) for _, a in fw.calls('widget_set_name')[named:]],
                          plates=plates, module=code_in(plates[-1], fills), view=q.widths[q.qp.qr]))
        assert b'Title' in q.texts and plates[-1][2] == q.widths[q.qp.qr]  # the plate is the code's whole view
        fw.fn('addon__qr_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    minimal, classic = looks
    assert not minimal['titled'] and len(minimal['plates']) == 1
    assert classic['titled'] and len(classic['plates']) == 2  # its title the bar's; a shadow first, under the plate
    (sx, sy, sw, sh, shade), (px, py, pw, ph, white) = classic['plates']
    assert sx < px and sy < py and sx + sw > px + pw and sy + sh > py + ph and shade >> 24 < white >> 24
    assert classic['view'] < minimal['view'] and classic['module'] < minimal['module']


def test_classics_progress_bar_is_its_scrubbers_rows(fw, settings_read):
    height = fw.fn('addon__progress_h', C.c_int)
    boot(fw, settings_read, b'0')
    strip = height()  # Minimal's flat strip
    boot(fw, settings_read, b'1')  # Classic, whose scrubber is rows
    gloss = height()
    Page(fw)
    fw.mock('widget_get_prop_int', lambda w, name, default, *_: 250 if C.string_at(name) == b'_pml' else default)
    bars = []
    for h in (strip, gloss):
        bar = block(fw, size=4096)
        C.c_int.from_address(bar + W_W).value, C.c_int.from_address(bar + W_H).value = 300, h
        bars.append(drawn(fw, paint_of(fw, bar)))
    rows = fw.fn('paint_rows_bar', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int)
    scrubber = drawn(fw, lambda canvas: rows(canvas, 0, 0, 300, gloss, 300 * 250 // 1000))
    # Minimal's strip isn't Classic's bar; Classic's is the scrubber's rows, the played quarter of its width over them
    assert strip != gloss and bars == [([], []), scrubber] and scrubber[1]


@pytest.mark.parametrize('theme', [b'0', b'1'])  # Minimal, Classic
def test_a_canvas_that_cant_round_gets_square_plates(fw, theme):
    settings(fw, THEME=theme)
    q = Qr(fw, qr_state())
    plates, code = drawn(fw, paint_of(fw, q.qp.qr))
    # each plate filled square where it would have been rounded, in its colour, under the same code
    assert plates and drawn(fw, paint_of(fw, q.qp.qr), rounds=False) == ([], plates + code)


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


class Library:
    """A card app's library request run by addon_poll's passes: its file in /tmp; the windows open (the window
    manager's children, the status bar among them); stock's scan dialog, whose own loop polls while the scan
    runs; the database's playlists, which Playlists' list (p_deque_playlist) holds only once Library → Playlists
    has loaded it (load_localclass_list 0xf00b), empty before; and stock's Import Playlists, whose listing of
    LIB_DIR is addon_m3u_dir's, which replaces or creates a playlist named after each file it imports, stopping
    at a create that fails, and reloads the list."""
    def __init__(self, fw, *exports, playlists=(), windows=(b'home_page',), **apps):
        self.fw, self.keep = fw, []
        self.world = card(fw, **(apps or {'foo': b'name=Foo\n'}))
        self.world.files[b'/tmp/mmc_add'] = b''  # the card is in
        self.exports = [e.encode() for e in exports]
        for name in self.exports:
            self.world.files[LIB_DIR.encode() + name] = b''
        scan(fw)
        self.deques = Deques(fw)
        self.stored = list(playlists)
        self.playlists, self.shown = self.deques.new(), self.deques.new()
        fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = self.playlists
        fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = self.shown
        fw.mock('stock_localclass_trampoline', self.load)
        self.bar = text(b'status_bar')
        fw.var('fw_ctx_system_bar', C.c_void_p).value = C.addressof(self.bar)
        self.show(*windows)
        fw.mock('widget_count_children', lambda *_: len(self.open))
        fw.mock('widget_get_child', lambda wm, i, *_: C.addressof(self.open[i]))
        fw.mock('widget_get_prop_str', lambda w, key, default, *_: w)  # a window here is its name
        self.scans, self.toasts, self.imports, self.scanning = 0, [], [], False
        self.dialog, self.lingers, self.result, self.refused = True, False, 0, set()
        fw.mock('navigator_to_with_context', self.navigate)
        fw.mock('navigator_window_is_exist', lambda name, *_: self.scanning and C.string_at(name) == b'updatemusic_dialog')
        fw.mock('toolsGetDirectoryM3u8', self.listing)
        fw.mock('importPlayList', self.import_)

    def record(self, offset, value):
        chars, r = C.create_string_buffer(value), C.create_string_buffer(0x60)
        struct.pack_into('<Q', r, offset, C.addressof(chars))
        self.keep += [chars, r]
        return C.addressof(r)

    def reload(self):
        self.deques[self.playlists][:] = [self.record(REC_NAME, name) for name in self.stored]

    def load(self, cls, *_):
        """Stock's load_localclass_list: Playlists' (0xf00b) reads the database's into p_deque_playlist and
        shows them."""
        if cls != 0xf00b:
            return 0
        self.reload()
        self.deques[self.shown][:] = self.deques[self.playlists]
        return len(self.stored)

    def show(self, *windows):
        self.open = [self.bar, *(text(w) for w in windows)]

    def navigate(self, name, ctx, *_):
        name = C.string_at(name)
        if name == b'dialog/updatemusic_dialog':  # its own loop until the scan is over
            self.scans += 1
            self.scanning = self.dialog
            self.poll()
            self.poll()
            self.scanning = self.dialog and self.lingers
        elif name == b'dialog/msginfo_dialog':  # a toast, whose dialog runs a loop of its own too
            self.toasts.append(C.string_at(ctx + 8))
            self.poll()

    def listing(self, folder, dq, *_):
        assert C.string_at(folder) == LIB_DIR.encode()
        self.deques[dq][:] = [self.record(REC_PATH, LIB_DIR.encode() + e) for e in self.exports]
        return len(self.exports)

    def import_(self, *_):
        listed = self.deques.new()
        n = self.fw.fn('addon_m3u_dir', C.c_int, C.c_char_p, C.c_void_p)(LIB_DIR.encode(), listed)
        names = [string_at(r, REC_PATH)[len(LIB_DIR):] for r in self.deques[listed]]
        assert n == len(names)
        self.imports.append(names)
        for name in names:
            if name in self.refused:  # a failed create stops stock, which returns as if done
                break
            self.stored = [s for s in self.stored if s != name[:-4]] + [name[:-4]]  # a same-named one replaced
        self.reload()
        return self.result

    def request(self, raw, app=b'foo'):
        self.world.files[b'/tmp/q2-%s.library' % app] = raw

    def answer(self, app=b'foo'):
        return self.world.files.get(b'/tmp/q2-%s.library.done' % app)

    def poll(self):
        addon_poll(self.fw, self.world)


def test_an_apps_request_scans_then_imports_what_it_names_and_answers(fw):
    lib = Library(fw, 'a.m3u', 'b.m3u', 'c.m3u', 'mine.m3u', playlists=[b'c'])
    lib.request(f'7\nscan\nimport {LIB_DIR}a.m3u\nadd {LIB_DIR}b.m3u\nadd {LIB_DIR}c.m3u\n'.encode())
    lib.poll()
    assert lib.scans == 1 and lib.imports == [] and lib.answer() is None
    lib.poll()
    # what it named, but not an add whose playlist is there already, nor the user's own export beside them
    assert lib.imports == [[b'a.m3u', b'b.m3u']] and not libup(fw).only
    assert lib.answer() == f'7 ok\nexists {LIB_DIR}c.m3u\n'.encode() and lib.toasts == [b'Playlists updated']
    lib.poll()
    assert lib.scans == 1 and len(lib.imports) == 1 and libup(fw).state == L_IDLE  # done once


def test_playlists_are_loaded_before_the_adds_are_looked_up(fw):
    lib = Library(fw, 'b.m3u', playlists=[b'b'])  # in the database, but Playlists not opened since the boot
    assert lib.deques[lib.playlists] == []
    lib.request(f'7\nadd {LIB_DIR}b.m3u\n'.encode())
    lib.poll()
    assert lib.imports == [] and lib.answer() == f'7 ok\nexists {LIB_DIR}b.m3u\n'.encode()


@pytest.mark.parametrize('window', [b'localmusic_page', b'unknown_page', b''])
def test_a_window_that_may_hold_library_records_holds_the_request(fw, window):
    lib = Library(fw, 'a.m3u', windows=(b'home_page', window, b'playing_page'))
    lib.request(f'7\nscan\nimport {LIB_DIR}a.m3u\n'.encode())
    lib.poll()
    lib.poll()
    assert lib.scans == 0 and lib.answer() is None and libup(fw).state == L_WAIT
    lib.show(b'home_page', b'playing_page', b'addon_page', b'qrcode_page', b'equalizer_page')
    lib.poll()
    lib.poll()
    assert lib.scans == 1 and lib.answer() == b'7 ok\n'


def test_an_import_waits_for_a_window_too(fw):
    lib = Library(fw, 'a.m3u', windows=(b'localmusic_page',))
    lib.request(f'7\nimport {LIB_DIR}a.m3u\n'.encode())
    lib.poll()
    assert libup(fw).state == L_IMPORT and lib.imports == []
    lib.show(b'stream_page')
    lib.poll()
    assert lib.imports == [[b'a.m3u']]


@pytest.mark.parametrize('status,lit,airplay,receiver,holds', [
    (2, 0, 0, 0, 1),  # the player playing
    (3, 1, 0, 0, 1),  # paused under a lit screen: the scan's dialog would stop it, losing its place
    (3, 0, 0, 0, 0),
    (1, 1, 0, 0, 0),  # stopped
    (1, 0, 2, 0, 1),  # AirPlay
    (1, 0, 0, 2, 1),  # the Bluetooth receiver
])
def test_what_plays_holds_the_scan(fw, status, lit, airplay, receiver, holds):
    lib = Library(fw)
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.mock('airplayGetFlag', lambda *_: airplay)
    fw.var('g_backlight_status', C.c_ubyte).value = lit
    fw.var('bt__recv_pageflag', C.c_ubyte).value = receiver
    lib.request(b'7\nscan\n')
    lib.poll()
    assert lib.scans == 1 - holds and libup(fw).state == (L_WAIT if holds else L_SCAN)


def test_a_paused_song_under_a_lit_screen_lets_an_import_run(fw):
    lib = Library(fw, 'a.m3u')
    fw.mock('mclGetPlayStatus', lambda *_: 3)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    lib.request(f'7\nimport {LIB_DIR}a.m3u\n'.encode())
    lib.poll()
    assert lib.imports == [[b'a.m3u']]


def test_the_import_waits_for_the_scan_to_be_over(fw):
    lib = Library(fw, 'a.m3u')
    lib.lingers = True  # its thread still running once the dialog is gone
    lib.request(f'7\nscan\nimport {LIB_DIR}a.m3u\n'.encode())
    lib.poll()
    lib.poll()
    assert lib.imports == [] and libup(fw).state == L_SCAN
    lib.scanning = False
    lib.poll()
    assert lib.imports == [[b'a.m3u']] and lib.answer() == b'7 ok\n'


def test_a_scan_alone_is_answered_once_it_is_over(fw):
    lib = Library(fw)
    lib.request(b'7\nscan\n')
    lib.poll()
    lib.poll()
    assert lib.answer() == b'7 ok\n' and lib.imports == [] and lib.toasts == []


def test_a_scan_never_seen_is_an_error_not_an_ok(fw):
    lib = Library(fw)
    lib.dialog = False  # its dialog never opened
    lib.request(b'7\nscan\n')
    lib.poll()
    lib.poll()
    assert lib.scans == 1 and lib.answer() == b"7 error Couldn't start the scan\n"


def test_a_playlist_stock_didnt_create_is_the_answer(fw):
    lib = Library(fw, 'a.m3u', 'big one.m3u')
    lib.refused.add(b'big one.m3u')  # the 100 playlists' cap, say
    lib.request(f'7\nimport {LIB_DIR}a.m3u\nimport {LIB_DIR}big one.m3u\n'.encode())
    lib.poll()
    assert lib.answer() == b"7 error Couldn't import big one\n" and lib.toasts == [b"Couldn't import big one"]


def test_the_card_gone_during_the_import_is_the_answer(fw):
    lib = Library(fw, 'a.m3u')
    lib.result = -2
    lib.request(f'7\nimport {LIB_DIR}a.m3u\n'.encode())
    lib.poll()
    assert lib.answer() == b'7 error No memory card\n' and lib.toasts == [b'No memory card']


def test_adds_whose_playlists_are_there_import_nothing(fw):
    lib = Library(fw, 'c.m3u', 'd.m3u', playlists=[b'c', b'd'])
    lib.request(f'7\nadd {LIB_DIR}c.m3u\nadd {LIB_DIR}d.m3u\n'.encode())
    lib.poll()
    assert lib.imports == [] and lib.toasts == []
    assert lib.answer() == f'7 ok\nexists {LIB_DIR}c.m3u\nexists {LIB_DIR}d.m3u\n'.encode()


@pytest.mark.parametrize('there', [b'c.m3u', b'c.m', b'cc', b'', b'd'])
def test_an_add_is_left_alone_only_for_a_playlist_of_its_whole_name(fw, there):
    lib = Library(fw, 'c.m3u', playlists=[there])
    lib.request(f'7\nadd {LIB_DIR}c.m3u\n'.encode())
    lib.poll()
    assert lib.imports == [[b'c.m3u']]


def test_requests_are_looked_at_every_half_second(fw):
    lib = Library(fw)
    poll = fw.fn('addon_poll', None)
    lib.request(b'7\n')
    poll()
    assert lib.answer() == b'7 ok\n'
    lib.request(b'8\n')
    lib.world.ms += 499
    poll()
    assert lib.answer() == b'7 ok\n'
    lib.world.ms += 1
    poll()
    assert lib.answer() == b'8 ok\n'


def test_one_request_at_a_time_and_none_from_a_dialogs_own_loop(fw):
    lib = Library(fw, 'a.m3u', foo=b'name=Alpha\n', bar=b'name=Beta\n')
    lib.request(f'7\nscan\nimport {LIB_DIR}a.m3u\n'.encode(), b'foo')
    lib.request(b'5\n', b'bar')
    lib.poll()
    assert lib.scans == 1 and lib.answer(b'bar') is None  # its scan dialog's polls took nothing
    lib.poll()
    # foo's answered before its toast, whose own loop polled with no request running: that took none either
    assert lib.answer(b'foo') == b'7 ok\n' and lib.toasts == [b'Playlists updated'] and lib.answer(b'bar') is None
    lib.poll()
    assert lib.answer(b'bar') == b'5 ok\n' and lib.imports == [[b'a.m3u']]


def test_stocks_own_import_row_still_gets_the_whole_folder(fw):
    lib = Library(fw, 'a.m3u', 'mine.m3u')
    listed = lib.deques.new()
    assert fw.fn('addon_m3u_dir', C.c_int, C.c_char_p, C.c_void_p)(LIB_DIR.encode(), listed) == 2
    assert [string_at(r, REC_PATH) for r in lib.deques[listed]] == [f'{LIB_DIR}a.m3u'.encode(), f'{LIB_DIR}mine.m3u'.encode()]


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



def shortcut(fw, value):
    """IPOD SHORTCUT in config.ini, every other key its default; Home's shortcut row's click."""
    def read(path, section, key, out, default, *_):
        v = value if C.string_at(key) == b'SHORTCUT' else C.string_at(default)
        C.memmove(out, v + b'\0', len(v) + 1)
    fw.mock('toolsReadConfig', read)
    return fw.fn('home_shortcut_open', C.c_int, C.c_void_p, C.c_void_p)


def toasts(fw):
    out = []
    fw.mock('navigator_to_with_context', lambda page, info, *_: out.append(C.string_at(info + 8)))  # toast's dialog
    return out


def test_the_groovebox_shortcut_starts_the_card_game_with_the_sound(fw):
    card(fw, groovebox=b'name=Groovebox\ngame=1\naudio=1\n')
    fw.mock('fw_fork', lambda *_: 77)
    fw.mock('fw_socket', lambda *_: 9)
    seen = toasts(fw)
    assert shortcut(fw, b'10')(None, None) == 0 and seen == []
    gm = fw.var('addon__gm', Game)
    assert (gm.pid, gm.audio, gm.sock, gm.path) == (77, 1, 9, b'/tmp/q2-groovebox.input.sock')
    assert fw.calls('player_stop') and fw.calls('mclSetMute') and fw.calls('device_set_volume')  # the jack readied, dac_take


@pytest.mark.parametrize('folder, said', [({}, b'Groovebox is not on the card'),
                                          ({'groovebox': b'name=Groovebox\n'}, b"Groovebox can't start from the card")])
def test_the_groovebox_shortcut_says_when_no_card_game_is_it(fw, folder, said):
    """No .groovebox, or one whose q2app isn't a game's: the binary is there, the row shows, the tap says so."""
    card(fw, other=b'name=Other\ngame=1\n', **folder)
    fw.mock('fw_fork', lambda *_: 77)
    seen = toasts(fw)
    assert shortcut(fw, b'10')(None, None) == 0
    assert seen == [said] and not fw.calls('fw_fork')
    opened = fw.fn('addon_game_open', C.c_int, C.c_char_p)
    assert opened(b'groovebox') == -1 and not fw.calls('fw_fork')
    assert opened(b'other') == 1 and fw.calls('fw_fork') and not fw.calls('player_stop')  # a game without the sound


def test_a_card_game_that_can_t_fork_says_so(fw):
    card(fw, groovebox=b'name=Groovebox\ngame=1\naudio=1\n')
    fw.mock('fw_fork', lambda *_: -1)
    seen = toasts(fw)
    assert shortcut(fw, b'10')(None, None) == 0
    assert seen == [b"It couldn't start"] and fw.var('addon__gm', Game).pid == 0 and fw.calls('player_stop')


@pytest.mark.parametrize('status, stops', [(3, 1), (1, 0)])
def test_the_game_with_the_sound_ending_clears_a_pause_stuck_on_nothing(fw, status, stops):
    """A jack or USB change under the game ran stock's player_save_start(1): the start failed, the pause stuck."""
    card(fw)
    gm = fw.var('addon__gm', Game)
    gm.pid, gm.audio = 77, 1
    fw.mock('fw_waitpid', lambda *_: 77)
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.fn('addon_poll', None)()
    assert gm.pid == 0 and gm.audio == 0 and len(fw.calls('player_stop')) == stops


@pytest.mark.parametrize('how', ['poll', 'timer'])
def test_a_url_queue_waiting_leaves_a_queue_loaded_since(fw, how):
    world = World(fw, {b'/tmp/q2-a.play': b'1 /tmp/q2-a.urls\n', b'/tmp/q2-a.urls': b'#Q2URLS/1\n'}, now=500)
    fw.mock('timer_add', lambda *_: 5)
    app = block(fw, size=512)  # an app_t, its id first
    C.memmove(app, b'a\0', 2)
    poll = fw.fn('addon__stream_poll', None, C.c_void_p)
    poll(app)
    s = sm(fw)
    assert s.state == S_WAIT and s.urls and s.timer == 5
    fw.var('playback_loads', C.c_uint).value += 1
    if how == 'poll':
        poll(app)
    else:
        fw.fn('addon__urls_start', C.c_int, C.c_void_p)(None)
    assert s.state == S_DONE and b'/tmp/q2-a.urls' not in world.names.values()


# Now playing (.now) for a URL queue that asks.

class Nw(C.Structure):
    _fields_ = [('owner', C.c_int), ('seq', C.c_char * 24), *((n, C.c_int) for n in ('live', 'n', 'state', 'sec', 'total')),
                ('stopped', C.c_uint), ('polled', C.c_uint), ('swept', C.c_int)]


PLAYING, PAUSED, STOPPED = 2, 3, 1  # mclGetPlayStatus


class Now:
    """A card app's URL queue playing: tidal.c's au (its generation and now=1) and stock's queue of its app:<gen>:<n>
    rows (MCL_TYPE 0xf1, MCL_POS), the player's state and position, and addon_poll's passes, the apps not running."""
    def __init__(self, fw, mcl, rows=('app:7:0', 'app:7:1', 'app:7:2')):
        self.fw, self.mcl, self.world = fw, mcl, World(fw)
        for i, name in enumerate((b'foo', b'bar')):
            ap(fw).a[i].id = name
        ap(fw).n = 2
        self.queue = Queue(fw)
        for r in rows:
            self.queue.row(r)
        self.au = fw.var('tidal__au', Au)
        self.au.gen, self.au.n, self.au.now = 7, len(rows), 1
        mcl.type, mcl.pos = 0xf1, 0
        self.status, self.sec, self.total = PLAYING, 0, 200
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('player_playtime_and_length', self.times)

    def times(self, sec, total, *_):
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.sec, self.total

    def start(self, slot=0, seq=b'4'):
        """urls_start's: the app's queue started, its .play's seq."""
        sm(self.fw).seq = seq
        self.fw.fn('addon__now_start', None, C.POINTER(App))(C.pointer(ap(self.fw).a[slot]))

    def step(self, sec=None, status=None, pos=None):
        """A UI pass ADDON_POLL_MS on."""
        self.sec = self.sec if sec is None else sec
        self.status = self.status if status is None else status
        self.mcl.pos = self.mcl.pos if pos is None else pos
        self.world.ms += 500
        self.fw.fn('addon_poll', None)()

    def now(self, app=b'foo'):
        return self.world.files.get(b'/tmp/q2-%s.now' % app)

    def writes(self):
        return [b for a, b in self.world.of('rename') if b.endswith(b'.now')]


def test_a_url_queue_tells_its_app_which_song_plays_and_how_far_in(fw, mcl):
    n = Now(fw, mcl)
    n.start()
    n.step(sec=10, pos=1)
    assert n.now() == b'4\t1\tplaying\t10\t200\n' and (b'/tmp/q2-now.tmp', b'/tmp/q2-foo.now') in n.world.of('rename')
    n.step(sec=14)
    assert n.now() == b'4\t1\tplaying\t10\t200\n' and len(n.writes()) == 1  # under NOW_STEP_S on: not written
    n.step(sec=15)
    assert n.now() == b'4\t1\tplaying\t15\t200\n'
    n.step(status=PAUSED)
    assert n.now() == b'4\t1\tpaused\t15\t200\n'  # at once
    n.step()
    assert len(n.writes()) == 3  # nothing changed: nothing written
    n.step(sec=40)
    assert n.now() == b'4\t1\tpaused\t40\t200\n'  # a seek while paused, at once
    n.step(status=PLAYING, sec=41)
    n.step(sec=20)
    assert n.now() == b'4\t1\tplaying\t20\t200\n'  # a seek back, at once
    n.step(sec=0, pos=2)
    assert n.now() == b'4\t2\tplaying\t0\t200\n'  # the next song, at once
    assert n.now(b'bar') is None


def test_a_stop_between_songs_isnt_told_and_a_real_stop_is_told_once(fw, mcl):
    n = Now(fw, mcl)
    n.start()
    n.step(sec=180)
    for _ in range(5):  # 2.5 s stopped: the next song still starting
        n.step(status=STOPPED, pos=1)
    assert n.now() == b'4\t0\tplaying\t180\t200\n'
    n.step(status=PLAYING, sec=0)
    assert n.now() == b'4\t1\tplaying\t0\t200\n'
    for _ in range(7):  # 3 s on from the first pass that saw it, less the stamp's | 1
        n.step(status=STOPPED)
    assert n.now() == b'4\t1\tplaying\t0\t200\n'
    n.step()  # NOW_STOP_MS stopped
    assert n.now() == b'4\t1\tstopped\t0\t200\n'
    writes = len(n.writes())
    for _ in range(20):
        n.step()
    assert len(n.writes()) == writes
    n.step(status=PLAYING, sec=0)  # played again
    assert n.now() == b'4\t1\tplaying\t0\t200\n'


# A load or a seek: the player has no place yet (-1); and a state outside 1-3, which stock never sets, must not index `names[]`.
@pytest.mark.parametrize('status, sec', [(PLAYING, -1), (PAUSED, -1), (0, 30)])
def test_a_pass_with_no_place_or_state_to_tell_writes_nothing_and_a_later_one_tells(fw, mcl, status, sec):
    n = Now(fw, mcl)
    n.start()
    n.step()
    n.step(status=status, sec=sec, pos=1)
    assert n.now() == b'4\t0\tplaying\t0\t200\n' and len(n.writes()) == 1
    n.step(status=PLAYING, sec=0)
    assert n.now() == b'4\t1\tplaying\t0\t200\n'


@pytest.mark.parametrize('other', ['local', 'tidal', 'older'])
def test_another_queue_playing_is_told_as_a_stop_at_once(fw, mcl, other):
    n = Now(fw, mcl, rows=('app:7:0', '12345', 'app:6:0'))
    n.start()
    n.step(sec=30)
    if other == 'local':
        mcl.type = 0xf001
    else:
        mcl.pos = 1 if other == 'tidal' else 2
    n.step()
    assert n.now() == b'4\t0\tstopped\t30\t200\n'
    writes = len(n.writes())
    n.step(sec=60)
    n.step()
    assert len(n.writes()) == writes


def test_a_queue_that_didnt_ask_tells_nothing(fw, mcl):
    n = Now(fw, mcl)
    n.au.now = 0
    n.start()
    for sec in (0, 10, 20):
        n.step(sec=sec)
    n.step(status=STOPPED)
    assert n.writes() == []


def test_another_apps_url_queue_stops_the_one_before(fw, mcl):
    n = Now(fw, mcl, rows=('app:7:0', 'app:8:0'))
    n.start()
    n.step(sec=30)
    n.au.gen = 8
    n.start(slot=1, seq=b'9')
    assert n.now() == b'4\t0\tstopped\t30\t200\n'
    n.step(sec=0, pos=1)
    assert n.now(b'bar') == b'9\t0\tplaying\t0\t200\n' and n.now() == b'4\t0\tstopped\t30\t200\n'


def test_the_same_apps_next_queue_stops_the_one_before(fw, mcl):
    n = Now(fw, mcl, rows=('app:7:0', 'app:8:0'))
    n.start()
    n.step(sec=30)
    n.au.gen = 8
    n.start(seq=b'9')
    assert n.now() == b'4\t0\tstopped\t30\t200\n'
    n.step(sec=0, pos=1)
    assert n.now() == b'9\t0\tplaying\t0\t200\n'


def test_stopping_the_apps_tells_a_stop_at_once(fw, mcl):
    """addon_stop: Stop Streaming Services, which stops a URL queue too, and Home's Rockbox row, after which demo is
    killed with /tmp left as it is: the file says stopped before then."""
    n = Now(fw, mcl)
    n.start()
    n.step(sec=30)
    fw.fn('addon_stop', None)()
    assert n.now() == b'4\t0\tstopped\t30\t200\n'
    writes = len(n.writes())
    n.step(status=STOPPED)
    fw.fn('addon_stop', None)()
    assert len(n.writes()) == writes


def test_a_restart_tells_a_stop_for_every_song_left_playing(fw):
    """demo restarted (a crash, the watchdog) with /tmp kept: at its first poll, every /tmp/q2-<id>.now that says
    playing or paused says stopped at its last place, its seq and n kept; anything else is left alone, and so is
    every file after that first poll."""
    files = {b'/tmp/q2-foo.now': b'4\t2\tplaying\t77\t200\n', b'/tmp/q2-bar.now': b'x9\t0\tpaused\t5\t0\n',
             b'/tmp/q2-baz.now': b'3\t1\tstopped\t8\t90\n', b'/tmp/q2-foo.playing': b'4\t2\tplaying\t77\t200\n',
             b'/tmp/q2-Bad!.now': b'4\t2\tplaying\t77\t200\n', b'/tmp/q2-odd.now': b'playing\t1\tplaying\t1\t1\n',
             b'/tmp/q2-.now': b'1\t1\tplaying\t1\t1\n'}
    world = World(fw, dict(files), {b'/tmp': [p[5:] for p in files]})
    fw.fn('addon_poll', None)()
    assert world.files[b'/tmp/q2-foo.now'] == b'4\t2\tstopped\t77\t200\n'
    assert world.files[b'/tmp/q2-bar.now'] == b'x9\t0\tstopped\t5\t0\n'
    assert world.files[b'/tmp/q2-odd.now'] == b'playing\t1\tstopped\t1\t1\n'  # the third field is the state
    assert {p: world.files[p] for p in files if p not in (b'/tmp/q2-foo.now', b'/tmp/q2-bar.now', b'/tmp/q2-odd.now')} == \
        {p: files[p] for p in files if p not in (b'/tmp/q2-foo.now', b'/tmp/q2-bar.now', b'/tmp/q2-odd.now')}
    renames = len(world.of('rename'))
    world.files[b'/tmp/q2-foo.now'] = b'5\t0\tplaying\t1\t200\n'  # this demo's own, from now on
    world.ms += 500
    fw.fn('addon_poll', None)()
    assert len(world.of('rename')) == renames and world.files[b'/tmp/q2-foo.now'] == b'5\t0\tplaying\t1\t200\n'


@pytest.mark.parametrize('play,seq', [(b'42 ', b'42'), (b'1' * 30 + b' ', b'1' * 23), (b'4\t2 ', b'4')])
def test_a_url_queue_started_is_told_under_its_play_seq(fw, play, seq):
    """urls_start hands the session's app and its .play's seq (up to a space or a control, at most 23 bytes) to
    now_start once app_urls_play has played the queue; a queue that didn't play starts nothing."""
    world = World(fw, {b'/tmp/q2-a.play': play + b'/tmp/q2-a.urls\n',
                       b'/tmp/q2-a.urls': b'#Q2URLS/1\tnow=1\nhttps://x/a.flac\tA\n'}, now=500)
    fw.mock('timer_add', lambda *_: 5)
    Deques(fw)
    ap(fw).a[0].id, ap(fw).n = b'a', 1
    fw.fn('addon__stream_poll', None, C.POINTER(App))(C.pointer(ap(fw).a[0]))
    assert sm(fw).seq == seq and sm(fw).state == S_WAIT
    free_stubbed(fw, lambda: fw.fn('addon__urls_start', C.c_int, C.c_void_p)(None))
    nw = fw.var('addon__nw', Nw)
    assert nw.owner == 1 and nw.seq == seq and nw.live == 0 and fw.var('tidal__au', Au).now == 1
    nw.owner = 0
    world.files[b'/tmp/q2-a.urls'] = b'#Q2URLS/1\tnow=1\n'  # no song: nothing plays
    world.files[b'/tmp/q2-a.play'] = b'43 /tmp/q2-a.urls\n'
    fw.fn('addon__stream_poll', None, C.POINTER(App))(C.pointer(ap(fw).a[0]))
    free_stubbed(fw, lambda: fw.fn('addon__urls_start', C.c_int, C.c_void_p)(None))
    assert nw.owner == 0


@pytest.mark.parametrize('audio, timers', [(1, 0), (0, 1)])
def test_a_card_apps_first_song_waits_while_a_game_has_the_sound(fw, audio, timers):
    world = World(fw, {b'/tmp/q2-a.play': b'1 /mnt/mmc/.a\n'}, dirs={b'/mnt/mmc/.a': [b'01.flac']}, now=500)
    world.mtime[b'/mnt/mmc/.a'] = 100
    gm = fw.var('addon__gm', Game)
    gm.pid, gm.audio = 77, audio
    fw.mock('timer_add', lambda *_: 5)
    app = block(fw, size=512)  # an app_t, its id first
    C.memmove(app, b'a\0', 2)
    fw.fn('addon__stream_poll', None, C.c_void_p)(app)
    s = sm(fw)
    assert s.state == S_WAIT and len(fw.calls('timer_add')) == timers and s.timer == (5 if timers else 0)
