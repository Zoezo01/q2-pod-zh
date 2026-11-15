"""radio.c (docs/internals.md#internet-radio): the station player, Favourites, the directory fetch and the pages. The
playlist and CSV parsers' own cases are test/peq.py's and test_streaming.py's; here, what the page makes of the card's
playlists and the directory's replies."""
import ctypes as C
import gc
import struct
import time
import urllib.parse

import pytest

import ipod
from conftest import O, Files, block

RADIO = '/mnt/mmc/Radio'
FAVS, LAST, STATE = f'{RADIO}/favourites.m3u', f'{RADIO}/.last', '/tmp/q2radio.state'
API = 'https://all.api.radio-browser.info'
QUERY = '?order=votes&reverse=true&limit=100&hidebroken=true'
KEY_PLAY, KEY_BACK_BTN, KEY_FWD_BTN, KEY_RETURN = O['KEY_PLAY'], O['KEY_BACK_BTN'], O['KEY_FWD_BTN'], O['KEY_RETURN']
EVT_DESTROY, EVT_VALUE_CHANGED, EVT_CLICK, EVT_BLUR, EVT_KEY_UP, EVT_IM_ACTION = (O[k] for k in (
    'EVT_DESTROY', 'EVT_VALUE_CHANGED', 'EVT_CLICK', 'EVT_BLUR', 'EVT_KEY_UP', 'EVT_IM_ACTION'))
EVENT_KEY, W_PARENT = O['EVENT_KEY'], O['W_PARENT']
MENU, PICK, STATIONS = range(3)
R_PLAYING, R_FAVS, R_FEATURED, R_TOP, R_COUNTRY, R_GENRE = range(6)
RET_REPEAT = 8
FETCH_MAX = 256 << 10  # "at most 256 KB" (docs/internals.md)
FEATURED = ['Radio Paradise', 'SomaFM Groove Salad', 'BBC World Service', 'KEXP', 'FIP', 'NTS 1']
KEXP = ('KEXP', 'https://kexp-mp3-128.streamguys1.com/kexp128.mp3')
MENU_ROWS = ['Now Playing', 'Favourites', 'Featured', 'Top Stations', 'By Country', 'By Genre']
SPOT_SOCK = '/tmp/q2-librespot.sock'
# libcurl's numbering (curl.h: CURLOPTTYPE_LONG 0, _OBJECTPOINT 10000, _FUNCTIONPOINT 20000; CURLINFO_LONG 0x200000)
CURLOPT_NOPROGRESS, CURLOPT_WRITEFUNCTION = 43, 20011
CURLOPT_SSL_CTX_FUNCTION, CURLOPT_XFERINFOFUNCTION, CURLINFO_RESPONSE_CODE = 20108, 20219, 0x200002
CURLE_WRITE_ERROR, CURLE_ABORTED_BY_CALLBACK, CURLE_UNKNOWN_OPTION = 23, 42, 48
X509_V_FLAG_NO_CHECK_TIME = 0x200000  # OpenSSL's x509_vfy.h


class List(C.Structure):
    _fields_ = [('buf', C.c_void_p), ('len', C.c_uint), ('n', C.c_int), ('truncated', C.c_int),
                ('name', C.c_uint * 201), ('url', C.c_uint * 201)]

    def stations(self):
        return [(self.at(self.name[i]), self.at(self.url[i])) for i in range(self.n)]

    def at(self, off):
        return C.string_at((self.buf or 0) + off).decode()


class Rd(C.Structure):
    _fields_ = [('pid', C.c_int), ('sock', C.c_int), ('active', C.c_int), ('at', C.c_int), ('wait', C.c_int), ('polled', C.c_uint),
                ('volume', C.c_uint), ('list', List), ('state', C.c_char * 12), ('title', C.c_char * 256),
                ('codec', C.c_char * 16), ('bitrate', C.c_char * 16), ('error', C.c_char * 64),
                ('since', C.c_ulonglong), ('serial', C.c_uint)]


class Fx(C.Structure):
    _fields_ = [('thread', C.c_ulong), ('running', C.c_int), ('cancel', C.c_int), ('done', C.c_int), ('code', C.c_long),
                ('body', C.c_void_p), ('n', C.c_uint), ('url', C.c_char * 320)]


class Ui(C.Structure):
    _fields_ = [('page', C.c_void_p), ('view', C.c_void_p), ('edit', C.c_void_p), ('timer', C.c_uint), ('go', C.c_uint),
                ('leave', C.c_uint), *((k, C.c_int) for k in 'level next arg want az note genre favs from'.split()),
                ('rows', C.c_int * 6), ('sel', C.c_int * 3), ('wifi', C.c_int), ('pick', List), ('list', List), ('caption', C.c_char * 64)]


class Np(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page station artist song info elapsed heart'.split()),
                ('timer', C.c_uint), ('leave', C.c_uint), ('seen', C.c_uint), ('ipod', C.c_int)]


def rd(fw):
    return fw.var('radio__rd', Rd)


def ui(fw):
    return fw.var('radio__ui', Ui)


def np_(fw):
    return fw.var('radio__np', Np)


def fx(fw):
    return fw.var('radio__fx', Fx)


class Hs(C.Structure):
    """headset.c's state (test_headset.py's mirror): epoch moves with each manual command (headset_manual)."""
    _fields_ = [*((n, C.c_uint) for n in 'generation running head tail report'.split()), ('keys', C.c_int * 16),
                *((n, C.c_uint) for n in ('worker_generation checked epoch paused_epoch paused_at recent_at recent_owner '
                                          'recent_session last_at session linked_at eq_checked').split()),
                *((n, C.c_int) for n in 'linked known ears want paused owner automatic last_key'.split()),
                ('address', C.c_ubyte * 14), ('airpods', C.c_int)]


class Sp(C.Structure):
    """spotify.c's state (test_streaming.py's mirror): playing while librespot plays."""
    _fields_ = [*((n, C.c_int) for n in ('launched', 'sock', 'playing', 'paused', 'local', 'active', 'played', 'dead')),
                *((n, C.c_uint) for n in ('polled', 'idle_at', 'duration', 'position', 'cover', 'launch_at', 'alive_at')),
                ('at', C.c_ulonglong), ('raw', C.c_char * 1536), ('state', C.c_char * 12), ('track', C.c_char * 64),
                ('title', C.c_char * 256), ('artist', C.c_char * 256), ('album', C.c_char * 256), ('out', C.c_char * 24),
                ('auth', C.c_int), ('loading', C.c_int), ('old', C.c_int), ('load_at', C.c_uint), ('log_from', C.c_long),
                ('wanted', C.c_char * 64), ('from_', C.c_char * 64)]


class Vid(C.Structure):
    """books.c's video player (test_headset.py's mirror): pid while a video plays."""
    _fields_ = [*((k, C.c_int) for k in 'paused volume gain pid sock seek'.split()),
                *((k, C.c_uint) for k in 'polled control_at seek_at key at paused_at'.split())]


class Mallinfo2(C.Structure):
    _fields_ = [(n, C.c_size_t) for n in 'arena ordblks smblks hblks hblkhd usmblks fsmblks uordblks fordblks keepcost'.split()]


def heap():
    """The bytes malloc has handed out and not had back (glibc's mallinfo2: its arena's and its mmapped blocks'). Under
    ASan, whose allocator glibc doesn't see, it stays put."""
    mallinfo2 = C.CDLL(None).mallinfo2
    mallinfo2.restype = Mallinfo2
    m = mallinfo2()
    return m.uordblks + m.hblkhd


def m3u(*stations):
    return ('#EXTM3U\n' + ''.join(f'#EXTINF:-1,{n}\n{u}\n' for n, u in stations)).encode()


def pls(*stations, header='[playlist]\n'):
    """A .pls as players write one: FileN= and TitleN= for each station, from 1, then their count."""
    entries = ''.join(f'File{i}={u}\nTitle{i}={n}\n' for i, (n, u) in enumerate(stations, 1))
    return f'{header}{entries}NumberOfEntries={len(stations)}\nVersion=2\n'.encode()


class Card(Files):
    """The card's files, RADIO_DIR's listing (opendir and readdir, not in name order) and access. made: each fopen's
    path and mode; handles: what it gave back. Faults: a read error (ferror), a file that won't open, and the .tmp a
    save writes failing to be made, written (the nth fwrite) or closed."""
    def __init__(self, fw, files=None):
        super().__init__(fw, dict(files or {}))
        self.ents, self.dirs, self.made, self.handles, self.unreadable = [], {}, [], set(), set()
        self.error, self.fail_create, self.fail_write, self.fail_close, self.writes = False, False, 0, False, 0
        fw.mock('fw_access', lambda p, *_: 0 if self.exists(C.string_at(p).decode()) else -1)
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda d, *_: self.dirs.pop(d) and 0)
        fw.mock('fw_ferror', lambda *_: int(self.error))
        fw.mock('fw_fopen', self.open_)
        fw.mock('fw_fwrite', self.write)
        fw.mock('fw_fclose', self.close)

    def open_(self, path, mode, *_):
        name, how = C.string_at(path).decode(), C.string_at(mode).decode()
        self.made.append((name, how))
        if name in self.unreadable or ('w' in how and self.fail_create):
            return 0
        handle = self.fopen(path, mode)
        self.handles.add(handle)
        return handle

    def write(self, data, size, count, f, *_):
        self.writes += 1
        return 0 if self.writes == self.fail_write else self.fwrite(data, size, count, f)

    def close(self, f, *_):
        failed = self.fail_close and self.open[f][2] is not None
        self.fclose(f)
        return -1 if failed else 0

    def exists(self, path):
        return path in self.files or any(p.startswith(path + '/') for p in self.files)

    def opendir(self, path, *_):
        path = C.string_at(path).decode()
        names = sorted({p[len(path) + 1:] for p in self.files if p.startswith(path + '/') and '/' not in p[len(path) + 1:]},
                       reverse=True)
        self.dirs[0x3100 + len(self.ents)] = names
        return 0x3100 + len(self.ents)

    def readdir(self, d, *_):
        if not self.dirs[d]:
            return 0
        ent = C.create_string_buffer(268)
        name = self.dirs[d].pop(0).encode()
        C.memmove(C.addressof(ent) + 11, name, len(name))
        self.ents.append(ent)
        return C.addressof(ent)


class Clock:
    """time_now_ms, sleep_ms passing it on, and clock_gettime's CLOCK_MONOTONIC (now_ms) in step."""
    def __init__(self, fw, now=100000):
        self.now = now
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('sleep_ms', lambda ms, *_: self.step(ms))
        fw.mock('fw_clock_gettime', lambda which, t, *_: struct.pack_into('<qq', (C.c_char * 16).from_address(t), 0,
                                                                          self.now // 1000, self.now % 1000 * 1000000))

    def step(self, ms):
        self.now += ms


class Player:
    """q2video: fork's pid (0 when it can't start) and the keys on RADIO_SOCK; waitpid says whether it ended, and
    reaped which ones it ended for. events: forks and datagrams in order."""
    def __init__(self, fw, pid=4242, ends=True):
        self.fw, self.pid, self.ends, self.started, self.killed, self.sent = fw, pid, ends, [], [], []
        self.reaped, self.events = [], []
        fw.mock('fw_fork', self.fork)
        fw.mock('fw_socket', lambda *_: 9)
        fw.mock('fw_waitpid', self.waitpid)
        fw.mock('fw_system', lambda cmd, *_: self.killed.append(C.string_at(cmd).decode()))
        fw.mock('fw_sendto', self.sendto)

    def fork(self, *_):
        self.started.append(self.pid)
        self.events.append('fork')
        return self.pid or -1

    def waitpid(self, pid, status, flags, *_):
        ended = self.ends or not flags
        if ended:
            self.reaped.append(pid)
        return pid if ended else 0

    def sendto(self, s, data, n, flags, to, *_):
        # the datagram and its address are on the caller's stack: read as sent
        self.sent.append((C.string_at(to + 2), C.string_at(data, n)))
        self.events.append(self.sent[-1])
        return n

    def keys(self):
        return [data for path, data in self.sent if path == b'/tmp/q2radio.sock']


class Widgets:
    """AWTK's constructors each a fresh zeroed widget with its parent; labels' text as set, read back. made: each
    widget alive (widget_destroy_children takes them out) by its constructor (or factory kind), parent and box; names:
    widget_set_name's; ints: widget_set_prop_int's, read back (none on no widget), set: each in order; strs and styles
    likewise. All read as the call is made: a key or value may be on the caller's stack."""
    def __init__(self, fw):
        self.fw, self.blocks, self.text, self.made, self.names = fw, [], {}, {}, {}
        self.ints, self.set, self.strs, self.styles = {}, [], {}, {}
        for name in ('window_create', 'scroll_view_create', 'list_item_create', 'image_create', 'hscroll_label_create',
                     'list_view_create', 'button_create', 'label_create'):
            fw.mock(name, lambda parent, x, y, w, h, *_, kind=name: self.new(parent, kind, (x, y, w, h)))
        fw.mock('widget_factory_create_widget',
                lambda f, kind, parent, x, y, w, *_: self.new(parent, C.string_at(kind).decode(), (x, y, w)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get)
        fw.mock('widget_set_name', lambda w, s, *_: self.names.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_destroy_children', lambda w, *_: self.drop(w))
        fw.mock('widget_set_prop_int', self.set_int)
        fw.mock('widget_get_prop_int', lambda w, key, default, *_: self.ints.get((w, C.string_at(key)), default) if w else default)
        fw.mock('widget_set_prop_str', lambda w, key, v, *_: self.strs.__setitem__((w, C.string_at(key)), v and C.string_at(v)))
        fw.mock('widget_use_style', lambda w, s, *_: self.styles.setdefault(w, []).append(C.string_at(s)))

    def set_int(self, w, key, v, *_):
        self.set.append((w, C.string_at(key), C.c_int(v).value))
        self.ints[self.set[-1][:2]] = self.set[-1][2]

    def drop(self, parent):
        for w in self.under(parent):
            self.drop(w)
            del self.made[w]

    def new(self, parent, kind='', box=()):
        self.blocks.append(C.create_string_buffer(0x200))
        C.c_void_p.from_address(C.addressof(self.blocks[-1]) + W_PARENT).value = parent
        self.made[C.addressof(self.blocks[-1])] = (kind, parent, tuple(C.c_int(v).value for v in box))
        return C.addressof(self.blocks[-1])

    def get(self, w, out, n, *_):
        s = self.text.get(w, '').encode()
        C.memmove(out, s + b'\0', len(s) + 1)
        return 0

    def under(self, parent, kind=''):
        """parent's widgets in the order made, of kind if given."""
        return [w for w, (k, p, _) in self.made.items() if p == parent and kind in ('', k)]


class Page:
    """radio_open's page: its timers by function, alive until removed or run to anything but RET_REPEAT (AWTK's
    one-shots), its rows' clicks and the toasts."""
    def __init__(self, fw, card, wifi=1, clock=None):
        self.fw, self.card, self.w, self.clock = fw, card, Widgets(fw), clock
        self.timers, self.toasts, self.threads, self.dead = [], [], [], set()
        self.wifi = wifi
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        fw.mock('timer_remove', lambda t, *_: self.dead.add(t))
        fw.mock('get_wifisignal', lambda *_: self.wifi)
        fw.mock('navigator_to_with_context', lambda name, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        fw.mock('fw_pthread_create', lambda t, a, fn, arg, *_: self.threads.append(fn) or 0)
        fw.fn('radio_open', C.c_int, C.c_void_p, C.c_void_p)(0, 0)

    def fn(self, name):
        return C.cast(getattr(self.fw.lib, 'radio__' + name), C.c_void_p).value

    def live(self, name):
        """The ids of name's timers still armed."""
        return [k + 1 for k, (f, _) in enumerate(self.timers) if f == self.fn(name) and k + 1 not in self.dead]

    def run(self, name):
        """The newest timer running name, as the next loop pass would; there must be one armed."""
        armed = self.live(name)
        assert armed, f'no {name} timer armed'
        result = C.CFUNCTYPE(C.c_int, C.c_void_p)(self.timers[armed[-1] - 1][0])(0)
        if result != RET_REPEAT:
            self.dead.add(armed[-1])
        return result

    def items(self, name):
        """The newest list's rows clicking name: each row by its index."""
        calls = self.fw.calls()
        start = max([k for k, (n, _) in enumerate(calls) if n == 'list_view_create'], default=0)
        return {ctx: w for n, (w, evt, fn, ctx, *_) in calls[start:]
                if n == 'widget_on' and evt == EVT_CLICK and fn == self.fn(name)}

    def rows(self, name):
        """The newest list's rows clicking name: their clicks by index."""
        click = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(self.fn(name))
        return {ctx: (lambda c=ctx: click(c, 0)) for ctx in self.items(name)}

    def captions(self, name):
        """The newest list's rows clicking name: the caption each shows, by index."""
        return {ctx: self.w.text[self.w.under(item, 'hscroll_label_create')[0]] for ctx, item in self.items(name).items()}

    def menu_rows(self):
        captions = self.captions('menu_click')
        return [captions[k] for k in sorted(captions)]

    def menu(self, caption):
        """The menu's row with caption, clicked, and the rebuild it schedules."""
        self.rows('menu_click')[self.menu_rows().index(caption)]()
        if caption != 'Now Playing':
            self.run('go')

    def handler(self, w, event):
        (fn, ctx), = [(fn, ctx) for _, (ww, evt, fn, ctx, *_) in self.fw.calls('widget_on') if ww == w and evt == event][-1:]
        return lambda e=0: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, e)

    def key(self, page, key=KEY_RETURN):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, EVENT_KEY, key)
        self.handler(page, EVT_KEY_UP)(C.addressof(event))


def page(fw, files=None, **kw):
    clock = Clock(fw)
    return Page(fw, Card(fw, files), clock=clock, **kw)


def at(p, row):
    """The wheel on row of the list up, as ringnav keeps it in its view's _ringnav_index."""
    p.w.ints[(ui(p.fw).view, b'_ringnav_index')] = row


def selected(p):
    """The row the page last selected (ringnav_select's _ringnav_index)."""
    return [v for _, key, v in p.w.set if key == b'_ringnav_index'][-1]


def title(p):
    """The newest list's title: the first label page_list puts on the page."""
    return p.w.text[p.w.under(ui(p.fw).page, 'hscroll_label_create')[-1]]


def classic(fw):
    """The Classic theme (THEME=1 in the config)."""
    def read(path, section, key, out, default, *_):
        v = b'1' if C.string_at(key) == b'THEME' else C.string_at(default)
        C.memmove(out, v + b'\0', len(v) + 1)
    fw.mock('toolsReadConfig', read)


# The menu and the built-in lists.

def test_the_menu_without_a_station_played_has_no_now_playing(fw):
    p = page(fw)
    assert p.menu_rows() == MENU_ROWS[1:]
    assert [C.string_at(a[0]) for _, a in fw.calls('fw_mkdir')] == [RADIO.encode()]  # the folder, nothing in it
    assert p.card.files == {}


def test_the_menu_after_a_restart_has_now_playing_from_last(fw):
    p = page(fw, {LAST: m3u(('KEXP', 'https://kexp.example/128.mp3'))})
    assert p.menu_rows() == MENU_ROWS and fw.calls('fw_mkdir') == []


def test_featured_is_the_six_built_in_stations(fw):
    p = page(fw)
    p.menu('Featured')
    stations = ui(fw).list.stations()
    assert [n for n, _ in stations] == FEATURED and all('://' in u for _, u in stations)
    assert ui(fw).caption == b'Featured' and ui(fw).level == STATIONS


def test_favourites_are_favourites_m3u_then_every_other_playlist(fw):
    p = page(fw, {FAVS: m3u(('One', 'http://1.example/')),
                  f'{RADIO}/b.PLS': b'[playlist]\nFile1=http://3.example/\nTitle1=Three\n',
                  f'{RADIO}/a.m3u': m3u(('Two', 'http://2.example/')),
                  f'{RADIO}/.hidden.m3u': m3u(('Hidden', 'http://h.example/')),
                  f'{RADIO}/notes.txt': b'http://x.example/\n', f'{RADIO}/._a.m3u': b'junk'})
    p.menu('Favourites')
    first, *rest = ui(fw).list.stations()
    assert first == ('One', 'http://1.example/')  # the rest in the folder's order
    assert sorted(rest) == [('Three', 'http://3.example/'), ('Two', 'http://2.example/')]
    assert ui(fw).caption == b'Favourites'


def test_no_favourites(fw):
    p = page(fw)
    p.menu('Favourites')
    assert ui(fw).list.n == 0 and ui(fw).caption == b'No favourites'


def test_the_menu_is_internet_radio_s_and_only_now_playing_plays_at_once(fw):
    """The page is the Streaming row's, Internet Radio; Now Playing plays, so its row has no chevron (row_into's
    list_into), and the rest open lists."""
    p = page(fw, {LAST: m3u(KEXP)})
    assert title(p) == 'Internet Radio'
    rows = {ctx: w for _, (w, evt, fn, ctx, *_) in fw.calls('widget_on') if evt == EVT_CLICK and fn == p.fn('menu_click')}
    inside = {w for item in rows.values() for w in p.w.under(item)}
    into = {a[0] for _, a in fw.calls('image_base_set_image') if a[0] in inside and C.string_at(a[1]) == b'list_into'}
    shown = p.menu_rows()
    assert [(shown[k], bool(into & set(p.w.under(rows[k])))) for k in sorted(rows)] == [(r, r != 'Now Playing') for r in MENU_ROWS]


def test_a_list_has_a_row_for_each_station_and_no_more(fw):
    p = page(fw)
    p.menu('Featured')
    captions = p.captions('station_click')
    assert [captions[k] for k in sorted(captions)] == FEATURED


def test_every_level_has_the_same_plain_rows(fw):
    """The menu, the lists and a note under them are page_list's rows, every level's as high as the others'."""
    p = page(fw, wifi=0)
    p.menu('Featured')
    p.key(ui(fw).page)
    p.run('leave')
    p.menu('Top Stations')  # No Wi-Fi: a note
    heights = [v for _, key, v in p.w.set if key == b'item_height']
    assert len(heights) == 4 and len(set(heights)) == 1


def test_a_new_list_from_the_menu_opens_on_its_first_row(fw):
    p = page(fw, {FAVS: m3u(('One', 'http://1.example/'), ('Two', 'http://2.example/'), ('Three', 'http://3.example/'))})
    p.menu('Featured')
    at(p, 4)
    p.key(ui(fw).page)
    p.run('leave')  # the menu, Featured's row 4 kept for it
    p.menu('Favourites')
    assert selected(p) == 0


def test_a_list_shorter_than_its_kept_row_selects_its_last(fw):
    p = page(fw, {FAVS: m3u(('One', 'http://1.example/'), ('Two', 'http://2.example/'), ('Three', 'http://3.example/'))})
    p.menu('Favourites')
    assert hold(fw, p, 2) == 'Removed from Favourites'  # the last row, held
    p.run('go')
    assert ui(fw).list.n == 2 and selected(p) == 1


def test_no_favourites_under_classic_is_an_empty_page_its_bar_names_favourites(fw):
    """page_empty: Classic's bar holds the title, so the empty list's caption stays its first line and the bar reads
    the list's name; a list with rows has no such page, and an empty one selects no row."""
    classic(fw)
    p = page(fw)
    fw.mock('widget_get_child', lambda w, i, *_: (p.w.under(w) + [0] * (i + 1))[i])
    p.menu('Featured')
    assert not [t for w, t in p.w.text.items() if t == 'Internet Radio' and w in p.w.under(ui(fw).page)]
    p.key(ui(fw).page)
    p.run('leave')
    marks = len(p.w.set)
    p.menu('Favourites')
    shown = {p.w.text.get(w) for w in p.w.under(ui(fw).page, 'hscroll_label_create')}
    assert {'No favourites', 'Favourites'} <= shown
    assert not [key for _, key, _ in p.w.set[marks:] if key.startswith(b'_ringnav')]  # ringnav_select's


# Playing.

def play(fw, p, i=0, pid=4242, caption='Featured'):
    player = Player(fw, pid)
    p.menu(caption)
    p.rows('station_click')[i]()
    return player


def test_a_station_plays_in_q2video_and_is_kept_as_last(fw):
    p = page(fw)
    player = play(fw, p, 3)
    state = rd(fw)
    assert player.started == [4242] and (state.pid, state.active, state.at) == (4242, 1, 3)
    assert state.state == b'connecting' and state.serial == 1
    assert p.card.files[LAST] == m3u(('KEXP', 'https://kexp-mp3-128.streamguys1.com/kexp128.mp3'))
    assert [n for n, _ in state.list.stations()] == FEATURED  # the list it was in, for the side buttons
    assert fw.fn('radio_status', C.c_int)() == 2


def test_a_station_that_can_t_start_says_so(fw):
    p = page(fw)
    play(fw, p, 0, pid=0)
    assert rd(fw).pid == 0 and rd(fw).state == b'error' and fw.fn('radio_status', C.c_int)() == 3


def media(fw, key):
    return fw.fn('radio_media', C.c_int, C.c_uint)(key)


def test_play_pause_stops_the_station_and_plays_it_again(fw):
    p = page(fw)
    player = play(fw, p, 2)
    assert media(fw, KEY_PLAY) == 1
    state = rd(fw)
    assert player.keys() == [b'q'] and state.pid == 0 and state.state == b'stopped' and state.active == 1
    assert [C.string_at(a[0]).decode() for _, a in fw.calls('fw_unlink')] == [STATE]
    assert media(fw, KEY_PLAY) == 1 and state.pid == 4242 and state.at == 2


@pytest.mark.parametrize('at, key, then', [(2, KEY_FWD_BTN, 3), (5, KEY_FWD_BTN, 0), (2, KEY_BACK_BTN, 1),
                                           (0, KEY_BACK_BTN, 5)])
def test_the_side_buttons_step_through_the_list_round_the_ends(fw, at, key, then):
    p = page(fw)
    player = play(fw, p, at)
    assert media(fw, key) == 1 and rd(fw).at == then and player.keys() == [b'q']


@pytest.mark.parametrize('key', [218, 172, 173, KEY_RETURN])
def test_other_keys_are_not_the_radio_s(fw, key):
    p = page(fw)
    play(fw, p)
    assert media(fw, key) == 0


def test_once_another_player_took_over_the_keys_are_theirs(fw):
    p = page(fw)
    player = play(fw, p)
    fw.fn('radio_stop', None)()
    assert rd(fw).active == 0 and player.keys() == [b'q'] and media(fw, KEY_PLAY) == 0
    assert fw.fn('radio_status', C.c_int)() == 0


def test_a_q2video_that_won_t_quit_is_killed_after_1_5_s(fw):
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    media(fw, KEY_PLAY)
    assert player.killed == ['kill -9 4242'] and rd(fw).pid == 0
    sleeps = [a[0] for _, a in fw.calls('sleep_ms')]
    assert set(sleeps) == {20} and 1500 <= 20 * len(sleeps) <= 1520


@pytest.mark.parametrize('active, pid, playing, then', [(1, 4242, 0, 'q'), (1, 0, 1, 'start'), (1, 4242, 1, None),
                                                        (1, 0, 0, None), (0, 0, 1, None)])
def test_set_playing(fw, active, pid, playing, then):
    p = page(fw)
    player = play(fw, p)
    if not pid:
        media(fw, KEY_PLAY)
    rd(fw).active = active
    started, keys = len(player.started), len(player.keys())
    fw.fn('radio_set_playing', None, C.c_int)(playing)
    assert (player.keys()[keys:], len(player.started) - started) == {'q': ([b'q'], 0), 'start': ([], 1), None: ([], 0)}[then]


def test_a_q2video_that_quits_when_asked_is_not_killed(fw):
    p = page(fw)
    player = play(fw, p)
    media(fw, KEY_PLAY)
    assert player.keys() == [b'q'] and player.killed == [] and player.reaped == [4242]


def test_a_killed_q2video_is_waited_for(fw):
    """kill -9, then a waitpid that waits: no zombie left behind."""
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    media(fw, KEY_PLAY)
    assert player.killed == ['kill -9 4242'] and player.reaped == [4242]


@pytest.mark.parametrize('stall, killed', [(1499, False), (1500, True)])
def test_q2video_has_until_1_5_s_after_the_q(fw, stall, killed):
    """The UI thread stalled after the q: still under RADIO_QUIT_MS, q2video gets another look (and has gone by
    then); at 1.5 s it is killed."""
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    sleeps = []
    def sleep(ms, *_):
        sleeps.append(ms)
        p.clock.now += stall if len(sleeps) == 1 else ms
        player.ends = len(sleeps) > 1
    fw.mock('sleep_ms', sleep)
    media(fw, KEY_PLAY)
    assert bool(player.killed) == killed and rd(fw).pid == 0


def test_a_station_runs_q2video_in_its_radio_mode(fw):
    """The child's exec (docs/internals.md): q2video -r DEVICE URL, the headphone DAC's plughw:0,0 and no volume here."""
    p = page(fw)
    fw.mock('fw_fork', lambda *_: 0)  # the child's side
    p.menu('Featured')
    p.rows('station_click')[3]()
    (_, argv), = fw.calls('fw_execl')
    assert [a and C.string_at(a).decode() for a in argv] == ['/usr/bin/q2video', '/usr/bin/q2video', '-r', 'plughw:0,0',
                                                             KEXP[1], 0]


def test_a_station_pauses_spotify_first(fw):
    """spot_yield: a playing librespot is told to pause before q2video takes the output."""
    fw.var('spotify__sp', Sp).playing = 1
    p = page(fw)
    player = play(fw, p)
    assert player.events[:2] == [(SPOT_SOCK.encode(), b's'), 'fork'] and fw.var('spotify__sp', Sp).playing == 0


def test_a_station_started_or_stopped_by_hand_is_the_user_s_choice(fw):
    """headset_manual: a headset's resume (a bud back in) is the user's to cancel by any manual command."""
    p = page(fw)
    hs = fw.var('headset__hs', Hs)
    before = hs.epoch
    play(fw, p)
    started = hs.epoch
    media(fw, KEY_PLAY)
    assert before < started < hs.epoch


def test_the_next_station_shows_nothing_of_the_last(fw):
    p = page(fw)
    player = play(fw, p, 0)
    player.ends = False
    p.card.files[STATE] = b'state=playing\ntitle=Air - Alpha\ncodec=MP3\nbitrate=128\nat=90000\n'
    rd(fw).polled -= 500
    poll(fw)
    media(fw, KEY_FWD_BTN)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    assert now_playing(fw, p) == {'station': FEATURED[1], 'artist': '', 'song': '', 'info': 'Connecting…', 'elapsed': ''}


def test_a_station_s_error_is_its_own(fw):
    """q2video's reason shows; the next station that can't start, or that fails with no reason, says only that."""
    p = page(fw)
    player = play(fw, p, 0)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.card.files[STATE] = b'state=error\nerror=open: Device or resource busy\n'
    rd(fw).polled -= 500
    poll(fw)
    p.run('np_tick')
    assert now_playing(fw, p)['info'] == "Can't play: open: Device or resource busy"
    player.pid = 0  # the next can't start
    media(fw, KEY_FWD_BTN)
    p.run('np_tick')
    assert now_playing(fw, p)['info'] == "Can't play this station"
    player.pid = 4243  # the one after starts, and fails with no reason
    media(fw, KEY_FWD_BTN)
    p.card.files[STATE] = b'state=error\n'
    rd(fw).polled -= 500
    poll(fw)
    p.run('np_tick')
    assert now_playing(fw, p)['info'] == "Can't play this station"


def test_now_playing_from_the_menu_keeps_the_list_playing(fw):
    """The station in memory, not .last's copy: the side buttons still step through the list it came from."""
    p = page(fw)
    play(fw, p, 2)
    p.handler(np_(fw).page, EVT_DESTROY)()
    p.key(ui(fw).page)
    p.run('leave')
    p.menu('Now Playing')
    assert [n for n, _ in rd(fw).list.stations()] == FEATURED and rd(fw).at == 2


def test_now_playing_stays_in_the_menu_while_a_station_is_known_though_last_wasn_t_saved(fw):
    p = page(fw)
    p.card.fail_rename = True  # .last never lands on the card
    play(fw, p)
    p.handler(np_(fw).page, EVT_DESTROY)()
    p.key(ui(fw).page)
    p.run('leave')
    assert p.menu_rows() == MENU_ROWS and LAST not in p.card.files


def test_a_stream_with_no_bitrate_after_one_with_shows_its_codec_alone(fw):
    p = page(fw)
    player = play(fw, p, 0)
    player.ends = False
    p.card.files[STATE] = b'state=playing\ncodec=MP3\nbitrate=128\nat=90000\n'
    rd(fw).polled -= 500
    poll(fw)
    media(fw, KEY_FWD_BTN)
    p.card.files[STATE] = b'state=playing\ncodec=AAC\nat=95000\n'
    rd(fw).polled -= 500
    poll(fw)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    assert now_playing(fw, p)['info'] == 'AAC'


def test_now_playing_with_no_station_to_play(fw):
    """A .last cut short on the card: Now Playing opens on nothing, plays nothing, and its heart shows and saves
    nothing."""
    p = page(fw, {LAST: b'#EXTM3U\n'})
    player = Player(fw)
    p.menu('Now Playing')
    assert player.started == [] and np_(fw).page and now_playing(fw, p)['station'] == ''
    p.handler(np_(fw).heart, EVT_CLICK)()
    assert hearts(fw) == [] and p.toasts == [] and FAVS not in p.card.files


# radio_poll.

def poll(fw):
    fw.fn('radio_poll', None)()


def test_the_poll_reads_q2video_s_state(fw):
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    p.card.files[STATE] = b'state=playing\ntitle=Artist - Song\ncodec=MP3\nbitrate=128\nerror=\nat=123456\n'
    state = rd(fw)
    state.polled -= 500
    poll(fw)
    assert (state.state, state.title, state.codec, state.bitrate, state.since) == (b'playing', b'Artist - Song', b'MP3',
                                                                                   b'128', 123456)


def test_while_it_plays_standby_and_the_dac_s_countdown_are_held(fw):
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    dac = fw.var('fw_ctx_g_dacoff_time', C.c_int)
    dac.value = 30
    rd(fw).polled -= 500
    poll(fw)
    assert [a[:3] for _, a in fw.calls('reset_poweroptions_timer')] == [(1, 1, 0)] and dac.value == 0


def test_a_dac_already_off_stays_off(fw):
    p = page(fw)
    play(fw, p).ends = False
    dac = fw.var('fw_ctx_g_dacoff_time', C.c_int)
    dac.value = -1
    rd(fw).polled -= 500
    poll(fw)
    assert dac.value == -1


def test_the_poll_is_paced_to_half_a_second(fw):
    p = page(fw)
    play(fw, p).ends = False
    state = rd(fw)
    state.polled = 100000 - 499
    poll(fw)
    assert fw.calls('reset_poweroptions_timer') == []


def test_a_volume_change_reaches_q2video(fw):
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    fw.var('g_volume', C.c_ubyte).value = 40
    rd(fw).polled -= 500
    poll(fw)
    assert player.keys()[-1] == b'v\x28'
    rd(fw).polled -= 500
    poll(fw)
    assert player.keys().count(b'v\x28') == 1  # only on a change


@pytest.mark.parametrize('was, now', [(b'playing', b'stopped'), (b'error', b'error')])
def test_a_station_that_ended_is_stopped_or_says_its_error(fw, was, now):
    p = page(fw)
    play(fw, p)
    p.card.files[STATE] = b'state=' + was + b'\n'
    rd(fw).polled -= 500
    poll(fw)
    assert rd(fw).pid == 0 and rd(fw).state == now and fw.calls('reset_poweroptions_timer') == []


def test_nothing_playing_no_poll(fw):
    Clock(fw)
    poll(fw)
    assert fw.calls('fw_waitpid') == []


def test_a_poll_comes_once_each_half_second(fw):
    p = page(fw)
    play(fw, p).ends = False
    rd(fw).polled = p.clock.now - 500
    poll(fw)
    poll(fw)  # the next pass, at once: too soon
    assert len(fw.calls('reset_poweroptions_timer')) == 1


def test_a_dac_countdown_about_to_end_is_held_too(fw):
    p = page(fw)
    play(fw, p).ends = False
    dac = fw.var('fw_ctx_g_dacoff_time', C.c_int)
    dac.value = 1
    rd(fw).polled -= 500
    poll(fw)
    assert dac.value == 0


def test_q2video_starts_at_the_volume_so_it_hears_of_no_change(fw):
    fw.var('g_volume', C.c_ubyte).value = 40
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    rd(fw).polled -= 500
    poll(fw)
    assert [k for k in player.keys() if k[:1] == b'v'] == []


# Favourites (radio_hold, the heart).

def hold(fw, p, row):
    at(p, row)
    s = fw.fn('radio_hold', C.c_char_p, C.c_void_p)(ui(fw).page)
    return s and s.decode()


def test_held_on_a_station_it_goes_into_favourites_and_out_again(fw):
    p = page(fw, {FAVS: m3u(('One', 'http://1.example/'))})
    p.menu('Featured')
    assert hold(fw, p, 3) == 'Added to Favourites'
    assert p.card.files[FAVS] == m3u(('One', 'http://1.example/'), ('KEXP', 'https://kexp-mp3-128.streamguys1.com/kexp128.mp3'))
    assert f'{FAVS}.tmp' not in p.card.files and fw.calls('fw_fsync')  # written aside, synced, renamed
    assert hold(fw, p, 3) == 'Removed from Favourites'
    assert p.card.files[FAVS] == m3u(('One', 'http://1.example/'))


def test_a_full_favourites_file_takes_no_more_but_gives_one_up(fw):
    kexp = ('KEXP', 'https://kexp-mp3-128.streamguys1.com/kexp128.mp3')
    full = m3u(*[(f'S{i}', f'http://{i}.example/') for i in range(199)], kexp)
    p = page(fw, {FAVS: full})
    p.menu('Featured')
    assert hold(fw, p, 0) == 'Could not save Favourites' and p.card.files[FAVS] == full
    assert hold(fw, p, 3) == 'Removed from Favourites'
    assert p.card.files[FAVS] == m3u(*[(f'S{i}', f'http://{i}.example/') for i in range(199)])


def test_a_favourites_file_past_200_is_never_rewritten(fw):
    over = m3u(*[(f'S{i}', f'http://{i}.example/') for i in range(201)])
    p = page(fw, {FAVS: over})
    p.menu('Featured')
    assert hold(fw, p, 0) == 'Could not save Favourites' and p.card.files[FAVS] == over


def test_a_favourites_file_with_a_nul_is_never_rewritten(fw):
    """The rest after a NUL would be lost."""
    odd = m3u(('One', 'http://1.example/')) + b'\0' + m3u(('Two', 'http://2.example/'))
    p = page(fw, {FAVS: odd})
    p.menu('Featured')
    assert hold(fw, p, 0) == 'Could not save Favourites' and p.card.files[FAVS] == odd


def test_a_failed_rename_is_a_failed_save(fw):
    p = page(fw)
    p.menu('Featured')
    p.card.fail_rename = True
    assert hold(fw, p, 0) == 'Could not save Favourites' and FAVS not in p.card.files


def test_a_toggle_from_the_favourites_list_rebuilds_it(fw):
    p = page(fw, {FAVS: m3u(('One', 'http://1.example/'), ('Two', 'http://2.example/'))})
    p.menu('Favourites')
    assert hold(fw, p, 0) == 'Removed from Favourites'
    p.run('go')
    assert ui(fw).list.stations() == [('Two', 'http://2.example/')]


@pytest.mark.parametrize('row', [-1, 6])
def test_held_on_no_station_does_nothing(fw, row):
    p = page(fw)
    p.menu('Featured')
    assert hold(fw, p, row) is None


def test_held_on_the_menu_or_another_page_does_nothing(fw):
    p = page(fw)
    assert hold(fw, p, 0) is None
    p.menu('Featured')
    assert fw.fn('radio_hold', C.c_char_p, C.c_void_p)(0x1234) is None


def test_held_on_the_menu_or_a_country_list_after_a_station_list_does_nothing(fw):
    """The station list left behind (ui.list) is not what the menu's or a country list's row means."""
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\nGermany,DE,99\n')
    p.menu('Featured')
    p.key(ui(fw).page)
    p.run('leave')
    assert hold(fw, p, 0) is None
    p.menu('By Country')
    fetched(fw, p, curl)
    assert hold(fw, p, 0) is None and FAVS not in p.card.files


def test_held_on_another_page_over_now_playing_does_nothing(fw):
    p = page(fw)
    play(fw, p)
    assert fw.fn('radio_hold', C.c_char_p, C.c_void_p)(0x1234) is None and FAVS not in p.card.files


@pytest.mark.parametrize('where', ['Featured', 'a country'])
def test_a_toggle_on_another_list_rebuilds_nothing(fw, where):
    """Only the Favourites list shows the change; any other stays as it is."""
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\nGermany,DE,99\n')
    if where == 'Featured':
        p.menu('Featured')
    else:
        p.menu('By Country')
        fetched(fw, p, curl)
        curl.body = m3u(('A', 'http://a.example/'))
        p.rows('pick_click')[0]()
        p.run('go')
        fetched(fw, p, curl)
    assert hold(fw, p, 0) == 'Added to Favourites' and p.live('go') == []


def test_favourites_read_m3u_files_as_people_write_them(fw):
    """A bare URL names itself, indented lines, Windows' and old Macs' line ends, a query in a URL, an #EXTINF with no
    name or no comma, a comment holding a URL (no station), a file:// URL, and a file with no last newline before the
    next."""
    favs = (b'#EXTM3U\r\n'
            b'http://a.example/bare\r\n'
            b'  #EXTINF:-1,Indented\r\n\t http://b.example/live\r\n'
            b'http://c.example/after-a-name\r\n'
            b'#EXTINF:-1,Query\rhttp://d.example/stream?type=mp3&kbps=128\r'
            b'#EXTINF:-1,\nhttp://e.example/unnamed\n'
            b'#EXTINF:-1\nhttp://f.example/no-comma\n'
            b'# mirror: http://x.example/not-a-station\n'
            b'file:///mnt/mmc/Radio/local.mp3\n'
            b'#EXTINF:-1,Last\nhttp://g.example/end')
    p, stations = favourites(fw, {FAVS: favs, f'{RADIO}/more.m3u': m3u(('More', 'http://m.example/'))})
    assert stations == [('http://a.example/bare', 'http://a.example/bare'), ('Indented', 'http://b.example/live'),
                        ('http://c.example/after-a-name', 'http://c.example/after-a-name'),
                        ('Query', 'http://d.example/stream?type=mp3&kbps=128'),
                        ('http://e.example/unnamed', 'http://e.example/unnamed'),
                        ('http://f.example/no-comma', 'http://f.example/no-comma'),
                        ('file:///mnt/mmc/Radio/local.mp3', 'file:///mnt/mmc/Radio/local.mp3'), ('Last', 'http://g.example/end'),
                        ('More', 'http://m.example/')]


def test_favourites_read_pls_files_by_their_titles(fw):
    """FileN= is a station and TitleN= names it, wherever either stands and however indented; an empty title, a title
    holding a URL (no station of its own), another key, or Title1 against File10 names nothing else."""
    lines = [b'[playlist]', b'NumberOfEntries=11']
    lines += [b'%sTitle%d=Station %d' % (b'  ' if i % 2 else b'\t', i, i) for i in range(11, 4, -1)]
    lines += [b'Title4=Live at http://p.example/4', b'Title3=', b'TITLE2=Station 2', b'Genre5=Jazz', b'Length1=-1']
    lines += [b'%sFile%d=http://p.example/%d' % (b'\t' if i % 2 else b'  ', i, i) for i in range(1, 12)]
    lines += [b'title1=Station 1', b'Version=2']
    p, stations = favourites(fw, {f'{RADIO}/stations.pls': b'\n'.join(lines) + b'\n'})
    names = {3: 'http://p.example/3', 4: 'Live at http://p.example/4'}
    assert stations == [(names.get(i, f'Station {i}'), f'http://p.example/{i}') for i in range(1, 12)]


def test_a_pls_s_titles_go_by_the_whole_key(fw):
    """The last TitleN= names every FileN= of its key, in any case, before or after it; an empty key names nothing,
    and a line that only starts like a key (Filmx=, Titlo2=, #EXTINFX, a URL holding an =) is a station whole, or
    nothing, as any other line."""
    lines = [b'Title9=Nine early', b'File9=http://9.example/', b'file2=http://2a.example/', b'FILE2=http://2b.example/',
             b'File=http://e.example/', b'Title=Empty', b'Filmx=http://f.example/', b'Titlex=Wrong',
             b'http://p.example/?id=7', b'Title://p.example/?id=Wrong', b'Files://s.example/', b'Titled://t.example/',
             b'Titlo=http://o.example/', b'#EXTINFX,Wrong', b'http://x.example/', b'Title2=Two', b'Titlo2=Wrong',
             b'title9=Nine']
    _, stations = favourites(fw, {f'{RADIO}/stations.pls': b'\n'.join(lines) + b'\n'})
    whole = ['http://e.example/', 'Filmx=http://f.example/', 'http://p.example/?id=7', 'Files://s.example/',
             'Titled://t.example/', 'Titlo=http://o.example/', 'http://x.example/']
    assert stations == [('Nine', 'http://9.example/'), ('Two', 'http://2a.example/'), ('Two', 'http://2b.example/'),
                        *((u, u) for u in whole)]


def favourites(fw, files):
    p = page(fw, files)
    p.menu('Favourites')
    return p, ui(fw).list.stations()


def test_a_file_url_with_an_equals_sign_is_a_station(fw):
    _, stations = favourites(fw, {FAVS: b'#EXTM3U\nfile:///mnt/mmc/Radio/Live=Set.mp3\n'})
    assert stations == [('file:///mnt/mmc/Radio/Live=Set.mp3',) * 2]


def test_the_folder_s_other_files_are_skipped_and_every_file_closed(fw):
    p, stations = favourites(fw, {f'{RADIO}/README': b'http://r.example/\n', f'{RADIO}/a.m3u': m3u(('A', 'http://a.example/')),
                                  f'{RADIO}/.last': m3u(KEXP)})
    assert stations == [('A', 'http://a.example/')] and p.card.open == {} and p.card.dirs == {}


def test_favourites_past_200_stations_list_the_first_200(fw):
    p, stations = favourites(fw, {FAVS: m3u(*[(f'S{i}', f'http://{i}.example/') for i in range(250)])})
    assert stations == [(f'S{i}', f'http://{i}.example/') for i in range(200)]


@pytest.mark.parametrize('header', ['[playlist]\n', ''])
def test_a_pls_past_200_stations_lists_the_first_200_by_their_titles(fw, header):
    """TitleN= names FileN and nothing else, for each of the 200 kept: the folder's only playlist, so the first text
    favs_read reads, with its [playlist] line or without, starting at File1= (playlist() needs no header)."""
    many = [(f'S{i}', f'http://{i}.example/') for i in range(250)]
    _, stations = favourites(fw, {f'{RADIO}/stations.pls': pls(*many, header=header)})
    assert stations == many[:200]


def test_a_pls_s_titles_are_looked_up_not_walked(fw):
    """FETCH_MAX of 201 FileN= lines 600 spaces in, then TitleN= lines (each walking back to every station's line
    start took over 2,500 times as long): its titles cost a few times the same lines as another key. The best of
    five each, so a slow runner scales both and a loaded one has to stretch every run. The clock's, not CPU time:
    test/cmut's runs read that as 0 for the lines as another key, under a millisecond."""
    fn = fw.fn('playlist', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int)

    def took(key):
        text = b''.join(b' ' * 600 + b'File%d=a://\n' % i for i in range(201))
        text = (text + b''.join(key + b'%d=x\n' % (i % 201) for i in range(15000)))[:FETCH_MAX]
        best = []
        for _ in range(5):
            buf, name, url = block(fw, text), block(fw, size=201 * 4), block(fw, size=201 * 4)
            start = time.perf_counter()
            assert fn(buf, name, url, 201) == 201
            best.append(time.perf_counter() - start)
        return min(best)
    assert took(b'Title') < 20 * took(b'Genre')


def test_a_favourites_file_past_200_gives_none_up_either(fw):
    """Taking one out would lose the stations past 200 too."""
    over = m3u(*[(f'S{i}', f'http://{i}.example/') for i in range(201)])
    p, _ = favourites(fw, {FAVS: over})
    assert hold(fw, p, 0) == 'Could not save Favourites' and p.card.files[FAVS] == over


def test_a_full_favourites_file_gives_up_its_first(fw):
    full = [(f'S{i}', f'http://{i}.example/') for i in range(200)]
    p, _ = favourites(fw, {FAVS: m3u(*full)})
    assert hold(fw, p, 0) == 'Removed from Favourites' and p.card.files[FAVS] == m3u(*full[1:])


@pytest.mark.parametrize('fault', ['past 256 KB', 'read error', "won't open"])
def test_a_favourites_file_not_read_whole_is_never_rewritten(fw, fault):
    favs = m3u(('One', 'http://1.example/')) + (b'#' * FETCH_MAX + b'\n' if fault == 'past 256 KB' else b'')
    p = page(fw, {FAVS: favs})
    p.menu('Featured')
    p.card.error = fault == 'read error'
    if fault == "won't open":
        p.card.unreadable.add(FAVS)
    assert hold(fw, p, 0) == 'Could not save Favourites' and p.card.files[FAVS] == favs


@pytest.mark.parametrize('cut', ['in a URL', 'after a URL'])
def test_favourites_stop_at_256_kb(fw, cut):
    """Every playlist is read into one FETCH_MAX buffer, a newline after each, room kept for a newline and the NUL:
    favourites.m3u, then b.m3u up to that room, the URL it cuts no station unless whole, and a.m3u not at all."""
    first = m3u(('A', 'http://a.example/'))
    room = FETCH_MAX - (len(first) + 1) - 2  # what b.m3u can give
    tail = b'\nhttp:/' if cut == 'in a URL' else b'\nhttp://b.example/'
    big = b'#' * (room - len(tail)) + tail + (b'/c.example/\n' if cut == 'in a URL' else b'\nhttp://c.example/\n')
    p, stations = favourites(fw, {FAVS: first, f'{RADIO}/b.m3u': big, f'{RADIO}/a.m3u': m3u(('D', 'http://d.example/'))})
    assert stations == [('A', 'http://a.example/')] + ([('http://b.example/', 'http://b.example/')] if cut == 'after a URL' else [])
    assert p.card.open == {}


def test_a_station_too_long_to_save_leaves_favourites_as_they_were(fw):
    """Its #EXTINF and URL past the line a favourite is written in: the save fails whole, nothing is written cut."""
    before = m3u(('One', 'http://1.example/'))
    p = page(fw, {FAVS: before})
    curl = Curl(fw, m3u(('Long', 'http://long.example/' + 'x' * 2000)))
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert hold(fw, p, 0) == 'Could not save Favourites' and p.card.files[FAVS] == before


def test_a_favourite_is_saved_whole_or_not_at_all(fw):
    """Stations whose saved lines run from under m3u_write's kilobyte line to over it: each goes into favourites.m3u
    whole, or the save fails and the file stays as it was; never cut, nor with a NUL in it that would hide the
    favourites after it (favs_read). The shorter ones are saved, the longer fail."""
    one = ('One', 'http://1.example/')
    long = [('Long', 'http://long.example/' + 'x' * k) for k in range(960, 1010)]
    p = page(fw, {FAVS: m3u(one)})
    curl = Curl(fw, m3u(*long))
    p.menu('Top Stations')
    fetched(fw, p, curl)
    saved = []
    for row, station in enumerate(long):
        p.card.files[FAVS] = m3u(one)
        saved.append(hold(fw, p, row) == 'Added to Favourites')
        assert p.card.files[FAVS] == (m3u(one, station) if saved[-1] else m3u(one))
    assert saved == sorted(saved, reverse=True) and saved[0] and not saved[-1]


def test_a_save_writes_beside_favourites_then_renames(fw):
    """The .tmp in the same folder, so the rename is atomic on the card."""
    p = page(fw)
    p.menu('Featured')
    hold(fw, p, 0)
    assert [path for path, mode in p.card.made if 'w' in mode] == [f'{FAVS}.tmp'] and FAVS in p.card.files


@pytest.mark.parametrize('fault', ['create', 'write', 'flush', 'sync', 'close'])
def test_a_save_the_card_fails_leaves_favourites_as_they_were(fw, fault):
    before = m3u(('One', 'http://1.example/'), ('Two', 'http://2.example/'))
    p = page(fw, {FAVS: before})
    p.menu('Featured')
    p.card.fail_create = fault == 'create'
    p.card.fail_write = 2 if fault == 'write' else 0  # One's line, after the header
    p.card.fail_close = fault == 'close'
    if fault in ('flush', 'sync'):
        fw.mock(f'fw_f{fault}', lambda *_: -1)
    assert hold(fw, p, 0) == 'Could not save Favourites' and p.card.files[FAVS] == before
    streams = {a[0] for n in ('fw_fflush', 'fw_fileno') for _, a in fw.calls(n)} | {a[3] for _, a in fw.calls('fw_fwrite')}
    assert streams <= p.card.handles  # nothing done to a file it hasn't got


def test_favourites_are_favourites_m3u_s_to_the_toggle_and_the_heart(fw):
    """The folder's other playlists are listed with Favourites but never copied into favourites.m3u, nor fill the
    heart."""
    paradise = ('Radio Paradise', 'http://stream.radioparadise.com/mp3-128')
    p = page(fw, {FAVS: m3u(('One', 'http://1.example/')), f'{RADIO}/other.m3u': m3u(paradise)})
    Player(fw)
    p.menu('Featured')
    assert hold(fw, p, 3) == 'Added to Favourites'
    assert p.card.files[FAVS] == m3u(('One', 'http://1.example/'), KEXP)
    p.rows('station_click')[0]()  # Radio Paradise, in other.m3u alone
    assert hearts(fw) == ['play_unfav']


# The directory.

class Curl:
    """demo's libcurl: perform calls the SSL_CTX callback with an OpenSSL 1.1.1 SSL_CTX (its verify params at 0x100,
    their flags the fourth word), then hands the body to the write callback in chunks, the progress callback asked
    first each time (with CURLOPT_NOPROGRESS off); its answer and the HTTP code, for CURLINFO_RESPONSE_CODE alone.
    result: perform's; flags: the verify params' after the callback; cleaned: the handles cleaned up."""
    def __init__(self, fw, body=b'', code=200, error=0, chunk=1000):
        self.fw, self.body, self.code, self.error, self.chunk, self.opts, self.wrote = fw, body, code, error, chunk, {}, []
        self.result, self.flags, self.cleaned = None, 0, []
        fw.mock('fw_curl_easy_init', lambda *_: 0x9000)
        fw.mock('fw_curl_easy_setopt', self.setopt)
        fw.mock('fw_curl_easy_perform', self.perform)
        fw.mock('fw_curl_easy_getinfo', self.getinfo)
        fw.mock('fw_curl_easy_cleanup', lambda c, *_: self.cleaned.append(c))

    def setopt(self, c, opt, value, *_):
        self.opts[opt] = C.string_at(value).decode() if 10000 <= opt < 20000 else value
        return 0

    def getinfo(self, c, info, out, *_):
        if info != CURLINFO_RESPONSE_CODE:
            return CURLE_UNKNOWN_OPTION
        C.c_long.from_address(out).value = self.code
        return 0

    def perform(self, c, *_):
        self.result = self.transfer(c)
        return self.result

    def transfer(self, c):
        if CURLOPT_SSL_CTX_FUNCTION in self.opts:
            params, ctx = (C.c_uint * 4)(), (C.c_char * 0x108)()
            C.c_void_p.from_buffer(ctx, 0x100).value = C.addressof(params)
            C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_void_p)(self.opts[CURLOPT_SSL_CTX_FUNCTION])(c, C.addressof(ctx), None)
            self.flags = params[3]
        progress = None
        if self.opts.get(CURLOPT_NOPROGRESS, 1) == 0 and CURLOPT_XFERINFOFUNCTION in self.opts:
            progress = C.CFUNCTYPE(C.c_int, C.c_void_p, *[C.c_longlong] * 4)(self.opts[CURLOPT_XFERINFOFUNCTION])
        write = C.CFUNCTYPE(C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_void_p)(self.opts[CURLOPT_WRITEFUNCTION])
        for at in range(0, len(self.body), self.chunk):
            if progress and progress(None, len(self.body), at, 0, 0):
                return CURLE_ABORTED_BY_CALLBACK
            part = self.body[at:at + self.chunk]
            self.wrote.append(write(part, 1, len(part), None))
            if self.wrote[-1] != len(part):
                return CURLE_WRITE_ERROR  # the callback took less
        return self.error


def fetched(fw, p, curl):
    """The worker run as its pthread would, then the page's tick."""
    C.CFUNCTYPE(C.c_void_p, C.c_void_p)(p.threads[-1])(0)
    return p.run('tick')


def note(p):
    return [t for t in p.w.text.values()]


def test_top_stations_fetches_by_votes_over_verified_tls(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('Zed', 'http://z.example/'), ('Abe', 'http://a.example/')))
    p.menu('Top Stations')
    assert fx(fw).running == 1 and 'Loading…' in note(p)
    fetched(fw, p, curl)
    assert curl.opts[10002] == f'{API}/m3u/stations/topvote/100?hidebroken=true'
    assert (curl.opts[10065], curl.opts[64], curl.opts[81], curl.opts[10018]) == ('/etc/scrobble-ca.pem', 1, 2, 'Q2Pod')
    assert (curl.opts[78], curl.opts[13], curl.opts[52], curl.opts[99]) == (10, 30, 1, 1)
    assert ui(fw).list.stations() == [('Zed', 'http://z.example/'), ('Abe', 'http://a.example/')]  # vote order
    assert ui(fw).level == STATIONS and ui(fw).caption == b'Top Stations' and fx(fw).running == 0


def test_countries_are_a_to_z_and_a_country_s_stations_too(fw):
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\n"Korea, Republic of",KR,10\nThe Netherlands,NL,90\nGermany,DE,99\n'
                    b'Spain,ES,70\nAustria,AT,50\n')
    p.menu('By Country')
    assert curl.opts == {} and fx(fw).url == f'{API}/csv/countries?order=stationcount&reverse=true&limit=60'.encode()
    fetched(fw, p, curl)
    pick = ui(fw).pick
    assert [(pick.at(pick.name[i]), pick.at(pick.url[i])) for i in range(pick.n)] == [  # "The" aside, as the library
        ('Austria', 'AT'), ('Germany', 'DE'), ('Korea, Republic of', 'KR'), ('The Netherlands', 'NL'), ('Spain', 'ES')]
    assert ui(fw).level == PICK
    curl.body = m3u(('NPO Radio 2', 'http://b.example/'), ('BNR', 'http://a.example/'))
    p.rows('pick_click')[3]()
    p.run('go')
    assert fx(fw).url == f'{API}/m3u/stations/bycountrycodeexact/NL{QUERY}'.encode()
    assert ui(fw).caption == b'The Netherlands'
    fetched(fw, p, curl)
    assert [n for n, _ in ui(fw).list.stations()] == ['BNR', 'NPO Radio 2']


def test_genres_by_tag_escaped(fw):
    p = page(fw)
    curl = Curl(fw, b'name,stationcount\nrock,900\nhip hop,500\n')
    p.menu('By Genre')
    assert fx(fw).url == f'{API}/csv/tags?order=stationcount&reverse=true&limit=60'.encode()
    fetched(fw, p, curl)
    pick = ui(fw).pick
    assert [pick.at(pick.name[i]) for i in range(pick.n)] == ['hip hop', 'rock']
    p.rows('pick_click')[0]()
    p.run('go')
    assert fx(fw).url == f'{API}/m3u/stations/bytagexact/hip%20hop{QUERY}'.encode()


def test_no_wifi_no_fetch(fw):
    p = page(fw, wifi=0)
    p.menu('Top Stations')
    assert p.threads == [] and fx(fw).running == 0 and 'No Wi-Fi' in note(p) and ui(fw).note == 1


@pytest.mark.parametrize('code, error, body', [(500, 0, m3u(('A', 'http://a/'))), (200, 28, m3u(('A', 'http://a/'))),
                                               (200, 0, b'')])
def test_a_failed_fetch_says_the_directory_is_unavailable(fw, code, error, body):
    p = page(fw)
    curl = Curl(fw, body, code=code, error=error)
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert 'Directory unavailable' in note(p) and ui(fw).note == 1


def test_a_fetch_with_no_stations_says_so(fw):
    p = page(fw)
    curl = Curl(fw, b'#EXTM3U\n')
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert 'No stations' in note(p)


def test_a_body_over_256_kb_is_cut_off(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a/')) + b'#' * (300 << 10), chunk=64 << 10)
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert curl.wrote == [64 << 10] * 3 + [0] and 'Directory unavailable' in note(p)


def test_the_tick_waits_for_the_worker(fw):
    p = page(fw)
    Curl(fw)
    p.menu('Top Stations')
    assert p.run('tick') == 8 and fx(fw).running == 1 and fw.calls('fw_pthread_join') == []


def test_return_while_loading_cancels_the_transfer_and_shows_the_menu(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a/')))
    p.menu('Top Stations')
    p.key(ui(fw).page)
    p.run('leave')
    assert fx(fw).cancel == 1 and fx(fw).running == 0 and len(fw.calls('fw_pthread_join')) == 1
    assert ui(fw).level == MENU and ui(fw).note == 0 and curl.opts == {}
    # what the transfer, still running until the join, hears: stop
    progress = fw.fn('radio__fetch_progress', C.c_int, C.c_void_p, *[C.c_longlong] * 4)
    got = fw.fn('radio__fetch_got', C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_void_p)
    assert progress(None, 0, 0, 0, 0) == 1 and got(b'abc', 1, 3, None) == 0


def test_return_goes_up_a_level_each_time(fw):
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\nGermany,DE,99\n')
    p.menu('By Country')
    fetched(fw, p, curl)
    curl.body = m3u(('A', 'http://a/'))
    p.rows('pick_click')[0]()
    p.run('go')
    fetched(fw, p, curl)
    levels = []
    for _ in range(3):
        p.key(ui(fw).page)
        p.run('leave')
        levels.append(ui(fw).level)
    assert levels == [PICK, MENU, MENU] and len(fw.calls('navigator_back')) == 1


def test_each_level_keeps_its_selected_row(fw):
    p = page(fw)
    fw.mock('widget_get_prop_int', lambda w, key, default, *_: 4 if C.string_at(key) == b'_ringnav_index' else default)
    p.menu('Featured')
    assert ui(fw).sel[MENU] == 4


def test_the_page_s_end_frees_its_lists_and_stops_its_timers(fw):
    p = page(fw)
    p.menu('Featured')
    p.handler(ui(fw).page, EVT_DESTROY)()
    assert bytes(ui(fw)) == bytes(C.sizeof(Ui))


def test_a_second_open_does_nothing(fw):
    p = page(fw)
    count = len(fw.calls('window_create'))
    fw.fn('radio_open', C.c_int, C.c_void_p, C.c_void_p)(0, 0)
    assert len(fw.calls('window_create')) == count  # p's buffers stay alive to here


def test_a_second_open_arms_and_builds_nothing(fw):
    p = page(fw)
    timers, lists = len(p.timers), len(fw.calls('list_view_create'))
    fw.fn('radio_open', C.c_int, C.c_void_p, C.c_void_p)(0, 0)
    assert len(p.timers) == timers and len(fw.calls('list_view_create')) == lists


def test_the_page_s_end_stops_every_timer_it_armed(fw):
    p = page(fw)
    p.rows('menu_click')[p.menu_rows().index('Featured')]()  # a row's choice, still to come
    p.key(ui(fw).page)  # and Return
    assert p.live('tick') and p.live('go') and p.live('leave')
    p.handler(ui(fw).page, EVT_DESTROY)()
    assert (p.live('tick'), p.live('go'), p.live('leave')) == ([], [], [])


def test_closing_while_loading_ends_the_transfer(fw):
    p = page(fw)
    Curl(fw)
    p.menu('Top Stations')
    p.handler(ui(fw).page, EVT_DESTROY)()
    assert (fx(fw).cancel, fx(fw).running, fx(fw).body) == (1, 0, None) and len(fw.calls('fw_pthread_join')) == 1


def test_closing_after_a_cancelled_fetch(fw):
    """The body went with the cancel, and goes once."""
    p = page(fw)
    Curl(fw)
    p.menu('Top Stations')
    p.key(ui(fw).page)
    p.run('leave')
    p.handler(ui(fw).page, EVT_DESTROY)()
    assert fx(fw).body is None and bytes(ui(fw)) == bytes(C.sizeof(Ui))


def test_a_fetch_after_a_cancelled_one_completes(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a.example/')))
    p.menu('Top Stations')
    p.key(ui(fw).page)
    p.run('leave')
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert ui(fw).list.stations() == [('A', 'http://a.example/')]


def test_a_fetch_that_fails_after_a_good_one_says_so(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a.example/')))
    p.menu('Top Stations')
    fetched(fw, p, curl)
    p.key(ui(fw).page)
    p.run('leave')
    curl.error = 28  # CURLE_OPERATION_TIMEDOUT, part of the body in
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert ui(fw).note == 1 and 'Directory unavailable' in note(p)


def test_a_finished_fetch_s_thread_is_joined_once(fw):
    """Its tick takes the list once; the ticks after it leave the list alone."""
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a.example/')))
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert p.run('tick') == RET_REPEAT and len(fw.calls('fw_pthread_join')) == 1
    assert ui(fw).list.stations() == [('A', 'http://a.example/')] and curl.cleaned == [0x9000]


def test_the_directory_s_last_line_needs_no_newline(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a.example/'), ('B', 'http://b.example/'))[:-1])
    Player(fw)
    p.menu('Top Stations')
    fetched(fw, p, curl)
    p.rows('station_click')[1]()
    assert rd(fw).list.stations() == [('A', 'http://a.example/'), ('B', 'http://b.example/')] and rd(fw).at == 1


@pytest.mark.parametrize('extra, whole', [(0, True), (1, False)])
def test_a_reply_may_fill_256_kb_but_for_its_nul(fw, extra, whole):
    body = m3u(('A', 'http://a.example/'))
    body += b'#' * (FETCH_MAX - 1 + extra - len(body) - 1) + b'\n'
    p = page(fw)
    curl = Curl(fw, body, chunk=64 << 10)
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert (ui(fw).list.stations() == [('A', 'http://a.example/')]) == whole and ui(fw).note == (not whole)


def test_the_directory_s_csv_leaves_out_rows_it_can_t_use(fw):
    """A row without a comma, with no name or with no code; a quoted name keeps its commas."""
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\nbad row\n,XX,5\nNowhere,,3\n"Korea, Republic of",KR,10\nGermany,DE,99\n')
    p.menu('By Country')
    fetched(fw, p, curl)
    pick = ui(fw).pick
    assert [(pick.at(pick.name[i]), pick.at(pick.url[i])) for i in range(pick.n)] == [('Germany', 'DE'),
                                                                                     ('Korea, Republic of', 'KR')]


def test_a_quoted_genre_is_fetched_by_its_name(fw):
    p = page(fw)
    curl = Curl(fw, b'name,stationcount\n"rock, pop",50\njazz,40\n')
    p.menu('By Genre')
    fetched(fw, p, curl)
    assert p.captions('pick_click') == {0: 'jazz', 1: 'rock, pop'}
    p.rows('pick_click')[1]()
    p.run('go')
    assert fx(fw).url == f'{API}/m3u/stations/bytagexact/rock%2C%20pop{QUERY}'.encode()


def test_a_directory_list_holds_200_rows(fw):
    p = page(fw)
    curl = Curl(fw, b'name,stationcount\n' + b''.join(b'tag%03d,%d\n' % (i, 1000 - i) for i in range(250)))
    p.menu('By Genre')
    fetched(fw, p, curl)
    pick = ui(fw).pick
    tags = [f'tag{i:03}' for i in range(200)]
    assert [(pick.at(pick.name[i]), pick.at(pick.url[i])) for i in range(pick.n)] == list(zip(tags, tags))


def test_equal_names_keep_the_directory_s_vote_order(fw):
    """list_sort is stable: names the library sorts alike (case, a leading "The") stay in vote order."""
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\nGermany,DE,99\n')
    p.menu('By Country')
    fetched(fw, p, curl)
    curl.body = m3u(('Radio 1', 'http://first.example/'), ('Alpha', 'http://a.example/'), ('radio 1', 'http://second.example/'),
                    ('The Radio 1', 'http://third.example/'))
    p.rows('pick_click')[0]()
    p.run('go')
    fetched(fw, p, curl)
    assert [u for _, u in ui(fw).list.stations()] == ['http://a.example/', 'http://first.example/', 'http://second.example/',
                                                     'http://third.example/']


def test_a_country_s_stations_are_titled_with_its_name(fw):
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\nGermany,DE,99\n')
    p.menu('By Country')
    fetched(fw, p, curl)
    curl.body = m3u(('A', 'http://a.example/'))
    p.rows('pick_click')[0]()
    p.run('go')
    fetched(fw, p, curl)
    assert title(p) == 'Germany'


def test_a_new_directory_list_opens_on_its_first_row(fw):
    """Each fetch's list starts at the top, whatever row a list at its level was left on."""
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\nAustria,AT,9\nBelgium,BE,8\nChile,CL,7\nDenmark,DK,6\n')
    p.menu('By Country')
    fetched(fw, p, curl)
    at(p, 3)
    curl.body = m3u(('A', 'http://a.example/'), ('B', 'http://b.example/'), ('C', 'http://c.example/'))
    p.rows('pick_click')[3]()
    p.run('go')
    fetched(fw, p, curl)
    assert selected(p) == 0  # the stations
    for _ in range(2):
        p.key(ui(fw).page)
        p.run('leave')  # the countries, then the menu
    curl.body = b'name,stationcount\njazz,4\npop,3\nrock,2\nsoul,1\n'
    p.menu('By Genre')
    fetched(fw, p, curl)
    assert selected(p) == 0


def test_a_fetch_whose_thread_won_t_start_says_the_directory_is_unavailable(fw):
    """The weakest signal is still Wi-Fi."""
    p = page(fw, wifi=1)
    fw.mock('fw_pthread_create', lambda *_: 11)  # EAGAIN
    p.menu('Top Stations')
    assert fx(fw).running == 0 and ui(fw).note == 1 and 'Directory unavailable' in note(p)


def test_wi_fi_lost_during_a_fetch_says_no_wi_fi(fw):
    p = page(fw)
    curl = Curl(fw, error=6)  # CURLE_COULDNT_RESOLVE_HOST
    p.menu('Top Stations')
    p.wifi = 0
    fetched(fw, p, curl)
    assert 'No Wi-Fi' in note(p)


def test_no_curl_handle_no_transfer(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a.example/')))
    fw.mock('fw_curl_easy_init', lambda *_: 0)
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert curl.opts == {} and fw.calls('fw_curl_easy_perform') == [] and 'Directory unavailable' in note(p)


def test_return_while_connecting_ends_the_transfer_through_its_progress_callback(fw):
    """Nothing written yet, so only libcurl's progress callback (CURLOPT_XFERINFOFUNCTION, with CURLOPT_NOPROGRESS
    off) can hear the cancel: the transfer ends aborted by it, before a byte is taken."""
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a.example/')))
    p.menu('Top Stations')
    p.key(ui(fw).page)
    p.run('leave')
    C.CFUNCTYPE(C.c_void_p, C.c_void_p)(p.threads[-1])(0)  # the worker, running until the join
    assert curl.result == CURLE_ABORTED_BY_CALLBACK and curl.wrote == []


def test_the_directory_s_certificate_is_checked_without_the_clock(fw):
    """CURLOPT_SSL_CTX_FUNCTION sets X509_V_FLAG_NO_CHECK_TIME on the context: the Q2's clock may be years off, and the
    pinned roots expire."""
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a.example/')))
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert curl.flags & X509_V_FLAG_NO_CHECK_TIME and ui(fw).list.n == 1


# Search.

def search(fw, p, text, failed=0):
    """The pull-down's box with text, and its OK; failed: the text didn't fit stock's copy."""
    fw.fn('radio_search', None)()
    edit = ui(fw).edit
    p.w.text[edit] = text
    wide = (C.c_uint * 2)(ord('x'), 0)
    if failed:
        fw.mock('widget_get_text_utf8', lambda *_: 1)
        fw.mock('widget_get_text', lambda *_: C.addressof(wide))
    p.handler(edit, EVT_VALUE_CHANGED)()
    return edit


def test_a_search_fetches_by_name_escaped_in_vote_order(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('Zed FM', 'http://z/'), ('Abe FM', 'http://a/')))
    edit = search(fw, p, 'jazz & blues')
    assert edit and fw.calls('widget_set_focused')
    p.run('go')
    assert fx(fw).url == f'{API}/m3u/stations/byname/jazz%20%26%20blues{QUERY}'.encode()
    fetched(fw, p, curl)
    assert [n for n, _ in ui(fw).list.stations()] == ['Zed FM', 'Abe FM'] and ui(fw).caption == b'jazz & blues'
    p.key(ui(fw).page)
    p.run('leave')
    assert ui(fw).level == MENU  # back to where it was pulled over


def test_an_empty_search_shows_the_page_again(fw):
    p = page(fw)
    search(fw, p, '')
    p.run('go')
    assert ui(fw).edit is None and ui(fw).level == MENU and p.threads == []


def test_a_search_too_long_for_the_box_says_so(fw):
    p = page(fw)
    search(fw, p, 'x', failed=1)
    assert p.toasts == ['Search is too long']


def test_the_box_left_without_a_search_shows_the_page(fw):
    p = page(fw)
    fw.fn('radio_search', None)()
    p.handler(ui(fw).edit, EVT_BLUR)()
    p.run('go')
    assert ui(fw).edit is None and ui(fw).level == MENU


def test_return_with_the_box_up_empties_and_blurs_it(fw):
    p = page(fw)
    fw.fn('radio_search', None)()
    edit = ui(fw).edit
    p.w.text[edit] = 'abc'
    p.key(ui(fw).page)
    p.run('leave')
    assert p.w.text[edit] == '' and [a[:2] for _, a in fw.calls('widget_set_focused')][-1] == (edit, 0)


def test_no_search_over_a_note(fw):
    p = page(fw, wifi=0)
    p.menu('Top Stations')
    fw.fn('radio_search', None)()
    assert ui(fw).edit is None


def edits(p):
    return [w for w, (kind, _, _) in p.w.made.items() if kind == 'edit']


def test_no_second_box_while_one_is_up_nor_once_the_page_is_closed(fw):
    """A second box would destroy the first under its open keyboard, which crashes demo."""
    p = page(fw)
    fw.fn('radio_search', None)()
    box, cleared = edits(p), len(fw.calls('widget_destroy_children'))
    fw.fn('radio_search', None)()
    assert edits(p) == box and len(fw.calls('widget_destroy_children')) == cleared
    p.handler(ui(fw).page, EVT_DESTROY)()
    fw.fn('radio_search', None)()
    assert ui(fw).edit is None and len(fw.calls('widget_destroy_children')) == cleared


def test_the_box_takes_the_page_and_the_keyboard(fw):
    """The rows go (the keyboard leaves no room for them); a focused text box with a hint, its keyboard's OK taken
    as its own (an empty OK sends nothing else)."""
    p = page(fw)
    fw.fn('radio_search', None)()
    edit = ui(fw).edit
    assert ui(fw).view is None and p.w.under(ui(fw).page) == [edit]
    assert p.w.strs[(edit, b'input_type')] == b'text' and p.w.strs[(edit, b'tips')]
    assert [a[:2] for _, a in fw.calls('widget_set_focused')] == [(edit, 1)]
    events = {evt for _, (w, evt, fn, *_) in fw.calls('widget_on') if w == edit and fn == p.fn('searched')}
    assert events == {EVT_VALUE_CHANGED, EVT_IM_ACTION}


def test_an_empty_ok_says_nothing_and_shows_the_page(fw):
    p = page(fw)
    fw.fn('radio_search', None)()
    nothing = (C.c_uint * 1)(0)
    fw.mock('widget_get_text', lambda *_: C.addressof(nothing))
    p.handler(ui(fw).edit, EVT_IM_ACTION)()  # the keyboard's OK, nothing typed
    p.run('go')
    assert p.toasts == [] and ui(fw).edit is None and ui(fw).level == MENU and p.threads == []


def test_the_keyboard_closing_after_ok_still_searches(fw):
    p = page(fw)
    Curl(fw)
    edit = search(fw, p, 'jazz')
    p.handler(edit, EVT_BLUR)()
    p.run('go')
    assert fx(fw).url == f'{API}/m3u/stations/byname/jazz{QUERY}'.encode() and fx(fw).running == 1


def test_the_box_left_puts_the_list_back_on_its_row(fw):
    p = page(fw)
    p.menu('Featured')  # a row's choice has come and gone
    at(p, 3)
    fw.fn('radio_search', None)()
    p.handler(ui(fw).edit, EVT_BLUR)()
    p.run('go')
    assert ui(fw).level == STATIONS and len(p.rows('station_click')) == 6 and selected(p) == 3


def test_the_box_s_blur_after_the_page_closed_does_nothing(fw):
    p = page(fw)
    fw.fn('radio_search', None)()
    blur = p.handler(ui(fw).edit, EVT_BLUR)
    p.handler(ui(fw).page, EVT_DESTROY)()
    blur()
    assert p.live('go') == []


@pytest.mark.parametrize('over, back', [('By Country', PICK), ('Featured', MENU)])
def test_return_from_search_results_goes_back_to_the_menu_or_the_country_list(fw, over, back):
    """The results take the station list's place, so Return goes where a station list would."""
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\nGermany,DE,99\n')
    p.menu(over)
    if over == 'By Country':
        fetched(fw, p, curl)
    curl.body = m3u(('A', 'http://a.example/'))
    search(fw, p, 'jazz')
    p.run('go')
    fetched(fw, p, curl)
    p.key(ui(fw).page)
    p.run('leave')
    assert ui(fw).level == back


def test_return_from_a_search_selects_the_row_it_was_pulled_over(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a.example/')))
    at(p, 4)
    search(fw, p, 'jazz')
    p.run('go')
    fetched(fw, p, curl)
    at(p, 0)
    p.key(ui(fw).page)
    p.run('leave')
    assert ui(fw).level == MENU and selected(p) == 4


def test_return_from_loading_selects_the_row_that_opened_it(fw):
    p = page(fw)
    Curl(fw)
    at(p, 2)  # Top Stations, the third row with no Now Playing
    p.menu('Top Stations')
    at(p, 0)
    p.key(ui(fw).page)
    p.run('leave')
    assert ui(fw).level == MENU and selected(p) == 2


def test_return_while_a_search_loads_cancels_it(fw):
    """The box went with the note over it: Return leaves the note, as for any fetch."""
    p = page(fw)
    Curl(fw)
    search(fw, p, 'jazz')
    p.run('go')
    p.key(ui(fw).page)
    p.run('leave')
    assert (fx(fw).cancel, ui(fw).note, ui(fw).level, ui(fw).edit) == (1, 0, MENU, None)


def test_search_results_over_favourites_are_no_favourites_list(fw):
    """A toggle on them rebuilds nothing."""
    p = page(fw, {FAVS: m3u(('One', 'http://1.example/'))})
    curl = Curl(fw, m3u(('A', 'http://a.example/')))
    p.menu('Favourites')
    search(fw, p, 'a')
    p.run('go')
    fetched(fw, p, curl)
    assert hold(fw, p, 0) == 'Added to Favourites' and p.live('go') == []


def test_a_search_is_percent_encoded_as_rfc_3986_says(fw):
    """Letters, digits and -._~ go as they are (RFC 3986's unreserved); every other byte as %XX, in upper case."""
    text = 'AZaz09-._~ @[`{/:!#?é'
    p = page(fw)
    search(fw, p, text)
    p.run('go')
    assert fx(fw).url == f'{API}/m3u/stations/byname/{urllib.parse.quote(text, safe="")}{QUERY}'.encode()


@pytest.mark.parametrize('text', ['a' + ' ' * 62, 'ab' + ' ' * 61])
def test_the_longest_search_is_cut_at_a_whole_escape(fw, text):
    """The box holds 63 bytes (the caption's room), all searched; their escape goes into a 128-byte key: as many whole
    escapes as fit before its NUL."""
    p = page(fw)
    search(fw, p, text)
    p.run('go')
    key = ''
    for unit in [urllib.parse.quote(c, safe='') for c in text]:
        if len(key) + len(unit) > 127:
            break
        key += unit
    assert ui(fw).caption == text.encode() and fx(fw).url == f'{API}/m3u/stations/byname/{key}{QUERY}'.encode()


# Now Playing.

def now_playing(fw, p):
    np = np_(fw)
    return {k: p.w.text.get(getattr(np, k), '') for k in ('station', 'artist', 'song', 'info', 'elapsed')}


def test_now_playing_shows_the_station_and_its_stream(fw):
    p = page(fw)
    player = play(fw, p, 1)
    shown = now_playing(fw, p)
    assert shown == {'station': 'SomaFM Groove Salad', 'artist': '', 'song': '', 'info': 'Connecting…', 'elapsed': ''}
    assert 'Internet Radio' in p.w.text.values()
    player.ends = False
    state = rd(fw)
    state.title, state.codec, state.bitrate, state.since = b'Air - La Femme d\'Argent', b'MP3', b'128', 100000 - 65000
    fw.mock('toolsTimeItoa', lambda out, sec, *_: C.memmove(out, b'%d\0' % sec, len(b'%d' % sec) + 1))
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert p.run('np_tick') == 8
    assert now_playing(fw, p) == {'station': 'SomaFM Groove Salad', 'artist': 'Air', 'song': 'La Femme d\'Argent',
                                  'info': 'MP3  128 kbps', 'elapsed': '65'}


@pytest.mark.parametrize('state, error, bitrate, info', [(b'stopped', b'', b'', 'Stopped'),
                                                         (b'error', b'open: Device busy', b'', "Can't play: open: Device busy"),
                                                         (b'error', b'', b'', "Can't play this station")])
def test_now_playing_says_why_nothing_plays(fw, state, error, bitrate, info):
    p = page(fw)
    play(fw, p)
    r = rd(fw)
    r.pid, r.state, r.error = 0, state, error
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    assert now_playing(fw, p)['info'] == info and now_playing(fw, p)['elapsed'] == ''


def test_a_stream_without_a_bitrate_shows_its_codec(fw):
    p = page(fw)
    play(fw, p)
    r = rd(fw)
    r.codec, r.since = b'AAC', 99000
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    assert now_playing(fw, p)['info'] == 'AAC'


def test_the_screen_off_leaves_now_playing_alone(fw):
    p = page(fw)
    play(fw, p)
    rd(fw).title = b'A - B'
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    assert p.run('np_tick') == 8 and now_playing(fw, p)['song'] == ''


def hearts(fw):
    return [C.string_at(a[1]).decode() for _, a in fw.calls('image_base_set_image') if a[0] == np_(fw).heart]


def test_the_heart_is_the_station_in_favourites_and_a_tap_toggles_it(fw):
    p = page(fw)
    play(fw, p, 4)
    assert hearts(fw) == ['play_unfav']
    p.handler(np_(fw).heart, EVT_CLICK)()
    assert p.toasts == ['Added to Favourites'] and p.card.files[FAVS] == m3u(('FIP', 'http://icecast.radiofrance.fr/fip-midfi.mp3'))
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    p.run('np_tick')  # favourites.m3u read again only when the serial moves
    assert hearts(fw) == ['play_unfav', 'play_fav']


def test_now_playing_from_the_menu_after_a_restart_plays_last(fw):
    p = page(fw, {LAST: m3u(('KEXP', 'https://kexp.example/128.mp3'))})
    player = Player(fw)
    p.menu('Now Playing')
    assert player.started == [4242] and rd(fw).list.stations() == [('KEXP', 'https://kexp.example/128.mp3')]
    assert np_(fw).page and now_playing(fw, p)['station'] == 'KEXP'


def test_now_playing_from_the_menu_while_playing_just_opens(fw):
    p = page(fw)
    player = play(fw, p, 2)
    p.key(np_(fw).page)
    p.run('np_leave')
    assert len(fw.calls('navigator_back')) == 1
    p.handler(np_(fw).page, EVT_DESTROY)()
    assert bytes(np_(fw)) == bytes(C.sizeof(Np))
    p.key(ui(fw).page)  # Featured back to the menu, which now has Now Playing
    p.run('leave')
    assert p.menu_rows() == MENU_ROWS
    p.menu('Now Playing')
    assert player.started == [4242] and np_(fw).page


def test_the_session_moves_with_each_start_and_favourites_change(fw):
    """headset.c's resume is for the same session: another station, or the heart, is another."""
    p = page(fw)
    session = fw.fn('radio_session', C.c_uint)
    play(fw, p, 0)
    first = session()
    media(fw, KEY_FWD_BTN)
    second = session()
    fw.fn('radio_hold', C.c_char_p, C.c_void_p)(np_(fw).page)
    assert len({first, second, session()}) == 3


def test_classic_s_now_playing_is_the_local_page_s(fw):
    def read(path, section, key, out, default, *_):
        v = b'1' if C.string_at(key) == b'THEME' else C.string_at(default)
        C.memmove(out, v + b'\0', len(v) + 1)
    fw.mock('toolsReadConfig', read)
    p = page(fw)
    play(fw, p, 0)
    arts = [C.string_at(a[1]).decode() for _, a in fw.calls('image_base_set_image') if a[0] != np_(fw).heart]
    assert np_(fw).ipod == 1 and arts[-2:] == ['stream_radio', 'classic_light_station']  # after the rows' arrows
    name = [w for w, t in p.w.text.items() if t == 'Internet Radio'][-1]  # the page's, after the menu's title
    assert (name, 0) in [a[:2] for _, a in fw.calls('widget_set_visible')]  # Classic's bar names the page


def test_held_on_now_playing_toggles_the_station_playing(fw):
    p = page(fw)
    play(fw, p, 5)
    assert fw.fn('radio_hold', C.c_char_p, C.c_void_p)(np_(fw).page) == b'Added to Favourites'
    assert p.card.files[FAVS] == m3u(('NTS 1', 'https://stream-relay-geo.ntslive.net/stream'))


def test_a_stream_title_without_a_dash_is_the_song_alone(fw):
    p = page(fw)
    play(fw, p).ends = False
    rd(fw).title = b'Station ID'
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    assert (now_playing(fw, p)['artist'], now_playing(fw, p)['song']) == ('', 'Station ID')


@pytest.mark.parametrize('playing, for_s, elapsed', [(1, 0, '0'), (1, 3600, '3600'), (0, 65, ''), (1, None, '')])
def test_the_running_time(fw, playing, for_s, elapsed):
    """Seconds since q2video's at= while the stream plays, from its first; none while it connects (no at= yet) or once
    it has stopped."""
    p = page(fw)
    play(fw, p).ends = False
    p.clock.now = 10_000_000
    fw.mock('toolsTimeItoa', lambda out, sec, *_: C.memmove(out, b'%d\0' % C.c_int(sec).value, len(b'%d' % C.c_int(sec).value) + 1))
    if for_s is not None:
        rd(fw).since = p.clock.now - for_s * 1000
    if not playing:
        media(fw, KEY_PLAY)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    assert now_playing(fw, p)['elapsed'] == elapsed


def test_a_refresh_writes_only_the_labels_that_changed(fw):
    """set(): a scrolling label keeps its place unless its text changes."""
    p = page(fw)
    play(fw, p).ends = False
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    before = len(fw.calls('widget_set_text_utf8'))
    rd(fw).title = b'Air - Alpha'
    p.run('np_tick')
    assert {a[0] for _, a in fw.calls('widget_set_text_utf8')[before:]} == {np_(fw).artist, np_(fw).song}


def look(p, w):
    """A label's box, styles, size and colour as made."""
    return (p.w.made[w][2], p.w.styles.get(w), p.w.ints.get((w, b'style:normal:font_size')),
            p.w.ints.get((w, b'style:normal:text_color'), 0) & 0xffffffff)


def test_now_playing_has_spotify_s_layout(fw):
    """docs/internals.md: "Internet Radio" above, the art, the station, artist and song in Spotify's title, artist and
    album rows, the stream from where its elapsed time starts to the running time, which is in its remaining time's
    place."""
    p = page(fw)
    play(fw, p)
    np = np_(fw)
    radio = p.w.under(np.page, 'hscroll_label_create')
    mark = len(p.w.made)
    fw.fn('spotify__spot_np', None)()
    spotify = [w for w in list(p.w.made)[mark:] if p.w.made[w][0] == 'hscroll_label_create']
    name, track, artist, album, elapsed, remain = spotify[:6]
    assert [look(p, w) for w in (radio[0], np.station, np.artist, np.song, np.elapsed)] == \
        [look(p, w) for w in (name, track, artist, album, remain)]
    (x, y, w, h), *rest = look(p, np.info)
    (ex, ey, _, eh), *erest = look(p, elapsed)
    assert ((x, y, h), *rest) == ((ex, ey, eh), *erest) and x + w == p.w.made[np.elapsed][2][0]
    art = [w for w in p.w.under(np.page, 'image_create') if w != np.heart]
    spot_art = [w for w in list(p.w.made)[mark:] if p.w.made[w][0] == 'image_create']
    assert p.w.made[art[0]][2] == p.w.made[spot_art[0]][2]


def test_the_art_and_the_heart_are_local_now_playing_s(fw):
    """The row's icon never scaled up in the art box; local Now Playing's heart (tools/ipod.py's img_fav): a stock
    50 px icon at the icons' row's end, its glyph's foot (NP_ICON_PAD above its box's) level with the art's, its image
    centred, and clickable, as an image isn't by default. The page refreshes every 500 ms."""
    p = page(fw)
    play(fw, p)
    np = np_(fw)
    art = [w for w in p.w.under(np.page, 'image_create') if w != np.heart][0]
    draw = {a[0]: a[1] for _, a in fw.calls('image_set_draw_type')}
    assert draw[art] == O['IMAGE_DRAW_SCALE_DOWN'] and draw[np.heart] == O['IMAGE_DRAW_CENTER']
    _, art_y, _, art_h = p.w.made[art][2]
    assert p.w.made[np.heart][2] == (ipod.NP_ICONS_END - ipod.NP_ICON, art_y + art_h + ipod.NP_ICON_PAD - ipod.NP_ICON,
                                     ipod.NP_ICON, ipod.NP_ICON)
    assert p.w.strs[(np.heart, b'clickable')] == b'true'
    assert [ms for f, ms in p.timers if f == p.fn('np_tick')] == [500]


@pytest.mark.parametrize('for_s, box', [(65, 'NP_IPOD_TIME_W2'), (3599, 'NP_IPOD_TIME_W2'), (3600, 'NP_IPOD_TIME_W')])
def test_classic_s_running_time_box(fw, for_s, box):
    """Classic: boxes for "-59:59" (NP_IPOD_TIME_W2) until the stream has played an hour, then for the hours."""
    classic(fw)
    p = page(fw)
    play(fw, p).ends = False
    p.clock.now = 10_000_000
    rd(fw).since = p.clock.now - for_s * 1000
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    assert [a[3] for _, a in fw.calls('widget_move_resize') if a[0] == np_(fw).elapsed][-1] == O[box]


def test_classic_s_rows_are_the_local_page_s_four(fw):
    """The station, artist, song and stream (the "3 of 12" row) in one column, each wholly under the one before, the
    station in the theme's face for the local page's title (theme_font)."""
    classic(fw)
    p = page(fw)
    play(fw, p)
    np = np_(fw)
    moved = {a[0]: a[1:5] for _, a in fw.calls('widget_move_resize')}
    rows = [moved[w] for w in (np.station, np.artist, np.song, np.info)]
    assert len({x for x, *_ in rows}) == 1
    assert all(y + h <= below for (_, y, _, h), (_, below, *_) in zip(rows, rows[1:]))
    assert p.w.strs.get((np.station, b'style:normal:font_name'))


def test_stock_s_now_playing_keeps_its_own_layout(fw):
    """Nothing moved (stream_ipod's and ipod_times' are Classic's), "Internet Radio" shown over the station's icon."""
    p = page(fw)
    play(fw, p).ends = False
    rd(fw).since = p.clock.now - 65000
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    np = np_(fw)
    name = p.w.under(np.page, 'hscroll_label_create')[0]
    art = [w for w in p.w.under(np.page, 'image_create') if w != np.heart][0]
    assert not [a for _, a in fw.calls('widget_move_resize') if a[0] in p.w.under(np.page)]
    assert (name, 0) not in [a[:2] for _, a in fw.calls('widget_set_visible')]
    assert [C.string_at(a[1]) for _, a in fw.calls('image_base_set_image') if a[0] == art] == [b'stream_radio']


def test_a_second_station_tap_under_now_playing_opens_no_second_page(fw):
    """A tap queued for the list before Now Playing covered it plays its station on the page already up."""
    p = page(fw)
    play(fw, p, 0)
    pages, labels = len(fw.calls('window_create')), len(p.w.under(np_(fw).page))
    p.rows('station_click')[1]()
    assert rd(fw).at == 1 and len(fw.calls('window_create')) == pages and len(p.w.under(np_(fw).page)) == labels


def test_now_playing_s_end_stops_its_timers(fw):
    p = page(fw)
    play(fw, p)
    p.key(np_(fw).page)  # Return, still to come
    assert p.live('np_tick') and p.live('np_leave')
    p.handler(np_(fw).page, EVT_DESTROY)()
    assert (p.live('np_tick'), p.live('np_leave')) == ([], [])


def test_the_pages_go_by_the_names_the_firmware_knows_them_by(fw):
    """headset.c's media keys: while radio_page or radionp_page is on top the radio's even over a playing video, as a
    focused streaming page takes them (test_headset.py), and the video's under any other page. The title bar names
    Now Playing by its window's name."""
    p = page(fw)
    player = play(fw, p)
    other = p.w.new(0, 'window_create')
    p.w.names[other] = 'playing_page'  # local Now Playing
    top = [ui(fw).page]
    names = {}
    def name(w, key, default, *_):
        if C.string_at(key) != b'name' or w not in p.w.names:
            return default
        return C.addressof(names.setdefault(w, C.create_string_buffer(p.w.names[w].encode())))
    fw.mock('widget_get_prop_str', name)
    fw.mock('window_manager_get_top_window', lambda *_: top[0])
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    fw.var('books__vid', Vid).pid = 4321
    def forward(window):
        """Next pressed with window on top: the station then playing and what the video was sent."""
        top[0] = window
        fw.fn('media_key', None, C.c_uint)(KEY_FWD_BTN)
        return rd(fw).at, [data for path, data in player.sent if path == b'/tmp/q2video.sock']
    assert forward(ui(fw).page) == (1, [])
    assert forward(other) == (1, [b'f'])
    assert forward(np_(fw).page) == (2, [b'f'])
    bar_title_of, label = fw.fn('navigation__bar_title_of', C.c_char_p, C.c_void_p, C.c_void_p), C.c_void_p()
    assert bar_title_of(np_(fw).page, C.byref(label)) == b'Internet Radio'


def test_nothing_read_is_kept(fw):
    """Every list, favourites read, reply and copy is freed once done with: a round of the page adds nothing to the
    heap after the first (favourites.m3u, a reply and the countries each 64 KB, saves failing so it stays so). The
    least of three rounds counts, each from a collected Python heap, so no free of another's makes up a leak."""
    big = b'#' * (64 << 10) + b'\n'
    favs = m3u(('One', 'http://1.example/'), ('Two', 'http://2.example/')) + big
    p = page(fw, {FAVS: favs})
    p.card.fail_rename = True
    curl = Curl(fw)
    player = Player(fw)
    player.ends = False
    click = {n: fw.fn('radio__' + n, C.c_int, C.c_void_p, C.c_void_p)
             for n in ('menu_click', 'station_click', 'np_heart', 'np_closed', 'closed')}
    leave = fw.fn('radio__leave', C.c_int, C.c_void_p)
    def choose(r):
        click['menu_click'](list(ui(fw).rows).index(r), None)
        p.run('go')
    def fetch(body):
        curl.body = body
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(p.threads[-1])(0)
        p.run('tick')
    def round_():
        fw.fn('radio_open', C.c_int, C.c_void_p, C.c_void_p)(0, 0)
        choose(R_FAVS)
        hold(fw, p, 0)
        click['station_click'](1, None)
        click['np_heart'](None, None)
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        p.run('np_tick')
        click['np_closed'](None, None)
        leave(None)
        choose(R_TOP)
        fetch(m3u(('A', 'http://a.example/')) + big)
        leave(None)
        choose(R_COUNTRY)
        fetch(b'name,iso_3166_1,stationcount\nGermany,DE,99\n' + big)
        leave(None)
        choose(R_TOP)
        leave(None)  # while loading
        click['closed'](None, None)
    round_()
    grew = []
    for _ in range(3):
        gc.collect()
        used = heap()
        round_()
        grew.append(heap() - used)
    assert min(grew) < 32 << 10 and p.card.files[FAVS] == favs
