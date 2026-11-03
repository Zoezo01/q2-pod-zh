#!/usr/bin/env python3
"""MP3 ReplayGain: the built hciplayer's ID3v2 path, run under Unicorn on real tags.

Stock code runs: the vendor's frame walker, FFmpeg's dictionary and ff_replaygain_export. Only libc,
the stream's buffer refill and the fd are modelled. Usage: test/id3.py BUILD_DIR"""
import pathlib, struct, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import jal, machine, symbols
from peq import ID3_CALLS, STOCK_ID3
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import *

out = pathlib.Path(sys.argv[1])/'peq'
path = out/'hciplayer'
raw = path.read_bytes()
id3_gain = symbols(out/'peq.elf')['id3_gain']
IMPORTS = 0x2800000  # each .got.plt slot gets IMPORTS + 4i, the way the loader binds it to libc
slots = {}
for line in subprocess.check_output(['readelf', '-rW', path], text=True).splitlines():
    s = line.split()
    if len(s) >= 5 and s[2] == 'R_MIPS_JUMP_SLOT': slots[int(s[0], 16)] = s[4].split('@')[0]
plt = {IMPORTS + 4*i: name for i, name in enumerate(slots.values())}
PENDING, UNKNOWN = 0xafc790, -0x80000000
for site in ID3_CALLS:
    assert struct.unpack_from('<I', raw, site-0x400000)[0] == jal(id3_gain), hex(site)

def syncsafe(n): return bytes([n >> 21 & 127, n >> 14 & 127, n >> 7 & 127, n & 127])
def frame(v, fid, body): return fid + (syncsafe(len(body)) if v == 4 else struct.pack('>I', len(body))) + b'\0\0' + body
def txxx(enc, key, val):
    if enc == 1: return b'\1' + b''.join(b'\xff\xfe' + s.encode('utf-16-le') + b'\0\0' for s in (key, val))[:-2]
    return bytes([enc]) + key.encode() + b'\0' + val.encode()
def tag(v, frames, pad=64):
    body = b''.join(frames) + bytes(pad)
    return b'ID3' + bytes([v, 0, 0]) + syncsafe(len(body)) + body
RG = (('REPLAYGAIN_TRACK_GAIN', '-6.50 dB'), ('REPLAYGAIN_TRACK_PEAK', '0.988123'),
      ('replaygain_album_gain', '-7.25 dB'), ('REPLAYGAIN_ALBUM_PEAK', '1.000000'))
WANT = (-650000, 98812, -725000, 100000)
ART = b'\0image/jpeg\0\3\0' + bytes(range(256)) * 40  # 10 KB of cover art: past the walker's 2 KB

def run(data, entry):
    """Pending gains after demux_audio_open's walker call, entered at entry, and the fd's offset."""
    u = machine(raw, 0x2000000)
    u.mem_map(0x3000000, 0x100000)
    u.mem_map(IMPORTS, 0x1000)
    for i, slot in enumerate(slots): u.mem_write(slot, struct.pack('<I', IMPORTS + 4*i))
    u.mem_write(PENDING, struct.pack('<iIiI', UNKNOWN, 0, UNKNOWN, 0))
    heap = [0x3000000]
    fd = {'at': 1234}
    s = 0x2000100  # stream_t: +0x14 fd, +0x1c flags (not seekable: the walker reads through), +0x28 buf_pos,
    #                +0x2c buf_len, +0x30 pos, +0x64 buffer. MPlayer read the 10-byte header already.
    u.mem_write(s, bytes(0x64))
    u.mem_write(s+0x14, struct.pack('<i', 3))
    u.mem_write(s+0x30, struct.pack('<q', 10))
    def a(n): return u.reg_read((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)[n])
    def cstr(p):
        b = bytearray()
        while (c := u.mem_read(p+len(b), 1)[0]): b.append(c)
        return bytes(b)
    def alloc(n):
        p = heap[0]; heap[0] = (p + n + 15) & ~15; return p
    def fill():
        pos = struct.unpack('<q', u.mem_read(s+0x30, 8))[0]
        chunk = data[pos:pos+2048]
        u.mem_write(s+0x64, chunk)
        u.mem_write(s+0x28, struct.pack('<iiq', 0, len(chunk), pos+len(chunk)))
        return len(chunk)
    def lseek(fdn, off, whence):
        assert fdn == 3
        fd['at'] = off if whence == 0 else fd['at'] + off
        return fd['at']
    def libc(name):
        if name == 'malloc': return alloc(a(0))
        if name == 'posix_memalign':
            u.mem_write(a(0), struct.pack('<I', alloc(a(2)))); return 0
        if name == 'realloc':
            p = alloc(a(1))
            if a(0): u.mem_write(p, bytes(u.mem_read(a(0), a(1))))
            return p
        if name == 'free': return 0
        if name == 'strlen': return len(cstr(a(0)))
        if name == 'memcpy':
            u.mem_write(a(0), bytes(u.mem_read(a(1), a(2)))); return a(0)
        if name == 'memset':
            u.mem_write(a(0), bytes([a(1) & 255]) * a(2)); return a(0)
        if name == 'memcmp':
            x, y = bytes(u.mem_read(a(0), a(2))), bytes(u.mem_read(a(1), a(2))); return (x > y) - (x < y)
        if name in ('memchr', 'strchr'):
            n = a(2) if name == 'memchr' else len(cstr(a(0))) + 1
            i = bytes(u.mem_read(a(0), n)).find(bytes([a(1) & 255])); return 0 if i < 0 else a(0) + i
        if name == 'lseek64':  # o32: fd in a0, the 64-bit offset in a2:a3, whence on the stack
            off = struct.unpack('<q', struct.pack('<II', a(2), a(3)))[0]
            r = lseek(a(0), off, struct.unpack('<i', u.mem_read(u.reg_read(UC_MIPS_REG_SP)+16, 4))[0])
            u.reg_write(UC_MIPS_REG_V1, r >> 32 & 0xffffffff); return r & 0xffffffff
        if name == 'read':
            chunk = data[fd['at']:fd['at']+a(2)]; u.mem_write(a(1), chunk); fd['at'] += len(chunk); return len(chunk)
        if name == 'strspn':
            b, accept = cstr(a(0)), cstr(a(1)); return len(b) - len(b.lstrip(accept))
        if name == 'strtol':
            b = cstr(a(0)); i = 1 if b[:1] in b'+-' else 0
            while i < len(b) and 48 <= b[i] <= 57: i += 1
            if a(1): u.mem_write(a(1), struct.pack('<I', a(0)+i))
            v = int(b[:i]) if i and b[:i] not in (b'+', b'-') else 0
            return v & 0xffffffff
        raise AssertionError('unmodelled import ' + name)
    def hook(u, addr, size, _):
        if addr == 0x4cc6b4: r = fill()        # stream_fill_buffer
        elif addr == 0x4460fc: r = 0           # mp_msg
        elif addr in plt: r = libc(plt[addr])
        else: return
        u.reg_write(UC_MIPS_REG_V0, r & 0xffffffff)
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, hook)
    u.reg_write(UC_MIPS_REG_SP, 0x200f000)
    u.reg_write(UC_MIPS_REG_A0, s); u.reg_write(UC_MIPS_REG_A1, len(data) - 10)
    u.reg_write(UC_MIPS_REG_RA, 0x2000000)
    u.emu_start(entry, 0x2000000, count=5_000_000)
    return struct.unpack('<iIiI', u.mem_read(PENDING, 16)), fd['at']

none = (UNKNOWN, 0, UNKNOWN, 0)
rg = lambda v, enc=3: [frame(v, b'TXXX', txxx(enc, k, x)) for k, x in RG]
title = frame(4, b'TIT2', b'\3Song')
cases = [  # name, tag, stock's result, the fix's
    ('2.4 Latin-1, gains first', tag(4, [title, *rg(4, 0)]), WANT, WANT),
    ('2.4 UTF-8, gains first', tag(4, [title, *rg(4)]), none, WANT),  # stock keeps the encoding byte in the key
    ('2.4, cover art first', tag(4, [title, frame(4, b'APIC', ART), *rg(4)]), none, WANT),
    ('2.4, 200-byte comment first', tag(4, [frame(4, b'COMM', b'\3eng\0' + b'x' * 195), *rg(4)]), none, WANT),
    ('2.3 UTF-16, cover art first', tag(3, [frame(3, b'APIC', ART), *rg(3, 1)]), none, WANT),
    ('2.3 Latin-1, gains first', tag(3, rg(3, 0)), WANT, WANT),
    ('no gains', tag(4, [title, frame(4, b'APIC', ART)]), none, none),
]
for name, data, stock, fixed in cases:
    data += b'\xff\xfb\x90\0' + bytes(400)
    assert run(data, STOCK_ID3)[0] == stock, ('stock', name, run(data, STOCK_ID3)[0])
    got, at = run(data, id3_gain)
    assert got == fixed, (name, got)
    assert at == 1234, (name, 'fd offset not restored', at)
print(f'MP3 ReplayGain: {len(cases)} ID3v2 layouts reach ff_replaygain_export; stock misses '
      f'{sum(s != f for _, _, s, f in cases)} of them: OK')
