"""q2remote's commands as they leave for the Q2, with no Q2: a fake shell() or socket answers them."""
import bisect
import contextlib
import hashlib
import io
import pathlib
import shlex
import socket
import struct
import subprocess
import sys
import tarfile
import time
import types
import zlib

import pytest

import q2remote as q


class Shell:
    """Stands in for shell(): records each call and plays back replies, raising the exceptions."""
    def __init__(self, *replies):
        self.calls, self.replies = [], list(replies)

    def __call__(self, cmd, data=b'', timeout=q.TIMEOUT, host=None, sink=None, done=None):
        self.calls.append(types.SimpleNamespace(cmd=cmd, data=data, timeout=timeout, host=host, sink=sink, done=done))
        reply = self.replies.pop(0) if self.replies else b''
        if isinstance(reply, BaseException):
            raise reply
        return reply


@pytest.fixture
def shell(monkeypatch):
    def install(*replies):
        fake = Shell(*replies)
        monkeypatch.setattr(q, 'shell', fake)
        return fake
    return install


class Sock:
    address: tuple[str, int] | None = None  # what create_connection was given
    connect_timeout: float | None = None

    def __init__(self, *chunks):
        self.chunks, self.sent, self.timeouts, self.how, self.asked = list(chunks), b'', [], None, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def sendall(self, data):
        self.sent += data

    def shutdown(self, how):
        self.how = how

    def recv(self, size):
        self.asked.append(size)
        chunk = self.chunks.pop(0) if self.chunks else b''
        if isinstance(chunk, Exception):
            raise chunk
        return chunk


@pytest.fixture
def connect(monkeypatch):
    monkeypatch.setenv('Q2_KEY', 'k')
    def install(*chunks):
        sock = Sock(*chunks)
        def create(address, timeout):
            sock.address, sock.connect_timeout = address, timeout
            return sock
        monkeypatch.setattr(socket, 'create_connection', create)
        return sock
    return install


def test_load_key_environment_wins(monkeypatch, tmp_path):
    (tmp_path/'k').write_text('from-file\n')
    monkeypatch.setenv('Q2_KEY_FILE', str(tmp_path/'k'))
    assert q.load_key() == 'from-file'
    monkeypatch.setenv('Q2_KEY', 'from-env')
    assert q.load_key() == 'from-env'


def test_load_key_default_file(monkeypatch, tmp_path):
    (tmp_path/'k').write_text('default\n')
    monkeypatch.setattr(q, 'KEY_FILE', tmp_path/'k')
    assert q.load_key() == 'default'
    monkeypatch.setattr(q, 'KEY_FILE', tmp_path/'absent')
    with pytest.raises(ValueError, match=r'^set Q2_KEY or Q2_KEY_FILE, or put q2remote.key in the repo root$'):
        q.load_key()


def test_shell_sends_key_command_stdin(connect):
    s = connect(b'OK\nou', b't')
    assert q.shell('cmd', b'in', 7, 'host') == b'out'
    assert s.sent == b'k\ncmd\nin' and s.address == ('host', q.PORT) and s.connect_timeout == 7
    assert s.timeouts == [7] and s.how == socket.SHUT_WR and set(s.asked) == {65536}


def test_shell_host_from_the_environment(connect, monkeypatch):
    s = connect(b'OK\n')
    monkeypatch.setenv('Q2_HOST', 'env-host')
    assert q.shell('x', timeout=None) == b''
    assert s.address[0] == 'env-host' and s.connect_timeout == q.TIMEOUT and s.timeouts == [None] and s.sent == b'k\nx\n'


def test_shell_needs_a_host(monkeypatch):
    with pytest.raises(ValueError, match=r'^set Q2_HOST \(`find` lists the Q2s on this network\)$'):
        q.shell('x')


def test_shell_sink_streams_past_the_ok(connect):
    connect(b'O', b'K\nab', b'cd')
    class Sink(io.BytesIO):
        flushes = 0

        def flush(self):
            self.flushes += 1
    sink = Sink()
    assert q.shell('x', host='h', sink=sink) == b''
    assert sink.getvalue() == b'abcd' and sink.flushes == 2


def test_shell_sink_stops_at_a_refusal(connect):
    s = connect(b'ERR', b'never')
    sink = io.BytesIO()
    with pytest.raises(PermissionError, match=r'^authentication rejected \(check Q2_KEY and /mnt/mmc/q2remote.key\)$'):
        q.shell('x', host='h', sink=sink)
    assert sink.getvalue() == b'' and s.chunks == [b'never']


def test_shell_done_ends_the_read(connect):
    s = connect(b'OK\nab', b'cd', b'never')
    seen = []
    def done(reply):
        seen.append(bytes(reply))
        return reply.endswith(b'cd')
    assert q.shell('x', host='h', done=done) == b'abcd'
    assert seen == [b'OK\nab', b'OK\nabcd'] and s.chunks == [b'never']


def test_shell_timeout(connect):
    connect(socket.timeout('late'))
    with pytest.raises(TimeoutError, match=r'^no answer from the Q2 for 1.5 s$'):
        q.shell('x', timeout=1.5, host='h')
    connect(socket.timeout('late'))
    with pytest.raises(TimeoutError, match=r'^no answer from the Q2 for 30 s$'):
        q.shell('x', timeout=None, host='h')


def test_shell_unreachable(connect):
    connect(OSError('boom'))
    with pytest.raises(ConnectionError, match=r'^cannot reach Q2 at h:2302: boom$'):
        q.shell('x', host='h')


def test_shell_no_reply(connect):
    connect()
    with pytest.raises(ConnectionError, match=r'^no reply from q2remote$'):
        q.shell('x', host='h')


def test_shell_wrong_key(connect):
    connect(b'ERR\n')
    with pytest.raises(PermissionError):
        q.shell('x', host='h')


PID = '/tmp/q2remote-00000004.pid'


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(q.secrets, 'token_hex', lambda n: f'{n:08x}')


def test_run_wraps_the_script(shell, token):
    fake = shell(b'hello\n\0q2rc=3\n')
    assert q.run('echo hi', 7) == (b'hello', 3)
    call = fake.calls[0]
    assert call.data == b'echo hi' and call.timeout == 7
    exec_, setsid, sh, flag, inner = shlex.split(call.cmd)
    assert (exec_, setsid, sh, flag) == ('exec', 'setsid', 'sh', '-c')
    assert inner == (f'q2remote_script=$(cat); echo $$ >{PID}; (eval "$q2remote_script") </dev/null; r=$?; rm -f {PID}; '
                     'printf "\\n\\0q2rc=%d\\n" $r')
    assert call.done(b'x\n\0q2rc=1\n') and not call.done(b'x')


def test_run_default_timeout(shell, token):
    fake = shell(b'\n\0q2rc=0\n')
    assert q.run('x') == (b'', 0)
    assert fake.calls[0].timeout == q.TIMEOUT


def test_run_interrupt_kills_the_group(shell, token):
    fake = shell(KeyboardInterrupt(), OSError('gone'))
    with pytest.raises(KeyboardInterrupt):
        q.run('x')
    assert fake.calls[1].cmd == f'p=$(cat {PID} 2>/dev/null) && kill -- -$p; rm -f {PID}' and fake.calls[1].timeout == 5


def test_run_timeout_names_the_group(shell, token):
    fake = shell(TimeoutError('slow'), b'123\n')
    with pytest.raises(TimeoutError) as e:
        q.run('x')
    assert str(e.value) == "slow; still running as process group 123 (`sh 'kill -- -123'` stops it)"
    assert fake.calls[1].cmd == f'cat {PID} 2>/dev/null' and fake.calls[1].timeout == 5


@pytest.mark.parametrize('reply', [b'', b'\n', OSError('gone')])
def test_run_timeout_without_a_group(shell, token, reply):
    shell(TimeoutError('slow'), reply)
    with pytest.raises(TimeoutError, match=r'^slow$'):
        q.run('x')


def test_run_lost_connection(shell, token):
    shell(b'partial')
    with pytest.raises(ConnectionError, match=r'^the Q2 closed the connection before the command finished$'):
        q.run('x')


def test_parse_devices_skips_incomplete_blocks():
    text = ('N: Name="no handlers"\n\nH: Handlers=event8\n\nN: Name="mouse"\nH: Handlers=mouse0\n \n'
            'N: Name="hyn_sliderbar"\nH: Handlers=event3\n')
    assert q.parse_devices(text) == [{'name': 'hyn_sliderbar', 'event': 3, 'handlers': 'event3', 'KEY': 0, 'ABS': 0}]


def test_touch_xy_rotates_into_the_panel():
    assert q.touch_xy(0, 0) == (319, 0)
    assert q.touch_xy(374, 319) == (0, 374)
    assert q.touch_xy(10, 20) == (299, 10)


@pytest.mark.parametrize('x,y', [(375, 0), (0, 320), (-1, 0), (0, -1)])
def test_touch_xy_off_screen(x, y):
    with pytest.raises(ValueError, match=r'^coordinates must be x=0..374, y=0..319$'):
        q.touch_xy(x, y)


def events(*triples):
    return b''.join(q.event(*t) for t in triples)


def test_event():
    """struct input_event on the Q2's 32-bit kernel: no timestamp, then u16 type and code and an s32 value."""
    assert q.event(q.EV_ABS, 0xffff, -1) == bytes(8) + b'\x03\x00\xff\xff\xff\xff\xff\xff'
    assert q.event(q.EV_KEY, q.BTN_TOUCH, 1) == bytes(8) + b'\x01\x00\x4a\x01\x01\x00\x00\x00'


def test_key_events():
    assert q.key_events(116) == [events((q.EV_KEY, 116, 1), (q.EV_SYN, 0, 0)), events((q.EV_KEY, 116, 0), (q.EV_SYN, 0, 0))]


def test_touch_batches():
    press, move, lift = q.touch_batches([(0, 0), (10, 20)])
    assert press == events((q.EV_ABS, q.ABS_X, 319), (q.EV_ABS, q.ABS_Y, 0), (q.EV_ABS, q.ABS_PRESSURE, 1),
                           (q.EV_KEY, q.BTN_TOUCH, 1), (q.EV_SYN, 0, 0))
    assert move == events((q.EV_ABS, q.ABS_X, 299), (q.EV_ABS, q.ABS_Y, 10), (q.EV_SYN, 0, 0))
    assert lift == events((q.EV_ABS, q.ABS_PRESSURE, 0), (q.EV_KEY, q.BTN_TOUCH, 0), (q.EV_SYN, 0, 0))


def test_swipe_points():
    assert q.swipe_points(0, 10, 100, 0, 100) == [(0, 10), (20, 8), (40, 6), (60, 4), (80, 2), (100, 0)]
    assert q.swipe_points(1, 2, 3, 4, 0) == [(1, 2), (3, 4)]
    assert len(q.swipe_points(0, 0, 9, 9, 39)) == 2 and len(q.swipe_points(0, 0, 9, 9, 40)) == 3
    assert len(q.swipe_points(0, 0, 9, 9, 9999)) == 500


def test_wheel_positions():
    assert q.wheel_positions(1) == [90, 79, 69, 58] + [58] * 4
    assert q.wheel_positions(-1) == [110, 121, 131, 142] + [142] * 4
    assert q.wheel_positions(3, q.wheel_units('WHEELSENSITIVITY=60\n')) == [90, 80, 70, 59, 49, 39, 29, 18] + [18] * 4
    # x runs 1..200 round the wheel.
    assert q.wheel_positions(1, 28, 5) == [195, 184, 174, 163] + [163] * 4
    assert q.wheel_positions(-1, 28, 190) == [200, 11, 21, 32] + [32] * 4


@pytest.mark.parametrize('config,units', [
    ('WHEELSENSITIVITY=100\n', 14.0), ('A=1\nWHEELSENSITIVITY=200  \nB=2\n', 7.0), ('WHEELSENSITIVITY=70', 20.0),
    ('WHEELSENSITIVITY=50\n', 28.0), ('', 28.0), ('XWHEELSENSITIVITY=100\n', 28.0), ('WHEELSENSITIVITY=080\n', 28.0),
    ('WHEELSENSITIVITY=40\n', 28.0), ('WHEELSENSITIVITY=300\n', 28.0), ('WHEELSENSITIVITY=55\n', 28.0),
])
def test_wheel_units(config, units):
    assert q.wheel_units(config) == units


def test_event_writer():
    assert q.event_writer('/dev/a b', [b'a', b'\n'], 5) == (
        r"""printf '%b' '\141' > '/dev/a b'; usleep 5; printf '%b' '\012' > '/dev/a b'""")
    assert q.event_writer('/x', [b'a', b'b']).count('usleep 20000') == 1
    assert q.event_writer('/x', [b'a', b'b'], 0) == r"""printf '%b' '\141\142' > /x"""


def test_input_path(shell):
    table = ('N: Name="md-gpio-keys"\nH: Handlers=kbd event1\n\nN: Name="hyn_ts"\nH: Handlers=event2\n\n'
             'N: Name="hyn_sliderbar"\nH: Handlers=event3\n')
    fake = shell(*[table.encode()] * 3)
    assert [q.input_path(k) for k in ('key', 'tap', 'wheel')] == ['/dev/input/event1', '/dev/input/event2', '/dev/input/event3']
    assert fake.calls[0].cmd == 'cat /proc/bus/input/devices'
    shell(b'')
    with pytest.raises(ValueError, match=r'^no hyn_ts input device; inspect `devices`$'):
        q.input_path('tap')


DEVICES = '''I: Bus=0019 Vendor=0001 Product=0001 Version=0100
N: Name="md-gpio-keys"
P: Phys=gpio-keys/input0
H: Handlers=kbd event1
B: PROP=0
B: EV=3
B: KEY=28 0 101080 0 0 0

I: Bus=0018 Vendor=0000 Product=0000 Version=0000
N: Name="hyn_ts"
H: Handlers=event2
B: EV=b
B: KEY=400 0 0 0 0 0 0 0 0 0 0
B: ABS=1000003

I: Bus=0000 Vendor=0000 Product=0000 Version=0000
N: Name="no-evdev"
H: Handlers=kbd
B: EV=3
'''


def test_parse_devices():
    gpio, touch = q.parse_devices(DEVICES)  # no-evdev has no event node to write to
    assert (gpio['name'], gpio['event'], gpio['handlers'], gpio['ABS']) == ('md-gpio-keys', 1, 'kbd event1', 0)
    assert (touch['name'], touch['event'], touch['handlers']) == ('hyn_ts', 2, 'event2')
    assert set(gpio) == set(touch) == {'name', 'event', 'handlers', 'KEY', 'ABS'}
    # the kernel's unpadded words, most significant first, 32 bits each
    assert q.bits(gpio['KEY']) == set(q.KEYS.values()) == {103, 108, 116, 163, 165}
    assert q.bits(touch['KEY']) == {q.BTN_TOUCH} and q.bits(touch['ABS']) == {q.ABS_X, q.ABS_Y, q.ABS_PRESSURE}


def test_decode_events():
    # each field at its limits: unsigned seconds, microseconds, type and code, a signed value
    edge = struct.pack('<IIHHi', 0xffffffff, 999999, 0xffff, 0x8000, -1)
    stream = edge + struct.pack('<IIHHi', 5, 7, q.EV_KEY, 116, 1) + q.event(q.EV_SYN, 0, 0)
    assert q.decode_events(stream) == [(0xffffffff, 999999, 0xffff, 0x8000, -1), (5, 7, q.EV_KEY, 116, 1), (0, 0, q.EV_SYN, 0, 0)]
    assert q.decode_events(b'') == []
    for size in (15, 17, len(stream) - 1):
        with pytest.raises(ValueError, match=r'^truncated input-event stream$'):
            q.decode_events(stream[:size])


def test_devices(shell):
    fake = shell(b'N: Name="caf\xff"\nH: Handlers=event4\n')
    assert q.devices() == [{'name': 'caf�', 'event': 4, 'handlers': 'event4', 'KEY': 0, 'ABS': 0}]
    assert fake.calls[0].cmd == 'cat /proc/bus/input/devices'


def test_config(shell):
    fake = shell(b'A=1\xff')
    assert q.config() == 'A=1�'
    assert fake.calls[0].cmd == 'cat /mnt/data/config.ini 2>/dev/null'


def test_parse_state():
    assert q.parse_state('1072\n92.41\n00\n01\n-\nff\n1e\n') == {
        'demo': 1072, 'uptime': 92.41, 'backlight': 0, 'usb': 1, 'keylock': None, 'lockscreen': 255, 'volume': 30}
    for text in ('', '1 2 3 4 5 6', '1 2 3 4 5 6 7 8'):
        with pytest.raises(ValueError, match=r'^demo is not running$'):
            q.parse_state(text)


def test_state_reads_demo_memory(shell):
    fake = shell(b'7 1.5 01 02 03 04 05\n')
    assert q.state() == {'demo': 7, 'uptime': 1.5, 'backlight': 1, 'usb': 2, 'keylock': 3, 'lockscreen': 4, 'volume': 5}
    reads = ''.join(f'v=$(dd if=/proc/$p/mem bs=1 skip={a} count=1 2>/dev/null | xxd -p); echo "${{v:--}}"; '
                    for a in (0x99f5b0, 0xa38aef, 0xa38c32, 0xa38b0f, 0xa38c41))
    assert fake.calls[0].cmd == f'p=$(pidof -s demo) && echo $p && cut -d" " -f1 /proc/uptime && {{ {reads}}}'


def press(presses):
    return q.event_writer('/dev/key', q.key_events(116) * presses, 80000).encode()


@pytest.fixture
def lit(monkeypatch):
    """wake()'s world: state() plays back backlight values, config() is WAKEDOUBLE's."""
    def install(values, config=''):
        values = iter(values)
        monkeypatch.setattr(q, 'state', lambda: {'backlight': next(values)})
        monkeypatch.setattr(q, 'config', lambda: config)
        monkeypatch.setattr(q, 'input_path', lambda kind: {'key': '/dev/key'}[kind])
    return install


def test_wake_when_lit(lit, shell):
    fake = shell()
    lit([1])
    assert q.wake() is False and not fake.calls


def test_wake_presses_centre(lit, shell):
    fake = shell()
    lit([0, 0, 1])
    assert q.wake() is True
    assert [(c.cmd, c.data) for c in fake.calls] == [('sh', press(1))]


@pytest.mark.parametrize('config,presses', [('WAKEDOUBLE=1\n', 2), ('A=1\nWAKEDOUBLE=1  \nB=2\n', 2), ('WAKEDOUBLE=0\n', 1), ('XWAKEDOUBLE=1\n', 1)])
def test_wake_double(lit, shell, config, presses):
    fake = shell()
    lit([0, 1], config)
    assert q.wake() is True
    assert fake.calls[0].data == press(presses)


def test_wake_unknown_backlight(lit, shell):
    shell()
    lit([None])
    with pytest.raises(RuntimeError, match=r'^cannot read the backlight byte; see `state`$'):
        q.wake()


def test_wake_gives_up(lit, shell, monkeypatch):
    shell()
    lit([0] * 21)
    naps = []
    monkeypatch.setattr(time, 'sleep', naps.append)
    with pytest.raises(RuntimeError, match=r'^the screen stayed off$'):
        q.wake()
    assert naps == [.1] * 20


def test_wake_last_chance(lit, shell):
    shell()
    lit([0] * 20 + [1])
    assert q.wake() is True


@pytest.fixture
def panel(monkeypatch):
    """A 2x3 portrait panel, so a whole frame is a few bytes."""
    monkeypatch.setattr(q, 'W', 2)
    monkeypatch.setattr(q, 'H', 3)


def bgra(k):
    """Frame pixel k, counted along the portrait rows: every channel of every pixel its own byte."""
    return bytes((0x10 + k, 0x20 + k, 0x30 + k, 0x40 + k))


def rgba(*pixels):
    return b''.join(bytes((0x30 + k, 0x20 + k, 0x10 + k, 0x40 + k)) for k in pixels)


def fb(w=2, h=3, stride=8, size=None, pixels=None, magic=b'Q2FB\0'):
    """--shot's output: magic, width, height, stride and size, then rows padded to stride with 0xee."""
    if pixels is None:
        pixels = b''.join(b''.join(bgra(y * w + x) for x in range(w)) + b'\xee' * (stride - w * 4) for y in range(h))
    return magic + struct.pack('<4I', w, h, stride, stride * h if size is None else size) + pixels


def png_chunks(png):
    """A PNG's (tag, body) chunks, each CRC checked."""
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    chunks, at = [], 8
    while at < len(png):
        size, = struct.unpack_from('>I', png, at)
        tag, body = png[at + 4:at + 8], png[at + 8:at + 8 + size]
        assert struct.unpack_from('>I', png, at + 8 + size) == (zlib.crc32(tag + body),)
        chunks.append((tag, body))
        at += 12 + size
    return chunks


@pytest.mark.parametrize('stride', [8, 11])
def test_framebuffer_png(panel, stride):
    chunks = png_chunks(q.framebuffer_png(fb(stride=stride)))
    assert [tag for tag, _ in chunks] == [b'IHDR', b'IDAT', b'IEND'] and chunks[2][1] == b''
    assert chunks[0][1] == struct.pack('>2I5B', 3, 2, 8, 6, 0, 0, 0)  # 3x2, 8-bit RGBA
    # Turned counterclockwise, the frame's right column is the picture's top row.
    assert zlib.decompress(chunks[1][1]) == b'\0' + rgba(1, 3, 5) + b'\0' + rgba(0, 2, 4)


def test_framebuffer_png_whole_panel():
    page = bytearray(q.W * q.H * 4)
    bottom_left = (q.H - 1) * q.W * 4
    page[(q.W - 1) * 4:q.W * 4], page[bottom_left:bottom_left + 4] = b'\1\2\3\4', b'\5\6\7\x08'
    chunks = png_chunks(q.framebuffer_png(fb(q.W, q.H, q.W * 4, pixels=bytes(page))))
    assert chunks[0][1] == struct.pack('>2I5B', 375, 320, 8, 6, 0, 0, 0)
    picture = bytearray(320 * (1 + 375 * 4))
    picture[1:5], picture[-4:] = b'\3\2\1\4', b'\7\6\5\x08'
    assert zlib.decompress(chunks[1][1]) == picture


@pytest.mark.parametrize('data', [b'', fb()[:20], fb(magic=b'Q2FB\1')])
def test_framebuffer_png_wants_the_helpers_header(panel, data):
    with pytest.raises(ValueError, match=r'^invalid framebuffer response$'):
        q.framebuffer_png(data)


@pytest.mark.parametrize('data', [
    fb(3, 2, 12),                   # landscape
    fb(stride=7, pixels=bytes(21)),  # rows overlap
    fb(size=25, pixels=bytes(25)),   # not stride * height
    fb()[:-1], fb() + b'\0', fb()[:21],
])
def test_framebuffer_png_refuses_an_inconsistent_frame(panel, data):
    with pytest.raises(ValueError, match=r'^invalid or truncated framebuffer data$'):
        q.framebuffer_png(data)


def test_frame(shell):
    import gzip
    fake = shell(gzip.compress(b'pixels'))
    assert q.frame() == b'pixels'
    assert fake.calls[0].cmd == '/usr/bin/q2remote --shot | gzip -1'
    for junk in (b'not gzip', zlib.compress(b'pixels')):  # gzip alone, as the helper sends
        shell(junk)
        with pytest.raises(ValueError, match=r'^invalid framebuffer response$'):
            q.frame()


def png_rgba(data):
    """A PNG's (width, height, RGBA bytes), read with struct and zlib alone: 8-bit RGBA, not interlaced."""
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    at, chunks = 8, []
    while at < len(data):
        n, tag = struct.unpack_from('>I4s', data, at)
        body = data[at + 8:at + 8 + n]
        assert struct.unpack_from('>I', data, at + 8 + n)[0] == zlib.crc32(tag + body), tag
        chunks.append((tag, body))
        at += 12 + n
    assert chunks[0][0] == b'IHDR' and chunks[-1] == (b'IEND', b'')
    w, h, *form = struct.unpack('>IIBBBBB', chunks[0][1])
    assert form == [8, 6, 0, 0, 0]
    raw, span = zlib.decompress(b''.join(body for tag, body in chunks if tag == b'IDAT')), 1 + 4 * w
    assert len(raw) == h * span
    out, up = bytearray(), bytes(4 * w)
    for y in range(h):
        kind, row = raw[y * span], bytearray(raw[y * span + 1:(y + 1) * span])
        for i in range(4 * w) if kind else ():  # PNG's filters: left (a), up (b) and up-left (c) predict each byte
            a, b, c = row[i - 4] if i >= 4 else 0, up[i], up[i - 4] if i >= 4 else 0
            p = a + b - c
            paeth = a if abs(p - a) <= abs(p - b) and abs(p - a) <= abs(p - c) else b if abs(p - b) <= abs(p - c) else c
            row[i] = (row[i] + (a, b, (a + b) // 2, paeth)[kind - 1]) & 255
        out += row
        up = row
    return w, h, bytes(out)


def test_framebuffer_png_is_the_page_turned_upright():
    # The panel's page is 320 columns by 375 rows, BGRA, each row padded to its stride; turned counterclockwise,
    # the screen is 375 by 320 and page column c, row r is screen x r, y 319 - c: where touch_xy puts a tap there.
    w, h, stride = q.W, q.H, q.W * 4 + 8
    colour = lambda c, r: (c & 255, r & 255, c >> 8 | r >> 8 << 4, (c + r) & 255)  # B, G, R, A: each pixel its own
    page = b''.join(b''.join(bytes(colour(c, r)) for c in range(w)) + b'\xee' * 8 for r in range(h))
    pw, ph, rgba = png_rgba(q.framebuffer_png(b'Q2FB\0' + struct.pack('<IIII', w, h, stride, len(page)) + page))
    assert (pw, ph) == (375, 320)
    assert rgba == b''.join(bytes((R, G, B, A)) for y in range(ph) for x in range(pw) for B, G, R, A in (colour(319 - y, x),))


@pytest.mark.parametrize('data', [b'', b'Q2FB\0' + bytes(15), b'Q2FX\0' + struct.pack('<IIII', 320, 375, 1280, 480000) + bytes(480000)])
def test_framebuffer_png_refuses_a_foreign_reply(data):
    with pytest.raises(ValueError, match=r'^invalid framebuffer response$'):
        q.framebuffer_png(data)


@pytest.mark.parametrize('w, h, stride, size, sent', [
    (375, 320, 1500, 480000, 480000),  # landscape: not the Q2's panel
    (320, 375, 1276, 478500, 478500),  # a stride short of a row's pixels
    (320, 375, 1280, 479999, 479999),  # a size that isn't its rows'
    (320, 375, 1280, 480000, 479999),  # cut short
    (320, 375, 1280, 480000, 480001),  # more than it said
])
def test_framebuffer_png_refuses_a_bad_page(w, h, stride, size, sent):
    with pytest.raises(ValueError, match=r'^invalid or truncated framebuffer data$'):
        q.framebuffer_png(b'Q2FB\0' + struct.pack('<IIII', w, h, stride, size) + bytes(sent))


@pytest.fixture
def shots(monkeypatch):
    def install(frames, backlight=1):
        frames = iter(frames)
        monkeypatch.setattr(q, 'frame', lambda: next(frames))
        monkeypatch.setattr(q, 'framebuffer_png', lambda data: b'png:' + data)
        if isinstance(backlight, Exception):
            def state():
                raise backlight
        else:
            state = lambda: {'backlight': backlight}
        monkeypatch.setattr(q, 'state', state)
    return install


def test_screenshot(shots, tmp_path, capsys):
    shots([b'a'])
    q.screenshot(tmp_path/'out')
    assert (tmp_path/'out').read_bytes() == b'png:a' and capsys.readouterr().err == ''


def test_screenshot_waits_for_two_matching_frames(shots, tmp_path, monkeypatch):
    shots([b'a', b'b', b'b', b'never'])
    naps = []
    monkeypatch.setattr(time, 'sleep', naps.append)
    q.screenshot(tmp_path/'out', settle=True)
    assert (tmp_path/'out').read_bytes() == b'png:b' and naps == [q.SETTLE_POLL_S] * 2


def test_screenshot_gives_up_on_a_changing_screen(shots, tmp_path, monkeypatch, capsys):
    shots([b'a', b'b', b'c', b'never'])
    clock = iter([0, 1, q.SETTLE_S + .5])
    monkeypatch.setattr(time, 'monotonic', lambda: next(clock))
    q.screenshot(tmp_path/'out', settle=True)
    assert (tmp_path/'out').read_bytes() == b'png:c'
    assert capsys.readouterr().err == 'q2remote: the screen kept changing for 5 s\n'


def test_screenshot_stops_at_exactly_the_limit_only_if_past_it(shots, tmp_path, monkeypatch):
    shots([b'a', b'b', b'c', b'c'])
    clock = iter([0, q.SETTLE_S, q.SETTLE_S])
    monkeypatch.setattr(time, 'monotonic', lambda: next(clock))
    q.screenshot(tmp_path/'out', settle=True)
    assert (tmp_path/'out').read_bytes() == b'png:c'


def test_screenshot_on_a_dark_screen(shots, tmp_path, capsys):
    shots([b'a'], backlight=0)
    q.screenshot(tmp_path/'out')
    assert capsys.readouterr().err == 'q2remote: the screen is off, so this is the last frame it showed; `wake` first\n'


@pytest.mark.parametrize('failure', [ValueError('no demo'), OSError('gone')])
def test_screenshot_keeps_the_frame_if_state_fails(shots, tmp_path, failure):
    shots([b'a'], backlight=failure)
    q.screenshot(tmp_path/'out')
    assert (tmp_path/'out').exists()


def test_screenshot_bad_frame_leaves_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(q, 'frame', lambda: b'junk')
    with pytest.raises(ValueError):
        q.screenshot(tmp_path/'out')
    assert not (tmp_path/'out').exists()


@pytest.fixture
def runner(monkeypatch):
    def install(*replies):
        calls = []
        replies = list(replies)
        def run(script, timeout=q.TIMEOUT):
            calls.append((script, timeout))
            return replies.pop(0)
        monkeypatch.setattr(q, 'run', run)
        return calls
    return install


def test_card_mounted(runner):
    calls = runner((b'', 0), (b'', 1))
    q.card_mounted()
    assert calls == [('mountpoint -q /mnt/mmc', q.TIMEOUT)]
    with pytest.raises(ValueError, match=r'^/mnt/mmc is not mounted: USB storage has the card \(`key return` ends it\)$'):
        q.card_mounted()


def test_push(shell, tmp_path, monkeypatch):
    local = tmp_path/'blob'
    local.write_bytes(b'data')
    md5 = hashlib.md5(b'data').hexdigest()
    fake = shell(md5.encode() + b'  /tmp/x y\n')
    monkeypatch.setattr(q, 'card_mounted', lambda: pytest.fail('checked the card for a /tmp push'))
    assert q.push(local, '/tmp//x y') == md5
    part, remote = "'/tmp/x y.part'", "'/tmp/x y'"
    assert fake.calls[0].cmd == f'cat >{part} && sync && mv {part} {remote} && sync && md5sum {remote} || {{ rm -f {part}; echo failed; }}'
    assert fake.calls[0].data == b'data' and fake.calls[0].timeout == 120


@pytest.mark.parametrize('remote', ['/mnt/mmc/x', '/mnt/mmc'])
def test_push_checks_the_card_first(shell, tmp_path, monkeypatch, remote):
    local = tmp_path/'blob'
    local.write_bytes(b'data')
    def absent():
        raise ValueError('no card')
    monkeypatch.setattr(q, 'card_mounted', absent)
    fake = shell()
    with pytest.raises(ValueError, match='no card'):
        q.push(local, remote, 9)
    assert not fake.calls


def test_push_card_lookalike_is_not_the_card(shell, tmp_path, monkeypatch):
    local = tmp_path/'blob'
    local.write_bytes(b'data')
    monkeypatch.setattr(q, 'card_mounted', lambda: pytest.fail('/mnt/mmc2 is not the card'))
    shell(hashlib.md5(b'data').hexdigest().encode())
    q.push(local, '/mnt/mmc2/x')


def test_push_failure(shell, tmp_path):
    local = tmp_path/'blob'
    local.write_bytes(b'data')
    shell(b' failed\xff\n')
    with pytest.raises(ValueError, match='^push failed: failed�$'):
        q.push(local, '/tmp/x')


def test_pull(runner, tmp_path):
    calls = runner((b'body', 0))
    local = tmp_path/'out'
    q.pull('/a b', str(local))
    assert calls == [("cat '/a b'", q.TIMEOUT)]
    assert local.read_bytes() == b'body' and not (tmp_path/'out.part').exists()


def test_pull_failure(runner, tmp_path):
    runner((b' no such file\xff\n', 1))
    local = tmp_path/'out'
    with pytest.raises(ValueError, match='^no such file�$'):
        q.pull('/x', str(local))
    assert not local.exists() and not (tmp_path/'out.part').exists()


def make_tar(path):
    with tarfile.open(path, 'w') as t:
        info = tarfile.TarInfo('x')
        t.addfile(info, io.BytesIO())


def test_install(runner, tmp_path, monkeypatch):
    make_tar(tmp_path/'update.tar')
    pushed = []
    monkeypatch.setattr(q, 'push', lambda local, remote: pushed.append((local, remote)) or 'abc')
    calls = runner((b'abc  /mnt/mmc/update.tar\n', 0))
    q.install(tmp_path/'update.tar')
    assert pushed == [(tmp_path/'update.tar', '/mnt/mmc/update.tar')]
    assert calls == [('sync; echo 3 >/proc/sys/vm/drop_caches; md5sum /mnt/mmc/update.tar', 120)]


def test_install_reads_back_wrong(runner, tmp_path, monkeypatch):
    make_tar(tmp_path/'update.tar')
    monkeypatch.setattr(q, 'push', lambda local, remote: 'abc')
    runner((b'zzz  /mnt/mmc/update.tar\n', 0))
    with pytest.raises(ValueError, match=r'^update.tar reads back differently from the card; check the card$'):
        q.install(tmp_path/'update.tar')


def test_install_refuses_a_non_tar(tmp_path, monkeypatch):
    (tmp_path/'x').write_bytes(b'junk' * 300)
    monkeypatch.setattr(q, 'push', lambda *a: pytest.fail('pushed a non-tar'))
    with pytest.raises(ValueError, match=r'x is not a tar file$'):
        q.install(tmp_path/'x')


@pytest.fixture
def probes(monkeypatch):
    """create_connection for find(): `answers` maps a host to its reply; anything else refuses."""
    def install(answers):
        seen = []
        def create(address, timeout):
            host, port = address
            seen.append((host, port, timeout))
            if host not in answers:
                raise OSError('refused')
            sock = Sock(answers[host])
            answers[host] = sock
            return sock
        monkeypatch.setattr(socket, 'create_connection', create)
        return seen
    return install


def test_find_given_hosts(probes):
    answers: dict = {'10.0.0.1': b'ERR\n', '10.0.0.3': b'OK\n'}  # each answered host's becomes its Sock
    seen = probes(answers)
    assert q.find(['10.0.0.1', '10.0.0.2', '10.0.0.3']) == ['10.0.0.1']
    assert sorted(seen) == [(f'10.0.0.{i}', q.PORT, 2) for i in (1, 2, 3)]
    assert answers['10.0.0.1'].sent == b'q2remote-find\n\n' and answers['10.0.0.1'].asked == [4]


def test_find_scans_this_network(probes, monkeypatch):
    udp = []
    class Udp:
        def __init__(self, family, kind):
            udp.append((family, kind))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def connect(self, address):
            udp.append(address)

        def getsockname(self):
            return ('192.168.7.9', 1234)
    monkeypatch.setattr(socket, 'socket', Udp)
    seen = probes({'192.168.7.1': b'ERR\n', '192.168.7.254': b'ERR\n'})
    assert q.find() == ['192.168.7.1', '192.168.7.254']
    assert udp == [(socket.AF_INET, socket.SOCK_DGRAM), ('8.8.8.8', 80)]
    assert len(seen) == 254


def test_reboot_without_waiting(shell):
    fake = shell()
    q.reboot(False)
    assert [c.cmd for c in fake.calls] == ['(sleep 1; sync; reboot) </dev/null >/dev/null 2>&1 &']


def test_reboot_waits_for_a_new_boot_id(shell, monkeypatch, capsys):
    fake = shell(b'old', b'', ConnectionError('down'), TimeoutError('down'), b'old', b'new', b'never')
    naps = []
    monkeypatch.setattr(time, 'sleep', naps.append)
    clock = iter([100, 142.4])
    monkeypatch.setattr(time, 'monotonic', lambda: next(clock))
    q.reboot(True)
    assert fake.calls[0].cmd == 'cat /proc/sys/kernel/random/boot_id' and fake.calls[0].timeout == q.TIMEOUT
    assert [(c.cmd, c.timeout) for c in fake.calls[2:6]] == [('cat /proc/sys/kernel/random/boot_id', 5)] * 4
    assert naps == [5] * 4 and capsys.readouterr().out == 'back after 42 s\n'


def test_reboot_gives_up(shell, monkeypatch):
    fake = shell(b'old', b'', *[b'old'] * 100)
    naps = []
    monkeypatch.setattr(time, 'sleep', naps.append)
    with pytest.raises(TimeoutError, match=r'^the Q2 did not answer within 360 s$'):
        q.reboot(True)
    assert len(naps) == 72 and len(fake.calls) == 74


def test_symbol_table(monkeypatch):
    calls = []
    outputs = {'-D': '00001000 T dynamic\n00002000 W weak\n', '--defined-only': ('00003000 t local\n00004000 w lower\n'
               '00005000 D data\n00006000 bad line here\n00007000 T\n00008000 X other\n00002000 T weak_static\n')}
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return types.SimpleNamespace(stdout=outputs[args[1]])
    monkeypatch.setattr(subprocess, 'run', run)
    keys, names, bisect_right = q.symbol_table('/x')
    assert keys == [0x1000, 0x2000, 0x3000, 0x4000]
    assert names == ['dynamic', 'weak_static', 'local', 'lower']
    assert bisect_right(keys, 0x2000) == 2
    assert calls == [(['llvm-nm', '-D', '--defined-only', '/x'], dict(capture_output=True, text=True)),
                     (['llvm-nm', '--defined-only', '/x'], dict(capture_output=True, text=True))]


MAPS = '''1234
1235
1234
00400000-00500000 r-xp 00001000 08:01 1 /release/bin/demo
10000000-10010000 rwxp 0000a000 08:01 2 /lib/libc.so
20000000-20010000 rw-p 00000000 08:01 3 /lib/data
30000000-30010000 r-xp 00000000 00:00 0 [vdso]
'''


@pytest.fixture
def prof(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    tables = {'demo': ([0x400100, 0x400200], ['stock_a', 'stock_b'], bisect.bisect_right),
              'patch.elf': ([0x400000], ['patch_entry'], bisect.bisect_right),
              'libc.so': ([0x1000, 0xb011], ['libc_f', 'libc_g'], bisect.bisect_right)}
    monkeypatch.setattr(q, 'symbol_table', lambda path: tables[pathlib.Path(path).name])
    pulled = []
    def pull(path, local):
        pulled.append((path, local))
        pathlib.Path(local).write_bytes(b'')
    monkeypatch.setattr(q, 'pull', pull)
    def install(samples, table=None):
        if table:
            tables.update(table)
        return runner_install([(MAPS.encode(), 0), (samples.encode(), 0)])
    calls = []
    def runner_install(replies):
        def run(script, timeout=q.TIMEOUT):
            calls.append((script, timeout))
            return replies.pop(0)
        monkeypatch.setattr(q, 'run', run)
        return calls, pulled
    return install


def test_profile_commands_one_thread(prof, tmp_path, capsys):
    calls, pulled = prof('')
    q.profile(2, False, None)
    assert calls == [('p=$(pidof demo); echo $p; ls /proc/$p/task; cat /proc/$p/maps', q.TIMEOUT),
                     ('q2remote --prof 10 200 1234', q.TIMEOUT + 6)]
    assert capsys.readouterr().out == '' and (tmp_path/'.cache/q2-prof').is_dir() and not pulled


def test_profile_all_threads(prof, monkeypatch):
    monkeypatch.setenv('Q2_PROF_BIN', '/tmp/q2remote-new')
    calls, _ = prof('')
    q.profile(1.5, True, None)
    assert calls[1] == ('/tmp/q2remote-new --prof 50 30 1235 1234', q.TIMEOUT + 4.5)


def test_profile_needs_demo(monkeypatch):
    monkeypatch.setattr(q, 'run', lambda *a, **k: (b'no demo', 1))
    with pytest.raises(RuntimeError, match=r'^no demo$'):
        q.profile(1, False, None)


def test_profile_stops_at_a_failed_map_read(monkeypatch):
    calls = []
    monkeypatch.setattr(q, 'run', lambda *a, **k: calls.append(a) or (b'', 1))
    with pytest.raises(RuntimeError):
        q.profile(1, False, None)
    assert len(calls) == 1


def test_profile_report(prof, tmp_path, capsys):
    (tmp_path/'build').mkdir()
    calls, pulled = prof('''1234 R 400104 400204 20000000 10001010 400204
1234 R 400104 400204
1234 D 10001010 xyz
short line
1235 S 400104 400104 400104 10001010
''' .replace('xyz', '30000000'))
    q.profile(1, True, str(tmp_path/'build'))
    out = capsys.readouterr().out
    assert out.splitlines() == [
        '== thread 1234 running: 2 of 20 samples',
        '     1  patch_entry  <- libc_f  <- patch_entry',
        '     1  patch_entry',
        '== thread 1234 blocked: 1 of 20 samples',
        '     1  libc_f',
        '== thread 1235 sleeping: 1 of 20 samples',
        '     1  patch_entry  <- libc_f',
    ]
    assert pulled == [('/lib/libc.so', str(tmp_path/'.cache/q2-prof/libc.so'))]


def test_profile_stock_demo_without_a_build(prof, tmp_path, capsys):
    # The library is mapped at 0x400000 from file offset 0x1000: 0x400104 is its 0x1104.
    calls, pulled = prof('1234 R 400104 400104\n', {'demo': ([0x1100], ['s'], bisect.bisect_right)})
    q.profile(1, False, None)
    assert pulled == [('/release/bin/demo', str(tmp_path/'.cache/q2-prof/demo'))]
    assert '     1  s\n' in capsys.readouterr().out


def test_profile_keeps_a_cached_library(prof, tmp_path, capsys):
    cache = tmp_path/'.cache/q2-prof'
    cache.mkdir(parents=True)
    (cache/'libc.so').write_bytes(b'')
    (cache/'demo').write_bytes(b'')
    calls, pulled = prof('1234 R 10001010 10001010\n')
    q.profile(1, False, None)
    assert not pulled and '     1  libc_f\n' in capsys.readouterr().out


def test_profile_stops_at_six_frames(prof, tmp_path, capsys):
    (tmp_path/'build').mkdir()
    tables = {'patch.elf': ([0x400000 + i * 16 for i in range(9)], [f'f{i}' for i in range(9)], bisect.bisect_right)}
    prof('1234 R ' + ' '.join(hex(0x400000 + i * 16)[2:] for i in range(9)) + '\n', tables)
    q.profile(1, False, str(tmp_path/'build'))
    assert '     1  f0  <- f1  <- f2  <- f3  <- f4  <- f5\n' in capsys.readouterr().out


def test_profile_symbol_range_ends_at_0x20000(prof, tmp_path, capsys):
    (tmp_path/'build').mkdir()
    prof('1234 R 41ffff 420000 400000\n', {'patch.elf': ([0x400000], ['near'], bisect.bisect_right),
                                            'demo': ([0x400000], ['near_demo'], bisect.bisect_right)})
    q.profile(1, False, str(tmp_path/'build'))
    assert '     1  near  <- demo  <- near\n' in capsys.readouterr().out


def test_profile_a_map_ends_before_its_last_address(prof, tmp_path, capsys):
    (tmp_path/'build').mkdir()
    prof('1234 R 500000 400000 500000\n')
    q.profile(1, False, str(tmp_path/'build'))
    assert '     1  patch_entry\n' in capsys.readouterr().out


def test_profile_address_in_the_later_of_two_adjacent_maps(prof, monkeypatch, capsys):
    maps = '1234\n1234\n10000000-10001000 r-xp 00000000 08:01 2 /lib/one.so\n10001000-10002000 r-xp 00000000 08:01 3 /lib/two.so\n'
    calls, _ = prof('', {'one.so': ([0], ['in_one'], bisect.bisect_right), 'two.so': ([0], ['in_two'], bisect.bisect_right)})
    replies = [(maps.encode(), 0), (b'1234 R 10001000 10001000\n', 0)]
    monkeypatch.setattr(q, 'run', lambda *a, **k: replies.pop(0))
    q.profile(1, False, None)
    assert '     1  in_two\n' in capsys.readouterr().out


def test_profile_truncates_long_names(prof, tmp_path, capsys):
    (tmp_path/'build').mkdir()
    prof('1234 R 400000 400000\n', {'patch.elf': ([0x400000], ['n' * 80], bisect.bisect_right)})
    q.profile(1, False, str(tmp_path/'build'))
    assert f'     1  {"n" * 60}\n' in capsys.readouterr().out


def test_profile_counts_and_keeps_the_top_15(prof, tmp_path, capsys):
    (tmp_path/'build').mkdir()
    addresses = [0x400000 + i * 16 for i in range(16)]
    samples = ''.join(f'1234 R {a:x} {a:x}\n' for a in addresses) + f'1234 R {addresses[0]:x} {addresses[0]:x}\n'
    prof(samples, {'patch.elf': (addresses, [f'f{i}' for i in range(16)], bisect.bisect_right)})
    q.profile(1, False, str(tmp_path/'build'))
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == '== thread 1234 running: 17 of 100 samples' and lines[1] == '     2  f0' and len(lines) == 16


def test_profile_address_before_every_symbol_keeps_its_module(prof, capsys):
    prof('1234 R 10004000 10004000\n', {'libc.so': ([0x100000], ['far'], bisect.bisect_right)})
    q.profile(1, False, None)
    assert '     1  libc.so\n' in capsys.readouterr().out


def test_profile_far_symbol_is_not_used(prof, tmp_path, capsys):
    (tmp_path/'build').mkdir()
    bisect_right = bisect.bisect_right
    prof('1234 R 4fffff 420000 41ffff\n', {'patch.elf': ([0x400000], ['near'], bisect_right),
                                           'demo': ([0x400000], ['near_demo'], bisect_right)})
    q.profile(1, False, str(tmp_path/'build'))
    assert '     1  demo  <- near\n' in capsys.readouterr().out


class Input:
    """input_action()'s world: the event node and every script handed to `sh`."""
    def __init__(self, monkeypatch):
        self.scripts, self.kinds = [], []
        monkeypatch.setattr(q, 'input_path', lambda kind: self.kinds.append(kind) or '/dev/in')
        monkeypatch.setattr(q, 'shell', lambda cmd, data=b'', *a, **k: self.scripts.append((cmd, data)))
        monkeypatch.setattr(q, 'config', lambda: 'WHEELSENSITIVITY=100\n')


@pytest.fixture
def inputs(monkeypatch):
    return Input(monkeypatch)


def test_input_key(inputs):
    q.input_action('key', ['PLAY'])
    assert inputs.scripts == [('sh', q.event_writer('/dev/in', q.key_events(108), 80000).encode())] and inputs.kinds == ['key']
    q.input_action('key', ['next', '1'])
    q.input_action('key', ['prev', '60000'])
    assert inputs.scripts[1:] == [('sh', q.event_writer('/dev/in', q.key_events(163), 1000).encode()),
                                  ('sh', q.event_writer('/dev/in', q.key_events(165), 60000000).encode())]


@pytest.mark.parametrize('args,message', [
    (['volume'], r'^key must be one of centre, center, return, play, next, prev$'),
    (['play', 'x'], r'^key duration must be an integer number of milliseconds$'),
    (['play', '0'], r'^key duration must be 1..60000 ms$'),
    (['play', '60001'], r'^key duration must be 1..60000 ms$'),
])
def test_input_key_errors(inputs, args, message):
    with pytest.raises(ValueError, match=message):
        q.input_action('key', args)
    assert not inputs.scripts


def test_input_tap(inputs):
    q.input_action('tap', ['10', '20'])
    assert inputs.scripts == [('sh', q.event_writer('/dev/in', q.touch_batches([(10, 20)])).encode())] and inputs.kinds == ['tap']


def test_input_tap_errors(inputs):
    with pytest.raises(ValueError, match=r'^tap coordinates must be integers$'):
        q.input_action('tap', ['x', '2'])


def test_input_swipe(inputs):
    q.input_action('swipe', ['1', '2', '3', '4'])
    q.input_action('swipe', ['1', '2', '3', '4', '20'])
    q.input_action('swipe', ['1', '2', '3', '4', '10000'])
    for script, ms in zip(inputs.scripts, (300, 20, 10000)):
        assert script == ('sh', q.event_writer('/dev/in', q.touch_batches(q.swipe_points(1, 2, 3, 4, ms))).encode())
    assert inputs.kinds == ['tap'] * 3


@pytest.mark.parametrize('args,message', [
    (['a', '2', '3', '4'], r'^swipe coordinates and duration must be integers$'),
    (['1', '2', '3', '4', '19'], r'^swipe duration must be 20..10000 ms$'),
    (['1', '2', '3', '4', '10001'], r'^swipe duration must be 20..10000 ms$'),
])
def test_input_swipe_errors(inputs, args, message):
    with pytest.raises(ValueError, match=message):
        q.input_action('swipe', args)


def test_input_wheel(inputs):
    q.input_action('wheel', ['1'])
    (cmd, data), = inputs.scripts
    xs = q.wheel_positions(1, 14.0)
    batches = [events((q.EV_ABS, q.ABS_X, 100), (q.EV_ABS, q.ABS_Y, 100), (q.EV_ABS, q.ABS_PRESSURE, 1),
                      (q.EV_KEY, q.BTN_TOUCH, 1), (q.EV_SYN, 0, 0))]
    batches += [events((q.EV_ABS, q.ABS_X, x), (q.EV_ABS, q.ABS_Y, 100 + i % 2), (q.EV_SYN, 0, 0)) for i, x in enumerate(xs)]
    batches += [events((q.EV_ABS, q.ABS_PRESSURE, 0), (q.EV_KEY, q.BTN_TOUCH, 0), (q.EV_SYN, 0, 0))]
    assert (cmd, data) == ('sh', q.event_writer('/dev/in', batches, 20000).encode()) and inputs.kinds == ['wheel']


def test_input_wheel_step(inputs):
    q.input_action('wheel', ['-1000', '--step-ms', '0'])
    q.input_action('wheel', ['1000', '--step-ms', '1000'])
    assert inputs.scripts[0][1].count(b'usleep') == 0 and b'usleep 1000000' in inputs.scripts[1][1]


def test_input_wheel_zero_is_nothing(inputs):
    q.input_action('wheel', ['0'])
    assert not inputs.scripts and not inputs.kinds


@pytest.mark.parametrize('args,message', [
    (['1', '--fast', '2'], r'^usage: wheel N \[--step-ms MS\]$'),
    (['x'], r'^wheel travel and --step-ms must be integers$'),
    (['1', '--step-ms', 'x'], r'^wheel travel and --step-ms must be integers$'),
    (['1', '--step-ms', '-1'], r'^--step-ms must be 0..1000$'),
    (['1', '--step-ms', '1001'], r'^--step-ms must be 0..1000$'),
    (['1001'], r'^wheel travel must be -1000..1000 ticks$'),
    (['-1001'], r'^wheel travel must be -1000..1000 ticks$'),
])
def test_input_wheel_errors(inputs, args, message):
    with pytest.raises(ValueError, match=message):
        q.input_action('wheel', args)
    assert not inputs.scripts


def test_input_unknown_kind_writes_nothing(inputs):
    with contextlib.suppress(ValueError):  # refused or ignored, but never written to an event node
        q.input_action('pinch', ['1', '2'])
    assert not inputs.scripts and not inputs.kinds


class Main:
    """main()'s world: every action it can take, recorded."""
    def __init__(self, monkeypatch):
        self.calls = []
        for name, reply in (('screenshot', None), ('input_action', None), ('pull', None), ('install', None),
                            ('reboot', None), ('profile', None), ('push', 'md5'), ('wake', True),
                            ('state', {'a': 1, 'b': None}), ('find', ['1.1.1.1', '2.2.2.2']),
                            ('devices', [{'event': 1, 'name': 'n', 'handlers': 'h', 'KEY': 0b101, 'ABS': 0b10}])):
            monkeypatch.setattr(q, name, self.recorder(name, reply))
        monkeypatch.setattr(q, 'run', self.recorder('run', (b'out', 3)))
        monkeypatch.setattr(q, 'shell', self.recorder('shell', b''))

    def recorder(self, name, reply):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return reply
        return record


@pytest.fixture
def world(monkeypatch):
    return Main(monkeypatch)


@pytest.mark.parametrize('argv', [[], ['-h'], ['--help']])
def test_main_help(world, argv, capsys):
    assert q.main(argv) == 0
    assert capsys.readouterr().out == q.__doc__ + '\n' and not world.calls


def test_main_sh(world, capsys):
    assert q.main(['sh', 'cmd']) == 3
    assert world.calls == [('run', ('cmd', q.TIMEOUT), {})] and capsys.readouterr().out == 'out'


def test_main_sh_timeout(world):
    assert q.main(['sh', '--timeout', '2.5', 'cmd']) == 3
    assert q.main(['sh', '--timeout', '.5', 'cmd']) == 3
    assert world.calls == [('run', ('cmd', 2.5), {}), ('run', ('cmd', .5), {})]


@pytest.mark.parametrize('seconds', ['0', '-1'])
def test_main_sh_bad_timeout(world, seconds):
    with pytest.raises(ValueError, match=r'^--timeout must be positive$'):
        q.main(['sh', '--timeout', seconds, 'cmd'])


def test_main_sh_empty(world):
    with pytest.raises(ValueError, match=r'^sh requires a non-empty command$'):
        q.main(['sh', ''])


@pytest.mark.parametrize('argv,expected', [
    (['shot', 'a.png'], ('screenshot', ('a.png', False), {})),
    (['shot', '--settle', 'a.png'], ('screenshot', ('a.png', True), {})),
    (['tap', '1', '2'], ('input_action', ('tap', ['1', '2']), {})),
    (['key', 'play'], ('input_action', ('key', ['play']), {})),
    (['key', 'play', '5'], ('input_action', ('key', ['play', '5']), {})),
    (['swipe', '1', '2', '3', '4'], ('input_action', ('swipe', ['1', '2', '3', '4']), {})),
    (['swipe', '1', '2', '3', '4', '5'], ('input_action', ('swipe', ['1', '2', '3', '4', '5']), {})),
    (['wheel', '1'], ('input_action', ('wheel', ['1']), {})),
    (['wheel', '1', '--step-ms', '2'], ('input_action', ('wheel', ['1', '--step-ms', '2']), {})),
    (['pull', 'a', 'b'], ('pull', ('a', 'b'), {})),
    (['reboot'], ('reboot', (False,), {})),
    (['reboot', '--wait'], ('reboot', (True,), {})),
    (['log', '--clear'], ('shell', (': >/tmp/q2-demo.log',), {})),
])
def test_main_dispatch(world, argv, expected, capsys):
    assert q.main(argv) == 0
    assert world.calls == [expected] and capsys.readouterr().out == ''


@pytest.mark.parametrize('argv', [
    ['shot'], ['shot', '--fast', 'a.png'], ['shot', 'a', 'b', 'c'], ['state', 'x'], ['wake', 'x'], ['devices', 'x'],
    ['push', 'a'], ['pull', 'a'], ['install'], ['install', 'a', 'b'], ['log', 'a', 'b'], ['find', 'x'],
    ['reboot', '--now'], ['prof'], ['prof', 'x'], ['events'], ['events', '1', '2', '3'], ['nothing'], ['sh'],
    ['sh', '--timeout', '5'], ['sh', 'a', 'b'],
])
def test_main_usage(world, argv):
    with pytest.raises(ValueError, match=r'^usage: q2remote.py \{sh '):
        q.main(argv)
    assert not world.calls


@pytest.mark.parametrize('argv', [['key'], ['key', 'a', 'b', 'c'], ['tap', '1'], ['tap', '1', '2', '3'], ['swipe', '1', '2', '3'],
                                  ['swipe', '1', '2', '3', '4', '5', '6'], ['wheel'], ['wheel', '1', '2']])
def test_main_input_argument_count(world, argv):
    with pytest.raises(ValueError, match=f'^invalid arguments for {argv[0]}$'):
        q.main(argv)


def test_main_state(world, capsys):
    assert q.main(['state']) == 0
    assert capsys.readouterr().out == 'a=1 b=None\n'


def test_main_wake(world, capsys, monkeypatch):
    assert q.main(['wake']) == 0
    assert capsys.readouterr().out == 'woke the screen\n'
    monkeypatch.setattr(q, 'wake', lambda: False)
    assert q.main(['wake']) == 0
    assert capsys.readouterr().out == 'the screen is already on\n'


def test_main_devices(world, capsys):
    assert q.main(['devices']) == 0
    assert capsys.readouterr().out == 'event1 n handlers=h keys=[0, 2] abs=[1]\n'


def test_main_push(world, capsys):
    assert q.main(['push', 'a', 'b']) == 0
    assert world.calls == [('push', ('a', 'b'), {})] and capsys.readouterr().out == 'md5\n'


def test_main_install(world, capsys):
    assert q.main(['install', 'a']) == 0
    assert world.calls == [('install', ('a',), {})]
    assert capsys.readouterr().out == 'update.tar is on the card: System settings → System Update → TF card update\n'


def test_main_log_tail(world, capsys):
    assert q.main(['log']) == 3
    assert q.main(['log', '5']) == 3
    assert [c[1] for c in world.calls] == [('tail -n 100 /tmp/q2-demo.log',), ('tail -n 5 /tmp/q2-demo.log',)]
    assert capsys.readouterr().out == 'outout'


def test_main_log_follow(world):
    assert q.main(['log', '-f']) == 0
    name, args, kwargs = world.calls[0]
    assert (name, args) == ('shell', ('tail -f /tmp/q2-demo.log',)) and kwargs == dict(timeout=None, sink=sys.stdout.buffer)


def test_main_log_junk(world):
    with pytest.raises(ValueError, match=r'^usage: log \[LINES\|-f\|--clear\]$'):
        q.main(['log', 'x'])


def test_main_find(world, capsys):
    assert q.main(['find']) == 0
    assert capsys.readouterr().out == '1.1.1.1\n2.2.2.2\n'


def test_main_prof(world, monkeypatch):
    assert q.main(['prof', '1.5']) == 0
    assert q.main(['prof', '2', '--all']) == 0
    assert q.main(['prof', '3', '--build', 'B']) == 0
    assert q.main(['prof', '4', '--build', 'B', '--all']) == 0
    assert q.main(['prof', '5', '--bogus']) == 0
    monkeypatch.setenv('Q2_BUILD', 'ENV')
    assert q.main(['prof', '6']) == 0
    assert [c[1] for c in world.calls] == [(1.5, False, None), (2.0, True, None), (3.0, False, 'B'), (4.0, True, 'B'),
                                           (5.0, False, None), (6.0, False, 'ENV')]


def test_main_prof_one_point_only(world):
    with pytest.raises(ValueError, match='^usage:'):
        q.main(['prof', '1.2.3'])


def test_main_events(world, monkeypatch, capsys):
    fake = Shell(q.event(1, 30, 1), b'')
    fake.replies[0] = struct.pack('<IIHHi', 5, 7, 1, 30, 1)
    monkeypatch.setattr(q, 'shell', fake)
    assert q.main(['events', '2']) == 0
    assert capsys.readouterr().out == '5.000007 type=1 code=30 value=1\n'
    assert fake.calls[0].cmd == 'cat /dev/input/event2 2>/dev/null & p=$!; sleep 5; kill $p' and fake.calls[0].timeout == q.TIMEOUT + 5
    assert q.main(['events', '0', '3600']) == 0
    assert fake.calls[1].cmd == 'cat /dev/input/event0 2>/dev/null & p=$!; sleep 3600; kill $p'
    assert q.main(['events', '0', '.5']) == 0


@pytest.mark.parametrize('args,message', [
    (['x'], r'^events requires an integer event number and seconds$'),
    (['1', 'x'], r'^events requires an integer event number and seconds$'),
    (['-1'], r'^events requires EVENT>=0 and 0<S<=3600$'),
    (['1', '0'], r'^events requires EVENT>=0 and 0<S<=3600$'),
    (['1', '3601'], r'^events requires EVENT>=0 and 0<S<=3600$'),
])
def test_main_events_errors(world, args, message):
    with pytest.raises(ValueError, match=message):
        q.main(['events', *args])
    assert not world.calls
