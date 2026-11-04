#!/usr/bin/env python3
"""Bluetooth's resampler across a gapless change (patch/peq_player.c rs_open): the built player's
lavcresample and libswresample under Unicorn.

A tone split across two files comes out of two instances exactly as one instance plays it whole
at AAC/SBC's 48 kHz and LDAC's 96 kHz. Stock's (and a skip mid-track, a rate change or a pause)
start the second from a fresh resampler, which loses the first's tail.
Then a USB DAC's and Bluetooth's init across a change (ao_init): a repeated request asks for what it negotiated.
Last the wired outputs' start (dac_*): at stock's own unmute sites only a press waits, once, before audio flows.
Usage: test/bt_resample.py BUILD_DIR"""
import math, pathlib, re, struct, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import fileoff, jal, load_elf, symbols
from peq import RS_OPEN_SLOT, RS_OPEN, AO_INIT_SLOT, AO_INIT, DAC_RELEASE
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
# open isn't a jump slot here: its peq_lib_ stub's GOT word (peq_imports.S), as in xfade_player.py.
hi, lo = re.search(r'peq_lib_open:\nlui \$gp.*\nori \$gp.*\nlui \$t8, (\d+)\nori \$t8, \$t8, (\d+)', (out/'peq_imports.S').read_text()).groups()
imports[STUBS + 4*len(imports)] = 'open'
u.mem_write(int(hi) << 16 | int(lo), struct.pack('<I', STUBS + 4*(len(imports)-1)))
heap, clock, freed, dac_log, pause_rc, press = [HEAP], [10**9], set(), [], [0], [None]  # press: PRESS_FILE's word
real = {}  # imports the stock-init sections answer, by name

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
    if n in real: result = real[n]() or 0
    elif n in ('malloc', 'calloc'): result = alloc(a(0) * (a(1) if n == 'calloc' else 1))
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
    elif n == 'open': assert cstr(a(0)) == b'/tmp/q2press' and not a(1); result = -1 if press[0] is None else 7
    elif n == 'read': assert a(0) == 7 and a(2) == 4; w(a(1), press[0]); result = 4
    elif n == 'close': assert a(0) == 7; result = 0
    elif n == 'ioctl': dac_log.append(('ioctl', a(0), a(1), r(a(2)))); result = 0
    elif n == 'usleep': dac_log.append('wait'); assert 100000 <= a(0) <= 200000, 'the mute line, without a long wait'; result = 0
    elif n == 'snd_pcm_pause': dac_log.append(('pause', a(1))); result = pause_rc[0]
    elif n == 'snd_pcm_state': result = 2  # not suspended
    elif n == 'snd_pcm_status_sizeof': result = 1024  # stock's get_space, from ao_init
    elif n in ('snd_pcm_status', 'snd_pcm_status_get_avail'): result = 0
    elif real: result = 0  # anything else stock's init calls
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
init_hook = u.hook_add(UC_HOOK_CODE, stock_init, begin=AO_INIT, end=AO_INIT)
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
# A pair doesn't outlive its DAC: the new one's fresh open moves it, even taken as asked.
plug(lambda rate: 48000, lambda fmt: fmt); init(44100, S24)
plug(lambda rate: rate, lambda fmt: fmt); init(48000, S24)
assert init(44100, S24) == (44100, S24)
# A DAC that takes everything (a Modi): every request passes as asked, as stock.
plug(lambda rate: rate, lambda fmt: fmt)
assert queue((44100, S24), (44100, S24), (96000, S24), (44100, S16)) == (
    [(44100, S24), (44100, S24), (96000, S24), (44100, S16)], 3)
# Back on the 48 kHz DAC after a stop, nothing of the other DAC's pair is asked; the other outputs are stock's.
plug(lambda rate: 48000, lambda fmt: fmt)
assert queue((44100, S16), (44100, S16)) == ([(44100, S16), (48000, S16)], 1)
assert init(44100, S24, way=0) == (44100, S24)
print('USB DAC init: an album on a 48 kHz DAC opens the PCM once; other requests, DACs and the jack are stock\'s.')

# The wired outputs' start (patch/peq_player.c dac_*), through stock's own sites around the patched calls.
for address, _, name in DAC_RELEASE:
    assert struct.unpack_from('<I', raw, fileoff(raw, address))[0] == jal(ps[name])
FLAG, FD, CAN_PAUSE, MPCTX, OFFSET = 0xb11584, 0xb1158c, 0xb11594, 0xaf9e30, 0xaf9b78
UNMUTE = ('ioctl', 5, 0xc0044d1f, 0)
def stub(u, address, size, _): u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
u.hook_add(UC_HOOK_CODE, stub, begin=0x4460fc, end=0x4460fc)  # mp_msg
u.hook_add(UC_HOOK_CODE, stub, begin=0x42bcf8, end=0x42bcf8)  # Bluetooth's volume after a resume
ctx, frame = alloc(0x100), alloc(0x400)
w(MPCTX, ctx), w(frame + 0x180, 0xb10000), w(CAN_PAUSE, 1), w(PCM, 0x1000)
def state(way=0, flag=1, fd=5, paused=False, offset=0.0):
    w(WAY, way), w(FLAG, flag), w(FD, fd), w(ctx + 4, 2 if paused else 1)
    u.mem_write(OFFSET, struct.pack('<d', offset))
def site(start, until, **regs):
    """What stock's code from start to until does, and the flag it leaves."""
    dac_log.clear()
    for reg, value in regs.items(): u.reg_write(globals()['UC_MIPS_REG_' + reg], value)
    u.reg_write(UC_MIPS_REG_SP, STACK), u.reg_write(UC_MIPS_REG_RA, DONE)
    u.emu_start(start, until, count=100_000)
    return [*dac_log], r(FLAG)
def init_end(): return site(0x438d78, 0x438d94, S4=0xb10000, FP=frame)
def seek(): return site(0x42b0d4, 0x42b128, V0=0xb10000, S0=ctx, S1=0xb10000, S2=0xb10000)
def resume(): return site(0x4387ec, DONE)
def wrapper(name, *args):
    dac_log.clear(), call(ps[name], *args)
    return [*dac_log], r(FLAG)
RELEASED, AS_STOCK = ([UNMUTE, 'wait'], 0), ([UNMUTE], 0)
def stamped(age=1):
    """A word in PRESS_FILE, age ms old by the clock, which moves on so no word comes twice."""
    clock[0] += 10**10
    press[0] = (clock[0] // 10**6 - age) & 0xffffffff | 1

# The end of init waits after its unmute only for a fresh press; with no mute, or with the unmute left to seek or
# resume, it does nothing.
state(); stamped()
assert init_end() == RELEASED
state(flag=0); stamped()
assert init_end() == ([], 0)
state(paused=True); stamped()
assert init_end() == ([], 1)
# A natural change: a stale press, one from the future (the clock wrapped between them), 0 or no file: the ioctl,
# exactly stock, no wait.
for age in (5001, 0x7fffffff, -1000):
    state(); stamped(age)
    assert init_end() == AS_STOCK, age
state(); stamped(4999); clock[0] += 10**6  # 5000 exactly
assert init_end() == AS_STOCK
state(); stamped(); press[0] = 0
assert init_end() == AS_STOCK
state(); press[0] = None
assert init_end() == AS_STOCK
# The window's edge, and a press either side of the 32-bit wrap.
state(); stamped(4999)
assert init_end() == RELEASED
clock[0] = (1 << 32) * 10**6 - 10**10 + 10**6 * 2
state(); stamped(100)
assert clock[0] // 10**6 >> 32 == 1 and press[0] >> 31 == 1 and init_end() == RELEASED
# One press waits once: a short track's natural reopen on the same word doesn't.
state()
assert init_end() == AS_STOCK
# A start with an offset: init passes it to seek, which follows the press and waits once; a later resume has
# nothing left to do. Without a press (a natural next, Memory Playback's place), it doesn't wait.
state(offset=30.0); stamped()
assert init_end() == ([], 1)
assert seek() == RELEASED and seek() == ([], 0) and resume() == ([('pause', 0)], 0)
for word in (None, 0):
    state(offset=30.0); press[0] = word
    assert init_end() == ([], 1) and seek() == AS_STOCK, word
state(offset=30.0); stamped(6000)
assert seek() == AS_STOCK
# A user's seek has a fresh stamp, and waits once. A-B Repeat's loop and a phone's seek have none: stock's.
state(); stamped()
assert seek() == RELEASED
state()
assert seek() == AS_STOCK
for word in (None, 0):
    state(); press[0] = word
    assert seek() == AS_STOCK, word
state(); stamped(6000)
assert seek() == AS_STOCK
# While paused, seek's own check passes it over, as the end of init's does.
state(paused=True); stamped()
assert seek() == ([], 1)
# Resume releases, waits, then lets audio flow, and stock's own unmute after the call passes; only ever a user's,
# so the file has no say.
for word in (None, 0):
    state(); press[0] = word
    assert resume() == ([UNMUTE, 'wait', ('pause', 0)], 0), word
state(); stamped(6000)
assert resume() == ([UNMUTE, 'wait', ('pause', 0)], 0)
state(flag=0)
assert resume() == ([('pause', 0)], 0)
# Resume hands back snd_pcm_pause's answer, which stock's failure path reads.
state(flag=0); pause_rc[0] = 0xffffffe0
assert call(ps['dac_resume'], 0x1000) == 0xffffffe0
pause_rc[0] = 0
# With the DAC closed, everything passes straight through.
state(fd=0); stamped()
assert resume() == ([('pause', 0)], 1) and init_end() == ([], 1) and seek() == ([], 1)
# Bluetooth and USB never wait and never touch the DAC, at stock's sites or the hooks themselves, press or none.
for way in (1, 2):
    state(way=way); stamped()
    assert resume() == ([('pause', 0)], 1), way
    assert init_end() == ([], 1), way
    assert wrapper('dac_unmute', 0) == ([], 1), way
state(way=2); stamped()
assert seek() == ([], 1)
print('Wired start: a press waits once before audio flows; a natural change, Bluetooth, USB and a clear flag don\'t wait.')

# Stock's own init from here on, with libasound answering as `plug:bluealsa` does: bluealsa's ioplug offers one
# rate (snd_pcm_ioplug_set_param_minmax(HW_RATE, sampling, sampling)) and resampling is off, so set_rate_near
# lands on it; plug converts the format. A call's name and arguments go in `alsa` in order.
u.hook_del(init_hook)
SET_RATE_NEAR, S16LE, GAPLESS, DSD_FILE, STOP, NEXT = 'snd_pcm_hw_params_set_rate_near', 9, 0xb10e58, 0xb10e5c, 0xb10e70, 0xb10e6c
TRIPLE, BT = (0xafa168, 0xafa16c, 0xafa164), 1
alsa, pcm_state, pcm_seen, queries = [], [2], [2], []  # the ioplug's real state, and what snd_pcm_state last showed
transport = [48000]
def set_rate_near():
    alsa.append((SET_RATE_NEAR, r(a(2))))
    if transport[0]: w(a(2), transport[0])
real.update({
    'snd_pcm_open': lambda: (w(a(0), 0x5000), alsa.append(('snd_pcm_open',)))[0],
    'snd_pcm_close': lambda: alsa.append(('snd_pcm_close',)),
    'snd_pcm_drain': lambda: alsa.append(('snd_pcm_drain',)),
    # The ioplug's state op only reads (libasound 0x904d0); snd_pcm_status runs the pointer callback that moves it.
    'snd_pcm_state': lambda: (queries.append('state'), pcm_seen[0])[1],
    'snd_pcm_status': lambda: (queries.append('status'), pcm_seen.__setitem__(0, pcm_state[0]), 0)[2],
    SET_RATE_NEAR: set_rate_near,
    'snd_pcm_hw_params_set_rate_resample': lambda: alsa.append(('resample', a(2))),
    'snd_pcm_hw_params_malloc': lambda: w(a(0), alloc(4096)), 'snd_pcm_sw_params_malloc': lambda: w(a(0), alloc(4096)),
    'snd_pcm_hw_params_sizeof': lambda: 1024, 'snd_pcm_sw_params_sizeof': lambda: 1024,
    'snd_pcm_info_sizeof': lambda: 1024, 'snd_pcm_status_sizeof': lambda: 1024,
    'snd_pcm_format_physical_width': lambda: 16, 'strlen': lambda: len(cstr(a(0))),
    'open64': lambda: 0xffffffff, 'perror': lambda: 0,  # the DAC's node, when its fd isn't open
})
def output(way, gapless=1, fd=0):
    w(WAY, way), w(GAPLESS, gapless), w(FD, fd), w(FLAG, 0), w(PCM, 0)
    w(DSD_FILE, 0), w(STOP, 0), w(NEXT, 0)
    for t in TRIPLE: w(t, 0xffffffff)
    pcm_state[0] = pcm_seen[0] = 2
def ask(rate, fmt=S16LE, entry=None):
    """A request through the slot (or stock's init); what it called, and ao_data's rate after."""
    alsa.clear()
    assert call(entry or ps['ao_init'], rate, 2, fmt, 0) == 1 and r(PCM) == 0x5000
    return [*alsa], r(AO_DATA)
REOPEN = ['snd_pcm_drain', 'snd_pcm_close', 'snd_pcm_open']
def opens(calls): return [c[0] for c in calls if c[0] in REOPEN]

# What the player asks on way 1: the store at 0x42efcc, reached from the way check, is 48 kHz for any file.
w(WAY, BT), w(AO_DATA, 44100)
site(0x42ea60, 0x42ed88, S3=0xb00000)
assert r(AO_DATA) == 48000
# The transport's rate is what comes back, and is what the chain then resamples to, with the PCM kept open.
for rate in (44100, 48000, 96000):
    output(BT); transport[0] = rate
    assert opens(ask(48000)[0]) == ['snd_pcm_open']
    for _ in range(3):
        calls, got = ask(48000)
        assert calls == [] and got == rate, (rate, calls, got)
    # Stock's init slot reopens at every change unless the transport happens to be what the player asks.
    output(BT)
    ask(48000, entry=AO_INIT)
    assert ask(48000, entry=AO_INIT)[0] == [] if rate == 48000 else opens(ask(48000, entry=AO_INIT)[0]) == REOPEN
# AAC and SBC: every call and argument the same through the slot as through stock's init, and no state query.
output(BT); transport[0] = 48000
through = [ask(48000)[0] for _ in range(3)]
output(BT)
assert through == [ask(48000, entry=AO_INIT)[0] for _ in range(3)]
queries.clear(); ask(48000); assert queries == []  # the slot asks nothing of a pair that changes nothing
# A stop closes the PCM: a fresh open, at whatever the transport is now, and no stale pair.
output(BT); transport[0] = 96000
ask(48000); assert ask(48000)[0] == []
w(PCM, 0); transport[0] = 48000
assert opens(ask(48000)[0]) == ['snd_pcm_open'] and ask(48000)[0] == [] and r(AO_DATA) == 48000
# Another transport with the PCM still open, once something reset the triple (mcl-orderstop, a skip): one reopen,
# which doesn't ask the old 96 kHz for ever; after it the album is kept open again.
output(BT); transport[0] = 96000
ask(48000); ask(48000)
for t in TRIPLE: w(t, 0xffffffff)
transport[0] = 48000
assert opens(ask(48000)[0]) == REOPEN and r(AO_DATA) == 48000
assert ask(48000) == ([], 48000)
# A transport gone under the open PCM: stock's reset only prepares it (way 1 at 0x439c00, past the slot), so
# the next change negotiates anew, as stock's reopen did. The states are set by hand: the plugin isn't run. XRUN is
# a link lost during an underrun (or a next track later than the buffer), reached only through snd_pcm_status.
for state in (8, 4, 6, 1):  # DISCONNECTED, XRUN, PAUSED, SETUP
    output(BT); transport[0] = 96000
    ask(48000); ask(48000)
    alsa.clear(); call(0x439c00)
    assert not opens(alsa)
    pcm_state[0], transport[0] = state, 48000
    assert opens(ask(48000)[0]) == REOPEN and r(AO_DATA) == 48000, state
    pcm_state[0] = pcm_seen[0] = 2
    assert ask(48000) == ([], 48000)
# Running or prepared, as at an ordinary change: kept.
for state in (3, 2):
    output(BT); transport[0] = 96000
    ask(48000); ask(48000)
    pcm_state[0] = state; queries.clear()
    assert ask(48000) == ([], 96000) and queries == ['status', 'state'], state
# From Bluetooth to a USB DAC, its PCM left open by mcl-orderstop (way 1 skips the close, 0x42ce78), the triple -1
# or kept: the pair stays Bluetooth's. A reopen at what the file asks, as stock.
for reset in (True, False):
    output(BT); transport[0] = 96000
    ask(48000); ask(48000)
    if reset:
        for t in TRIPLE: w(t, 0xffffffff)
    w(WAY, 2); transport[0] = None
    calls = ask(48000)[0]
    assert (SET_RATE_NEAR, 48000) in calls and opens(calls) == REOPEN[1:], (reset, calls)
    assert r(AO_DATA) == 48000
    # The pair moved with it: back on Bluetooth nothing of USB's is asked, and nothing of the old either.
    w(WAY, BT); transport[0] = 96000
    for t in TRIPLE: w(t, 0xffffffff)
    assert (SET_RATE_NEAR, 48000) in ask(48000)[0], reset
# From Bluetooth to the jack: way 0 never uses the pair, whatever a Bluetooth session left in it.
output(BT); transport[0] = 96000
ask(48000); ask(48000)
output(0, fd=5); transport[0] = 48000
assert ask(48000)[0] and ask(48000)[0] == []
# Nor does it use its own: an answer that differs reopens at each change, as stock.
output(0, fd=5); transport[0] = 96000
ask(48000); assert opens(ask(48000)[0]) == REOPEN
print('Bluetooth init: at 44.1, 48 and 96 kHz transports the PCM stays open across a change; AAC and SBC call as stock; a stop, a new transport and a lost one negotiate anew.')

# A file rate change on a kept-open 96 kHz PCM: the chain resamples, so what comes out is at the right pitch and
# length; the tail carries only between like instances, and a 96 kHz file between has no resampler to carry through.
def pitch(pcm, rate):
    s = struct.unpack('<%dh' % (len(pcm) // 2), pcm)[::2]
    crossings = [i for i in range(200, len(s) - 1) if s[i] <= 0 < s[i + 1]]
    return rate * (len(crossings) - 1) / (crossings[-1] - crossings[0])
for rate in (44100, 48000, 88200):
    got = play(instance(rate, 96000, ps['rs_open']), tone(rate, 16384))
    assert abs(pitch(got, 96000) - 220) < 0.5 and abs(len(got) // 4 - 16384 * 96000 / rate) < 64, (rate, pitch(got, 96000))
pcm = tone(44100, 16384)
whole = play(instance(44100, 96000, ps['rs_open']), pcm)
fresh = play(instance(44100, 96000, ps['rs_open']), pcm[8 * 4096:])
x, y = change(pcm[:8 * 4096], pcm[8 * 4096:], 44100, 96000)  # 44.1 -> 44.1, carried
assert x + y == whole
old = instance(44100, 96000, ps['rs_open'])
play(old, pcm[:8 * 4096])
u.mem_write(ps['rs_end'], struct.pack('<q', clock[0]))
call(r(old + 8), old)                                        # 44.1 ends; a 96 kHz file plays with no resampler
clock[0] += 4 * 10**9
assert play(instance(44100, 96000, ps['rs_open']), pcm[8 * 4096:]) == fresh  # and 44.1 again starts fresh
print('Bluetooth rate change on a kept-open PCM: right pitch at 44.1, 48 and 88.2 kHz to 96 kHz; 44.1 to 96 to 44.1 starts fresh.')
