"""patch/video.c, q2video (docs/internals.md#videos): Videos' player, Internet Radio's sound (-r) and Spotify's
sink (-s), run whole as the program it is (video_host.py) on a Q2 made of its stubs (Q2 below).

Each ffmpeg it starts runs video.c's own child's half up to its exec (video_host.py), so the command line and
the descriptors it keeps across exec are the ones video.c gave it, and the stand-in ffmpeg answers from those
alone: rawvideo frames sized by its -vf at its -r on whatever it holds as the output's pipe:N, its report on its
2. The sound thread is a model as the main loop sees it (au.played rising with the sound heard, au.done at the
end); it runs for real on its own and as Spotify's sink. Expected values come from what video.c's comments and
docs/internals.md say it does."""
import ctypes as C
import functools
import json
import os
import struct
from array import array
from collections.abc import Callable, Iterator

import pytest

import host
import video_host

W, H = 320, 375  # the panel's picture; /dev/fb0 holds two pages of it
ROW = W * 4
FRAME_MS = 40  # ffmpeg's -r 25
# The longest the playing loop sleeps in a poll. Its time t is taken before the poll, so a key's 400 ms seek wait
# and 1.5 s bar run from the poll's start, up to this much before the key came.
POLL_MS = 100
CLIP, URL = '/mnt/mmc/Videos/a clip.mp4', 'http://radio.example/stream'
JACK, BT, USB = 'plughw:0,0', 'plug:bluealsa', 'usb'  # the outputs demo names (books.c output_device)
PLACE, STATE, RADIO_STATE = '/tmp/q2video.place', '/tmp/q2video.state', '/tmp/q2radio.state'
SOCK, RADIO_SOCK, SINK_SOCK = '/tmp/q2video.sock', '/tmp/q2radio.sock', '/tmp/q2sink.sock'
MONO, SWAP = '/tmp/q2mono', '/tmp/q2swap'
DEMO_FDS = (3, 5, 9)  # descriptors q2video inherits from demo, the first one past stdio's among them
# MIPS o32's numbers, as video.c declares them
O_WRONLY, O_CREAT, O_TRUNC, O_CLOEXEC = 1, 0x100, 0x200, 0x80000
F_SETFD, F_SETPIPE_SZ, FD_CLOEXEC = 2, 1031, 1
SIGKILL, SIGCHLD = 9, 18
EBUSY, ENODEV, EPIPE = 16, 19, 32
FBIOGET_VSCREENINFO, FBIOGET_FSCREENINFO, FBIOPAN_DISPLAY = 0x4600, 0x4602, 0x4606
DAC_PCM, DAC_MUTE = 0xc0044d1b, 0xc0044d1f  # /dev/shanling_dac's, as hciplayer sets them
POLLIN, POLLHUP = 1, 0x10
S16_LE, RW_INTERLEAVED = 2, 3  # alsa-lib's SND_PCM_FORMAT_ and SND_PCM_ACCESS_
STRERROR = {EBUSY: b'Device or resource busy', ENODEV: b'No such device', EPIPE: b'Broken pipe'}
CARDS = b' 0 [Codec          ]: Codec - Q2 Codec\n                      Q2 Codec\n'
BANNER = ['ffmpeg version 4.2 Copyright (c) 2000-2019 the FFmpeg developers', '  built with gcc 7.3.0 (GCC)',
          '  configuration: --enable-gpl --enable-small', '  libavutil      56. 31.100 / 56. 31.100']


class Bitfield(C.Structure):
    _fields_ = [('offset', C.c_uint32), ('length', C.c_uint32), ('msb_right', C.c_uint32)]


class VarInfo(C.Structure):
    """linux/fb.h's fb_var_screeninfo."""
    _fields_ = [*((k, C.c_uint32) for k in 'xres yres xres_virtual yres_virtual xoffset yoffset bits_per_pixel grayscale'.split()),
                *((k, Bitfield) for k in ('red', 'green', 'blue', 'transp')),
                *((k, C.c_uint32) for k in ('nonstd activate height width accel_flags pixclock left_margin right_margin '
                                            'upper_margin lower_margin hsync_len vsync_len sync vmode rotate colorspace').split()),
                ('reserved', C.c_uint32 * 4)]


class FixInfo(C.Structure):
    """fb_fix_screeninfo, its unsigned longs 32 bits as on the Q2."""
    _fields_ = [('id', C.c_char * 16), ('smem_start', C.c_uint32), ('smem_len', C.c_uint32), ('type', C.c_uint32),
                ('type_aux', C.c_uint32), ('visual', C.c_uint32), ('xpanstep', C.c_uint16), ('ypanstep', C.c_uint16),
                ('ywrapstep', C.c_uint16), ('line_length', C.c_uint32), ('mmio_start', C.c_uint32),
                ('mmio_len', C.c_uint32), ('accel', C.c_uint32), ('capabilities', C.c_uint16), ('reserved', C.c_uint16 * 2)]


class Au(C.Structure):
    """video.c's au: the sound thread's state, shared with the main loop."""
    _fields_ = [('pcm', C.c_void_p), ('fd', C.c_int), ('gain', C.c_int), ('paused', C.c_int), ('done', C.c_int),
                ('played', C.c_long), ('error', C.c_char * 64), ('video', C.c_int), ('buffer', C.c_ulong)]


class PollFd(C.Structure):
    _fields_ = [('fd', C.c_int), ('events', C.c_short), ('revents', C.c_short)]


def i32(x):
    """An int argument as its stub got it, in a 64-bit register."""
    return C.c_int(x).value


def curve(v):
    """hciplayer's soft volume for v in 1/65536: 0.002 v under 50, then 0.1 + 0.018 (v - 50), 1 at 100."""
    v = min(max(v, 0), 100)
    return 65536 * (2 * v if v < 50 else 100 + 18 * (v - 50)) // 1000


def pixel(frame, y):
    """The stand-in ffmpeg's picture: its frame's number and the row in every pixel, so a frame shows whole or wrong."""
    return frame & 0xffff | (0x8000 | y) << 16


@functools.lru_cache(maxsize=4)
def picture(frame, w, h, fmt):
    """Frame number frame as rawvideo w x h: bgra's bytes are the word above's, little-endian; rgba swaps red and blue."""
    rows = []
    for y in range(h):
        b, g, r, a = struct.pack('<I', pixel(frame, y))
        rows.append(bytes((b, g, r, a) if fmt == 'bgra' else (r, g, b, a)) * w)
    return b''.join(rows)


def geometry(vf):
    """What an -vf chain puts out, (w, h, format): pad sets the size, transpose turns it, scale fits what comes in."""
    w = h = 0
    fmt = 'yuv420p'
    for step in vf.split(','):
        name, _, value = step.partition('=')
        if name == 'pad':
            w, h = (int(x) for x in value.split(':')[:2])
        elif name == 'transpose':
            w, h = h, w
        elif name == 'format':
            fmt = value
    return w, h, fmt


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


def plan(begins, total=None, end=None, stall=None, lost=None):
    """The sound a pipe carries, for the sound thread's model (absolute ms): it begins (None: never) and lasts total
    ms of hearing (None: on), the pipe ends, it stalls from-to, the output is lost."""
    return {'begins': begins, 'total': total, 'end': end, 'stall': stall, 'lost': lost}


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


class Media:
    """A video file to the stand-in ffmpeg: seconds long at 30 fps, its sound stream sound seconds (0: none).
    late (k, ms): the k-th frame on are decoded ms after ffmpeg starts; piece: the most a pipe read gets; stall
    (from, to): ms after its sound starts that the output stops taking it; length: whether its report says it."""
    def __init__(self, seconds, sound=None, late=None, piece=0, stall=None, length=True):
        self.seconds, self.sound = seconds, seconds if sound is None else sound
        self.late, self.piece, self.stall, self.length = late, piece, stall, length

    def report(self, url):
        s = self.seconds
        length = f'{s // 3600:02}:{s // 60 % 60:02}:{s % 60:02}.00' if self.length else 'N/A'
        lines = [f"Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '{url}':", '  Metadata:', '    major_brand     : isom',
                 f'  Duration: {length}, start: 0.000000, bitrate: 1200 kb/s',
                 '    Stream #0:0(und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1280x720, 1070 kb/s, 30 fps']
        if self.sound:
            lines.append('    Stream #0:1(und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s')
        return lines + ['At least one output file must be specified']

    def arrives(self, start, k):
        return start + (self.late[1] if self.late and k >= self.late[0] else 0)

    def frames(self, start, first, count, w, h, fmt):
        for k in range(count):
            def make(f=first + k):
                return picture(f, w, h, fmt)
            if not self.piece:
                yield self.arrives(start, k), make
                continue
            whole = make()
            for i in range(0, len(whole), self.piece):
                yield self.arrives(start, k), whole[i:i + self.piece]


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


REFUSED = [(0, '[tcp @ 0x6e4a20] Connection to tcp://radio.example:80 failed: Connection refused'),
           (0, f'{URL}: Connection refused')]


def light(colour):
    """A BGRA pixel's brightness: its blue, green and red together."""
    return sum(colour[:3])


class Bar:
    """A bar over the picture, found by its shape (Q2.look): its filled rows and its empty rows, each part one colour
    of its own, and the panel's columns it takes. The filled part is the brighter; a bar all of one part is filled if
    that colour is brighter than mid-grey."""
    def __init__(self, parts, columns):
        colours = sorted(parts, key=light, reverse=True)
        if len(colours) == 1 and light(colours[0]) <= 3 * 255 // 2:
            colours = [b'', *colours]
        self.filled: list[int] = parts.get(colours[0], [])
        self.empty: list[int] = parts.get(colours[1], []) if len(colours) > 1 else []
        self.columns: set[int] = columns

    def share(self):
        return len(self.filled) / (len(self.filled) + len(self.empty))

    def close_to(self, share):
        """Its filled share is share, to a row."""
        return abs(self.share() - share) <= 1 / (len(self.filled) + len(self.empty))


class Shot:
    """A pan: when, to which page, whether that page was already on the panel (drawn where it shows), the frame
    there (None: not one whole frame), its bar, the sound heard (ms) and the unpaused wall clock since this ffmpeg
    began, and whether every video's ffmpeg was gone (the pan back to demo's page)."""
    def __init__(self, time, page, same, frame, bar, heard, wall, restore):
        self.time, self.page, self.same, self.frame, self.bar = time, page, same, frame, bar
        self.heard, self.wall, self.restore = heard, wall, restore


class Q2:
    """The Q2 around q2video: each call it makes is answered here. The clock (ms) moves only when it polls, sleeps
    or blocks; files, pipes, the framebuffer and its two pages, the DAC, ALSA, demo's datagram keys, and ffmpeg."""
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
        self.streams: dict[int, int] = {}
        self.buffers: list[object] = []
        self.errors: list[str] = []
        self.polls: list[tuple] = []
        self.sleeps: list[tuple] = []
        self.sleep_hook: Callable[[], object] | None = None
        self.env: list[tuple] = []
        self.signals: list[tuple] = []
        self.setenv_ok = self.malloc_ok = self.mmap_ok = self.fb_ok = True
        self.media: dict[str, Media] = {}
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
        self.pipe_sizes: list[tuple] = []
        self.made: list[Pipe] = []  # every pipe q2video made, in order
        self.read_pipes: list[Pipe] = []
        self.var = VarInfo(xres=W, yres=H, xres_virtual=W, yres_virtual=2 * H, bits_per_pixel=32, red=Bitfield(16, 8, 0),
                           green=Bitfield(8, 8, 0), blue=Bitfield(0, 8, 0), transp=Bitfield(24, 8, 0))
        self.fix = FixInfo(id=b'soc_fb', smem_len=2 * H * ROW, line_length=ROW)
        self.fb_fail: set[int] = set()
        self.fb_addr = 0
        self.showing = 0  # the page on the panel: demo's
        self.shots: list[Shot] = []
        self.wall = 0
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
        self.thread_error = 0
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
                 'write': self.write, 'ioctl': self.ioctl, 'mmap': self.mmap, 'malloc': self.malloc,
                 'socket': self.socket, 'bind': self.bind, 'recv': self.recv, 'unlink': self.unlink,
                 'rename': self.rename, 'pipe2': self.pipe2, 'fcntl': self.fcntl, 'fork': self.fork, 'dup2': self.dup2,
                 'execv': self.execv, '_exit': self.exit, 'kill': self.kill, 'waitpid': self.waitpid, 'poll': self.poll,
                 'usleep': self.usleep, 'clock_gettime': self.clock, 'pthread_create': self.thread,
                 'pthread_join': self.join, 'fopen': self.fopen, 'fwrite': self.fwrite, 'fclose': self.fclose,
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
    def frames(self):
        return [s for s in self.shots if not s.restore]

    def video_runs(self):
        return [r for r in self.runs if '0:v:0' in r.argv]

    def place(self):
        return struct.unpack('<i', self.files[PLACE])[0]

    def states(self):
        """Each /tmp/q2video.state demo could read: 1 paused."""
        return [struct.unpack('<i', data)[0] for _, path, data in self.renamed if path == STATE]

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
        if not self.au.paused:
            self.wall += t - self.now
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
        if not self.au.paused and p['begins'] is not None:
            a = max(self.now, p['begins'])
            span = max(0, t - a)
            if p['stall']:
                span -= max(0, min(t, p['stall'][1]) - max(a, p['stall'][0]))
            s['heard'] += span
            if p['total'] is not None:
                s['heard'] = min(s['heard'], p['total'])
            self.au.played = s['heard'] * self.rate // 1000
        if p['lost'] is not None and t >= p['lost']:
            self.au.error, self.au.done = b'write: No such device', 1
        if p['total'] is not None and s['heard'] >= p['total'] or p['end'] is not None and t >= p['end']:
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
        if path == '/dev/fb0':
            obj = 'fb' if self.fb_ok else None
        elif path == '/dev/shanling_dac':
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

    def fopen(self, path, mode, *_):
        fd = self.open(path, O_WRONLY | O_CREAT | O_TRUNC if b'w' in C.string_at(mode) else 0)
        if fd < 0:
            return 0
        self.streams[0x5100 + fd] = fd
        return 0x5100 + fd

    def fwrite(self, data, size, count, stream, *_):
        return count if self.write(self.streams[stream], data, size * count) == size * count else 0

    def fclose(self, stream, *_):
        return self.close(self.streams.pop(stream))

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

    def malloc(self, n, *_):
        if not self.malloc_ok:
            return 0
        block = C.create_string_buffer(n)
        self.buffers.append(block)
        return C.addressof(block)

    def mmap(self, addr, length, prot, flags, fd, offset):
        obj = self.fds.get(i32(fd))
        if obj == 'fb' and self.mmap_ok and length <= self.fix.smem_len:
            mem = C.create_string_buffer(length)
            C.memset(mem, 0x55, length)  # demo's picture
            self.buffers.append(mem)
            self.fb_addr = C.addressof(mem)
            return self.fb_addr
        if isinstance(obj, OpenFile) and length == 4:
            if obj.path not in self.maps:
                self.maps[obj.path] = C.c_int(int.from_bytes(self.files[obj.path][:4].ljust(4, b'\0'), 'little', signed=True))
            return C.addressof(self.maps[obj.path])
        return -1

    def ioctl(self, fd, request, arg, *_):
        obj, request = self.fds.get(i32(fd)), request & 0xffffffff
        if obj == 'dac':
            self.dac.append((self.now, request, C.c_int.from_address(arg).value))
            return 0
        if obj != 'fb' or request in self.fb_fail:
            return -1
        if request == FBIOGET_VSCREENINFO:
            C.memmove(arg, C.addressof(self.var), C.sizeof(VarInfo))
        elif request == FBIOGET_FSCREENINFO:
            C.memmove(arg, C.addressof(self.fix), C.sizeof(FixInfo))
        elif request == FBIOPAN_DISPLAY:
            self.pan(VarInfo.from_address(arg).yoffset)
        else:
            return -1
        return 0

    def pan(self, page):
        frame, bar = self.look(page)
        heard = self.sound['heard'] if self.sound and self.sound['plan'] else None
        restore = all(r.reaped is not None for r in self.video_runs())
        self.shots.append(Shot(self.now, page, page == self.showing, frame, bar, heard, self.wall, restore))
        self.showing = page

    def look(self, page):
        """What page shows: its frame's number (None: not one whole frame, or demo's padding touched) and its bar, by
        its shape alone: rows that are the picture but for one span of columns in a colour of its own, two colours
        at most over all the rows (Bar)."""
        line = self.fix.line_length
        raw = memoryview(C.string_at(self.fb_addr + page * line, H * line))
        frame = int.from_bytes(raw[:4], 'little') & 0xffff
        picture_ = memoryview(picture(frame, W, H, 'bgra'))
        pad = b'\x55' * (line - ROW)
        if line == ROW and raw == picture_:
            return frame, None
        parts: dict[bytes, list[int]] = {}  # the bar's rows by their colour
        columns: set[int] = set()
        whole, x, k = True, 0, 0  # x, k: the span as the last bar row had it, which a bar's rows share
        for y in range(H):
            row, expect = raw[y * line:y * line + ROW], picture_[y * ROW:(y + 1) * ROW]
            if raw[y * line + ROW:(y + 1) * line] != pad or row[:4] != expect[:4]:
                whole = False
                continue
            if row == expect:
                continue
            if not k or row[:4 * x] != expect[:4 * x] or row[4 * (x + k):] != expect[4 * (x + k):]:  # measure it
                px, want = row.cast('I'), expect.cast('I')
                off = [i for i in range(W) if px[i] != want[i]]
                x, k = off[0], off[-1] + 1 - off[0]
            colour = bytes(row[4 * x:4 * x + 4])
            if row[4 * x:4 * (x + k)] != colour * k:  # not one colour from end to end: no bar's
                whole, k = False, 0
                continue
            parts.setdefault(colour, []).append(y)
            columns.update(range(x, x + k))
        if len(parts) > 2:  # more than a filled part and an empty one
            whole, parts = False, {}
        return (frame if whole else None), (Bar(parts, columns) if parts else None)

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
        self.made.append(pipe)
        pair[0] = self.alloc(End(pipe, False), cloexec)
        pair[1] = self.alloc(End(pipe, True), cloexec)
        return 0

    def fcntl(self, fd, cmd, arg, *_):
        fd, cmd, arg = i32(fd), i32(cmd), i32(arg)
        if cmd == F_SETFD:
            table = self.child_cloexec if self.in_child else self.cloexec
            if arg & FD_CLOEXEC:
                table.add(fd)
            else:
                table.discard(fd)
            return 0
        obj = self.fds.get(fd)
        if cmd == F_SETPIPE_SZ and isinstance(obj, End):
            self.pipe_sizes.append((obj.pipe, arg))
            return arg
        return -1

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
        self.polls.append((self.now, timeout))
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
        if self.sleep_hook:
            self.sleep_hook()
        return 0

    def clock(self, which, ts, *_):
        if i32(which) != 1:
            self.errors.append('a clock that is not CLOCK_MONOTONIC')
        pair = (C.c_long * 2).from_address(ts)
        pair[0], pair[1] = self.now // 1000, self.now % 1000 * 1_000_000
        return 0

    def thread(self, thread, attr, start, arg, *_):
        if self.thread_error:
            return self.thread_error
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
        url, report = run.opt('-i'), run.writes(2)
        lines = [] if '-hide_banner' in run.argv else [(0, x) for x in BANNER]
        if run.opt('-icy') == '1':
            lines += self.station(run, url)
        elif url not in self.media:
            lines.append((0, f'{url}: No such file or directory'))
        elif not outputs(run.argv):
            lines += [(0, x) for x in self.media[url].report(url)]
        else:
            self.decode(run, self.media[url])
        if report is not None and run.opt('-loglevel') != 'quiet':
            report.feed(run, sorted((run.start + t, (x + '\n').encode()) for t, x in lines))

    def decode(self, run, media):
        outs = [(run.writes(n), opts) for n, opts in outputs(run.argv)]
        self.wall = 0
        if not all(pipe for pipe, _ in outs) or any(o.get('-map') == '0:a:0' and not media.sound for _, o in outs):
            return  # a bad descriptor, or no stream for a map: it fails before writing anything
        ss = int(run.opt('-ss') or 0)
        for pipe, opts in outs:
            assert pipe is not None
            if opts.get('-map') == '0:v:0':
                w, h, fmt = geometry(opts.get('-vf', ''))
                rate = int(opts.get('-r', 30))
                count = max(0, (media.seconds - ss) * rate)
                pipe.feed(run, media.frames(run.start, ss * rate, count, w, h, fmt))
                run.end = media.arrives(run.start, count - 1) if count else run.start
            elif opts.get('-map') == '0:a:0':
                stall = media.stall and (run.start + media.stall[0], run.start + media.stall[1])
                pipe.feed(run, ())
                pipe.plan = plan(run.start, max(0, media.sound - ss) * 1000, stall=stall)

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


def play(q2, device=BT, volume='60', seconds=1, at=None, **media):
    """q2video DEVICE CLIP [VOLUME], as books.c video_start runs it: CLIP seconds long, from its second at."""
    q2.media[CLIP] = Media(seconds, **media)
    if at is not None:
        q2.files[PLACE] = struct.pack('<i', at)
    return q2.run(device, CLIP, *([volume] if volume is not None else []))


def radio(q2, device=JACK, *volume):
    """q2video -r DEVICE URL [VOLUME], as radio.c starts it."""
    return q2.run('-r', device, URL, *volume)


def sink(q2, device=BT, volume='h50'):
    """q2video -s DEVICE VOLUME, librespot's sink as spotify.c starts it."""
    return q2.run('-s', device, volume)


def on_time(shots, clock):
    """Each frame shown within its 1/25 s of clock (heard or wall): from n/25 s, and less than a whole frame late."""
    return all(s.frame * FRAME_MS <= getattr(s, clock) < (s.frame + 1) * FRAME_MS for s in shots)


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


def test_alsa_s_lock_goes_and_demo_s_descriptors_close_before_anything_opens(q2):
    assert play(q2) == 0
    calls = q2.v.calls()
    names = [name for name, _ in calls]
    first = min(names.index(n) for n in ('fw_open', 'fw_socket', 'fw_pipe2', 'fw_snd_pcm_open'))
    assert names.index('fw_setenv') < first and q2.env == [('LIBASOUND_THREAD_SAFE', '0', 1)]
    closed = {i32(args[0]) for name, args in calls[:first] if name == 'fw_close'}
    assert set(DEMO_FDS) <= closed and not closed & {0, 1, 2}  # demo's go; stdio stays, stdin being Spotify's sound
    assert names.index('fw_signal') < names.index('fw_fork') and q2.signals == [(SIGCHLD, 0)]  # SIG_DFL: waitpid sees ffmpeg


def test_if_alsa_s_lock_can_t_be_turned_off_it_plays_nothing(q2):
    q2.setenv_ok = False
    assert play(q2) == 1
    assert not q2.runs and not q2.pcm_log and not q2.shots


SCREENS = {
    'wider': lambda q2: setattr(q2.var, 'xres', W + 1),
    'taller': lambda q2: setattr(q2.var, 'yres', H + 1),
    'one page': lambda q2: setattr(q2.var, 'yres_virtual', H),
    '16 bpp': lambda q2: setattr(q2.var, 'bits_per_pixel', 16),
    'red at 0': lambda q2: setattr(q2.var.red, 'offset', 0),
    'line shorter than a row': lambda q2: setattr(q2.fix, 'line_length', ROW - 4),
    'memory short of two pages': lambda q2: setattr(q2.fix, 'smem_len', 2 * H * ROW - 1),
    'no fb0': lambda q2: setattr(q2, 'fb_ok', False),
    'no var info': lambda q2: q2.fb_fail.add(FBIOGET_VSCREENINFO),
    'no fix info': lambda q2: q2.fb_fail.add(FBIOGET_FSCREENINFO),
    'no mapping': lambda q2: setattr(q2, 'mmap_ok', False),
    'no memory': lambda q2: setattr(q2, 'malloc_ok', False),
}


@pytest.mark.parametrize('change', SCREENS)
def test_a_screen_unlike_the_q2_s_is_left_alone(q2, change):
    SCREENS[change](q2)
    assert play(q2) == 1
    assert not q2.shots and not q2.runs and not q2.sockets and not q2.pcm_log


# Playing
@pytest.mark.parametrize('demo_page', [0, H])
def test_every_frame_shows_once_on_the_sound_s_clock_drawn_out_of_sight(q2, demo_page):
    q2.var.yoffset = q2.showing = demo_page
    assert play(q2) == 0
    frames = q2.frames()
    assert [s.frame for s in frames] == list(range(25))  # 1 s at ffmpeg's -r 25, in order
    assert on_time(frames, 'heard')
    assert frames[0].page != demo_page and not any(s.same for s in frames)  # each drawn hidden, then panned to
    assert q2.shots[-1].restore and q2.showing == demo_page  # demo's page back at the end


def test_an_end_cleans_up_after_itself(q2):
    assert play(q2) == 0
    probe, video = q2.runs
    assert q2.place() == 0  # played to its end
    assert all(r.killed is not None and r.reaped is not None and r.killed <= r.reaped for r in q2.runs)
    assert probe.reaped <= q2.frames()[1].time  # read for the length, then gone
    assert q2.events('drain', 'drop', 'prepare', 'close') == ['drain', 'drop', 'prepare', 'close']  # the end plays out
    assert SOCK not in q2.files and STATE not in q2.files and q2.states() == [0]
    assert not [fd for fd, obj in q2.fds.items() if isinstance(obj, End)]


def test_each_ffmpeg_keeps_its_stdio_and_its_pipes_and_nothing_else(q2):
    assert play(q2) == 0
    probe, video = q2.runs
    assert probe.path == video.path == '/usr/bin/ffmpeg' and all('-nostdin' in r.argv for r in q2.runs)
    assert probe.opt('-i') == video.opt('-i') == CLIP and not outputs(probe.argv)
    assert set(probe.fds) == {0, 1, 2} and probe.writes(2) in q2.read_pipes  # its report: the length
    assert set(video.fds) == {0, 1, 2, 3, 4} and video.fds[2] == 'stderr'
    assert video.writes(3) in q2.read_pipes and video.writes(4) is q2.thread_pipes[0]  # pictures here, sound there
    ((resized, size),) = q2.pipe_sizes  # frames ahead, so the sound paces ffmpeg: room for more than the one being read
    assert resized is video.writes(3) and size >= 2 * ROW * H


def test_the_picture_waits_while_the_sound_stalls(q2):
    assert play(q2, seconds=2, stall=(400, 900)) == 0
    frames = q2.frames()
    assert [s.frame for s in frames] == list(range(50)) and on_time(frames, 'heard')
    assert frames[11].time - frames[10].time >= 500  # the wall clock went on; the picture held


@pytest.mark.parametrize('arrival', [700, 720])  # mid-frame, and a frame's end exactly
def test_frames_decoded_a_whole_frame_late_are_dropped_not_shown_late(q2, arrival):
    assert play(q2, seconds=2, late=(10, arrival)) == 0
    late = [k for k in range(10, 50) if (k + 1) * FRAME_MS <= arrival]
    assert late and [s.frame for s in q2.frames()] == [k for k in range(50) if k not in late]
    assert on_time(q2.frames(), 'heard')


def test_a_frame_that_comes_in_pieces_shows_whole(q2):
    assert play(q2, piece=99991) == 0
    assert [s.frame for s in q2.frames()] == list(range(25))


def test_a_padded_line_takes_each_row_and_keeps_its_padding(q2):
    q2.fix.line_length, q2.fix.smem_len = ROW + 20, 2 * H * (ROW + 20)
    assert play(q2) == 0
    assert [s.frame for s in q2.frames()] == list(range(25))


def test_after_the_sound_ends_the_picture_goes_on_by_the_clock_from_where_the_sound_left_it(q2):
    assert play(q2, seconds=2, sound=1, stall=(200, 700)) == 0  # the sound half a second behind the wall clock
    frames = q2.frames()
    assert [s.frame for s in frames] == list(range(50))  # none dropped at the change of clocks
    assert all(FRAME_MS - 1 <= b.time - a.time <= FRAME_MS + 1 for a, b in zip(frames[25:], frames[26:]))


def test_a_video_without_sound_plays_again_silent_on_the_clock(q2):
    assert play(q2, sound=0) == 0
    first, second = q2.video_runs()
    assert first.opt('-ss') == second.opt('-ss') == '0'
    assert [n for n, _ in outputs(first.argv)] == [3, 4] and [n for n, _ in outputs(second.argv)] == [3]
    frames = q2.frames()
    assert [s.frame for s in frames] == list(range(25)) and on_time(frames, 'wall')
    assert len(q2.threads) == 1


def test_no_output_plays_silent_from_the_start(q2):
    assert play(q2, '-', None) == 0
    (video,) = q2.video_runs()
    assert not q2.pcm_log and not q2.threads and [n for n, _ in outputs(video.argv)] == [3]
    assert [s.frame for s in q2.frames()] == list(range(25)) and on_time(q2.frames(), 'wall')


def test_a_refused_fork_ends_the_video_cleanly(q2):
    q2.forks = [None, -1, -1]  # the probe, then the video with sound and without
    assert play(q2) == 0
    assert not q2.shots and q2.place() == 0 and len(q2.v.calls('fw_fork')) == 3
    (probe,) = q2.runs
    videos = [p for p in q2.made if p is not probe.writes(2)]  # with sound, then without
    assert len(videos) == 3 and not [o for o in q2.fds.values() if isinstance(o, End) and o.pipe in videos]


def test_a_child_whose_ffmpeg_won_t_start_exits_127(q2):
    q2.forks = [0]  # q2video itself is the probe's child
    assert play(q2) == 127 and q2.exits == [127]
    (child,) = q2.execs
    assert child['path'] == child['argv'][0] == '/usr/bin/ffmpeg' and child['argv'][-2:] == ['-i', CLIP]
    assert set(child['fds']) == {0, 1, 2}
    report = q2.fds[child['fds'][2]]
    assert isinstance(report, End) and report.write
    assert any(isinstance(o, End) and not o.write and o.pipe is report.pipe for o in q2.fds.values())


def test_ffmpeg_starts_at_the_place_demo_left_its_frames_on_3_and_sound_on_4(q2):
    q2.press(b'q', 300)
    assert play(q2, seconds=600, at=42) == 0
    (video,) = q2.video_runs()
    assert video.opt('-ss') == '42' and video.opt('-i') == CLIP
    assert [(n, o['-f']) for n, o in outputs(video.argv)] == [(3, 'rawvideo'), (4, 's16le')]
    assert q2.frames()[0].frame == 42 * 25


def test_a_stale_socket_left_by_an_earlier_run_still_takes_keys(q2):
    q2.files[SOCK] = b''
    q2.press(b'q', 300)
    assert play(q2, seconds=600, at=100) == 0
    assert q2.place() == 100 and SOCK in [p for _, p in q2.unlinked]


# Keys
def test_pause_holds_picture_and_sound_and_tells_demo(q2):
    q2.press(b'p', 500)
    q2.press(b'p', 2500)
    assert play(q2, seconds=2) == 0
    frames = q2.frames()
    assert [s.frame for s in frames] == list(range(50)) and on_time(frames, 'heard')
    assert not [s for s in frames if q2.t0 + 500 < s.time < q2.t0 + 2500]
    assert q2.states() == [0, 1, 0]


def test_the_ears_pause_and_resume_whatever_it_was(q2):
    for at, key in ((300, b'a'), (500, b'a'), (1500, b'r'), (1700, b'r')):
        q2.press(key, at)
    assert play(q2, seconds=2) == 0
    assert q2.states() == [0, 1, 1, 0, 0]
    assert not [s for s in q2.frames() if q2.t0 + 300 < s.time < q2.t0 + 1500]
    assert [s.frame for s in q2.frames()] == list(range(50))


@pytest.mark.xfail(strict=True, reason='BUG: a paused, still picture should wait on its keys up to 1 s (cap 1000, '
                   '"paused and still: wait on a key"), but wait starts at 100 and the poll takes the smaller, so it '
                   'wakes ten times a second for its whole pause')
def test_a_paused_still_picture_waits_on_its_keys(q2):
    q2.press(b'p', 500)
    q2.press(b'q', 5500)
    assert play(q2, seconds=10) == 0
    assert len([t for t, _ in q2.polls if q2.t0 + 600 <= t < q2.t0 + 5400]) <= 6


@pytest.mark.parametrize('device, volume, gain', [(BT, '60', curve(75)), (USB, 'h60', 65536)])
def test_a_volume_shows_on_a_bar_for_1_5_s_and_sets_a_soft_gain(q2, device, volume, gain):
    q2.press(b'v' + bytes([75]), 300)
    assert play(q2, device, volume, seconds=2) == 0
    assert q2.au.gain == gain  # Bluetooth's soft volume; a USB DAC with a volume of its own sets it itself
    frames = q2.frames()
    barred = [s for s in frames if s.bar]
    assert all(s.bar for s in frames if q2.t0 + 300 <= s.time < q2.t0 + 1800 - POLL_MS)
    assert barred and all(q2.t0 + 300 <= s.time < q2.t0 + 1800 for s in barred)  # 1.5 s
    for s in barred:
        assert s.bar.close_to(0.75)
        assert max(s.bar.filled) < min(s.bar.empty)  # from the picture's left, turned clockwise onto the panel's top
        assert max(s.bar.columns) < W // 2  # along the picture's bottom: the panel's left side


def test_the_headset_s_gain_sets_the_sound_without_a_bar(q2):
    q2.press(b'g' + bytes([30]), 500)
    assert play(q2) == 0
    assert q2.au.gain == curve(30) and not [s for s in q2.frames() if s.bar]


def test_on_the_jack_the_dac_keeps_the_volume_and_is_readied_then_muted(q2):
    q2.press(b'v' + bytes([20]), 300)
    assert play(q2, JACK, None) == 0
    assert q2.au.gain == 65536 and [s for s in q2.frames() if s.bar]
    assert [(r, v) for _, r, v in q2.dac] == [(DAC_PCM, 0), (DAC_MUTE, 0), (DAC_MUTE, 1)]
    assert q2.dac[1][0] <= q2.frames()[0].time and q2.dac[2][0] >= q2.frames()[-1].time


def test_bluetooth_leaves_the_dac_alone(q2):
    assert play(q2, BT, '60') == 0
    assert not q2.dac and q2.au.gain == curve(60)


def test_the_position_bar_shows_where_the_picture_is_of_its_length(q2):
    q2.press(b's', 500)
    q2.press(b'q', 1000)
    assert play(q2, seconds=120, at=60) == 0
    barred = [s for s in q2.frames() if s.bar]
    assert barred and all(s.bar.close_to(s.frame // 25 / 120) for s in barred)  # start plus frames shown
    assert q2.place() == 60 + len(q2.frames()) // 25


def test_without_a_length_there_is_no_position_bar_but_a_volume_bar(q2):
    q2.press(b's', 200)
    q2.press(b'v' + bytes([50]), 600)
    assert play(q2, length=False) == 0
    assert not [s for s in q2.frames() if s.bar and s.time < q2.t0 + 600]
    assert [s for s in q2.frames() if s.bar and s.time >= q2.t0 + 600]


def test_a_run_of_seek_ticks_restarts_ffmpeg_once_at_their_sum(q2):
    for at in (1000, 1100, 1200):
        q2.press(b'f', at)
    q2.press(b'q', 1800)
    assert play(q2, seconds=60) == 0
    first, second = q2.video_runs()
    frames = q2.frames()
    jump = next(k for k in range(1, len(frames)) if frames[k].frame != frames[k - 1].frame + 1)
    before = frames[:jump]  # the first ffmpeg's, from 0
    assert q2.t0 + 1200 + 400 - POLL_MS <= second.start <= q2.t0 + 1200 + 400  # once, 400 ms after the last tick
    assert int(second.opt('-ss')) == len(before) // 25 + 3 * 10  # where it was, 10 s a tick on
    assert first.reaped is not None and first.reaped <= second.start
    assert frames[jump].frame == int(second.opt('-ss')) * 25
    pending = [s for s in before if s.time >= q2.t0 + 1200]
    assert pending and all(s.bar and s.bar.close_to((30 + s.frame // 25) / 60) for s in pending)
    assert q2.events('drain', 'drop', 'prepare') == ['drop', 'prepare'] * 2  # the old place's sound dropped


def test_a_seek_back_past_the_start_starts_at_the_start(q2):
    q2.press(b'b', 200)
    q2.press(b'q', 1000)
    assert play(q2, seconds=60, at=5) == 0
    assert [r.opt('-ss') for r in q2.video_runs()] == ['5', '0']


def test_a_seek_while_paused_stays_paused_on_its_first_frame(q2):
    q2.press(b'p', 500)
    q2.press(b'f', 700)
    q2.press(b'p', 3000)
    q2.press(b'q', 3500)
    assert play(q2, seconds=30) == 0
    first, second = q2.video_runs()
    assert second.opt('-ss') == '10'
    held = [s for s in q2.frames() if second.start <= s.time < q2.t0 + 3000]
    assert held[0].frame == 250 and all(s.heard == 0 for s in held)  # its first frame, nothing heard
    assert [s for s in q2.frames() if s.time > q2.t0 + 3000]  # and on after the resume
    assert q2.states() == [0, 1, 0] and 'drain' not in q2.events('drain')


def test_a_volume_while_paused_shows_its_bar_and_takes_it_away(q2):
    q2.press(b'p', 500)
    q2.press(b'v' + bytes([40]), 800)
    q2.press(b'q', 4000)
    assert play(q2, seconds=10) == 0
    paused = [s for s in q2.frames() if s.time > q2.t0 + 500]
    assert [bool(s.bar) for s in paused] == [True, False]
    assert paused[0].bar.close_to(0.4)
    assert q2.t0 + 800 + 1500 - POLL_MS <= paused[1].time <= q2.t0 + 800 + 1500 + POLL_MS  # 1.5 s, by its next wake


def test_a_sound_thread_that_won_t_start_plays_it_again_silent(q2):
    q2.thread_error = 11  # EAGAIN
    assert play(q2) == 0
    first, second = q2.video_runs()
    assert [n for n, _ in outputs(second.argv)] == [3]  # the first lost its sound's reader and ended
    assert [s.frame for s in q2.frames()] == list(range(25)) and on_time(q2.frames(), 'wall')
    assert not q2.joins and 'drain' not in q2.events('drain')


@pytest.mark.parametrize('seconds, at, length, keys, place', [
    (600, 100, True, (), 100),  # where it was
    (600, 10, True, (), 0),  # within 30 s of the start
    (600, 575, True, (), 0),  # within 30 s of the end
    (6000, 5000, False, (), 5000),  # no length known: kept
    (600, 100, True, (b'f',), 110),  # a seek still pending counts
])
def test_a_quit_leaves_the_place_to_resume_from(q2, seconds, at, length, keys, place):
    for key in keys:
        q2.press(key, 300)
    q2.press(b'q', 500)
    assert play(q2, seconds=seconds, at=at, length=length) == 0
    assert q2.place() == place
    assert 'drain' not in q2.events('drain')  # a quit drops the sound left


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


@pytest.mark.parametrize('mode', ['video', 'radio', 'sink'])
def test_the_pcm_is_s16_interleaved_stereo_at_the_sound_s_rate(q2, mode):
    q2.buffer = 9600
    if mode == 'video':
        play(q2)
    elif mode == 'radio':
        q2.stations[URL] = [Station(icy(), sound=300)]
        q2.press(b'q', 1000)
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


@pytest.mark.parametrize('volume, gain', [('60', curve(80)), ('h60', 65536)])
def test_a_volume_key_sets_the_station_s_soft_gain(q2, volume, gain):
    q2.stations[URL] = [Station(icy(), sound=300)]
    q2.press(b'v' + bytes([80]), 1000)
    q2.press(b'q', 1500)
    assert radio(q2, BT, volume) == 0
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
def writer(q2, data, video=0, gain=65536):
    q2.stdin(data)
    q2.au.fd, q2.au.pcm, q2.au.gain, q2.au.video = 0, 0x6600, gain, video
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


def test_a_video_s_failed_write_is_dropped_and_its_sound_read_on(q2):
    sound = pcm(1536)
    q2.write_answers = [-ENODEV, 512, 512]
    assert writer(q2, sound.tobytes(), video=1) == 0
    assert q2.written == sound[1024:] and not q2.au.error and q2.au.done


@pytest.mark.parametrize('delay, error, avail, heard', [
    (300, 0, 9600, 1536 - 300), (0, 0, 9600, 1536), (0, -EPIPE, 9600, 1536), (-64, 0, 9600, 1536),
    (2000, 0, 9600, 0),  # more than was written (Bluetooth adds the link's own delay): nothing heard yet
    (300, 0, 9600 - 1536, 0),  # after a seek's drop bluealsa answers the link alone; the ring still holds it all
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


def test_a_paused_writer_holds_its_block_until_resumed(q2):
    sound, seen = pcm(1024), []
    q2.au.paused = 1
    def sleep():
        seen.append(len(q2.written))
        if len(seen) == 3:
            q2.au.paused = 0
    q2.sleep_hook = sleep
    writer(q2, sound.tobytes())
    assert seen == [0, 0, 0] and q2.written == sound  # nothing written through three sleeps, all of it once resumed
    assert all(0 < us <= 100_000 for _, us in q2.sleeps)  # it sleeps, not spins, waking at 10 Hz at least (aed1488)


def test_a_short_last_block_plays_and_half_a_frame_does_not(q2):
    sound = pcm(700)
    writer(q2, sound.tobytes() + b'\x01\x02')
    assert q2.written == sound


def test_the_gain_scales_every_sample_down_and_never_wraps(q2):
    sound = pcm(1024)
    writer(q2, sound.tobytes(), gain=curve(99))
    assert q2.written == array('h', [s * curve(99) >> 16 for s in sound])
