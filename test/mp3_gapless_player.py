#!/usr/bin/env python3
"""Run the built MIPS gapless hooks, including preloads, seeks, fd restoration and cleanup.
Usage: python3 test/mp3_gapless_player.py BUILD
Only libc, file I/O and the downstream audio filters are modelled.
"""
from pathlib import Path
from collections import Counter
import struct
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from build import fileoff, jal, machine, symbols
from peq import MP3_ALLOC, MP3_FILTER, MP3_CLOSE, MP3_READ, DEMUX_READ
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import *

out = Path(sys.argv[1])/'peq'
raw = (out/'hciplayer').read_bytes()
syms = symbols(out/'peq.elf')
for address, value in ((MP3_ALLOC, jal(syms['mp3_private_alloc'])),
                       (MP3_FILTER, jal(syms['mp3_filter_entry'])), (MP3_CLOSE, syms['mp3_close']),
                       (MP3_READ, jal(syms['mp3_read']))):
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
            positions[fd] = off if not whence else positions[fd] + off
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

# Seek to zero: stock resync discards the tag frame before the decoder sees it.
u.mem_write(t[2]+8, struct.pack('<d', 0))
w(0xb152b4, 1)
call('mp3_gapless_seek', t[0], 1)
assert feed(t, pcm[1152*8:]) == pcm[2257*8:-671*8]
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
assert imports_called['read'] == counts['read'] + 1
assert imports_called['lseek64'] == counts['lseek64'] + 3
call('mp3_close', tr[0])
assert not allocations

# A seek before the first PCM block, untagged files and allocation failure.
for i, header in enumerate((h, bytes(417), h)):
    tr = track(0, header)
    if i == 0:
        w(0xb152b4, 1)
        call('mp3_gapless_seek', tr[0], 1)
        assert feed(tr, pcm[1152*8:]) == pcm[2257*8:-671*8]
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
