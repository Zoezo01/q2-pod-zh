#!/usr/bin/env python3
"""Opt-in lazy clock acquisition for the audited Q2 soc_rotator.ko only.

Keep the stock ABI and every conversion/IRQ instruction. The first valid request
acquires the clock under the stock mutex; it then stays on until module removal.
This deliberately does not attempt to gate a running or previously used engine.
"""
import argparse
import hashlib
import pathlib
import struct

STOCK_SHA256 = 'a341847285bc2e204f81be19d6cd942b0265275fba9c13ff8257a9f2574f2dc0'
MODULE_PATH = 'module_driver/soc_rotator.ko'
OWNED = 0x50  # appended after the entire stock .bss, never borrow object padding
HEADER = '<16sHHIIIIIHHHHHH'
SECTION = '<10I'
KEYS = ('name_offset', 'type', 'flags', 'addr', 'offset', 'size', 'link', 'info', 'align', 'entsize')


def require(ok, message):
    if not ok:
        raise ValueError(message)


class Elf:
    """Small ELF32 reader/writer; preserve section/symbol indices and ELF flags."""
    def __init__(self, data):
        self.header = list(struct.unpack_from(HEADER, data))
        h = self.header
        require(h[0][:7] == b'\x7fELF\x01\x01\x01' and h[1:3] == [1, 8], 'Expected MIPS ELF32 relocatable')
        require(h[5] == 0 and h[8] == 52 and h[11] == 40, 'Unexpected ELF headers')
        self.sections = []
        for i in range(h[12]):
            s = dict(zip(KEYS, struct.unpack_from(SECTION, data, h[6] + i * 40)))
            s['data'] = bytearray() if s['type'] == 8 else bytearray(data[s['offset']:s['offset'] + s['size']])
            self.sections.append(s)
        names = self.sections[h[13]]['data']
        for s in self.sections:
            s['name'] = bytes(names[s['name_offset']:]).split(b'\0', 1)[0].decode()
        self.by_name = {s['name']: s for s in self.sections}
        self.symbols = []
        strings = self.by_name['.strtab']['data']
        for off in range(0, len(self.by_name['.symtab']['data']), 16):
            n, value, size, info, other, section = struct.unpack_from('<IIIBBH', self.by_name['.symtab']['data'], off)
            name = bytes(strings[n:]).split(b'\0', 1)[0].decode()
            self.symbols.append(dict(name=name, value=value, size=size, info=info, other=other, section=section))

    def symbol_index(self, name):
        if name in self.by_name:
            section = self.sections.index(self.by_name[name])
            return next(i for i, s in enumerate(self.symbols) if s['info'] & 15 == 3 and s['section'] == section)
        return next(i for i, s in enumerate(self.symbols) if s['name'] == name)

    def add_function(self, name, value, size):
        strings = self.by_name['.strtab']['data']
        n = len(strings)
        strings.extend(name.encode() + b'\0')
        # Global, non-exported: appending leaves the existing local/global split intact.
        self.by_name['.symtab']['data'].extend(struct.pack('<IIIBBH', n, value, size, 0x12, 0, 3))

    def pack(self):
        data = bytearray(52)
        for i, s in enumerate(self.sections):
            if not i:
                continue
            data.extend(bytes((-len(data)) % max(s['align'], 1)))
            s['offset'] = len(data)
            if s['type'] != 8:
                s['size'] = len(s['data'])
                data.extend(s['data'])
        data.extend(bytes((-len(data)) % 4))
        self.header[6] = len(data)
        for s in self.sections:
            data.extend(struct.pack(SECTION, *(s[k] for k in KEYS)))
        struct.pack_into(HEADER, data, 0, *self.header)
        return bytes(data)


class Code:
    def __init__(self, elf, start):
        self.elf, self.start = elf, start
        self.words, self.relocations, self.labels, self.branches = [], [], {}, []

    @property
    def pc(self):
        return self.start + 4 * len(self.words)

    def emit(self, word):
        self.words.append(word)

    def imm(self, op, rt, rs, value):
        self.emit(op << 26 | rs << 21 | rt << 16 | (value & 0xffff))

    def address(self, reg, symbol):
        for op, rs, kind in ((15, 0, 5), (9, reg, 6)):
            self.relocations.append((self.pc, self.elf.symbol_index(symbol) << 8 | kind))
            self.imm(op, reg, rs, 0)

    def call_clock(self, name):
        self.address(2, name)
        self.emit(0x0040f809)  # jalr v0
        self.imm(35, 4, 18, 0)  # delay: a0 = rotator_dev.clk

    def jump(self, target):
        self.relocations.append((self.pc, self.elf.symbol_index('.text') << 8 | 4))
        self.emit(0x08000000 | target >> 2)
        self.emit(0)

    def branch(self, op, rs, rt, name):
        self.branches.append((len(self.words), name))
        self.imm(op, rt, rs, 0)
        self.emit(0)

    def label(self, name):
        self.labels[name] = self.pc

    def finish(self):
        for i, name in self.branches:
            delta = (self.labels[name] - (self.start + i * 4 + 4)) // 4
            require(-32768 <= delta <= 32767, 'Branch out of range')
            self.words[i] |= delta & 0xffff
        return struct.pack('<%dI' % len(self.words), *self.words)


def patch_module(data):
    require(hashlib.sha256(data).hexdigest() == STOCK_SHA256,
            'Unsupported rotator module: SHA-256 differs from audited stock (or already patched)')
    elf = Elf(data)
    text = elf.by_name['.text']['data']
    require(len(text) == 0x860 and elf.by_name['.bss']['size'] == OWNED, 'Unexpected module layout')
    pins = {0xcc: 0x8e510040, 0xd0: 0x3c020000, 0xd8: 0x24420000,
            0x578: 0x3c020000, 0x57c: 0x24420000, 0x734: 0x3c020000, 0x738: 0x24420000,
            0x73c: 0x0040f809, 0x740: 0x02202025, 0x744: 0x10400024}
    for off, word in pins.items():
        require(struct.unpack_from('<I', text, off)[0] == word, f'Unexpected instruction at {off:#x}')
    rels = list(struct.iter_unpack('<II', elf.by_name['.rel.text']['data']))
    removed = {0xd0: ('memset', 5), 0xd8: ('memset', 6),
               0x578: ('clk_disable', 5), 0x57c: ('clk_disable', 6),
               0x734: ('clk_prepare', 5), 0x738: ('clk_prepare', 6)}
    for off, (name, kind) in removed.items():
        require((off, elf.symbol_index(name) << 8 | kind) in rels, f'Unexpected relocation at {off:#x}')
    rels = [(off, info) for off, info in rels if off not in removed]
    c = Code(elf, len(text))
    acquire = c.pc
    c.imm(35, 2, 18, OWNED)  # mutex is already held; s2 is rotator_dev
    c.branch(5, 2, 0, 'ready')
    c.call_clock('clk_prepare')
    c.branch(5, 2, 0, 'fail')
    c.call_clock('clk_enable')
    c.branch(4, 2, 0, 'owned')
    # 0x28(sp) is unused until the stock wait entry is initialized, later.
    c.imm(43, 2, 29, 0x28)
    c.call_clock('clk_unprepare')
    c.imm(35, 2, 29, 0x28)
    c.branch(4, 0, 0, 'fail')
    c.label('owned')
    c.imm(9, 2, 0, 1)
    c.imm(43, 2, 18, OWNED)
    c.label('ready')
    c.imm(35, 17, 18, 0x40)  # replay displaced descriptor load
    c.address(2, 'memset')  # complete HI/LO pair here; stock d8 becomes nop
    c.jump(0xd4)
    c.label('fail')
    c.imm(43, 2, 29, 0x28)
    c.address(2, 'mutex_unlock')
    c.emit(0x0040f809)
    c.imm(9, 4, 18, 4)
    c.imm(35, 2, 29, 0x28)
    c.jump(0x404)  # stock canary check + restore, preserving the actual errno
    acquire_end = c.pc
    release = c.pc
    c.imm(35, 2, 17, OWNED)  # s1 is rotator_dev; a0 already holds its clock
    c.branch(4, 2, 0, 'put')
    c.address(2, 'clk_disable')
    c.emit(0x0040f809)
    c.imm(43, 4, 29, 0x1c)  # replay original delay slot; unprepare reloads it
    c.address(2, 'clk_unprepare')
    c.emit(0x0040f809)
    c.imm(35, 4, 29, 0x1c)
    c.imm(43, 0, 17, OWNED)
    c.label('put')
    c.jump(0x598)  # never acquired: skip disable/unprepare, still clk_put
    text.extend(c.finish())
    for off, target in ((0xcc, acquire), (0x578, release)):
        struct.pack_into('<II', text, off, 0x08000000 | target >> 2, 0)
        rels.append((off, elf.symbol_index('.text') << 8 | 4))
    struct.pack_into('<I', text, 0xd8, 0)
    # Fall through instead of adding a branch after the stock IS_ERR branch.
    # The remaining 748 lui is harmless: 74c immediately overwrites v0, and its
    # original HI16/LO16 pair (748/7d8) stays intact in the unreachable enable path.
    text[0x734:0x748] = bytes(20)
    rels += c.relocations
    elf.by_name['.rel.text']['data'] = bytearray(b''.join(struct.pack('<II', *r) for r in rels))
    elf.by_name['.bss']['size'] += 4
    elf.add_function('q2_rotator_acquire', acquire, acquire_end - acquire)
    elf.add_function('q2_rotator_release', release, c.pc - release)
    elf.by_name['.modinfo']['data'].extend(b'q2_clock_policy=lazy-first-use-v1\0')
    # Give the modified module its own deterministic build ID; never reuse stock's.
    note = elf.by_name['.note.gnu.build-id']['data']
    require(len(note) == 36 and note[12:16] == b'GNU\0', 'Unexpected build ID note')
    note[16:36] = bytes(20)
    note[16:36] = hashlib.sha256(elf.pack()).digest()[:20]
    return elf.pack()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stock', type=pathlib.Path)
    ap.add_argument('out', type=pathlib.Path)
    args = ap.parse_args()
    args.out.write_bytes(patch_module(args.stock.read_bytes()))
