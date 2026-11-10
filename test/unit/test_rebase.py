"""tools/rebase.py on two builds of a toy MIPS program assembled here: no stock file involved."""
import collections
import io
import struct
import types

import pytest

import rebase

V0, V1, A0, T9, GP, SP, RA = 2, 3, 4, 25, 28, 29, 31
NOP, JR_RA, JALR_T9, JR_T9 = 0, 0x03e00008, 0x0320f809, 0x03200008


def lui(rt, hi): return 0x3c000000 | rt << 16 | hi
def addiu(rt, rs, lo): return 0x24000000 | rs << 21 | rt << 16 | lo & 0xffff
def ori(rt, rs, lo): return 0x34000000 | rs << 21 | rt << 16 | lo
def lw(rt, off, rs): return 0x8c000000 | rs << 21 | rt << 16 | off & 0xffff
def sw(rt, off, rs): return 0xac000000 | rs << 21 | rt << 16 | off & 0xffff
def jal(target): return 0x0c000000 | target >> 2 & 0x3ffffff
def bal(words): return 0x04110000 | words & 0xffff
def beq(rs, rt, words): return 0x10000000 | rs << 21 | rt << 16 | words & 0xffff
def hi(a): return (a + 0x8000) >> 16
def lo(a): return a & 0xffff


def image(sections, symbols=(), local=2, gotsym=None):
    """An ELF of sections {name: (address, bytes, or a size for .bss)} and its dynamic symbols
    (name, value, size, type, section index or 0 when undefined)."""
    dynstr, dynsym = b'\0', bytes(16)
    for name, value, size, kind, shndx in symbols:
        dynsym += struct.pack('<IIIBBH', len(dynstr), value, size, 0x10 | kind, 0, shndx)
        dynstr += name.encode() + b'\0'
    dynamic = struct.pack('<4I', 0x7000000a, local, 0x70000013, len(symbols) + 1 if gotsym is None else gotsym)
    sections = {'.dynsym': (0x400100, dynsym), '.dynstr': (0x400400, dynstr), '.dynamic': (0x400700, dynamic), **sections}
    names, body, table = b'\0', bytearray(52), [bytes(40)]
    for name, (addr, content) in sections.items():
        kind = 8 if isinstance(content, int) else 1
        table.append(struct.pack('<10I', len(names), kind, 0, addr, len(body), content if kind == 8 else len(content), 0, 0, 4, 0))
        names += name.encode() + b'\0'
        if kind != 8:
            body += content + bytes(-len(content) % 4)
    table.append(struct.pack('<10I', len(names), 3, 0, 0, len(body), len(names) + 10, 0, 0, 1, 0))
    body += names + b'.shstrtab\0'
    body += bytes(-len(body) % 4)
    struct.pack_into('<4sBB', body, 0, b'\x7fELF', 1, 1)
    struct.pack_into('<H', body, 18, 8)
    struct.pack_into('<I', body, 32, len(body))
    struct.pack_into('<HHH', body, 46, 40, len(table), len(table) - 1)
    return bytes(body + b''.join(table))


class Toy:
    """One build of the toy: exported alpha, beta and gamma with the statics one and two between them, a lazy
    stub and a PLT entry, strings, a pointer table, a GOT and a .bss. The arguments are what a new build
    changes: the order of the functions, words added inside alpha, the data's shift, a GOT slot more, and
    gamma's body."""
    TEXT, STUBS, PLT = 0x401000, 0x402000, 0x402100

    def __init__(self, order=('alpha', 'one', 'two', 'beta', 'gamma'), pad=0, shift=0, slot=0, gamma=1, hello=b'hello, world', tail=0, extra=()):
        self.rodata, self.data, self.got, self.bss = 0x403000 + shift, 0x404000 + shift, 0x405000 + 4 * shift, 0x406000 + shift
        self.gp = self.got + 0x7ff0
        self.slots = ['lazy', 'module'] + ['spare'] * slot + ['text', 'rodata', 'data', 'g_flag', 'printf']
        self.s = {'hello': self.rodata, 'world': self.rodata + 16, 'consts': self.rodata + 0x40,
                  'table': self.data, 'counter': self.data + 0x20, 'g_flag': self.bss + 0x10, 'state': self.bss + 0x20,
                  'printf': self.STUBS, 'puts': self.PLT + 32}
        at = lambda name: lambda: self.s[name]
        page = lambda name, rt: lambda: lw(rt, 4 * self.slots.index(name) - 0x7ff0, GP)
        low = lambda name, base, rt, rs: lambda: addiu(rt, rs, self.s[name] - base())
        code = {
            'alpha': [
                addiu(SP, SP, -32), sw(RA, 28, SP), *[NOP] * pad,
                page('text', T9), low('one', lambda: self.TEXT, T9, T9), JALR_T9, NOP,          # a local PIC call
                page('rodata', A0), low('hello', lambda: self.rodata, A0, A0),                  # a string
                page('printf', T9), JALR_T9, NOP,                                               # an import
                page('g_flag', V0), lw(V1, 0, V0),                                              # a global object
                lambda: jal(self.s['two']), NOP,
                lw(RA, 28, SP), JR_RA, addiu(SP, SP, 32)],
            'one': [
                addiu(SP, SP, -24),
                lambda: lui(V0, hi(self.s['counter'])), lambda: lw(V1, lo(self.s['counter']), V0),
                addiu(V1, V1, 1), lambda: sw(V1, lo(self.s['counter']), V0),
                JR_RA, addiu(SP, SP, 24)],
            'two': [
                addiu(SP, SP, -16),
                lambda: lui(A0, hi(self.s['world'])), lambda: addiu(A0, A0, lo(self.s['world'])),
                lambda: lui(V0, self.s['state'] >> 16), lambda: ori(V0, V0, lo(self.s['state'])),
                lambda: bal((self.s['one'] - self.s['two']) // 4 - 6), NOP,
                beq(V0, 0, 1), NOP,
                JR_RA, addiu(SP, SP, 16)],
            'beta': [
                addiu(SP, SP, -40),
                page('data', V0), low('table', lambda: self.data, V0, V0),
                lambda: jal(self.s['two']), NOP,
                lambda: jal(self.s['puts']), NOP,
                page('text', T9), low('one', lambda: self.TEXT, T9, T9), JR_T9, addiu(SP, SP, 40)],  # a tail call
            'gamma': [addiu(SP, SP, -48), ori(V0, 0, 0x1234 * gamma), ori(V1, 0, 7), 0x00431021 * gamma,
                      JR_RA, addiu(SP, SP, 48)],
        }
        pc = self.TEXT
        for name in order:
            self.s[name] = pc
            pc += 4 * len(code[name])
        self.sizes = {name: 4 * len(code[name]) for name in order}
        self.words = [w() if callable(w) else w for name in order for w in code[name]] + [addiu(V0, 0, k) for k in range(tail)]
        values = {'lazy': 0, 'module': 0x80000000, 'spare': 0x400000, 'text': self.TEXT, 'rodata': self.rodata, 'data': self.data,
                  'g_flag': self.s['g_flag'], 'printf': self.STUBS}
        rodata = hello.ljust(16, b'\0') + b'second string'.ljust(48, b'\0') + struct.pack('<4I', 0x11111111, self.s['one'], 0x22222222, 0x33333333)
        data = struct.pack('<8I', self.s['alpha'], self.s['one'], self.s['two'], 0xdeadbeef, 7, 0, 0, 0) + struct.pack('<2I', 5, 0x5555aaaa)
        stub = struct.pack('<4I', 0x8f998010, 0x03e07825, JALR_T9, 0x24180005)
        plt = bytes(32) + struct.pack('<4I', lui(15, hi(0x404100)), 0x8df90000 | lo(0x404100), JR_T9, 0x25f80000 | lo(0x404100))
        symbols = [(name, self.s[name], self.sizes[name], 2, 4) for name in ('alpha', 'beta', 'gamma')]
        symbols += [('g_flag', self.s['g_flag'], 4, 1, 9), ('printf', 0, 0, 2, 0), ('puts', 0, 0, 2, 0)]
        self.blob = image({'.rel.plt': (0x400800, struct.pack('<II', 0x404100, 6 << 8 | 127)),
                           '.text': (self.TEXT, struct.pack(f'<{len(self.words)}I', *self.words)),
                           '.MIPS.stubs': (self.STUBS, stub + bytes(16)), '.plt': (self.PLT, plt + bytes(4)),
                           '.rodata': (self.rodata, rodata), '.data': (self.data, data),
                           '.got': (self.got, struct.pack(f'<{len(self.slots)}I', *(values[s] for s in self.slots))),
                           '.bss': (self.bss, 0x100), **dict(extra)},
                          symbols, local=len(self.slots) - 2, gotsym=4)
        self.elf = rebase.Elf(self.blob)


@pytest.fixture
def old():
    return Toy()


@pytest.fixture
def new():
    """The next build: alpha grew by three words, one and two changed places, the data moved, the GOT gained a slot."""
    return Toy(order=('alpha', 'two', 'one', 'beta', 'gamma'), pad=3, shift=0x100, slot=1)


@pytest.fixture
def mp(old, new):
    return rebase.Map(old.elf, new.elf)


def test_small_helpers():
    with pytest.raises(ValueError, match='^no$'):
        rebase.check(False, 'no')
    rebase.check(True, 'no')
    assert [rebase.s16(x) for x in (0, 0x7fff, 0x8000, 0xffff)] == [0, 0x7fff, -0x8000, -1]
    count = collections.Counter
    assert rebase.decisive(count()) is None
    assert rebase.decisive(count({5: 1})) == 5
    assert rebase.decisive(count({5: 3, 6: 1})) == 5
    assert rebase.decisive(count({5: 2, 6: 1})) is None  # twice the next is not over twice
    assert rebase.decisive(count({5: 1, 6: 1})) is None


def test_low_opcodes():
    assert [op for op in range(64) if rebase.low(op)] == [9, 0x0d, *range(0x20, 0x40)]


@pytest.mark.parametrize('word, masked', [
    (jal(0x401234), 0x0c000000), (0x08100000, 0x08000000),                    # jal, j: the target
    (lui(V0, 0x1234), lui(V0, 0)),                                            # lui: the high half
    (addiu(A0, V0, 0x1234), addiu(A0, V0, 0)), (ori(A0, V0, 0x1234), ori(A0, V0, 0)),
    (lw(V1, 0x1234, GP), lw(V1, 0, GP)), (sw(V1, 0x1234, V0), sw(V1, 0, V0)),
    (0xfc611234, 0xfc610000),                                                 # the last opcode of the range
    (addiu(V0, 0, 5), addiu(V0, 0, 5)), (addiu(SP, SP, -32), addiu(SP, SP, -32)), (sw(RA, 28, SP), sw(RA, 28, SP)),
    (lw(V0, 16, 30), lw(V0, 16, 30)),                                         # off $zero, $sp, $fp: kept
    (bal(5), bal(0)), (beq(V0, V1, 9), beq(V0, V1, 0)), (0x14430009, 0x14430000), (0x18400009, 0x18400000),
    (0x1c400009, 0x1c400000), (0x50430009, 0x50430000), (0x5c400009, 0x5c400000),
    (0x00431021, 0x00431021), (0x28430005, 0x28430005), (0x30430005, 0x30430005), (0x7c03e83b, 0x7c03e83b),
])
def test_heavy_masks_what_may_carry_an_address(word, masked):
    assert rebase.heavy(word) == masked


def test_lis_is_the_longest_rising_chain():
    assert rebase.lis([]) == []
    assert rebase.lis([(0, 5)]) == [(0, 5)]
    assert rebase.lis([(0, 0), (1, 9), (2, 1), (3, 2), (4, 8), (5, 3)]) == [(0, 0), (2, 1), (3, 2), (5, 3)]
    assert rebase.lis([(0, 3), (1, 2), (2, 1)]) == [(2, 1)]
    assert rebase.lis([(0, 1), (1, 1), (2, 2)]) == [(1, 1), (2, 2)]  # equal is not rising


def test_chunk_aligns_equal_runs_and_leaves_the_rest():
    a, b = [1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6]
    m = [-1] * 8
    rebase.chunk([0, *a, 0], [9, 9, *b], 1, 7, 2, 8, m)
    assert m == [-1, 2, 3, 4, 5, 6, 7, -1]
    m = [-1] * 9
    rebase.chunk([1, 2, 3, 7, 7, 4, 5, 6, 8], [1, 2, 3, 4, 5, 6, 9, 9], 0, 9, 0, 8, m)
    assert m == [0, 1, 2, -1, -1, 3, 4, 5, -1]
    m = [-1] * 4
    rebase.chunk([1, 2, 9, 9], [1, 2, 8, 8], 0, 4, 0, 4, m)   # a run under three words counts only at the start
    assert m == [0, 1, -1, -1]
    m = [-1] * 4
    rebase.chunk([9, 9, 1, 2], [8, 8, 1, 2], 0, 4, 0, 4, m)
    assert m == [-1, -1, -1, -1]
    m = [-1] * 5
    rebase.chunk([9, 1, 2, 3, 9], [8, 8, 1, 2, 3], 0, 5, 0, 5, m)
    assert m == [-1, 2, 3, 4, -1]


def test_chunk_splits_what_is_too_big_on_runs_found_once(monkeypatch):
    monkeypatch.setattr(rebase, 'BIG', 40)
    a = list(range(100, 112)) + [1, 1, 1] + list(range(200, 212))
    b = list(range(100, 112)) + [2, 2] + list(range(200, 212)) + [3]
    m = [-1] * len(a)
    rebase.chunk(a, b, 0, len(a), 0, len(b), m, k=3)
    assert m == list(range(12)) + [-1, -1, -1] + list(range(14, 26))
    # a run found twice on one side is no landmark, and one out of order is dropped
    a = [5, 6, 7, 5, 6, 7, 10, 11, 12, 13, 20, 21, 22, 23, 24, 25, 26]
    b = [20, 21, 22, 23, 24, 25, 26, 5, 6, 7, 10, 11, 12, 13, 30, 31, 32]
    m = [-1] * len(a)
    rebase.chunk(a, b, 0, len(a), 0, len(b), m, k=3)
    assert m == [-1] * 10 + list(range(7))


def test_chunk_takes_no_short_run_and_no_word_for_junk():
    m = [-1] * 6
    rebase.chunk([1, 2, 50, 51, 52, 53], [60, 61, 1, 2, 70, 71], 0, 6, 0, 6, m)
    assert m == [-1] * 6                                                    # two words alike, not where both begin
    zeros, m = [0] * 250, [-1] * 256
    rebase.chunk([1, 2, 3] + zeros + [4, 5, 6], [7, 8, 9] + zeros + [10, 11, 12], 0, 256, 0, 256, m)
    assert m == [-1] * 3 + list(range(3, 253)) + [-1] * 3                   # a long run of one word is matched all the same


def test_align_between_anchors():
    a = [1, 2, 3, 4, 5, 6, 7, 8]
    assert rebase.align(a, a, []) == (list(range(8)), 0)
    assert rebase.align(a, [9, 9, *a], [(0, 2)]) == (list(range(2, 10)), 0)
    assert rebase.align(a, [9, 9, *a], [(0, 2), (0, 2)]) == (list(range(2, 10)), 0)       # one anchor named twice
    assert rebase.align(a, [5, 6, 7, 8, 1, 2, 3, 4], [(0, 4), (4, 0)]) == ([-1, -1, -1, -1, 0, 1, 2, 3], 1)
    assert rebase.align([1, 2, 3, 4], [1, 2, 3, 4, 1, 2, 3, 4], [(0, 4)]) == ([4, 5, 6, 7], 0)


def test_elf_reads_sections_symbols_got_and_imports(old):
    e = old.elf
    assert (e.text, e.end, e.gp, len(e.words)) == (0x401000, 0x401000 + 4 * len(old.words), 0x40cff0, len(old.words))
    assert e.words == old.words
    assert e.sec['.rodata'] == (0x403000, e.sec['.rodata'][1], 80, 1) and e.sec['.bss'][2:] == (0x100, 8)
    assert e.syms == {'alpha': (old.s['alpha'], old.sizes['alpha'], 'FUNC'), 'beta': (old.s['beta'], old.sizes['beta'], 'FUNC'),
                      'gamma': (old.s['gamma'], old.sizes['gamma'], 'FUNC'), 'g_flag': (0x406010, 4, 'OBJECT')}
    assert e.dynnames == ['', 'alpha', 'beta', 'gamma', 'g_flag', 'printf', 'puts']
    assert e.got == {0x405014: 'g_flag', 0x405018: 'printf', 0x40501c: 'puts'}
    assert e.imports == {0x402000: 'printf', 0x402120: 'puts'}
    assert [e.section(a) for a in (0x401000, 0x403000, 0x40304f, 0x403050, 0x406000, 0x4060ff, 0x406100, 0)] == \
        ['.text', '.rodata', '.rodata', None, '.bss', '.bss', None, None]
    assert e.u32(0x403040) == 0x11111111 and e.u32(0x406010) is None and e.u32(0x500000) is None
    assert e.u32(e.sec['.got'][0] + 4 * (len(old.slots) - 1)) == 0x402000
    assert e.cstr(0x403000) == b'hello, world' and e.cstr(0x403010) == b'second string' and e.cstr(0x406010) is None
    assert e.off(0x403004) == e.sec['.rodata'][1] + 4 and e.off(0x403050) is None and e.off(0x402fff) is None
    with pytest.raises(ValueError, match='Not a 32-bit little-endian MIPS ELF'):
        rebase.Elf(b'\x7fELF\x02\x01' + old.blob[6:])
    with pytest.raises(ValueError, match='Not a 32-bit'):
        rebase.Elf(old.blob[:18] + b'\x03\0' + old.blob[20:])


def test_elf_reads_every_stub_and_plt_entry():
    """Two lazy stubs and two PLT entries, among entries that only look like one."""
    stub = lambda index: struct.pack('<4I', 0x8f998010, 0x03e07825, JALR_T9, 0x24180000 | index)
    plt = lambda slot: struct.pack('<4I', lui(15, hi(slot)), 0x8df90000 | lo(slot), JR_T9, 0x25f80000 | lo(slot))
    stubs = stub(1) + stub(2) + struct.pack('<4I', 0x8f998010, 0, 0, 0) + struct.pack('<4I', 0, 0, 0, 0x24180001) + bytes(16)
    entries = (bytes(32) + plt(0x404100) + plt(0x404104) + struct.pack('<4I', lui(15, hi(0x404100)), 0, 0, 0)
               + struct.pack('<4I', 0, 0x8df90000 | lo(0x404100), 0, 0) + plt(0x404108) + bytes(4))
    names = [(n, 0, 0, 2, 0) for n in ('read', 'write', 'open', 'close')]
    e = rebase.Elf(image({'.rel.plt': (0x400800, struct.pack('<4I', 0x404100, 3 << 8 | 127, 0x404104, 4 << 8 | 127)),
                          '.text': (0x401000, struct.pack('<2I', JR_RA, NOP)), '.MIPS.stubs': (0x402000, stubs), '.plt': (0x402100, entries),
                          '.got': (0x405000, bytes(8))}, names))
    assert e.imports == {0x402000: 'read', 0x402010: 'write', 0x402120: 'open', 0x402130: 'close'}
    assert e.got == {} and e.syms == {}
    bare = rebase.Elf(image({'.text': (0x401000, struct.pack('<2I', JR_RA, NOP)), '.got': (0x405000, bytes(8))}))
    assert bare.imports == {} and bare.words == [JR_RA, NOP] and bare.gp == 0x405000 + 0x7ff0



def test_elf_got_without_its_tags():
    text = {'.text': (0x401000, struct.pack('<2I', JR_RA, NOP)), '.got': (0x405000, bytes(16))}
    syms = [('f', 0x401000, 8, 2, 4), ('g', 0x401004, 4, 2, 4)]
    assert rebase.Elf(image({**text, '.dynamic': (0x400700, struct.pack('<2I', 0x7000000a, 2))}, syms)).got == {}
    assert rebase.Elf(image({**text, '.dynamic': (0x400700, struct.pack('<2I', 0x70000013, 1))}, syms)).got == {0x405000: 'f', 0x405004: 'g'}
    assert rebase.Elf(image(text, syms, local=2, gotsym=2)).got == {0x405008: 'g'}


def test_elf_names_places(old):
    e = rebase.Elf(old.blob)
    assert e.where(old.s['alpha']) == 'alpha' and e.where(old.s['alpha'] + 8) == 'alpha+0x8'
    assert e.where(old.s['one']) == f'alpha+{old.sizes["alpha"]:#x}(past)'
    assert e.where(0x400000) == '0x400000'
    assert e.where(0x406010) == 'g_flag' and e.where(0x406013) == 'g_flag+0x3' and e.where(0x406014) == 'g_flag+0x4(past)'
    e.named([(old.s['one'], old.sizes['one'], 'one'), (0x404020, 4, 'counter')])
    assert e.syms['one'] == (old.s['one'], old.sizes['one'], 'FUNC') and e.syms['counter'] == (0x404020, 4, 'OBJECT')
    assert e.where(old.s['one'] + 4) == 'one+0x4' and e.where(0x404020) == 'counter'
    assert e.where(e.end - 4) == f'gamma+{old.sizes["gamma"] - 4:#x}'
    e.named([(e.end, 4, 'past')])
    assert e.syms['past'][2] == 'OBJECT' and old.elf.where(old.s['one'] + 4) != 'one+0x4'


def test_track_follows_addresses_and_calls(old, new):
    votes, calls, sites, callsites, changes = rebase.track(old.elf, old.elf, list(range(len(old.words))))
    s = old.s
    same = lambda x, n=1: collections.Counter({x: n})
    assert {x: c for x, c in votes.items()} == {
        s['one']: same(s['one'], 2), s['hello']: same(s['hello']), s['g_flag']: same(s['g_flag']), s['counter']: same(s['counter'], 2),
        s['world']: same(s['world']), s['state']: same(s['state']), s['table']: same(s['table'])}
    assert calls == {s['one']: same(s['one'], 3), s['printf']: same(s['printf']), s['two']: same(s['two'], 2), s['puts']: same(s['puts'])}
    assert sites[s['alpha'] + 12] == (s['one'], s['one']) and sites[s['one'] + 16] == (s['counter'], s['counter'])
    assert sites[s['two'] + 16] == (s['state'], s['state']) and len(sites) == 9
    assert callsites == {s['alpha'] + 52: (s['two'], s['two']), s['two'] + 20: (s['one'], s['one']),
                         s['beta'] + 12: (s['two'], s['two']), s['beta'] + 20: (s['puts'], s['puts'])}
    assert changes == []


def test_track_between_two_builds(old, new, mp):
    s, t = old.s, new.s
    assert mp.votes[s['hello']] == {t['hello']: 1} and mp.votes[s['counter']] == {t['counter']: 2}
    assert mp.votes[s['g_flag']] == {t['g_flag']: 1} and mp.votes[s['table']] == {t['table']: 1}
    assert mp.calls[s['two']] == {t['two']: 2} and mp.calls[s['printf']] == {t['printf']: 1}
    assert mp.callsites[s['beta'] + 12] == (s['two'], t['two'])
    assert mp.sites[s['one'] + 8] == (s['counter'], t['counter'])
    assert mp.changes == [] and mp.unexplained == set()


def test_track_notes_words_that_changed(old):
    def other(**changes):
        words = list(old.words)
        for at, word in changes.items():
            words[(old.s[at.rstrip('0123456789_')] - old.TEXT) // 4 + int(at.split('_')[1])] = word
        blob = bytearray(old.blob)
        struct.pack_into(f'<{len(words)}I', blob, old.elf.sec['.text'][1], *words)
        return rebase.Elf(bytes(blob))
    walk = lambda b: rebase.track(old.elf, b, list(range(len(old.words))))[4]
    one, two, gamma = old.s['one'], old.s['two'], old.s['gamma']
    assert walk(other(one_3=addiu(V1, V1, 2))) == [(one + 12, one + 12, addiu(V1, V1, 1), addiu(V1, V1, 2))]   # an immediate
    assert walk(other(gamma_3=0x00431023)) == [(gamma + 12, gamma + 12, 0x00431021, 0x00431023)]             # an R-type
    assert walk(other(gamma_1=ori(V0, 0, 0x4321))) == [(gamma + 4, gamma + 4, ori(V0, 0, 0x1234), ori(V0, 0, 0x4321))]
    assert walk(other(two_7=beq(V0, 0, 2))) == [(two + 28, two + 28, beq(V0, 0, 1), beq(V0, 0, 2))]           # a branch elsewhere
    assert walk(other(gamma_0=0x7c03e83b)) == [(gamma, gamma, addiu(SP, SP, -48), 0x7c03e83b)]
    # the slot of another symbol: g_flag's load now reads printf's
    slot = lambda name: 4 * old.slots.index(name) - 0x7ff0
    assert walk(other(alpha_11=lw(V0, slot('printf'), GP))) == [(old.s['alpha'] + 44, old.s['alpha'] + 44, lw(V0, slot('g_flag'), GP), lw(V0, slot('printf'), GP))]
    assert walk(other(alpha_6=lw(A0, slot('data'), GP))) == []   # a local slot is only an address
    # nor is a prologue's $gp, which is as far from the function as the GOT is
    here, there = (other(gamma_1=lui(GP, h), gamma_2=addiu(GP, GP, l)) for h, l in ((0x40, 0x4000), (0x41, 0x5000)))
    assert rebase.track(here, there, list(range(len(old.words))))[4] == []


def test_track_forgets_registers(old):
    """What a register held does not outlive a call, a write, a gap in the alignment or the function."""
    big = Toy(tail=100)
    n = len(big.words)

    def votes(words, m=None, starts=()):
        blob = bytearray(big.blob)
        padded = words + [NOP] * (n - len(words))
        struct.pack_into(f'<{n}I', blob, big.elf.sec['.text'][1], *padded)
        e = rebase.Elf(bytes(blob))
        e.syms = {f'f{k}': (old.TEXT + 4 * i, 4, 'FUNC') for k, i in enumerate(starts)}
        v, calls, _, _, _ = rebase.track(e, e, m or list(range(n)))
        return sorted(v), sorted(calls)
    high, low = lui(V0, 0x40), addiu(A0, V0, 0x4000)
    assert votes([high, low]) == ([0x404000], [])
    assert votes([high, NOP, NOP, low]) == ([0x404000], [])
    assert votes([high, jal(0x401000), NOP, low]) == ([], [0x401000])               # after a call's delay slot
    assert votes([high, jal(0x401000), low]) == ([0x404000], [0x401000])            # in it
    assert votes([high, JALR_T9, NOP, low]) == ([], [])
    assert votes([high, JALR_T9, low]) == ([0x404000], [])
    assert votes([high, bal(4), NOP, low]) == ([], [0x401018])
    assert votes([lui(16, 0x40), jal(0x401000), NOP, addiu(A0, 16, 0x4000)]) == ([0x404000], [0x401000])  # $s0 survives
    assert votes([high, 0x00001021, low]) == ([], [])                               # move v0, zero
    assert votes([high, addiu(V0, 0, 5), low]) == ([], [])
    assert votes([high, lw(V0, 4, SP), low]) == ([], [])
    assert votes([high, ori(V0, 0, 5), low]) == ([], [])
    assert votes([high, 0x28430005, low]) == ([0x404000], [])                       # slti reads v0
    assert votes([high, 0x28620005, low]) == ([], [])                               # slti into v0
    assert votes([high, 0x70431002, low]) == ([], [])                               # mul v0
    assert votes([high, 0x7c021420, low]) == ([], [])                               # seb v0
    assert votes([high, 0x00430018, low]) == ([0x404000], [])                       # mult writes no register
    assert votes([high, 0x0043001b, low]) == ([0x404000], [])                       # divu
    assert votes([high, addiu(SP, SP, -32), low]) == ([], [])                       # a prologue
    assert votes([high, addiu(SP, SP, 32), low]) == ([0x404000], [])                # an epilogue
    assert votes([high, NOP, low], starts=[2]) == ([], [])                          # a symbol's start
    assert votes([high, NOP, low], starts=[0]) == ([0x404000], [])
    assert votes([high, low, low], m=[0, *range(2, n), -1]) == ([], [])             # not the next word
    assert votes([high, NOP, low], m=[0, -1, *range(2, n)]) == ([], [])             # a word with no counterpart between
    assert votes([high, addiu(V0, V0, 0x4000), lw(V1, 8, V0)]) == ([0x404000, 0x404008], [])   # a full address goes on
    assert votes([high, lw(V0, 0x4000, V0), lw(V1, 8, V0)]) == ([0x404000], [])                # a load ends it
    assert votes([high, ori(V0, V0, 0x8000)]) == ([0x408000], [])                   # ori's half is unsigned
    assert votes([high, addiu(V0, V0, 0x8000)]) == ([0x3f8000], [])
    assert votes([high, 0x00400008]) == ([], [])                                    # jr on a bare high half: no call
    assert votes([high, addiu(V0, V0, 0x4000), 0x00400008]) == ([0x404000], [0x404000])
    assert votes([lw(T9, -0x7ff0 + 12, GP), beq(0, 0, 100)]) == ([], [0x401000 + 4 * 102])    # b with $t9 loaded: a tail call
    assert votes([beq(0, 0, 100)]) == ([], [])
    assert votes([lw(T9, -0x7ff0 + 12, GP), beq(0, 0, 64)]) == ([], [])             # a short b is a branch
    assert votes([lw(T9, 0x7000, GP), JALR_T9]) == ([], [])                         # a slot outside the file
    # a PIC prologue sets $gp to $t9 plus how far the GOT is: that is no address, nor is what $gp is added to
    setup = [lui(GP, 0x40), addiu(GP, GP, 0x4000), 0x0399e021]
    assert votes(setup) == ([], []) and votes(setup + [addiu(A0, GP, 0x4010)]) == ([], [])
    assert votes(setup + [lw(T9, -0x7ff0 + 8, GP), addiu(T9, T9, 0x40), JALR_T9]) == ([0x401040], [0x401040])


WIDE = Toy(tail=100)
N, T = len(WIDE.words), Toy.TEXT


def walked(words, other=None, m=None):
    """track over the toy's .text holding words, against other in the second build's."""
    def elf(ws):
        blob = bytearray(WIDE.blob)
        struct.pack_into(f'<{N}I', blob, WIDE.elf.sec['.text'][1], *ws, *[NOP] * (N - len(ws)))
        e = rebase.Elf(bytes(blob))
        e.syms = {}
        return e
    return rebase.track(elf(words), elf(words if other is None else other), m or list(range(N)))


def test_track_finds_each_kind_of_call():
    text = lw(T9, -0x7ff0 + 8, GP)
    _, calls, _, callsites, _ = walked([0x08000000 | (T + 0x40) >> 2, NOP, 0x04130004, NOP, beq(0, 0x11, 4)])   # j, bgezall; no bal
    assert calls == {T + 0x40: {T + 0x40: 1}, T + 28: {T + 28: 1}} and callsites == {T: (T + 0x40, T + 0x40), T + 8: (T + 28, T + 28)}
    assert walked([NOP, NOP, bal(-3)])[1] == {T: {T: 1}}                                    # to the first word
    assert walked([NOP] * (N - 2) + [bal(1), NOP])[1] == {}                                 # past the last
    _, calls, _, callsites, _ = walked([NOP] * 69 + [text, beq(0, 0, -71)])                 # a PIC tail call to the first word
    assert calls == {T: {T: 1}} and callsites == {T + 280: (T, T)}
    assert walked([NOP] * (N - 71) + [text, beq(0, 0, 69)])[1] == {}                        # past the last
    assert walked([text, beq(0, 0, 65)])[1] == {T + 4 * 67: {T + 4 * 67: 1}}                # the shortest that is one
    _, calls, _, callsites, _ = walked([text, beq(0, 0, 100), NOP, text, beq(0, 0, 97)])
    assert calls == {T + 408: {T + 408: 2}} and callsites == {T + 4: (T + 408, T + 408), T + 16: (T + 408, T + 408)}
    assert walked([lui(V0, 0x40), ori(V0, V0, 0x8000)])[0] == {0x408000: {0x408000: 1}}


@pytest.mark.parametrize('op, rt', [(1, 0), (1, 1), (4, V1), (5, V1), (6, 0), (7, 0), (20, V1), (21, V1), (22, 0), (23, 0)])
def test_track_takes_a_branch_that_moved_with_its_target_for_no_change(op, rt):
    branch = lambda words: op << 26 | V0 << 21 | rt << 16 | words & 0xffff
    more = [0, 1, 2, *range(4, N), -1]                                                      # a word more before the target
    assert walked([branch(2)], [branch(3)], more)[4] == []
    assert walked([branch(2)], [branch(4)], more)[4] == [(T, T, branch(2), branch(4))]
    assert walked([NOP, NOP, branch(-3)], [NOP, NOP, NOP, branch(-4)], [0, *range(2, N), -1])[4] == []   # to the first word
    last = [NOP] * (N - 1)
    assert walked(last + [branch(0)], last + [branch(1)])[4] == [(T + 4 * N - 4, T + 4 * N - 4, branch(0), branch(1))]   # past the last
    assert walked([branch(2)], m=[0, 1, 2, -1, *range(4, N)])[4] == []                      # the same word is no change


def test_track_notes_a_changed_word_of_any_other_kind():
    assert walked([0x28430005], [0x28430006])[4] == [(T, T, 0x28430005, 0x28430006)]        # slti


def test_track_forgets_each_register_written():
    seen = lambda words: (sorted(walked(words)[0]), sorted(walked(words)[1]))
    high, low = lui(V0, 0x40), addiu(A0, V0, 0x4000)
    assert seen([high, 0x30440001]) == ([], [])                                     # andi reads no address
    assert seen([high, 0xfc440010]) == ([0x400010], [])                             # the last opcode that may
    assert seen([high, 0x80440010]) == ([0x400010], [])                             # lb, the first load
    assert seen([high, 0x80420000, low]) == ([0x400000], [])                        # lb into v0
    assert seen([high, 0x98420000, low]) == ([0x400000], [])                        # lwr, the last load, into v0
    assert seen([lui(V1, 0x40), high, 0x9c430000, addiu(A0, V1, 0x4000)]) == ([0x400000, 0x404000], [])   # opcode 0x27 loads nothing
    assert seen([lw(T9, -0x7ff0 + 8, GP), lw(T9, 0x7000, GP), addiu(T9, T9, 0x40), JALR_T9]) == ([], [])  # a slot outside the file
    assert seen([high, ori(V0, V0, 0x4000), lw(V1, 8, V0)]) == ([0x404000, 0x404008], [])                 # ori makes a full address
    assert seen([high, 0x20020005, low]) == ([], [])                                # addi into v0
    assert seen([high, 0x38020005, low]) == ([], [])                                # xori into v0
    assert seen([0x70431002]) == ([], [])                                           # mul into a register that held nothing
    assert seen([high, 0x7c620004, low]) == ([], [])                                # ins into v0


def test_view(old):
    v = rebase.view(old.elf)
    s = old.s
    assert v.starts == {s['alpha'], s['one'], s['two'], s['beta'], s['gamma']}
    assert v.refs == {s['hello'], s['g_flag'], s['counter'], s['world'], s['state'], s['table']}
    assert [v.function(a) for a in (s['alpha'], s['one'] - 4, s['one'], s['gamma'] + 20, old.elf.end, s['alpha'] - 4)] == \
        [s['alpha'], s['alpha'], s['one'], s['gamma'], None, None]
    assert v.data(s['hello']) and v.data(old.gp) and v.data(0x402000) and v.data(0x402120)
    assert v.data(0x406010) and v.data(0x406013) and not v.data(0x406014) and not v.data(0x40600f)
    assert not v.data(s['hello'] + 4) and not v.data(0x403040)
    assert rebase.View(old.elf, [], []).function(old.TEXT + 8) == old.TEXT
    blob = bytearray(old.blob)
    struct.pack_into('<I', blob, old.elf.sec['.data'][1] + 12, s['alpha'] + 8)   # a pointer into a function: a start too
    struct.pack_into('<I', blob, old.elf.sec['.data'][1] + 16, s['alpha'] + 6)   # unaligned: not code
    assert rebase.view(rebase.Elf(bytes(blob))).starts == v.starts | {s['alpha'] + 8}


def test_view_reads_every_pointer_table():
    plain = Toy(tail=8, slot=1)
    end = plain.elf.end
    tables = ('.data.rel.ro', '.init_array', '.ctors', '.dtors')
    toy = Toy(tail=8, slot=1, extra={name: (0x407000 + 0x100 * k, struct.pack('<I', end - 4 * (k + 1))) for k, name in enumerate(tables)})
    blob = bytearray(toy.blob)
    struct.pack_into('<I', blob, toy.elf.sec['.got'][1] + 8, end - 20)                 # the spare slot
    struct.pack_into('<2I', blob, toy.elf.sec['.data'][1] + 32, end, end - 24)        # the table's last words: the end is no code
    assert rebase.view(rebase.Elf(bytes(blob))).starts == rebase.view(plain.elf).starts | {end - 4 * k for k in range(1, 7)}


class Far(Toy):
    TEXT = 0x40fff0  # alpha lies across a 64 KB boundary


def test_view_takes_no_got_page_for_a_start():
    far = Far()
    blob = bytearray(far.blob)
    struct.pack_into('<2I', blob, far.elf.sec['.data'][1] + 12, 0x410000, 0x410004)   # a page as the GOT holds it, and a pointer
    assert rebase.view(rebase.Elf(bytes(blob))).starts == rebase.view(far.elf).starts | {0x410004}


def test_map_of_a_build_onto_itself(old):
    same = rebase.Map(old.elf, rebase.Elf(old.blob))
    assert same.m == list(range(len(old.words))) and same.dropped == 0 and same.moved == 0 and same.unexplained == set()
    for x in (old.s['alpha'], old.s['one'] + 8, old.s['hello'], old.s['counter'], old.gp, old.s['state'], 0x402000):
        assert same.lookup(x)[0] == x and same.old(x) in (x, None)
    assert same.word(old.s['one'] + 4) == (old.words[(old.s['one'] + 4 - old.TEXT) // 4], 'same')


def test_map_carries_code(old, new, mp):
    s, t = old.s, new.s
    assert mp.dropped == 0 and mp.moved == 1          # one of the two statics that changed places is aligned from its callers
    for name in ('alpha', 'one', 'two', 'beta', 'gamma'):
        for off in range(0, old.sizes[name], 4):
            pad = 12 if name == 'alpha' and off >= 8 else 0
            assert mp.code(s[name] + off) == t[name] + pad + off, (name, off)
            assert mp.old(t[name] + pad + off) == s[name] + off
    assert mp.code(s['alpha'] + 9) == t['alpha'] + 12 + 9
    assert mp.code(old.elf.end) is None and mp.code(old.TEXT - 4) is None
    assert mp.old(t['alpha'] + 8) is None and mp.old(t['alpha'] + 16) is None    # the words added
    assert mp.lookup(s['two'] + 8) == (t['two'] + 8, 'code')
    assert mp.bounds() == (sorted(s[n] for n in old.sizes), sorted([*(t[n] for n in old.sizes)]))


def test_map_anchors():
    old = Toy()
    swapped = rebase.Map(old.elf, Toy(order=('beta', 'one', 'two', 'alpha', 'gamma')).elf)
    assert swapped.dropped == 1                                         # alpha and beta changed places: one is no anchor
    assert swapped.lookup(old.s['gamma'] + 8) == (old.s['gamma'] + 8, 'code')
    a, b = Toy(), Toy(order=('alpha', 'two', 'one', 'beta', 'gamma'), pad=3, shift=0x100, slot=1)
    plain = rebase.Map(a.elf, b.elf)
    for e in (a.elf, b.elf): e.syms['_init'] = (0x400f00, 8, 'FUNC')    # a function before .text is no anchor
    assert rebase.Map(a.elf, b.elf).m == plain.m and plain.moved == 1


def test_reanchor_places_functions_that_moved():
    first = Toy(order=('alpha', 'beta', 'gamma', 'two', 'one'))
    last = Toy(order=('one', 'alpha', 'two', 'beta', 'gamma'))
    mid = Toy()
    for a, b, moved in ((first, mid, 2), (mid, first, 2), (mid, last, 1), (last, mid, 1), (first, last, 2), (last, first, 2)):
        mp = rebase.Map(a.elf, b.elf)
        assert mp.moved == moved and mp.dropped == 0
        for name in a.sizes:
            assert [mp.code(a.s[name] + o) for o in range(0, a.sizes[name], 4)] == [b.s[name] + o for o in range(0, a.sizes[name], 4)], name
        assert sorted(mp.back) == list(range(len(a.words))) and mp.unexplained == set()


def test_map_carries_data(old, new, mp):
    s, t = old.s, new.s
    assert mp.data(old.gp) == (new.gp, 'gp') and mp.lookup(old.gp) == (new.gp, 'gp')
    assert mp.data(s['printf']) == (t['printf'], 'import') and mp.data(s['puts']) == (t['puts'], 'import')
    assert mp.data(s['hello']) == (t['hello'], 'ref') and mp.data(s['state']) == (t['state'], 'ref')
    assert mp.data(s['g_flag'] + 2) == (t['g_flag'] + 2, 'sym')
    assert mp.data(s['hello'] + 16) == (t['hello'] + 16, 'ref')
    assert mp.data(0x403014) == (0x403114, 'string')                     # the tail of a string no code names
    assert mp.data(0x405008) == (None, 'unreferenced')                   # a GOT slot: nothing names it, and its neighbours changed
    assert mp.data(0x403044) == (0x403144, 'content')                    # a word of a table, its pointer carried
    assert mp.data(0x404008) == (0x404108, 'content') and mp.data(0x404000 + 0x24) == (0x404124, 'near')
    assert mp.data(0x404004) == (0x404104, 'near')                       # too near its section's start for the words around it
    assert mp.data(0x406030) == (0x406130, 'near') and mp.data(0x406000 + 0x18) == (0x406118, 'near')
    assert mp.data(0x500000) == (None, 'not an address')
    assert mp.old(new.gp) == old.gp and mp.old(t['hello']) == s['hello'] and mp.old(t['hello'] + 4) is None


def test_map_data_edge_cases(old):
    """A string found once, twice, or changed; an import gone; an address two words form differently."""
    moved = rebase.Map(old.elf, Toy(shift=0x100).elf)
    assert moved.data(0x403014) == (0x403114, 'string') and moved.data(0x403018) == (0x403118, 'content')   # under six characters
    assert moved.data(0x403017) == (0x403117, 'string')                                                   # six
    a, b = Toy(), Toy(shift=0x100)
    a.elf.named([(0x406040, 4, 'obj'), (0x406060, 0, 'mark')])
    b.elf.named([(0x406140, 4, 'obj'), (0x406160, 0, 'mark')])
    named = rebase.Map(a.elf, b.elf)                                     # an object nothing names goes with its symbol, to its last byte
    assert [named.data(x) for x in (0x406040, 0x406043, 0x406060)] == [(0x406140, 'sym'), (0x406143, 'sym'), (0x406160, 'sym')]
    assert all(named.data(x)[1] != 'sym' for x in (0x40603f, 0x406044, 0x406061))
    assert rebase.Map(old.elf, Toy(shift=0x100, hello=b'second string').elf).data(0x403014) == (0x403114, 'near')   # there twice
    blob = Toy(shift=0x100).blob.replace(b'second string', b'second strinG')
    assert rebase.Map(old.elf, rebase.Elf(blob)).data(0x403014) == (0x403114, 'near')                     # there no more
    gone = rebase.Map(old.elf, rebase.Elf(Toy().blob.replace(b'printf\0', b'prinTf\0')))
    assert gone.data(0x402000) == (None, 'import gone')
    blob = bytearray(old.blob)
    struct.pack_into('<I', blob, old.elf.sec['.text'][1] + old.s['one'] + 8 - old.TEXT, lw(V1, lo(old.s['counter']) + 4, V0))
    torn = rebase.Map(old.elf, rebase.Elf(bytes(blob)))
    assert torn.data(0x404020) == (None, 'conflict 0x404024x1 0x404020x1') and torn.lookup(0x404020)[0] is None
    same = rebase.Map(old.elf, old.elf)
    assert same.content(0x404026, '.data') is None and same.content(0x406010, '.bss') is None and same.content(0x403044, '.nope') is None
    assert same.content(0x404018, '.data') is None                       # two words around it say something: too few
    assert same.content(0x404004, '.data') is None                       # no section before it to match
    assert same.content(0x404008, '.data') == 0x404008 and same.content(0x40400c, '.data') == 0x40400c


def test_map_words(old, new, mp):
    s, t = old.s, new.s
    word = lambda e, a: e.elf.u32(a)
    assert mp.word(s['gamma'] + 4) == (word(new, t['gamma'] + 4), 'same')
    assert mp.word(s['alpha'] + 8) == (word(new, t['alpha'] + 20), 'moved') and word(old, s['alpha'] + 8) != word(new, t['alpha'] + 20)
    assert mp.word(s['one'] + 4) == (lui(V0, hi(t['counter'])), 'same') and mp.word(s['one'] + 8) == (lw(V1, lo(t['counter']), V0), 'moved')
    assert mp.word(0x404004) == (t['one'], 'moved') and mp.word(0x40400c) == (0xdeadbeef, 'same')
    assert mp.word(0x406010) == (None, 'gone') and mp.word(0x500000) == (None, 'gone') and mp.word(0x405008) == (None, 'gone')
    edited = rebase.Map(old.elf, Toy(gamma=2).elf)
    assert edited.word(s['gamma'] + 4) == (None, 'gone') and edited.lookup(s['gamma'] + 4) == (None, 'code changed')
    blob = bytearray(old.blob)
    struct.pack_into('<I', blob, old.elf.sec['.text'][1] + s['one'] + 12 - old.TEXT, addiu(V1, V1, 2))
    edited = rebase.Map(old.elf, rebase.Elf(bytes(blob)))
    assert edited.word(s['one'] + 12) == (addiu(V1, V1, 2), 'changed') and edited.unexplained == {s['one'] + 12}
    blob = bytearray(Toy(shift=0x100).blob)
    struct.pack_into('<I', blob, rebase.Elf(bytes(blob)).sec['.data'][1] + 4, 0x401004)
    assert rebase.Map(old.elf, rebase.Elf(bytes(blob))).word(0x404004) == (0x401004, 'changed')   # a pointer to somewhere else


def test_map_function_whose_first_word_changed(old):
    """A start the alignment lost goes where its symbol, or its callers, went."""
    def build(**words):
        toy = Toy()
        blob = bytearray(toy.blob)
        for at, word in words.items():
            struct.pack_into('<I', blob, toy.elf.sec['.text'][1] + toy.s[at] - toy.TEXT, word)
        return rebase.Map(old.elf, rebase.Elf(bytes(blob)))
    mp = build(gamma=addiu(SP, SP, -64))
    assert mp.code(old.s['gamma']) is None and mp.lookup(old.s['gamma']) == (old.s['gamma'], 'code')
    assert mp.old(old.s['gamma']) == old.s['gamma'] and mp.lookup(old.s['gamma'] + 4) == (old.s['gamma'] + 4, 'code')
    mp = build(one=addiu(SP, SP, -64))
    assert mp.code(old.s['one']) is None and mp.lookup(old.s['one']) == (old.s['one'], 'code')    # by its three callers
    assert mp.moved == 0                                                                          # and is not aligned again
    mp = build(two=0x7c03e83b, **{'one': 0x7c03e83b})
    assert mp.m[(old.s['one'] - old.TEXT) // 4] == -1 and mp.lookup(old.s['one'] + 4)[0] == old.s['one'] + 4
    # a handler nothing calls goes where the code that forms its address says
    def handler(first):
        toy = Toy()
        blob = bytearray(toy.blob)
        text = toy.elf.sec['.text'][1] - toy.TEXT
        struct.pack_into('<2I', blob, text + toy.s['alpha'] + 12, addiu(T9, T9, toy.s['gamma'] - toy.TEXT), NOP)   # formed, not called
        struct.pack_into('<I', blob, text + toy.s['gamma'], first)
        e = rebase.Elf(bytes(blob))
        del e.syms['gamma']
        return e
    mp = rebase.Map(handler(addiu(SP, SP, -48)), handler(addiu(SP, SP, -64)))
    assert mp.code(old.s['gamma']) is None and mp.lookup(old.s['gamma']) == (old.s['gamma'], 'code')
    assert mp.old(old.s['gamma']) == old.s['gamma']
    gone = rebase.Map(old.elf, Toy(gamma=2, order=('alpha', 'one', 'two', 'beta', 'gamma')).elf)
    assert gone.lookup(old.s['gamma'] + 4) == (None, 'code changed')


def test_unexplained_changes(old):
    """A word that changed for a reason the walk can name is no change of Shanling's."""
    mp = rebase.Map(old.elf, Toy(shift=0x100).elf)
    assert mp.unexplained == set() and mp.moved == 0
    blob = bytearray(Toy().blob)
    text = old.elf.sec['.text'][1]
    struct.pack_into('<I', blob, text + old.s['beta'] + 12 - old.TEXT, jal(old.s['one']))     # beta calls one, not two
    struct.pack_into('<I', blob, text + old.s['one'] + 8 - old.TEXT, lw(V1, lo(old.s['counter']) + 4, V0))
    mp = rebase.Map(old.elf, rebase.Elf(bytes(blob)))
    assert mp.unexplained == {old.s['beta'] + 12, old.s['one'] + 8, old.s['one'] + 16}   # both uses of an address two ways


def test_unexplained_low_halves():
    """A low half that moved as the data did is an address the walk lost the high half of, not a change."""
    old, new = Toy(), Toy(shift=0x100, pad=3)

    def changed(a_word, b_word):
        blobs = []
        for toy, word in ((old, a_word), (new, b_word)):
            blob = bytearray(toy.blob)
            struct.pack_into('<I', blob, toy.elf.sec['.text'][1] + toy.s['gamma'] + 4 - toy.TEXT, word)
            blobs.append(rebase.Elf(bytes(blob)))
        return rebase.Map(*blobs).unexplained
    here = {old.s['gamma'] + 4}
    assert changed(lw(V1, 0x4020, V0), lw(V1, 0x4120, V0)) == set()
    assert changed(lw(V1, 0x4020, V0), lw(V1, 0x4124, V0)) == here
    assert changed(lw(V1, 0x4020, V0), lw(V1, 0x3f20, V0)) == here     # the other way
    assert changed(lw(V1, 0xff80, V0), lw(V1, 0x0080, V0)) == set()    # across the half's end
    assert changed(ori(V1, V0, 0x4020), ori(V1, V0, 0x4120)) == set() and changed(addiu(V1, V0, 0x4020), addiu(V1, V0, 0x4120)) == set()
    assert changed(0x80430020, 0x80430120) == set() and changed(0xfc430020, 0xfc430120) == set()   # lb, and the last opcode
    assert changed(beq(V0, V1, 0x10), beq(V0, V1, 0x110)) == here      # a branch is no low half


def test_literals():
    text = 'a = 0x4a1234, 0X0040abcd\nb = "0400828c" + \'24020001afbf001c\' 0x1234 0x123456789 x0x12345 0x12345g "0400828"\n0xABCDE'
    lits = rebase.literals(text)
    assert [(l.value, l.digits, l.line, l.kind) for l in lits] == [
        (0x4a1234, 6, 0, 'hex'), (0x40abcd, 8, 0, 'hex'), ((0x8c820004,), 8, 1, 'le'), ((0x01000224, 0x1c00bfaf), 16, 1, 'le'), (0xabcde, 5, 2, 'hex')]
    assert [text[l.start:l.end] for l in lits] == ['0x4a1234', '0X0040abcd', '0400828c', '24020001afbf001c', '0xABCDE']
    assert rebase.literals('"00112233445566778899aabb" "0011223344556677889900112233445566"')[0].value == (0x33221100, 0x77665544, 0xbbaa9988)
    assert len(rebase.literals('"0011223344556677889900112233445566"')) == 0
    A = rebase.address
    L = lambda value, digits, kind='hex': rebase.Lit(0, 0, value, digits, 0, kind)
    assert A(L(0x400000, 6)) and A(L(0xffffff, 6)) and A(L(0x4a1234, 7))
    assert not A(L(0x3fffff, 6)) and not A(L(0x1000000, 7)) and not A(L(0x4a1234, 8)) and not A(L((0x4a1234,), 6, 'le'))


# judge: demo is the old toy and hciplayer another build of it, its code over the same addresses as stock's is.
# Both end in 64 words nothing names, 0x4010d4 to 0x4011d4.
@pytest.fixture
def views():
    demo, hci = Toy(tail=64), Toy(order=('gamma', 'beta', 'alpha', 'two', 'one'), shift=0x200, tail=64)
    assert [demo.s[n] for n in ('alpha', 'one', 'two', 'beta', 'gamma')] == [0x401000, 0x401048, 0x401064, 0x401090, 0x4010bc]
    assert [hci.s[n] for n in ('gamma', 'beta', 'alpha', 'two', 'one')] == [0x401000, 0x401018, 0x401044, 0x40108c, 0x4010b8]
    return {'demo': rebase.view(demo.elf), 'hciplayer': rebase.view(hci.elf)}


def verdicts(text, views, **kw):
    """judge's verdicts, in the text's order: [(literal as written, binary, how)]."""
    lits = rebase.literals(text)
    return [(text[lits[n].start:lits[n].end], *v) for n, v in sorted(rebase.judge(text, lits, views, **kw).items())]


def test_judge_solid_evidence(views):
    V = lambda text, **kw: verdicts(text, views, **kw)
    # word: the stock word is on the line, the next or the one before; never 0, all ones or the address itself
    assert V('PATCH(0x401048, 0x27bdffe8)') == [('0x401048', 'demo', 'word')]
    assert V('0x27bdffe8,\n0x401050') == [] and V('0x401004,\n0xafbf001c') == [('0x401004', 'demo', 'word')]
    assert V('0xafbf001c,\n0x401004') == [('0x401004', 'demo', 'word')]
    assert V('0xafbf001c,\n\n0x401004') == [] and V('0x401004\n\n0xafbf001c') == []
    assert V('"1c00bfaf" 0x401004') == [('0x401004', 'demo', 'word')] and V('"000000001c00bfaf" 0x401004') == []   # a string's first word
    assert V('0x401014 0x00000000') == [] and V('0x401010 0x401010') == []
    assert V('0x4011d0 0x00000000') == [] and V('0x401190 0xffffffff') == []
    assert V('0x40108c 0x27bdfff0') == [('0x40108c', 'hciplayer', 'word')]
    # name: a symbol of that address, or the function holding it; an object by its start alone
    assert V('alpha at 0x401000') == [('0x401000', 'demo', 'name')] and V('beta+0x10: 0x4010a0') == [('0x4010a0', 'demo', 'name')]
    assert V('beta 0x4010bc') == [('0x4010bc', 'demo', 'start')] and V('beta 0x401028') == [('0x401028', 'hciplayer', 'name')]
    assert V('beta 0x4010b8') == [('0x4010b8', 'demo', 'name')]                # demo's beta holds it; hciplayer only starts a function there
    assert V('g_flag 0x406010') == [('0x406010', 'demo', 'name')] and V('g_flag 0x406012') == [('0x406012', 'demo', 'data')]
    assert V('al 0x401010') == [] and V('alphabet 0x401010') == []
    # start and data
    assert V('0x401048') == [('0x401048', 'demo', 'start')] and V('0x401018') == [('0x401018', 'hciplayer', 'start')]
    assert V('0x403010') == [('0x403010', 'demo', 'data')] and V('0x403210') == [('0x403210', 'hciplayer', 'data')]
    assert V('0x403014') == [] and V('0x401049') == [] and V('0x500000') == [] and V('0x00401048') == [] and V('"48104000"') == []
    # table: only the file's binary, aligned in its code or anywhere in its data
    T = lambda text: V(text, home='demo', table=True)
    assert T('0x401010') == [('0x401010', 'demo', 'table')] and T('0x401012') == [] and T('0x404025') == [('0x404025', 'demo', 'table')]
    assert T('0x403206') == [] and T('0x400104') == [] and V('0x401010', home='demo') == [] and V('0x401010', table=True) == []
    assert V('0x401010', home='hciplayer', table=True) == [('0x401010', 'hciplayer', 'table')]
    assert T('0x401048') == [('0x401048', 'demo', 'start')]                  # the stronger evidence is the one named


def test_judge_between_binaries(views):
    V = lambda text, **kw: verdicts(text, views, **kw)
    # both builds start a function at 0x401000 and have a stub at 0x402000
    assert V('0x401000') == [('0x401000', '?', 'start')] and V('0x402000') == [('0x402000', '?', 'data')]
    assert V('0x401000', home='demo') == [('0x401000', 'demo', 'start')] and V('0x401000', home='hciplayer') == [('0x401000', 'hciplayer', 'start')]
    assert V('0x401000', home='bluealsa') == [('0x401000', '?', 'start')]
    assert V('gamma 0x401000') == [('0x401000', 'hciplayer', 'name')]            # the stronger evidence wins outright
    # the nearest literal with solid evidence, within 15 lines
    far = '\n' * 16
    assert V('0x401048\n0x401000') == [('0x401048', 'demo', 'start'), ('0x401000', 'demo', 'start')]
    assert V('0x401000\n0x401018') == [('0x401000', 'hciplayer', 'start'), ('0x401018', 'hciplayer', 'start')]
    assert V('0x401048' + '\n' * 15 + '0x401000' + far + '0x401018')[1] == ('0x401000', 'demo', 'start')
    assert V('0x401048' + far + '0x401000' + '\n' * 15 + '0x401018')[1] == ('0x401000', 'hciplayer', 'start')
    assert V('0x401048' + far + '0x401000' + far + '0x401018')[1] == ('0x401000', '?', 'start')
    assert V('0x401048\n0x401000\n\n0x401018')[1] == ('0x401000', 'demo', 'start')
    assert V('0x401048\n\n0x401000\n0x401018')[1] == ('0x401000', 'hciplayer', 'start')
    assert V('0x401048   0x401000 0x401018')[1] == ('0x401000', 'hciplayer', 'start')     # on one line: the nearer in characters
    assert V('0x401018   0x401000 0x401048')[1] == ('0x401000', 'demo', 'start')
    assert V('0x401048 0x401000   0x401018')[1] == ('0x401000', 'demo', 'start')
    assert V('0x401048\n   0x401018\n0x401000')[2] == ('0x401000', 'hciplayer', 'start')    # a line nearer counts before characters
    # none near: the binary with more of the file's solid evidence
    assert V('0x401000' + far + '0x401048' + far + '0x401064')[0] == ('0x401000', 'demo', 'start')
    assert V('0x401000' + far + '0x40108c' + far + '0x401018')[0] == ('0x401000', 'hciplayer', 'start')
    assert V('0x401000' + far + '0x401048' + far + '0x401064' + far + '0x401018' + far + '0x40108c')[0] == ('0x401000', '?', 'start')


def test_judge_weak_evidence(views):
    V = lambda text, **kw: verdicts(text, views, **kw)
    D = lambda text, **kw: V(text, home='demo', **kw)
    # same: the value has solid evidence, here or in another file
    assert V('0x401010', anchors=[('demo', 0x401010, 'table')]) == [('0x401010', 'demo', 'same')]
    assert V('0x401010', anchors=[('demo', 0x401010, 'note')]) == [('0x401010', 'demo', 'told')]
    assert V('0x401190', anchors=[('demo', 0x401194, 'note')]) == []
    assert V('0x401012', anchors=[('demo', 0x401012, 'table')]) == [('0x401012', 'demo', 'same')]
    assert V('0x401012', anchors=[('demo', 0x401012, 'line')]) == []                       # weak, and not aligned
    assert V('0x403014', anchors=[('demo', 0x403014, 'note')]) == []                       # weak, and not code
    # near: within 0x40 bytes of solid evidence, whoever's file it is
    assert V('0x401090 0x4010d0') == [('0x401090', 'demo', 'start'), ('0x4010d0', 'demo', 'near')]
    assert V('0x401090 0x4010d4') == [('0x401090', 'demo', 'start')]
    assert V('0x401090 0x401050') == [('0x401090', 'demo', 'start'), ('0x401050', 'demo', 'near')]
    assert V('0x401090 0x40104c') == [('0x401090', 'demo', 'start')]
    assert V('0x401150', anchors=[('demo', 0x401110, 'table')]) == [('0x401150', 'demo', 'near')]
    assert V('0x401150', anchors=[('demo', 0x401110, 'table'), ('demo', 0x401190, 'start')]) == [('0x401150', 'demo', 'near')]
    assert V('0x401150', anchors=[('demo', 0x401010, 'name'), ('demo', 0x401190, 'start')]) == [('0x401150', 'demo', 'near')]
    assert V('0x401150', anchors=[('demo', 0x401110, 'table'), ('demo', 0x4011d0, 'start')]) == [('0x401150', 'demo', 'near')]
    assert V('0x401150', anchors=[('demo', 0x40110c, 'table'), ('demo', 0x401194, 'start')]) == []
    assert V('0x401152', anchors=[('demo', 0x401110, 'table')]) == []
    # near: inside a function that holds solid evidence, for the file's binaries only
    assert D('0x4010bc, 0x401190') == [('0x4010bc', 'demo', 'start'), ('0x401190', 'demo', 'near')]
    assert V('0x4010bc, 0x401190') == [('0x4010bc', 'demo', 'start')]
    assert D('0x401190') == [] and D('0x401190', anchors=[('demo', 0x4010c0, 'name')]) == [('0x401190', 'demo', 'near')]
    assert D('0x401190', anchors=[('demo', 0x4010b8, 'name')]) == []
    # line: two or more different literals, all in the binary's code
    assert D('0x401110, 0x401190') == [('0x401110', 'demo', 'line'), ('0x401190', 'demo', 'line')]
    assert D('0x401110, 0x401110') == [] and D('0x401110, 0x401192') == [] and D('0x401110, 0x403014') == [] and V('0x401110, 0x401190') == []
    # note: in a comment; in prose every line is one
    for text in ('# 0x401190', 'x = 1  # see 0x401190', '// 0x401190', '/* 0x401190 */', '   * 0x401190', '* 0x401190'):
        assert D(text) == [('0x401190', 'demo', 'note')], text
    for text in ('0x401190', '#define SITE 0x401190', '#include 0x401190', '#if 0x401190', '#elif 0x401190', '#endif 0x401190', 'a * 0x401190',
                 '0x401190  # after it'):
        assert D(text) == [], text
    assert D('at 0x401190', prose=True) == [('0x401190', 'demo', 'note')] and V('# 0x401190') == [] and V('at 0x401190', prose=True) == []
    # a file with three solid literals of a binary is that binary's too
    three = '0x401048 0x401064 0x401090\n'
    assert V(three + '# 0x401190')[3:] == [('0x401190', 'demo', 'note')] and V('0x401048 0x401064\n# 0x401190')[2:] == []
    assert V(three + '0x401110, 0x401190')[3:] == [('0x401110', 'demo', 'line'), ('0x401190', 'demo', 'line')]


def test_judge_weak_evidence_between_binaries(views):
    V = lambda text, **kw: verdicts(text, views, **kw)
    both = '0x401048 0x401064 0x401090\n0x401018 0x401044 0x40108c\n' + '\n' * 20     # three solid literals each
    T = lambda text, **kw: V(both + text, **kw)[6:]
    assert T('# 0x401190') == [('0x401190', '?', 'told')]
    assert T('# 0x401190', home='demo') == [('0x401190', 'demo', 'note')]
    assert T('# 0x401190', home='hciplayer') == [('0x401190', 'hciplayer', 'note')]
    assert T("# demo's 0x401190") == [('0x401190', 'demo', 'note')]
    assert T("# In Hciplayer's code, 0x401190") == [('0x401190', 'hciplayer', 'note')]
    assert T("# demo's and hciplayer's 0x401190") == [('0x401190', '?', 'told')]
    assert T("# demo's" + ' ' * 54 + '0x401190') == [('0x401190', 'demo', 'note')]
    assert T("# demo's" + ' ' * 55 + '0x401190') == [('0x401190', '?', 'told')]             # named too far back
    assert T("# 0x401190 is demo's") == [('0x401190', '?', 'told')]                         # named after it
    assert T("# demo 0x401190") == [('0x401190', '?', 'told')] and T("# xdemo's 0x401190") == [('0x401190', '?', 'told')]
    assert T("# demo's 0x401190", home='hciplayer') == [('0x401190', 'hciplayer', 'note')]  # the file's binary first
    assert T('# 0x401190', anchors=[('hciplayer', 0x401190, 'note')]) == [('0x401190', 'hciplayer', 'told')]
    assert T("# demo's 0x401190", anchors=[('hciplayer', 0x401190, 'note')]) == [('0x401190', 'demo', 'note')]
    assert V('# 0x401190', anchors=[('hciplayer', 0x401190, 'note'), ('demo', 0x401190, 'line')]) == [('0x401190', '?', 'told')]
    assert T('# 0x401190', anchors=[('hciplayer', 0x401190, 'table')]) == [('0x401190', 'hciplayer', 'same')]
    # a comment in a file of the binary's before a neighbour of another's
    assert V('0x401048 0x401064 0x401090\n# 0x401190', anchors=[('hciplayer', 0x4011a0, 'name')])[3:] == [('0x401190', 'demo', 'note')]
    assert V('0x401048 0x401064\n# 0x401190', anchors=[('hciplayer', 0x4011a0, 'name')])[2:] == [('0x401190', 'hciplayer', 'near')]
    # a tie goes to the nearest solid literal
    assert V('0x401048 0x401064 0x401090\n0x401018 0x401044 0x40108c\n# 0x401190')[6:] == [('0x401190', 'hciplayer', 'note')]
    assert V('0x401018 0x401044 0x40108c\n0x401048 0x401064 0x401090\n# 0x401190')[6:] == [('0x401190', 'demo', 'note')]


def test_judge_refuses_what_is_no_address(views):
    V = lambda text, **kw: verdicts(text, views, **kw)
    for text in ('color = 0x401048', 'rgb(0x401048)', 'RGBA(1, 2, 0x401048)', 'color_t{0x401048}', 'px: 0x401048', 'TINT_GREY = [0x401048]',
                 'row_colours[2] = 0x401048', 'ink, 0x401048', 'bg_color 0x401048', 'luma(12, 0x401048)', 'grey=0x401048', 'gray = (0x401048)',
                 'hue = 0x401048', 'mix(0x401048', 'blend(0x401048', 'gradient(0x401048', 'colors = [0x12345, 0x401048]', 'BAR_COLOR = 0x401048',
                 'rgba(0x401048', 'RGB(0x401048', 'NP_RGB_TOP = 0x401048', 'colour: 0x401048', 'color[i] = {0x12, 0x401048}'):
        assert V(text) == [], text
    for text in ('colour = 0; site = 0x401048', 'px 12 at 0x401048', 'express = 0x401048', 'INK = 0x401048', 'tinted = 0x401048', 'rgbx(0x401048)',
                 'color = f(0x401048)', 'color_t c = {0x401048}', 'Colour = 0x401048', 'ink.0x401048'):
        assert V(text) == [('0x401048', 'demo', 'start')], text
    assert V('color alpha = 0x401000') == [('0x401000', 'demo', 'name')] and V('color = 0x401048, 0x27bdffe8') == [('0x401048', 'demo', 'word')]
    assert V('color = 0x401048; 0x401064') == [('0x401064', 'demo', 'start')]
    # an offset
    for text in ('base + 0x401048', 'base+0x401048', 'x - 0x401048', 'f(a) -0x401048', 'a[1] + 0x401048', 'a +  0x401048'):
        assert V(text) == [], text
    for text in ('x = -0x401048', '(+0x401048)', 'a + b, 0x401048', '0x401048 + 4', 'a * 0x401048'):
        assert V(text) == [('0x401048', 'demo', 'start')], text
    assert V('alpha + 0x401000') == [('0x401000', 'demo', 'name')]
    # a page or a size
    page = {'demo': rebase.view(Toy(shift=0xc000).elf)}                         # its .data starts at 0x410000, and its code names that
    assert verdicts('0x410020', page) == [('0x410020', 'demo', 'data')] and verdicts('0x410000', page) == []
    assert verdicts('0x410000 0x00401000', page) == [('0x410000', 'demo', 'word')]
    # a line of numbers most of which nothing claims
    assert V('0x401048, 0x401140, 0x401150, 0x401160') == [] and V('0x401048, 0x401140, 0x401150') == [('0x401048', 'demo', 'start')]
    assert V('0x401048, 0x401064, 0x401150, 0x401160') == [('0x401048', 'demo', 'start'), ('0x401064', 'demo', 'start')]
    assert V('0x401048, 0x401064, 0x401150, 0x401160, 0x401170') == []
    assert V('0x401048, 0x401140, 0x401150, 0x401160', prose=True) == [('0x401048', 'demo', 'start')]
    assert V('0x401048, 0x27bdffe8, 0x401140, 0x401150, 0x401160') == [('0x401048', 'demo', 'word')]
    assert V('0x401048, 0x401140, 0x401150, 0x00401160') == [('0x401048', 'demo', 'start')]      # an 8-digit word is no address
    assert V('0x401048, 0x401140,\n0x401150, 0x401160') == [('0x401048', 'demo', 'start')]


def test_about():
    A = lambda rel: tuple(rebase.about(rel).values())
    assert A('tools/build.py') == ('demo', True, False) and A('tools/ipod.py') == ('demo', True, False) and A('patch/ipod.json') == ('demo', True, False)
    assert A('tools/peq.py') == ('hciplayer', True, False) and A('tools/flac.py') == ('hciplayer', True, False)
    assert A('patch/peq_player.c') == ('hciplayer', False, False) and A('patch/flac_frame.c') == ('hciplayer', False, False)
    assert A('patch/mp3_gapless.c') == ('hciplayer', False, False) and A('patch/lavf_gapless.c') == ('hciplayer', False, False)
    assert A('tools/bt_resample.S') == ('bluealsa', False, False) and A('patch/navigation.c') == ('demo', False, False)
    assert A('docs/internals.md') == (None, False, True) and A('test/patch.py') == (None, False, False) and A('tools/buildx.py') == (None, False, False)
    assert A('xtools/build.py') == (None, False, False) and A('tools/flacs.py') == (None, False, False) and A('patch/flac_.c') == ('demo', False, False)
    assert list(rebase.about('x')) == ['home', 'table', 'prose']


def test_judge_tree(views):
    texts = {'tools/build.py': 'SITES = [0x401010]\n# 0x401100\n', 'test/a.py': 'f(0x401010)\ng(0x401100)\nh(0x401150)\n',
             'patch/x.c': '// 0x401010\n// 0x401150\n', 'test/b.py': '0x401048 0x401064 0x401090\n# 0x4011a0\n', 'test/c.py': 'k(0x4011a0)\n',
             'docs/d.md': 'See 0x4011a0 and 0x401010.\n'}
    tree = rebase.judge_tree(texts, views)
    assert {rel: [(lits[n].value, *v) for n, v in sorted(verdict.items())] for rel, (lits, verdict) in tree.items()} == {
        'tools/build.py': [(0x401010, 'demo', 'table'), (0x401100, 'demo', 'table')],
        'test/a.py': [(0x401010, 'demo', 'same'), (0x401100, 'demo', 'same'), (0x401150, 'demo', 'told')],   # by build.py, and by x.c's comment
        'patch/x.c': [(0x401010, 'demo', 'note'), (0x401150, 'demo', 'note')],                               # its own evidence stays
        'test/b.py': [(0x401048, 'demo', 'start'), (0x401064, 'demo', 'start'), (0x401090, 'demo', 'start'), (0x4011a0, 'demo', 'note')],
        'test/c.py': [],                                                                                     # b.py is not a file about demo
        'docs/d.md': [(0x401010, 'demo', 'same')]}
    assert [lit.value for lit in tree['test/a.py'][0]] == [0x401010, 0x401100, 0x401150]


def judged(text, elf, **kw):
    lits = rebase.literals(text)
    return lits, rebase.judge(text, lits, {'demo': rebase.view(elf)}, **kw)


def rewritten(text, elf, maps):
    lits, verdict = judged(text, elf, home='demo', table=True)
    return rebase.rewrite(text, lits, verdict, maps)


def test_rewrite_carries_addresses_and_the_words_beside_them(old, new, mp):
    text = ('ONE = 0x401048\n'                           # a static that moved
            'TWO = 0x0401064  # seven digits\n'
            'ALPHA = 0x401000\n'                         # where it was
            'WORLD = 0x403010, COUNTER = 0x404020\n'
            'PATCH(0x401068, 0x3c040040)\n'              # lui: the same word at its new place
            'PATCH(0x40106c, 0x24843010)\n'              # the low half moved with the string
            'AFTER(0x401064, 0x24843010)\n'              # two words on from the address
            'BYTES(0x40106c, "103084244000023c")\n'      # two words, as bytes
            'GUESS = 0x404024\n'
            'OTHER = 0x24843010\n'                       # the same word again, lines away from an address: the nearest address's
            'SLOT = 0x405008\n')                         # a GOT slot no code names, its neighbours changed
    lits, verdict = judged(text, old.elf, home='demo', table=True)
    now, notes = rebase.rewrite(text, lits, verdict, {'demo': mp})
    assert now == ('ONE = 0x401080\n'
                   'TWO = 0x0401054  # seven digits\n'
                   'ALPHA = 0x401000\n'
                   'WORLD = 0x403110, COUNTER = 0x404120\n'
                   'PATCH(0x401058, 0x3c040040)\n'
                   'PATCH(0x40105c, 0x24843110)\n'
                   'AFTER(0x401054, 0x24843110)\n'
                   'BYTES(0x40105c, "103184244000023c")\n'
                   'GUESS = 0x404124\n'
                   'OTHER = 0x24843110\n'
                   'SLOT = 0x405008\n')
    assert notes == [(9, '0x404024', "demo: carried to 0x404124 by its neighbours' shift, a guess"),
                     (11, '0x405008', 'demo: not carried, unreferenced (gamma+0x3f4c(past))')]
    assert rewritten(now, new.elf, {'demo': rebase.Map(new.elf, new.elf)}) == (now, [])


def test_rewrite_leaves_what_it_cannot_place(old):
    maps = {'demo': rebase.Map(old.elf, Toy(gamma=2).elf)}
    text = 'GAMMA = 0x4010bc, 0x34021234\nSITE = 0x4010c0\nSLOT = 0x405008\nBOTH = 0x401000\nFAR = 0x27bdffe8\nUP = 0x00431021\n'   # the GOT did not move
    lits, verdict = judged(text, old.elf, home='demo', table=True)
    assert verdict[4] == ('demo', 'start')
    verdict[4] = ('?', 'start')
    assert rebase.rewrite(text, lits, verdict, maps) == (text, [                 # the addresses first, then the words
        (2, '0x4010c0', 'demo: not carried, code changed (gamma+0x4)'),
        (4, '0x401000', 'ambiguous: start in more than one binary'),
        (1, '0x34021234', 'demo: the stock word at 0x4010c0 (gamma+0x4) gone'),
        (6, '0x00431021', 'demo: the stock word at 0x4010c8 (gamma+0xc) gone')])
    blob = bytearray(old.blob)
    struct.pack_into('<I', blob, old.elf.sec['.text'][1] + old.s['one'] + 12 - old.TEXT, addiu(V1, V1, 2))
    maps = {'demo': rebase.Map(old.elf, rebase.Elf(bytes(blob)))}
    text = 'ONE = 0x401048\nx = "01006324204043ac"\ny = 0x24630001\nz = 0xac434020\n'
    assert rewritten(text, old.elf, maps) == (text, [
        (2, '0x24630001', 'demo: the stock word at 0x401054 (alpha+0x54(past)) changed, now 0x24630002'),
        (3, '0x24630001', 'demo: the stock word at 0x401054 (alpha+0x54(past)) changed, now 0x24630002')])
    # a word more than five words from every address is not looked for; nor a literal of another width
    text = 'ALPHA = 0x401000\nfar = 0x27bdffe8\nnear = 0x24630001\nshort = 0x2463001\n'
    assert rewritten(text, old.elf, maps) == (text, [])
    text = 'ALPHA = 0x401040\nw = 0x24630001\n'
    assert rewritten(text, old.elf, maps)[1] == [
        (2, '0x24630001', 'demo: the stock word at 0x401054 (alpha+0x54(past)) changed, now 0x24630002')]
    text = 'ALPHA = 0x40103c\nw = 0x24630001\n'
    assert rewritten(text, old.elf, maps)[1] == []
    text = 'BETA = 0x401068\nw = 0x24630001\n'           # five words back from an address
    assert len(rewritten(text, old.elf, maps)[1]) == 1
    text = 'BETA = 0x40106c\nw = 0x24630001\n'
    assert rewritten(text, old.elf, maps)[1] == []


def test_rewrite_takes_a_word_from_the_nearest_address(old, mp):
    """The same word sits at two addresses of the file: the literal is the one by the nearer line, then the nearer word."""
    # jalr $t9 / nop is in alpha twice, after its call of one and of printf; only the first is followed by a GOT load that moved
    text = 'A = 0x401010\n"09f8200300000000"\nB = 0x401024\n'
    lits, verdict = judged(text, old.elf, home='demo', table=True)
    assert rebase.rewrite(text, lits, verdict, {'demo': mp})[0] == 'A = 0x40101c\n"09f8200300000000"\nB = 0x401030\n'
    text = 'A = 0x401018, B = 0x40102c, 0x8f84801c\n'       # the word after each call: lw of the GOT's rodata slot after the first
    lits, verdict = judged(text, old.elf, home='demo', table=True)
    assert rebase.rewrite(text, lits, verdict, {'demo': mp})[0] == 'A = 0x401024, B = 0x401038, 0x8f848020\n'


def test_stale():
    a = Toy(tail=64)
    b = Toy(order=('alpha', 'two', 'one', 'beta', 'gamma'), pad=20, shift=0x100, slot=1, tail=64)
    maps, va, vb = {'demo': rebase.Map(a.elf, b.elf)}, {'demo': rebase.view(a.elf)}, {'demo': rebase.view(b.elf)}
    assert maps['demo'].lookup(0x401190) == (0x4011e0, 'code') and maps['demo'].lookup(0x401068) == (0x40109c, 'code')
    S = lambda texts: rebase.stale(texts, maps, va, vb)
    # the old address beside its stock word: stale whatever the new build has there; the new one is not
    assert S({'tools/build.py': 'PATCH(0x401068, 0x3c040040)\n'}) == [('tools/build.py', 1, '0x401068', 'demo', 'word')]
    assert S({'tools/build.py': '\nx = alpha, 0x401044\n'}) == [] and S({'tools/build.py': 'PATCH(0x40109c, 0x3c040040)\n'}) == []
    assert S({'test/x.py': 'beta(0x4010a0)\n'}) == [('test/x.py', 1, '0x4010a0', 'demo', 'name')]
    # an old function start, old data: stale where the new build has nothing for them
    assert S({'test/x.py': 'f(0x401048)\n'}) == [('test/x.py', 1, '0x401048', 'demo', 'start')]
    assert S({'test/x.py': 'k(0x403010)\nl(0x403110)\n'}) == [('test/x.py', 1, '0x403010', 'demo', 'data')]
    assert S({'tools/build.py': 'ONE = 0x401048\n'}) == []                                  # a table of the new build's sites takes it as one
    assert S({'tools/build.py': 'ONE = 0x401048\n', 'test/x.py': 'f(0x401048)\n'}) == []   # and so does every file after it
    assert S({'test/x.py': 'f(0x401048, 0x40104c)\n'}) == [('test/x.py', 1, '0x401048', 'demo', 'start')]
    assert S({'tools/build.py': 'TABLE = 0x401194\n'}) == []                               # only the table's word for it, old or new
    # where an address the tree holds on the new layout came from
    assert S({'tools/build.py': 'PATCH(0x4011e0, 0x2402002f)\n', 'test/x.py': 'g(0x401190)\nh(0x4011e0)\n'}) == [('test/x.py', 1, '0x401190', 'demo', 'same')]
    assert S({'test/x.py': 'g(0x401190)\nh(0x4011e0)\n'}) == []
    assert rebase.stale({'test/x.py': 'f(0x401048)\n'}, {'demo': rebase.Map(a.elf, a.elf)}, va, va) == []
    assert rebase.stale({'test/x.py': 'f(0x401048)\n'}, {'demo': rebase.Map(a.elf, rebase.Elf(a.blob))}, va, vb) == []   # the same file: nothing moved
    # data the new build forms two ways is not carried: stale though the new build's table would take the old address
    c = Toy(shift=4, tail=64)
    torn = bytearray(c.blob)
    struct.pack_into('<I', torn, c.elf.sec['.text'][1] + c.s['one'] + 8 - c.TEXT, lw(V1, lo(c.s['counter']) + 4, V0))
    ec = rebase.Elf(bytes(torn))
    mc = rebase.Map(a.elf, ec)
    assert mc.lookup(0x404020) == (None, 'conflict 0x404028x1 0x404024x1') and ec.section(0x404020) == '.data'
    assert rebase.stale({'tools/build.py': 'COUNTER = 0x404020\n'}, {'demo': mc}, va, {'demo': rebase.view(ec)}) == [('tools/build.py', 1, '0x404020', 'demo', 'data')]
    assert rebase.stale({'tools/build.py': 'TABLE = 0x404008\n'}, {'demo': mc}, va, {'demo': rebase.view(ec)}) == []


MAP = ("# Our names for stock's unnamed functions, at stock V1.32's addresses.\n"
       'binary\taddress\tsize\tname\twhat\n'
       'demo\t0x401048\t0x1c\tone\tcounts a call\n'
       'demo\t0x401064\t0x2c\ttwo\tloads world\n'
       '\n'
       'demo\t0x404020\t0x4\tcounter\thow many calls\n')


def test_read_stock_map():
    version, names, rows = rebase.read_stock_map(MAP)
    assert version == 'V1.32' and dict(names) == {'demo': [(0x401048, 0x1c, 'one'), (0x401064, 0x2c, 'two'), (0x404020, 4, 'counter')]}
    assert rows == [['demo', '0x401048', '0x1c', 'one', 'counts a call'], ['demo', '0x401064', '0x2c', 'two', 'loads world'],
                    ['demo', '0x404020', '0x4', 'counter', 'how many calls']]
    assert rebase.read_stock_map('at V1.34.2 and V1.32\ncolumns\n')[::2] == ('V1.34.2', [])
    assert rebase.read_stock_map('V10.4\n')[0] == 'V10.4' and rebase.read_stock_map('V1.32\ncolumns\n \n\t\n')[2] == []
    for bad in ('no version here\ncolumns\n', 'V1\ncolumns\n', 'v1.32\ncolumns\n'):
        with pytest.raises(ValueError, match='^stock map: the first line names no stock version$'):
            rebase.read_stock_map(bad)
    with pytest.raises(ValueError, match=r"^stock map: \['demo', '0x401048', '0x1c', 'one'\] is not binary, address, size, name, what$"):
        rebase.read_stock_map('V1.32\ncolumns\ndemo\t0x401048\t0x1c\tone\n')
    with pytest.raises(ValueError, match='is not binary'):
        rebase.read_stock_map('V1.32\ncolumns\ndemo\t0x401048\t0x1c\tone\twhat\tmore\n')


def test_carry_stock_map(old, mp):
    more = MAP + 'demo\t0x401000\t0x48\talpha_copy\tgrew\ndemo\t0x405008\t0x4\tslot\tnot named by code\ndemo\t0x401090\t0x0\tbeta_start\tno size\n'
    assert rebase.carry_stock_map(more, {'demo': mp}) == (
        "# Our names for stock's unnamed functions, at stock V1.32's addresses.\n"
        'binary\taddress\tsize\tname\twhat\n'
        'demo\t0x401080\t0x1c\tone\tcounts a call\n'
        'demo\t0x401054\t0x2c\ttwo\tloads world\n'
        'demo\t0x404120\t0x4\tcounter\thow many calls\n'
        'demo\t0x401000\t0x54\talpha_copy\tgrew\n'
        'demo\t0x405008\t0x4\tslot\tnot named by code\n'
        'demo\t0x40109c\t0x0\tbeta_start\tno size\n', [('slot', 'demo: not carried from 0x405008, unreferenced')])
    blob = bytearray(old.blob)
    struct.pack_into('<I', blob, old.elf.sec['.text'][1] + old.s['one'] + 12 - old.TEXT, addiu(V1, V1, 2))
    text, notes = rebase.carry_stock_map(MAP, {'demo': rebase.Map(old.elf, rebase.Elf(bytes(blob)))})
    assert text == MAP.replace('\n\n', '\n') and notes == [('one', 'demo: changed, 0 words gone and 1 changed of 7')]
    gamma = 'V1.32\ncolumns\ndemo\t0x4010bc\t0x18\tgamma_copy\tx\ndemo\t0x4010b4\t0x10\tbeta_tail\ty\n'
    assert rebase.carry_stock_map(gamma, {'demo': rebase.Map(old.elf, Toy(gamma=2).elf)}) == (gamma, [
        ('gamma_copy', 'demo: changed, 5 words gone and 0 changed of 6'), ('beta_tail', 'demo: changed, 1 words gone and 0 changed of 4')])
    # a function that lost its end keeps its size; one that lost only its start, or its middle, takes what is left
    grown = Toy(order=('alpha', 'one', 'two', 'beta', 'gamma'), gamma=2, tail=4)
    tail = 'V1.32\ncolumns\ndemo\t0x4010bc\t0x28\tgamma_and_more\tx\n'
    assert rebase.carry_stock_map(tail, {'demo': rebase.Map(Toy(tail=4).elf, grown.elf)})[0] == tail


def test_function_diff(old, new):
    assert rebase.function_diff(old.elf, old.elf) == (3, [], [], [])
    assert rebase.function_diff(old.elf, new.elf) == (2, [('alpha', 0x48, 0x54)], [], [])
    assert rebase.function_diff(old.elf, Toy(gamma=2, shift=0x40).elf) == (2, [('gamma', 0x18, 0x18)], [], [])
    renamed = rebase.Elf(old.blob.replace(b'gamma\0', b'delta\0'))
    assert rebase.function_diff(old.elf, renamed) == (2, [], ['gamma'], ['delta'])
    assert rebase.function_diff(renamed, old.elf) == (2, [], ['delta'], ['gamma'])
    odd = rebase.Elf(old.blob)
    odd.syms.update(empty=(0x401048, 0, 'FUNC'), outside=(0x402000, 16, 'FUNC'), before=(0x400800, 8, 'FUNC'), thing=(0x401064, 8, 'OBJECT'),
                    last=(odd.end, 4, 'FUNC'))
    assert rebase.function_diff(odd, odd) == (3, [], [], []) and rebase.function_diff(odd, old.elf) == (3, [], [], [])
    odd.syms['first'] = (0x401000, 8, 'FUNC')
    assert rebase.function_diff(odd, old.elf) == (3, [], ['first'], [])


def test_gaps():
    assert rebase.gaps([0, -1, -1, 3, -1, 5, -1, -1, -1], 0x1000) == [(0x1004, 2), (0x1018, 3)]
    assert rebase.gaps([0, -1, -1, 3, -1, 5, -1, -1, -1], 0x1000, 1) == [(0x1004, 2), (0x1010, 1), (0x1018, 3)]
    assert rebase.gaps([0, -1, -1, 3, -1, 5, -1, -1, -1], 0, 3) == [(24, 3)] and rebase.gaps([-1, -1], 8) == [(8, 2)]
    assert rebase.gaps([], 0) == [] and rebase.gaps([0, 1, 2], 0) == [] and rebase.gaps([-1, 0, 0, -1], 0) == []


def test_places(old):
    s = old.s
    mp = rebase.Map(old.elf, Toy(gamma=2).elf)
    every = list(range(old.TEXT, old.elf.end, 4)) + [0x403000, old.TEXT - 4, old.elf.end]
    assert dict(rebase.places(mp, every)) == {('gamma', 'body'): [(s['gamma'], s['gamma'])] + [(s['gamma'] + o, None) for o in range(4, 24, 4)]}
    assert dict(rebase.places(mp, [s['gamma'] + 8, s['gamma'] + 8, s['gamma']])) == {('gamma', 'body'): [(s['gamma'], s['gamma']), (s['gamma'] + 8, None)]}
    blob = bytearray(old.blob)
    struct.pack_into('<I', blob, old.elf.sec['.text'][1] + s['two'] + 28 - old.TEXT, beq(V0, 0, 2))     # a word changed in a static after alpha
    mp = rebase.Map(old.elf, rebase.Elf(bytes(blob)))
    assert mp.unexplained == {s['two'] + 28}
    assert dict(rebase.places(mp, [s['alpha'] + 4, s['one'], s['two'] + 4, s['beta'], s['gamma']])) == {
        ('alpha', 'statics after'): [(s['one'], s['one']), (s['two'] + 4, s['two'] + 4)]}
    assert dict(rebase.places(mp, [s['alpha'] + 0x44, s['alpha'] + 0x48])) == {('alpha', 'statics after'): [(s['alpha'] + 0x48, s['alpha'] + 0x48)]}
    named = rebase.Elf(old.blob)
    named.named([(s['one'], 0x1c, 'one'), (s['two'], 0x2c, 'two'), (0x404020, 4, 'counter')])
    mp = rebase.Map(named, rebase.Elf(bytes(blob)))
    assert dict(rebase.places(mp, [s['alpha'] + 4, s['one'], s['two'] + 4, s['beta'], s['gamma']])) == {('two', 'body'): [(s['two'] + 4, s['two'] + 4)]}
    struct.pack_into('<I', blob, old.elf.sec['.text'][1] + s['alpha'] + 4 - old.TEXT, sw(RA, 24, SP))  # and one in alpha's own body
    mp = rebase.Map(old.elf, rebase.Elf(bytes(blob)))
    assert dict(rebase.places(mp, [s['alpha'], s['one']])) == {('alpha', 'body'): [(s['alpha'], s['alpha'])], ('alpha', 'statics after'): [(s['one'], s['one'])]}


def test_strings_and_config_keys():
    blob = b'\x00hello world\x00short\x01another one!\xff\x7f12345678\x1f  spaces  \n'
    assert rebase.strings(blob) == {'hello world', 'another one!', '12345678', '  spaces  '}
    assert rebase.strings(blob, 5) == {'hello world', 'short', 'another one!', '12345678', '  spaces  '}
    assert rebase.strings(b'~tilde and space ~') == {'~tilde and space ~'} and rebase.strings(b'1234567') == set()
    assert rebase.config_keys('[PLAY]\r\nVOL=5\r\nnote\n[SYS] \r\nLANG = en\nA=b=c\n') == ['PLAY VOL', 'SYS LANG', 'SYS A']
    assert rebase.config_keys('TOP=1\n[X]\n') == [' TOP'] and rebase.config_keys('') == []


def test_pins():
    A, B, C, D = 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64
    old = types.SimpleNamespace(version='V1.32', member='Q2 Firmware V1.32/update.tar')
    new = types.SimpleNamespace(version='V1.34', member='Q2 V1.34 beta/update.tar')
    hashes = ({A: 'the ZIP', C: 'release/bin/gone', D: 'same'}, {B: 'the ZIP', D: 'same'})
    text = (f'ZIP = "{A}"\n\nGONE = "{C}"\nSAME = "{D}" OTHER = "{"e" * 64}"\nLONG = "{A}a" x{A} {A}x\n'
            'V1.32 (V1.32) V1.321 V1.32.1 XV1.32 1.V1.32 v1.32 V1x32\nPATH = "Q2 Firmware V1.32/update.tar"\n')
    assert rebase.pins(text, old, new, hashes) == (
        f'ZIP = "{B}"\n\nGONE = "{C}"\nSAME = "{D}" OTHER = "{"e" * 64}"\nLONG = "{A}a" x{B} {B}x\n'
        'V1.34 (V1.34) V1.321 V1.34.1 XV1.32 1.V1.32 v1.32 V1x32\nPATH = "Q2 V1.34 beta/update.tar"\n',
        [(3, 'c' * 12, 'release/bin/gone is not in the new firmware')])
    same = types.SimpleNamespace(version='V1.32', member='other/update.tar')
    assert rebase.pins(text, old, same, hashes)[0] == text.replace(A + '"', B + '"', 1).replace('x' + A, 'x' + B).replace(A + 'x', B + 'x')
    dotted = types.SimpleNamespace(version='V1x32', member='none')
    assert rebase.pins('V1.32 V1x32 V1y32', dotted, new, ({}, {})) == ('V1.32 V1.34 V1y32', [])


def test_rewrite_carries_a_section_by_its_file_offset_and_size(old, new, mp):
    a, b = old.elf, new.elf
    assert (a.sec['.rodata'][1:3], b.sec['.rodata'][1:3]) == ((0x20c, 0x50), (0x218, 0x50))
    assert (a.sec['.got'][1:3], b.sec['.got'][1:3]) == ((0x284, 0x1c), (0x290, 0x20)) and b.sec['.text'][1:3] == (0xe4, 0xe0)
    text = ('RODATA = (0x0000020c, 0x00050)\nSPLIT = 0x0000020c,\n  0x00050\nSWAPPED = (0x00050, 0x0000020c)\nOTHER = (0x0000020c, 0x00054)\n'
            'GOT = 0x00284 0x0001c, TEXT = [0x000e4, 0x00000d4]\nEMPTY = 0x00000 0x00000\n"0c020000" 0x00050\n')
    lits = rebase.literals(text)
    assert rebase.rewrite(text, lits, {}, {'demo': rebase.Map(a, a)}) == (text, [])
    now = ('RODATA = (0x00000218, 0x00050)\nSPLIT = 0x0000020c,\n  0x00050\nSWAPPED = (0x00050, 0x0000020c)\nOTHER = (0x0000020c, 0x00054)\n'
           'GOT = 0x00290 0x00020, TEXT = [0x000e4, 0x00000e0]\nEMPTY = 0x00000 0x00000\n"0c020000" 0x00050\n')
    assert rebase.rewrite(text, lits, {}, {'demo': mp}) == (now, [])
    # an offset judged an address, or a size that is also a stock word by an address, is still the section's
    got, text_size = (next(n for n, lit in enumerate(lits) if lit.value == v) for v in (0x284, 0xd4))
    assert rebase.rewrite(text, lits, {got: ('demo', 'word')}, {'demo': mp}) == (now, [])
    held = types.SimpleNamespace(a=types.SimpleNamespace(sec={'.text': (0x401000, 0x24843010, 0xd4, 1)}), b=types.SimpleNamespace(sec={'.text': (0, 0x24843210, 0xe0, 1)}))
    pair = 'A = 0x40106c, 0x24843010, 0x000000d4, B = 0x401048\n'  # the word at A moved, but here it is another binary's offset
    lits = rebase.literals(pair)
    assert rebase.rewrite(pair, lits, {0: ('demo', 'word'), 1: ('demo', 'start'), 3: ('demo', 'start')}, {'demo': mp, 'other': held}) == (
        'A = 0x40105c, 0x24843210, 0x000000e0, B = 0x401080\n', [])
    less = types.SimpleNamespace(a=a, b=types.SimpleNamespace(sec={n: v for n, v in b.sec.items() if n != '.rodata'}))
    lits = rebase.literals(text)
    out = rebase.rewrite(text, lits, {}, {'demo': less})[0]
    assert out.startswith('RODATA = (0x0000020c, 0x00050)\n') and 'GOT = 0x00290 0x00020' in out


def test_rewrite_places_a_pic_prologue_by_the_distance_it_holds():
    """lui $gp / addiu $gp, $gp / addu $gp, $gp, $t9 is how far the GOT is from the function: such a literal
    needs no address beside it."""
    def build(gp, at, first=0x3c1c0000, second=0x279c0000):  # a binary with one function, at `at`
        d = gp - at
        words = [first | (d + 0x8000) >> 16, second | d & 0xffff, 0x0399e021]
        return types.SimpleNamespace(gp=gp, sec={}, where=hex, u32=lambda a: words[(a - at) // 4] if a in (at, at + 4, at + 8) else None), words
    le = lambda words: struct.pack(f'<{len(words)}I', *words).hex()
    def run(a, wa, b, words=None):
        mp = types.SimpleNamespace(a=a, b=b, word=lambda x: (b.u32(x - 0x401230 + 0x40f5f8), 'moved'))
        nowhere = types.SimpleNamespace(a=types.SimpleNamespace(gp=a.gp + 0x10000, sec={}, u32=lambda a: None), b=None)
        text = f'"entry": "{le(words or wa)}"\n'
        return rebase.rewrite(text, rebase.literals(text), {}, {'hciplayer': nowhere, 'demo': mp})[0]
    b, wb = build(0x49a000, 0x40f5f8)
    for gp in (0x48f000, 0x48a000):  # 0x8ddd0 from the function, a negative low half; 0x88dd0, a positive one
        a, wa = build(gp, 0x401230)
        assert run(a, wa, b) == f'"entry": "{le(wb)}"\n'
        assert run(a, wa, b, wa[:2]) == f'"entry": "{le(wa[:2])}"\n'                       # two words are no prologue
        assert run(a, wa, b, [*wa[:2], 0]) == f'"entry": "{le([*wa[:2], 0])}"\n'           # nor are three the binary does not have there
    for first, second in ((0x3c020000, 0x279c0000), (0x3c1c0000, 0x24420000)):                # lui $v0, or addiu $v0, $v0: no GOT's distance
        a, wa = build(0x48f000, 0x401230, first, second)
        assert run(a, wa, b) == f'"entry": "{le(wa)}"\n'


def test_refs(old):
    def said(what, elf=old.elf):
        out = io.StringIO()
        rebase.refs(elf, what, out)
        return out.getvalue().splitlines()
    s = old.s
    assert said(s['counter']) == ["0x401050  alpha+0x50(past)  ->  0x404020 'gamma+0x2f64(past)'", "0x401058  alpha+0x58(past)  ->  0x404020 'gamma+0x2f64(past)'"]
    named = rebase.Elf(old.blob)
    named.named([(s['one'], 0x1c, 'one'), (s['two'], 0x2c, 'two'), (0x404020, 4, 'counter')])
    assert said(s['counter'], named) == ["0x401050  one+0x8  ->  0x404020 'counter'", "0x401058  one+0x10  ->  0x404020 'counter'"]
    assert said(s['two'], named) == ["0x401034  alpha+0x34  ->  0x401064 'two'", "0x40109c  beta+0xc  ->  0x401064 'two'"]       # called
    assert said(s['one'], named) == ["0x40100c  alpha+0xc  ->  0x401048 'one'", "0x401078  two+0x14  ->  0x401048 'one'",
                                     "0x4010b0  beta+0x20  ->  0x401048 'one'"]
    assert said(s['printf'], named) == ["0x401020  alpha+0x20  ->  0x402000 'printf'"]                                            # its GOT slot loaded
    assert said(s['g_flag'], named) == ["0x40102c  alpha+0x2c  ->  0x406010 'g_flag'", "0x401030  alpha+0x30  ->  0x406010 'g_flag'"]
    assert said('hello', named) == ["0x40101c  alpha+0x1c  ->  0x403000 'hello, world'"]             # not the load of its page from the GOT
    assert said('string', named) == ["0x40106c  two+0x8  ->  0x403010 'second string'"]
    assert said('o', named) == ["0x40101c  alpha+0x1c  ->  0x403000 'hello, world'", "0x40106c  two+0x8  ->  0x403010 'second string'"]
    assert said(0x403014, named) == ["0x403014 'gamma+0x1f58(past)': no code names it"] and said('nothing like it', named) == []
    assert said(0x405008, named) == ["0x405008 'counter+0xfe8(past)': no code names it"]
    lone = rebase.Elf(old.blob.replace(b'hello, world\0', b'hello, w\0rld\0'))
    assert said('rld', lone) == ["0x403009 'rld': no code names it"] and said('hello', lone) == ["0x40101c  alpha+0x1c  ->  0x403000 'hello, w'"]
