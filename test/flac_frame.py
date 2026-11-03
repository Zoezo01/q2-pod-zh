#!/usr/bin/env python3
"""Run complete stock FLAC decode_frame against an experimental player in Unicorn.

The stock header parser, subframe/residual decoders, predictors, DSP initializer,
decorrelation and PCM writers execute. Only libc mutex/memory/log services and
AVFrame allocation use host stubs; decoder buffers are preallocated. A narrow,
checked timestamp conversion bridge accommodates Unicorn's FR=0 FPU: the corpus
frame numbers/sample positions fit signed 32 bits. No PCM instruction is changed.

Existing fixtures: bp1-verify (LPC, fixed, Rice/escape/wasted/stereo), bp2-check
(fixed predictors/stereo), bitperfect (verbatim), each t16/t24.flac + .raw.
The .raw reference is signed int32 little-endian interleaved, before output shift.

Usage:
  python test/flac_frame.py STOCK --player PATCHED --elf PAYLOAD_ELF \
    --fixtures /home/tyler/spotinv/scratch-hires --out /mnt/e/q2-work/flac-lab/frame
With --elf alone, hook words are applied in memory using tools/flac.py.
Instruction counts exclude real OS costs and are not device cycles or watts.
"""
import argparse
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_BLOCK, UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_0, UC_MIPS_REG_PC

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build import fileoff, segments, symbols
from peq import PLAYER_SHA
from flac import patch_words, selection

DECODE = 0x5b6950
CODEC, FRAME, PACKET, GOT, CTX = 0x2000000, 0x2001000, 0x2002000, 0x2003000, 0x2004000
INPUT, OUTPUT, SAMPLE, STRIDE = 0x2010000, 0x2200000, 0x2300000, 0x80000
SP, END = 0x2ff0000, 0x3000000
GUARD = b'FLAC-QA!' * 4
FORMATS = {1: (2, False), 2: (4, False), 6: (2, True), 7: (4, True)}
# mthc1 high word is zero for the bounded fixture timestamps; cvt.s.l -> cvt.s.w.
TIME_BRIDGE = (
    (0x5b7330, 0x44e30800, 0), (0x5b7334, 0x46a00860, 0x46800860),
    (0x5b6a98, 0x44e70800, 0), (0x5b6a9c, 0x46a00860, 0x46800860),
)


def word(x):
    return struct.pack('<I', x & 0xffffffff)


def crc(data, bits, poly):
    c, mask = 0, (1 << bits) - 1
    for b in data:
        c ^= b << (bits - 8)
        for _ in range(8):
            c = ((c << 1) ^ (poly if c >> (bits - 1) else 0)) & mask
    return c


class Bits:
    def __init__(self, data, offset):
        self.data, self.pos = data, offset * 8

    def get(self, n):
        assert self.pos + n <= len(self.data) * 8, 'truncated fixture'
        if not n:
            return 0
        begin, shift = self.pos // 8, self.pos % 8
        size = (shift + n + 7) // 8
        value = int.from_bytes(self.data[begin:begin + size], 'big')
        self.pos += n
        return (value >> (size * 8 - shift - n)) & ((1 << n) - 1)

    def skip(self, n):
        assert n >= 0 and self.pos + n <= len(self.data) * 8
        self.pos += n

    def unary(self):
        n = 0
        while not self.get(1):
            n += 1
        return n


def parse_fixture(path):
    """Walk framing/residual lengths only; never reconstruct predicted samples."""
    raw = path.read_bytes()
    assert raw[:4] == b'fLaC'
    at, info = 4, None
    while True:
        kind, size = raw[at], int.from_bytes(raw[at + 1:at + 4], 'big')
        data = raw[at + 4:at + 4 + size]
        if kind & 127 == 0:
            assert size == 34
            packed = int.from_bytes(data[10:18], 'big')
            info = dict(rate=packed >> 44, channels=((packed >> 41) & 7) + 1,
                        bps=((packed >> 36) & 31) + 1, samples=packed & ((1 << 36) - 1),
                        maxblock=int.from_bytes(data[2:4], 'big'))
        at += size + 4
        if kind & 128:
            break
    assert info and info['channels'] == 2 and info['bps'] in (16, 24)
    assert info['samples'] < 0x80000000  # timestamp bridge precondition
    frames, coverage = [], Counter()
    while at < len(raw):
        start = at
        b = Bits(raw, at)
        assert b.get(14) == 0x3ffe and b.get(1) == 0
        variable = b.get(1)
        blockcode, ratecode, assignment, bpscode = b.get(4), b.get(4), b.get(4), b.get(3)
        assert b.get(1) == 0
        first = b.get(8)
        if first < 128:
            number = first
        else:
            leading = 0
            while first & (0x80 >> leading):
                leading += 1
            assert 2 <= leading <= 7
            number = first & ((1 << (7 - leading)) - 1)
            for _ in range(leading - 1):
                tail = b.get(8)
                assert tail & 0xc0 == 0x80
                number = (number << 6) | (tail & 63)
        assert number < 0x80000000 and (variable or number == len(frames))
        if blockcode == 1:
            count = 192
        elif 2 <= blockcode <= 5:
            count = 576 << (blockcode - 2)
        elif blockcode in (6, 7):
            count = b.get(8 if blockcode == 6 else 16) + 1
        else:
            assert 8 <= blockcode <= 15
            count = 256 << (blockcode - 8)
        if ratecode in (12, 13, 14):
            b.skip(8 if ratecode == 12 else 16)
        assert ratecode != 15
        b.skip(8)
        assert crc(raw[start:b.pos // 8], 8, 0x07) == 0, 'header CRC'
        bps = {0: info['bps'], 1: 8, 2: 12, 4: 16, 5: 20, 6: 24, 7: 32}[bpscode]
        assert bps == info['bps']
        channels = assignment + 1 if assignment <= 7 else 2
        assert channels == info['channels'] and assignment <= 10
        coverage[f'assignment_{assignment}'] += 1
        for channel in range(channels):
            effective = bps + int((assignment == 8 and channel == 1) or
                                  (assignment == 9 and channel == 0) or
                                  (assignment == 10 and channel == 1))
            assert b.get(1) == 0
            kind, wasted = b.get(6), b.get(1)
            if wasted:
                waste = b.unary() + 1
                effective -= waste
                coverage['wasted_bits'] += 1
            if kind == 0:
                coverage['constant'] += 1
                b.skip(effective)
                continue
            if kind == 1:
                coverage['verbatim'] += 1
                b.skip(count * effective)
                continue
            if 8 <= kind <= 12:
                order = kind - 8
                coverage[f'fixed_{order}'] += 1
                b.skip(order * effective)
            else:
                assert 32 <= kind <= 63
                order = kind - 31
                coverage[f'lpc_{order}'] += 1
                b.skip(order * effective)
                precision = b.get(4) + 1
                assert precision <= 15
                b.skip(5 + precision * order)
            method, partition = b.get(2), b.get(4)
            assert method in (0, 1) and count % (1 << partition) == 0
            width, plen = 4 + method, count >> partition
            coverage[f'rice_{method}'] += 1
            for p in range(1 << partition):
                residuals, param = plen - (order if not p else 0), b.get(width)
                assert residuals >= 0
                if param == (1 << width) - 1:
                    coverage['escape_partition'] += 1
                    b.skip(residuals * b.get(5))
                else:
                    for _ in range(residuals):
                        b.unary()
                        b.skip(param)
        b.skip((-b.pos) % 8)
        b.skip(16)
        at = b.pos // 8
        packet = raw[start:at]
        assert crc(packet, 16, 0x8005) == 0, 'frame CRC'
        frames.append((packet, count))
    assert sum(n for _, n in frames) == info['samples']
    reference = path.with_suffix('.raw').read_bytes()
    assert len(reference) == info['samples'] * info['channels'] * 4
    return info, frames, reference, coverage


def map_images(u, images):
    pages = set()
    for image in images:
        for _, (kind, off, addr, physical, filesz, memsz, flags, align) in segments(image):
            if kind == 1 and memsz:
                pages.update(range(addr & ~4095, (addr + memsz + 4095) & ~4095, 4096))
    for page in sorted(pages):
        u.mem_map(page, 4096)
    for image in images:
        for _, (kind, off, addr, physical, filesz, memsz, flags, align) in segments(image):
            if kind == 1 and filesz:
                u.mem_write(addr, image[off:off + filesz])


class Machine:
    def __init__(self, image, payload=None, hooks=(), names=None):
        u = self.u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        map_images(u, [image] + ([payload] if payload else []))
        u.mem_map(0x2000000, 0x1000000)
        u.mem_map(END, 0x10000)
        for address, old, new in hooks:
            assert self.read(address) == old, ('hook guard', hex(address))
            self.write(address, new)
        for address, old, new in TIME_BRIDGE:
            assert self.read(address) == old, ('timestamp bridge guard', hex(address))
            self.write(address, new)
        self.count, self.calls, self.logs = 0, Counter(), []
        self.last = deque(maxlen=8)
        self.stub(0xadb580, lambda: self.service('mutex_lock', 0))
        self.stub(0xadb3f0, lambda: self.service('mutex_unlock', 0))
        self.stub(0xadb1a0, self.memcmp)
        self.stub(0xadb970, self.memset)
        self.stub(0x6f0f4c, self.log)
        self.stub(0x5f5c28, self.frame_buffer)
        u.hook_add(UC_HOOK_BLOCK, self.block)
        entries = {'decode_frame': DECODE, 'dsp_init': 0x40a17c, 'residual': 0x5b6334,
                   'stock_lpc32': 0x5ba9bc, 'stock_lpc64': 0x5bab3c}
        entries.update(names or {})
        for name, address in entries.items():
            u.hook_add(UC_HOOK_CODE, self.hit, name, begin=address, end=address)

    def read(self, address):
        return struct.unpack('<I', self.u.mem_read(address, 4))[0]

    def write(self, address, value):
        self.u.mem_write(address, word(value))

    def reg(self, n):
        return self.u.reg_read(UC_MIPS_REG_0 + n)

    def result(self, value):
        self.u.reg_write(UC_MIPS_REG_0 + 2, value & 0xffffffff)

    def block(self, u, address, size, unused):
        self.count += size // 4
        self.last.append(hex(address))

    def hit(self, u, address, size, name):
        self.calls[name] += 1

    def stub(self, address, fn):
        self.u.mem_write(address, struct.pack('<II', 0x03e00008, 0))  # jr ra; nop
        self.u.hook_add(UC_HOOK_CODE, lambda u, a, s, x: fn(), begin=address, end=address)

    def service(self, name, result):
        self.calls[name] += 1
        self.result(result)

    def memcmp(self):
        n = self.reg(6)
        a = bytes(self.u.mem_read(self.reg(4), n))
        b = bytes(self.u.mem_read(self.reg(5), n))
        self.service('memcmp', (a > b) - (a < b))

    def memset(self):
        self.u.mem_write(self.reg(4), bytes([self.reg(5) & 255]) * self.reg(6))
        self.service('memset', self.reg(4))

    def log(self):
        address, text = self.reg(6), bytearray()
        for _ in range(512):
            c = bytes(self.u.mem_read(address, 1))
            if c == b'\0':
                break
            text += c
            address += 1
        self.logs.append(text.decode(errors='replace'))
        self.service('av_log', 0)

    def frame_buffer(self):
        assert self.reg(4) == CODEC and self.read(self.reg(5)) == FRAME
        for i in range(2):
            self.write(FRAME + i * 4, OUTPUT + i * STRIDE)
        self.write(FRAME + 0x40, FRAME)  # extended_data points to data[]
        self.service('frame_buffer', 0)

    def configure(self, info, sample_format):
        self.u.mem_write(CODEC, bytes(0x5000))
        self.write(CODEC + 0x3c, CTX)
        self.write(CODEC + 0x1a8, sample_format)
        self.write(CODEC + 0x1c0, 3)  # stereo channel layout, already negotiated
        width, planar = FORMATS[sample_format]
        for off, value in (
            (8, info['rate']), (12, 2), (16, info['bps']),
            (20, info['maxblock']), (24, 0x40000), (40, CODEC),
            (68, width * 8 - info['bps']), (76, 1),
            (80, SAMPLE), (84, SAMPLE + STRIDE),
        ):
            self.write(CTX + off, value)
        self.info, self.fmt = info, sample_format
        # Running player, consumer idle: heartbeat is not cleared asynchronously.
        self.u.mem_write(0xb10e68, b'\0')
        self.write(0xb10e70, 0)
        self.write(0xb10e6c, 0)

    def decode(self, packet, expected_count):
        self.count, self.calls, self.logs = 0, Counter(), []
        self.u.mem_write(INPUT, packet + bytes(64))
        self.write(PACKET + 24, INPUT)
        self.write(PACKET + 28, len(packet))
        self.write(GOT, 0)
        width, planar = FORMATS[self.fmt]
        out_ranges = ([(OUTPUT + c * STRIDE, expected_count * width) for c in range(2)]
                      if planar else [(OUTPUT, expected_count * width * 2)])
        sample_ranges = [(SAMPLE + c * STRIDE, expected_count * 4) for c in range(2)]
        for address, length in out_ranges + sample_ranges:
            self.u.mem_write(address - 32, GUARD + bytes([0xcc]) * length + GUARD)
        preserved = {r: 0x70000000 + r for r in (*range(16, 24), 28, 30)}
        for reg, value in preserved.items():
            self.u.reg_write(UC_MIPS_REG_0 + reg, value)
        for reg, value in {4: CODEC, 5: FRAME, 6: GOT, 7: PACKET, 29: SP, 31: END}.items():
            self.u.reg_write(UC_MIPS_REG_0 + reg, value)
        try:
            self.u.emu_start(DECODE, END, count=30000000)
        except Exception as e:
            raise AssertionError(('emulator fault', hex(self.u.reg_read(UC_MIPS_REG_PC)), list(self.last))) from e
        assert self.u.reg_read(UC_MIPS_REG_PC) == END, 'instruction limit'
        assert self.reg(29) == SP
        for reg, value in preserved.items():
            assert self.reg(reg) == value, ('callee-saved', reg)
        consumed, got, count = self.reg(2), self.read(GOT), self.read(CTX + 64)
        assert consumed == len(packet) and got == 1 and count == expected_count, (
            'frame result', consumed, len(packet), got, count, expected_count, self.logs)
        for address, length in out_ranges + sample_ranges:
            assert bytes(self.u.mem_read(address - 32, 32)) == GUARD, ('front canary', hex(address))
            assert bytes(self.u.mem_read(address + length, 32)) == GUARD, ('rear canary', hex(address))
        assert self.calls['dsp_init'] == 1, 'real DSP initializer did not run'
        output = [bytes(self.u.mem_read(a, n)) for a, n in out_ranges]
        return output, dict(instructions=self.count, calls=dict(self.calls), consumed=consumed)


def expected_output(raw, bps, fmt):
    values = struct.unpack('<%di' % (len(raw) // 4), raw)
    width, planar = FORMATS[fmt]
    shift, mask = width * 8 - bps, (1 << (width * 8)) - 1
    def pack(rows):
        return b''.join(((v << shift) & mask).to_bytes(width, 'little') for v in rows)
    return [pack(values[0::2]), pack(values[1::2])] if planar else [pack(values)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stock', type=Path)
    p.add_argument('--player', type=Path)
    p.add_argument('--elf', type=Path)
    p.add_argument('--experiments', default='sync,lpc,output')
    p.add_argument('--fixtures', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--limit', type=int, help='first N frames per file/format, for harness debugging')
    a = p.parse_args()
    assert a.player or a.elf, 'supply --player or --elf'
    stock = a.stock.read_bytes()
    assert hashlib.sha256(stock).hexdigest() == PLAYER_SHA, 'unexpected stock player'
    syms = symbols(a.elf) if a.elf else {}
    hooks = patch_words(selection(a.experiments), syms) if syms else ()
    names = {name: address for name, address in syms.items() if name.startswith('flac_')}
    if a.player:
        candidate = a.player.read_bytes()
        for address, old, new in hooks:
            assert struct.unpack_from('<I', candidate, fileoff(candidate, address))[0] == new, (
                'player hook mismatch', hex(address))
        new = Machine(candidate, names=names)
    else:
        candidate = a.elf.read_bytes()
        new = Machine(stock, candidate, hooks, names)
    old = Machine(stock)
    report = dict(stock_sha256=PLAYER_SHA, candidate_sha256=hashlib.sha256(candidate).hexdigest(),
                  experiments=selection(a.experiments), frames=0, samples=0, cases=[],
                  limitation='Unicorn instruction counts with cheap libc/allocator stubs; '
                             'timestamp-only FR=0 bridge; no hardware-cycle or power claim.')
    totals = {'stock': Counter(), 'candidate': Counter()}
    a.out.mkdir(parents=True, exist_ok=True)
    for family in ('bp1-verify', 'bp2-check', 'bitperfect'):
        for bps in (16, 24):
            path = a.fixtures / family / f't{bps}.flac'
            info, frames, reference, coverage = parse_fixture(path)
            for fmt in ((1, 6) if bps == 16 else (2, 7)):
                old.configure(info, fmt)
                new.configure(info, fmt)
                start, at, before, after = time.monotonic(), 0, 0, 0
                calls_old, calls_new = Counter(), Counter()
                selected = frames[:a.limit] if a.limit else frames
                for index, (packet, count) in enumerate(selected):
                    pcm = reference[at * 8:(at + count) * 8]
                    expected = expected_output(pcm, bps, fmt)
                    x, s = old.decode(packet, count)
                    y, t = new.decode(packet, count)
                    assert x == y == expected, ('PCM mismatch', family, bps, fmt, index)
                    before += s['instructions']
                    after += t['instructions']
                    calls_old.update(s['calls'])
                    calls_new.update(t['calls'])
                    at += count
                row = dict(family=family, bps=bps, sample_format=fmt, frames=len(selected),
                           stereo_sample_frames=at, stock_instructions=before,
                           candidate_instructions=after,
                           reduction_percent=round(100 * (1 - after / before), 2),
                           stock_calls=dict(calls_old), candidate_calls=dict(calls_new),
                           fixture_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                           reference_sha256=hashlib.sha256(reference).hexdigest(),
                           coverage=dict(coverage))
                report['cases'].append(row)
                report['frames'] += len(selected)
                report['samples'] += at * 2
                totals['stock'].update(calls_old)
                totals['candidate'].update(calls_new)
                print(f'{family} {bps}-bit fmt={fmt}: {len(selected)} frames bitexact; '
                      f'{before} -> {after} instructions ({row["reduction_percent"]}% fewer), '
                      f'{time.monotonic() - start:.1f}s', flush=True)
                (a.out / 'frame-results.json').write_text(json.dumps(report, indent=2) + '\n')
    report['calls'] = {k: dict(v) for k, v in totals.items()}
    (a.out / 'frame-results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{report["frames"]} frame decodes per variant; {report["samples"]} scalar samples checked')
    print(f'Report: {a.out / "frame-results.json"}')


if __name__ == '__main__':
    main()
