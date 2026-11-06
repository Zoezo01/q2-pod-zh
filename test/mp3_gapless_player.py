#!/usr/bin/env python3
"""Run the built MIPS gapless hooks, including preloads, seeks, fd restoration and cleanup, with libc, file
I/O and the downstream audio filters modelled; then stock's real stream, demuxer, libmad.so and filter chain on
files lame makes here, shaped as encoders and taggers in the wild shape them (real_chain).
Usage: python3 test/mp3_gapless_player.py BUILD
"""
from pathlib import Path
from collections import Counter
import array
import math
import random
import re
import struct
import subprocess
import sys
import tempfile
import wave
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from build import fileoff, jal, machine, symbols
from peq import MP3_ALLOC, MP3_FILTER, MP3_CLOSE, MP3_READ, DEMUX_READ, MP3_FILL
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE
from unicorn.mips_const import *

out = Path(sys.argv[1])/'peq'
raw = (out/'hciplayer').read_bytes()
syms = symbols(out/'peq.elf')
for address, value in ((MP3_ALLOC, jal(syms['mp3_private_alloc'])),
                       (MP3_FILTER, jal(syms['mp3_filter_entry'])), (MP3_CLOSE, syms['mp3_close']),
                       (MP3_READ, jal(syms['mp3_read'])), (MP3_FILL, syms['mp3_fill'])):
    assert struct.unpack_from('<I', raw, fileoff(raw, address))[0] == value

u = machine(raw, 0x2000000)
u.mem_map(0x3000000, 0x200000)
u.mem_map(0x2800000, 0x1000)
u.mem_map(0x3300000, 0x20000)
imports = {}
for line in subprocess.check_output(['readelf', '-rW', out/'hciplayer'], text=True).splitlines():
    s = line.split()
    if len(s) >= 5 and s[2] == 'R_MIPS_JUMP_SLOT':
        address = 0x2800000 + 4 * len(imports)
        imports[address] = s[4].split('@')[0]
        u.mem_write(int(s[0], 16), struct.pack('<I', address))
heap, allocations, files, positions, filtered, descriptors = 0x3000000, {}, {}, {}, [], {}
fail = None
demux_left = 0
imports_called = Counter()


def w(p, *v): u.mem_write(p, struct.pack('<' + 'I'*len(v), *v))
def r(p): return struct.unpack('<I', u.mem_read(p, 4))[0]
def a(n): return u.reg_read((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)[n])


def alloc(n):
    global heap
    p = heap
    heap = (heap + n + 15) & ~15
    allocations[p] = n
    return p


def hook(u, address, size, _):
    if address == 0x44d350:  # af_play: capture exactly what all filters see
        data = a(1)
        filtered.append(bytes(u.mem_read(r(data), r(data+4))))
        result = data
    elif address == DEMUX_READ:  # demux_read_data: demux_left bytes of 0xaa, then EOF
        result = min(a(2), demux_left)
        u.mem_write(a(1), b'\xaa' * result)
    elif address in imports:
        name = imports[address]
        imports_called[name] += 1
        if name == fail:
            result = 0
        elif name == 'calloc':
            result = alloc(a(0)*a(1))
            u.mem_write(result, bytes(a(0)*a(1)))
        elif name == 'realloc':
            result = alloc(a(1))
            if a(0):
                u.mem_write(result, bytes(u.mem_read(a(0), min(a(1), allocations.pop(a(0))))))
        elif name == 'free':
            if a(0): allocations.pop(a(0))
            result = 0
        elif name in ('memcpy', 'memmove'):
            u.mem_write(a(0), bytes(u.mem_read(a(1), a(2))))
            result = a(0)
        elif name == 'memset':
            u.mem_write(a(0), bytes([a(1) & 255]) * a(2))
            result = a(0)
        elif name == 'memcmp':
            x, y = bytes(u.mem_read(a(0), a(2))), bytes(u.mem_read(a(1), a(2)))
            result = (x > y) - (x < y)
        elif name == 'lseek64':
            fd = a(0)
            off = struct.unpack('<q', struct.pack('<II', a(2), a(3)))[0]
            whence = r(u.reg_read(UC_MIPS_REG_SP)+16)
            positions[fd] = off + (0, positions[fd], len(files[fd]))[whence]
            result = positions[fd]
            u.reg_write(UC_MIPS_REG_V1, result >> 32)
        elif name == 'read':
            fd = a(0)
            data = files[fd][positions[fd]:positions[fd]+a(2)]
            u.mem_write(a(1), data)
            positions[fd] += len(data)
            result = len(data)
        else:
            raise AssertionError(name)
    else:
        return
    u.reg_write(UC_MIPS_REG_V0, result & 0xffffffff)
    u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))


u.hook_add(UC_HOOK_CODE, hook)


def call(name, *args, sh=0):
    for i, value in enumerate(args): u.reg_write((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)[i], value)
    u.reg_write(UC_MIPS_REG_SP, 0x200f000)
    u.reg_write(UC_MIPS_REG_FP, sh)
    u.reg_write(UC_MIPS_REG_RA, 0x2000000)
    u.emu_start(syms[name], 0x2000000, count=1000000)
    assert u.reg_read(UC_MIPS_REG_PC) == 0x2000000
    return u.reg_read(UC_MIPS_REG_V0)


h = bytearray(417)
h[:4] = bytes.fromhex('fffb9000')
h[36:48] = b'Info' + struct.pack('>II', 1, 100)
h[48:57] = b'LAME3.100'
h[69:72] = ((576 << 12) | 1200).to_bytes(3, 'big')


def track(i, header=h):
    d, ds, sh, stream = [0x2001000 + i*0x3000 + x for x in (0, 0xd00, 0xe00, 0xf00)]
    p = call('mp3_private_alloc', 16)
    w(p, 1)
    w(d, 0x89fd90)
    w(d+0xc70, p)
    w(d+0x20, stream)
    w(d+0x10, 10, 0)
    w(ds+0x58, d)
    w(sh, ds)
    w(stream+0x14, i+3)
    files[i+3], positions[i+3] = bytes(10) + header, 1234
    descriptors[sh] = i+3
    return d, sh, p


def feed(t, pcm):
    d, sh, p = t
    data, buf = 0x200b000, 0x3300000
    u.mem_write(buf, pcm)
    w(data, buf, len(pcm), 44100, 2, 0x1d, 4)
    call('mp3_filter_entry', 0x200af00, data, sh=sh)
    assert positions[descriptors[sh]] == 1234
    return filtered[-1]


t, preload = track(0), track(1)
pcm = b''.join(struct.pack('<II', i, ~i & 0xffffffff) for i in range(7000))
result = []
for at in range(0, len(pcm), 2048*8):
    result.append(feed(t, pcm[at:at+2048*8]))
    if not at: assert feed(preload, pcm[:2048*8]) == b''
assert b''.join(result) == pcm[2257*8:-671*8]
assert feed(preload, pcm[2048*8:4096*8]) == pcm[2257*8:(4096-671)*8]

# Seek to zero: stock's check of the next frame is off, so the tag frame reaches the decoder whole (real_chain's
# rewind). Any other seek keeps it.
u.mem_write(t[2]+8, struct.pack('<d', 0))
w(0xb152b4, 1)
call('mp3_gapless_seek', t[0], 0)
assert r(0xb152b4) == 1
call('mp3_gapless_seek', t[0], 1)
assert r(0xb152b4) == 0
assert feed(t, pcm) == pcm[2257*8:-671*8]
# Mid-track seek drops held audio, preserving the new input apart from its tail.
u.mem_write(t[2]+8, struct.pack('<d', 10))
call('mp3_gapless_seek', t[0], 0)
assert feed(t, pcm) == pcm[:-671*8]
for tr in (t, preload): call('mp3_close', tr[0])
assert not allocations

# Warmed-up playback reuses buffers and never probes the file again.
tr = track(0)
assert feed(tr, pcm[:4096*8]) == pcm[2257*8:(4096-671)*8]
counts = imports_called.copy()
fail = 'calloc'
assert feed(tr, pcm) == pcm[(4096-671)*8:4096*8] + pcm[:-671*8]
for _ in range(20):
    assert feed(tr, pcm) == pcm[-671*8:] + pcm[:-671*8]
fail = None
for name in ('read', 'lseek64', 'calloc', 'realloc'):
    assert imports_called[name] == counts[name], (name, imports_called, counts)
call('mp3_close', tr[0])
assert not allocations

# With no tail padding, even the leading trim needs no buffer or PCM copies.
zero_tail = bytearray(h)
zero_tail[69:72] = ((576 << 12) | 529).to_bytes(3, 'big')
tr = track(0, zero_tail)
counts = imports_called.copy()
assert feed(tr, pcm[:2048*8]) == b''
assert feed(tr, pcm[2048*8:]) == pcm[2257*8:]
for _ in range(20):
    assert feed(tr, pcm) == pcm
for name in ('calloc', 'realloc', 'memcpy', 'memmove'):
    assert imports_called[name] == counts[name], (name, imports_called, counts)
assert imports_called['read'] == counts['read'] + 2  # the first frame and the file's last bytes, once
assert imports_called['lseek64'] == counts['lseek64'] + 4
call('mp3_close', tr[0])
assert not allocations

# A seek before the first PCM block, untagged files and allocation failure.
for i, header in enumerate((h, bytes(417), h)):
    tr = track(0, header)
    if i == 0:
        call('mp3_gapless_seek', tr[0], 1)
        assert feed(tr, pcm) == pcm[2257*8:-671*8]
    else:
        fail = 'calloc' if i == 2 else None
        assert feed(tr, pcm) == pcm
        fail = None
        assert feed(tr, pcm) == pcm
    call('mp3_close', tr[0])
    assert not allocations
# read_frame at EOF: the last frame gets 8 zero guard bytes once, then stock's EOF; a seek re-arms it.
tr = track(0)
ds, buf = r(tr[1]), 0x3310000
u.mem_write(buf, b'\xff' * 16)
demux_left = 5
assert call('mp3_read', ds, buf, 100) == 5 and bytes(u.mem_read(buf, 5)) == b'\xaa' * 5
demux_left = 0
assert call('mp3_read', ds, buf, 7) == 0
assert call('mp3_read', ds, buf, 100) == 8 and bytes(u.mem_read(buf, 9)) == bytes(8) + b'\xff'
assert call('mp3_read', ds, buf, 100) == 0
call('mp3_gapless_seek', tr[0], 0)
assert call('mp3_read', ds, buf, 100) == 8
w(tr[0], 0)  # another demuxer's stream keeps stock's EOF
assert call('mp3_read', ds, buf, 100) == 0
w(tr[0], 0x89fd90)
call('mp3_close', tr[0])
assert not allocations
print('MIPS MP3 gapless: installed hooks, preloads, seek/reset, initial seek, fd restoration, untagged bypass, allocation failure, last-frame guard and cleanup: OK')


def real_chain():
    """The built player's own open_stream, demux_open, init_best_audio_codec (it picks libmad), init_audio_filters
    and decode_audio, with the firmware's libmad.so. libc is modelled, as above."""
    OPEN_STREAM, DEMUX_OPEN, CODEC_CFG, BEST_CODEC, INIT_FILTERS, DECODE_AUDIO, UNINIT, DEMUX_SEEK = (
        0x4a4ed0, 0x4855b4, 0x72d9f0, 0x47f090, 0x47f444, 0x47f68c, 0x47eb40, 0x4868ac)
    MSG, URL, MAD, HEAP, STUBS, DONE, STACK = 0x4460fc, 0xb10b90, 0x30000000, 0x20000000, 0x10000000, 0xf000000, 0xe000000
    tmp = tempfile.TemporaryDirectory()
    work = Path(tmp.name)
    libmad = work/'libmad.so'
    libmad.write_bytes(subprocess.check_output(['unsquashfs', '-cat', Path(sys.argv[1])/'stock.squashfs', 'usr/lib/libmad.so.0.2.1']))
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    u.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
    u.ctl_set_tcg_buffer_size(96 << 20)
    regs = (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)
    def segments(image, base):
        end = 0
        for i in range(struct.unpack_from('<H', image, 44)[0]):
            t, o, v, _, f, m, _, _ = struct.unpack_from('<8I', image, struct.unpack_from('<I', image, 28)[0] + i*32)
            if t == 1:
                a, e = max((base+v) & ~0xfff, end), (base+v+m+0xfff) & ~0xfff
                u.mem_map(a, e-a)
                end = e
                u.mem_write(base+v, image[o:o+f])
    segments(raw, 0)
    segments(libmad.read_bytes(), MAD)
    for base, size in ((DONE, 0x10000), (STUBS, 0x20000), (HEAP, 0x4000000), (STACK-0x400000, 0x410000)):
        u.mem_map(base, size)
    u.reg_write(UC_MIPS_REG_CP0_STATUS, u.reg_read(UC_MIPS_REG_CP0_STATUS) | 1 << 29 | 1 << 26)
    names, stubs, heap = {}, {}, [HEAP]
    exports = {x[7].split('@')[0]: int(x[1], 16) for x in map(str.split, subprocess.check_output(['readelf', '-sW', libmad], text=True).splitlines())
               if len(x) >= 8 and x[0].endswith(':') and x[6] not in ('UND', 'Ndx')}
    def target(name):
        if name in exports: return MAD + exports[name]
        if name not in stubs:
            stubs[name] = STUBS + 4*len(stubs)
            names[stubs[name]] = name
        return stubs[name]
    def link(path, base):
        local = glob = False
        for line in subprocess.check_output(['readelf', '-AW', path], text=True).splitlines():
            if 'Local entries' in line: local, glob = True, False
            elif 'Global entries' in line: local, glob = False, True
            elif 'PLT GOT' in line: local = glob = False
            x = line.split()
            if len(x) >= 3 and re.fullmatch('[0-9a-f]{8}', x[0]):
                if local and base: u.mem_write(int(x[0], 16)+base, struct.pack('<I', int(x[2], 16)+base))
                elif glob and len(x) >= 7:
                    weak = x[-1].startswith('_ITM') or x[-1] in ('__gmon_start__', '__cxa_finalize')
                    value = base + int(x[3], 16) if 'UND' not in x else 0 if weak else target(x[-1].split('@')[0])
                    if x[4] != 'OBJECT' or 'UND' not in x: u.mem_write(int(x[0], 16)+base, struct.pack('<I', value))
        for line in subprocess.check_output(['readelf', '-rW', path], text=True).splitlines():
            x = line.split()
            if len(x) >= 5 and x[2] == 'R_MIPS_JUMP_SLOT':
                u.mem_write(int(x[0], 16)+base, struct.pack('<I', target(x[4].split('@')[0])))
            elif len(x) >= 3 and x[2] == 'R_MIPS_REL32' and base:
                old = struct.unpack('<I', u.mem_read(int(x[0], 16)+base, 4))[0]
                u.mem_write(int(x[0], 16)+base, struct.pack('<I', old + base + (int(x[3], 16) if len(x) >= 5 else 0)))
    link(out/'hciplayer', 0)
    link(libmad, MAD)

    fs, fds, at, next_fd = {}, {}, {}, [10]
    def arg(n): return u.reg_read(regs[n])
    def word(p): return struct.unpack('<I', u.mem_read(p, 4))[0]
    def text(p):
        s = bytearray()
        while (c := u.mem_read(p + len(s), 1)[0]): s.append(c)
        return bytes(s)
    def new(n):
        p = heap[0] = (heap[0] + 15) & ~15
        heap[0] += max(n, 1)
        u.mem_write(p, bytes(n))
        return p
    def fresh(s):
        p = new(len(s)+1)
        u.mem_write(p, s)
        return p
    def compare(x, y): return (x > y) - (x < y)
    def find(p, i): return p + i if i >= 0 else 0
    def strsep():
        p = word(arg(0))
        if not p: return 0
        s, stop = text(p), text(arg(1))
        i = next((i for i, c in enumerate(s) if c in stop), -1)
        if i < 0:
            u.mem_write(arg(0), bytes(4))
        else:
            u.mem_write(p+i, b'\0')
            u.mem_write(arg(0), struct.pack('<I', p+i+1))
        return p
    def seek64():
        off = struct.unpack('<q', struct.pack('<II', arg(2), arg(3)))[0]
        fd, whence = arg(0), word(u.reg_read(UC_MIPS_REG_SP)+16)
        at[fd] = off + (0, at[fd], len(fds[fd]))[whence]
        u.reg_write(UC_MIPS_REG_V1, at[fd] >> 32 & 0xffffffff)
        return at[fd]
    def read():
        data = fds[arg(0)][at[arg(0)]:at[arg(0)]+arg(2)]
        u.mem_write(arg(1), data)
        at[arg(0)] += len(data)
        return len(data)
    def open_file():
        path = text(arg(0)).decode()
        if path not in fs: return -1
        fds[next_fd[0]], at[next_fd[0]] = fs[path], 0
        next_fd[0] += 1
        return next_fd[0]-1
    def posix_memalign():
        heap[0] = (heap[0]+arg(1)-1) & ~(arg(1)-1)
        u.mem_write(arg(0), struct.pack('<I', new(arg(2))))
        return 0
    def realloc():
        p = new(arg(1))
        if arg(0): u.mem_write(p, bytes(u.mem_read(arg(0), arg(1))))
        return p
    def divdi3():
        x, y = (struct.unpack('<q', struct.pack('<II', arg(i), arg(i+1)))[0] for i in (0, 2))
        q = abs(x)//abs(y) * (1 if (x < 0) == (y < 0) else -1)
        u.reg_write(UC_MIPS_REG_V1, q >> 32 & 0xffffffff)
        return q
    copy = lambda: (u.mem_write(arg(0), bytes(u.mem_read(arg(1), arg(2)))), arg(0))[1]
    libc = dict(malloc=lambda: new(arg(0)), calloc=lambda: new(arg(0)*arg(1)), realloc=realloc, memcpy=copy, memmove=copy,
                memalign=lambda: (heap.__setitem__(0, (heap[0]+arg(0)-1) & ~(arg(0)-1)), new(arg(1)))[1],
                memset=lambda: (u.mem_write(arg(0), bytes([arg(1) & 255])*arg(2)), arg(0))[1],
                memcmp=lambda: compare(bytes(u.mem_read(arg(0), arg(2))), bytes(u.mem_read(arg(1), arg(2)))),
                strlen=lambda: len(text(arg(0))), strcmp=lambda: compare(text(arg(0)), text(arg(1))),
                strcasecmp=lambda: compare(text(arg(0)).lower(), text(arg(1)).lower()),
                strncmp=lambda: compare(text(arg(0))[:arg(2)], text(arg(1))[:arg(2)]),
                strncasecmp=lambda: compare(text(arg(0))[:arg(2)].lower(), text(arg(1))[:arg(2)].lower()),
                strchr=lambda: find(arg(0), text(arg(0)).find(arg(1) & 255)), rindex=lambda: find(arg(0), text(arg(0)).rfind(arg(1) & 255)),
                strrchr=lambda: find(arg(0), text(arg(0)).rfind(arg(1) & 255)), strstr=lambda: find(arg(0), text(arg(0)).find(text(arg(1)))),
                strcasestr=lambda: find(arg(0), text(arg(0)).lower().find(text(arg(1)).lower())), strsep=strsep,
                memchr=lambda: find(arg(0), bytes(u.mem_read(arg(0), arg(2))).find(arg(1) & 255)), strdup=lambda: fresh(text(arg(0))), strcpy=lambda: (u.mem_write(arg(0), text(arg(1))+b'\0'), arg(0))[1],
                posix_memalign=posix_memalign, open64=open_file, open=open_file, read=read, lseek64=seek64, __errno_location=lambda: HEAP, __divdi3=divdi3)
    harmless = ('free', 'close', 'fflush', 'fprintf', 'printf', 'vfprintf', 'fputs', 'puts', 'fwrite', 'isatty', 'usleep', '__fxstat64',
                'pthread_mutex_lock', 'pthread_mutex_unlock', 'clock_gettime', '__xstat64', 'getenv', 'snprintf', 'sprintf', 'vsnprintf')
    def stub(u, address, size, _):
        name = names[address]
        assert name in libc or name in harmless, name
        u.reg_write(UC_MIPS_REG_V0, (libc[name]() if name in libc else 0) & 0xffffffff)
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, stub, begin=STUBS, end=STUBS+0x20000)
    def silent(u, address, size, _):
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, silent, begin=MSG, end=MSG)  # mp_msg
    new(16)  # errno
    def run(fn, *args):
        sp = STACK - 0x200
        for i, v in enumerate(args):
            if i < 4: u.reg_write(regs[i], v & 0xffffffff)
            else: u.mem_write(sp+16+4*(i-4), struct.pack('<I', v & 0xffffffff))
        u.reg_write(UC_MIPS_REG_SP, sp), u.reg_write(UC_MIPS_REG_RA, DONE), u.reg_write(UC_MIPS_REG_25, fn)
        u.emu_start(fn, DONE, count=10**9)
        assert u.reg_read(UC_MIPS_REG_PC) == DONE
        return u.reg_read(UC_MIPS_REG_V0)
    run(CODEC_CFG, 0)
    def play(sh):
        pcm = bytearray()
        while True:
            done = run(DECODE_AUDIO, sh, 5512, 0, 0)  # the last two steer the vendor's 24-bit path; this is 16
            pcm += u.mem_read(word(sh+0x78), word(sh+0x7c))
            u.mem_write(sh+0x7c, bytes(4))
            if done: return bytes(pcm)
    def decode(data, rewind=False):
        fs['/a.mp3'] = data
        name = fresh(b'/a.mp3')
        u.mem_write(URL, struct.pack('<I', name))
        stream = run(OPEN_STREAM, name, 0, 0, new(4))
        d = run(DEMUX_OPEN, stream, 0, 0, -1, -1, -2, name)
        ds = next(v for v in (word(d+i) for i in range(0x20, 0x60, 4)) if HEAP <= v < HEAP+0x4000000 and word(v+0x58) == d and word(v+0x6c))
        sh = word(ds+0x6c)
        assert run(BEST_CODEC, sh, 0, 0) and text(word(word(sh+4)+0x3e0)) == b'libmad'
        params = new(12)
        u.mem_write(params, struct.pack('<III', word(sh+0x3c), word(sh+0x40), word(sh+0x38)))
        run(INIT_FILTERS, sh, word(sh+0x3c), params, params+4, params+8)
        pcm = play(sh)
        if rewind:  # seek()'s demux_seek to 0 and resync_audio_stream
            u.mem_write(sh+0x6c, bytes(4)), u.mem_write(sh+0x5c, bytes(4))
            run(DEMUX_SEEK, d, 0, 0, 1)
            run(word(word(sh+0x88)+0x10), sh, 1, 0)
            pcm = (pcm, play(sh))
        run(UNINIT, sh)
        return pcm

    # A sweep, split where no frame ends. Each file is a track of a continuous recording.
    rate, frames, split = 44100, 26000, 13457
    pcm = array.array('h', (int(26000*math.sin(2*math.pi*(200*i + 0.03*i*i)/rate)) for i in range(frames) for _ in (0, 1)))
    def encode(name, part, *options):
        with wave.open(str(work/(name+'.wav')), 'wb') as f:
            f.setnchannels(2), f.setsampwidth(2), f.setframerate(rate), f.writeframes(part.tobytes())
        subprocess.check_call(['lame', '--silent', *options, work/(name+'.wav'), work/(name+'.mp3')])
        return (work/(name+'.mp3')).read_bytes()
    def walk(mp3):
        result, i = [], 0
        while i + 4 <= len(mp3) and mp3[i] == 255:
            size = 144000*(0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)[mp3[i+2] >> 4]//rate + (mp3[i+2] >> 1 & 1)
            result.append(mp3[i:i+size])
            i += size
        return result
    def extension(frame):
        flags = frame[43]
        return 44 + (4 if flags & 1 else 0) + (4 if flags & 2 else 0) + (100 if flags & 4 else 0) + (4 if flags & 8 else 0)
    def edited(mp3, offset, value):
        i = extension(mp3)
        return mp3[:i+offset] + value + mp3[i+offset+len(value):]

    plain = encode('plain', pcm[:split*2], '-b', '320')
    want = decode(plain)
    assert len(want) == split*4, len(want)
    # Encoder names, and a tag revision, that good players read.
    def signed(mp3):  # the tag's CRC-16, over the frame up to it
        i, crc = extension(mp3), 0
        for byte in mp3[:i+34]:
            crc ^= byte
            for _ in range(8):
                crc = crc >> 1 ^ 0xa001 if crc & 1 else crc >> 1
        return mp3[:i+34] + crc.to_bytes(2, 'big') + mp3[i+36:]
    for mp3 in (signed(edited(plain, 0, b'L3.99r\0\0\0')), signed(edited(plain, 0, b'GOGO3.13\0')),
                edited(plain, 9, bytes([plain[extension(plain)+9] | 0x10]))):
        assert decode(mp3) == want
    # Tags ahead of the first frame (ID3v2 with a footer, twice), and an Info frame's header unlike the audio's.
    id3 = b'ID3\4\0\x10\0\0\0\x10' + bytes(16) + b'3DI\4\0\x10\0\0\0\x10'
    assert decode(id3 + id3 + plain) == want
    low = encode('low', pcm[:split*2], '-b', '128')
    info = bytearray(walk(low)[0])
    info[extension(low):extension(low)+36] = plain[extension(plain):extension(plain)+36]
    assert decode(bytes(info) + plain[len(walk(plain)[0]):]) == want
    # Tail tags. Binary in APEv2 (a PNG cover's, say) holds frame syncs that decode as noise.
    def apev2(value):
        items = struct.pack('<II', len(value), 0) + b'Cover Art (Front)\0' + value
        tag = b'APETAGEX' + struct.pack('<IIII', 2000, len(items)+32, 1, 0xa0000000) + bytes(8)
        return tag + items + tag[:20] + struct.pack('<I', 0x80000000) + bytes(8)
    id3v1 = b'TAG' + bytes(124) + b'\xff'
    binary = random.Random(7).randbytes(60000)
    for tail in (id3v1, apev2(b'-6.5 dB'), apev2(b'-6.5 dB') + id3v1, apev2(binary), apev2(binary) + id3v1):
        assert decode(plain + tail) == want, len(tail)
    # A footer's size is only believed with a header at its place: half the file's here would otherwise cut music,
    # and with no header to look for it plays on.
    tags = apev2(b'-6.5 dB')
    wrong_size = tags[:-20] + struct.pack('<I', len(plain)//2) + tags[-16:]
    no_header = tags[:-12] + struct.pack('<I', 0) + tags[-8:]
    for tail in (wrong_size, wrong_size + id3v1, no_header):
        assert decode(plain + tail) == want, len(tail)
    # A tag frame with no usable extension, or VBRI: nothing to trim by, but the frame's own 1152 samples go, so the
    # track starts as the same audio with no tag frame does. Real lame frames, the name and fields overwritten.
    first_frame = walk(plain)[0]
    bare = decode(plain[len(first_frame):])
    no_extension = bytearray(first_frame)
    no_extension[extension(first_frame):extension(first_frame)+36] = bytes(36)
    no_count = bytearray(first_frame)
    no_count[43] &= ~1
    vbri = bytearray(len(first_frame))
    vbri[:4] = first_frame[:4]
    vbri[36:42] = b'VBRI\0\1'
    for tag_frame in (no_extension, no_count, vbri):
        assert decode(bytes(tag_frame) + plain[len(first_frame):]) == bare
    again = decode(bytes(no_extension) + plain[len(first_frame):], rewind=True)
    assert again[0] == again[1] == bare, (len(again[0]), len(again[1]), len(bare))
    # iTunSMPB in ID3v2's comment forms. A lame -t file is what iTunes' measured files are shaped like, a known delay
    # of 576 in the priming and a hard end: each must come out as LAME's own tag has it.
    def smpb_text(priming, samples, remainder):
        return ' 00000000 %08X %08X %016X 00000000 00000000' % (priming, remainder, samples)
    def latin1(text, language=b'eng'):
        return b'\0' + language + b'iTunSMPB\0' + text.encode()
    def utf16(text, language=b'eng'):
        return b'\1' + language + 'iTunSMPB'.encode('utf-16') + b'\0\0' + text.encode('utf-16')
    def tag(version, *frames):
        body = b''
        for name, frame in frames:
            size = len(frame)
            body += name + (size.to_bytes(3, 'big') if version == 2 else
                            size.to_bytes(4, 'big') + bytes(2) if version == 3 else
                            bytes((size >> s & 127) for s in (21, 14, 7, 0)) + bytes(2)) + frame
        size = len(body)
        return b'ID3' + bytes((version, 0, 0)) + bytes((size >> s & 127) for s in (21, 14, 7, 0)) + body
    bare_mp3 = plain[len(first_frame):]
    assert encode('notag', pcm[:split*2], '-b', '320', '-t') == bare_mp3
    total = len(walk(bare_mp3)) * 1152
    good = smpb_text(576, split, total - 576 - split)
    cover = random.Random(8).randbytes(70000)
    for ahead in (tag(2, (b'COM', b'\0eng\0some comment'), (b'COM', latin1(good))),
                  tag(2, (b'COM', utf16(good))),
                  tag(3, (b'APIC', cover), (b'COMM', latin1(good))),
                  tag(3, (b'COMM', utf16(good, b'xyz'))),
                  tag(4, (b'APIC', cover), (b'COMM', b'\3eng' + b'iTunSMPB\0' + good.encode())),
                  tag(3, (b'TXXX', b'\0iTunSMPB\0' + good.encode())),
                  tag(3, (b'TIT2', b'\0x')) + tag(3, (b'COMM', latin1(good)))):
        assert decode(ahead + bare_mp3) == want, ahead[:24]
    # With a tag frame, which leaves itself out of its count; a valid LAME extension wins over any comment.
    ahead = tag(3, (b'COMM', latin1(good)))
    assert decode(ahead + bytes(no_extension) + bare_mp3) == want
    other = tag(3, (b'COMM', latin1(smpb_text(1000, split, total - 1000 - split))))
    assert decode(other + plain) == want
    wrong = tag(3, (b'COMM', latin1(smpb_text(576, split + 1, total - 576 - split))))
    # What a refused tag leaves is what no tag leaves: the wrong sum, another description, too much priming.
    assert decode(wrong + bytes(no_extension) + bare_mp3) == bare
    named = tag(3, (b'COMM', b'\0engiTunNORM\0' + good.encode()))
    primed = tag(3, (b'COMM', latin1(smpb_text(16385, split, total - 16385 - split))))
    assert decode(named + bare_mp3) == bare and decode(primed + bare_mp3) == bare
    # Past the frames looked at, or in a tag laid out otherwise, it isn't found.
    padded = tag(3, *[(b'TXXX', b'\0k\0v')] * 64, (b'COMM', latin1(good)))
    assert decode(padded + bare_mp3) == bare
    # A real encoder's shape: `Lame3.100`, the tag's CRC-16 right for that, ID3v2.4 with a large cover ahead of the
    # first frame, and APEv2 then ID3v1 behind the last. FFmpeg's name list refuses this one, so FFmpeg's agreement
    # says nothing here; mpg123's has no list.
    field = extension(first_frame)
    cover = random.Random(9).randbytes(188000)
    named = signed(first_frame[:field] + b'Lame3.100' + first_frame[field+9:])
    spelled = tag(4, (b'APIC', b'\0image/jpeg\0\3\0' + cover), (b'TIT2', b'\3x')) + named + bare_mp3 + apev2(b'x'*1500) + id3v1
    assert decode(spelled) == want
    # Any other name without its CRC is no extension: only the tag frame goes.
    assert decode(edited(plain, 0, b'GOGO3.13\0')) == bare
    # Both sides of a boundary: nothing lost or inserted between the tracks.
    longer = array.array('h', (int(26000*math.sin(2*math.pi*(200*i + 0.003*i*i)/rate)) for i in range(60000) for _ in (0, 1)))
    for part in (longer[:30011*2], longer[30011*2:]):
        lame_tag, no_tag = encode('tagged', part, '-b', '320'), encode('untagged', part, '-b', '320', '-t')
        n, count = len(part)//2, len(walk(no_tag)) * 1152
        ahead = tag(2, (b'COM', latin1(smpb_text(576, n, count - 576 - n))))
        side = decode(ahead + no_tag)
        assert len(side) == n*4 and side == decode(lame_tag)
    # The trim only ever cuts: what's left is the untrimmed decode's samples, with padding beyond one frame too.
    assert want == bare[1105*4:][:len(want)]
    wide = bytearray(first_frame)
    wide[field+21:field+24] = (576 << 12 | 2154).to_bytes(3, 'big')
    wide_pcm = decode(bytes(wide) + bare_mp3)
    assert len(wide_pcm) == len(bare) - (1105 + 1625)*4 and wide_pcm == bare[1105*4:][:len(wide_pcm)]
    # A rewind plays the track from its first sample again: CBR, VBR (the next frame's size used to cut the tag frame
    # short and lose libmad its sync, two frames late), and a padded tag frame ahead of a shorter one.
    first, again = decode(plain, rewind=True)
    assert first == again == want, (len(first), len(again))
    for options in (('-V', '2'), ('-V', '0')):
        vbr = encode('vbr', pcm[:split*2], *options)
        first, again = decode(vbr, rewind=True)
        assert len(first) == split*4 and again == first, (options, len(first), len(again))
    low_frames = walk(low)
    head = bytearray(low_frames[0])
    head[2] |= 2
    head.append(0)
    assert not low_frames[1][2] & 2
    first, again = decode(bytes(head) + b''.join(low_frames[1:]), rewind=True)
    assert len(first) == split*4 and again == first, (len(first), len(again))


real_chain()
print('Real chain: lame files through stock demuxer, libmad and filters: encoder names (Lame3.100 with its cover and tails), ID3v2, Info header, tag frames without an extension, iTunSMPB, tail tags, rewind: OK')
