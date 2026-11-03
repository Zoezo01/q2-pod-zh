#!/usr/bin/env python3
"""Bluetooth's resampler across a gapless change (patch/peq_player.c rs_open): the built player's
lavcresample and libswresample under Unicorn.

A tone split across two files comes out of two instances exactly as one instance plays it whole
at AAC/SBC's 48 kHz and LDAC's 96 kHz. Stock's (and a skip mid-track, a rate change or a pause)
start the second from a fresh resampler, which loses the first's tail.
Then a USB DAC's init across a change (ao_init): a repeated request asks for what it negotiated.
Usage: test/bt_resample.py BUILD_DIR"""
import math, pathlib, struct, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import fileoff, load_elf, symbols
from peq import RS_OPEN_SLOT, RS_OPEN, AO_INIT_SLOT, AO_INIT
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE
from unicorn.mips_const import *

out = pathlib.Path(sys.argv[1])/'peq'
raw = (out/'hciplayer').read_bytes()
ps = symbols(out/'peq.elf')
assert struct.unpack_from('<I', raw, fileoff(raw, RS_OPEN_SLOT))[0] == ps['rs_open']
DONE, STUBS, HEAP, STACK, INFO = 0x2000000, 0x2800000, 0x3000000, 0x40f0000, RS_OPEN_SLOT - 20

u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
u.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
load_elf(u, raw)
for base, size in ((DONE, 0x10000), (STUBS, 0x1000), (HEAP, 0x1000000), (STACK - 0xf0000, 0x100000)):
    u.mem_map(base, size)
u.reg_write(UC_MIPS_REG_CP0_STATUS, u.reg_read(UC_MIPS_REG_CP0_STATUS) | 1 << 29 | 1 << 26)  # CU1, FR
imports = {}
for line in subprocess.check_output(['readelf', '-rW', out/'hciplayer'], text=True).splitlines():
    s = line.split()
    if len(s) >= 5 and s[2] == 'R_MIPS_JUMP_SLOT':
        imports[STUBS + 4*len(imports)] = s[4].split('@')[0]
        u.mem_write(int(s[0], 16), struct.pack('<I', STUBS + 4*(len(imports)-1)))
heap, clock, freed = [HEAP], [10**9], set()

def w(p, *v): u.mem_write(p, struct.pack('<' + 'I'*len(v), *v))
def r(p): return struct.unpack('<I', u.mem_read(p, 4))[0]
def a(n): return u.reg_read((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)[n])
def f12(): return struct.unpack('<d', struct.pack('<Q', u.reg_read(UC_MIPS_REG_F12)))[0]
def alloc(n, align=16):
    p = (heap[0] + align - 1) & ~(align - 1)
    heap[0] = p + n
    u.mem_write(p, bytes(n))
    return p
def cstr(p): return bytes(u.mem_read(p, 256)).split(b'\0')[0]
MATH = {'sin': math.sin, 'cos': math.cos, 'exp': math.exp, 'floor': math.floor, 'ceil': math.ceil,
        'sqrt': math.sqrt, 'fabs': abs, 'log': math.log}

def libc(u, address, size, _):
    """What libswresample and the payload call: allocation, strings, libm and the clock."""
    n, v1 = imports[address], None
    if n in ('malloc', 'calloc'): result = alloc(a(0) * (a(1) if n == 'calloc' else 1))
    elif n == 'posix_memalign': w(a(0), alloc(a(2), a(1))); result = 0
    elif n == 'free': assert not a(0) or a(0) not in freed, 'double free'; freed.add(a(0)); result = 0
    elif n in ('memcpy', 'memmove'): u.mem_write(a(0), bytes(u.mem_read(a(1), a(2)))); result = a(0)
    elif n == 'memset': u.mem_write(a(0), bytes([a(1) & 255]) * a(2)); result = a(0)
    elif n == 'memcmp':
        x, y = bytes(u.mem_read(a(0), a(2))), bytes(u.mem_read(a(1), a(2)))
        result = (x > y) - (x < y)
    elif n == 'strcmp': x, y = cstr(a(0)), cstr(a(1)); result = (x > y) - (x < y)
    elif n == 'vsnprintf': result = 0  # av_log
    elif n == 'clock_gettime': w(a(1), clock[0] // 10**9, clock[0] % 10**9); result = 0
    elif n == 'lrintf': result = round(struct.unpack('<f', struct.pack('<I', u.reg_read(UC_MIPS_REG_F12) & 0xffffffff))[0])
    elif n == 'llrint': result = round(f12()); v1 = result >> 32
    elif n in MATH:
        u.reg_write(UC_MIPS_REG_F0, struct.unpack('<Q', struct.pack('<d', MATH[n](f12())))[0]); result = 0
    elif n == '__ctzdi2': x = a(0) | a(1) << 32; result = (x & -x).bit_length() - 1
    elif n == '__divdi3':
        x, y = (v - (1 << 64) if v >> 63 else v for v in (a(0) | a(1) << 32, a(2) | a(3) << 32))
        result = abs(x) // abs(y) * (1 if (x < 0) == (y < 0) else -1); v1 = result >> 32
    else: raise AssertionError(n)
    u.reg_write(UC_MIPS_REG_V0, result & 0xffffffff)
    if v1 is not None: u.reg_write(UC_MIPS_REG_V1, v1 & 0xffffffff)
    u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
u.hook_add(UC_HOOK_CODE, libc, begin=STUBS, end=STUBS + 0xfff)

def call(fn, *args):
    for i, value in enumerate(args): u.reg_write((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)[i], value)
    u.reg_write(UC_MIPS_REG_SP, STACK), u.reg_write(UC_MIPS_REG_RA, DONE)
    u.emu_start(fn, DONE, count=100_000_000)
    return u.reg_read(UC_MIPS_REG_V0)

def instance(rate, output_rate, open_fn):
    """lavcresample uses S16 stereo; LDAC's following format filter converts that to S32."""
    af = alloc(64)
    w(af, INFO)
    assert call(open_fn, af) == 1
    w(r(af + 0x14) + 8, output_rate)
    data = alloc(24)
    w(data, 0, 0, rate, 2, 9, 2)
    assert call(r(af + 4), af, 0x10000100, data) == 1
    return af

def play(af, pcm, block=4096):
    got = b''
    for at in range(0, len(pcm), block):
        chunk = pcm[at:at + block]
        buf, data = alloc(len(chunk)), alloc(24)
        u.mem_write(buf, chunk)
        w(data, buf, len(chunk), 0, 2, 9, 2)
        result = call(r(af + 12), af, data)
        got += bytes(u.mem_read(r(result), r(result + 4)))
    return got

def tone(rate, frames): return b''.join(struct.pack('<hh', s, s) for s in (int(16000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(frames)))

def change(first, second, rate, output_rate, rate2=None, ending=1, pause=0, open_fn=ps['rs_open']):
    """first through one instance, then second through the next one, as at a track change."""
    old = instance(rate, output_rate, open_fn)
    got = play(old, first)
    u.mem_write(ps['rs_end'], struct.pack('<q', clock[0] * ending))  # the last block near its end
    call(r(old + 8), old)
    new = instance(rate2 or rate, output_rate, open_fn)
    clock[0] += pause
    return got, play(new, second)

for rate, output_rate in ((96000, 48000), (44100, 48000), (44100, 96000), (48000, 96000)):
    pcm = tone(rate, 16384)
    whole = play(instance(rate, output_rate, ps['rs_open']), pcm)
    first, second = pcm[:8 * 4096], pcm[8 * 4096:]
    fresh = play(instance(rate, output_rate, ps['rs_open']), second)

    # A continuous change plays exactly what one instance plays, including its fractional phase.
    x, y = change(first, second, rate, output_rate)
    assert x + y == whole, (rate, output_rate, len(x + y), len(whole))
    # Stock's own open loses the first file's tail and resets the second file's phase.
    x, y = change(first, second, rate, output_rate, open_fn=RS_OPEN)
    lost = (len(whole) - len(x + y)) // 4
    assert lost > 0 and y == fresh and x == whole[:len(x)], (rate, output_rate, lost)
    # A skip, a pause past CARRY_STALE_NS, or another input rate starts fresh.
    assert change(first, second, rate, output_rate, ending=0)[1] == fresh
    assert change(first, second, rate, output_rate, pause=4 * 10**9)[1] == fresh
    rate2 = 88200 if rate == 44100 else 44100
    other = tone(rate2, 8192)
    assert change(first, other, rate, output_rate, rate2=rate2)[1] == play(instance(rate2, output_rate, ps['rs_open']), other)
    print(f'Bluetooth resampler {rate}->{output_rate}: continuous samples across a change '
          f'(stock loses {lost} frames); skip, pause and input-rate change start fresh.')

# A USB DAC's init across a change (patch/peq_player.c ao_init, on ao_alsa's init slot). The stub is
# stock's init as a DAC: it keeps the PCM open when the request equals the saved triple (the last
# request, its rate and format as negotiated), else opens and negotiates, and ao_data holds the result.
assert struct.unpack_from('<I', raw, fileoff(raw, AO_INIT_SLOT))[0] == ps['ao_init']
WAY, PCM, AO_DATA, S16, S24 = 0xb10e54, 0xb115a8, 0xafa1a0, 0x88, 0x90
dac, seen, saved = {}, [], [None]
def stock_init(u, address, size, _):
    asked = (a(0), a(1), a(2))
    seen.append(asked)
    if not (r(PCM) and asked == saved[0]):
        saved[0] = (dac['rate'](asked[0]), asked[1], dac['fmt'](asked[2]))
        dac['opens'] += 1
    w(AO_DATA, *saved[0]), w(PCM, 1)
    u.reg_write(UC_MIPS_REG_V0, 1), u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
u.hook_add(UC_HOOK_CODE, stock_init, begin=AO_INIT, end=AO_INIT)
def plug(rate, fmt):
    """Another DAC: the PCM is closed, as after a stop."""
    dac.update(rate=rate, fmt=fmt, opens=0), w(PCM, 0)
def init(rate, fmt, way=2):
    w(WAY, way)
    assert call(ps['ao_init'], rate, 2, fmt, 0) == 1
    return seen[-1][0], seen[-1][2]
def queue(*tracks):
    """Each track's rate and format as stock's init sees them, and how many opens the queue cost."""
    before = dac['opens']
    return [init(*t) for t in tracks], dac['opens'] - before

# A 48 kHz DAC that takes 24 bits (the report's): a 44.1 kHz album opens once, not per track, and a
# 16-bit track in between neither drops the album to 16 bits nor loses it the match afterwards.
plug(lambda rate: 48000, lambda fmt: fmt)
assert queue((44100, S24), (44100, S24), (44100, S24)) == ([(44100, S24), (48000, S24), (48000, S24)], 1)
assert queue((48000, S16), (44100, S24), (44100, S24)) == ([(48000, S16), (48000, S24), (48000, S24)], 2)
assert queue((48000, S24), (44100, S24)) == ([(48000, S24), (48000, S24)], 0)  # a 44.1/48 mix stays matched
# A 16-bit DAC that takes any rate: the album is not resampled to a rate another track negotiated.
plug(lambda rate: rate, lambda fmt: S16)
assert queue((44100, S24), (44100, S24), (48000, S16), (44100, S24)) == (
    [(44100, S24), (44100, S16), (48000, S16), (44100, S16)], 3)
# A DAC that takes everything (a Modi): every request passes as asked, as stock.
plug(lambda rate: rate, lambda fmt: fmt)
assert queue((44100, S24), (44100, S24), (96000, S24), (44100, S16)) == (
    [(44100, S24), (44100, S24), (96000, S24), (44100, S16)], 3)
# Back on the 48 kHz DAC after a stop, nothing of the other DAC's pair is asked; the other outputs are stock's.
plug(lambda rate: 48000, lambda fmt: fmt)
assert queue((44100, S16), (44100, S16)) == ([(44100, S16), (48000, S16)], 1)
assert init(44100, S24, way=0) == (44100, S24) and init(44100, S24, way=1) == (44100, S24)
print('USB DAC init: an album on a 48 kHz DAC opens the PCM once; other requests, DACs and outputs are stock\'s.')
