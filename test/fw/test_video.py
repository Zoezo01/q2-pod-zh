"""patch/video.c, q2video (docs/internals.md#q2video): Internet Radio's sound (-r) and Spotify's sink (-s), run
whole as the program it is (video_host.py) on a Q2 made of its stubs (Q2 below).

Each ffmpeg it starts runs video.c's own child's half up to its exec (video_host.py), so the command line and
the descriptors it keeps across exec are the ones video.c gave it, and the stand-in ffmpeg answers from those
alone: its report on its 2, and the sound on whatever it holds as the output's pipe:N. The sound thread is a model
as the main loop sees it (au.played rising with the sound heard, au.done at the end); it runs for real on its own
and as Spotify's sink. Expected values come from what video.c's comments and docs/internals.md say it does."""
import ctypes as C
import json
import os
import struct
from array import array
from collections.abc import Iterator

import pytest

import host
import video_host

URL = 'http://radio.example/stream'
JACK, BT, USB = 'plughw:0,0', 'plug:bluealsa', 'usb'  # the outputs demo names (books.c output_device)
RADIO_STATE, RADIO_SOCK, SINK_SOCK = '/tmp/q2radio.state', '/tmp/q2radio.sock', '/tmp/q2sink.sock'
MONO, SWAP = '/tmp/q2mono', '/tmp/q2swap'
DEMO_FDS = (3, 5, 9)  # descriptors q2video inherits from demo, the first one past stdio's among them
# MIPS o32's numbers, as video.c declares them
O_CREAT, O_TRUNC, O_CLOEXEC = 0x100, 0x200, 0x80000
F_SETFD, FD_CLOEXEC = 2, 1
SIGKILL, SIGCHLD = 9, 18
EBUSY, ENODEV, EPIPE = 16, 19, 32
DAC_PCM, DAC_MUTE = 0xc0044d1b, 0xc0044d1f  # /dev/shanling_dac's, as hciplayer sets them
POLLIN, POLLHUP = 1, 0x10
S16_LE, RW_INTERLEAVED = 2, 3  # alsa-lib's SND_PCM_FORMAT_ and SND_PCM_ACCESS_
STRERROR = {EBUSY: b'Device or resource busy', ENODEV: b'No such device', EPIPE: b'Broken pipe'}
CARDS = b' 0 [Codec          ]: Codec - Q2 Codec\n                      Q2 Codec\n'
BANNER = ['ffmpeg version 4.2 Copyright (c) 2000-2019 the FFmpeg developers', '  built with gcc 7.3.0 (GCC)',
          '  configuration: --enable-gpl --enable-small', '  libavutil      56. 31.100 / 56. 31.100']


class Au(C.Structure):
    """video.c's au: the sound thread's state, shared with the main loop."""
    _fields_ = [('pcm', C.c_void_p), ('fd', C.c_int), ('gain', C.c_int), ('done', C.c_int), ('played', C.c_long),
                ('error', C.c_char * 64), ('buffer', C.c_ulong)]


class PollFd(C.Structure):
    _fields_ = [('fd', C.c_int), ('events', C.c_short), ('revents', C.c_short)]


def i32(x):
    """An int argument as its stub got it, in a 64-bit register."""
    return C.c_int(x).value


def curve(v):
    """hciplayer's soft volume for v in 1/65536: 0.002 v under 50, then 0.1 + 0.018 (v - 50), 1 at 100."""
    v = min(max(v, 0), 100)
    return 65536 * (2 * v if v < 50 else 100 + 18 * (v - 50)) // 1000


def outputs(argv):
    """ffmpeg's outputs after its input: [(N of pipe:N, {option: value})]."""
    out, opts, i = [], {}, argv.index('-i') + 2
    while i < len(argv):
        if argv[i].startswith('pipe:'):
            out.append((int(argv[i][5:]), opts))
            opts, i = {}, i + 1
        elif argv[i] == '-vn':
            opts['-vn'], i = '', i + 1
        else:
            opts[argv[i]], i = argv[i + 1], i + 2
    return out


def plan(begins, end=None, lost=None):
    """The sound a pipe carries, for the sound thread's model (absolute ms): it begins (None: never), the pipe ends,
    the output is lost."""
    return {'begins': begins, 'end': end, 'lost': lost}


def pcm(frames):
    """Stereo S16 with its two channels apart, the extremes among them."""
    out = array('h')
    for i in range(frames):
        out.extend(((i * 523) % 65536 - 32768, 32767 - (i * 211) % 65536))
    return out


def mix(sound, mono, swap):
    """Mono Audio (each frame's channels their mean) then the channel swap, as the sound thread applies them."""
    out = array('h', sound)
    for i in range(0, len(out), 2):
        if mono:
            out[i] = out[i + 1] = (out[i] + out[i + 1]) >> 1
        if swap:
            out[i], out[i + 1] = out[i + 1], out[i]
    return out


class Pipe:
    """A pipe from its read end: the pieces its writer sends, (time, bytes or a function making them), then EOF
    once nothing holds the write end, neither q2video (until it closes its copy) nor an ffmpeg (until it ends)."""
    def __init__(self):
        self.pieces: Iterator[tuple] = iter(())
        self.head: tuple | None = None
        self.buf = b''
        self.held = 1
        self.readers = 1
        self.run: Run | None = None
        self.plan: dict | None = None

    def feed(self, run, pieces):
        self.run, self.pieces = run, iter(pieces)
        self.head = next(self.pieces, None)
        if run is not None:
            run.pipes.append(self)

    def unread(self):
        return bool(self.buf) or self.head is not None

    def ready(self, now):
        """When a read won't block: now (data or EOF), a later time, or None (not as things stand)."""
        if self.buf:
            return now
        if self.head is not None:
            return max(now, self.head[0])
        if self.held:
            return None
        if self.run is not None and self.run.alive(now):
            return self.run.end
        return now

    def data(self, now):
        return self.ready(now) == now and (bool(self.buf) or self.head is not None)

    def read(self, now, n):
        """Up to n bytes, b'' at EOF, None if it would block."""
        if self.ready(now) != now:
            return None
        if not self.buf and self.head is not None:
            data, self.head = self.head[1], next(self.pieces, None)
            self.buf = data() if callable(data) else data
        out, self.buf = self.buf[:n], self.buf[n:]
        return out


class End:
    """A descriptor's end of a pipe."""
    def __init__(self, pipe, write):
        self.pipe, self.write = pipe, write


class OpenFile:
    def __init__(self, path, writable):
        self.path, self.writable, self.pos = path, writable, 0


class Run:
    """One ffmpeg: its pid, path and command line, the descriptors it kept across exec (n: what q2video had there,
    and which of q2video's descriptors it came from), when it started and when its output ends."""
    def __init__(self, pid, path, argv, fds, origins, start):
        self.pid, self.path, self.argv, self.fds, self.origins, self.start = pid, path, argv, fds, origins, start
        self.end: int | None = start
        self.killed: int | None = None
        self.reaped: int | None = None
        self.pipes: list[Pipe] = []

    def stop(self, now):
        """It ends now, writing nothing more (a broken pipe)."""
        self.end = now
        for pipe in self.pipes:
            pipe.pieces, pipe.head = iter(()), None

    def alive(self, now):
        """Not killed, and not done: its output unread (it blocks on a full pipe) or still to come."""
        return self.killed is None and (self.end is None or now < self.end or any(p.unread() for p in self.pipes))

    def opt(self, name):
        return self.argv[self.argv.index(name) + 1] if name in self.argv else None

    def writes(self, n):
        """The pipe this ffmpeg holds as its n, if it holds one there."""
        end = self.fds.get(n)
        return end.pipe if isinstance(end, End) and end.write else None


class Station:
    """One ffmpeg start on a station: its report's lines (ms after the start, line), the ms its sound comes, the
    stream ends and the output is lost (None: never)."""
    def __init__(self, lines=(), sound=None, end=None, lost=None):
        self.lines, self.sound, self.end, self.lost = list(lines), sound, end, lost


def icy(title='Artist - Song', bitrate='128'):
    """Stock ffmpeg 4.2's report on an MP3 station with -icy 1, at -loglevel verbose."""
    return [(0, f"[http @ 0x6e4a20] Opening '{URL}' for reading"), (300, f"Input #0, mp3, from '{URL}':"),
            (300, '  Metadata:'), (300, f'    icy-br          : {bitrate}'), (300, '    icy-genre       : Jazz'),
            (300, '    icy-name        : Station'), (300, f'    StreamTitle     : {title}'),
            (300, '  Duration: N/A, start: 0.000000, bitrate: 128 kb/s'),
            (300, '    Stream #0:0: Audio: mp3 (mp3float), 44100 Hz, stereo, fltp, 128 kb/s'), (310, 'Stream mapping:'),
            (310, '  Stream #0:0 -> #0:0 (mp3 (mp3float) -> pcm_s16le (native))'), (310, "Output #0, s16le, to 'pipe:4':"),
            (310, '    Stream #0:0: Audio: pcm_s16le, 48000 Hz, stereo, s16, 1536 kb/s')]


def update(at, title):
    """A StreamTitle change, which libavformat's http logs at verbose."""
    return at, f'[http @ 0x6e4a20] Metadata update for StreamTitle: {title}'


def ogg(artist='Artist 1', title='Song 1', codec='vorbis'):
    """Stock ffmpeg 4.2's report on an Ogg station (Icecast's, with no ICY tags), its first song's Vorbis comments under
    its stream with their keys in capitals, and again under the sound's output."""
    tags = [f'      {k:<16}: {v}' for k, v in (('ARTIST', artist), ('TITLE', title)) if v]
    lines = [f"Input #0, ogg, from '{URL}':", '  Duration: N/A, start: 0.000000, bitrate: N/A',
             f'    Stream #0:0: Audio: {codec}, 48000 Hz, stereo, fltp, 128 kb/s', '    Metadata:', *tags,
             '      SUBTITLE        : Live', 'Stream mapping:',
             f'  Stream #0:0 -> #0:0 ({codec} (native) -> pcm_s16le (native))',
             "Output #0, s16le, to 'pipe:4':", '  Metadata:', '    encoder         : Lavf58.29.100',
             '    Stream #0:0: Audio: pcm_s16le, 48000 Hz, stereo, s16, 1536 kb/s', '    Metadata:', *tags]
    return [(300 + i, x) for i, x in enumerate(lines)]  # in order: the stand-in sorts a millisecond's lines


REFUSED = [(0, '[tcp @ 0x6e4a20] Connection to tcp://radio.example:80 failed: Connection refused'),
           (0, f'{URL}: Connection refused')]


class Q2:
    """The Q2 around q2video: each call it makes is answered here. The clock (ms) moves only when it polls, sleeps
    or blocks; files, pipes, the DAC, ALSA, demo's datagram keys, and ffmpeg."""
    def __init__(self, video):
        self.v = video
        self.au = video.var('video__au', Au)
        self.stop = C.c_int.in_dll(video.lib, 'fw_video_stop')
        self.t0 = self.now = 3_600_000  # CLOCK_MONOTONIC: an hour after boot
        self.horizon = 120_000
        self.fds: dict[int, object] = {0: 'stdin', 1: 'stdout', 2: 'stderr', **{fd: 'demo' for fd in DEMO_FDS}}
        self.cloexec: set[int] = set()
        self.closed: list[int] = []
        self.files: dict[str, bytes] = {'/proc/asound/cards': CARDS}
        self.maps: dict[str, C.c_int] = {}
        self.renamed: list[tuple] = []
        self.unlinked: list[tuple] = []
        self.buffers: list[object] = []
        self.errors: list[str] = []
        self.sleeps: list[tuple] = []
        self.env: list[tuple] = []
        self.signals: list[tuple] = []
        self.setenv_ok = True
        self.stations: dict[str, list[Station]] = {}
        self.runs: list[Run] = []
        self.forks: list[int | None] = []  # a test's answers to the next forks: -1 refused, 0 this process the child
        self.refuse_pipes: list[bool] = []
        self.execs: list[dict] = []
        self.exits: list[int] = []
        self.kills: list[tuple] = []
        self.child: Run | None = None  # the process whose child's half runs, until its exec
        self.in_child = False
        self.child_fds: dict[int, int] = {}
        self.child_cloexec: set[int] = set()
        self.read_pipes: list[Pipe] = []
        self.busy: dict[str, int] = {}  # a PCM's open says EBUSY until ms after t0: hciplayer still holds it
        self.params_busy: dict[str, int] = {}  # bluealsa claims its PCM at set_params
        self.alsa_fds = 0
        self.pcm_name = ''
        self.pcm_fds: list[int] = []
        self.pcm_log: list[tuple] = []
        self.rate = 0
        self.written = array('h')
        self.write_answers: list[int] = []
        self.delay = self.delay_error = 0
        self.filling = False  # ALSA before it starts: all that was written still in its buffer, the delay
        self.buffer = self.avail = 0  # ALSA's ring and its free part, frames: by default nothing held in it
        self.strings: dict[int, int] = {}
        self.dac: list[tuple] = []
        self.real_writer = False  # the sound thread's body runs, whole, when it is started; else its model
        self.in_writer = False
        self.threads: list[tuple] = []
        self.thread_pipes: list[Pipe | None] = []
        self.joins: list[int] = []
        self.sound: dict | None = None
        self.keys: list[tuple] = []
        self.listening = False
        self.bound: list[str] = []
        self.sockets: list[tuple] = []
        mocks = {'setenv': self.setenv, 'close': self.close, 'signal': self.signal, 'open': self.open, 'read': self.read,
                 'write': self.write, 'ioctl': self.ioctl, 'mmap': self.mmap,
                 'socket': self.socket, 'bind': self.bind, 'recv': self.recv, 'unlink': self.unlink,
                 'rename': self.rename, 'pipe2': self.pipe2, 'fcntl': self.fcntl, 'fork': self.fork, 'dup2': self.dup2,
                 'execv': self.execv, '_exit': self.exit, 'kill': self.kill, 'waitpid': self.waitpid, 'poll': self.poll,
                 'usleep': self.usleep, 'clock_gettime': self.clock, 'pthread_create': self.thread,
                 'pthread_join': self.join,
                 'snd_pcm_open': self.pcm_open, 'snd_pcm_set_params': self.pcm_params, 'snd_pcm_close': self.pcm('close'),
                 'snd_pcm_drop': self.pcm('drop'), 'snd_pcm_prepare': self.pcm('prepare'),
                 'snd_pcm_drain': self.pcm('drain'), 'snd_pcm_writei': self.writei, 'snd_pcm_recover': self.recover,
                 'snd_pcm_delay': self.pcm_delay, 'snd_pcm_avail': lambda *_: self.avail,
                 'snd_pcm_get_params': self.pcm_get_params, 'snd_strerror': self.strerror}
        for name, fn in mocks.items():
            video.mock(f'fw_{name}', fn)

    # What a test does
    def run(self, *args):
        """q2video with args, to its end: main's status, or the one _exit was given."""
        argv = [b'/usr/bin/q2video', *(a.encode() for a in args)]
        table = (C.c_char_p * (len(argv) + 1))(*argv)
        return self.v.fn('fw_video_run', C.c_int, C.c_int, C.POINTER(C.c_char_p))(len(argv), table)

    def writer_alone(self):
        """The sound thread's body on its own, as it runs once started."""
        self.in_writer = True
        status = self.v.fn('fw_video_writer', C.c_int)()
        self.in_writer = False
        return status

    def press(self, key, at):
        """demo's datagram key, at ms after the start."""
        self.keys.append((self.t0 + at, key))
        self.keys.sort(key=lambda k: k[0])

    def stdin(self, data=b'', piece=2048, sound=None, hook=None):
        """librespot's sound on stdin, data in pieces then EOF (hook(i) before the i-th); sound: the model's plan."""
        pipe = Pipe()
        pipe.held = 0
        def pieces():
            for i in range(0, len(data), piece):
                def chunk(i=i):
                    if hook:
                        hook(i // piece)
                    return data[i:i + piece]
                yield self.t0, chunk
        pipe.feed(None, pieces())
        pipe.plan = sound
        self.fds[0] = End(pipe, False)
        return pipe

    # What the test reads
    def radio_states(self):
        """Each RADIO_STATE demo could read, as key=value lines."""
        return [{k: v for k, _, v in (line.partition('=') for line in data.decode().splitlines())}
                for _, path, data in self.renamed if path == RADIO_STATE]

    def events(self, *what):
        return [e[0] for e in self.pcm_log if e[0] in what]

    # The clock and the sound thread's model
    def advance(self, t):
        if t <= self.now:
            return
        self.hear(t)
        self.now = t
        if self.now - self.t0 > self.horizon and not self.in_writer:
            self.stop.value = 1

    def hear(self, t):
        """The sound thread from now to t: au.played rises with what is heard, until its plan's end."""
        s = self.sound
        if s is None or self.au.done:
            return
        p = s['plan']
        if p is None:  # nothing on its pipe: EOF at once
            self.au.done = 1
            return
        if p['begins'] is not None:
            s['heard'] += max(0, t - max(self.now, p['begins']))
            self.au.played = s['heard'] * self.rate // 1000
        if p['lost'] is not None and t >= p['lost']:
            self.au.error, self.au.done = b'write: No such device', 1
        if p['end'] is not None and t >= p['end']:
            self.au.done = 1

    # The system
    def alloc(self, obj, cloexec):
        fd = 0
        while fd in self.fds:
            fd += 1
        self.fds[fd] = obj
        if cloexec:
            self.cloexec.add(fd)
        return fd

    def setenv(self, name, value, overwrite, *_):
        self.env.append((C.string_at(name).decode(), C.string_at(value).decode(), i32(overwrite)))
        return 0 if self.setenv_ok else -1

    def signal(self, sig, handler, *_):
        self.signals.append((i32(sig), handler))
        return 0

    def close(self, fd, *_):
        fd = i32(fd)
        obj = self.fds.pop(fd, None)
        if obj is None:
            return -1
        self.cloexec.discard(fd)
        self.closed.append(fd)
        if isinstance(obj, End) and obj.write:
            obj.pipe.held -= 1
        elif isinstance(obj, End):
            obj.pipe.readers -= 1
            run = obj.pipe.run
            if not obj.pipe.readers and run is not None and run.alive(self.now):
                run.stop(self.now)  # ffmpeg's next write there fails, and it ends
        return 0

    def open(self, path, flags, *_):
        path, flags = C.string_at(path).decode(), i32(flags)
        obj: object = None
        if path == '/dev/shanling_dac':
            obj = 'dac'
        else:
            if flags & O_CREAT and (flags & O_TRUNC or path not in self.files):
                self.files[path] = b''
            obj = OpenFile(path, bool(flags & 3)) if path in self.files else None
        return -1 if obj is None else self.alloc(obj, flags & O_CLOEXEC)

    def read(self, fd, buf, n, *_):
        obj = self.fds.get(i32(fd))
        if isinstance(obj, OpenFile):
            data = self.files.get(obj.path, b'')[obj.pos:obj.pos + n]
            obj.pos += len(data)
        elif isinstance(obj, End) and not obj.write:
            got = obj.pipe.read(self.now, n)
            if got is None and self.in_writer:  # the sound thread blocks; time goes on
                when = obj.pipe.ready(self.now)
                if when is not None:
                    self.advance(when)
                    got = obj.pipe.read(self.now, n)
            if got is None:
                self.errors.append(f'a read of {i32(fd)} that blocks for ever')
                return -1
            if not self.in_writer and obj.pipe not in self.read_pipes:
                self.read_pipes.append(obj.pipe)
            data = got
        else:
            return -1
        C.memmove(buf, data, len(data))
        return len(data)

    def write(self, fd, buf, n, *_):
        obj = self.fds.get(i32(fd))
        if not isinstance(obj, OpenFile) or not obj.writable:
            return -1
        self.files[obj.path] += C.string_at(buf, n)
        return n

    def unlink(self, path, *_):
        path = C.string_at(path).decode()
        self.unlinked.append((self.now, path))
        return 0 if self.files.pop(path, None) is not None else -1

    def rename(self, old, new, *_):
        old, new = C.string_at(old).decode(), C.string_at(new).decode()
        if old not in self.files:
            return -1
        self.files[new] = self.files.pop(old)
        self.renamed.append((self.now, new, self.files[new]))
        return 0

    def mmap(self, addr, length, prot, flags, fd, offset):
        obj = self.fds.get(i32(fd))
        if isinstance(obj, OpenFile) and length == 4:
            if obj.path not in self.maps:
                self.maps[obj.path] = C.c_int(int.from_bytes(self.files[obj.path][:4].ljust(4, b'\0'), 'little', signed=True))
            return C.addressof(self.maps[obj.path])
        return -1

    def ioctl(self, fd, request, arg, *_):
        obj, request = self.fds.get(i32(fd)), request & 0xffffffff
        if obj != 'dac':
            return -1
        self.dac.append((self.now, request, C.c_int.from_address(arg).value))
        return 0

    def socket(self, domain, kind, protocol, *_):
        self.sockets.append((i32(domain), i32(kind) & ~O_CLOEXEC))
        return self.alloc('sock', i32(kind) & O_CLOEXEC)

    def bind(self, fd, addr, size, *_):
        path = C.string_at(addr + 2).decode()
        self.bound.append(path)
        if path in self.files:  # EADDRINUSE: a socket's node is there
            return -1
        self.files[path] = b''
        self.listening = True
        return 0

    def recv(self, fd, buf, n, flags, *_):
        if self.fds.get(i32(fd)) != 'sock' or not self.listening or not self.keys or self.keys[0][0] > self.now:
            self.errors.append('a recv that blocks')
            return -1
        data = self.keys.pop(0)[1][:n]
        C.memmove(buf, data, len(data))
        return len(data)

    def pipe2(self, fds, flags, *_):
        if self.refuse_pipes and self.refuse_pipes.pop(0):
            return -1
        pipe, pair, cloexec = Pipe(), (C.c_int * 2).from_address(fds), i32(flags) & O_CLOEXEC
        pair[0] = self.alloc(End(pipe, False), cloexec)
        pair[1] = self.alloc(End(pipe, True), cloexec)
        return 0

    def fcntl(self, fd, cmd, arg, *_):
        fd, cmd, arg = i32(fd), i32(cmd), i32(arg)
        if cmd != F_SETFD:
            return -1
        table = self.child_cloexec if self.in_child else self.cloexec
        if arg & FD_CLOEXEC:
            table.add(fd)
        else:
            table.discard(fd)
        return 0

    def fork(self, *_):
        """A new process: the harness runs its child's half before the pid comes back (video_host.py); a test's
        answer first, -1 refused or 0 (q2video itself the child)."""
        answer = self.forks.pop(0) if self.forks else None
        if answer is not None and answer <= 0:
            if answer == 0:
                self.become_child(None)
            return answer
        run = Run(4000 + len(self.runs), '', [], {}, {}, self.now)
        self.runs.append(run)
        self.become_child(run)
        return run.pid

    def become_child(self, run):
        self.in_child, self.child = True, run
        self.child_fds = {fd: fd for fd in self.fds}
        self.child_cloexec = set(self.cloexec)

    def dup2(self, old, new, *_):
        old, new = i32(old), i32(new)
        if not self.in_child:
            self.errors.append('q2video moved its own descriptors')
            return -1
        if old not in self.child_fds:
            return -1
        if old != new:
            self.child_fds[new] = self.child_fds[old]
            self.child_cloexec.discard(new)
        return new

    def execv(self, path, argv, *_):
        table, args = C.cast(argv, C.POINTER(C.c_char_p)), []
        while (arg := table[len(args)]) is not None:
            args.append(arg.decode())
        path, kept = C.string_at(path).decode(), {n: fd for n, fd in self.child_fds.items() if n not in self.child_cloexec}
        run, self.in_child, self.child = self.child, False, None
        if run is None:  # q2video itself, as a test's fork made it: no ffmpeg here
            self.execs.append({'path': path, 'argv': args, 'fds': kept})
            return -1
        run.path, run.argv, run.origins = path, args, kept
        run.fds = {n: self.fds.get(fd) for n, fd in kept.items()}
        if path == '/usr/bin/ffmpeg':
            self.ffmpeg(run)
        return -1  # ffmpeg runs; the child's _exit after it goes back to the parent (video_host.py)

    def exit(self, status, *_):
        self.exits.append(i32(status))

    def kill(self, pid, sig, *_):
        run = next((r for r in self.runs if r.pid == i32(pid)), None)
        if run is None:
            return -1
        self.kills.append((self.now, run.pid, i32(sig)))
        if i32(sig) == SIGKILL and run.killed is None:
            run.killed = self.now
        return 0

    def waitpid(self, pid, status, options, *_):
        run = next((r for r in self.runs if r.pid == i32(pid)), None)
        if run is None or run.reaped is not None:
            return -1
        if run.alive(self.now):
            self.errors.append(f'a wait for {run.pid}, still running')
        run.reaped = self.now
        if status:
            C.c_int.from_address(status).value = SIGKILL if run.killed is not None else 0
        return run.pid

    def poll(self, fds, n, timeout, *_):
        table, timeout = (PollFd * n).from_address(fds), i32(timeout)
        times = [t for t in (self.ready(p.fd) for p in table if p.fd >= 0) if t is not None]
        if timeout >= 0:
            times.append(self.now + timeout)
        if not times:
            self.errors.append('a poll that waits for ever')
            self.stop.value = 1
            return 0
        self.advance(min(times))
        for p in table:
            p.revents = self.revents(p.fd) if p.fd >= 0 else 0
        return sum(p.revents != 0 for p in table)

    def ready(self, fd):
        obj = self.fds.get(fd)
        if obj == 'sock':
            return self.keys[0][0] if self.listening and self.keys else None
        if isinstance(obj, End) and not obj.write:
            return obj.pipe.ready(self.now)
        return None

    def revents(self, fd):
        obj = self.fds.get(fd)
        if obj == 'sock':
            return POLLIN if self.ready(fd) is not None and self.keys[0][0] <= self.now else 0
        if isinstance(obj, End) and not obj.write and obj.pipe.ready(self.now) == self.now:
            return POLLIN if obj.pipe.data(self.now) else POLLHUP
        return 0

    def usleep(self, us, *_):
        self.sleeps.append((self.now, us & 0xffffffff))
        self.advance(self.now + (us & 0xffffffff) // 1000)
        return 0

    def clock(self, which, ts, *_):
        pair = (C.c_long * 2).from_address(ts)
        if i32(which) != 1:
            self.errors.append('a clock that is not CLOCK_MONOTONIC')
        pair[0], pair[1] = self.now // 1000, self.now % 1000 * 1_000_000
        return 0

    def thread(self, thread, attr, start, arg, *_):
        C.c_ulong.from_address(thread).value = 0x7100 + len(self.threads)
        end = self.fds.get(self.au.fd)
        pipe = end.pipe if isinstance(end, End) and not end.write else None
        self.threads.append((self.now, self.au.fd))
        self.thread_pipes.append(pipe)
        if self.real_writer:
            self.in_writer = True
            C.CFUNCTYPE(C.c_void_p, C.c_void_p)(start)(arg)
            self.in_writer = False
            return 0
        self.sound = {'plan': pipe.plan if pipe else None, 'heard': 0}
        self.hear(self.now)
        return 0

    def join(self, thread, ret, *_):
        self.joins.append(self.now)
        if not self.real_writer:
            self.au.done = 1  # it reads to the end of a pipe no ffmpeg writes now
        self.sound = None
        return 0

    # ALSA
    def pcm_open(self, out, name, stream, mode, *_):
        name = C.string_at(name).decode()
        self.pcm_log.append(('open', self.now, name))
        if self.now < self.t0 + self.busy.get(name, 0):
            return -EBUSY
        self.pcm_name = name
        self.pcm_fds = [self.alloc(('alsa', name), True) for _ in range(self.alsa_fds)]
        C.c_void_p.from_address(out).value = 0x6600
        return 0

    def pcm_params(self, pcm, fmt, access, channels, rate, resample):
        self.rate = rate & 0xffffffff
        self.pcm_log.append(('params', self.now, (i32(fmt), i32(access), i32(channels), self.rate, i32(resample))))
        return -EBUSY if self.now < self.t0 + self.params_busy.get(self.pcm_name, 0) else 0

    def pcm(self, what):
        def call(pcm, *_):
            self.pcm_log.append((what, self.now))
            if what == 'close':
                for fd in self.pcm_fds:
                    self.close(fd)
            return 0
        return call

    def writei(self, pcm, buf, frames, *_):
        answer = min(self.write_answers.pop(0), frames) if self.write_answers else frames
        if answer > 0:
            self.written.frombytes(C.string_at(buf, answer * 4))
        return answer

    def recover(self, pcm, err, silent, *_):
        self.pcm_log.append(('recover', self.now, i32(err), i32(silent)))
        return 0 if i32(err) == -EPIPE else i32(err)

    def pcm_delay(self, pcm, out, *_):
        if self.delay_error:
            return self.delay_error
        C.c_long.from_address(out).value = len(self.written) // 2 if self.filling else self.delay
        return 0

    def pcm_get_params(self, pcm, buffer, period, *_):
        self.pcm_log.append(('get_params', self.now))
        C.c_ulong.from_address(buffer).value = self.buffer
        return 0

    def strerror(self, err, *_):
        if i32(err) not in self.strings:
            text = C.create_string_buffer(STRERROR.get(-i32(err), b'Unknown error'))
            self.buffers.append(text)
            self.strings[i32(err)] = C.addressof(text)
        return self.strings[i32(err)]

    # The stand-in ffmpeg
    def ffmpeg(self, run):
        """Stock ffmpeg 4.2 on run's command line, as far as q2video can tell: what it writes where, and when."""
        report = run.writes(2)
        lines = [] if '-hide_banner' in run.argv else [(0, x) for x in BANNER]
        lines += self.station(run, run.opt('-i'))
        if report is not None and run.opt('-loglevel') != 'quiet':
            report.feed(run, sorted((run.start + t, (x + '\n').encode()) for t, x in lines))

    def station(self, run, url):
        tries = self.stations.get(url) or [Station(REFUSED, end=0)]
        st = tries[min(sum(r.opt('-i') == url for r in self.runs), len(tries)) - 1]
        verbose = run.opt('-loglevel') in ('verbose', 'debug', 'trace')
        lines = [(t, x) for t, x in st.lines if verbose or 'Metadata update' not in x]
        outs = outputs(run.argv)
        sound = run.writes(outs[0][0]) if outs else None
        run.end = None if st.end is None else run.start + st.end
        if sound is None:
            run.end = run.start
            return lines + [(0, f'pipe:{outs[0][0] if outs else 4}: Bad file descriptor')]
        sound.feed(run, ())
        sound.plan = plan(None if st.sound is None else run.start + st.sound, end=run.end,
                          lost=None if st.lost is None else run.start + st.lost)
        return lines


@pytest.fixture(scope='session')
def q2video(tmp_path_factory):
    return video_host.build(tmp_path_factory.mktemp('q2video'), video_host.flags())


@pytest.fixture
def q2(q2video, tmp_path):
    loaded = host.Fw(q2video, tmp_path)
    world = Q2(loaded)
    yield world
    unknown = loaded.unknown()
    loaded.close()
    assert not world.errors, world.errors
    if unknown and os.environ.get('Q2_FW_STRICT'):
        pytest.fail(f'unmocked stubs with no known return type: {", ".join(unknown)}')


def radio(q2, device=JACK, *volume):
    """q2video -r DEVICE URL [VOLUME], as radio.c starts it."""
    return q2.run('-r', device, URL, *volume)


def sink(q2, device=BT, volume='h50'):
    """q2video -s DEVICE VOLUME, librespot's sink as spotify.c starts it."""
    return q2.run('-s', device, volume)


# The build
def test_everything_q2video_reaches_outside_itself_is_a_stub(q2video):
    types = json.loads((q2video.parent/'stubs.json').read_text())
    effects = {'fork', 'execv', 'kill', 'unlink', 'rename', 'open', 'write', 'ioctl', 'mmap', 'socket', 'bind',
               'setenv', '_exit', 'snd_pcm_open', 'snd_pcm_writei', 'pthread_create'}
    assert {f'fw_{name}' for name in effects} <= set(types)
    assert not {name[3:] for name in types} & video_host.PURE
    assert None not in types.values()  # each declared in video.c, so strict mode knows what it returns


# Starting
def test_too_few_arguments_touch_nothing(q2):
    assert q2.run(BT) == 2
    assert not q2.v.calls()


def test_a_run_that_is_neither_radio_s_nor_the_sink_s_plays_nothing(q2):
    assert q2.run(BT, '/mnt/mmc/Videos/a clip.mp4', '60') == 2
    assert not q2.runs and not q2.pcm_log and not q2.sockets and not q2.renamed


def tune(q2, quit_at=1000):
    """A station that sounds 300 ms in, and demo's quit."""
    q2.stations[URL] = [Station(icy(), sound=300)]
    q2.press(b'q', quit_at)


def test_alsa_s_lock_goes_and_demo_s_descriptors_close_before_anything_opens(q2):
    tune(q2)
    assert radio(q2) == 0
    calls = q2.v.calls()
    names = [name for name, _ in calls]
    first = min(names.index(n) for n in ('fw_open', 'fw_socket', 'fw_pipe2', 'fw_snd_pcm_open'))
    assert names.index('fw_setenv') < first and q2.env == [('LIBASOUND_THREAD_SAFE', '0', 1)]
    closed = {i32(args[0]) for name, args in calls[:first] if name == 'fw_close'}
    assert set(DEMO_FDS) <= closed and not closed & {0, 1, 2}  # demo's go; stdio stays, stdin being Spotify's sound
    assert names.index('fw_signal') < names.index('fw_fork') and q2.signals == [(SIGCHLD, 0)]  # SIG_DFL: waitpid sees ffmpeg


def test_if_alsa_s_lock_can_t_be_turned_off_it_plays_nothing(q2):
    q2.setenv_ok = False
    assert radio(q2) == 1
    assert not q2.runs and not q2.pcm_log and not q2.renamed


def test_ffmpeg_keeps_its_stdio_and_its_pipes_and_nothing_else(q2):
    tune(q2)
    assert radio(q2) == 0
    (run,) = q2.runs
    assert run.path == '/usr/bin/ffmpeg' and '-nostdin' in run.argv and run.opt('-i') == URL
    assert set(run.fds) == {0, 1, 2, 4}
    assert run.writes(2) in q2.read_pipes and run.writes(4) is q2.thread_pipes[0]  # its report here, its sound there


def test_a_child_whose_ffmpeg_won_t_start_exits_127(q2):
    q2.forks = [0]  # q2video itself is ffmpeg's child
    assert radio(q2) == 127 and q2.exits == [127]
    (child,) = q2.execs
    assert child['path'] == child['argv'][0] == '/usr/bin/ffmpeg' and URL in child['argv']
    assert set(child['fds']) == {0, 1, 2, 4}
    report = q2.fds[child['fds'][2]]
    assert isinstance(report, End) and report.write
    assert any(isinstance(o, End) and not o.write and o.pipe is report.pipe for o in q2.fds.values())


def test_a_stale_socket_left_by_an_earlier_run_still_takes_keys(q2):
    q2.files[RADIO_SOCK] = b''
    tune(q2)
    assert radio(q2) == 0
    assert RADIO_SOCK in [path for _, path in q2.unlinked] and RADIO_STATE not in q2.files  # the quit came


def test_bluetooth_leaves_the_dac_alone(q2):
    tune(q2)
    assert radio(q2, BT, '60') == 0
    assert not q2.dac and q2.au.gain == curve(60)


# Sound
@pytest.mark.parametrize('cards, device', [
    (CARDS, 'plughw:1,0'),  # none listed: card 1, behind the headphone DAC's 0
    (CARDS + b' 1 [Loopback       ]: Loopback - Loopback\n                      Loopback 1\n'
     b' 2 [DAC            ]: USB-Audio - USB Audio DAC\n                      USB Audio DAC at usb-1, high speed\n', 'plughw:2,0'),
])
def test_usb_is_the_card_the_kernel_lists_as_usb_audio(q2, cards, device):
    q2.files['/proc/asound/cards'] = cards
    assert sink(q2, USB, 'h60') == 0
    assert [e[2] for e in q2.pcm_log if e[0] == 'open'] == [device]


@pytest.mark.parametrize('device, busy, opens', [(JACK, 1500, True), (JACK, 2500, False), (BT, 2500, True),
                                                 (BT, 5500, False), (USB, 2500, True), (USB, 5500, False)])
def test_the_pcm_is_tried_again_while_its_last_user_lets_go(q2, device, busy, opens):
    q2.busy['plughw:1,0' if device == USB else device] = busy
    assert sink(q2, device) == (0 if opens else 1)
    tries = [t - q2.t0 for what, t, *_ in q2.pcm_log if what == 'open']
    (step,) = {b - a for a, b in zip(tries, tries[1:])}  # evenly spaced
    if opens:
        assert tries[-2] < busy <= tries[-1] and q2.threads  # at the first try once it is let go
    else:  # given up at its last try before the limit: the headphone DAC's 2 s (docs/internals.md), Bluetooth's and USB's 5
        assert tries[-1] < (2000 if device == JACK else 5000) <= tries[-1] + step
        assert q2.au.error == b'open: Device or resource busy' and not q2.threads
        assert not q2.events('get_params')  # no PCM to ask: libasound would fault on a null one


@pytest.mark.parametrize('device, opens', [(BT, True), (JACK, False)])
def test_bluealsa_busy_at_its_params_is_waited_out_the_jack_is_not(q2, device, opens):
    q2.params_busy[device] = 1500
    assert sink(q2, device) == (0 if opens else 1)
    tries = [t - q2.t0 for what, t, *_ in q2.pcm_log if what == 'params']
    if opens:
        assert len({b - a for a, b in zip(tries, tries[1:])}) == 1 and tries[-2] < 1500 <= tries[-1]  # once it is free
    else:
        assert tries == [0] and q2.au.error == b'params: Device or resource busy' and q2.events('close') == ['close']
        assert not q2.events('get_params')  # closed: none left to ask


@pytest.mark.parametrize('mode', ['radio', 'sink'])
def test_the_pcm_is_s16_interleaved_stereo_at_the_sound_s_rate(q2, mode):
    q2.buffer = 9600
    if mode == 'radio':
        tune(q2)
        radio(q2)
    else:
        sink(q2)
    (params,) = [e[2] for e in q2.pcm_log if e[0] == 'params']
    sound = [o for r in q2.runs for n, o in outputs(r.argv) if o.get('-f') == 's16le']
    rate = int(sound[0]['-ar']) if mode != 'sink' else 44100  # ffmpeg's -ar, or librespot's 44.1 kHz
    assert params == (S16_LE, RW_INTERLEAVED, 2, rate, 1) and all(o['-ac'] == '2' for o in sound)
    assert q2.au.buffer == 9600  # the ring the writer floors its delay at


# Internet Radio
def test_a_station_connects_plays_and_follows_its_titles(q2):
    q2.stations[URL] = [Station(icy() + [update(5000, 'Next - Song')], sound=800)]
    q2.press(b'q', 7000)
    assert radio(q2) == 0
    names = [name for name, _ in q2.v.calls()]
    assert names.index('fw_rename') < names.index('fw_snd_pcm_open')  # connecting at once, the PCM waited for after
    states = q2.radio_states()
    assert states[0]['state'] == 'connecting'
    assert all(set(s) == {'state', 'title', 'codec', 'bitrate', 'at', 'error'} for s in states)  # each whole
    playing = [s for s in states if s['state'] == 'playing']
    sound = q2.runs[0].start + 800
    assert sound <= int(playing[0]['at']) < sound + 500  # the sound's start, within one poll
    assert (playing[0]['codec'], playing[0]['bitrate'], playing[0]['title']) == ('MP3', '128', 'Artist - Song')
    assert playing[-1]['title'] == 'Next - Song' and playing[-1]['at'] == playing[0]['at']
    assert RADIO_STATE not in q2.files and RADIO_SOCK not in q2.files  # a quit: demo sees it gone
    assert all(r.killed is not None and r.reaped is not None for r in q2.runs)
    assert [(r, v) for _, r, v in q2.dac] == [(DAC_PCM, 0), (DAC_MUTE, 0), (DAC_MUTE, 1)]


def songs(q2):
    """The titles demo read while the station played, each once, in order."""
    titles = [s['title'] for s in q2.radio_states() if s['state'] == 'playing']
    return [t for i, t in enumerate(titles) if not i or titles[i - 1] != t]


@pytest.mark.parametrize('artist, title, codec, shown', [
    ('Artist 1', 'Song 1', 'vorbis', 'Artist 1 - Song 1'),  # not SUBTITLE's
    ('', 'Song 1', 'opus', 'Song 1'),  # an artist only if named
    ('', '', 'opus', '')])  # a stream that names no song (LISTEN.moe's): the codec and bitrate alone
def test_an_ogg_station_shows_the_song_its_stream_starts_with(q2, artist, title, codec, shown):
    q2.stations[URL] = [Station(ogg(artist, title, codec), sound=500)]
    q2.press(b'q', 3000)
    assert radio(q2) == 0
    assert songs(q2) == [shown]
    assert {s['codec'] for s in q2.radio_states() if s['state'] == 'playing'} == {codec.upper()}
    (run,) = q2.runs
    assert [n for n, _ in outputs(run.argv)] == [4]  # the sound alone


def test_a_dropped_stream_starts_again_at_once_then_a_second_later_each_silent_start(q2):
    q2.stations[URL] = [Station(icy(), sound=500, end=3000), Station(REFUSED, end=0)]
    assert radio(q2) == 0
    starts = [r.start - q2.t0 for r in q2.runs]
    assert starts[:2] == [0, 3000]
    assert [b - a for a, b in zip(starts[1:], starts[2:])] == [1000, 2000, 3000, 4000]  # five in a row, no sound
    assert q2.radio_states()[-1]['state'] == 'error' and RADIO_STATE in q2.files


def test_a_station_that_never_sounds_gives_up_after_20_s(q2):
    q2.stations[URL] = [Station(icy())]
    assert radio(q2) == 1
    last = q2.radio_states()[-1]
    assert (last['state'], last['error']) == ('error', 'no sound from the output')
    assert 20000 < q2.renamed[-1][0] - q2.t0 <= 20500
    (run,) = q2.runs
    assert run.killed is not None and RADIO_SOCK not in q2.files


def test_a_quit_while_waiting_to_start_again_stops_at_once(q2):
    q2.stations[URL] = [Station(REFUSED, end=0)]
    q2.press(b'q', 1500)
    assert radio(q2) == 0
    assert [r.start - q2.t0 for r in q2.runs] == [0, 1000]
    assert [t - q2.t0 for t, path in q2.unlinked if path == RADIO_STATE][-1] <= 1600


@pytest.mark.parametrize('device, volume, gain', [
    (BT, ('60',), curve(80)), (BT, ('h60',), 65536), (JACK, (), 65536)])  # the DAC applies its own
def test_a_volume_key_sets_the_station_s_soft_gain(q2, device, volume, gain):
    q2.press(b'v' + bytes([80]), 1000)
    tune(q2, 1500)
    assert radio(q2, device, *volume) == 0
    assert q2.au.gain == gain


def test_a_station_on_an_output_that_won_t_open_says_why(q2):
    q2.busy[JACK] = 60000
    assert radio(q2) == 0
    last = q2.radio_states()[-1]
    assert not q2.runs and (last['state'], last['error']) == ('error', 'open: Device or resource busy')


def test_a_lost_output_ends_the_station_with_its_reason(q2):
    q2.stations[URL] = [Station(icy(), sound=500, lost=2000), Station(icy(), lost=0)]
    assert radio(q2, BT, '60') == 0
    states = q2.radio_states()
    assert 'playing' in [s['state'] for s in states] and len(q2.runs) == 6
    assert (states[-1]['state'], states[-1]['error']) == ('error', 'write: No such device')


def test_a_pipe_already_on_ffmpeg_s_number_still_reaches_ffmpeg(q2):
    """Started without stdio, with ALSA's own descriptors open, the sound pipe's write end lands on 4 itself, which
    dup2 leaves close-on-exec."""
    q2.fds = {}
    q2.alsa_fds = 2
    q2.stations[URL] = [Station(icy(), sound=500)]
    q2.press(b'q', 2000)
    assert radio(q2, BT, '60') == 0
    (run,) = q2.runs
    assert run.origins[4] == 4 and run.writes(4) is not None
    assert 'playing' in [s['state'] for s in q2.radio_states()]


# Spotify's sink, its sound thread run whole
@pytest.mark.parametrize('volume, gain', [('60', curve(60)), ('25', curve(25)), ('100', 65536), ('h60', 65536)])
def test_spotify_s_sound_reaches_alsa_at_the_soft_volume_then_drains(q2, volume, gain):
    sound = pcm(4 * 1024 + 391)  # a short last block too
    q2.real_writer = True
    q2.stdin(sound.tobytes())
    assert sink(q2, BT, volume) == 0
    assert q2.written == array('h', [s * gain >> 16 for s in sound])
    assert q2.pcm_log[-1][0] == 'drain' and SINK_SOCK not in q2.files  # the buffer's last 200 ms still play


@pytest.mark.parametrize('mono, swap', [(1, 0), (0, 1), (1, 1)])
def test_spotify_on_the_jack_follows_mono_audio_and_the_channel_swap(q2, mono, swap):
    sound = pcm(2048)
    q2.files[MONO], q2.files[SWAP] = struct.pack('<i', mono), struct.pack('<i', swap)
    q2.real_writer = True
    q2.stdin(sound.tobytes())
    assert sink(q2, JACK, 'h100') == 0
    assert q2.written == mix(sound, mono, swap)
    assert [(r, v) for _, r, v in q2.dac[:2]] == [(DAC_PCM, 0), (DAC_MUTE, 0)]


def test_mono_audio_turned_on_mid_stream_applies_by_the_next_hundredth_chunk(q2):
    sound = pcm(512 * 300)
    q2.real_writer = True
    q2.stdin(sound.tobytes(), hook=lambda i: i == 150 and q2.files.update({MONO: struct.pack('<i', 1)}))
    assert sink(q2, JACK, 'h100') == 0
    cut = 200 * 512 * 2  # looked for every hundredth chunk till it appears
    assert q2.written[:cut] == sound[:cut] and q2.written[cut:] == mix(sound[cut:], 1, 0)


def test_mono_audio_turned_off_applies_from_the_next_chunk(q2):
    sound = pcm(512 * 20)
    q2.files[MONO] = struct.pack('<i', 1)
    q2.real_writer = True
    q2.stdin(sound.tobytes(), hook=lambda i: i == 10 and setattr(q2.maps[MONO], 'value', 0))
    assert sink(q2, JACK, 'h100') == 0
    cut = 10 * 512 * 2
    assert q2.written[:cut] == mix(sound[:cut], 1, 0) and q2.written[cut:] == sound[cut:]


def test_a_lost_device_ends_spotify_s_sink_without_a_drain(q2):
    q2.real_writer = True
    q2.stdin(pcm(4096).tobytes())
    q2.write_answers = [512, -ENODEV]
    assert sink(q2) == 0
    assert q2.au.error == b'write: No such device' and 'drain' not in q2.events('drain')
    assert len(q2.written) == 2 * 512 and len(q2.v.calls('fw_read')) == 2  # it stopped there
    assert [e[2:] for e in q2.pcm_log if e[0] == 'recover'] == [(-ENODEV, 1)]


@pytest.mark.parametrize('end', [None, 600])  # librespot's sound going on, or ending as the quit comes
def test_a_quit_ends_spotify_s_sink_without_a_drain(q2, end):
    q2.stdin(sound=plan(q2.t0, end=end and q2.t0 + end))
    q2.press(b'v' + bytes([40]), 200)
    q2.press(b'q', 600)
    assert sink(q2, BT, '80') == 0
    assert q2.au.gain == curve(40) and 'drain' not in q2.events('drain') and SINK_SOCK not in q2.files


def test_spotify_s_sink_without_a_device_exits_1(q2):
    q2.busy[BT] = 60000
    assert sink(q2, BT, '50') == 1 and not q2.threads and SINK_SOCK not in q2.files


# The sound thread on its own
def writer(q2, data, gain=65536):
    q2.stdin(data)
    q2.au.fd, q2.au.pcm, q2.au.gain = 0, 0x6600, gain
    return q2.writer_alone()


def test_the_writer_resumes_partial_writes_recovers_an_underrun_and_waits_out_no_progress(q2):
    sound = pcm(1024)
    q2.write_answers = [128, -EPIPE, 0, 512]
    assert writer(q2, sound.tobytes()) == 0
    assert q2.written == sound  # every frame once, in order
    assert [e[2:] for e in q2.pcm_log if e[0] == 'recover'] == [(-EPIPE, 1)]  # quietly
    order = [name for name, _ in q2.v.calls() if name in ('fw_snd_pcm_writei', 'fw_usleep')]
    assert order[3] == 'fw_usleep' and order.count('fw_usleep') == 1  # once, after the write that took nothing
    (us,) = [us for _, us in q2.sleeps]
    assert 0 < us < 512 * 1_000_000 // 48000  # shorter than a chunk's sound, about 11 ms
    assert q2.au.done == 1 and not q2.au.error


def test_an_underrun_that_recovery_does_not_cure_ends_a_sink(q2):
    q2.write_answers = [-EPIPE, -EPIPE]
    assert writer(q2, pcm(1024).tobytes()) == 0
    assert q2.au.error == b'write: Broken pipe' and q2.au.done and not q2.written


@pytest.mark.parametrize('delay, error, avail, heard', [
    (300, 0, 9600, 1536 - 300), (0, 0, 9600, 1536), (0, -EPIPE, 9600, 1536), (-64, 0, 9600, 1536),
    (2000, 0, 9600, 0),  # more than was written (Bluetooth adds the link's own delay): nothing heard yet
    (300, 0, 9600 - 1536, 0),  # after a restart's drop bluealsa answers the link alone; the ring still holds it all
    (300, 0, 9600 - 1000, 1536 - 1000), (300, 0, -EPIPE, 1536 - 300),  # the ring past the delay; an XRUN: the delay
])
def test_played_is_what_was_written_less_what_alsa_still_holds(q2, delay, error, avail, heard):
    q2.delay, q2.delay_error, q2.avail = delay, error, avail
    q2.au.buffer = 9600
    writer(q2, pcm(1536).tobytes())
    assert q2.au.played == heard


def test_nothing_is_heard_while_alsa_still_holds_all_that_was_written(q2):
    q2.filling = True
    writer(q2, pcm(1536).tobytes())
    assert q2.au.played == 0


def test_a_short_last_block_plays_and_half_a_frame_does_not(q2):
    sound = pcm(700)
    writer(q2, sound.tobytes() + b'\x01\x02')
    assert q2.written == sound


def test_the_gain_scales_every_sample_down_and_never_wraps(q2):
    sound = pcm(1024)
    writer(q2, sound.tobytes(), gain=curve(99))
    assert q2.written == array('h', [s * curve(99) >> 16 for s in sound])
