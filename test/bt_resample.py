#!/usr/bin/env python3
"""Bluetooth's resampler across a gapless change (patch/peq_player.c rs_open): the built player's
lavcresample and libswresample under Unicorn.

A tone split across two files comes out of two instances exactly as one instance plays it whole
at AAC/SBC's 48 kHz and LDAC's 96 kHz. Stock's (and a skip mid-track, a rate change or a pause)
start the second from a fresh resampler, which loses the first's tail.
Then a USB DAC's and Bluetooth's init across a change (ao_init): a repeated request asks for what it negotiated.
Then the wired outputs' start (dac_*): at stock's own unmute sites a press waits, once, before audio flows.
Then the old track's end (ao_uninit, ao_init): a natural change that reopens drains, mutes, reopens, unmutes and waits;
one demo left unmuted at the same format keeps the PCM open; a skip stays mute-first.
Last an unpause (ao_write, dac_resume): the frames go back into a prepared PCM from where the fade began. The DMA
itself isn't modelled: the PCM here is its status words and what reaches it.
Usage: test/bt_resample.py BUILD_DIR"""
import math, pathlib, re, struct, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import fileoff, jal, load_elf, symbols
from peq import RS_OPEN_SLOT, RS_OPEN, AO_INIT_SLOT, AO_INIT, AO_UNINIT_SLOT, DAC_RELEASE
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE, UC_HOOK_INTR
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
hi, lo = re.search(r'peq_lib_dlsym:\nlui \$gp.*\nori \$gp.*\nlui \$t8, (\d+)\nori \$t8, \$t8, (\d+)', (out/'peq_imports.S').read_text()).groups()
imports[STUBS + 4*len(imports)] = 'dlsym'
u.mem_write(int(hi) << 16 | int(lo), struct.pack('<I', STUBS + 4*(len(imports)-1)))
class dev:  # what the stubs below see of the PCM (mute: MUTE_FILE's word, None for no file)
    state, trigger, appl, hw, status_rc, mute, most, calls, got, errors = 2, 0, 0, 0, 0, None, 1 << 30, 0, b'', []
    before = None  # the log at the first frame written since pause_at
    avail = 0
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
    elif n == 'open':
        assert cstr(a(0)) in (b'/tmp/q2press', b'/tmp/q2mute') and not a(1)
        result = (-1 if press[0] is None else 7) if cstr(a(0)) == b'/tmp/q2press' else (-1 if dev.mute is None else 8)
    elif n == 'read': assert a(0) in (7, 8) and a(2) == 4; w(a(1), press[0] if a(0) == 7 else dev.mute); result = 4
    elif n == 'close': assert a(0) in (7, 8); result = 0
    elif n == 'ioctl': dac_log.append(('ioctl', a(0), a(1), r(a(2)))); result = 0
    elif n == 'usleep': dac_log.append('wait'); assert 100000 <= a(0) <= 200000, 'the mute line, without a long wait'; result = 0
    elif n == 'snd_pcm_pause': dac_log.append(('pause', a(1))); result = pause_rc[0]
    elif n == 'snd_pcm_state': result = 2  # not suspended
    elif n == 'snd_pcm_status_sizeof': result = 1024  # stock's get_space, from ao_init
    elif n == 'snd_pcm_status_get_avail': result = dev.avail
    elif n == 'snd_pcm_status':
        w(a(1), dev.state, dev.trigger // 1000, dev.trigger % 1000 * 10**6, 0, 0, dev.appl & 0xffffffff, dev.hw)
        result = dev.status_rc
    elif n == 'snd_pcm_drop': dac_log.append('drop'); result = 0
    elif n == 'snd_pcm_prepare': dac_log.append('prepare'); dev.appl = dev.hw = 0; result = 0
    elif n == 'snd_pcm_writei':
        frames = a(2)
        if dev.errors: result = dev.errors.pop(0)
        else:
            frames = min(frames, dev.most)
            dev.got += bytes(u.mem_read(a(1), frames * r(0xb11598))); dev.appl += frames; result = frames
            dev.calls += 1
            if dev.before is None: dev.before = [*dac_log]
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
    asked = (a(0), a(1), a(2))  # ao_data is the request already: reinit_audio_chain writes it (0x42e9e4)
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
def ask(rate, fmt=S16LE, entry=None, channels=2):
    """A request through the slot (or stock's init); what it called, and ao_data's rate after."""
    alsa.clear()
    w(AO_DATA, rate, channels, fmt)  # as reinit_audio_chain does before init (0x42e9e4, 0x42eaa0)
    assert call(entry or ps['ao_init'], rate, channels, fmt, 0) == 1 and r(PCM) == 0x5000
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

# The old track's end. Stock's uninit_player (0x42e0a8), the descriptor's slot and stock's init around ao_*, in
# order of the calls that matter, with a drain that takes the buffer's rest once and then fails as libasound's
# state check does (mask 0x7c in snd_pcm_drain, at 0x3ff94): a PCM in SETUP gets -EBADFD at once.
assert struct.unpack_from('<I', raw, fileoff(raw, AO_UNINIT_SLOT))[0] == ps['ao_uninit']
EBADFD, PLAY_FLAG, PRELOADED, INITED, TAIL_MS = -77, 0xb10e34, 0xb10df8, 0xb10e8c, 500
DRAIN, CLOSE, OPEN, MUTE, UNMUTE, WAIT = 'snd_pcm_drain', 'snd_pcm_close', 'snd_pcm_open', ('dac', 1), ('dac', 0), 'wait'
DROP = 'snd_pcm_drop'
# libasound is modelled: the first drain takes the buffer's rest and leaves SETUP, a later one gets -EBADFD. during[0],
# if set, is a command landing on the other thread while the drain blocks, run for real on this machine; a drop then
# ends the drain, as the kernel's does.
running, wall, drain_rc, during = [True], [0], [None], [None]
def drain():
    assert a(0), 'snd_pcm_drain(NULL)'
    alsa.append((DRAIN,))
    if drain_rc[0] is not None:
        rc, drain_rc[0] = drain_rc[0], None
        return rc
    if not running[0]: return EBADFD
    if during[0]:
        fn, during[0] = during[0], None
        saved = u.context_save()
        fn()
        u.context_restore(saved)
        if not running[0]: return EBADFD
    wall[0] += TAIL_MS
    running[0] = False
def drop():
    assert a(0), 'snd_pcm_drop(NULL)'
    alsa.append((DROP,))
    running[0] = False
def pcm_open():
    w(a(0), 0x5000), alsa.append((OPEN,))
    running[0] = True
def dac_call():
    if a(1) == 0xc0044d1f: alsa.append(('dac', r(a(2))))
BUFFER, avail = 22048, [0]  # frames; what snd_pcm_status_get_avail answers, BUFFER or more being dry
real.update({DRAIN: drain, DROP: drop, OPEN: pcm_open, 'ioctl': dac_call, 'usleep': lambda: alsa.append((WAIT,)),
             'snd_pcm_hw_params_get_buffer_size': lambda: w(a(1), BUFFER), 'snd_pcm_status_get_avail': lambda: avail[0]})
def order(): return [c if c[0] == 'dac' else c[0] for c in alsa if c[0] in (DRAIN, DROP, CLOSE, OPEN, 'dac', WAIT)]
w(ctx + 0x74, 0), w(ctx + 4, 1), w(MPCTX, ctx), w(OFFSET, 0), w(OFFSET + 4, 0)
def end(eof, inited=2):
    """A file's end: uninit_player as the main loop calls it (0x421804)."""
    w(ctx + 8, 0x88f758), w(ctx + 0x14, eof), w(INITED, inited)  # uninit_player clears the descriptor
    alsa.clear(); call(0x42e0a8, 0xff7a)
    return order()
def begin(way, rate=44100, fmt=S16LE, gapless=1, answer=None):
    output(way, gapless, 5 if not way else 0); transport[0] = answer or (96000 if way == BT else None)
    running[0] = True
    ask(rate, fmt)
def nxt(rate=44100, fmt=S16LE, channels=2, entry=None):
    wall[0] = 0
    ask(rate, fmt, entry, channels)
    return order()
def file_end_section(): site(0x4224a4, 0x4224b4, S0=0xb10000)  # 0x422694: with no preload the triple goes
press[0] = None  # nobody pressed: PRESS_FILE's wait stays out of everything below
JACK = [MUTE, DRAIN, CLOSE, OPEN, UNMUTE]  # stock's order: the old audio's end goes unheard
UNCLASSED = [DRAIN, *JACK]  # demo may have muted, so stock's timing after the drain
HEARD = [*UNCLASSED, WAIT]  # the old track to its end, stock's mute around the close and open, the line, then frames
# Natural change, way 0: rate, bit depth or channels, on a preload (the triple kept): heard, then stock's reopen.
for change_to in ({'rate': 48000}, {'fmt': 0x19}, {'channels': 1}):
    begin(0)
    w(PRELOADED, 1); file_end_section(); assert r(PLAY_FLAG) == 1 and r(TRIPLE[0]) != 0xffffffff
    assert end(1) == []
    got = nxt(**change_to)
    assert got == HEARD, (change_to, got)
    assert wall[0] == TAIL_MS  # one buffer's time, the second drain returning at once
# The same through stock's own init is what it replaces: same wall time, the old audio unheard, frames at the unmute.
begin(0); end(1)
assert nxt(48000, entry=AO_INIT) == JACK and wall[0] == TAIL_MS
# Cold natural changes reset the triple as a skip does (no preload: Gapless off, stock Shuffle, Repeat One, a short track).
begin(0)
w(PRELOADED, 0); file_end_section(); assert r(TRIPLE[0]) == 0xffffffff
assert end(1) == [] and nxt() == UNCLASSED  # no mark from demo, which then muted as stock
begin(0, gapless=0); w(TRIPLE[0], 0xffffffff)  # Gapless off: stock's compare is skipped
got = (end(1), nxt()); assert got == ([], UNCLASSED), got
# Manual skip (set-nexturl: the next flag, eof 4): mute first, no drain before it, whatever the format.
for rate in (44100, 48000):
    begin(0)
    w(NEXT, 1); w(PRELOADED, 0); file_end_section()
    assert end(4) == []
    w(NEXT, 0)  # the idle loop clears it (0x42da10)
    assert nxt(rate) == JACK, rate
# A skip that lands after the loop's natural eof, before uninit, and one after uninit but before init.
begin(0); w(NEXT, 1); end(1); w(NEXT, 0)
assert nxt(48000) == JACK
u.hook_add(UC_HOOK_INTR, lambda u, intno, _: (u.reg_write(UC_MIPS_REG_V0, 0), u.reg_write(UC_MIPS_REG_A3, 0)) and None)
command = [lambda: None]
def handler(u, address, size, _):
    if command[0] is None: return  # stock's own handler runs
    command[0](); u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
u.hook_add(UC_HOOK_CODE, handler, begin=0x42be20, end=0x42be20)
def call_on_command_stack(fn, buf=0):
    """Its own stack: during a drain the main thread's frames are live below STACK."""
    u.reg_write(UC_MIPS_REG_A0, buf), u.reg_write(UC_MIPS_REG_SP, STACK - 0x80000), u.reg_write(UC_MIPS_REG_RA, DONE)
    u.emu_start(fn, DONE, count=1_000_000)
def on_command_thread(buf=0): call_on_command_stack(ps['hci_command'], buf)
def pressed(flag):
    command[0] = lambda: w(flag, 1)
    on_command_thread()
def nxt_pressed(flag, **kw):
    """The next init, with a command landing while its drain blocks."""
    during[0] = lambda: pressed(flag)
    got = nxt(**kw)
    assert during[0] is None, 'no drain to land in'
    return got
for flag in (NEXT, STOP):  # the idle loop clears either (0x42da10, 0x42da18), so only hci_command's disarm remains
    begin(0); end(1); pressed(flag); w(flag, 0)
    assert nxt(48000) == JACK, hex(flag)
# A flag up as init looks: the disarm raced the arm (the uninit slot read the flags just before), so nothing drains.
for flag in (NEXT, STOP):
    begin(0); end(1); w(flag, 1)
    assert nxt(48000) == JACK, hex(flag)
    w(flag, 0)
# A press during the drain. The jack: stock's mute at once, its flag set so stock's unmute runs once.
SILENCED = [DRAIN, MUTE, *JACK]
for flag in (NEXT, STOP, PLAY_FLAG):  # PLAY_FLAG: set-songurl, demo's not-playing press, which sets nothing else
    w(PLAY_FLAG, 0)
    begin(0); end(1)
    got = nxt_pressed(flag, rate=48000)
    assert got == SILENCED and got.count(UNMUTE) == 1 and r(FLAG) == 0, (hex(flag), got)
    w(flag, 0)
# USB: its DAC doesn't hear the mute, so the drop stops it at once and the drain with it; no ioctl, and the PCM is
# still there for the drop and stock's close.
for flag in (NEXT, STOP, PLAY_FLAG):
    w(PLAY_FLAG, 0)
    begin(2); end(1)
    got = nxt_pressed(flag, rate=48000)
    assert got == [DRAIN, DROP, CLOSE, OPEN] and wall[0] == 0, (hex(flag), got)
    w(flag, 0)
# The same through stock's own handler (0x42be20), for the commands demo sends: its string parsing answered here.
def strchr():
    at = cstr(a(0)).find(bytes([a(1) & 255]))
    return a(0) + at if at >= 0 else 0
def strncpy(): u.mem_write(a(0), cstr(a(1))[:a(2)].ljust(a(2), b'\0'))
def strcpy(): u.mem_write(a(0), cstr(a(1)) + b'\0')
real.update({'strchr': strchr, 'strncpy': strncpy, 'strcpy': strcpy})
w(0xb10bdc, alloc(0x800))  # the song record the url goes in (0x42bc7c)
def stock_command(text):
    buf = alloc(len(text) + 1); u.mem_write(buf, text.encode() + b'\0')
    command[0] = None
    on_command_thread(buf)
STOP_CMD, NEXT_CMD, SONG_CMD = '{mcl-stop\\null}', '{set-nexturl\\/a.flac|ss=0|0|0|0|0|90}', '{set-songurl\\/a.flac|ss=0|0|0|0|0|90}'
for text, flag in ((STOP_CMD, STOP), (NEXT_CMD, NEXT), (SONG_CMD, PLAY_FLAG)):
    w(PLAY_FLAG, 0)
    w(flag, 0); stock_command(text); assert r(flag) == 1, text  # stock sets what the disassembly says
    w(PLAY_FLAG, 0), w(STOP, 0), w(NEXT, 0)
    begin(0); end(1)
    during[0] = lambda: stock_command(text)
    assert nxt(48000) == SILENCED, text
    w(PLAY_FLAG, 0), w(STOP, 0), w(NEXT, 0)
    begin(2); end(1)
    during[0] = lambda: (stock_command(text), w(GAPLESS, 1))  # its fields set the Gapless word (0x42d300); off, USB waits 350 ms (0x439580)
    assert nxt(48000) == [DRAIN, DROP, CLOSE, OPEN] and wall[0] == 0, text
    w(PLAY_FLAG, 0), w(STOP, 0), w(NEXT, 0)
# And before init: stock's skip or stop disarms, stock's set-songurl (the cold route's own) doesn't.
for text, want in ((STOP_CMD, JACK), (NEXT_CMD, JACK), (SONG_CMD, HEARD)):  # the triple kept here, as a preload's
    begin(0); end(1); stock_command(text)
    w(PLAY_FLAG, 0), w(STOP, 0), w(NEXT, 0)  # the idle loop clears all three (0x42d9fc)
    assert nxt(48000) == want, text
# mcl-orderstop's close, then a press, during the drain on USB: no drop of a closed PCM.
begin(2); end(1)
during[0] = lambda: (call_on_command_stack(0x43b1d0), pressed(NEXT))
assert DROP not in nxt(48000)
w(NEXT, 0)
# The main thread stays out of stock's close while the other thread is on the PCM (2: being cut).
wake = real['usleep']
def still_cut():
    wake(); w(ps['ao_draining'], 0)
real['usleep'] = still_cut
begin(2); end(1)
during[0] = lambda: w(ps['ao_draining'], 2)
assert nxt(48000)[:3] == [DRAIN, WAIT, CLOSE]
real['usleep'] = wake
# A status query or a set-gapless during the drain cuts nothing; nor does a press outside one.
begin(0); end(1)
during[0] = lambda: (command.__setitem__(0, lambda: w(PRELOADED, 1)), on_command_thread())
assert nxt(48000) == HEARD and wall[0] == TAIL_MS
for way in (0, 2):
    w(PLAY_FLAG, 0)
    begin(way); end(1); nxt()
    alsa.clear(); pressed(NEXT); assert order() == [], way
    w(NEXT, 0); pressed(PLAY_FLAG); assert order() == [], way
    w(PLAY_FLAG, 0)
# A status query between the natural end and init changes nothing.
begin(0); end(1); command[0] = lambda: None; on_command_thread()
assert nxt(48000) == HEARD
# Stop: closes at once, muted, no drain (eof 4); a stop flag with immed 0 stays stock's mute-first drain.
begin(0); w(STOP, 1)
assert end(4) == [MUTE, CLOSE]
begin(0); w(STOP, 1)
assert end(1) == [MUTE, DRAIN, CLOSE]
w(STOP, 0)  # that close leaves nothing to drain at the next init
assert r(PCM) == 0 and nxt(48000) == [MUTE, OPEN, UNMUTE]
# A failed open of the next file (0x422664): 0x42a1dc sets eof 1, then the stop flag, with the AO's flag already
# cleared, so the slot isn't called at all.
begin(0); w(STOP, 1)
assert end(1, inited=0) == []
w(STOP, 0)
assert nxt(48000) == JACK
# Pause's uninit_player(2) at eof 0 (0x430390): immed 1, no flags up, and it arms nothing.
begin(0)
assert end(0) == []
assert nxt(48000) == JACK
# mcl-orderstop closed the PCM after a natural end (0x43b1d0, an unplayable file's dialog, then another song): nothing to drain.
for way, stock in ((0, [MUTE, OPEN, UNMUTE]), (2, [OPEN])):
    begin(way); end(1); call(0x43b1d0)
    assert r(PCM) == 0 and nxt(48000) == stock, way
# An arm on Bluetooth doesn't drain another way's PCM: LDAC ends, the USB DAC arrives.
begin(BT); ask(48000); end(1)
w(WAY, 2); transport[0] = None
assert nxt(48000) == [CLOSE, OPEN]
# Same format, natural, preloaded: nothing stock didn't do but one status read on a wired or USB way, to see it isn't
# dry. No word read, no wait, no mute, at any way, through uninit and init alike.
for way in (0, BT, 2):
    begin(way)
    w(PRELOADED, 1); file_end_section()
    ask(48000 if way == BT else 44100)
    assert end(1) == [] and alsa == []
    wall[0] = 0; queries.clear(); press[0] = 0x7fffffff  # no mark to find, were it looked for
    assert ask(48000 if way == BT else 44100)[0] == [] and wall[0] == 0, way
    assert way == BT or queries == ['status'], (way, queries)  # Bluetooth's is ao_playing's, at LDAC's rate
    press[0] = None
    assert r(FLAG) == 0
# The new track's start. Dry: the PCM runs on silence with hw_ptr past appl_ptr (stop_threshold and silence_size are the
# boundary), so frames written now land behind the hardware: reopen. A jack's is the natural order, a USB DAC's unmuted.
# Nearly dry, one frame left: stays open and silent, as any other (the limit in docs/internals.md).
for way, want in ((0, HEARD), (2, [DRAIN, CLOSE, OPEN])):
    begin(way); w(PRELOADED, 1); file_end_section(); end(1)
    avail[0] = BUFFER
    assert nxt() == want, way
    begin(way); w(PRELOADED, 1); file_end_section(); end(1)
    avail[0] = BUFFER * 2  # far past
    assert nxt() == want, way
    begin(way); w(PRELOADED, 1); file_end_section(); end(1)
    avail[0] = BUFFER - 1
    assert nxt() == [], way
    avail[0] = 0
    begin(way); w(PRELOADED, 1); file_end_section(); end(1)
    status, real['snd_pcm_status'] = real['snd_pcm_status'], lambda: -1 & 0xffffffff  # says nothing: reopen, whatever the stack held
    assert nxt() == want, way
    real['snd_pcm_status'] = status
# Bluetooth's underrun stops its plugin and stock's play prepares (0x43b038): a full answer there changes nothing.
begin(BT); ask(48000); end(1); avail[0] = BUFFER
assert ask(48000)[0] == []
avail[0] = 0
# A natural change with no preload, which demo left unmuted (crossfade.c start_mute, odd being a press's stamp), on the
# jack at 44.1 and 192 kHz. Crossfade's route is this one: the fade is in the filter, which ao_init doesn't touch.
def marked(age=1):
    clock[0] += 10**10
    press[0] = ((clock[0] // 10**6 - age) & 0xffffffff) & ~1
def cold(rate, gapless=1, way=0, answer=None):
    begin(way, rate, gapless=gapless, answer=answer); w(PRELOADED, 0); file_end_section(); w(TRIPLE[0], 0xffffffff); assert end(1) == []
    marked()
for rate in (44100, 192000):
    # Same format, Gapless on (stock Shuffle, a folder's end, Repeat One, Crossfade in Shuffle): nothing to reopen for.
    cold(rate); wall[0] = 0; queries.clear()
    assert ask(rate)[0] == [] and wall[0] == 0 and r(FLAG) == 0, rate
    assert [r(t) for t in TRIPLE] == [rate, 2, S16LE], rate
    # Ran dry first, as 192 kHz's 85 ms can, or one frame short of it.
    cold(rate); avail[0] = BUFFER
    assert nxt(rate) == HEARD and r(FLAG) == 0, rate
    cold(rate); avail[0] = BUFFER - 1
    assert nxt(rate) == [], rate
    # MQA's get_space is capped below the ring (0x438210): the count is read directly.
    cold(rate); avail[0] = BUFFER
    w(0xb10c54, 2), w(0xb10b6c, 0x18), w(0xb11598, 8), w(0xafa1b4, BUFFER * 8)  # 32 bits: stock's get_space answers 3/4 of the ring
    assert call(0x43815c) < r(0xafa1b4)
    assert nxt(rate) == HEARD and r(FLAG) == 0, rate
    w(0xb10c54, 0), w(0xb10b6c, 0)
    avail[0] = 0
    # A PCM not running (a pause's, a suspend's) isn't kept.
    cold(rate); pcm_state[0] = 6
    assert nxt(rate) == HEARD, rate
    pcm_state[0] = 2
    # Gapless off and a change of rate, bit depth or channels really reopen: to the end, mute, close, open, unmute, wait.
    cold(rate, gapless=0)
    assert nxt(rate) == HEARD and wall[0] == TAIL_MS, rate
    other = 96000
    for change_to in ({'rate': other}, {'rate': rate, 'fmt': 0x19}, {'rate': rate, 'channels': 1}):
        cold(rate)
        got = nxt(**change_to)
        assert got == HEARD and got.count(UNMUTE) == 1 and r(FLAG) == 0, (rate, change_to, got)
    # A mark that isn't for this start: stale, used, or a press's. Demo muted as stock, so stock's: nothing kept, no wait.
    for word in ('stale', 'used', 'press', 'none'):
        cold(rate)
        if word == 'stale': marked(age=5000)
        elif word == 'press': stamped()
        elif word == 'none': press[0] = None
        elif word == 'used': ask(rate); begin(0, rate); w(PRELOADED, 0); file_end_section(); end(1)
        got = nxt(rate)
        if word == 'press': assert got[:2] == [DRAIN, MUTE] and WAIT in got, got  # a press's stamp waits, as ever
        else: assert got == UNCLASSED, (rate, word, got)
    press[0] = None
# A preload doesn't read the word: a mark is for the cold start it was left for.
begin(0); w(PRELOADED, 1); file_end_section(); end(1); marked()
assert nxt() == []
w(TRIPLE[0], 0xffffffff); end(1)
assert nxt() == []
press[0] = None
# A press during the drain of a natural reopen silences at once, and isn't given the natural wait.
cold(44100, gapless=0)
got = nxt_pressed(NEXT, rate=44100)
assert got == SILENCED, got
w(NEXT, 0)
# A skip after the mark, no stamp over it: stock's order, and the mark, unread, waits once at init's end. Then it's spent.
cold(44100); pressed(NEXT); w(NEXT, 0)
got = nxt(); assert got == [*JACK, WAIT], got
got = nxt(); assert got == [], got
# Bluetooth and USB: demo's mute is way 0's only (0x5ad1b0 tests its output way), so no mark is looked for and the same
# format stays open with any word or none. A press's stamp, too: it hears the buffer's rest first (the limit in
# docs/internals.md). LDAC's answer and AAC's and SBC's 48 kHz both meet the compare; neither way waits for a line.
REOPEN = [DRAIN, CLOSE, OPEN]
for way, rate, answer in ((2, 44100, None), (2, 192000, None), (BT, 48000, 96000), (BT, 48000, 48000)):
    for word in ('none', 'mark', 'press'):
        cold(rate, way=way, answer=answer)
        if word == 'none': press[0] = None
        elif word == 'press': stamped()
        wall[0] = 0
        assert ask(rate)[0] == [] and wall[0] == 0 and r(FLAG) == 0, (way, rate, word)
        assert r(TRIPLE[1]) == 2 and r(TRIPLE[0]) != 0xffffffff, (way, rate, word)
    press[0] = None
    # Gapless off, a bit depth or channels: a reopen; ao_wait never reaches them. USB's one sleep with Gapless off is stock's own (0x439580).
    cold(rate, gapless=0, way=way, answer=answer)
    got = nxt(rate); assert got == (REOPEN if way == BT else [*REOPEN, WAIT]), (way, rate, got)
    for change_to in ({'fmt': 0x19}, {'channels': 1}):
        cold(rate, way=way, answer=answer)
        assert nxt(rate, **change_to) == REOPEN, (way, rate, change_to)
    # A PCM not running isn't kept; a skip after the end isn't natural.
    cold(rate, way=way, answer=answer); pcm_state[0] = 6
    got = nxt(rate); assert got == REOPEN, (way, rate, got)
    pcm_state[0] = 2
    cold(rate, way=way, answer=answer); pressed(NEXT); w(NEXT, 0)
    assert nxt(rate) == ([CLOSE, OPEN] if way == 2 else REOPEN), (way, rate)
    # DSD at either end.
    for at_end in (1, 0):
        cold(rate, way=way, answer=answer) if not at_end else None
        if at_end:
            begin(way, rate, answer=answer); w(PRELOADED, 0); file_end_section(); w(TRIPLE[0], 0xffffffff)
            w(DSD_FILE, 1); end(1); w(DSD_FILE, 0)
        else: w(DSD_FILE, 1)
        got = nxt(rate); w(DSD_FILE, 0)
        assert got == ([CLOSE, OPEN] if way == 2 else REOPEN), (way, rate, at_end, got)
# Run dry, or a status that says nothing: USB reopens with what's left drained; Bluetooth's plugin stops at its own underrun.
cold(44100, way=2); avail[0] = BUFFER
assert nxt() == REOPEN
avail[0] = 0
# Bluetooth keeps it only with BT_STAY_MS of the ring to play; less is stock's reopen, whose Drain starts the silence
# fill. Exactly the limit stays, a frame under doesn't, at AAC's and SBC's rate and LDAC's.
for answer, limit in ((48000, 9600), (96000, 19200)):
    cold(48000, way=BT, answer=answer); avail[0] = BUFFER - limit
    assert nxt(48000) == [], answer
    cold(48000, way=BT, answer=answer); avail[0] = BUFFER - limit + 1
    assert nxt(48000) == REOPEN, answer
    cold(48000, way=BT, answer=answer); avail[0] = BUFFER
    assert nxt(48000) == REOPEN, answer
    avail[0] = 0
# MQA's get_space isn't in the ring's units: it reopens. A status that fails reads as full, and the state decides.
cold(48000, way=BT); w(0xb10c54, 2), w(0xb10b6c, 0x18)
got = nxt(48000)
w(0xb10c54, 0), w(0xb10b6c, 0)
assert got == REOPEN, got
cold(48000, way=BT)
status, real['snd_pcm_status'] = real['snd_pcm_status'], lambda: -1 & 0xffffffff
got = nxt(48000)
real['snd_pcm_status'] = status
assert got == [], got
# USB and a preload don't look: a ring nearly out still stays.
cold(44100, way=2); avail[0] = BUFFER - 1
assert nxt() == []
begin(BT); w(PRELOADED, 1); file_end_section(); ask(48000); end(1); avail[0] = BUFFER - 1
assert ask(48000)[0] == []
avail[0] = 0
press[0] = None
# What's left of a natural end isn't kept: a same-format change takes it, so a later reset doesn't drain.
begin(0); end(1); nxt()
for t in TRIPLE: w(t, 0xffffffff)
got = nxt(); assert got == JACK, got
# DSD keeps its mute, at the end of a DSD track and at the start of one. The end's reopen waits for the line, a press's cut doesn't.
begin(0); w(DSD_FILE, 1); end(1); w(DSD_FILE, 0)
assert nxt(48000) == [*JACK, WAIT]
begin(0); w(DSD_FILE, 1); w(NEXT, 1); end(1); w(DSD_FILE, 0); w(NEXT, 0)
assert nxt(48000) == JACK
begin(0); end(1); w(DSD_FILE, 1)
assert nxt(48000) == JACK
# A mark at a DSD end is spent by that wait: the reopen after it has none to read.
begin(0); w(DSD_FILE, 1); end(1); w(DSD_FILE, 0); marked()
assert nxt(48000) == [*JACK, WAIT]
got = nxt(44100); assert WAIT not in got, got
press[0] = None
# The drain's error: nothing changes, stock's own drain and reopen follow.
begin(0); end(1); drain_rc[0] = -5
assert nxt(48000) == HEARD and r(PCM) == 0x5000
# USB: a natural format change now drains, stock's close and open as ever; a skip still closes at once.
begin(2); end(1)
assert nxt(48000) == [DRAIN, CLOSE, OPEN] and wall[0] == TAIL_MS
begin(2); end(1)
assert nxt(48000, entry=AO_INIT) == [CLOSE, OPEN] and wall[0] == 0
begin(2); w(NEXT, 1); end(4); w(NEXT, 0)
assert nxt(48000) == [CLOSE, OPEN]
# Bluetooth: stock drains unmuted itself, so no second one.
begin(BT); end(1)
assert nxt(48000, fmt=0x19) == [DRAIN, CLOSE, OPEN]
# mcl-orderstop's close (0x43b1d0) is stock's: the jack drains unmuted, USB closes.
for way, want in ((0, [DRAIN, CLOSE]), (2, [CLOSE])):
    begin(way)
    alsa.clear(); call(0x43b1d0)
    assert order() == want, way
print('Old track\'s end: a natural change that reopens is heard to its end, then mutes around the close and open and waits for the line; one at the same format that demo left unmuted stays open; dry reopens; a press during it silences; a skip, a stop, DSD and a preloaded same-format change call as stock.')

real.clear()  # the stubs above answer from here on
UNMUTE = ('ioctl', 5, 0xc0044d1f, 0)  # the real ioctl again, not the section above's marker
# An unpause: every write after the first is kept as it went, and a resume writes the frames again, in order, from the
# fade's start to the newest, into a dropped and prepared PCM, after the unmute's wait and never by snd_pcm_pause.
PAUSED, SEEK, AO_BUF, FRAME, S16F, S32F = 6, 0xb10c38, AO_DATA + 0x14, 0xb11598, 0x9, 0x19
buf = alloc(1 << 20)
def frames_of(start, n, width, nch=2):
    """Frames that name themselves: left is the counter, the rest its negative."""
    fmt = 'h' if width == 2 else 'i'
    top = 32000 if width == 2 else 2000000000
    out = bytearray()
    for i in range(start, start + n):
        v = (i * 7 + 1000) % top
        out += struct.pack(f'<{nch}{fmt}', v, *([-v] * (nch - 1)))
    return bytes(out)
def setup(rate, fmt, frame, ring_frames, way=0):
    w(WAY, way), w(AO_DATA, rate), w(AO_DATA + 8, fmt), w(AO_BUF, ring_frames * frame), w(FRAME, frame), w(FLAG, 1), w(FD, 5)
    w(SEEK, 0), w(ctx + 0x2c, 0)
    dev.state, dev.appl, dev.hw, dev.status_rc, dev.most, dev.errors = 2, 0, 0, 0, 1 << 30, []
def play(start, n, width, frame):
    """The player's writes, 4096 frames a time; returns the bytes and checks they reach the PCM untouched."""
    data = frames_of(start, n, width, frame // width)
    for at in range(0, n, 4096):
        chunk = data[at * frame:(at + 4096) * frame]
        dev.got = b''
        u.mem_write(buf, chunk)
        assert call(ps['ao_write'], PCM_H, buf, len(chunk) // frame) == len(chunk) // frame and dev.got == chunk
    return data
def pause_at(left, hw, age, now=500000):
    """Paused with left frames unplayed and hw played; the stamp age ms before the pause's trigger."""
    dev.state, dev.appl, dev.hw, dev.trigger = PAUSED, hw + left, hw, now
    dev.mute = None if age is None else (now - age) | 1
    dev.got, dev.calls, dev.before = b'', 0, None
def go():
    dac_log.clear()
    rc = call(ps['dac_resume'], PCM_H)
    return [*dac_log], rc, r(FLAG)
PCM_H = 0x1000
OPENED = ['drop', 'prepare', UNMUTE, 'wait']

# The tunables, from the source, so tuning one doesn't fail these.
source = (pathlib.Path(__file__).resolve().parents[1]/'patch'/'peq_player.c').read_text()
FADE_MS, TRIM_MS = (int(re.search(rf'#define {n} \(?(-?\d+)', source)[1]) for n in ('PS_FADE_MS', 'PS_TRIM_MS'))
def faded(rate): return rate // 1000 * FADE_MS
def went_back(ms, rate): return max(ms + TRIM_MS, 0) * rate // 1000
assert TRIM_MS < 0  # the join is past the fade's first step, so went_back's clamp is live
# 44.1 kHz S16: the ring's unplayed part and 279 ms less the trim back, to the frame; the fade's frames rise from 0.
setup(44100, S16F, 4, 22048)
data = play(0, 40000, 2, 4)
pause_at(12000, 28000, 280)  # the stamp is odd, so 279 ms
log, rc, flag = go()
assert log == OPENED and rc == 0 and flag == 0 and dev.before == OPENED, (log, rc, flag, dev.before)  # no frame before the wait is over
back, fade = went_back(279, 44100), faded(44100)
want = data[(40000 - 12000 - back) * 4:]
assert len(dev.got) == len(want) and dev.got[fade * 4:] == want[fade * 4:]
for i in (0, 1, fade // 2, fade - 1):
    l = struct.unpack_from('<h', dev.got, i * 4)[0]
    o = struct.unpack_from('<h', want, i * 4)[0]
    assert abs(l - int(o * i / fade)) <= 1, (i, l, o)
assert not any(dev.got[:4])
# A stale stamp, none, or one from the future (the clock wrapped): no further back than the pause.
for age in (1001, 5000, -50, None):
    setup(44100, S16F, 4, 22048)
    data = play(0, 40000, 2, 4)
    pause_at(12000, 28000, age)
    assert go()[0] == OPENED and dev.got[fade * 4:] == data[(40000 - 12000 + fade) * 4:], age
# Younger than the trim: back is age + trim clamped at 0, and the age doesn't wrap. Just past it, it goes back that far.
setup(44100, S16F, 4, 22048)
data = play(0, 40000, 2, 4)
pause_at(12000, 28000, 2)  # odd, so 1 ms
assert go()[0] == OPENED and len(dev.got) == 12000 * 4
setup(44100, S16F, 4, 22048)
data = play(0, 40000, 2, 4)
pause_at(12000, 28000, -TRIM_MS + 11)  # odd, so 10 ms back
assert go()[0] == OPENED and len(dev.got) == (12000 + 10 * 44100 // 1000) * 4
# The pause's own milliseconds count, not its whole seconds: this stamp is odd already, so 280 ms.
setup(44100, S16F, 4, 22048)
data = play(0, 40000, 2, 4)
pause_at(12000, 28000, 280, now=500123)
assert go()[0] == OPENED and len(dev.got) == (12000 + went_back(280, 44100)) * 4
# A word of 0 is no stamp.
setup(44100, S16F, 4, 22048)
data = play(0, 40000, 2, 4)
pause_at(12000, 28000, 100); dev.mute = 0
assert go()[0] == OPENED and len(dev.got) == 12000 * 4
# A track's first frames: never back past hw_ptr, which a prepare zeroed, whatever older frames are held.
setup(44100, S16F, 4, 22048)
data = play(0, 40000, 2, 4)
pause_at(3000, 500, 280)
assert go()[0] == OPENED and len(dev.got) == 3500 * 4 and dev.got[fade * 4:] == data[(40000 - 3500 + fade) * 4:]
# Fewer frames than the fade: all of it rises.
setup(44100, S16F, 4, 22048)
data = play(0, 8192, 2, 4)
pause_at(100, 0, None)
assert go()[0] == OPENED and len(dev.got) == 400 and abs(struct.unpack_from('<h', dev.got, 99 * 4)[0] - struct.unpack_from('<h', data, (8192 - 1) * 4)[0] * 99 / 100) <= 1
# Twice over: a second pause goes back within what the first wrote again, and what follows is still the same stream.
setup(44100, S16F, 4, 22048)
data = play(0, 40000, 2, 4)
pause_at(12000, 28000, 280)
go()
data2 = play(40000, 4096, 2, 4)
pause_at(4096, back + 12000, 280)
assert go()[0][:2] == ['drop', 'prepare'] and dev.got[fade * 4:] == (data + data2)[(44096 - 4096 - back + fade) * 4:]
# Past the shadow's end and across its wrap, in two writes and a short one, nothing is lost or reordered.
setup(44100, S16F, 4, 22048)
data = play(0, 200000, 2, 4)
pause_at(22048, 100000, 280)
dev.most = 1000
assert go()[0] == OPENED and dev.got[fade * 4:] == data[(200000 - 22048 - back + fade) * 4:] and dev.calls > 20
# A signal between, and a failing write: stock's play recovers it, so the rest is left.
setup(44100, S16F, 4, 22048)
data = play(0, 40000, 2, 4)
pause_at(1000, 28000, None)
dev.errors = [-4]
assert go()[0] == OPENED and len(dev.got) == 4000
pause_at(1000, 28000, None)
dev.errors = [-32]
assert go()[0] == ['drop', 'prepare'] and dev.got == b''
# 96 kHz S32: its own fade and back. Another rate starts empty, and a ring not all held is stock's: the
# pause as before, no drop.
setup(96000, S32F, 8, 16384)
data = play(0, 40000, 4, 8)
pause_at(8000, 32000, 200)
assert go()[0] == OPENED
held = data[-(8000 + went_back(199, 96000)) * 8:]
fade96 = faded(96000)
assert len(dev.got) == len(held) and dev.got[fade96 * 8:] == held[fade96 * 8:] and not any(dev.got[:8])
assert abs(struct.unpack_from('<i', dev.got, fade96 // 2 * 8)[0] - struct.unpack_from('<i', held, fade96 // 2 * 8)[0] / 2) <= 2
setup(88200, S32F, 8, 16384)
play(0, 4096, 4, 8)
pause_at(8000, 1000, 200)
assert go()[0] == [UNMUTE, 'wait', ('pause', 0)] and dev.got == b''
setup(48000, S16F, 4, 24000)  # another format: nothing of the last is kept
pause_at(100, 1000, None); dev.calls = 0
assert go()[0] == [UNMUTE, 'wait', ('pause', 0)]
# DSD stays bit-exact, the same frames from the same place.
setup(176400, S32F, 8, 16384)
codec, sh, name = alloc(0x400), alloc(16), alloc(16)
u.mem_write(name, b'dsdiff\0')
w(codec + 0x3e0, name), w(sh + 4, codec), w(ctx + 0x2c, sh)
data = play(0, 40000, 4, 8)
pause_at(8000, 32000, 100)
assert go()[0] == OPENED and dev.got == data[-(8000 + went_back(99, 176400)) * 8:]
w(ctx + 0x2c, 0)
# A seek while paused: no old frame sounds; its reset writes next. Prepared, not paused.
setup(44100, S16F, 4, 22048)
play(0, 40000, 2, 4)
pause_at(12000, 28000, 280)
w(SEEK, 1)
log, rc, flag = go()
assert log == OPENED and dev.got == b'' and rc == 0
w(SEEK, 0)
# Stock's route: not paused (a pause that failed, a PCM the drain reopened); status failing.
setup(44100, S16F, 4, 22048)
play(0, 40000, 2, 4)
pause_at(12000, 28000, 280); dev.state = 3
assert go()[0] == [UNMUTE, 'wait', ('pause', 0)] and dev.got == b''
pause_at(12000, 28000, 280); dev.status_rc = -1 & 0xffffffff
w(FLAG, 1)
assert go()[0] == [UNMUTE, 'wait', ('pause', 0)] and dev.got == b''
dev.status_rc = 0
# hw_ptr past appl_ptr: more than is held, so stock's.
pause_at(12000, 28000, 280); dev.appl = 27000
w(FLAG, 1)
assert go()[0] == [UNMUTE, 'wait', ('pause', 0)]
# Back goes no further than what is held, however much hw_ptr has played.
setup(32000, S16F, 4, 24000)
data = play(0, 13000, 2, 4)
pause_at(12000, 28000, 280)
assert go()[0] == OPENED and len(dev.got) == 13000 * 4 and dev.got[faded(32000) * 4:] == data[faded(32000) * 4:]
# What is held: the ring and 500 ms, 176 KB at 44.1 kHz S16. The array stays touched this far, so it's pinned, not derived.
setup(44100, S16F, 4, 22048)
data = play(0, 100000, 2, 4)
pause_at(5000, 90000, 1000)  # 999 ms: more than is held
assert go()[0] == OPENED and len(dev.got) == 88192 + 88000
# A same-rate change of format starts empty and then keeps the new frames.
setup(48000, S16F, 4, 24000)
play(0, 40000, 2, 4)
setup(48000, S32F, 8, 16384)
data = play(0, 8192, 4, 8)
pause_at(2000, 6000, None)
assert go()[0] == OPENED and len(dev.got) == 2000 * 8 and dev.got[faded(48000) * 8:] == data[-(2000 - faded(48000)) * 8:]
# Past the 2 MB array, and a frame that doesn't divide it: capped, and the wrap stays on a frame.
setup(768000, 0x11, 6, 16384)
data = b''.join(struct.pack('<I', i)[:3] * 2 for i in range(1, 420000))
for at in range(0, len(data), 4096 * 6):
    chunk = data[at:at + 4096 * 6]
    u.mem_write(buf, chunk), call(ps['ao_write'], PCM_H, buf, len(chunk) // 6)
pause_at(1000, 400000, 1000)
kept = (1 << 21) // 6
assert go()[0] == OPENED and len(dev.got) == kept * 6 and dev.got[faded(768000) * 6:] == data[-(kept - faded(768000)) * 6:]
# Every format rises from its own silence, to the frame, and is untouched after: 8, 6 and 3 byte frames, unsigned, big-endian.
def sample(b, at, f, width):
    """A sample's value relative to its silence."""
    if f & 4: return struct.unpack_from('<f' if f & 1 else '>f', b, at)[0]
    v = int.from_bytes(b[at:at + width], 'little' if f & 1 else 'big', signed=not f & 2)
    return v - (1 << width * 8 - 1) if f & 2 else v
def encode(x, f, width):
    """x in -1..1."""
    if f & 4: return struct.pack('<f' if f & 1 else '>f', x)
    v = int(x * ((1 << width * 8 - 1) - 1)) + ((1 << width * 8 - 1) if f & 2 else 0)
    return v.to_bytes(width, 'little' if f & 1 else 'big', signed=not f & 2)
for name, f, frame, width in (('float', 0x1d, 8, 4), ('S24', 0x11, 6, 3), ('S24 mono', 0x11, 3, 3), ('U16', 0xb, 4, 2), ('U8', 0x2, 2, 1), ('S8', 0x0, 2, 1),
                              ('S16 BE', 0x8, 4, 2), ('S32 BE', 0x18, 8, 4), ('float BE', 0x1c, 8, 4), ('U16 BE', 0xa, 4, 2)):
    setup(44100, f, frame, 22048)
    nch = frame // width
    data = b''.join(encode(0.5 * (1 if i & 1 else -1) * (0.5 + (i % 97) / 200), f, width) for i in range(30000 * nch))
    for at in range(0, len(data), 4096 * frame):
        chunk = data[at:at + 4096 * frame]
        u.mem_write(buf, chunk), call(ps['ao_write'], PCM_H, buf, len(chunk) // frame)
    pause_at(1000, 28000, None)
    assert go()[0] == OPENED and len(dev.got) == 1000 * frame, name
    held = data[-1000 * frame:]
    assert dev.got[fade * frame:] == held[fade * frame:], name
    for c in range(nch):
        assert sample(dev.got, c * width, f, width) == 0, (name, c)
        for i in (1, fade // 2, fade - 1):
            at = (i * frame + c * width)
            got, was = sample(dev.got, at, f, width), sample(held, at, f, width)
            assert abs(got - was * i / fade) <= (1e-4 if f & 4 else 1), (name, c, i, got, was)
# The way turned to 0 while paused on a Bluetooth or USB PCM: this PCM's frames were never kept, so nothing old goes in.
for way in (1, 2):
    setup(48000, S16F, 4, 24000)
    play(0, 40000, 2, 4)
    w(WAY, way)
    play(40000, 4096, 2, 4)
    w(WAY, 0)
    pause_at(1000, 1000, 280)
    assert go()[0] == [UNMUTE, 'wait', ('pause', 0)] and dev.got == b'', way
# Bluetooth and USB: nothing kept and nothing different, even paused.
for way in (1, 2):
    setup(48000, S16F, 4, 24000, way=way)
    before = bytes(u.mem_read(ps['ps_ring'], 64)) if 'ps_ring' in ps else b''
    play(0, 4096, 2, 4)
    if before: assert bytes(u.mem_read(ps['ps_ring'], 64)) == before
    pause_at(1000, 1000, 280)
    assert go()[0] == [('pause', 0)], way
    # Frames kept before the output changed, a seek pending or not, are no reason to touch this PCM.
    setup(48000, S16F, 4, 24000)
    play(0, 40000, 2, 4)
    w(WAY, way)
    for seek in (0, 1):
        w(SEEK, seek), pause_at(1000, 1000, 280)
        assert go()[0] == [('pause', 0)] and dev.got == b'', (way, seek)
# A kept PCM forwarded past a dry ring at its first write: the gap holds nothing of the old track. What goes back is the
# new track's only, and with the gap still to play there isn't all the ring to rewrite, so it's stock's.
FORWARD, forwards = STUBS + 0xff0, []
imports[FORWARD] = 'snd_pcm_forward'
real.update({'dlsym': lambda: FORWARD, 'snd_pcm_forward': lambda: forwards.append(a(1))})
setup(44100, S16F, 4, 22048)
play(0, 40000, 2, 4)
new = frames_of(100000, 1000, 2, 2)
u.mem_write(buf, new)
w(ps['ao_stayed'], 1), w(0xafa1b0, 16 * 4)  # AO_OUTBURST: 16 frames
dev.avail, dev.got, dev.status_rc = 22048 + 100, b'', -1 & 0xffffffff  # a status that fails forwards nothing
assert call(ps['ao_write'], PCM_H, buf, 1000) == 1000 and forwards == [] and dev.got == new
w(ps['ao_stayed'], 1)
dev.got, dev.status_rc = b'', 0
assert call(ps['ao_write'], PCM_H, buf, 1000) == 1000 and forwards == [116] and dev.got == new
dev.avail = 0
pause_at(1200, 60000, 280)  # the gap's last 200 frames unplayed
assert go()[0] == [UNMUTE, 'wait', ('pause', 0)] and dev.got == b''
w(FLAG, 1), pause_at(600, 60000, 280)
assert go()[0] == OPENED and len(dev.got) == 1000 * 4 and dev.got[faded(44100) * 4:] == new[faded(44100) * 4:]
real.clear()
print('Unpause: a resume writes the frames again from the fade\'s second step, under its wait; a seek, a failed pause, Bluetooth and USB as they were.')
