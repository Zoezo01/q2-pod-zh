#!/usr/bin/env python3
"""Bluetooth's resampler across a gapless change (patch/peq_player.c rs_open): the built player's
lavcresample and libswresample under Unicorn.

A 220 Hz tone at 96 kHz split across two files comes out of two instances exactly as one instance
plays it whole when the first ended near its track's end; stock's (and a skip mid-track, a rate
change or a pause) start the second from a fresh resampler, as stock, which loses the first's tail.
Usage: test/bt_resample.py BUILD_DIR"""
import math, pathlib, struct, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import fileoff, load_elf, symbols
from peq import RS_OPEN_SLOT, RS_OPEN
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

def instance(rate, open_fn):
    """A resampler set up for rate, 16-bit stereo in, 48 kHz out, as af_init's negotiation leaves it."""
    af = alloc(64)
    w(af, INFO)
    assert call(open_fn, af) == 1
    w(r(af + 0x14) + 8, 48000)
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

def change(first, second, rate2=96000, ending=1, pause=0, open_fn=ps['rs_open']):
    """first through one instance, then second through the next one, as at a track change."""
    old = instance(96000, open_fn)
    got = play(old, first)
    u.mem_write(ps['rs_end'], struct.pack('<q', clock[0] * ending))  # the last block near its end
    call(r(old + 8), old)
    new = instance(rate2, open_fn)
    clock[0] += pause
    return got, play(new, second)

pcm = tone(96000, 16384)
whole = play(instance(96000, ps['rs_open']), pcm)
first, second = pcm[:8 * 4096], pcm[8 * 4096:]
fresh = play(instance(96000, ps['rs_open']), second)

# The fix: a continuous change plays what one instance plays.
x, y = change(first, second)
assert x + y == whole, (len(x + y), len(whole))
# Stock's own open: the first file's tail never plays and the second arrives early.
x, y = change(first, second, open_fn=RS_OPEN)
lost = (len(whole) - len(x + y)) // 4
assert 8 <= lost <= 12 and y == fresh and x == whole[:len(x)], lost
# A skip mid-track (or a seek), a pause past CARRY_STALE_NS, or another rate starts fresh, as stock.
assert change(first, second, ending=0)[1] == fresh
assert change(first, second, pause=4 * 10**9)[1] == fresh
other = tone(44100, 8192)
assert change(first, other, rate2=44100)[1] == play(instance(44100, ps['rs_open']), other)
print(f'Bluetooth resampler: a gapless change is sample-continuous (stock loses {lost} frames at 48 kHz); '
      'a skip, pause or rate change starts fresh.')
