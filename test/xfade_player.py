#!/usr/bin/env python3
"""Crossfade in the built player (patch/peq_player.c crossfade) under Unicorn: the MIPS build of
the filter, as test/peq.py's crossfade_check drives the host build.

A track's last seconds are held back (half of each block put out once the hold window opens) and
mixed, equal power, under the next instance's first frames, at that track's rate; no fade asked
plays every block whole.
Usage: test/xfade_player.py BUILD_DIR"""
import math, pathlib, re, struct, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import fileoff, load_elf, symbols
from peq import EQ_OPEN_SLOT
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE
from unicorn.mips_const import *

out = pathlib.Path(sys.argv[1])/'peq'
raw = (out/'hciplayer').read_bytes()
ps = symbols(out/'peq.elf')
assert struct.unpack_from('<I', raw, fileoff(raw, EQ_OPEN_SLOT))[0] == ps['peq_open']
DONE, STUBS, HEAP, STACK = 0x2000000, 0x2800000, 0x3000000, 0x40f0000
WRITTEN_PTS, LENGTH, MPCTX = 0x44641c, 0xb10e84, 0xaf9e30  # peq_player.c decoded(), LENGTH, MPCTX
NEXT_FLAG = 0xb10e6c  # up at a skip's uninit, not at a file's end

u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
u.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
load_elf(u, raw)
for base, size in ((DONE, 0x10000), (STUBS, 0x1000), (HEAP, 0x1000000), (STACK - 0xf0000, 0x100000)):
    u.mem_map(base, size)
u.reg_write(UC_MIPS_REG_CP0_STATUS, u.reg_read(UC_MIPS_REG_CP0_STATUS) | 1 << 29 | 1 << 26)  # CU1, FR
imports = {}  # each peq_lib_* stub's GOT word (peq_imports.S) points at a stub of our own
for name, hi, lo in re.findall(r'peq_lib_(\w+):\nlui \$gp.*\nori \$gp.*\nlui \$t8, (\d+)\nori \$t8, \$t8, (\d+)', (out/'peq_imports.S').read_text()):
    imports[STUBS + 4*len(imports)] = name
    u.mem_write(int(hi) << 16 | int(lo), struct.pack('<I', STUBS + 4*(len(imports)-1)))
assert len(imports) > 60, 'no peq_lib_* stubs found in peq_imports.S'
heap, clock, pts = [HEAP], [10**9], [-1.0]

def w(p, *v): u.mem_write(p, struct.pack('<' + 'I'*len(v), *v))
def r(p): return struct.unpack('<I', u.mem_read(p, 4))[0]
def a(n): return u.reg_read((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)[n])
def f12(): return struct.unpack('<d', struct.pack('<Q', u.reg_read(UC_MIPS_REG_F12)))[0]
def f12s(): return struct.unpack('<f', struct.pack('<I', u.reg_read(UC_MIPS_REG_F12) & 0xffffffff))[0]
def ret_d(x): u.reg_write(UC_MIPS_REG_F0, struct.unpack('<Q', struct.pack('<d', x))[0])
def ret_f(x): u.reg_write(UC_MIPS_REG_F0, struct.unpack('<I', struct.pack('<f', x))[0])
def alloc(n, align=16):
    p = (heap[0] + align - 1) & ~(align - 1)
    heap[0] = p + n
    u.mem_write(p, bytes(n))
    return p
def cstr(p): return bytes(u.mem_read(p, 256)).split(b'\0')[0]
flag = alloc(4)  # /tmp/q2xfade, demo's int
w(LENGTH, 10)
sh = alloc(16)  # sh_audio with no codec: not DSD
mp = alloc(64)
w(mp + 0x2c, sh), w(mp + 0x34, sh)
w(MPCTX, mp)

def libc(u, address, size, _):
    """What the filter calls: allocation, strings, libm, the clock and demo's shared ints."""
    n, result = imports[address], 0
    if n in ('malloc', 'calloc'): result = alloc(a(0) * (a(1) if n == 'calloc' else 1))
    elif n == 'free': pass
    elif n in ('memcpy', 'memmove'): u.mem_write(a(0), bytes(u.mem_read(a(1), a(2)))); result = a(0)
    elif n == 'memset': u.mem_write(a(0), bytes([a(1) & 255]) * a(2)); result = a(0)
    elif n == 'memcmp':
        x, y = bytes(u.mem_read(a(0), a(2))), bytes(u.mem_read(a(1), a(2)))
        result = (x > y) - (x < y)
    elif n == 'strcmp': x, y = cstr(a(0)), cstr(a(1)); result = (x > y) - (x < y)
    elif n == 'strlen': result = len(cstr(a(0)))
    elif n == 'clock_gettime': w(a(1), clock[0] // 10**9, clock[0] % 10**9)
    elif n == 'fopen': result = 0  # no saved EQ: the defaults, bypass
    elif n == 'open': result = 5 if cstr(a(0)) == b'/tmp/q2xfade' else -1
    elif n == 'mmap64': result = flag
    elif n == 'close': pass
    elif n in ('sinf', 'cosf'): ret_f(getattr(math, n[:3])(f12s()))
    elif n in ('sin', 'cos', 'exp', 'log', 'sqrt'): ret_d(getattr(math, n)(f12()))
    elif n == 'pow': ret_d(f12() ** struct.unpack('<d', struct.pack('<Q', u.reg_read(UC_MIPS_REG_F14)))[0])
    else: raise AssertionError(n)
    u.reg_write(UC_MIPS_REG_V0, result & 0xffffffff)
    u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
u.hook_add(UC_HOOK_CODE, libc, begin=STUBS, end=STUBS + 0xfff)
def written_pts(u, address, size, _):
    ret_d(pts[0])
    u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
u.hook_add(UC_HOOK_CODE, written_pts, begin=WRITTEN_PTS, end=WRITTEN_PTS)

def call(fn, *args):
    for i, value in enumerate(args): u.reg_write((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)[i], value)
    u.reg_write(UC_MIPS_REG_SP, STACK), u.reg_write(UC_MIPS_REG_RA, DONE)
    u.emu_start(fn, DONE, count=100_000_000)
    return u.reg_read(UC_MIPS_REG_V0)

def track(rate, nch=2):
    """A filter instance negotiated for float nch channels at rate, as the player adds one per file."""
    af, data = alloc(48), alloc(24)
    assert call(ps['peq_open'], af) == 1
    w(data, 0, 0, rate, nch, 0x1d, 4)
    assert call(r(af + 4), af, 0x10000100, data) == 1
    return af

BUF, DATA, MAX = alloc(8 * 4096), alloc(24), 4096
def block(af, frames, n, level, rate):
    """One stereo block: channel 0 a ramp from n (the track's frame count), channel 1 level. Returns
    the frames put out and them as (ch0, ch1) pairs."""
    pcm = []
    for i in range(frames): pcm += [float(n + i), level]
    u.mem_write(BUF, struct.pack(f'<{2 * frames}f', *pcm))
    pts[0] = (n + frames) / rate
    w(DATA, BUF, frames * 8, rate, 2, 0x1d, 4)
    assert call(r(af + 12), af, DATA) == DATA and r(DATA) == BUF and r(DATA + 4) % 8 == 0
    k = r(DATA + 4) // 8
    got = struct.unpack(f'<{2 * k}f', u.mem_read(BUF, 8 * k))
    return k, list(zip(got[::2], got[1::2]))
def wide(af, frames, n, rate, nch):
    """One block of nch channels, each the ramp from n: the frames put out and the samples."""
    u.mem_write(BUF, struct.pack(f'<{nch * frames}f', *[float(n + i) for i in range(frames) for _ in range(nch)]))
    pts[0] = (n + frames) / rate
    w(DATA, BUF, frames * 4 * nch, rate, nch, 0x1d, 4)
    assert call(r(af + 12), af, DATA) == DATA and r(DATA) == BUF
    k = r(DATA + 4) // (4 * nch)
    return k, struct.unpack(f'<{nch * k}f', u.mem_read(BUF, 4 * nch * k))
def uninit(af): call(r(af + 8), af)
def skipped(af):
    """The instance freed by a press: the next flag up while it goes, as the idle loop clears it later."""
    w(NEXT_FLAG, 1), uninit(af), w(NEXT_FLAG, 0)

def hold(rate, length, fade_ms, start, block_frames=1024):
    """The outgoing track from start seconds to its end: frames put out, in order, and the held count."""
    w(flag, fade_ms), w(LENGTH, length)
    af = track(rate)
    n = out = start * rate
    window = length - 2.0 * fade_ms / 1000 - 2.0
    while n < length * rate:
        k, got = block(af, block_frames, n, 1.0, rate)
        if (n + block_frames) / rate <= window: assert k == block_frames, (n, k)
        for c0, c1 in got: assert c0 == float(out) and c1 == 1.0, (out, c0, c1); out += 1
        n += block_frames
    uninit(af)
    return out, n - out

def mix(rate, held, out, seconds=2, block_frames=1024, step=1.0):
    """The next track's first seconds: the tail (step held frames a frame) under it, equal power, then
    alone, every frame out."""
    af = track(rate)
    w(flag, 0)  # the next fade switched off must still finish this one
    m = mixed = 0
    while m < seconds * rate:
        k, got = block(af, block_frames, m, 0.0, rate)
        assert k == block_frames, (m, k)
        for i, (c0, c1) in enumerate(got):
            if mixed < held:
                p = (mixed + 0.5) / held * 1.5707963
                assert abs(c1 - math.cos(p)) < 1e-4, (mixed, c1, math.cos(p))
                # the ramp at c1's own gain (the rotated gains drift 1e-5 over a long fade), float32
                assert abs(c0 - ((m + i) * math.sqrt(1 - c1 * c1) + (out + mixed * step) * c1)) < 1 + abs(c0) * 1e-6, (mixed, c0)
            else:
                assert c0 == float(m + i) and c1 == 0.0, (m + i, c0, c1)
            mixed += 1
        m += block_frames
    uninit(af)
    assert mixed >= held

# A 1 s fade at the end of a 10 s track: the hold opens 4 s before the end and keeps one second;
# the next track mixes it under its start, from the ring the instances share.
out, held = hold(48000, 10, 1000, 0)
assert held == 48000, held
mix(48000, held, out)
# A real song: 5 s at 44.1 kHz, the hold starting 12 s before the end of 4 minutes.
out, held = hold(44100, 240, 5000, 225)
assert held == 5 * 44100, held
mix(44100, held, out, seconds=6)
# Odd block sizes at the longest fade, 15 s at 48 kHz: the hold wraps the ring.
out, held = hold(48000, 60, 15000, 20, block_frames=1000)
assert held == 15 * 48000, held
mix(48000, held, out, seconds=16, block_frames=333)
# The next track at another rate: the 48 kHz tail plays over 44100 frames at 44.1 kHz, interpolated.
out, held = hold(48000, 10, 1000, 0)
mix(44100, 44100, out, step=48000 / 44100)
def alone(rate):
    """The next track with nothing under it: its first block whole."""
    af = track(rate)
    k, got = block(af, 1024, 0, 0.0, rate)
    assert k == 1024 and got[0] == (0.0, 0.0) and got[1023] == (1023.0, 0.0), (got[0], got[1023])
    uninit(af)
STOP = 1 << 16  # demo's count of stops, above the fade's ms (crossfade.c xfade_poll)
# A stop since the hold began drops the tail, however soon the next track comes.
out, held = hold(48000, 10, 1000, 0)
w(flag, STOP)
alone(48000)
# So does a stop in the hold, before the track's end.
w(flag, 1000), w(LENGTH, 10)
af = track(48000)
for n in range(0, 8 * 48000, 1024): block(af, 1024, n, 1.0, 48000)
uninit(af)
w(flag, 1000 | STOP)
alone(48000)
# A tail held to its track's end, with no stop, waits out a cold start: stock Shuffle's, a declined
# or failed preload, a slow card. Every frame is mixed, at 2 s, at 29 s; at 31 s it is dropped.
for wait in (2, 3.2, 29):
    out, held = hold(48000, 10, 1000, 0)
    assert held == 48000
    clock[0] += int(wait * 10**9)
    mix(48000, held, out)
out, held = hold(48000, 10, 1000, 0)
clock[0] += 31 * 10**9
alone(48000)
# A tail cut short (Pause in the hold, then Next long after) still goes after 3 s, even paused in the
# decoder's last 1.5 s, where a position test can't tell it from an end; Next at once fades what is held
# so far under the chosen track, as it always did; a pause alone only waits.
def cut_short(fade=1000, length=10):
    w(flag, fade), w(LENGTH, length)
    af, n, out = track(48000), 0, 0
    while n < (length - 1.5) * 48000 - 1024:
        k, got = block(af, 1024, n, 1.0, 48000)
        out += k; n += 1024
    return af, n, out
af, n, out = cut_short()
skipped(af)
clock[0] += 4 * 10**9
alone(48000)
w(flag, 1000), w(LENGTH, 10)
af, n = track(48000), 0
while n < 9.2 * 48000:
    block(af, 1024, n, 1.0, 48000); n += 1024
clock[0] += 25 * 10**9
skipped(af)
alone(48000)
af, n, out = cut_short()
skipped(af)
mix(48000, n - out, out)
# A file shorter than its length says ends by itself far from it: still a cold start's wait.
af, n, out = cut_short()
uninit(af)
clock[0] += 4 * 10**9
mix(48000, n - out, out)
af, n, out = cut_short()
clock[0] += 3600 * 10**9  # a pause in the hold
while n < 10 * 48000:
    k, got = block(af, 1024, n, 1.0, 48000)
    for c0, c1 in got: assert c0 == float(out), (out, c0); out += 1
    n += 1024
uninit(af)
assert n - out == 48000
mix(48000, n - out, out)
# Demo's flag clearing in the hold (Crossfade off, a queue or mode change to a same-album next, Gapless
# off) finishes the hold: no jump, every frame in order, what is held mixed. Early in the hold too, where
# the position is before any shorter fade's window; a shorter fade the same.
for clear, then in ((50, 0), (47, 0), (50, 1000)):
    w(flag, 5000), w(LENGTH, 60)
    af, n, out = track(44100), 40 * 44100, 40 * 44100
    while n < 60 * 44100:
        if n >= clear * 44100 and n - 1024 < clear * 44100:
            w(flag, then); mark = n - out
        k, got = block(af, 1024, n, 1.0, 44100)
        for c0, c1 in got: assert c0 == float(out), (clear, then, out, c0); out += 1
        n += 1024
    uninit(af)
    assert n - out >= mark and (then or n - out == mark), (n - out, mark)
    mix(44100, n - out, out, seconds=int((n - out) / 44100) + 1)
# A Play Next changes demo's answer mid-track. Asked before the window, the whole fade; inside it, half of
# what is left; asked after the last block, or taken back before the window, a plain join, untouched.
for begin, at, then, want in ((0, 10, 5000, 5), (0, 24, 5000, 3), (0, 30, 5000, 0), (5000, 10, 0, 0)):
    w(flag, begin), w(LENGTH, 30)
    af, n, out = track(44100), (at - 2) * 44100, (at - 2) * 44100
    while n < 30 * 44100:
        if n >= at * 44100 and n - 1024 < at * 44100: w(flag, then)
        k, got = block(af, 1024, n, 1.0, 44100)
        for c0, c1 in got: assert c0 == float(out), (at, out, c0); out += 1
        n += 1024
    uninit(af)
    assert abs((n - out) / 44100 - want) < 0.05, (begin, at, n - out)
    if want: mix(44100, n - out, out, seconds=want + 1)
    else:
        w(flag, 0); af = track(44100); k, got = block(af, 1024, 0, 0.25, 44100); uninit(af)
        assert k == 1024 and got[5] == (5.0, 0.25), (begin, at, got[5])
# A track shorter than its hold: half of it held, none of it lost.
out, held = hold(48000, 3, 5000, 0)
assert out + held >= 3 * 48000 and held > 48000
mix(48000, held, out, seconds=3)
# The ring's cap: a 15 s fade at 192 kHz holds what fits, 3.75 s, and all of it mixes.
out, held = hold(192000, 60, 15000, 45, block_frames=4096)
assert held == (786432 - 65536), held
mix(192000, held, out, seconds=4, block_frames=4096)
# Mid-mix, a 48 kHz tail under a 44.1 kHz track: its own track paused past 3 s mixes on where it
# was, and so does a skip to another track at that rate; another rate (48 kHz, the tail's own) takes
# the rest retimed; a stop and another track 4 s on drops what is left.
def mixing(af, m):
    k, got = block(af, 1024, m, 0.0, 44100)
    assert k == 1024 and abs(got[0][1] - math.cos((m + 0.5) / 44100 * 1.5707963)) < 1e-4, (m, got[0])
for then in ('rate', 'stop', 'skip'):
    hold(48000, 10, 1000, 0)
    af = track(44100)
    w(flag, 0)
    mixing(af, 0)
    clock[0] += 4 * 10**9  # a pause
    mixing(af, 1024)
    uninit(af)
    if then == 'rate':  # the rest retimed, at the angle it had reached: a track shorter than the fade
        af = track(48000)
        k, got = block(af, 1024, 0, 0.0, 48000)
        assert k == 1024 and abs(got[0][1] - math.cos(2048 / 44100 * 1.5707963)) < 1e-3, got[0]
        m = 1024
        while m < 48000:
            block(af, 1024, m, 0.0, 48000); m += 1024
        uninit(af)
        alone(48000)
    elif then == 'skip':
        af = track(44100)
        mixing(af, 2048)
        uninit(af)
        clock[0] += 4 * 10**9
        alone(44100)
    else:
        clock[0] += 4 * 10**9 * (then == 'stop')
        alone(44100)
# Stereo into mono: each held frame's mean under the mono track, every frame. A 10 s hold at 48 kHz starts
# 12 s into the ring and runs past its end, so the mix's reads wrap at the stereo ring's size.
for length, fade, start, seconds in ((10, 1000, 0, 2), (60, 10000, 30, 11)):
    out, held = hold(48000, length, fade, start, block_frames=1000)
    af = track(48000, 1)
    m = 0
    while m < seconds * 48000:
        k, got = wide(af, 1024, m, 48000, 1)
        assert k == 1024
        for i in range(k):
            p = (m + i + 0.5) / held * 1.5707963
            want = (m + i) * math.sin(p) + (out + m + i + 1.0) / 2 * math.cos(p) if m + i < held else m + i
            assert abs(got[i] - want) < 2 + abs(want) * 3e-5, (m + i, got[i], want)
        m += 1024
    uninit(af)
# Mono into stereo: each held frame on both sides.
w(flag, 1000), w(LENGTH, 10)
af, n, out = track(48000, 1), 0, 0
while n < 10 * 48000:
    k, got = wide(af, 1024, n, 48000, 1)
    for x in got: assert x == float(out), (out, x); out += 1
    n += 1024
uninit(af)
held, af, m = n - out, track(48000), 0
assert held == 48000
while m < 2 * 48000:
    k, got = block(af, 1024, m, 0.0, 48000)
    for i, (c0, c1) in enumerate(got):
        p = (m + i + 0.5) / held * 1.5707963
        tail = (out + m + i) * math.cos(p) if m + i < held else 0.0
        assert abs(c1 - tail) < 2 + tail * 3e-5, (m + i, c1, tail)
        assert abs(c0 - ((m + i) * (math.sin(p) if m + i < held else 1.0) + tail)) < 2 + abs(c0) * 3e-5, (m + i, c0, tail)
    m += 1024
uninit(af)
# DSD going out (dsd(): the codec's driver): every block untouched with a fade asked, nothing held, so
# the next track plays alone.
codec, drv = alloc(0x400), alloc(16)
u.mem_write(drv, b'dsdiff\0')
w(codec + 0x3e0, drv)
def dsd_track():
    w(sh + 4, codec)
    af, data = alloc(48), alloc(24)
    assert call(ps['peq_open'], af) == 1
    w(data, 0, 0, 88200, 2, 0x19, 4)
    assert call(r(af + 4), af, 0x10000100, data) == 1
    w(sh + 4, 0)
    words = struct.pack('<2048i', *range(2048))
    for n in range(0, 10 * 88200, 88200):
        u.mem_write(BUF, words)
        pts[0] = (n + 1024) / 88200
        w(DATA, BUF, len(words), 88200, 2, 0x19, 4)
        assert call(r(af + 12), af, DATA) == DATA and bytes(u.mem_read(BUF, len(words))) == words and r(DATA + 4) == len(words)
    uninit(af)
w(flag, 1000), w(LENGTH, 10)
dsd_track()
w(flag, 0)
alone(44100)
# A tail with DSD after it (stock Shuffle's refill draw) has nothing to mix under, and is gone: the
# track after the DSD one, seconds on, plays alone.
hold(48000, 10, 1000, 0)
dsd_track()
clock[0] += 5 * 10**9
alone(48000)
# A stop mid-mix, seen by the count, drops what is left, however soon the next track comes.
hold(48000, 10, 1000, 0)
af = track(48000)
w(flag, 0)
k, got = block(af, 1024, 0, 0.0, 48000)
assert got[0][1] > 0.99
uninit(af)
w(flag, STOP)
alone(48000)
w(flag, 0)
# No fade asked: every block whole.
w(flag, 0), w(LENGTH, 10)
af = track(48000)
for n in range(0, 10 * 48000, 1024):
    k, got = block(af, 1024, n, 1.0, 48000)
    assert k == 1024 and got[0] == (float(n), 1.0)
uninit(af)
print('Player crossfade (MIPS build): the hold window, half-rate emission in order, the equal-power '
      'mix at the same and another rate and between mono and stereo, after a cold start, through a pause and a skip, '
      'a hold finished after the flag clears, the ring cap, a short track, DSD out, the stop, stale and mid-mix '
      'drops and whole blocks with no fade passed.')
