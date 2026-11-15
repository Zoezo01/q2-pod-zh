"""End to end: the online features as a user meets them, through their real entry points, with stock's
network, card, thread and UI calls stubbed at their boundary (docs/internals.md: Network Update, Q2 Shelf,
Card apps, Spotify, Tidal cache and track lists, the lyrics and cover match).

One world stands in for the Q2 around the firmware: the card and /tmp as dicts (the card with its room),
the shell for the commands the firmware runs (rm, busybox unzip, find, kill and the launchers), demo's
libcurl over routes by URL, AF_UNIX sockets to the card's programs, and the window manager: its widgets,
timers, toasts and threads. A worker thread runs at the next UI pass after it starts, as it would beside
the UI loop, or when a test says; a transfer can hand the UI passes, presses or another track while it
runs, which is where cancels and skips land."""
import ctypes as C
import fnmatch
import json
import hashlib
import re
import shlex
import struct

import pytest

import host
from conftest import O, Deques, block, high_halves, libc
from test_addon import Dirent
from test_lyrics import Ly

TAG = host.tools_build.VERSION
_n = int(TAG.replace('.', '')) + 1
NEWER = f'{_n // 100}.{_n // 10 % 10}.{_n % 10}'  # the next single-digit tag, as netupdate's strcmp orders them
RET_REPEAT = 8
TOAST = 'dialog/msginfo_dialog'
MB = 1 << 20

TIMER = C.CFUNCTYPE(C.c_int, C.c_void_p)
HANDLER = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
THREAD = C.CFUNCTYPE(C.c_void_p, C.c_void_p)
WRITE = C.CFUNCTYPE(C.c_uint, C.c_void_p, C.c_uint, C.c_uint, C.c_void_p)
XFER = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_longlong, C.c_longlong, C.c_longlong, C.c_longlong)


def s(p):
    return C.string_at(p).decode('utf-8', 'surrogateescape')


class Clock:
    """time_now_ms and CLOCK_MONOTONIC, one clock in ms; the firmware's sleeps move it."""
    def __init__(self, w, ms=50_000_000):
        self.ms, self.slept, self.hooks = ms, [], []  # hooks: what else moves with time (librespot)
        def gettime(which, ts, *_):
            t = C.cast(ts, C.POINTER(C.c_long))  # struct { long s, ns; }
            t[0], t[1] = self.ms // 1000, self.ms % 1000 * 1000000
        w.mock('time_now_ms', lambda *_: self.ms & 0xffffffff)
        w.mock('fw_clock_gettime', gettime)
        w.mock('sleep_ms', lambda ms, *_: self.sleep(ms))
        w.mock('fw_usleep', lambda us, *_: self.sleep(us // 1000))

    def sleep(self, ms):
        self.slept.append(ms)
        self.ms += ms
        self.moved()

    def moved(self):
        for hook in self.hooks:
            hook()


class Handle:
    def __init__(self, path, write):
        self.path, self.at, self.write, self.error = path, 0, write, 0


class Card:
    """The card and /tmp: files by path, folders as a set, stdio and the calls around it; the card (and only
    it) has room, which writes take and unlinks give back. Everything that changes a file is logged."""
    def __init__(self, w, room=4 << 30):
        self.w, self.room, self.log = w, room, w.log
        self.files, self.dirs, self.mtime, self.reads = {}, {'/', '/mnt', '/mnt/mmc', '/tmp', '/etc', '/proc'}, {}, {}
        self.files['/proc/mounts'] = b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'  # the card is in (card_mounted)
        self.handles, self.lists, self.listed, self.next = {}, {}, {}, 0x3000
        for name in ('fopen', 'fread', 'fwrite', 'fputs', 'fseek', 'ftell', 'fclose', 'fflush', 'ferror', 'fileno', 'fsync', 'rename',
                     'unlink', 'mkdir', 'rmdir', 'access', '__xstat', 'statfs', 'opendir', 'readdir', 'closedir'):
            w.mock('fw_' + name, getattr(self, name.strip('_')))

    @staticmethod
    def oncard(p):
        return p.startswith('/mnt/mmc/')

    @staticmethod
    def parent(p):
        return p.rsplit('/', 1)[0] or '/'

    def exists(self, p):
        return p in self.files or p in self.dirs

    def take(self, p, size):
        """size more bytes for p: whether the card had them."""
        if self.oncard(p):
            if size > self.room:
                return False
            self.room -= size
        return True

    def put(self, p, data=b'', count=True):
        """p's bytes, its folders made; counted against the room unless count is off (a card as it came)."""
        old = len(self.files.get(p, b''))
        if count and not self.take(p, len(data) - old):
            return False
        d = self.parent(p)
        while d not in self.dirs:
            self.dirs.add(d)
            d = self.parent(d)
        self.files[p] = bytes(data)
        return True

    def drop(self, p):
        """rm -rf p."""
        for f in [f for f in self.files if f == p or f.startswith(p + '/')]:
            if self.oncard(f):
                self.room += len(self.files[f])
            del self.files[f]
        self.dirs -= {d for d in self.dirs if d == p or d.startswith(p + '/')}

    def tree(self, top='/mnt/mmc'):
        return {p[len(top) + 1:]: d for p, d in self.files.items() if p.startswith(top + '/')}

    def fopen(self, path, mode, *_):
        p, how = s(path), s(mode)
        self.reads[p] = self.reads.get(p, 0) + ('r' in how)
        if 'r' in how and not self.exists(p) or 'r' not in how and self.parent(p) not in self.dirs:
            return 0
        if 'w' in how:
            if self.oncard(p):
                self.room += len(self.files.get(p, b''))
            self.files[p] = b''
            self.log.append(('create', p))
        self.files.setdefault(p, b'')  # 'a' on a file not there yet
        f, self.next = self.next, self.next + 1
        self.handles[f] = Handle(p, 'r' not in how)
        return f

    def fread(self, out, size, n, f, *_):
        h = self.handles[f]
        data = self.files.get(h.path, b'')
        have = max(0, min(n, (len(data) - h.at) // size)) if size else 0
        C.memmove(out, data[h.at:h.at + have * size], have * size)
        h.at += have * size
        return have

    def written(self, f, data, items, size):
        h = self.handles[f]
        fits = items if not self.oncard(h.path) else min(items, self.room // size if size else items)
        self.take(h.path, fits * size)
        self.files[h.path] += data[:fits * size]
        if fits < items:
            h.error = 1
        return fits

    def fwrite(self, data, size, n, f, *_):
        return self.written(f, C.string_at(data, size * n), n, size)

    def fputs(self, text, f, *_):
        data = C.string_at(text)
        return 1 if self.written(f, data, len(data), 1) == len(data) else -1

    def fseek(self, f, offset, whence, *_):
        h = self.handles[f]
        h.at = C.c_long(offset).value + {0: 0, 1: h.at, 2: len(self.files.get(h.path, b''))}[whence]
        return 0

    def ftell(self, f, *_):
        return self.handles[f].at

    def fclose(self, f, *_):
        return -1 if self.handles.pop(f).error else 0

    def fflush(self, f, *_):
        return -1 if self.handles[f].error else 0

    def ferror(self, f, *_):
        return self.handles[f].error

    def fileno(self, f, *_):
        return f

    def fsync(self, fd, *_):
        self.log.append(('fsync', self.handles[fd].path))
        return 0

    def rename(self, a, b, *_):
        a, b = s(a), s(b)
        if a not in self.files or self.parent(b) not in self.dirs:
            return -1
        if b in self.files and self.oncard(b):
            self.room += len(self.files[b])
        self.files[b] = self.files.pop(a)
        self.log.append(('rename', a, b))
        return 0

    def unlink(self, path, *_):
        p = s(path)
        if p not in self.files:
            return -1
        if self.oncard(p):
            self.room += len(self.files[p])
        del self.files[p]
        self.log.append(('unlink', p))
        return 0

    def mkdir(self, path, mode, *_):
        p = s(path)
        if self.exists(p) or self.parent(p) not in self.dirs:
            return -1
        self.dirs.add(p)
        return 0

    def rmdir(self, path, *_):
        p = s(path)
        if p not in self.dirs or any(x.startswith(p + '/') for x in [*self.files, *self.dirs]):
            return -1
        self.dirs.discard(p)
        self.log.append(('rmdir', p))
        return 0

    def access(self, path, mode, *_):
        return 0 if self.exists(s(path)) else -1

    def xstat(self, version, path, info, *_):
        p = s(path)
        if not self.exists(p):
            return -1
        st = C.cast(info, C.POINTER(C.c_uint))  # glibc 2.28 MIPS o32: mode word 5, size 12, mtime 16
        st[5] = 0o100644 if p in self.files else 0o040755
        st[12], st[16] = len(self.files.get(p, b'')), self.mtime.get(p, 1)
        return 0

    def statfs(self, path, buf, *_):
        words = C.cast(buf, C.POINTER(C.c_uint))  # MIPS o32: f_bsize word 1, f_bavail word 7
        words[1], words[7] = 4096, self.room // 4096 if s(path).startswith('/mnt/mmc') else 1 << 20
        return 0

    def opendir(self, path, *_):
        p = s(path)
        if p not in self.dirs:
            return 0
        names = sorted({x[len(p) + 1:].split('/')[0] for x in [*self.files, *self.dirs] if x.startswith(p + '/')})
        d, self.next = self.next, self.next + 1
        self.lists[d], self.listed[d] = [Dirent(d_name=n.encode()) for n in names], 0
        return d

    def readdir(self, d, *_):
        """The next entry, kept to the test's end: the C reads its d_name after readdir returns, as
        from a DIR's own buffer."""
        rows, k = self.lists[d], self.listed[d]
        if k == len(rows):
            return 0
        self.listed[d] = k + 1
        return C.addressof(rows[k])

    def closedir(self, d, *_):
        return 0


ZIP = b'PK-fake\n'


def zipped(files):
    """A release's zip as the shell's unzip reads it: its entries and their text."""
    return ZIP + json.dumps(sorted(files.items())).encode()


class Shell:
    """system(): the firmware's commands run on the card, each '; '-separated piece by the first hook that
    matches it, else as rm, busybox unzip or find; anything else is answered 0."""
    def __init__(self, w):
        self.w, self.ran, self.hooks, self.watch = w, [], [], None
        w.mock('fw_system', self.system)

    def hook(self, pattern, fn):
        """fn(match) runs what matches pattern, ahead of the hooks before it (librespot's run before any app's)."""
        self.hooks.insert(0, (re.compile(pattern), fn))

    def system(self, cmd, *_):
        line = s(cmd)
        self.ran.append(line)
        self.w.log.append(('system', line))
        if self.watch:
            self.watch(line)
        status = 0
        for piece in line.split('; '):
            status = self.one(piece)
        return status

    def one(self, piece):
        for pattern, fn in self.hooks:
            m = pattern.fullmatch(piece)
            if m:
                return fn(m) or 0
        found = re.fullmatch(r'cd (\S+) && find (\S+) -type f > (\S+)', piece)
        if found:
            top, sub, out = found.groups()
            names = sorted(p[len(top) + 1:] for p in self.w.card.files if p.startswith(f'{top}/{sub}/'))
            return 0 if self.w.card.put(out, ''.join(n + '\n' for n in names).encode()) else 1
        if piece.startswith(('rm ', 'busybox ')):
            words = shlex.split(piece)
            if words[0] == 'rm':
                for p in words[2:]:
                    self.w.card.drop(p)
                return 0
            return self.unzip(words[2:])
        return 0

    def unzip(self, args):
        """busybox unzip [-l] [-o] -qq [-d DIR] ZIP PATTERN... -x PATTERN... [> OUT]: fnmatch without
        FNM_PATHNAME, so * spans /, as busybox 1.31.1 matches."""
        out, dest = None, None
        if '>' in args:
            out, args = args[args.index('>') + 1], args[:args.index('>')]
        opts, i = [], 0
        while args[i].startswith('-'):
            if args[i] == '-d':
                dest, i = args[i + 1], i + 2
            else:
                opts, i = opts + [args[i]], i + 1
        data, rest = self.w.card.files.get(args[i], b''), args[i + 1:]
        keep, skip = (rest[:rest.index('-x')], rest[rest.index('-x') + 1:]) if '-x' in rest else (rest, [])
        if not data.startswith(ZIP):
            return 9
        chosen = [(n, c.encode()) for n, c in json.loads(data[len(ZIP):])
                  if any(fnmatch.fnmatchcase(n, p) for p in keep) and not any(fnmatch.fnmatchcase(n, p) for p in skip)]
        if '-l' in opts:
            return 0 if self.w.card.put(out, ''.join(f'{len(c):9}  10-06-2026 13:18   {n}\n' for n, c in chosen).encode()) else 50
        return 0 if all(self.w.card.put(f'{dest}/{n}', c) for n, c in chosen) else 50


class Reply:
    """One transfer's answer: its code, body (Content-Length), redirect; cut: the byte of the body after which
    the connection drops; during(sent): called after each chunk and its progress call. A transfer carried on
    from a byte (RESUME_FROM) gets the body from there, as a 206."""
    def __init__(self, code=200, body=b'', location=None, cut=None, chunk=16384, during=None, fail=0):
        self.code, self.body, self.location, self.cut, self.chunk, self.during, self.fail = code, body, location, cut, chunk, during, fail


class Curl:
    def __init__(self):
        self.opts, self.code, self.location = {}, 0, None


class Net:
    """demo's libcurl: each transfer answered by the latest route its URL starts with; none, or the network
    down, is a host that can't be resolved. Bodies go through the write callback in chunks, the transfer's
    progress callback (when on) before and after each, as curl calls it."""
    def __init__(self, w):
        self.w, self.routes, self.asked, self.up, self.handles, self.keep = w, [], [], True, {}, []
        w.mock('fw_curl_easy_init', self.init)
        w.mock('fw_curl_easy_setopt', lambda c, opt, value, *_: self.handles[c].opts.__setitem__(opt, value))
        w.mock('fw_curl_easy_perform', self.perform)
        w.mock('fw_curl_easy_getinfo', self.getinfo)
        w.mock('fw_curl_easy_cleanup', lambda c, *_: self.handles.pop(c) and 0)

    def route(self, prefix, reply):
        self.routes.insert(0, (prefix, reply))

    def init(self, *_):
        c = 0x7000 + len(self.asked) * 16 + len(self.handles)
        self.handles[c] = Curl()
        return c

    def perform(self, c, *_):
        h = self.handles[c]
        url = s(h.opts[10002])
        self.asked.append(url)
        self.w.log.append(('get', url))
        reply = next((r for prefix, r in self.routes if url.startswith(prefix)), None)
        if not self.up or reply is None:
            return 6  # CURLE_COULDNT_RESOLVE_HOST
        reply = reply(url) if callable(reply) else reply
        h.code, h.location = reply.code, reply.location
        if reply.fail or h.opts.get(44):  # NOBODY: the headers alone
            return reply.fail
        write = WRITE(h.opts[20011]) if 20011 in h.opts else None
        progress = XFER(h.opts[20219]) if h.opts.get(43, 1) == 0 and 20219 in h.opts else None
        start = h.opts.get(21, 0)  # RESUME_FROM
        body, sent = reply.body[start:], 0
        end = len(body) if reply.cut is None else max(0, min(reply.cut - start, len(body)))
        if start and reply.code == 200:
            h.code = 206
        if progress and progress(h.opts.get(10057), len(body), 0, 0, 0):
            return 42  # CURLE_ABORTED_BY_CALLBACK
        while sent < end:
            piece = body[sent:min(end, sent + reply.chunk)]
            buf = C.create_string_buffer(piece, len(piece))
            if write and write(C.addressof(buf), 1, len(piece), h.opts.get(10001)) != len(piece):
                return 23  # CURLE_WRITE_ERROR
            sent += len(piece)
            if progress and progress(h.opts.get(10057), len(body), sent, 0, 0):
                return 42
            if reply.during:
                reply.during(sent)
        return 18 if sent < len(body) else 0  # CURLE_PARTIAL_FILE: shorter than its Content-Length

    def getinfo(self, c, what, out, *_):
        h = self.handles[c]
        if what == 0x200002:  # RESPONSE_CODE
            C.c_long.from_address(out).value = h.code
        elif what == 0x10001f:  # REDIRECT_URL
            loc = C.create_string_buffer(h.location.encode()) if h.location else None
            self.keep.append(loc)
            C.c_void_p.from_address(out).value = C.addressof(loc) if loc else None
        return 0


class Server:
    """A card program's socket: whether it listens now, its answer to a stream request, its datagrams."""
    def __init__(self, listening, answer=None, datagram=None):
        self.listening, self.answer, self.datagram = listening, answer, datagram


class Sockets:
    """AF_UNIX sockets to the card's programs, by path. A stream connection carries one request, answered
    once the firmware shuts its writing side, and read back 4 KB a recv; a datagram goes to its server."""
    def __init__(self, w):
        self.servers, self.fds, self.next = {}, {}, 40
        w.mock('fw_socket', self.socket)
        w.mock('fw_connect', self.connect)
        w.mock('fw_fcntl', lambda *_: 0)
        w.mock('fw_select', lambda *_: 1)
        w.mock('fw_send', lambda fd, buf, n, *_: self.fds[fd].__setitem__('out', self.fds[fd]['out'] + C.string_at(buf, n)) or n)
        w.mock('spot_shut', self.shut)
        w.mock('fw_recv', self.recv)
        w.mock('fw_close', lambda fd, *_: self.fds.pop(fd, None) and 0)
        w.mock('fw_sendto', self.sendto)

    def socket(self, *_):
        fd, self.next = self.next, self.next + 1
        self.fds[fd] = {'path': None, 'out': b'', 'in': None}
        return fd

    def connect(self, fd, addr, *_):
        server = self.servers.get(s(addr + 2))  # struct sockaddr_un: the family, then the path
        if not server or not server.listening():
            return -1
        self.fds[fd]['path'] = s(addr + 2)
        return 0

    def shut(self, fd, *_):
        sock = self.fds[fd]
        sock['in'] = self.servers[sock['path']].answer(sock['out'])
        return 0

    def recv(self, fd, buf, n, *_):
        sock = self.fds[fd]
        piece, sock['in'] = sock['in'][:min(n, 4096)], sock['in'][min(n, 4096):]
        C.memmove(buf, piece, len(piece))
        return len(piece)

    def sendto(self, fd, data, n, flags, to, *_):
        server = self.servers.get(s(to + 2))
        if server and server.listening() and server.datagram:
            server.datagram(C.string_at(data, n))
        return n


class Widget:
    def __init__(self, parent, kind, name, text):
        self.parent, self.kind, self.name, self.text = parent, kind, name, text
        self.children, self.visible, self.live = [], True, True


class Ui:
    """The window manager: widgets as a tree (each a zeroed block, its parent at W_PARENT), their handlers,
    texts and visibility; timers on the clock; toasts; Wi-Fi's bars; and pthreads, run at the next UI pass
    (or at once, eager) and joined by running what is left of them."""
    CREATORS = ('window_create', 'list_view_create', 'scroll_view_create', 'list_item_create', 'hscroll_label_create',
                'image_create', 'label_create', 'view_create', 'button_create')

    def __init__(self, w, clock):
        self.w, self.clock = w, clock
        self.widgets, self.keep, self.handlers, self.stale = {}, [], [], []
        self.timers, self.tid = {}, 0
        self.threads, self.started, self.eager = [], 0, False
        self.toasts, self.loop, self.wifi, self.selected, self.dialogs, self.names = [], [], -1, {}, {}, {}
        self.root = self.new(0, 'window_manager')  # the windows' parent: window_manager()
        w.mock('window_manager', lambda *_: self.root)
        w.mock('widget_get_prop_str', self.prop_str)
        for name in self.CREATORS:
            if name in w.fw.stubs:
                w.mock(name, lambda parent, *_, k=name: self.new(parent, k[:-7]))
        w.mock('widget_factory_create_widget', lambda f, kind, parent, *_: self.new(parent, s(kind)))
        w.mock('widget_get_child', lambda p, i, *_: self.get(p).children[i] if p in self.widgets and i < len(self.get(p).children) else 0)
        w.mock('widget_count_children', lambda p, *_: len(self.get(p).children) if p in self.widgets else 0)
        w.mock('widget_destroy_children', lambda p, *_: p in self.widgets and [self.destroy(c) for c in list(self.get(p).children)] and 0)
        w.mock('widget_destroy', lambda p, *_: self.destroy(p))
        w.mock('window_close', lambda p, *_: self.destroy(p))
        w.mock('navigator_back', lambda *_: self.back())
        w.mock('window_manager_get_top_window', lambda *_: self.top())
        w.mock('widget_lookup', lambda p, name, *_: self.lookup(p, s(name)))
        w.mock('widget_set_name', lambda p, name, *_: setattr(self.get(p), 'name', s(name)) if p in self.widgets else 0)
        w.mock('widget_on', self.on)
        w.mock('widget_set_text_utf8', lambda p, t, *_: self.write(p, s(t)))
        w.mock('widget_set_tr_text', lambda p, t, *_: self.write(p, 'tr:' + s(t)))
        w.mock('widget_get_text_utf8', self.read)
        w.mock('widget_set_visible', lambda p, v, *_: setattr(self.get(p), 'visible', bool(v)) if p in self.widgets else 0)
        w.mock('widget_get_prop_int', lambda p, name, default, *_: self.selected.get(p, 0) if s(name) == '_ringnav_index' else default)
        w.mock('timer_add', self.timer_add)
        w.mock('timer_remove', lambda i, *_: self.timers.pop(i, None) and 0)
        w.mock('navigator_to_with_context', self.navigate)
        w.mock('get_wifisignal', lambda *_: self.wifi)
        w.mock('fw_pthread_create', self.create)
        w.mock('fw_pthread_join', self.join)
        w.mock('fw_pthread_self', lambda *_: 7)

    def get(self, p):
        return self.widgets[p]

    def prop_str(self, p, name, default, *_):
        """A widget's name, as AWTK hands out its widget->name: one buffer per name a widget has had,
        kept to the test's end, so a name the C holds stays readable while it asks again."""
        if s(name) != 'name' or p not in self.widgets:
            return default
        key = (p, self.get(p).name)
        if key not in self.names:
            self.names[key] = C.create_string_buffer(key[1].encode())
        return C.addressof(self.names[key])

    def new(self, parent, kind, name='', text=''):
        if kind == 'window' and not parent:
            parent = self.root
        buf = C.create_string_buffer(0x400)
        self.keep.append(buf)
        p = C.addressof(buf)
        C.c_void_p.from_address(p + O['W_PARENT']).value = parent or None
        self.widgets[p] = Widget(parent, kind, name, text)
        if parent in self.widgets:
            self.get(parent).children.append(p)
        return p

    def write(self, p, text):
        if p in self.widgets and self.get(p).live:
            self.get(p).text = text
        elif p in self.widgets:
            self.stale.append((self.get(p).kind, text))

    def read(self, p, out, n, *_):
        data = self.get(p).text.encode() if p in self.widgets else b''
        C.memmove(out, data[:n - 1] + b'\0', min(len(data), n - 1) + 1)
        return 0

    def on(self, p, event, fn, ctx, *_):
        self.handlers.append((p, event, fn, ctx))
        return len(self.handlers)

    def fire(self, p, event):
        e = block(self.w.fw, size=0x40)
        for q, ev, fn, ctx in list(self.handlers):
            if q == p and ev == event:
                HANDLER(fn)(ctx, e)

    def destroy(self, p):
        if p not in self.widgets or not self.get(p).live:
            return 0
        for c in list(self.get(p).children):
            self.destroy(c)
        self.fire(p, O['EVT_DESTROY'])
        self.get(p).live = False
        parent = self.get(p).parent
        if parent in self.widgets and p in self.get(parent).children:
            self.get(parent).children.remove(p)
        return 0

    def windows(self):
        return [p for p, x in self.widgets.items() if x.kind == 'window' and x.live]

    def top(self):
        return (self.windows() or [0])[-1]

    def back(self):
        self.destroy(self.top())
        return 0

    def lookup(self, p, name):
        for c in self.under(p):
            if self.get(c).name == name:
                return c
        return 0

    def under(self, p):
        for c in self.get(p).children if p in self.widgets else ():
            yield c
            yield from self.under(c)

    # what the user sees
    def texts(self, p):
        """The texts on a page, in order, of what shows (each widget and its parents visible)."""
        return [self.get(c).text for c in self.under(p) if self.get(c).text and self.shows(c)]

    def shows(self, p):
        while p in self.widgets:
            if not self.get(p).visible:
                return False
            p = self.get(p).parent
        return True

    def rows(self, page):
        """A page_list's rows: each list item's caption and, when it has one, its detail."""
        out = []
        for c in self.under(page):
            if self.get(c).kind == 'list_item':
                labels = [self.get(x).text for x in self.get(c).children if self.get(x).kind == 'hscroll_label']
                out.append(tuple(labels) if len(labels) > 1 else labels[0] if labels else '')
        return out

    def row(self, page, caption):
        for c in self.under(page):
            x = self.get(c)
            if x.kind == 'list_item' and x.children and self.get(x.children[0]).text == caption:
                return c
        raise AssertionError(f'no row {caption!r} in {self.rows(page)}')

    def click(self, p):
        self.fire(p, O['EVT_CLICK'])

    def press(self, page, caption):
        self.click(self.row(page, caption))

    def navigate(self, path, info, *_):
        if s(path) in self.dialogs:  # a modal dialog: its own loop runs until it closes
            self.dialogs[s(path)]()
        if s(path) == TOAST:
            self.toasts.append(s(info + 8))
            self.w.log.append(('toast', self.toasts[-1]))
        return 0

    # time
    def timer_add(self, fn, ctx, ms, *_):
        self.tid += 1
        self.timers[self.tid] = [fn, ctx, ms, self.clock.ms + ms]
        return self.tid

    def due(self):
        while True:
            ready = sorted((t[3], i) for i, t in self.timers.items() if t[3] <= self.clock.ms)
            if not ready:
                return
            i = ready[0][1]
            fn, ctx, ms, _ = self.timers[i]
            again = TIMER(fn)(ctx) == RET_REPEAT
            if i in self.timers:
                if again:
                    self.timers[i][3] = self.clock.ms + max(ms, 1)
                else:
                    del self.timers[i]

    def run(self, ms=0, step=50):
        """ms of UI loop passes, step apart: the threads started meanwhile run, then each pass's polls
        (ringnav_sleep's) and the timers due."""
        self.work()
        self.due()
        end = self.clock.ms + ms
        while self.clock.ms < end:
            self.clock.ms = min(end, self.clock.ms + step)
            self.clock.moved()
            self.work()
            for poll in self.loop:
                poll()
            self.due()

    # threads
    def create(self, t, attr, fn, arg, *_):
        self.started += 1
        C.c_ulong.from_address(t).value = self.started
        if self.eager:
            THREAD(fn)(arg)
        else:
            self.threads.append((self.started, fn, arg))
        return 0

    def work(self):
        while self.threads:
            _, fn, arg = self.threads.pop(0)
            THREAD(fn)(arg)

    def join(self, thread, ret, *_):
        for k, (i, fn, arg) in enumerate(self.threads):
            if i == thread:
                del self.threads[k]
                THREAD(fn)(arg)
                break
        return 0


class CardApps:
    """The card apps' launchers (docs/internals.md#card-apps): run starts the app unless its loop runs, and
    leaves the loop's pid in /tmp/q2-<id>; a TERM to it ends the app and the loop removes its pid file. An
    app served here listens on its page's socket half a second after it starts."""
    def __init__(self, w):
        self.w, self.started = w, {}
        w.shell.hook(r'/bin/sh /mnt/mmc/\.([a-z0-9-]+)/run', self.run)
        w.shell.hook(r'kill \$\(cat (/tmp/q2-[a-z0-9-]+)\) 2>/dev/null \|\| rm -f \1', lambda m: w.card.drop(m[1]))

    def run(self, m):
        if f'/tmp/q2-{m[1]}' not in self.w.card.files:
            self.started[m[1]] = self.w.clock.ms
            self.w.card.put(f'/tmp/q2-{m[1]}', b'321')

    def serve(self, app, answer):
        def listening():
            return f'/tmp/q2-{app}' in self.w.card.files and self.w.clock.ms >= self.started.get(app, 0) + 500
        self.w.sockets.servers[f'/tmp/q2-{app}.browse.sock'] = Server(listening, answer=answer)


class World:
    """The Q2 around the firmware, and the calls its fakes saw, in order (log)."""
    def __init__(self, fw):
        self.fw, self.log, self.errors = fw, [], []
        self.clock = Clock(self)
        self.card = Card(self)
        self.shell = Shell(self)
        self.net = Net(self)
        self.sockets = Sockets(self)
        self.ui = Ui(self, self.clock)
        self.apps = CardApps(self)
        self.playing = 1  # the local player: 1 stopped, 2 playing, 3 paused
        self.mock('mclGetPlayStatus', lambda *_: self.playing)
        self.mock('tk_str_start_with', lambda a, b, *_: bool(a and b) and C.string_at(a).startswith(C.string_at(b)))  # AWTK's

    def mock(self, name, fn):
        if name not in self.fw.stubs:  # nothing in this build calls it
            return
        def safe(*args):
            try:
                return fn(*args)
            except BaseException as e:  # a fake's own failure: kept for the test, as ctypes would drop it
                self.errors.append(e)
                return 0
        self.fw.mock(name, safe)

    def fn(self, name, restype, *argtypes):
        return self.fw.fn(name, restype, *argtypes)

    def calls(self, name):
        return [c[1] for c in self.fw.calls(name)]

    def byte(self, name, value):
        self.fw.var(name, C.c_ubyte).value = value


@pytest.fixture
def w(fw, mcl):
    world = World(fw)
    yield world
    if world.errors:
        raise world.errors[0]
    assert not world.ui.stale, f'text written to widgets already gone: {world.ui.stale}'


# Network Update (System Update's second row)

LATEST = 'https://github.com/owner/repo/releases/latest'  # host.py builds with --update-repo's owner/repo
API = 'https://api.github.com/repos/owner/repo/releases/tags/'
ASSET = f'https://github.com/owner/repo/releases/download/{NEWER}/update.tar'
TAR = '/mnt/mmc/update.tar'
PART = TAR + '.part'
UPDATE_TAR = bytes(range(256)) * 2048  # 512 KB


def tag_url(tag):
    return f'https://github.com/owner/repo/releases/tag/{tag}'


class SystemUpdate:
    """Stock's System Update page as its builder leaves it, two rows of a button (named by its index) over
    its label, with ringnav_fwupdate run on it: the second row is Network Update's."""
    def __init__(self, w, battery=80):
        self.w, ui = w, w.ui
        w.fw.var('fw_ctx_g_power_capacity', C.c_int).value = battery
        self.page = ui.new(0, 'window', 'systemset_fwupdate_page')
        view = ui.new(self.page, 'view', 'scroll_view_fwupdate')
        self.buttons, self.labels = [], []
        for i, text in enumerate(('tr:update_tf', 'tr:update_online')):
            self.buttons.append(ui.new(ui.new(view, 'row'), 'button', str(i)))
            self.labels.append(ui.new(self.buttons[-1], 'label', text=text))
        w.mock('stock_fwupdate_trampoline', lambda *_: 0)
        w.mock('fwupdate_tf', lambda *_: w.log.append(('fwupdate_tf',)))
        assert w.fn('ringnav_fwupdate', C.c_int, C.c_void_p, C.c_void_p)(self.page, None) == 0

    def press(self):
        self.w.ui.click(self.buttons[1])

    @property
    def text(self):
        return self.w.ui.get(self.labels[1]).text


def tar_release(tag, tar):
    """GitHub's record of a release, trimmed: its guide, then update.tar with its size and digest."""
    rows = ','.join('{"url":"u","id":1,"name":"%s","label":"","uploader":{"login":"x","id":2},"content_type":"z","state":"uploaded",'
                    '"size":%d,"digest":"sha256:%s","download_count":3,"browser_download_url":"https://h/%s"}'
                    % (name, len(data), hashlib.sha256(data).hexdigest(), name) for name, data in (('Q2-Pod-User-Guide.pdf', b'guide'), ('update.tar', tar)))
    return ('{"url":"x","assets_url":"y","tag_name":"%s","name":"%s","assets":[%s],"body":"notes"}' % (tag, tag, rows)).encode()


def releases(w, tag=NEWER, tar=UPDATE_TAR, sent=None, **asset):
    """The latest release: the redirect naming its tag, GitHub's record of it and its update.tar (sent: the
    bytes that arrive, when not the ones the record names)."""
    w.net.route(LATEST, Reply(302, location=tag_url(tag)))
    w.net.route(API + tag, Reply(200, tar_release(tag, tar)))
    sent = tar if sent is None else sent
    w.net.route(f'https://github.com/owner/repo/releases/download/{tag}/update.tar', Reply(200, sent, chunk=len(sent) // 4, **asset))


def test_network_update_checks_downloads_and_hands_the_tar_to_stocks_tf_update(w):
    w.ui.wifi = 3
    page = SystemUpdate(w)
    seen = []
    releases(w, during=lambda sent: (w.ui.run(500), seen.append(page.text)))
    assert page.buttons[1] and w.ui.get(page.buttons[1]).name == '2'  # stock's click (an atoi of it) passes it by
    page.press()
    assert page.text == 'Checking…'
    w.ui.run(500)
    assert seen == ['Downloading 25%', 'Downloading 50%', 'Downloading 75%', 'Downloading 100%']
    assert w.card.files[TAR] == UPDATE_TAR and PART not in w.card.files
    assert page.text == 'tr:update_online' and not w.ui.toasts
    # the part whole on the card, renamed, and the rename's entry on the card, before stock flashes it
    steps = [e for e in w.log if e[0] in ('fsync', 'rename', 'fwupdate_tf')]
    assert steps == [('fsync', PART), ('rename', PART, TAR), ('fsync', '/mnt/mmc'), ('fwupdate_tf',)]
    assert w.net.asked == [LATEST, API + NEWER, ASSET]
    assert w.card.reads[PART] == 3 and '/tmp/q2-update.json' not in w.card.files  # sized for the room, sized to carry on (none yet), then read back once
    held = w.calls('reset_poweroptions_timer')
    assert held and all(c[:3] == (1, 1, 0) for c in held)  # standby and auto power-off, not the screen
    w.ui.run(5000)
    assert len(w.calls('reset_poweroptions_timer')) == len(held) and not w.ui.timers


@pytest.mark.parametrize('tag', [TAG, '0.9.9'])
def test_network_update_on_the_latest_release_or_an_older_one_writes_nothing(w, tag):
    w.ui.wifi = 3
    page = SystemUpdate(w)
    releases(w, tag)
    page.press()
    w.ui.run(500)
    assert w.ui.toasts == ['Latest firmware installed'] and w.net.asked == [LATEST]
    assert not w.card.tree() and page.text == 'tr:update_online' and not w.calls('fwupdate_tf')


@pytest.mark.refusal('update:truncated')
def test_network_update_cut_off_midway_keeps_the_old_tar_and_carries_on_at_the_next_press(w):
    w.ui.wifi = 3
    w.card.put(TAR, b'the release before', count=False)
    page = SystemUpdate(w)
    half = len(UPDATE_TAR) // 2
    releases(w, cut=half)  # the connection drops there, at every try
    page.press()
    w.ui.run(500)
    assert w.ui.toasts == ["Can't reach GitHub"] and page.text == 'tr:update_online' and not w.calls('fwupdate_tf')
    assert w.card.tree() == {'update.tar': b'the release before', 'update.tar.part': UPDATE_TAR[:half]}
    assert w.net.asked.count(ASSET) == 4 and w.clock.slept.count(100) == 3 * 50  # 5 s between tries, in 100 ms steps
    # the network is back: the rest alone is fetched, and the whole is what the release names
    seen = []
    releases(w, during=lambda sent: (w.ui.run(500), seen.append(page.text)))
    page.press()
    w.ui.run(500)
    assert seen[0] == 'Downloading 74%' and seen[-1] == 'Downloading 100%'  # of the whole tar, not of the rest
    assert w.card.tree() == {'update.tar': UPDATE_TAR} and ('fwupdate_tf',) in w.log


def test_network_update_with_no_network_asks_once(w):
    w.ui.wifi = 3  # the bars, but no way out
    w.net.up = False
    page = SystemUpdate(w)
    page.press()
    w.ui.run(500)
    assert w.ui.toasts == ["Can't reach GitHub"] and w.net.asked == [LATEST] and not w.card.tree()


@pytest.mark.refusal('update:oversize')
def test_network_update_on_a_card_without_the_room_says_how_much_and_downloads_nothing(w):
    w.ui.wifi = 3
    w.card.room = len(UPDATE_TAR) // 3
    page = SystemUpdate(w)
    releases(w)
    page.press()
    w.ui.run(500)
    assert w.ui.toasts == ['Needs 65 MB more space'] and w.net.asked == [LATEST, API + NEWER]  # the tar twice over, and 64 MB
    assert not w.card.tree() and w.card.room == len(UPDATE_TAR) // 3


def test_network_update_on_a_card_that_fills_meanwhile_says_so_and_gives_the_room_back(w):
    w.ui.wifi = 3
    page = SystemUpdate(w)
    releases(w, during=lambda sent: setattr(w.card, 'room', 0))
    page.press()
    w.ui.run(500)
    assert w.ui.toasts == ["Can't write to the card"] and not w.card.tree() and w.card.room == len(UPDATE_TAR) // 4


def test_network_update_never_names_a_damaged_download_update_tar(w):
    """What arrived isn't what GitHub recorded for the release, by one byte: it goes, the old tar stays and
    stock's updater is never started."""
    w.ui.wifi = 3
    w.card.put(TAR, b'the release before', count=False)
    page = SystemUpdate(w)
    seen = []
    releases(w, sent=UPDATE_TAR[:-1] + b'!', during=lambda sent: sent == len(UPDATE_TAR) and seen.append(w.card.files[PART]))
    page.press()
    w.ui.run(500)
    assert seen == [UPDATE_TAR[:-1] + b'!'] and w.ui.toasts == ['The download was damaged, try again']
    assert w.card.tree() == {'update.tar': b'the release before'} and not w.calls('fwupdate_tf')


def test_network_update_on_a_low_battery_asks_nothing_of_the_network(w):
    w.ui.wifi = 3
    page = SystemUpdate(w, battery=15)
    releases(w)
    page.press()
    w.ui.run(500)
    assert w.ui.toasts == ['Charge the battery first'] and not w.net.asked and not w.card.tree() and not w.ui.started


def test_a_second_press_cancels_the_download_and_a_third_before_it_ends_does_nothing(w):
    w.ui.wifi = 3
    w.card.put(TAR, b'the release before', count=False)
    page = SystemUpdate(w)
    seen = []
    def during(sent):
        if sent == len(UPDATE_TAR) // 4:
            page.press()
            seen.append(page.text)
    releases(w, during=during)
    page.press()
    w.ui.work()
    assert seen == ['Cancelling…'] and w.net.asked == [LATEST, API + NEWER, ASSET]
    page.press()  # the thread has ended, not yet joined
    w.ui.run(500)
    assert w.ui.started == 1 and w.ui.toasts == ['Download cancelled']
    assert w.card.tree() == {'update.tar': b'the release before'} and page.text == 'tr:update_online'


def test_leaving_system_update_cancels_its_download_and_keeps_what_came(w):
    w.ui.wifi = 3
    page = SystemUpdate(w)
    quarter = len(UPDATE_TAR) // 4
    releases(w, during=lambda sent: sent == quarter and w.ui.destroy(page.page))
    page.press()
    w.ui.run(500)
    assert w.ui.toasts == ['Download cancelled'] and w.card.tree() == {'update.tar.part': UPDATE_TAR[:2 * quarter]}  # ended at the next progress call
    assert len(w.net.asked) == 3 and not w.calls('fwupdate_tf')


def test_a_download_that_ends_as_the_page_closes_says_where_it_is(w):
    w.ui.wifi = 3
    page = SystemUpdate(w)
    releases(w)
    page.press()
    w.ui.work()
    w.ui.destroy(page.page)  # after the rename, before the join
    w.ui.run(500)
    assert w.ui.toasts == ['Update downloaded: use TF card update'] and w.card.files[TAR] == UPDATE_TAR
    assert not w.calls('fwupdate_tf')


def test_a_worker_done_before_pthread_create_returns_is_still_reported(w):
    w.ui.wifi, w.ui.eager = 3, True
    page = SystemUpdate(w)
    releases(w)
    page.press()
    w.ui.run(500)
    assert w.card.files[TAR] == UPDATE_TAR and ('fwupdate_tf',) in w.log


# Q2 Shelf: System settings' App Store

LIST_URL = 'https://raw.githubusercontent.com/DiamondBond/q2-pod/main/sources.list'
STAGING = '/mnt/mmc/.q2/staging'


def api(repo):
    return f'https://api.github.com/repos/{repo}/releases/latest'


def release_json(tag, url, size):
    """GitHub's releases/latest, trimmed: a notes file, then the zip."""
    assets = [{'name': 'notes.txt', 'size': 5, 'browser_download_url': 'https://h/notes.txt'},
              {'name': url.rsplit('/', 1)[1], 'size': size, 'browser_download_url': url}]
    return json.dumps({'url': 'x', 'tag_name': tag, 'name': 'Release', 'assets': assets, 'body': 'notes'}).encode()


def app_zip(binary, **extra):
    """A card app's release: its folder, plus what never belongs on the card (a readme, macOS's ._ files)."""
    return {'.foo/q2-foo': binary, '.foo/q2app': 'name=Foo\n', '.foo/run': '#!/bin/sh\n', 'INSTALL.txt': 'read me',
            '.foo/._run': 'mac', '__MACOSX/.foo/._q2-foo': 'mac', **extra}


FOO_FILES = {'.foo/q2-foo', '.foo/q2app', '.foo/run'}


class GitHub:
    def __init__(self, w, sources):
        self.w = w
        w.net.route(LIST_URL, Reply(200, sources.encode()))

    def release(self, repo, tag, files, **reply):
        data = zipped(files)
        url = f'https://github.com/{repo}/releases/download/{tag}/{repo.split("/")[1]}.zip'
        self.w.net.route(api(repo), Reply(200, release_json(tag, url, len(data))))
        self.w.net.route(url, Reply(200, data, chunk=len(data) // 4 + 1, **reply))
        return url, data


class AppStore:
    """System settings' Apps page: Check for Updates, then a row per app in the list."""
    CHECK = 'Check for Updates'

    def __init__(self, w):
        self.w = w
        assert w.fn('shelf_open', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
        self.page = w.ui.top()

    def rows(self):
        """The apps' rows, under Check for Updates."""
        first, *apps = self.w.ui.rows(self.page)
        assert first[0] == self.CHECK
        return apps

    def detail(self, name):
        return dict(self.w.ui.rows(self.page))[name]

    def press(self, name):
        self.w.ui.press(self.page, name)


def installed(w, tag='1.0', extra=None, sources='me/q2-foo Foo\n'):
    """Foo as an install left it, with what it saved since."""
    for name, data in {'.foo/q2-foo': b'binary 1', '.foo/q2app': b'name=Foo\n', '.foo/run': b'#!/bin/sh\n',
                       '.q2/apps/foo.files': b'.foo/q2-foo\n.foo/q2app\n.foo/run\n', '.q2/apps/foo.tag': tag.encode(),
                       '.q2/sources.list': sources.encode(), **(extra or {})}.items():
        w.card.put('/mnt/mmc/' + name, data, count=False)


def streaming(w):
    """Streaming's card apps, as its page scans them."""
    n = w.fn('addon_scan', C.c_int)()
    return [w.fn('addon_name', C.c_char_p, C.c_int)(k).decode() for k in range(n)]


def test_the_app_store_lists_installs_and_streaming_finds_the_app(w):
    w.ui.wifi = 3
    hub = GitHub(w, 'me/q2-foo Foo\n')
    store = AppStore(w)
    shown = []
    _, data = hub.release('me/q2-foo', '1.0', app_zip('x' * 40000), during=lambda sent: (w.ui.run(500), shown.append(store.detail('Foo'))))
    assert store.rows() == [] and store.detail(store.CHECK) == 'Checking…'  # no list on the card yet
    w.ui.run(500)
    assert store.rows() == [('Foo', 'Install')] and w.net.asked == [LIST_URL, api('me/q2-foo')]
    assert not w.ui.toasts  # nothing installed to update, and the opening check isn't Check for Updates
    assert w.card.files['/mnt/mmc/.q2/sources.list'] == b'me/q2-foo Foo\n'  # the page's list offline
    store.press('Foo')
    shown.append(store.detail('Foo'))
    w.shell.watch = lambda cmd: (w.ui.run(500), shown.append(store.detail('Foo')))
    w.ui.run(500)
    stages = [x for i, x in enumerate(shown) if not i or shown[i - 1] != x]
    assert stages[0] == 'Checking…' and stages[-2:] == ['Unpacking…', 'Installing…']
    percents = [int(p) for x in stages[1:-2] for p in re.findall(r'^Downloading (\d+)%$', x)]
    assert len(percents) == len(stages[1:-2]) and percents == sorted(percents) and percents[-1] == 100
    assert w.ui.toasts == ['Foo is ready in Streaming'] and store.rows() == [('Foo', 'Installed')]
    card = w.card.tree()
    assert set(card) == FOO_FILES | {'.q2/apps/foo.files', '.q2/apps/foo.tag', '.q2/sources.list'}
    assert card['.foo/q2-foo'] == b'x' * 40000 and card['.q2/apps/foo.tag'] == b'1.0'
    assert set(card['.q2/apps/foo.files'].decode().split('\n')) == FOO_FILES | {''}
    assert STAGING not in w.card.dirs and not [p for p in w.card.files if p.startswith(STAGING)]
    moved = [e[2] for e in w.log if e[0] == 'rename' and e[2].startswith('/mnt/mmc/.foo/')]
    assert sorted(moved) == sorted('/mnt/mmc/' + f for f in FOO_FILES) and moved[-1] == '/mnt/mmc/.foo/q2-foo'  # the binary last
    assert w.card.room + sum(map(len, card.values())) == 4 << 30  # nothing else holds the card's room
    w.shell.watch = None
    assert streaming(w) == ['Foo']


def test_the_app_store_updates_an_app_stopping_it_first_and_keeping_what_it_saved(w):
    w.ui.wifi = 3
    installed(w, extra={'.foo/settings.cfg': b'volume=3'})
    w.card.put('/tmp/q2-foo', b'321')  # it runs
    hub = GitHub(w, 'me/q2-foo Foo\n')
    hub.release('me/q2-foo', '1.1', app_zip('binary 2', **{'.foo/lib.so': 'new'}))
    store = AppStore(w)
    w.ui.run(500)
    assert store.rows() == [('Foo', 'Update')] and w.ui.toasts == ['1 app can be updated']  # the opening check's count
    store.press('Foo')
    w.ui.run(500)
    assert w.ui.toasts[1:] == ['Foo is ready in Streaming'] and store.rows() == [('Foo', 'Installed')]
    card = w.card.tree()
    assert card['.foo/q2-foo'] == b'binary 2' and card['.foo/lib.so'] == b'new' and card['.foo/settings.cfg'] == b'volume=3'
    assert card['.q2/apps/foo.tag'] == b'1.1' and '.foo/lib.so' in card['.q2/apps/foo.files'].decode().split('\n')
    stop = w.log.index(('system', 'kill $(cat /tmp/q2-foo) 2>/dev/null || rm -f /tmp/q2-foo'))
    first = next(i for i, e in enumerate(w.log) if e[0] == 'rename' and e[2].startswith('/mnt/mmc/.foo/'))
    assert stop < first and '/tmp/q2-foo' not in w.card.files
    assert streaming(w) == ['Foo']
    w.fn('addon_open', C.c_int, C.c_void_p, C.c_void_p)(0, None)  # Streaming's row starts the new one
    assert w.shell.ran.count('/bin/sh /mnt/mmc/.foo/run') == 1


def test_the_app_store_removes_an_app_with_what_it_saved_and_without_wifi(w):
    installed(w, extra={'.foo/login.json': b'{"token":1}', '.bar/q2-bar': b'b', '.bar/q2app': b'name=Bar\n', '.bar/run': b'',
                        '.q2/apps/bar.files': b'.bar/q2-bar\n.bar/q2app\n.bar/run\n', '.q2/apps/bar.tag': b'2.0'},
              sources='me/q2-foo Foo\nme/q2-bar Bar\n')
    assert streaming(w) == ['Bar', 'Foo']
    store = AppStore(w)
    assert store.rows() == [('Foo', 'Installed'), ('Bar', 'Installed')]
    store.press('Foo')
    assert store.detail('Foo') == 'Press again to remove'
    store.press('Foo')
    assert store.detail('Foo') == 'Removing…'
    w.ui.run(500)
    assert w.ui.toasts == ['Foo removed'] and store.rows() == [('Foo', 'Install'), ('Bar', 'Installed')]
    assert not [p for p in w.card.tree() if 'foo' in p] and '/mnt/mmc/.foo' not in w.card.dirs
    assert set(w.card.tree()) == {'.bar/q2-bar', '.bar/q2app', '.bar/run', '.q2/apps/bar.files', '.q2/apps/bar.tag', '.q2/sources.list'}
    assert not w.net.asked and streaming(w) == ['Bar']


def test_wifi_gates_the_online_jobs_until_it_comes_up(w):
    store, page = AppStore(w), SystemUpdate(w)  # no list on the card yet; opened offline, no check
    before = w.ui.rows(store.page)
    assert store.rows() == [] and store.detail(store.CHECK) == 'Connect to Wi-Fi to load the list'
    store.press(store.CHECK)
    page.press()
    assert w.ui.toasts == ['Connect to Wi-Fi first'] * 2 and not w.ui.started
    w.ui.run(1000)
    assert not w.net.asked and page.text == 'tr:update_online' and w.ui.rows(store.page) == before
    w.ui.wifi = 2  # it comes up: the same presses go out
    GitHub(w, 'me/q2-foo Foo\n').release('me/q2-foo', '1.0', app_zip('x'))
    releases(w, TAG)
    store.press(store.CHECK)
    assert store.rows() == [] and store.detail(store.CHECK) == 'Checking…'  # the list is loading: no app rows yet
    w.ui.run(500)
    assert store.rows() == [('Foo', 'Install')] and w.ui.toasts[2:] == []  # System Update's page covers it: no count
    page.press()
    w.ui.run(500)
    assert w.ui.toasts[2:] == ['Latest firmware installed'] and w.ui.started == 2


def test_a_job_whose_thread_cant_start_says_so_and_the_next_press_starts_it(w):
    w.ui.wifi = 3
    GitHub(w, 'me/q2-foo Foo\n').release('me/q2-foo', '1.0', app_zip('x'))
    releases(w, TAG)
    installed(w, sources='me/q2-foo Foo\n')  # opened offline-checked: no job runs
    w.ui.wifi = -1
    store, page = AppStore(w), SystemUpdate(w)
    w.ui.wifi = 3
    w.mock('fw_pthread_create', lambda *_: 11)  # EAGAIN
    page.press()
    store.press('Foo')
    store.press('Foo')  # Installed's second press removes it
    assert len(w.ui.toasts) == 2 and page.text == 'tr:update_online'  # one each, and both rows as before
    assert store.rows() == [('Foo', 'Installed')] and FOO_FILES <= set(w.card.tree()) and not w.ui.timers
    w.mock('fw_pthread_create', w.ui.create)
    page.press()
    store.press('Foo')
    store.press('Foo')
    w.ui.run(500)
    assert w.ui.toasts[2:] == ['Latest firmware installed', 'Foo removed']


@pytest.mark.refusal('addon:oversize')
def test_an_install_without_room_says_how_much_more_and_downloads_nothing(w):
    w.ui.wifi = 3
    hub = GitHub(w, 'me/q2-foo Foo\n')
    url, data = hub.release('me/q2-foo', '1.0', app_zip('x' * 40000))
    w.card.room = 10 * MB
    store = AppStore(w)
    w.ui.run(500)
    have = w.card.room // 4096 * 4096  # as statfs counts it
    store.press('Foo')
    w.ui.run(500)
    short = -(-(len(data) + 64 * MB - have) // MB)  # its zip and 64 MB beside it, in whole MB
    assert w.ui.toasts == [f'Needs {short} MB more space'] and url not in w.net.asked
    assert set(w.card.tree()) == {'.q2/sources.list'} and store.rows() == [('Foo', 'Install')]


@pytest.mark.refusal('addon:truncated')
def test_an_install_cut_off_midway_leaves_the_card_as_it_was(w):
    w.ui.wifi = 3
    hub = GitHub(w, 'me/q2-foo Foo\n')
    _, data = hub.release('me/q2-foo', '1.0', app_zip('x' * 40000), cut=20000)
    store = AppStore(w)
    w.ui.run(500)
    room = w.card.room
    store.press('Foo')
    w.ui.run(500)
    assert w.ui.toasts == ["Can't reach GitHub"] and store.rows() == [('Foo', 'Install')]
    assert set(w.card.tree()) == {'.q2/sources.list'} and STAGING not in w.card.dirs and w.card.room == room


def test_a_second_press_cancels_an_install_and_another_app_waits(w):
    w.ui.wifi = 3
    hub = GitHub(w, 'me/q2-foo Foo\nme/q2-bar Bar\n')
    hub.release('me/q2-bar', '1.0', {'.bar/q2-bar': 'b'})
    store = AppStore(w)
    seen = []
    def during(sent):
        if not seen:
            store.press('Bar')
            store.press('Foo')
            seen.append(store.detail('Foo'))
    hub.release('me/q2-foo', '1.0', app_zip('x' * 40000), during=during)
    w.ui.run(500)
    store.press('Foo')
    w.ui.run(500)
    assert seen == ['Cancelling…'] and w.ui.toasts == ['Wait for the one in progress', 'Download cancelled']
    assert store.rows() == [('Foo', 'Install'), ('Bar', 'Install')]
    assert set(w.card.tree()) == {'.q2/sources.list'} and STAGING not in w.card.dirs
    assert not [u for u in w.net.asked if 'q2-bar.zip' in u]


# An installed card app from Streaming: its page over Q2SPOT/1, and the library update its sync asks for

LIB_DIR = '/mnt/mmc/_explaylist_data/'


def q2spot(request, status=b'ok', rows=(), msg=b''):
    """A Q2SPOT/1 reply to request (docs/librespot.md): the header's id, status, message and no cursor, then the rows."""
    id_ = request.split(b'\n')[1].split(b'\t')[0]
    return b'Q2SPOT/1\n%s\t%s\t%s\t\n' % (id_, status, msg) + b''.join(b'\t'.join(r) + b'\n' for r in rows)


class Foo:
    """Foo as it runs: a menu with Sync now, whose sync asks the Q2 for a library update (its playlist's
    import after a scan) through /tmp/q2-foo.library, whole, as an app writes it."""
    def __init__(self, w):
        self.w, self.ops = w, []
        w.apps.serve('foo', self.answer)

    def answer(self, request):
        op = request.split(b'\n')[1].split(b'\t')[1]
        self.ops.append(op)
        if op == b'sync':
            self.w.card.put('/tmp/q2-foo.library', f'7\nscan\nimport {LIB_DIR}Foo Mix.m3u\n'.encode())
            return q2spot(request, msg=b'Syncing 1 playlist')
        return q2spot(request, rows=[(b'action', b'sync', b'Sync now', b'From your server', b'', b''),
                                     (b'list', b'albums', b'Albums', b'', b'', b'')])


class Library:
    """Stock's library: Update Library's dialog (modal: its own loop polls, as stock's does, until its
    scan ends), Import Playlists (its folder's listing through M3U_DIR_CALL, a playlist made of each file
    left) and Playlists' list (p_deque_playlist), each record its name."""
    def __init__(self, w):
        self.w, self.open, self.keep = w, False, []
        self.deques = Deques(w.fw)
        self.playlists = self.deques.new()
        w.fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = self.playlists
        w.fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = self.deques.new()  # platform_init's
        w.ui.dialogs['dialog/updatemusic_dialog'] = self.scan
        w.mock('navigator_window_is_exist', lambda name, *_: self.open and s(name) == 'updatemusic_dialog')
        w.mock('toolsGetDirectoryM3u8', self.listing)
        w.mock('importPlayList', self.imports)

    def record(self, offset, text):
        r, chars = C.create_string_buffer(0x60), C.create_string_buffer(text.encode())
        self.keep += [r, chars]
        struct.pack_into('<Q', r, offset, C.addressof(chars))
        return C.addressof(r)

    def scan(self):
        self.open = True
        self.w.log.append(('scan',))
        self.w.fn('library_poll', None)()  # the dialog's loop: a poll there runs nothing
        self.open = False

    def listing(self, folder, dq, *_):
        for p in sorted(p for p in self.w.card.files if p.startswith(s(folder)) and p.endswith('.m3u')):
            self.deques[dq].append(self.record(O['REC_PATH'], p))
        return len(self.deques[dq])

    def imports(self, *_):
        dq = self.deques.new()
        self.w.fn('addon_m3u_dir', C.c_int, C.c_char_p, C.c_void_p)(LIB_DIR.encode(), dq)
        names = [s(C.c_void_p.from_address(r + O['REC_PATH']).value)[len(LIB_DIR):-4] for r in self.deques[dq]]
        self.deques[self.playlists].extend(self.record(O['REC_NAME'], n) for n in names)
        self.w.log.append(('import', names))
        return 1


def test_an_installed_app_draws_its_page_and_its_sync_updates_the_library_once_the_app_store_closes(w):
    installed(w)
    w.card.put('/tmp/mmc_add', b'')  # the card is in
    w.card.put(LIB_DIR + 'Foo Mix.m3u', b'/mnt/mmc/Music/B/01 Song 2.flac\n', count=False)
    w.card.put(LIB_DIR + 'My Own.m3u', b'/mnt/mmc/Music/A/01 Song 1.flac\n', count=False)  # the user's export
    app, library = Foo(w), Library(w)
    w.ui.loop.append(w.fn('addon_poll', None))
    assert streaming(w) == ['Foo']
    w.fn('addon_open', C.c_int, C.c_void_p, C.c_void_p)(0, None)  # Foo's Streaming row
    page = w.ui.top()
    assert w.shell.ran.count('/bin/sh /mnt/mmc/.foo/run') == 1
    w.ui.run(250)
    assert w.ui.texts(page) == ['Starting Foo…']  # no socket yet
    w.ui.run(1000)
    assert w.ui.rows(page) == [('Sync now', 'From your server'), ('Albums', '')]
    w.ui.press(page, 'Sync now')
    w.ui.run(1500)  # its page shows no library records: the request runs under it
    assert w.ui.toasts == ['Syncing 1 playlist', 'Playlists updated'] and app.ops == [b'menu', b'sync', b'menu']
    assert w.card.files['/tmp/q2-foo.library.done'] == b'7 ok\n'
    steps = [e for e in w.log if e[0] in ('scan', 'import')]
    assert steps == [('scan',), ('import', ['Foo Mix'])]  # the app's playlist alone, after the scan
    w.ui.run(3000)
    assert [e for e in w.log if e[0] in ('scan', 'import')] == steps  # a seq is done once
    w.ui.back()
    store = AppStore(w)  # Q2 Pod's own page, not one the import may run under
    w.card.put('/tmp/q2-foo.library', f'8\nscan\nimport {LIB_DIR}Foo Mix.m3u\n'.encode())  # a sync from the phone
    w.ui.run(3000)
    assert [e for e in w.log if e[0] in ('scan', 'import')] == steps and w.card.files['/tmp/q2-foo.library.done'] == b'7 ok\n'
    w.ui.destroy(store.page)
    w.ui.run(1500)
    assert [e for e in w.log if e[0] in ('scan', 'import')] == steps * 2 and w.card.files['/tmp/q2-foo.library.done'] == b'8 ok\n'


def test_a_card_app_reply_that_lands_before_pthread_create_returns_is_still_taken(w):
    installed(w)
    Foo(w)
    w.card.put('/tmp/q2-foo', b'321')  # it runs and listens
    assert streaming(w) == ['Foo']
    w.ui.eager = True  # each worker runs to its end before pthread_create returns
    w.fn('addon_open', C.c_int, C.c_void_p, C.c_void_p)(0, None)
    page = w.ui.top()
    w.ui.run(3000)
    assert w.ui.rows(page) == [('Sync now', 'From your server'), ('Albums', '')]


# Spotify: Streaming's row, librespot on the card, the hand-over both ways and the idle stop

SPOT_STATE = '/tmp/q2-librespot.state'
BROWSE = '/tmp/q2-librespot.browse.sock'
CONTROL = '/tmp/q2-librespot.sock'
RUN = '/bin/sh /mnt/mmc/.spotify/run'
KEY_PLAY = O['KEY_PLAY']
DAC = ('config_outputchannel', 'mclSetDacPwr', 'mclSetPcmMode', 'mclSetMute', 'device_set_volume')  # books.c's dac_take
TRACKS = [('1', 'Song 1', 'Artist A', 'Album A', 200000), ('2', 'Song 2', 'Artist B', 'Album B', 180000)]


class Librespot:
    """librespot as the card's run starts it (docs/internals.md#spotify, docs/librespot.md): its sockets
    answer a second later; every request is a login until the phone picks the Q2; a play is ok'd and plays
    when the test says; s pauses it once its sink has let the DAC go, 60 ms on; killall ends it."""
    def __init__(self, w, on_card=True):
        self.w, self.runs, self.kills, self.up_at = w, 0, 0, None
        self.paired, self.commands, self.plays, self.pause_at, self.track = False, [], [], None, None
        if on_card:
            w.card.put('/mnt/mmc/.spotify/librespot', b'ELF', count=False)
            w.card.put('/mnt/mmc/.spotify/run', b'#!/bin/sh\n', count=False)
        w.shell.hook(re.escape(RUN), self.run)
        w.shell.hook(r'killall -9 librespot aplay', self.kill)
        w.sockets.servers[BROWSE] = Server(self.listening, answer=self.answer)
        w.sockets.servers[CONTROL] = Server(self.listening, datagram=self.command)
        w.clock.hooks.append(self.tick)

    def run(self, m):
        if self.up_at is None:  # its launcher exits when one runs
            self.runs += 1
            self.up_at = self.w.clock.ms + 1000
            self.w.card.put('/tmp/q2-librespot', b'99')
            self.write('none')

    def kill(self, m):
        self.up_at = None
        self.kills += 1

    def listening(self):
        return self.up_at is not None and self.w.clock.ms >= self.up_at

    def write(self, state, track=None):
        text = f'state={state}\n'
        if track:
            text += 'track={}\ntitle={}\nartist={}\nalbum={}\nduration={}\nposition=0\nat={}\ncover=0\n'.format(*track, self.w.clock.ms)
        self.w.card.put(SPOT_STATE, text.encode())
        self.track = track

    def answer(self, request):
        id_, op, *args = request.split(b'\n')[1].split(b'\t')
        def reply(status, rows=()):
            return b'Q2SPOT/1\n%s\t%s\t\t\n' % (id_, status) + b''.join(b'\t'.join(r) + b'\n' for r in rows)
        if not self.paired:
            return reply(b'login')
        if op == b'liked':
            return reply(b'ok', [(b'track', b'spotify:track:' + t[0].encode(), t[1].encode(), t[2].encode(), b'spotify:collection', b'%d' % i)
                                 for i, t in enumerate(TRACKS)])
        if op == b'play':
            self.plays.append(tuple(args))
        return reply(b'ok')

    def command(self, data):
        self.commands.append(data)
        if data == b's':
            self.pause_at = self.w.clock.ms + 60

    def tick(self):
        if self.pause_at is not None and self.w.clock.ms >= self.pause_at:
            self.pause_at = None
            self.write('paused', self.track)


class Spotify:
    def __init__(self, w, on_card=True):
        self.w, self.lib = w, Librespot(w, on_card)
        w.byte('g_backlight_status', 1)
        w.ui.loop.append(w.fn('spot_poll', None))  # ringnav_sleep's, every UI loop pass
        w.mock('toolsTimeItoa', lambda out, sec, *_: C.memmove(out, f'{sec // 60}:{sec % 60:02}'.encode() + b'\0', 8) and 0)

    def open(self):
        """Streaming's Spotify row."""
        assert self.w.fn('spot_open', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
        return self.w.ui.top()

    def key(self, key):
        return self.w.fn('spot_media', C.c_int, C.c_uint)(key)


def picked_spotify(w):
    """The phone paired and Liked Songs' second song picked on the Q2, its play ok'd; local music was playing."""
    sp = Spotify(w)
    w.playing = 2
    browser = sp.open()
    assert w.shell.ran.count(RUN) == 1 and w.ui.texts(browser) == ['Loading…']
    w.ui.run(500)  # no socket yet: librespot starting
    assert w.ui.texts(browser) == ['Loading…'] and not sp.lib.listening()
    w.ui.run(1500)
    assert w.ui.rows(browser) == [('Open Spotify on your phone and choose Q2', 'Both on the same Wi-Fi')]
    sp.lib.paired = True
    w.ui.run(2500)  # the prompt asks again every 2 s
    menu = w.ui.rows(browser)
    assert 'Liked Songs' in menu and 'Now Playing' not in menu  # no track yet
    w.ui.press(browser, 'Liked Songs')
    w.ui.run(500)
    assert w.ui.rows(browser) == [('Song 1', 'Artist A'), ('Song 2', 'Artist B')]
    w.ui.press(browser, 'Song 2')
    w.ui.run(500)
    assert sp.lib.plays == [(b'spotify:collection', b'1', b'spotify:track:2')]
    np = w.ui.top()
    assert np != browser and w.ui.texts(np) == ['Loading…', 'Starting playback']
    return sp, browser, np


def playing_spotify(w):
    """picked_spotify, and librespot playing it."""
    sp, browser, np = picked_spotify(w)
    calls = len(w.fw.calls())
    sp.lib.write('playing', TRACKS[1])
    w.ui.run(1000)
    return sp, browser, np, calls


def test_spotify_launches_pairs_and_plays_taking_the_dac_from_local_music(w):
    sp, browser, np, before = playing_spotify(w)
    shown = w.ui.texts(np)
    assert {'Song 2', 'Artist B', 'Album B'} <= set(shown) and 'Starting playback' not in shown
    names = [c[0] for c in w.fw.calls()[before:]]
    readied = [i for i, name in enumerate(names) if name in DAC]
    assert readied and 'player_stop' in names[:readied[0]]  # local music lets go before the DAC is readied
    assert w.calls('mclSetMute')[-1][0] == 0  # and left unmuted
    held = len(w.calls('reset_poweroptions_timer'))
    w.ui.run(2000)
    assert len(w.calls('reset_poweroptions_timer')) > held and all(c[:3] == (1, 1, 0) for c in w.calls('reset_poweroptions_timer'))
    assert sp.key(KEY_PLAY) == 1 and sp.lib.commands == [b'p']  # Play/Pause is Spotify's now
    assert sp.lib.runs == 1


def test_local_music_takes_playback_back_from_spotify(w):
    sp, browser, np, _ = playing_spotify(w)
    w.clock.slept.clear()
    w.fn('spot_yield', None)()  # a local track starts (mclStartPlayer's hook)
    assert sp.lib.commands == [b's'] and 60 <= sum(w.clock.slept) < 1500  # until librespot says paused
    assert sp.key(KEY_PLAY) == 0 and sp.lib.commands == [b's']  # the keys are local music's again
    w.playing = 2
    w.ui.run(1000)
    assert sp.key(KEY_PLAY) == 0 and sp.lib.kills == 0


def test_spotify_idle_with_its_pages_shut_is_stopped_and_the_row_starts_it_again(w):
    sp, browser, np, _ = playing_spotify(w)
    w.fn('spot_yield', None)()
    w.playing = 2
    w.ui.run(500)
    w.ui.back()  # Now Playing
    w.ui.back()  # the browser
    assert not w.ui.windows()
    w.ui.run(29000)
    assert sp.lib.kills == 0
    w.ui.run(2000)
    assert sp.lib.kills == 1 and SPOT_STATE not in w.card.files and '/tmp/q2-librespot' not in w.card.files
    reads = w.card.reads[SPOT_STATE]
    w.ui.run(5000)
    assert w.card.reads[SPOT_STATE] == reads  # nothing reads it until the row starts it again
    sp.open()
    assert sp.lib.runs == 2


def test_a_track_key_spotify_refuses_ends_the_wait_with_one_kill_and_says_why(w):
    w.card.put('/mnt/mmc/.spotify/spot.log', b'[old] error audio key\n', count=False)  # a line from before the play
    sp, browser, np = picked_spotify(w)
    w.ui.run(2000)
    assert sp.lib.kills == 0 and w.ui.texts(np) == ['Loading…', 'Starting playback']
    w.card.files['/mnt/mmc/.spotify/spot.log'] += b'[new] librespot_playback: error audio key 403\n'
    w.ui.run(1000)
    assert sp.lib.kills == 1 and w.ui.texts(np) == ['Spotify refused this account', "Its tracks can't play on the Q2"]
    w.ui.run(5000)
    sp.lib.run(None)  # its launcher's loop starts it again, with no track
    w.ui.run(15000)
    assert sp.lib.kills == 1 and len(sp.lib.plays) == 1 and w.ui.texts(np)[0] == 'Spotify refused this account'  # no play sent again
    sp.lib.write('playing', TRACKS[0])  # any track that plays later clears it
    w.ui.run(1000)
    assert 'Song 1' in w.ui.texts(np)


def test_spotify_not_on_the_card_says_so_and_runs_nothing(w):
    sp = Spotify(w, on_card=False)
    page = sp.open()
    assert w.ui.texts(page) == ["Spotify isn't on the card", 'See the Q2 Pod guide to add it']
    w.ui.run(2000)
    assert RUN not in w.shell.ran and not w.card.reads.get(SPOT_STATE)


SPOTIFY_SOURCES = 'DiamondBond/q2-librespot Spotify\n'


def test_updating_spotify_in_the_app_store_stops_librespot_keeps_its_login_and_streaming_starts_the_new_one(w):
    w.ui.wifi = 3
    sp = Spotify(w)
    for name, data in {'.spotify/cache/credentials.json': b'{"login":"me"}', '.spotify/.cache-size': b'1G',
                       '.q2/apps/spotify.files': b'.spotify/librespot\n.spotify/run\n', '.q2/apps/spotify.tag': b'1.0',
                       '.q2/sources.list': SPOTIFY_SOURCES.encode()}.items():
        w.card.put('/mnt/mmc/' + name, data, count=False)
    sp.open()
    w.ui.run(1500)
    assert sp.lib.runs == 1 and sp.lib.listening()
    w.ui.back()  # the browser
    def meanwhile(sent):  # the user leaves the App Store mid-download and starts Spotify from Streaming
        if sp.lib.runs == 1:
            w.ui.back()
            sp.open()
    GitHub(w, SPOTIFY_SOURCES).release('DiamondBond/q2-librespot', '1.1', {
        '.spotify/librespot': 'ELF 1.1', '.spotify/run': '#!/bin/sh\n# 1.1\n', '.spotify/cache/credentials.json': '{}',
        '.spotify/.cache-size': '2G'}, during=meanwhile)
    store = AppStore(w)
    w.ui.run(500)
    assert store.rows() == [('Spotify', 'Update')] and w.ui.toasts == ['1 app can be updated']  # the opening check's count
    store.press('Spotify')
    assert sp.lib.kills == 1 and not sp.lib.listening()  # stopped on the UI thread as the job starts
    w.ui.run(500)
    assert sp.lib.runs == 2 and w.ui.toasts[1:] == ['Spotify is ready in Streaming']  # said away from the page
    assert AppStore(w).rows() == [('Spotify', 'Installed')]
    card = w.card.tree()
    assert card['.spotify/librespot'] == b'ELF 1.1' and card['.spotify/run'] == b'#!/bin/sh\n# 1.1\n'
    assert card['.spotify/cache/credentials.json'] == b'{"login":"me"}' and card['.spotify/.cache-size'] == b'1G'  # never the zip's
    assert card['.q2/apps/spotify.tag'] == b'1.1'
    spot = [(i, e[1] == RUN) for i, e in enumerate(w.log) if e[0] == 'system' and (e[1] == RUN or 'killall -9 librespot aplay' in e[1])]
    starts, stops = [i for i, run in spot if run], [i for i, run in spot if not run]
    moves = [i for i, e in enumerate(w.log) if e[0] == 'rename' and e[2].startswith('/mnt/mmc/.spotify/')]
    # the one started during the download is stopped before its files move, and again as the job ends
    assert starts[-1] < moves[0] and any(starts[-1] < i < moves[0] for i in stops) and moves[-1] < stops[-1]
    sp.open()  # Streaming's row starts the new one
    assert sp.lib.runs == 3


def test_a_spotify_reply_that_lands_before_pthread_create_returns_is_still_taken(w):
    sp = Spotify(w)
    sp.lib.paired = True
    sp.lib.run(None)
    w.clock.ms = sp.lib.up_at
    w.ui.eager = True  # each worker runs to its end before pthread_create returns
    browser = sp.open()
    w.ui.run(3000)
    assert 'Liked Songs' in w.ui.rows(browser)


# Tidal: a track list paged whole, and the card's copy of each stream

class TidalList:
    """Stock's side of a Tidal list (docs/internals.md#tidal-track-lists) over n tracks: its thread asks one
    page, the struct's offset (or its argument, for favourites) and limit, and writes the reply to
    /tmp/tidal_<thread>; its parser appends the reply's records, writes back the total and the reply's
    limit, and fails on no reply. The page's open (init_infodata) sets limit 50, offset 0."""
    def __init__(self, w, n, thread, parser, by_offset=False):
        self.w, self.n, self.by_offset, self.asked, self.fail, self.during = w, n, by_offset, [], set(), None
        self.deques = Deques(w.fw)
        self.t = w.fw.var('fw_ctx_stTidalTracks', C.c_int * 4)  # total, limit, offset, the deque (its low half)
        self.handle = self.deques.new()
        self.t[0], self.t[1], self.t[2], self.t[3] = 0, 50, 0, self.handle
        self.reply = '/tmp/tidal_7'  # pthread_self's
        w.mock(thread, self.page)
        w.mock(parser, self.parse)

    def page(self, arg, *_):
        offset, limit = arg if self.by_offset else self.t[2], self.t[1]
        self.asked.append(offset)
        if self.during:
            self.during(offset)
        if offset not in self.fail:
            items = list(range(1000 + offset, 1000 + min(self.n, offset + limit)))
            self.w.card.put(self.reply, json.dumps({'totalNumberOfItems': self.n, 'limit': limit, 'offset': offset, 'items': items}).encode())
        return 0

    def parse(self, path, *_):
        data = self.w.card.files.get(s(path))
        if data is None:
            return -1
        reply = json.loads(data)
        self.deques[self.handle].extend(reply['items'])
        if 'totalNumberOfItems' in reply:
            self.t[0], self.t[1] = reply['totalNumberOfItems'], reply['limit']
        return 1

    def shown(self):
        """What the page lists once its done flag is 2: its own parse of the reply, then the deque."""
        self.parse(C.c_char_p(self.reply.encode()))
        return [x - 1000 for x in self.deques[self.handle]]


def test_a_tidal_playlist_is_paged_whole_and_its_page_lists_each_track_once(w):
    tracks = TidalList(w, 120, 'stock_tidalplaylist_trampoline', 'parseTidalPlaylistDetailJson')
    assert w.fn('ringnav_tidal_playlist', C.c_int, C.c_void_p)(None) == 0
    assert tracks.asked == [0, 50, 100] and w.fw.var('tidal_albuminfo_thread_run', C.c_ubyte).value == 2
    assert tracks.shown() == list(range(120))


def test_a_tidal_album_whose_second_page_fails_lists_the_first(w):
    tracks = TidalList(w, 120, 'stock_tidalalbum_trampoline', 'parseTidalAlbumDetailJson')
    tracks.fail = {50}
    w.fn('ringnav_tidal_album', C.c_int, C.c_void_p)(None)
    assert tracks.asked == [0, 50] and tracks.shown() == list(range(50))
    assert w.fw.var('tidal_albuminfo_thread_run', C.c_ubyte).value == 2


def test_a_failed_first_page_is_left_to_the_pages_own_error(w):
    tracks = TidalList(w, 120, 'stock_tidalplaylist_trampoline', 'parseTidalPlaylistDetailJson')
    tracks.fail = {0}
    w.fn('ringnav_tidal_playlist', C.c_int, C.c_void_p)(None)
    assert tracks.asked == [0] and tracks.reply not in w.card.files  # stock's parse meets no reply, as stock's would
    assert w.fw.var('tidal_albuminfo_thread_run', C.c_ubyte).value == 2


def test_tidal_favourites_page_by_their_offset_and_leave_it_at_the_last_page(w):
    tracks = TidalList(w, 120, 'stock_tidalfavs_trampoline', 'parseTidalColTracksJson', by_offset=True)
    w.fn('ringnav_tidal_favs', C.c_int, C.c_void_p)(0)
    assert tracks.asked == [0, 50, 100] and tracks.shown() == list(range(120))
    assert tracks.t[2] == 100 and w.fw.var('tidal_collection_thread_run', C.c_ubyte).value == 2  # no page left to scroll to


def test_a_tidal_list_opened_again_midway_stops_the_old_run(w):
    tracks = TidalList(w, 300, 'stock_tidalplaylist_trampoline', 'parseTidalPlaylistDetailJson')
    def reopened(offset):
        if offset == 50:  # while this page loads, a list opens (init_infodata: the deque cleared, offset 0)
            tracks.deques[tracks.handle][:] = range(5000, 5100)  # and its own run reads two pages
            tracks.t[2] = 100
    tracks.during = reopened
    w.fn('ringnav_tidal_playlist', C.c_int, C.c_void_p)(None)
    # the old page lands in a list that grew meanwhile, so only the moved offset says to stop
    assert tracks.asked == [0, 50] and tracks.t[2] == 100  # and the new run's place is left as it was


class Low:
    """Strings where stock's 32-bit record layout holds them on this 64-bit host: below 4 GB (a field whose
    high half is the next field, left 0), or at a low half of 0 (a field whose low half is the one
    before's high half), its high half one of this file's (conftest.high_halves). Unmapped after the test."""
    HIGHS = high_halves(0x40)

    def __init__(self):
        self.maps = []
        libc.munmap.argtypes = [C.c_void_p, C.c_size_t]

    def map(self, at, data):
        got = libc.mmap(at, 0x1000, 3, 0x100022, -1, 0)  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
        assert got == at, f'{at:#x} is taken'
        self.maps.append(at)
        C.memmove(at, data + b'\0', len(data) + 1)
        return at

    def below_4g(self, data):
        return self.map(0x5e000000 + len(self.maps) * 0x1000, data)

    def zero_low_half(self, data):
        return self.map(self.HIGHS[len(self.maps)] << 32, data)

    def close(self):
        for at in self.maps:
            libc.munmap(at, 0x1000)


@pytest.fixture
def low():
    strings = Low()
    yield strings
    strings.close()


TIDAL = '/mnt/mmc/.tidal'


class TidalQueue:
    """Tidal's queue as stock's URL thread finds a track's record: its song id, and the title and artist
    the copy is named and tagged with."""
    def __init__(self, w, low, level=2):
        self.w, self.low, self.keep, self.played = w, low, [], []
        self.deques = Deques(w.fw)
        self.handle = self.deques.new()
        w.fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.handle
        w.fw.var('fw_ctx_tidalStreamingLevelNow', C.c_int).value = level
        w.mock('stock_tidal_trampoline', lambda url, *_: self.played.append(s(url)) and 0)
        w.playing = 2

    def add(self, song_id, title=None, artist=None):
        r = C.create_string_buffer(0x60)
        song = C.create_string_buffer(song_id.encode())
        self.keep += [r, song]
        struct.pack_into('<Q', r, O['REC_TIDAL_ID'], C.addressof(song))
        if title:
            struct.pack_into('<I', r, O['REC_TITLE'], self.low.below_4g(title.encode()))
        if artist:
            struct.pack_into('<Q', r, O['REC_ARTIST'], self.low.zero_low_half(artist.encode()))
        self.deques[self.handle].append(C.addressof(r))

    def play(self, url, song_id):
        """mcl_tidalStartPlayer, from stock's stream thread: the URL it fetched for the song."""
        return self.w.fn('tidal_play', C.c_int, C.c_char_p, C.c_char_p)(url.encode(), song_id.encode())


def cdn(n, token=1):
    return f'https://sp-ad-cf.audio.tidal.com/mediatracks/{n}/0.flac?token={token}'


def test_a_tidal_stream_is_kept_on_the_card_and_plays_from_it_after(w, low):
    q = TidalQueue(w, low)
    q.add('123')
    flac = b'fLaC' + bytes(300000)
    w.net.route(cdn(1).split('?')[0], Reply(200, flac))
    q.play(cdn(1), '123')
    assert q.played == [cdn(1)]  # the stream plays while it downloads
    w.ui.run(0)
    assert w.card.tree(TIDAL) == {'123-2.flac': flac}
    q.play(cdn(1, token=2), '123')
    assert q.played[-1] == TIDAL + '/123-2.flac' and w.net.asked == [cdn(1)] and w.ui.started == 1


def test_a_tidal_copy_is_named_and_tagged_from_its_queue_row(w, low):
    q = TidalQueue(w, low)
    q.add('123', 'Song', 'Artist')
    flac = b'fLaC' + bytes(1000)
    w.net.route(cdn(1).split('?')[0], Reply(200, flac))
    def ffmpeg(pid, status, options, *_):  # stock ffmpeg's -c copy remux with the row's tags, exit 0
        tmp = next(p for p in w.card.files if p.endswith('.flac.tmp'))
        w.card.put(tmp + '.tag.tmp', w.card.files[tmp] + b'+tags')
        C.c_int.from_address(status).value = 0
        return pid
    w.mock('fw_fork', lambda *_: 4242)
    w.mock('fw_waitpid', ffmpeg)
    q.play(cdn(1), '123')
    w.ui.run(0)
    assert w.card.tree(TIDAL) == {'123-2 Artist - Song.flac': flac + b'+tags'}


def test_a_skipped_tidal_track_drops_its_copy_and_the_next_one_is_kept(w, low):
    q = TidalQueue(w, low)
    q.add('123')
    q.add('456')
    first, second = b'fLaC' + bytes(300000), b'fLaC' + b'\1' * 200000
    skipped = []
    def skip(sent):
        if not skipped:
            skipped.append(q.play(cdn(2), '456'))  # the next track starts, from stock's thread
    w.net.route(cdn(1).split('?')[0], Reply(200, first, chunk=50000, during=skip))
    w.net.route(cdn(2).split('?')[0], Reply(200, second))
    q.play(cdn(1), '123')
    w.ui.run(0)
    assert q.played == [cdn(1), cdn(2)] and w.net.asked == [cdn(1), cdn(2)]
    assert w.card.tree(TIDAL) == {'456-2.flac': second} and w.ui.started == 1  # one thread, one transfer at a time


# Online match: lyrics for the playing song, found, saved beside it and loaded; the cover search

class CJson:
    """demo's cJSON over json.loads: items laid out as the Q2's (valuestring at +16)."""
    def __init__(self, w):
        self.nodes, self.keep = {}, []
        w.mock('cJSON_Parse', self.parse)
        w.mock('cJSON_GetArraySize', lambda n, *_: len(self.nodes[n]))
        w.mock('cJSON_GetArrayItem', lambda n, i, *_: self.node(self.nodes[n][i]))
        w.mock('cJSON_GetObjectItem', self.member)

    def node(self, value):
        raw = C.create_string_buffer(32)
        if isinstance(value, str):
            text = C.create_string_buffer(value.encode())
            self.keep.append(text)
            C.c_void_p.from_buffer(raw, 16).value = C.addressof(text)
        self.keep.append(raw)
        self.nodes[C.addressof(raw)] = value
        return C.addressof(raw)

    def parse(self, text, *_):
        try:
            return self.node(json.loads(C.string_at(text)))
        except ValueError:
            return 0

    def member(self, n, key, *_):
        value, name = self.nodes.get(n), s(key)
        return self.node(value[name]) if isinstance(value, dict) and name in value else 0


def keywords(w, title, album, artist):
    """Stock's lyric and cover keywords (online_getkeyword_info): title at 0x200, album 0x400, artist 0x500."""
    def fill(key, *_):
        for at, value in ((0x200, title), (0x400, album), (0x500, artist)):
            C.memmove(key + at, value.encode() + b'\0', len(value.encode()) + 1)
    w.mock('online_getkeyword_info', fill)


SONG = '/mnt/mmc/Music/B/01 Song 2.flac'
LRC = '/mnt/mmc/Music/B/01 Song 2.lrc'
SHADOW = '/mnt/mmc/.downpic/01 Song 2.lrc'  # stock's Download Cover & Lyrics copy, which its loader tries first
LRCLIB = 'https://lrclib.net/api/search?track_name='
SYNCED = '[00:00.50] Mix Engineer : Someone\n[00:01.50] First line\n[00:02.00]\n[00:03.25] Second line\n'
STOCK_CP = 'cp -rf %s "/mnt/mmc/.downpic/%s"'


class Lyrics:
    """The playing song, on the card, and stock's lyric list (MCL_LYRICS: whole seconds and text) and
    loader, which opens the song's .lrc through LYRIC_OPEN, lists it as stock does, blank lines and all,
    and leaves ringnav_lyric the rest.

    The song's queue record holds REC_NAME, REC_PATH and REC_TYPE alone. Read 8 bytes wide on this host,
    REC_PATH runs into REC_TITLE, so REC_TITLE reads the path's high half as an address nothing maps.
    The paths here (lrc_song, ringnav_lyric_save, ringnav_lyric) read no other pointer in it. One that came
    to read REC_TITLE would crash the whole test/fw run rather than fail its test: it belongs with
    test/patch.py, as Download Album Lyrics does (docs/building.md, Limits)."""
    def __init__(self, w, mcl, low):
        self.w, self.lines, self.keep, self.stock = w, [], [], []
        w.card.put(SONG, b'fLaC', count=False)
        w.card.put(SHADOW, b'stale', count=False)
        record = C.create_string_buffer(0x60)
        self.keep.append(record)
        struct.pack_into('<I', record, O['REC_NAME'], low.below_4g(SONG.rsplit('/', 1)[1].encode()))
        struct.pack_into('<Q', record, O['REC_PATH'], low.zero_low_half(SONG.encode()))  # its high half in REC_TITLE
        struct.pack_into('<i', record, O['REC_TYPE'], 8)
        self.deques = Deques(w.fw)
        self.queue = self.deques.new([C.addressof(record)])
        w.fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.queue
        mcl.type, mcl.pos = 1, 0  # a folder play, its first song
        self.cell = C.c_uint.from_address(O['MCL_LYRICS'])
        self.saved, self.cell.value = self.cell.value, 0x4ee0
        w.mock('fw_deque_clear', lambda q, *_: self.lines.clear() if q & 0xffffffff == 0x4ee0 else self.deques[q].clear())
        w.mock('fw__deque_push_back', self.push)
        w.mock('mclGetLyricSize', lambda *_: len(self.lines))
        w.mock('stock_lyric_trampoline', self.stock_load)
        CJson(w)
        keywords(w, 'Song 2', 'Album B', 'Artist B')

    def push(self, q, v, *_):
        if q & 0xffffffff == 0x4ee0:  # struct { int time; const char *text; }
            self.lines.append((C.c_int.from_address(v).value, s(C.c_void_p.from_address(v + 8).value)))
        else:
            self.deques[q].append(v)

    def stock_load(self, *_):
        f = self.w.fn('ringnav_lrc_open', C.c_void_p, C.c_char_p, C.c_char_p)(LRC.encode(), b'r')
        if not f:
            return 0
        self.lines[:] = [(int(m[1]) * 60 + int(m[2]), m[4]) for m in re.finditer(r'\[(\d+):(\d+)\.(\d+)\] ?(.*)', self.w.card.files[LRC].decode())]
        self.stock = list(self.lines)
        return 1

    def close(self):
        self.cell.value = self.saved


@pytest.fixture
def lyrics(w, mcl, low):
    playing = Lyrics(w, mcl, low)
    yield playing
    playing.close()


def published(w):
    """The lyrics page's next table: its lines' start, in ms."""
    t = w.fw.var('lyrics__ly', Ly).next.contents
    return [t.line[i].ms for i in range(t.n)]


def test_lyrics_for_the_playing_song_go_beside_it_and_load_to_the_millisecond(w, lyrics):
    w.net.route(LRCLIB, Reply(200, json.dumps([{'id': 1, 'syncedLyrics': None}, {'id': 2, 'syncedLyrics': SYNCED}]).encode()))
    # stock's lyric thread, Download Cover & Lyrics' search
    assert w.fn('ringnav_lyric_search', C.c_int, C.c_char_p, C.c_char_p)(b'Song 2 Artist B', b'/tmp/oslyric_0') == 1
    assert w.net.asked == [LRCLIB + 'Song%202&artist_name=Artist%20B']
    found = w.card.files['/tmp/oslyric_0']
    assert found == '﻿'.encode() + SYNCED.split('\n', 1)[1].encode()  # a BOM as Kugou's, its credits gone
    # the user takes them: stock's cp to .downpic becomes nothing, the lyrics go beside the song
    cmd = C.create_string_buffer(512)
    w.fn('ringnav_lyric_save', C.c_int, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p, C.c_char_p)(
        cmd, 512, STOCK_CP.encode(), b'/tmp/oslyric_0', b'01 Song 2.lrc')
    assert cmd.value == b':' and w.card.files[LRC] == found and SHADOW not in w.card.files
    assert LRC + '.part' not in w.card.files
    # stock reloads them: its list in whole seconds, blank line and all, until ringnav_lyric's replace it
    assert w.fn('ringnav_lyric', C.c_int)() == 1
    assert lyrics.stock == [(1, 'First line'), (2, ''), (3, 'Second line')]
    assert lyrics.lines == [(1, 'First line'), (3, 'Second line')] and published(w) == [1500, 3250]


def test_lyrics_on_a_full_card_leave_it_as_it_was_and_run_nothing(w, lyrics):
    w.net.route(LRCLIB, Reply(200, json.dumps([{'id': 2, 'syncedLyrics': SYNCED}]).encode()))
    w.fn('ringnav_lyric_search', C.c_int, C.c_char_p, C.c_char_p)(b'Song 2', b'/tmp/oslyric_0')
    w.card.room = 10  # full
    cmd = C.create_string_buffer(512)
    w.fn('ringnav_lyric_save', C.c_int, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p, C.c_char_p)(
        cmd, 512, STOCK_CP.encode(), b'/tmp/oslyric_0', b'01 Song 2.lrc')
    assert cmd.value == b':'  # never stock's command, which runs the name through the shell (GHSA-qp92-mq7c-6r64)
    assert LRC not in w.card.files and LRC + '.part' not in w.card.files and w.card.files[SHADOW] == b'stale'
    assert SHADOW + '.part' not in w.card.files


def test_lyrics_with_lrclib_unreachable_leave_nothing_to_take(w, lyrics):
    w.net.up = False
    assert w.fn('ringnav_lyric_search', C.c_int, C.c_char_p, C.c_char_p)(b'Song 2', b'/tmp/oslyric_0') == 1
    assert len(w.net.asked) == 1 and '/tmp/oslyric_0' not in w.card.files  # once: offline is not busy


MB_SEARCH = 'https://musicbrainz.org/ws/2/release/?fmt=json&limit=10&query=release%3A%22Album%20B%22%20AND%20artist%3A%22Artist%20B%22'
GROUP = 'abcdefgh-0000-0000-0000-00000000000%d'


def test_the_cover_search_offers_musicbrainz_and_itunes_first_then_stocks(w):
    CJson(w)
    keywords(w, 'Song 2', 'Album B', 'Artist B')
    covers = []
    w.mock('online_addcoverlist', lambda url, *_: covers.append(s(url)))
    w.mock('online_getsize_coverlist', lambda *_: len(covers))
    w.mock('stock_kugoucover_trampoline', lambda *_: w.log.append(('kugou', list(covers))))
    groups = [GROUP % 1, GROUP % 1, GROUP % 2]  # two releases of one group
    w.net.route(MB_SEARCH, Reply(200, json.dumps({'releases': [{'release-group': {'id': g}} for g in groups]}).encode()))
    w.net.route('https://coverartarchive.org/release-group/' + GROUP % 1, Reply(307, location='https://archive.org/download/mbid-1/front-500.jpg'))
    w.net.route('https://coverartarchive.org/release-group/' + GROUP % 2, Reply(404))
    w.net.route('https://itunes.apple.com/search', Reply(200, json.dumps({'results': [{'artworkUrl100': 'https://is1.mzstatic.com/a/100x100bb.jpg'}]}).encode()))
    w.fn('ringnav_cover_search', None, C.c_char_p)(b'Album B Artist B')
    ours = ['https://archive.org/download/mbid-1/front-500.jpg', 'https://is1.mzstatic.com/a/600x600bb.jpg']
    assert covers == ours and ('kugou', ours) in w.log  # stock's sources after ours
    assert [u for u in w.net.asked if 'coverartarchive' in u] == [f'https://coverartarchive.org/release-group/{g}/front-500' for g in (GROUP % 1, GROUP % 2)]


def test_the_cover_search_offline_asks_once_and_leaves_stocks_sources(w):
    CJson(w)
    keywords(w, 'Song 2', 'Album B', 'Artist B')
    w.net.up = False
    covers = []
    w.mock('online_addcoverlist', lambda url, *_: covers.append(s(url)))
    w.mock('stock_kugoucover_trampoline', lambda *_: w.log.append(('kugou',)))
    w.fn('ringnav_cover_search', None, C.c_char_p)(b'Album B Artist B')
    assert w.net.asked == [MB_SEARCH] and not covers and ('kugou',) in w.log
