"""End to end, from a card's files to what plays: Update Local Music and the folder markers (library_scope.c), the
Library's lists (navigation.c, coverflow.c), a pick, the queue's order, Up Next and Play Next (playback.c, upnext.c),
a restart's resume (navigation.c resume_poll, playback.c's snapshot), the gapless preload (playback.c), Crossfade's
choice of fade (crossfade.c), an audiobook's chapters (chapters.c) and a book's page (books.c).

The firmware runs through the entry points the build hooks into stock (tools/build.py HOOKS: a stock call the build
hooks reaches the payload's hook here too), and each hook's trampoline reaches a small model of stock's own body. Only
stock is stood in for (Q2 below): its database as sqlite3 over the card's files, its player as a queue position and a
clock, its pages as widgets that remember their text. Every journey is a chain: what one step leaves, the next uses."""
import ctypes as C
import random
import re
import sqlite3
import struct

import pytest

import host
from conftest import NAVIGATION_SOURCE, FNV_PRIME, O, Deques, fnv1a, high_halves, libc

libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
FIXED = 0x100022  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
STRINGS = 'name path title album artist genre composer album_artist cover'.split()  # REC_NAME on, a word apart
RESUME_FILE, XFADE_FILE = '/mnt/data/ringnav-resume', '/tmp/q2xfade'
SONGS, ALBUMS, ALBUM = 0xf001, 0xf003, 0xff10  # stock's list classes: all songs, all albums, an album's songs
STOPPED, PLAYING, PAUSED = 1, 2, 3
LIST_PLAY, REPEAT_ONE, SHUFFLE, REPEAT_ALL = range(4)  # stock's play modes (MCL_MODE)
AUDIO = ('.flac', '.mp3', '.m4a', '.m4b', '.dsf', '.ogg')
ROW = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.POINTER(C.c_char_p))  # sqlite3_exec's
NAVIGATION = NAVIGATION_SOURCE


def enum(prefix):
    """One of navigation.c's enums from 1, by name: the queue menu's row kinds (QM_) and actions (QA_)."""
    body = host.tools_build.need(re.search(r'enum \{\s*(' + prefix + r'\w+ = 1,.*?)\};', NAVIGATION, re.S), prefix)[1]
    return {name: i + 1 for i, name in enumerate(re.findall(prefix + r'(\w+)', body))}


QM, QA = enum('QM_'), enum('QA_')
# Stock entry points the build hooks (tools/build.py HOOKS) that these journeys reach: here too each goes to its hook.
HOOKED = ('mclLoadPlayList', 'mclNextSong', 'mclPrevSong', 'mclStartPlayer', 'player_start', 'player_start_queue',
          'player_change_music', 'player_seek_time', 'player_seek_start', 'player_play_pause', 'toolsQueryDbTable',
          'load_localclass_list', 'load_album_detaillist', 'scanAllMusicFile', 'memeory_startplayer',
          'save_memoryplay_info', 'mcl_preload', 'mcl_open_preload', 'mclSetPlayMode', 'mclSetPlaySpeed')


def key(path):
    """A place's key: the path's FNV-1a with its terminator (navigation.c fnv)."""
    return fnv1a(path.encode()) * FNV_PRIME & 0xffffffff


class Memory:
    """Stock's records as the firmware reads them here. Each lies below 2 GB, as crossfade.c keeps one in an int (in a
    MAP_32BIT block, wherever the kernel finds room). Its string fields lie 4 bytes apart (stock's 32-bit layout) and
    each read takes 8, so a field's low word is its string's and the next field's low word that string's high half:
    every word from the path's on names a 4 GB block of its own (in BLOCK, this file's high halves:
    conftest.high_halves), where its string sits."""
    BLOCK, SIZE = high_halves(16 * 256), 0x60

    def __init__(self):
        self.maps, self.n = [], 0
        self.low = libc.mmap(0, 0x10000, 3, 0x62, -1, 0)  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT
        assert self.low and self.low + 0x10000 <= 1 << 31
        self.maps.append((self.low, 0x10000))

    def map(self, at, size):
        assert libc.mmap(at, size, 3, FIXED, -1, 0) == at, hex(at)
        self.maps.append((at, size))

    def record(self, kind=8, id_=0, track=0, disc=0, cue=(0, 0), **strings):
        assert self.n < 256
        j, r = self.n, self.low + self.n * self.SIZE
        self.n += 1
        words = [0x100] + [self.BLOCK[16 * j + k] for k in range(9)]
        for k, name in enumerate(STRINGS):
            value = strings.pop(name, '').encode()
            at = words[k + 1] << 32 | words[k]
            self.map(at & ~0xfff, 0x2000)
            C.memmove(at, value + b'\0', len(value) + 1)
        assert not strings, strings
        body = (C.c_char * self.SIZE).from_address(r)
        struct.pack_into('<10I', body, O['REC_NAME'], *words)
        for field, value in (('ID', id_), ('TYPE', kind), ('TRACK', track), ('DISC', disc), ('CUE_START', cue[0]),
                             ('CUE_END', cue[1])):
            struct.pack_into('<i', body, O['REC_' + field], value)
        return r

    def close(self):
        for at, size in self.maps:
            libc.munmap(at, size)


def text_of(r, field):
    p = C.c_void_p.from_address(r + O['REC_' + field.upper()]).value
    return C.string_at(p).decode() if p else None


def int_of(r, field):
    return C.c_int.from_address(r + O['REC_' + field.upper()]).value


class Dirent(C.Structure):  # test/fw/host.py's
    _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]


class Disk:
    """The card and the flash: files by path, folders as their paths imply; stdio, open/write/mmap, stat, access and the
    folder listing over them. A restart hands the same files to the next firmware."""
    def __init__(self, fw, files):
        self.files = files
        self.handles, self.dirs, self.fds, self.shared = {}, {}, {}, {}
        self.errno, self.n = C.c_int(2), 0x2000
        for name in ('fopen', 'fread', 'fwrite', 'fseek', 'ftell', 'fclose', 'rename', 'unlink', 'access', '__xstat',
                     'opendir', 'readdir', 'open', 'write', 'close', 'mmap'):
            fw.mock('fw_' + name, getattr(self, name.strip('_')))
        for name in ('fflush', 'fsync', 'fileno', 'munmap', 'closedir'):
            fw.mock('fw_' + name, lambda *_: 0)
        fw.mock('fw___errno_location', lambda *_: C.addressof(self.errno))

    def handle(self):
        self.n += 1
        return self.n

    def folder(self, path):
        return any(f.startswith(path + '/') for f in self.files)

    def fopen(self, path, mode, *_):
        path, mode = C.string_at(path).decode(), C.string_at(mode).decode()
        if 'r' in mode and path not in self.files:
            return 0
        f = self.handle()
        self.handles[f] = [path, 0, b'' if 'w' in mode else None]
        return f

    def fread(self, out, size, count, f, *_):
        state = self.handles[f]
        data = self.files[state[0]]
        have = max(0, min(count, (len(data) - state[1]) // size)) if size else 0
        C.memmove(out, data[state[1]:state[1] + have * size], have * size)
        state[1] += have * size
        return have

    def fwrite(self, data, size, count, f, *_):
        self.handles[f][2] += C.string_at(data, size * count)
        return count

    def fseek(self, f, offset, whence, *_):
        state = self.handles[f]
        target = C.c_long(offset).value + {0: 0, 1: state[1], 2: len(self.files[state[0]])}[whence]
        if target < 0:
            return -1
        state[1] = target
        return 0

    def ftell(self, f, *_):
        return self.handles[f][1]

    def fclose(self, f, *_):
        path, _, written = self.handles.pop(f)
        if written is not None:
            self.files[path] = written
        return 0

    def rename(self, old, new, *_):
        old, new = C.string_at(old).decode(), C.string_at(new).decode()
        if old not in self.files:
            return -1
        self.files[new] = self.files.pop(old)
        if old in self.shared:
            self.shared[new] = self.shared.pop(old)
        return 0

    def unlink(self, path, *_):
        return 0 if self.files.pop(C.string_at(path).decode(), None) is not None else -1

    def access(self, path, mode, *_):
        path = C.string_at(path).decode()
        return 0 if path in self.files or self.folder(path) else -1

    def xstat(self, version, path, out, *_):
        path = C.string_at(path).decode()
        words = C.cast(out, C.POINTER(C.c_uint))
        if path in self.files:
            words[5], words[12] = 0o100644, len(self.files[path])
        elif self.folder(path):
            words[5], words[12] = 0o40755, 4096
        else:
            return -1
        return 0

    def opendir(self, path, *_):
        path = C.string_at(path).decode()
        if not self.folder(path):
            return 0
        entries = {}
        for f in self.files:
            if f.startswith(path + '/'):
                rest = f[len(path) + 1:]
                entries.setdefault(rest.split('/')[0], 4 if '/' in rest else 8)
        d = self.handle()
        self.dirs[d] = [list(entries.items()), Dirent()]
        return d

    def readdir(self, d, *_):
        entries, entry = self.dirs[d]
        if not entries:
            return 0
        name, kind = entries.pop(0)
        entry.d_name, entry.d_type = name.encode(), kind
        return C.addressof(entry)

    def open(self, path, flags, mode, *_):
        fd = self.handle()
        self.fds[fd] = C.string_at(path).decode()
        self.files[self.fds[fd]] = b''
        return fd

    def write(self, fd, data, n, *_):
        if fd not in self.fds:
            return -1
        self.files[self.fds[fd]] += C.string_at(data, n)
        return n

    def close(self, fd, *_):
        self.fds.pop(fd, None)
        return 0

    def mmap(self, at, size, prot, flags, fd, *_):
        if fd not in self.fds:
            return -1
        cell = self.shared[self.fds[fd]] = C.c_int()
        return C.addressof(cell)


class Library:
    """Stock's database.db: songtable in sqlite3, which a scan fills from the card's audio files and their tags, one
    row a CUE track, and toolsQueryDbTable, which runs a query and calls its row callback with each row's text, as
    sqlite3_exec does."""
    COLUMNS = ('id integer primary key, fileurl, filename, title, album, songer, albumsonger, genre, composer, track, '
               'cd_num, year, cue_start, cue_end, time_create')

    def __init__(self, tags):
        self.tags = tags
        self.db = sqlite3.connect(':memory:')
        self.db.execute(f'create table songtable ({self.COLUMNS})')

    def scan(self, files):
        self.db.execute('delete from songtable')
        for path in files:
            if not path.lower().endswith(AUDIO) or not path.startswith('/mnt/mmc/'):
                continue
            t = self.tags.get(path, {})
            for start, end, title in t.get('cue', [(0, 0, t.get('title', ''))]):
                self.db.execute('insert into songtable (fileurl, filename, title, album, songer, albumsonger, genre, '
                                'composer, track, cd_num, year, cue_start, cue_end, time_create) values '
                                '(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                (path, path.rsplit('/', 1)[1], title, t.get('album', ''), t.get('artist', ''),
                                 t.get('album_artist', ''), '', '', t.get('track', 0), t.get('disc', 0), t.get('year', ''),
                                 start, end, 0))

    def query(self, sql, row, ctx):
        rows = self.db.execute(sql)
        names = [(d[0] or '').encode() for d in rows.description]
        n = 0
        for values in rows.fetchall():
            argv = (C.c_char_p * len(names))(*[None if v is None else str(v).encode() for v in values])
            cols = (C.c_char_p * len(names))(*names)
            ROW(row)(ctx, len(names), argv, cols)
            n += 1
        return n


class Q2:
    """Stock's side of a Q2 these journeys walk: the card, its library, the player, the pages, the clock, config.ini.
    A stock entry point the build hooks goes to its hook (HOOKED); the hook's trampoline, stock's own body, is the
    model's. What the user would notice is kept: the files the player was told to open (opened), the seconds it was
    told to seek to (seeks), the toasts, the pages opened and each widget's text."""
    def __init__(self, fw, mcl, scratch, memory, files, tags=None, config=None, library=None):
        self.fw, self.mcl, self.scratch, self.memory = fw, mcl, scratch, memory
        self.disk = Disk(fw, files)
        self.library = library or Library(tags or {})
        self.config = dict(config or {})
        self.ms = 1_000_000
        self.status, self.file_sec, self.start_seek = STOPPED, 0, 0
        self.opened, self.seeks, self.toasts, self.pages, self.texts = [], [], [], [], {}
        self.blocks, self.keep, self.made, self.children, self.handlers, self.np = [], [], [], {}, {}, {}
        self.timers, self.timer_n = {}, 0
        self.preloads, self.gapless, self.sent, self.speeds = [], [], [], []
        self.random = random.Random(7)
        self.deques = Deques(fw)
        self.pool, self.queue, self.showlist, self.staging = (self.deques.new() for _ in range(4))
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.queue
        fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = self.showlist
        fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value = self.staging
        C.c_uint64.from_address(O['MCL_POOL']).value = self.pool
        mcl.fd = mcl.lastpos = mcl.prepos = -1
        self.song_row, self.album_row = ROW(self.make_song), ROW(self.make_album)
        self.hooks = {}
        for name in HOOKED:
            hook = getattr(fw.lib, host.tools_build.HOOKS[name][1])
            hook.restype, hook.argtypes = C.c_long, [C.c_long] * 6
            self.hooks[name] = lambda *a, hook=hook: C.c_int(hook(*a, *[0] * (6 - len(a)))).value
            if name in fw.stubs:
                fw.mock(name, self.hooks[name])
        model = {
            # the player (mcl and player_*)
            'stock_load_trampoline': self.load, 'stock_start_player_trampoline': self.start,
            'stock_next_trampoline': self.next_song, 'stock_prev_trampoline': self.prev_song,
            'stock_change_trampoline': self.change, 'stock_seek_start_trampoline': self.seek_start,
            'stock_seek_time_trampoline': self.seek, 'stock_player_start_trampoline': self.player_start,
            'stock_start_queue_trampoline': self.start_queue, 'stock_pause_trampoline': self.pause,
            'stock_preload_trampoline': self.preload, 'stock_open_preload_trampoline': self.open_preload,
            'stock_mode_trampoline': self.mode,
            'config_playmode': lambda mode, *_: self.call('mclSetPlayMode', mode),
            'stock_speed_trampoline': lambda mode, *_: self.speeds.append(mode),
            'fw_send': lambda fd, data, n, *_: self.sent.append(C.string_at(data, n)) or n,
            'mclStop': self.stop, 'mclGetPlayStatus': lambda *_: self.status, 'mclGetPlayTime': self.play_time,
            'player_playtime_and_length': self.track_time, 'player_playtime': lambda *_: self.elapsed() or 0,
            'mclSetStartSeekTime': self.start_at, 'mclSetSeek': self.seek_file, 'mcl_shuffle_pick': self.shuffle_pick,
            # the library: its scan, queries and lists
            'stock_scan_all_trampoline': self.scan, 'stock_dbquery_trampoline': self.dbquery,
            'getAllMusic': self.all_music, 'getAllAlbum': self.all_albums, 'getMusicByAlbum': self.by_album,
            'album_row': lambda ctx, n, v, c, *_: self.make_album(ctx, n, C.cast(v, C.POINTER(C.c_char_p)), 0),
            'stock_localclass_trampoline': self.localclass, 'stock_detail_trampoline': self.album_detail,
            'toolsLoadDirectory': self.load_directory,
            # pages and the rest
            'navigator_to_with_context': self.navigate, 'navigator_switch_to_with_context': self.navigate,
            'navigator_back': self.back, 'navigator_to': lambda name, *_: self.pages.append(C.string_at(name).decode()),
            'time_now_ms': lambda *_: self.ms & 0xffffffff, 'fw_clock_gettime': self.clock_gettime,
            'fw_time': lambda *_: 1_750_000_000 + self.ms // 1000, 'toolsReadConfig': self.read_config,
            'tk_str_start_with': lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)),
            'fw__deque_push_back': self.push_back, 'fw_deque_pop_back': lambda q, *_: self.deques[q].pop(),
            'widget_set_text_utf8': lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()),
            'timer_add': self.add_timer, 'timer_remove': lambda t, *_: self.timers.pop(t, None) and 0,
            'widget_factory_create_widget': lambda f, kind, parent, *_: self.widget(parent, C.string_at(kind).decode()),
        }
        for kind in ('window_create', 'scroll_view_create', 'list_item_create', 'image_create', 'hscroll_label_create',
                     'list_view_create', 'button_create'):
            model[kind] = lambda parent, *_, kind=kind: self.widget(parent, kind)
        model['window_create'] = lambda *_: self.pages.append(self.widget(0, 'window_create')) or self.pages[-1]
        model['widget_on'] = lambda w, event, fn, ctx, *_: self.handlers.__setitem__((w, event), (fn, ctx)) or 1
        model['widget_destroy_children'] = lambda w, *_: self.children.pop(w, None) and 0
        for name, fn in model.items():
            fw.mock(name, fn)

    def st(self, name, ctype=C.c_int):
        """navigation.c's state (scratch_t st), by field."""
        return ctype.from_address(C.addressof(self.fw.var('st', C.c_char)) + self.scratch[name])

    # Pages: AWTK's widgets as zeroed blocks that keep their parent, children, text and handlers.
    def widget(self, parent, kind='widget'):
        self.blocks.append(C.create_string_buffer(0x400))
        w = C.addressof(self.blocks[-1])
        struct.pack_into('<Q', self.blocks[-1], O['W_PARENT'], parent)
        self.children.setdefault(parent, []).append(w)
        self.made.append((kind, w))
        return w

    def last(self, kind):
        return [w for k, w in self.made if k == kind][-1]

    def rows_of(self, view):
        """A list's rows as they show: each row's caption and detail (its labels' text, in order)."""
        return [tuple(self.texts.get(label) for label in self.children.get(item, [])) for item in self.children.get(view, [])]

    def tap(self, caption, view=None):
        """The row of view (the last list made) showing caption, tapped: its click handler runs."""
        view = view or self.last('scroll_view_create')
        (item,) = [item for item in self.children.get(view, []) if self.texts.get(self.children[item][0]) == caption]
        fn, ctx = self.handlers[item, O['EVT_CLICK']]
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)

    def now_playing(self):
        """Now Playing's position and album labels, as each of its paints writes them (np_sync)."""
        if not self.np:
            self.np = {name: self.widget(0) for name in ('np_win', 'np_title', 'np_pos', 'np_album', 'np_slider', 'np_remain')}
            for name, w in self.np.items():
                self.st(name, C.c_void_p).value = w
        self.fw.fn('np_sync', C.c_int, C.c_void_p)(self.np['np_win'])
        return self.texts.get(self.np['np_pos']), self.texts.get(self.np['np_album'])

    def back(self, *_):
        """navigator_back: the page on top closes, and a payload page's destroy handler runs, as AWTK's does."""
        top = self.pages.pop() if self.pages else None
        if isinstance(top, int) and (top, O['EVT_DESTROY']) in self.handlers:
            fn, ctx = self.handlers[top, O['EVT_DESTROY']]
            C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)
        return 0

    def add_timer(self, fn, ctx, ms, *_):
        self.timer_n += 1
        self.timers[self.timer_n] = fn, ctx
        return self.timer_n

    def run_timers(self):
        """The main loop's due timers, each once: one that asks to go on (RET_REPEAT) stays."""
        for t, (fn, ctx) in list(self.timers.items()):
            if t in self.timers and C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(ctx) != 8:
                self.timers.pop(t, None)

    # The queue menu: a Play/Pause hold on a row, as ringnav_keylong takes it, then a pick in its menu, which runs
    # once the dialog has closed (qm_run) and finds its row again before it acts.
    def menu(self, kind, cls, at, rows, r, action):
        st = self.st
        st('qm_kind').value, st('qm_cls', C.c_uint).value, st('qm_action').value = kind, cls, action
        st('qm_idx', C.c_uint).value, st('qm_rows', C.c_uint).value = at, rows
        st('qm_hash', C.c_uint).value = self.fw.fn('navigation__rec_hash', C.c_uint, C.c_void_p)(r)
        st('qm_browse', C.c_uint).value = self.fw.fn('navigation__browse_hash', C.c_uint)()
        st('qm_dialog', C.c_void_p).value = self.widget(0, 'dialog')
        self.fw.fn('navigation__qm_run', C.c_int, C.c_void_p)(None)

    def hold(self, title, action):
        """A hold on the list's song row showing title."""
        rows = self.rows()
        (at,) = [i for i, r in enumerate(rows) if text_of(r, 'title') == title]
        self.menu(QM['SONG'], self.fw.var('fw_ctx_g_class_type', C.c_int).value, at, len(rows), rows[at], action)

    def hold_playing(self, action):
        """A hold on Now Playing: the track playing."""
        q = self.deques[self.queue]
        self.st('qm_queue', C.c_void_p).value = self.queue
        self.st('qm_queue_hash', C.c_uint).value = self.fw.fn('queue_hash', C.c_uint, C.c_void_p)(self.queue)
        self.menu(QM['PLAYING'], SONGS, self.mcl.pos, len(q), q[self.mcl.pos], action)

    # Calls, as stock's code makes them.
    def call(self, name, *args):
        """A stock entry point by name, as stock's code calls it: its hook (HOOKED)."""
        return self.hooks[name](*args)

    def push_back(self, q, value, *_):
        if q & 0xffffffff == self.pool:  # a deque of int: deque_at hands out a pointer to one
            self.keep.append(C.c_int(value))
            value = C.addressof(self.keep[-1])
        self.deques[q].append(value)

    def read_config(self, path, section, key, out, default, *_):
        value = self.config.get(C.string_at(key).decode())
        value = value.encode() if value is not None else C.string_at(default)
        C.memmove(out, value + b'\0', len(value) + 1)
        return 0

    def clock_gettime(self, which, ts, *_):
        t = C.cast(ts, C.POINTER(C.c_long))
        t[0], t[1] = self.ms // 1000, self.ms % 1000 * 1_000_000
        return 0

    # The library.
    def scan(self, *_):
        self.library.scan(self.disk.files)
        return 0

    def update(self):
        """Library → Update Local Music: stock's scan thread runs the hooked scan."""
        return self.call('scanAllMusicFile', 0, 0)

    def dbquery(self, db, sql, row, ctx, *_):
        return self.library.query(C.string_at(sql).decode(), row, ctx)

    def directory(self):
        return self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value

    def make_song(self, ctx, n, values, columns):
        row = {columns[i].decode(): (values[i] or b'').decode() for i in range(n)}
        r = self.memory.record(
            id_=int(row['id']), name=row['filename'], path=row['fileurl'], title=row['title'], album=row['album'],
            artist=row['songer'], album_artist=row['albumsonger'], track=int(row['track'] or 0),
            disc=int(row['cd_num'] or 0), cue=(int(row['cue_start'] or 0), int(row['cue_end'] or 0)))
        self.deques[self.directory()].append(r)
        return 0

    def make_album(self, ctx, n, v, c):
        """getAllAlbum's row (stock's album_row, 0x506cc4): id, album, songer and the art track's path."""
        r = self.memory.record(kind=4, id_=int(v[0]), album=(v[1] or b'').decode(), artist=(v[2] or b'').decode(),
                               path=(v[3] or b'').decode())
        self.deques[self.directory()].append(r)
        return 0

    def query(self, sql, row):
        """A stock query function's body: toolsQueryDbTable, which the build hooks."""
        sql = C.create_string_buffer(sql.encode())
        return self.call('toolsQueryDbTable', C.addressof(C.create_string_buffer(b'/mnt/data/database.db')),
                         C.addressof(sql), C.cast(row, C.c_void_p).value, 0)

    def all_music(self, sort, *_):
        return self.query('select * from songtable order by ' + ('id' if sort == 3 else 'title collate nocase'),
                          self.song_row)

    def all_albums(self, *_):
        return self.query("select id,album,songer,fileurl from songtable where ifnull(album,'')<>'' "
                          'group by album collate nocase order by album collate nocase', self.album_row)

    def by_album(self, name, *_):
        where = "ifnull(album,'')=''" if not name else "album='%s' collate nocase" % C.string_at(name).decode().replace("'", "''")
        return self.query('select * from songtable where ' + where + ' order by cd_num, track, fileurl', self.song_row)

    def classinfo(self):
        return (C.c_char * 912).in_dll(self.fw.lib, 'fw_ctx_g_local_classinfo_save')

    def localclass(self, cls, *_):
        """load_localclass_list: the class's query into the staging deque, then the showlist; getAllAlbum's list gains
        an Unknown row (id -1) whenever it has any."""
        staging = self.deques[self.directory()]
        staging.clear()
        if cls == ALBUMS:
            self.all_albums()
            if staging:
                staging.append(self.memory.record(kind=4, id_=-1, album='Unknown Album'))
        elif cls == ALBUM:
            info = self.classinfo()
            self.by_album(None if info[9] != b'\0' else C.addressof(info) + 0xd)
        self.deques[self.showlist][:] = staging
        staging.clear()
        return len(self.deques[self.showlist])

    def album_detail(self, cls, r, *_):
        """load_album_detaillist: an Albums row's name and artist in the classinfo, then its songs (0xff10)."""
        info = self.classinfo()
        C.memset(C.addressof(info), 0, 912)
        album, artist = (text_of(r, 'album') or '').encode(), (text_of(r, 'artist') or '').encode()
        info[9] = int_of(r, 'id') == -1
        C.memmove(C.addressof(info) + 0xd, album, len(album))
        C.memmove(C.addressof(info) + 0x10d, artist, len(artist))
        self.fw.var('fw_ctx_g_class_type', C.c_int).value = ALBUM
        return self.call('load_localclass_list', ALBUM)

    def load_directory(self, path, *_):
        """toolsLoadDirectory: a folder's audio files as untagged records, into the staging deque."""
        path = C.string_at(path).decode() if isinstance(path, int) else path.value.decode()
        names = sorted({f[len(path) + 1:] for f in self.disk.files if f.startswith(path + '/') and '/' not in f[len(path) + 1:]})
        rows = self.deques[self.directory()]
        for name in names:
            tags = self.library.tags.get(f'{path}/{name}', {})
            if not name.lower().endswith(AUDIO):
                continue
            # a file with a CUE sheet lists its tracks, each its title and its start and end in the file
            for start, end, title in tags.get('cue', [(0, 0, '')]):
                rows.append(self.memory.record(name=title or name, path=f'{path}/{name}', title=title, cue=(start, end)))
        return len(rows)

    def rows(self):
        return list(self.deques[self.showlist])

    def songs(self):
        """Library → Songs: the titles its rows show."""
        self.fw.var('fw_ctx_g_class_type', C.c_int).value = SONGS
        assert self.fw.fn('ringnav_localclass', C.c_int, C.c_int)(SONGS) == len(self.rows())
        return [text_of(r, 'title') for r in self.rows()]

    def albums(self):
        """Library → Albums: each row's album and album artist (else artist); the Unknown row as None."""
        self.fw.var('fw_ctx_g_class_type', C.c_int).value = ALBUMS
        assert self.fw.fn('ringnav_localclass', C.c_int, C.c_int)(ALBUMS) == len(self.rows())
        return [None if int_of(r, 'id') == -1 else (text_of(r, 'album'), text_of(r, 'album_artist') or text_of(r, 'artist'))
                for r in self.rows()]

    def open_album(self, name, artist):
        """An Albums row picked: its songs' titles, as its page lists them."""
        (r,) = [r for r in self.rows() if text_of(r, 'album') == name and (text_of(r, 'album_artist') or text_of(r, 'artist')) == artist]
        self.fw.fn('ringnav_album_detail', C.c_int, C.c_int, C.c_void_p)(ALBUMS, r)
        return [text_of(r, 'title') for r in self.rows()]

    def pick(self, title, cls=ALBUM):
        """A song row of the list on show picked: its page plays the list from there (player_start, mode 2)."""
        (at,) = [i for i, r in enumerate(self.rows()) if text_of(r, 'title') == title]
        return self.call('player_start', self.showlist, at, cls, 2)

    def folder(self, path):
        """Folder view at path: its listing (toolsLoadDirectory) is the list on show; the file names."""
        self.deques[self.directory()].clear()
        self.load_directory(C.create_string_buffer(path.encode()))
        self.deques[self.showlist][:] = self.deques[self.directory()]
        self.deques[self.directory()].clear()
        return [text_of(r, 'name') for r in self.rows()]

    def pick_file(self, name):
        """A file of the folder on show picked: a folder play (class 1) from it."""
        (at,) = [i for i, r in enumerate(self.rows()) if text_of(r, 'name') == name]
        return self.call('player_start', self.showlist, at, 1, 2)

    # The player.
    def now(self):
        q = self.deques[self.queue]
        return q[self.mcl.pos] if 0 <= self.mcl.pos < len(q) else None

    def length(self, r):
        tags = self.library.tags.get(text_of(r, 'path'), {})
        cue = int_of(r, 'cue_start'), int_of(r, 'cue_end')
        return (cue[1] or tags.get('seconds', 240)) - cue[0]

    def load(self, q, at, kind, *_):
        """mclLoadPlayList: the queue copied, its position and class, and Shuffle's pool all of it; no play."""
        self.deques[self.queue][:] = self.deques[q]
        self.mcl.pos, self.mcl.type, self.mcl.preload, self.mcl.prepos = C.c_int(at).value, C.c_int(kind).value, 0, -1
        self.deques[self.pool].clear()
        for i in range(len(self.deques[q])):
            self.push_back(self.pool, i)
        return 0

    def pool_left(self):
        return [C.c_int.from_address(a).value for a in self.deques[self.pool]]

    def shuffle_pick(self, forward, *_):
        """Stock Shuffle's pick (mcl_shuffle_pick): the playing index leaves the pool, refilled once empty, and the
        position goes to one left there at random."""
        pool = self.deques[self.pool]
        pool[:] = [a for a in pool if C.c_int.from_address(a).value != self.mcl.pos]
        if not pool:
            for i in range(len(self.deques[self.queue])):
                if i != self.mcl.pos:
                    self.push_back(self.pool, i)
        self.mcl.pos = C.c_int.from_address(self.random.choice(pool)).value
        return 0

    def start(self, *_):
        """mclStartPlayer: the queue's file at its position opens and plays, from a set start time if any."""
        r = self.now()
        if r is None:
            return -1
        self.opened.append(text_of(r, 'path') if not int_of(r, 'cue_start') else (text_of(r, 'path'), int_of(r, 'cue_start')))
        self.status, self.file_sec, self.start_seek = PLAYING, int_of(r, 'cue_start') + self.start_seek, 0
        self.mcl.preload, self.mcl.prepos = 0, -1
        return 0

    def stop(self, *_):
        self.status = STOPPED
        return 0

    def elapsed(self):
        """The track's seconds played (a CUE track's from its start), None while stopped."""
        r = self.now()
        return None if self.status == STOPPED or r is None else self.file_sec - int_of(r, 'cue_start')

    def track_time(self, sec, total, *_):
        """player_playtime_and_length: the track's seconds and its length; -1 while stopped."""
        if self.elapsed() is None:
            return -1
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.elapsed(), self.length(self.now())
        return 0

    def play_time(self, sec, total, *_):
        r = self.now()
        C.c_int.from_address(sec).value = self.file_sec if r is not None and self.status != STOPPED else 0
        C.c_int.from_address(total).value = self.length(r) + int_of(r, 'cue_start') if r is not None else 0
        return 0

    def start_at(self, sec, *_):
        self.start_seek = sec
        return 0

    def next_song(self, automatic, *_):
        """mclNextSong outside advanced play: the next row, Repeat One's own again, Repeat All's wrap, Shuffle's pick;
        a list's end stops."""
        n, at = len(self.deques[self.queue]), self.mcl.pos
        if automatic and self.mcl.preload == 1 and 0 <= self.mcl.prepos < n:
            # Gapless: the decoder opened ahead takes over at the change, no new start
            self.mcl.pos, self.mcl.preload, self.mcl.prepos = self.mcl.prepos, 0, -1
            r = self.now()
            self.opened.append(text_of(r, 'path'))
            self.gapless.append(text_of(r, 'path'))
            self.file_sec = int_of(r, 'cue_start')
            return 0
        if self.mcl.mode == SHUFFLE:  # its pick, at the call the build makes ringnav_shuffle's
            self.fw.fn('ringnav_shuffle', C.c_int, C.c_int)(1)
            return self.call('mclStartPlayer')
        if automatic and self.mcl.mode == REPEAT_ONE:
            to = at
        elif at + 1 < n:
            to = at + 1
        elif self.mcl.mode == REPEAT_ALL or not automatic:
            to = 0
        else:
            return self.stop() or -1
        self.mcl.pos = to
        return self.call('mclStartPlayer')

    def preload(self, *_):
        """mcl_preload outside advanced play: List Play's and Repeat All's next (Repeat All's wrap), opened ahead."""
        n, at = len(self.deques[self.queue]), self.mcl.pos
        to = at + 1 if at + 1 < n else 0 if self.mcl.mode == REPEAT_ALL else -1
        if self.mcl.mode not in (LIST_PLAY, REPEAT_ALL) or to < 0:
            return 0
        self.mcl.prepos = to
        return self.call('mcl_open_preload')

    def open_preload(self, *_):
        """mcl_open_preload: the file at MCL_PREPOS opens ahead, for the change to take over."""
        self.mcl.preload = 1
        self.preloads.append(text_of(self.deques[self.queue][self.mcl.prepos], 'path'))
        return 0

    def prev_song(self, *_):
        n = len(self.deques[self.queue])
        self.mcl.pos = self.mcl.pos - 1 if self.mcl.pos > 0 else n - 1
        return self.call('mclStartPlayer')

    def change(self, forward, *_):
        """player_change_music: at the queue's ends stock wraps itself, but for Repeat All, which as everywhere else
        goes through mclNextSong(0) or mclPrevSong."""
        n, at = len(self.deques[self.queue]), self.mcl.pos
        if (forward and at + 1 >= n or not forward and at == 0) and self.mcl.mode != REPEAT_ALL:
            self.mcl.pos = 0 if forward else n - 1
            return self.call('mclStartPlayer')
        return self.call('mclNextSong', 0) if forward else self.call('mclPrevSong')

    def seek_start(self, *_):
        """player_seek_start: past 10 s back to the start, and 1; else 0, Previous changes track."""
        if (self.elapsed() or 0) > 10:
            self.seek(0)
            return 1
        return 0

    def seek(self, sec, *_):
        """The player told to seek to sec of the track."""
        r = self.now()
        self.seeks.append(C.c_int(sec).value)
        self.file_sec = (int_of(r, 'cue_start') if r is not None else 0) + C.c_int(sec).value
        return 0

    def seek_file(self, sec, *_):
        self.seeks.append(C.c_int(sec).value - int_of(self.now(), 'cue_start'))
        self.file_sec = C.c_int(sec).value
        return 0

    def player_start(self, q, at, cls, mode, *_):
        """player_start: the list loads as the queue and plays from at; paused unless mode 2."""
        self.call('mclLoadPlayList', q, at, cls)
        self.call('mclStartPlayer')
        if mode != 2:
            self.status = PAUSED
        return 0

    def start_queue(self, at, *_):
        self.mcl.pos = at
        return self.call('mclStartPlayer')

    def pause(self, *_):
        self.status = {PLAYING: PAUSED, PAUSED: PLAYING}.get(self.status, self.status)
        return 0

    def mode(self, mode, *_):
        self.mcl.mode = mode
        return 0

    def navigate(self, name, context=0, *_):
        name = C.string_at(name).decode()
        if name == 'dialog/msginfo_dialog':  # a toast: it closes by itself
            self.toasts.append(C.string_at(context + 8).decode())
            return 0
        self.pages.append(name)
        if name == 'playing_page' and context:
            # playing_page_init: player_start with its context's queue, unless class 0xff (Now Playing as it is)
            dq = C.c_void_p.from_address(context).value
            idx, cls, mode = struct.unpack('<3i', C.string_at(context + 8, 12))
            if cls != 0xff:
                self.call('player_start', dq, idx, cls, mode)
        return 0

    def boot_resume(self):
        """Home's boot resume (home_page_init): memeory_startplayer rebuilds the queue and names its track and class,
        then Now Playing's start, which the build makes a paused one on Home (ringnav_boot). The queue rebuilt."""
        out = self.deques.new()
        at = self.call('memeory_startplayer', out)
        cls = C.c_int.in_dll(self.fw.lib, 'fw_ctx_g_memory_info').value
        self.fw.fn('ringnav_boot', None, C.c_char_p, C.c_void_p)(b'playing_page', (C.c_int * 4)(out, at, cls, 3))
        return out

    # Keys and time.
    def next_key(self):
        return self.call('player_change_music', 1)

    def previous_key(self):
        """player_prev_music: on a track playing or paused its seek to the start first, which the build hooks; under
        11 s, or stopped, the track before."""
        if self.status == STOPPED or not self.call('player_seek_start'):
            self.call('player_change_music', 0)

    def ui_pass(self):
        """main_loop_sleep_default, the UI loop's pass, which the build hooks (ringnav_sleep)."""
        self.fw.fn('ringnav_sleep', C.c_int, C.c_void_p)(None)

    def play_for(self, seconds):
        """The clock runs on a second at a time, a UI pass each, as mclAutoChange: with Gapless on, 6 s before a
        track's end the next opens ahead (mcl_preload); at its end it changes by itself (mclNextSong(1))."""
        for _ in range(seconds):
            self.ms += 1000
            r = self.now()
            if self.status == PLAYING and r is not None:
                self.file_sec += 1
                left = self.length(r) - (self.file_sec - int_of(r, 'cue_start'))
                if left == 6 and self.mcl.preload == 0 and C.c_ubyte.from_address(O['MCL_GAPLESS']).value:
                    self.call('mcl_preload')  # with Gapless on, 6 s before the end
                if left <= 0:
                    self.call('mclNextSong', 1)
            self.ui_pass()


@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets as this build lays it out: its pointers move them."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def q2(fw, mcl, scratch):
    """A Q2 for the test's firmware (or a restart's), its records in the test's Memory."""
    memory = Memory()
    yield lambda files, tags=None, config=None, firmware=None, library=None: Q2(
        firmware or fw, mcl, scratch, memory, files, tags, config, library)
    memory.close()


SONG = 30  # a song's seconds
ALPHA, BETA = '/mnt/mmc/Music/Alpha/First/', '/mnt/mmc/Music/Beta/'


def music_card():
    """Two artists' albums, one name twice (First: Alpha's and Beta's), a folder marked database.ignore with one
    marked database.unignore inside it, an audiobook and a podcast. Each song's title is its name."""
    tags = {}
    def song(path, title, album, artist, track):
        tags['/mnt/mmc/Music/' + path] = dict(title=title, album=album, artist=artist, album_artist=artist, track=track,
                                              seconds=SONG)
    song('Alpha/First/01 Dawn.flac', 'Dawn', 'First', 'Alpha', 1)
    song('Alpha/First/02 Noon.flac', 'Noon', 'First', 'Alpha', 2)
    song('Alpha/First/03 Dusk.flac', 'Dusk', 'First', 'Alpha', 3)
    song('Beta/First/01 Other.flac', 'Other', 'First', 'Beta', 1)
    song('Beta/Second/01 Rise.flac', 'Rise', 'Second', 'Beta', 1)
    song('Beta/Second/02 Fall.flac', 'Fall', 'Second', 'Beta', 2)
    song('Junk/Ringtone.mp3', 'Ringtone', '', '', 0)
    song('Junk/Demos/Demo.flac', 'Demo', 'Demos', 'Alpha', 1)
    song('Junk/Keep/Gem.flac', 'Gem', 'Kept', 'Gamma', 1)
    tags['/mnt/mmc/Audiobooks/Novel/Novel.m4b'] = dict(title='Novel', album='Novel', artist='Author')
    tags['/mnt/mmc/Podcasts/Show/Episode 1.mp3'] = dict(title='Episode 1', album='Show', artist='Host')
    files = {path: b'\0' * 64 for path in tags}
    files['/mnt/mmc/Music/Junk/database.ignore'] = b''
    files['/mnt/mmc/Music/Junk/Keep/.database.unignore'] = b''
    return files, tags


def title_of(q, path):
    return q.library.tags[path]['title']


def album_of(q, path):
    t = q.library.tags[path]
    return t['album'], t['album_artist']


# Update Local Music, the folder markers and the Library's lists.
def test_a_marked_folder_stays_out_of_the_songs_and_albums_until_its_marker_goes(q2):
    files, tags = music_card()
    q = q2(files, tags)
    q.update()
    assert sorted(q.songs()) == ['Dawn', 'Dusk', 'Fall', 'Gem', 'Noon', 'Other', 'Rise']
    assert sorted(q.albums(), key=str) == [('First', 'Alpha'), ('First', 'Beta'), ('Kept', 'Gamma'), ('Second', 'Beta')]
    del files['/mnt/mmc/Music/Junk/database.ignore']  # taken off the card, then Update again
    q.update()
    assert sorted(q.songs()) == ['Dawn', 'Demo', 'Dusk', 'Fall', 'Gem', 'Noon', 'Other', 'Ringtone', 'Rise']
    albums = q.albums()
    assert albums[-1] is None and sorted(albums[:-1]) == [('Demos', 'Alpha'), ('First', 'Alpha'), ('First', 'Beta'),
                                                         ('Kept', 'Gamma'), ('Second', 'Beta')]


# A list played: an album to its end, gapless, a folder's speed, and the queue menu's Play Next and Add to Queue.
def test_an_album_picked_from_its_list_plays_on_to_its_end_and_stops_there(q2):
    files, tags = music_card()
    q = q2(files, tags)
    q.update()
    q.albums()
    assert q.open_album('First', 'Alpha') == ['Dawn', 'Noon', 'Dusk']  # Beta's First is another album
    q.pick('Noon')
    assert q.opened == [ALPHA + '02 Noon.flac'] and q.now_playing() == ('2 of 3', 'First')
    q.play_for(SONG)  # Noon ends by itself
    assert q.opened[1:] == [ALPHA + '03 Dusk.flac'] and q.now_playing() == ('3 of 3', 'First')
    q.play_for(3)
    q.next_key()  # on the album's last track: the album is over, not round to its first (stock's own Next wraps)
    assert len(q.opened) == 2 and q.status == STOPPED
    q.pick('Dusk')
    q.play_for(SONG)  # and its end by itself stops too
    assert q.opened[2:] == [ALPHA + '03 Dusk.flac'] and q.status == STOPPED
    q.call('mclSetPlayMode', REPEAT_ALL)  # Play Mode: stock's Repeat All goes round
    q.pick('Dusk')
    q.play_for(SONG)
    assert q.opened[3:] == [ALPHA + '03 Dusk.flac', ALPHA + '01 Dawn.flac'] and q.now_playing() == ('1 of 3', 'First')


def test_gapless_takes_over_the_next_opened_ahead_and_a_play_next_closes_a_stale_one(q2):
    files, tags = music_card()
    q = q2(files, tags)
    C.c_ubyte.from_address(O['MCL_GAPLESS']).value = 1
    q.mcl.fd = 7  # the player's socket
    q.update()
    q.albums()
    q.open_album('First', 'Alpha')
    q.pick('Dawn')
    q.play_for(SONG - 6)
    assert q.preloads == [ALPHA + '02 Noon.flac']  # Noon, opened ahead
    q.songs()
    q.hold('Rise', QA['NEXT'])  # now Rise is next: the decoder holding Noon is told to let it go
    assert q.sent == [rb'{mcl-closegapless\null}']
    q.play_for(6)
    assert q.opened[1:] == [BETA + 'Second/01 Rise.flac'] and q.gapless == []  # Rise, from a cold start
    q.play_for(3 * SONG)
    assert [title_of(q, p) for p in q.opened] == ['Dawn', 'Rise', 'Noon', 'Dusk'] and q.status == STOPPED
    assert q.gapless == q.preloads[1:] == [ALPHA + '02 Noon.flac', ALPHA + '03 Dusk.flac']  # the rest seamless


def test_a_folders_speed_holds_across_its_tracks_and_the_next_folder_plays_at_its_own(q2):
    files, tags = music_card()
    q = q2(files, tags)
    C.c_ubyte.from_address(O['MCL_GAPLESS']).value = 1
    q.update()
    q.albums()
    q.open_album('First', 'Alpha')
    q.pick('Dawn')
    q.songs()
    q.hold('Rise', QA['ADD'])
    assert q.speeds == [0]  # Dawn starts at its folder's speed: none chosen, normal
    q.call('mclSetPlaySpeed', 2)  # Now Playing's speed, chosen while Dawn plays
    assert q.speeds == [0, 2]
    q.play_for(3 * SONG)
    assert [title_of(q, p) for p in q.opened] == ['Dawn', 'Noon', 'Dusk', 'Rise']
    assert q.gapless == q.preloads == [ALPHA + '02 Noon.flac', ALPHA + '03 Dusk.flac']  # one folder: seamless
    assert q.speeds == [0, 2, 0]  # Rise, from another folder, was never opened ahead: a fresh start at its own
    q.call('mclSetPlayMode', REPEAT_ALL)
    q.play_for(SONG)  # round to Dawn: its folder's speed came back with it
    assert title_of(q, q.opened[-1]) == 'Dawn' and q.speeds == [0, 2, 0, 2]


def test_play_next_and_add_to_queue_join_the_album_playing(q2):
    files, tags = music_card()
    q = q2(files, tags)
    q.update()
    q.albums()
    q.open_album('First', 'Alpha')
    q.pick('Dawn')
    q.songs()  # Library → Songs, while Dawn plays
    q.hold('Rise', QA['NEXT'])
    q.hold('Gem', QA['ADD'])
    assert q.toasts == [] and q.now_playing() == ('1 of 5', 'First')
    q.play_for(SONG)
    assert q.opened[1:] == [BETA + 'Second/01 Rise.flac'] and q.now_playing() == ('2 of 5', 'Second')
    q.play_for(4 * SONG)
    assert [title_of(q, p) for p in q.opened] == ['Dawn', 'Rise', 'Noon', 'Dusk', 'Gem'] and q.status == STOPPED


# Library → Shuffle, and Up Next: View Queue while a shuffle is on.
def shuffle_albums(q):
    q.fw.fn('shuffle_menu', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    q.tap('Shuffle Albums')


def up_next(q):
    """View Queue in a shuffle: Up Next's rows, each its title over its artist."""
    page = q.fw.lib.navigation__page_closed, q.fw.lib.page_keyup
    q.fw.fn('up_next', None, C.c_void_p, C.c_void_p)(*(C.cast(f, C.c_void_p) for f in page))
    return q.rows_of(q.last('scroll_view_create'))


def test_shuffle_albums_plays_each_album_whole_in_turn_as_up_next_said(q2):
    files, tags = music_card()
    q = q2(files, tags)
    C.c_ubyte.from_address(O['MCL_GAPLESS']).value = 1
    q.update()
    shuffle_albums(q)
    assert len(q.opened) == 1
    rows = up_next(q)
    q.play_for(7 * SONG)
    played = [title_of(q, p) for p in q.opened]
    assert played[1:] == [title for title, _ in rows] and q.status == STOPPED  # Up Next was right; one pass, then stop
    assert q.gapless == q.preloads == q.opened[1:]  # each next opened ahead, in the shuffle's order, and taken over
    assert sorted(played) == sorted(q.songs())  # every song the Library shows, once
    albums = [album_of(q, p) for p in q.opened]
    runs = [a for i, a in enumerate(albums) if not i or albums[i - 1] != a]
    assert sorted(runs) == [('First', 'Alpha'), ('First', 'Beta'), ('Kept', 'Gamma'), ('Second', 'Beta')]  # each whole
    for album in runs:
        tracks = [q.library.tags[p]['track'] for p in q.opened if album_of(q, p) == album]
        assert tracks == sorted(tracks)  # and in its order


def test_a_pick_from_up_next_plays_it_and_previous_gives_back_what_it_jumped(q2):
    files, tags = music_card()
    q = q2(files, tags)
    q.update()
    shuffle_albums(q)
    first = q.opened[0]
    q.hold_playing(QA['QUEUE'])  # View Queue: in a shuffle, Up Next
    rows = q.rows_of(q.last('scroll_view_create'))
    page = q.pages[-1]
    assert len(rows) == 6
    q.tap(rows[2][0])  # the third: two jumped
    assert title_of(q, q.opened[-1]) == rows[2][0] and page not in q.pages  # it plays, and Up Next closes
    q.play_for(3)
    q.previous_key()  # 3 s in: back to the track the pick left
    assert q.opened[-1] == first
    q.play_for(SONG)  # on in the order from there: the jumped tracks come back
    assert title_of(q, q.opened[-1]) == rows[0][0]
    q.play_for(6 * SONG)
    assert [title_of(q, p) for p in q.opened[4:]] == [title for title, _ in rows[1:]] and q.status == STOPPED


def test_shuffle_songs_plays_each_song_once_a_pass_and_the_next_list_picked_plays_as_before(q2):
    files, tags = music_card()
    q = q2(files, tags)
    q.update()
    q.fw.fn('shuffle_menu', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    q.tap('Shuffle Songs')
    assert q.mcl.mode == SHUFFLE  # saved as stock's Shuffle
    rows = [title for title, _ in up_next(q)]
    assert len(rows) == 6 + 7  # the rest of this pass, then the whole of the next (Repeat All)
    q.play_for(13 * SONG)
    played = [title_of(q, p) for p in q.opened]
    assert played[1:] == rows and q.status == PLAYING  # as Up Next said, across the passes
    assert sorted(played[:7]) == sorted(played[7:]) == sorted(q.songs())  # each pass every song once
    q.albums()
    q.open_album('Second', 'Beta')
    q.pick('Rise')  # another list: the play mode Shuffle Songs replaced, List Play, is back
    assert q.mcl.mode == LIST_PLAY
    q.play_for(2 * SONG)
    assert [title_of(q, p) for p in q.opened[14:]] == ['Rise', 'Fall'] and q.status == STOPPED


# Crossfade's fade for the next change, which hciplayer's filter reads from /tmp/q2xfade.
ECHO = BETA + 'Second/03 Echo.dsf'


def fade(q):
    """The fade the next change gets, in ms, and the stops counted: none until one is first asked for."""
    cell = q.disk.shared.get(XFADE_FILE)
    return (0, 0) if cell is None else (cell.value & 0xffff, cell.value >> 16)


def test_crossfade_fades_into_another_album_only_and_never_into_dsd(q2):
    files, tags = music_card()
    tags[ECHO] = dict(title='Echo', album='Second', artist='Beta', album_artist='Beta', track=3, seconds=SONG)
    files[ECHO] = b'\0' * 64
    q = q2(files, tags, config={'XFADE': '1', 'XFADESEC': '6'})
    gapless = C.c_ubyte.from_address(O['MCL_GAPLESS'])
    gapless.value = 1
    q.update()
    q.albums()
    q.open_album('First', 'Alpha')
    q.pick('Noon')
    q.songs()
    q.hold('Other', QA['ADD'])  # First too, but Beta's
    q.hold('Echo', QA['ADD'])
    q.ui_pass()
    assert fade(q) == (0, 0)  # Noon into Dusk: one album, gapless
    q.play_for(SONG)
    assert title_of(q, q.opened[-1]) == 'Dusk' and fade(q) == (6000, 0)  # into another album of the same name
    gapless.value = 0
    q.ui_pass()
    assert fade(q) == (0, 0)  # stock's Gapless off: no fade either
    gapless.value = 1
    q.play_for(SONG)
    assert title_of(q, q.opened[-1]) == 'Other' and fade(q) == (0, 0)  # into DSD, which skips the filter
    q.play_for(SONG)
    assert title_of(q, q.opened[-1]) == 'Echo' and fade(q) == (0, 0)  # the last: nothing after it
    q.play_for(SONG)
    assert q.status == STOPPED and fade(q) == (0, 1)  # the queue's end is a stop, counted once
    q.play_for(3)
    assert fade(q) == (0, 1)


def test_under_stock_shuffle_crossfade_asks_about_the_very_track_that_plays_next(q2):
    files, tags = music_card()
    tags[ECHO] = dict(title='Echo', album='Second', artist='Beta', album_artist='Beta', track=3, seconds=SONG)
    files[ECHO] = b'\0' * 64
    q = q2(files, tags, config={'XFADE': '1', 'XFADESEC': '6'})
    C.c_ubyte.from_address(O['MCL_GAPLESS']).value = 1
    q.update()
    q.call('mclSetPlayMode', SHUFFLE)  # Play Mode: stock's Shuffle, which draws each next at the change
    q.songs()
    q.pick('Dawn', SONGS)
    asked = []
    for _ in range(24):
        q.play_for(SONG - 1)
        drawn = len(q.pool_left()) >= 2  # drawn ahead; with one left the pool refills at the change, unknown
        ms = fade(q)[0]
        q.play_for(1)
        if drawn:
            asked.append((ms, q.opened[-1] == ECHO))
    # Stock's pool holds the queue's 8 at the first change and loses the track left at each; emptied, it refills with
    # the 7 not playing. So the 8th, 15th and 22nd changes draw at the change and the other 21 were drawn ahead. The
    # first 7 are one pass over the pool, the 7 tracks after Dawn each once: the DSD file comes next once among them.
    assert len(asked) == 24 - 3 and sum(dsd for _, dsd in asked[:7]) == 1
    # no fade into the DSD file exactly when it comes next: the fade was set for the track that then played
    assert all((ms == 0) == dsd for ms, dsd in asked)


# A restart: Memory Playback's queue and a long track's place.
@pytest.fixture
def restart(library, tmp_path, q2):
    """Power off and on: the next firmware, freshly loaded, with the card and the flash as they were (the database
    on it too), and stock's player globals zeroed as at boot."""
    loaded = []

    def boot(q, memory_play=0):
        (tmp_path/f'boot{len(loaded)}').mkdir()
        loaded.append(host.Fw(library, tmp_path/f'boot{len(loaded)}'))
        C.memset(O['MCL_POOL'], 0, O['MCL_FD'] + 4 - O['MCL_POOL'])
        loaded[-1].var('g_memory_play', C.c_ubyte).value = memory_play
        return q2(q.disk.files, config=q.config, firmware=loaded[-1], library=q.library)
    yield boot
    for f in loaded:
        f.close()


EPISODE = '/mnt/mmc/Podcasts/Show/Episode 1.mp3'


TRACK, LOCATION = 1, 2  # Memory Playback


def place(q, path):
    """The resume ring's saved place for path (navigation.c spots: RESUME_SLOTS of key, seconds), None for none."""
    words = 2 * O['RESUME_SLOTS']
    ring = struct.unpack(f'<{words}I', q.disk.files.get(RESUME_FILE, bytes(4 * words)))
    return next((ring[i + 1] for i in range(0, words, 2) if ring[i] == key(path)), None)


@pytest.mark.parametrize('memory_play', [TRACK, LOCATION])
def test_a_long_episode_paused_and_powered_off_comes_back_at_its_place(q2, restart, memory_play):
    files, tags = music_card()
    tags[EPISODE]['seconds'] = 45 * 60
    q = q2(files, tags)
    q.fw.var('g_memory_play', C.c_ubyte).value = memory_play
    q.update()
    assert q.folder('/mnt/mmc/Podcasts/Show') == ['Episode 1.mp3']
    q.pick_file('Episode 1.mp3')
    q.play_for(10 * 60)
    q.call('player_play_pause')  # paused at about 10:00
    at = q.elapsed()
    q.play_for(5)
    assert place(q, EPISODE) == at  # the place is kept as the time stands still
    q.call('save_memoryplay_info')  # the power-off page's save

    q = restart(q, memory_play)
    out = q.boot_resume()
    assert [text_of(r, 'path') for r in q.deques[out]] == [EPISODE]
    assert q.opened == [EPISODE] and q.status == PAUSED  # paused on Home until Play
    q.play_for(3)
    assert q.seeks == [] and q.elapsed() == (0 if memory_play == TRACK else at)  # a paused boot waits
    q.call('player_play_pause')
    q.play_for(3)
    # Location starts at the second the snapshot kept; Track at 0, and the episode's own place takes it there
    assert q.seeks == ([at] if memory_play == TRACK else []) and at < q.elapsed() <= at + 3
    # Previous past 10 s: stock starts a track over, but on a place it only asks, and plays on
    seeks, opened, toasts, sec = list(q.seeks), len(q.opened), len(q.toasts), q.elapsed()
    q.previous_key()
    q.play_for(4)
    assert q.seeks == seeks and len(q.opened) == opened and len(q.toasts) == toasts + 1 and q.elapsed() == sec + 4
    q.previous_key()  # more than 3 s after it asked: it asks again
    q.play_for(1)
    assert q.seeks == seeks and len(q.toasts) == toasts + 2 and place(q, EPISODE) == at
    q.previous_key()  # pressed again within the 3 s: from its start, and the place goes with it
    q.play_for(1)
    assert q.seeks == seeks + [0] and len(q.opened) == opened and q.elapsed() == 1 and place(q, EPISODE) is None


def test_a_shuffle_restarted_with_memory_playback_plays_on_in_the_order_it_had(q2, restart):
    files, tags = music_card()
    q = q2(files, tags)
    q.fw.var('g_memory_play', C.c_ubyte).value = TRACK
    q.update()
    q.fw.fn('shuffle_menu', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    q.tap('Shuffle Songs')
    rows = [title for title, _ in up_next(q)]
    q.play_for(2 * SONG + 5)  # the third song of the pass, 5 s in
    playing = title_of(q, q.opened[-1])
    assert playing == rows[1]
    q.call('save_memoryplay_info')

    q = restart(q, TRACK)
    q.boot_resume()
    q.call('player_play_pause')
    assert [title_of(q, p) for p in q.opened] == [playing] and q.elapsed() == 0  # Track: the song, from its start
    assert [title for title, _ in up_next(q)] == rows[2:]  # Up Next as it was
    q.play_for(11 * SONG)
    assert [title_of(q, p) for p in q.opened[1:]] == rows[2:]  # the rest of the pass, then the next as it was drawn


# Audiobooks: an M4B's chapters (ffmpeg's ffmetadata), a CUE book's place, and a book's page (books.c).
NOVEL = '/mnt/mmc/Audiobooks/Novel/Novel.m4b'


CHAPTERS = (b';FFMETADATA1\ntitle=Novel\n' + b''.join(
    b'[CHAPTER]\nTIMEBASE=1/1000\nSTART=%d\nEND=%d\ntitle=%s\n' % (start * 1000, end * 1000, title)
    for start, end, title in ((0, 1200, b'Opening'), (1200, 3000, b'The Road'), (3000, 5400, b'The Sea'),
                              (5400, 10800, b'Home'))))


class Ffmpeg:
    """/usr/bin/ffmpeg as chapters.c runs it, forked onto a pipe: its ffmetadata, then a clean exit."""
    def __init__(self, q, output):
        self.output, self.left, self.runs = output, b'', 0
        q.fw.mock('chapter_pipe2', self.pipe2)
        q.fw.mock('fw_fork', lambda *_: 4321)
        q.fw.mock('fw_fcntl', lambda *_: 0)
        q.fw.mock('fw_read', self.read)
        q.fw.mock('fw_waitpid', lambda pid, status, *_: C.c_int.from_address(status).__setattr__('value', 0) or pid)

    def pipe2(self, fds, flags, *_):
        C.cast(fds, C.POINTER(C.c_int))[0], C.cast(fds, C.POINTER(C.c_int))[1] = 5, 6
        self.left, self.runs = self.output, self.runs + 1
        return 0

    def read(self, fd, out, n, *_):
        chunk, self.left = self.left[:n], self.left[n:]
        C.memmove(out, chunk, len(chunk))
        return len(chunk)


def chapters(q):
    """Now Playing's hold → Chapters, once ffmpeg has answered: its rows, each a title over its start."""
    q.hold_playing(QA['CHAPTERS'])
    q.run_timers()
    return q.rows_of(q.last('scroll_view_create'))


def test_an_audiobooks_chapter_takes_it_there_and_its_place_follows_it(q2):
    files, tags = music_card()
    tags[NOVEL]['seconds'] = 3 * 3600
    q = q2(files, tags)
    Ffmpeg(q, CHAPTERS)
    q.update()
    q.folder('/mnt/mmc/Audiobooks/Novel')
    q.pick_file('Novel.m4b')
    q.play_for(3)
    q.call('player_seek_time', 25 * 60)  # a scrub to 25:00
    q.play_for(60)
    assert chapters(q) == [('Opening', '0:00:00'), ('The Road', '0:20:00'), ('The Sea', '0:50:00'), ('Home', '1:30:00')]
    page = q.pages[-1]
    q.tap('The Sea')
    assert q.seeks[-1] == 3000 and page not in q.pages  # there, and the page closes
    q.play_for(65)
    assert 3065 - 60 < place(q, NOVEL) <= 3065  # the place moves on with the listening
    q.folder('/mnt/mmc/Podcasts/Show')
    q.pick_file('Episode 1.mp3')
    q.play_for(5)
    assert place(q, NOVEL) == 3065  # another track: the book's place where it was left
    q.folder('/mnt/mmc/Audiobooks/Novel')
    q.pick_file('Novel.m4b')
    q.play_for(3)
    assert q.seeks[-1] == 3065  # back to the book: where it was
    playing = len(q.opened), list(q.seeks), q.elapsed()
    q.pick_file('Novel.m4b')
    assert (len(q.opened), q.seeks, q.elapsed()) == playing  # its row again, playing on its place: nothing starts over
    q.folder('/mnt/mmc/Podcasts/Show')
    q.pick_file('Episode 1.mp3')
    q.play_for(5)
    q.folder('/mnt/mmc/Audiobooks/Novel')
    q.pick_file('Novel.m4b')
    assert chapters(q)[1] == ('The Road', '0:20:00')
    q.tap('The Road')  # a chapter picked as the book starts, before its place could take it back
    q.play_for(5)
    assert q.seeks[-1] == 1200 and q.elapsed() == 1205  # the chapter, not the place


SAGA = '/mnt/mmc/Audiobooks/Saga/Saga.mp3'


def test_a_cue_books_place_is_the_files_and_resume_takes_it_back_into_the_chapter(q2):
    files, tags = music_card()
    tags[SAGA] = dict(album='Saga', artist='Teller', seconds=2400,
                      cue=[(0, 600, 'Part 1'), (600, 1500, 'Part 2'), (1500, 0, 'Part 3')])
    files[SAGA] = b'\0' * 64
    q = q2(files, tags)
    q.update()
    assert q.folder('/mnt/mmc/Audiobooks/Saga') == ['Part 1', 'Part 2', 'Part 3']
    q.pick_file('Part 2')
    q.play_for(20)
    assert place(q, SAGA) is None  # a chapter's first 30 s keep the book's place as it was
    q.play_for(80)
    q.folder('/mnt/mmc/Podcasts/Show')
    q.pick_file('Episode 1.mp3')
    q.play_for(3)
    assert place(q, SAGA) == 600 + 100  # the file's second: the chapter's start and what was heard of it
    q.folder('/mnt/mmc/Audiobooks/Saga')
    q.pick_file('Part 3')
    q.play_for(3)
    assert q.opened[-1] == (SAGA, 1500) and q.seeks == [] and q.elapsed() == 3  # a chapter picked: from its start
    q.folder('/mnt/mmc/Podcasts/Show')
    q.pick_file('Episode 1.mp3')
    q.play_for(3)
    assert place(q, SAGA) == 700  # Part 3's 3 s were no place
    q.folder('/mnt/mmc/Audiobooks/Saga')
    q.hold('Part 1', QA['RESUME'])  # the hold menu's Resume on any of its rows
    q.play_for(3)
    # the chapter holding the place, then into it once it plays (a new track waits a poll), and on from there
    assert q.opened[-1] == (SAGA, 600) and q.seeks == [100] and 100 < q.elapsed() <= 100 + 3


BOOKS = '/mnt/mmc/Books'


class Reader:
    """Books' page as the paint hook draws it: the canvas's lines of text, each character 10 px wide."""
    def __init__(self, q):
        self.q, self.lines = q, []
        self.lcd = C.create_string_buffer(0x200)
        self.canvas = C.create_string_buffer(0x100)
        struct.pack_into('<Q', self.canvas, O['CANVAS_LCD'], C.addressof(self.lcd))
        q.fw.mock('canvas_measure_text', lambda *_: 10.0)
        q.fw.mock('canvas_draw_text', lambda c, s, n, *_: self.lines.append(C.string_at(s, 4 * n).decode('utf-32-le')))

    def bk(self, k):
        """books.c's state: its page and its sheet, the first and fifth pointers."""
        return C.c_void_p.from_address(C.addressof(self.q.fw.var('books__bk', C.c_char)) + 8 * k).value

    def page(self):
        """A paint of the reader: the lines it shows."""
        self.lines = []
        self.q.fw.fn('books_paint', None, C.c_void_p, C.c_void_p)(self.bk(4), self.canvas)
        return self.lines

    def key(self, key):
        """A key, as ringnav() hands the reader its keys; Return as the page's key-up gets it, which leaves on the
        next pass."""
        if key == O['KEY_RETURN']:
            fn, ctx = self.q.handlers[self.bk(0), O['EVT_KEY_UP']]
            event = C.create_string_buffer(0x40)
            struct.pack_into('<i', event, O['EVENT_KEY'], key)
            C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, event)
            self.q.run_timers()
        else:
            self.q.fw.fn('books_key', C.c_int, C.c_void_p, C.c_uint)(self.bk(0), key)


def open_books(q):
    """Local Music → Books: its rows, each a book's title."""
    q.fw.fn('books_open', None, C.c_char_p)(BOOKS.encode())
    return [row[0] for row in q.rows_of(q.last('scroll_view_create'))]


def lines_from(n, count):
    """The book's lines n on, count of them: Tale's text is 'Line 001' to 'Line 100', a line each."""
    return [f'Line {k:03}' for k in range(n, n + count)]


def test_a_book_opens_again_after_a_restart_on_the_page_it_was_left_on(q2, restart):
    tale = ''.join(line + '\n' for line in lines_from(1, 100)).encode()
    files = {BOOKS + '/Novels/Tale.txt': tale, BOOKS + '/Notes.txt': b'A note.\n', BOOKS + '/cover.jpg': b'',
             BOOKS + '/.hidden.txt': b''}
    q = q2(files)
    reader = Reader(q)
    assert open_books(q) == ['Notes', 'Tale']  # its books and one folder down, by path; no picture, no dot file
    books = q.last('window_create')
    q.tap('Tale')
    q.run_timers()
    pages = [reader.page()]
    assert pages[0] and pages[0] == lines_from(1, len(pages[0]))  # from its start, line by line
    for _ in range(3):
        reader.key(O['KEY_NEXT'])
        page = reader.page()
        after = int(pages[-1][-1].split()[1]) + 1
        assert page and page == lines_from(after, len(page))  # on from the line after the last one shown
        pages.append(page)
    reader.key(O['KEY_PREV'])
    left = reader.page()
    assert left == pages[2]  # three pages on, one back
    reader.key(O['KEY_RETURN'])  # to the list
    reader.key(O['KEY_RETURN'])  # out of Books
    assert books not in q.pages

    q = restart(q)
    reader = Reader(q)
    assert open_books(q) == ['Notes', 'Tale']
    q.tap('Tale')
    q.run_timers()
    assert reader.page() == left  # where it was left
    reader.key(O['KEY_RETURN'])
    q.tap('Notes')
    q.run_timers()
    assert reader.page() == ['A note.']  # each book its own place: this one never read
