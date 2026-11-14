#!/usr/bin/env python3
"""Carry Q2 Pod's stock addresses from one stock firmware ZIP to the next; see docs/rebasing.md.

  rebase.py report OLD.zip NEW.zip   what Shanling changed, and which of our addresses sit in it
  rebase.py apply OLD.zip NEW.zip    rewrite the addresses, expected stock words and pins in patch/, tools/, test/ and docs/
  rebase.py check OLD.zip NEW.zip    fail while an address is still on OLD's layout
  rebase.py refs ZIP BINARY WHAT...  where a binary's code forms an address, calls a function or loads a string

Code: the two .text streams are aligned between exported symbols on words whose address operands are masked;
a function the alignment lost is aligned again from where the calls to it went.
Data: each aligned lui or GOT load and the low half after it vote for where the address they form went.
The stock ZIPs are untrusted and Shanling's: run this with python3 -I. It extracts under a fresh directory
outside the repo, and writes nothing of theirs into the repo but addresses, instruction words, hashes and
(checked) names.
"""
import argparse, bisect, collections, difflib, gzip, hashlib, io, pathlib, re, struct, subprocess, sys, tarfile, tempfile, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
# The stock executables our files name addresses in, by their place in the rootfs. Libraries and kernel
# modules load anywhere: our files pin those by SHA-256 and name offsets, which this tool leaves alone.
BINARIES = {'demo': 'release/bin/demo', 'hciplayer': 'usr/bin/hciplayer', 'bluealsa': 'usr/bin/bluealsa'}
# Our names for stock's unnamed functions and data: binary, address, size, name, what it does.
STOCK_MAP = ROOT/'tools/stock_map.tsv'
# How to find each patch site again, in the same columns: binary, address, 4, the function it is in, where in it and
# what uses it. docs/rebasing/sitenotes.py writes it; apply carries it, and a row it cannot carry is what to look for.
STOCK_SITES = ROOT/'tools/stock_sites.tsv'
TABLES = tuple(str(p.relative_to(ROOT)) for p in (STOCK_MAP, STOCK_SITES))
SCANNED = ('patch', 'tools', 'test', 'docs')
# Never rewritten: history, and this tool's tests, whose addresses are a toy program's.
SKIPPED = ('docs/changelog.md', 'test/unit/test_rebase.py')

def check(condition, message):
    if not condition: raise ValueError(message)

def s16(x): return x - 0x10000 if x & 0x8000 else x

def decisive(counter):
    """The value most of counter's votes are for, if it has over twice the next one's; else None."""
    best = counter.most_common(2)
    if best and (len(best) == 1 or best[0][1] > 2 * best[1][1]): return best[0][0]
    return None

class Elf:
    """A stock MIPS32 little-endian executable: sections, .text words, exported and imported names, and the
    stock map's names for it once named() has them."""
    def __init__(self, data):
        d = self.d = data
        check(d[:6] == b'\x7fELF\x01\x01' and struct.unpack_from('<H', d, 18)[0] == 8, 'Not a 32-bit little-endian MIPS ELF')
        shoff, = struct.unpack_from('<I', d, 32)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 46)
        raw = [struct.unpack_from('<10I', d, shoff + i * shentsize) for i in range(shnum)]
        self.sec = {self.cstr_at(raw[shstrndx][4] + r[0]): (r[3], r[4], r[5], r[1]) for r in raw}
        self.loads = sorted((addr, off, size) for addr, off, size, typ in self.sec.values() if addr and typ != 8)
        self.text, off, size, _ = self.sec['.text']
        self.end = self.text + size
        self.words = list(struct.unpack_from(f'<{size // 4}I', d, off))
        self.gp = self.sec['.got'][0] + 0x7ff0
        _, off, size, _ = self.sec['.dynsym']
        dynstr = self.sec['.dynstr'][1]
        dynsym = [struct.unpack_from('<IIIBBH', d, o) for o in range(off, off + size, 16)]
        self.dynnames = [self.cstr_at(dynstr + s[0]) for s in dynsym]
        # name: (address, size, FUNC or OBJECT), the defined ones
        self.syms = {n: (s[1], s[2], 'OBJECT' if s[3] & 15 == 1 else 'FUNC')
                     for n, s in zip(self.dynnames, dynsym) if s[5] and s[3] & 15 in (1, 2)}
        self.named(())
        # A global GOT slot holds the dynamic symbol of its place: the address of the slot to its name.
        dyn = dict(struct.unpack_from('<II', d, self.sec['.dynamic'][1] + o) for o in range(0, self.sec['.dynamic'][2], 8))
        local, first = dyn.get(0x7000000a, 0), dyn.get(0x70000013, len(dynsym))
        self.got = {self.sec['.got'][0] + 4 * (local + i - first): self.dynnames[i] for i in range(first, len(dynsym))}
        # An import's stub to its name: a lazy stub ends li $t8, its dynamic symbol's index; a PLT entry loads its .got.plt slot.
        self.imports = {}
        addr, off, size, _ = self.sec.get('.MIPS.stubs', (0, 0, 0, 0))
        for o in range(0, size - 12, 16):
            if struct.unpack_from('<I', d, off + o)[0] == 0x8f998010 and struct.unpack_from('<I', d, off + o + 12)[0] >> 16 == 0x2418:
                self.imports[addr + o] = self.dynnames[struct.unpack_from('<H', d, off + o + 12)[0]]
        _, off, size, _ = self.sec.get('.rel.plt', (0, 0, 0, 0))
        slots = {slot: self.dynnames[info >> 8] for slot, info in (struct.unpack_from('<II', d, off + o) for o in range(0, size, 8))}
        addr, off, size, _ = self.sec.get('.plt', (0, 0, 0, 0))
        for o in range(32, size - 4, 16):
            hi, lo = struct.unpack_from('<II', d, off + o)
            if hi >> 16 == 0x3c0f and lo >> 16 == 0x8df9 and ((hi & 0xffff) << 16) + s16(lo & 0xffff) in slots:
                self.imports[addr + o] = slots[((hi & 0xffff) << 16) + s16(lo & 0xffff)]
    def named(self, rows):
        """Add the stock map's names, rows of (address, size, name)."""
        for address, size, name in rows:
            self.syms[name] = (address, size, 'FUNC' if self.text <= address < self.end else 'OBJECT')
        self.starts = sorted((a, n) for n, (a, _, _) in self.syms.items())
    def cstr_at(self, o): return self.d[o:self.d.index(b'\0', o)].decode('utf-8', 'replace')
    def off(self, a):
        for addr, off, size in self.loads:
            if addr <= a < addr + size: return off + a - addr
    def u32(self, a):
        o = self.off(a)
        return struct.unpack_from('<I', self.d, o)[0] if o is not None and o + 4 <= len(self.d) else None
    def cstr(self, a):
        o = self.off(a)
        return self.d[o:self.d.index(b'\0', o)] if o is not None else None
    def section(self, a):
        for n, (addr, _, size, _) in self.sec.items():
            if addr and addr <= a < addr + size: return n
    def where(self, a):
        """name+offset of the last named thing at or before a, '(past)' beyond its size."""
        i = bisect.bisect_right(self.starts, (a, '\U0010ffff')) - 1
        if i < 0: return f'{a:#x}'
        s, n = self.starts[i]
        if a == s: return n
        return f'{n}+{a - s:#x}' if a - s < self.syms[n][1] else f'{n}+{a - s:#x}(past)'

def low(op):
    """Whether an opcode's immediate may be an address's low half: addiu, ori, a load or a store."""
    return op in (9, 0x0d) or op >= 0x20

def heavy(w):
    """w with any operand that may carry an address masked: what two builds' words are aligned on."""
    op = w >> 26
    if op in (2, 3): return w & 0xfc000000
    if op == 0x0f: return w & 0xffff0000
    if low(op):
        return w if (w >> 21) & 31 in (0, 29, 30) else w & 0xffff0000  # off $zero, $sp and $fp it is a constant or a frame slot
    if op in (1, 4, 5, 6, 7, 20, 21, 22, 23): return w & 0xffff0000  # a branch or bal to a neighbour moves with it
    return w

def lis(pairs):
    """The longest chain of pairs (i, j) rising in both, from pairs sorted by i."""
    tails, idx, prev = [], [], [-1] * len(pairs)
    for n, (_, j) in enumerate(pairs):
        k = bisect.bisect_left(tails, j)
        if k == len(tails): tails.append(j); idx.append(n)
        else: tails[k] = j; idx[k] = n
        prev[n] = idx[k - 1] if k else -1
    out, n = [], idx[-1] if idx else -1
    while n >= 0: out.append(pairs[n]); n = prev[n]
    return out[::-1]

BIG = 4_000_000  # the most cells difflib is given at once

def chunk(A, B, a0, a1, b0, b1, m, k=6):
    """Align A[a0:a1] with B[b0:b1] into m: m[i] becomes the index in B of A[i] where it has one."""
    if a1 - a0 == b1 - b0 and A[a0:a1] == B[b0:b1]:
        m[a0:a1] = range(b0, b1); return
    if (a1 - a0) * (b1 - b0) > BIG:  # too big for difflib: split on the runs of k words found once in each
        def grams(X, x0, x1):
            seen = {}
            for i in range(x0, x1 - k + 1):
                g = tuple(X[i:i + k]); seen[g] = -1 if g in seen else i
            return seen
        ga, gb = grams(A, a0, a1), grams(B, b0, b1)
        pa, pb = a0, b0
        for i, j in lis(sorted((i, gb[g]) for g, i in ga.items() if i >= 0 and gb.get(g, -1) >= 0)) + [(a1, b1)]:
            if (i - pa) * (j - pb) <= BIG: chunk(A, B, pa, i, pb, j, m, k)
            if i < a1: m[i] = j
            pa, pb = i + 1, j + 1
        return
    sm = difflib.SequenceMatcher(None, A[a0:a1], B[b0:b1], autojunk=False)
    for i, j, n in sm.get_matching_blocks():
        if n >= 3 or (i == 0 and j == 0): m[a0 + i:a0 + i + n] = range(b0 + j, b0 + j + n)

def align(A, B, anchors):
    """m, where m[i] is the index in B of A[i] or -1, and how many anchors were out of order: A and B
    aligned between anchors, pairs of indices kept as their longest rising chain."""
    m = [-1] * len(A)
    chain = lis(sorted(set(anchors)))
    pa = pb = 0
    for i, j in chain + [(len(A), len(B))]:
        chunk(A, B, pa, i, pb, j, m); pa, pb = i, j
    return m, len(set(anchors)) - len(chain)

CALLER = set(range(1, 16)) | {24, 25, 31}  # the registers a call may change

def track(ea, eb, m):
    """Walk the aligned words of two builds in step. Returns
    votes: {old address: Counter of new addresses} the operands formed, from a lui or GOT load and a low half;
    calls: the same for call targets;
    sites: {old pc: (old address, new address)} for each low half;
    callsites: {old pc: (old target, new target)} for each direct call;
    changes: [(old pc, new pc, old word, new word)] that differ for no such reason."""
    votes, calls = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    sites, callsites, changes = {}, {}, []
    WA, WB = ea.words, eb.words; st = {}; clobber = -1
    starts = {(a - ea.text) // 4 for a, _, kind in ea.syms.values() if kind == 'FUNC'}
    for i, j in enumerate(m):
        if j < 0 or (i and m[i - 1] != j - 1): st.clear()
        if j < 0: continue
        if i == clobber:
            for r in CALLER: st.pop(r, None)
        wa, wb = WA[i], WB[j]; pa, pb = ea.text + 4 * i, eb.text + 4 * j
        if i in starts or (wa >> 16 == 0x27bd and wa & 0x8000): st.clear()  # a function starts: an early jr $ra does not end one
        op = wa >> 26; rs = (wa >> 21) & 31; rt = (wa >> 16) & 31; rd = (wa >> 11) & 31
        ia, ib = s16(wa & 0xffff), s16(wb & 0xffff)
        if op in (2, 3):
            ta, tb = (pa & 0xf0000000) | (wa & 0x3ffffff) << 2, (pb & 0xf0000000) | (wb & 0x3ffffff) << 2
            calls[ta][tb] += 1; callsites[pa] = (ta, tb); clobber = i + 2
        elif op == 0:
            f = wa & 63
            if f in (8, 9) and rs in st and st[rs][0] != 'hi': calls[st[rs][1]][st[rs][2]] += 1  # jr: a tail call
            if f == 9: clobber = i + 2
            st.pop(rd, None)  # $zero for a jr, a mult or a div
            if wa != wb: changes.append((pa, pb, wa, wb))
        elif op == 0x0f:
            if rt != 28: st[rt] = ('hi', (wa & 0xffff) << 16, (wb & 0xffff) << 16)  # into $gp it is how far the GOT is from the function, no address
        elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23):
            ti, tj = i + 1 + ia, j + 1 + ib
            ta, tb = ea.text + 4 * ti, eb.text + 4 * tj
            if op == 1 and rt in (0x11, 0x13) and rs == 0 and 0 <= ti < len(m):  # bal: a call
                calls[ta][tb] += 1; callsites[pa] = (ta, tb); clobber = i + 2
            elif op == 4 and rs == 0 and rt == 0 and 0 <= ti < len(m) and abs(ia) > 64 and 25 in st:  # b: a PIC tail call
                calls[ta][tb] += 1; callsites[pa] = (ta, tb)
            elif wa != wb and not (0 <= ti < len(m) and m[ti] == tj): changes.append((pa, pb, wa, wb))
        elif low(op):
            load = 0x20 <= op <= 0x26
            if rs == 28 and op == 0x23:
                ga, gb = ea.gp + ia, eb.gp + ib
                va, vb = ea.u32(ga), eb.u32(gb)
                if ea.got.get(ga) != eb.got.get(gb): changes.append((pa, pb, wa, wb))  # another symbol's slot
                if va is None or vb is None: st.pop(rt, None)
                else: st[rt] = ('got', va, vb)
            elif rs == rt == 28: pass  # the low half of that
            elif rs in st:
                _, ha, hb = st[rs]
                a = ha + (wa & 0xffff if op == 0x0d else ia); b = hb + (wb & 0xffff if op == 0x0d else ib)
                votes[a][b] += 1; sites[pa] = (a, b)
                if op in (9, 0x0d): st[rt] = ('full', a, b)
                elif load: st.pop(rt, None)
            else:
                if wa != wb: changes.append((pa, pb, wa, wb))
                if op in (9, 0x0d) or load: st.pop(rt, None)
        else:
            if wa != wb: changes.append((pa, pb, wa, wb))
            if 8 <= op <= 14: st.pop(rt, None)
            elif op in (0x1c, 0x1f): st.pop(rd, None); st.pop(rt, None)
    return votes, calls, sites, callsites, changes

class View:
    """One build of a binary as a literal's evidence sees it: where its functions start and the data its code names."""
    def __init__(self, elf, starts, refs):
        self.elf, self.starts, self.refs = elf, set(starts), set(refs)
        self.sorted = sorted(self.starts)
        self.objects = sorted((a, max(z, 1)) for a, z, kind in elf.syms.values() if kind == 'OBJECT')
    def function(self, a):
        """The start of the function holding a, or None outside .text."""
        if not self.elf.text <= a < self.elf.end: return None
        i = bisect.bisect_right(self.sorted, a) - 1
        return self.sorted[i] if i >= 0 else self.elf.text
    def data(self, a):
        """Whether a is data the code or a symbol names."""
        if a in self.refs or a == self.elf.gp or a in self.elf.imports or a in self.elf.got: return True
        i = bisect.bisect_right(self.objects, (a, 1 << 32)) - 1
        return i >= 0 and self.objects[i][0] <= a < sum(self.objects[i])

def view(elf):
    """elf's View: the function starts are its symbols', the targets of its calls, the code addresses its
    operands form and the ones its data tables hold; the data is what its operands form."""
    votes, calls, _, _, _ = track(elf, elf, list(range(len(elf.words))))
    tables = set()
    for name in ('.data.rel.ro', '.data', '.got', '.init_array', '.ctors', '.dtors'):
        _, off, size, typ = elf.sec.get(name, (0, 0, 0, 8))
        if typ != 8: tables |= {w for w in struct.unpack_from(f'<{size // 4}I', elf.d, off) if w & 0xffff}  # not the GOT's pages
    starts = {x for x in (*calls, *votes, *tables) if elf.text <= x < elf.end and not x & 3} | {s[0] for s in elf.syms.values() if s[2] == 'FUNC'}
    return View(elf, starts, [x for x in votes if not elf.text <= x < elf.end])

class Map:
    """Where each address of build a of a binary went in build b."""
    def __init__(self, a, b):
        self.a, self.b = a, b
        self.A, self.B = [heavy(w) for w in a.words], [heavy(w) for w in b.words]
        if a.d == b.d: self.m, self.dropped = list(range(len(a.words))), 0
        else:
            self.m, self.dropped = align(self.A, self.B, [((a.syms[n][0] - a.text) // 4, (b.syms[n][0] - b.text) // 4)
                                                             for n in a.syms.keys() & b.syms.keys()
                                                             if a.syms[n][2] == 'FUNC' and a.text <= a.syms[n][0] < a.end])
        self.moved = 0
        for _ in range(6):
            self.votes, self.calls, self.sites, self.callsites, self.changes = track(a, b, self.m)
            n = 0 if a.d == b.d else self.reanchor()
            self.moved += n
            if not n: break
        self.back = {j: i for i, j in enumerate(self.m) if j >= 0}
        # where a function went whose first word changed: by the code that forms its address, by its callers, by its symbol
        self.starts = {x: decisive(c) for x, c in (*self.votes.items(), *self.calls.items()) if decisive(c) is not None}
        self.starts.update((a.syms[n][0], b.syms[n][0]) for n in a.syms.keys() & b.syms.keys() if a.syms[n][2] == b.syms[n][2] == 'FUNC')
        self.starts_back = {y: x for x, y in self.starts.items()}
        self.rdata = {decisive(c): x for x, c in self.votes.items() if decisive(c) is not None}
        self.known = sorted(x for x, c in self.votes.items() if decisive(c) is not None)
        deltas = collections.Counter((y - x) & 0xffff for x, c in self.votes.items() for y in c)
        deltas = {d for d, n in deltas.items() if n >= 3}
        # A word that changed, and not because an address moved: an immediate that moved by a section's shift is
        # the low half of an address whose high half the walk lost; a low half is explained by its address's vote;
        # a call by its target's.
        self.unexplained = {c[0] for c in self.changes if not (((c[3] - c[2]) & 0xffff) in deltas and low(c[2] >> 26))}
        self.unexplained |= {pc for pc, (x, y) in self.sites.items() if decisive(self.votes[x]) != y}
        self.unexplained |= {pc for pc, (x, y) in self.callsites.items() if self.lookup(x)[0] != y}
    def bounds(self):
        """Every function start the two builds show, sorted: (old, new)."""
        text = lambda e, xs: sorted({x for x in xs if e.text <= x < e.end} | {s[0] for s in e.syms.values() if s[2] == 'FUNC'})
        return (text(self.a, [*self.calls, *self.votes]),
                text(self.b, [y for c in (*self.calls.values(), *self.votes.values()) for y in c]))
    def reanchor(self):
        """Align again each function whose callers' calls, or whose address as its users form it, went
        elsewhere than the alignment put it: static functions change order between builds. Returns how many moved."""
        a, b, m = self.a, self.b, self.m
        sa, sb = self.bounds()
        inv = {j: i for i, j in enumerate(m) if j >= 0}
        moved = 0
        for x, c in sorted(list(self.calls.items()) + list(self.votes.items())):
            y = decisive(c)
            if y is None or not a.text <= x < a.end or not b.text <= y < b.end or x & 3 or y & 3 or self.code(x) == y: continue
            i0, j0 = (x - a.text) // 4, (y - b.text) // 4
            k = bisect.bisect_right(sa, x); i1 = (sa[k] - a.text) // 4 if k < len(sa) else len(m)
            k = bisect.bisect_right(sb, y); j1 = (sb[k] - b.text) // 4 if k < len(sb) else len(self.B)
            trial = [-1] * len(m)
            chunk(self.A, self.B, i0, i1, j0, j1, trial)
            # only if it places more of the function than is lost, of it and of what was aligned onto its new place
            lost = {i for i in range(i0, i1) if m[i] >= 0} | {inv[j] for j in range(j0, j1) if j in inv}
            if trial[i0] != j0 or sum(t >= 0 for t in trial[i0:i1]) <= len(lost) // 2: continue
            for i in lost: inv.pop(m[i], None); m[i] = -1
            for i in range(i0, i1):
                if trial[i] >= 0: m[i] = trial[i]; inv[trial[i]] = i
            moved += 1
        return moved
    def code(self, x):
        i = (x - self.a.text) // 4
        if 0 <= i < len(self.m) and self.m[i] >= 0: return self.b.text + 4 * self.m[i] + (x & 3)
    def data(self, x):
        """(new address, how it was found) or (None, why not)."""
        a, b = self.a, self.b
        if x == a.gp: return b.gp, 'gp'
        if x in a.imports:
            stubs = [y for y, n in b.imports.items() if n == a.imports[x]]
            return (stubs[0], 'import') if stubs else (None, 'import gone')
        if x in a.got:  # a symbol's GOT slot, which a gp-relative load reads and no operand forms: the slot of its name
            slots = [y for y, n in b.got.items() if n == a.got[x]]
            return (slots[0], 'slot') if slots else (None, 'slot gone')
        c = self.votes.get(x)
        if c:
            if decisive(c) is not None: return decisive(c), 'ref'
            return None, 'conflict ' + ' '.join(f'{y:#x}x{n}' for y, n in c.most_common(2))
        for n, (addr, size, kind) in a.syms.items():
            if kind == 'OBJECT' and addr <= x < addr + max(size, 1) and n in b.syms: return b.syms[n][0] + x - addr, 'sym'
        sec = a.section(x)
        if sec is None: return None, 'not an address'
        if sec == '.rodata':
            s = a.cstr(x)
            if s and len(s) >= 6:
                _, off, size, _ = b.sec['.rodata']
                blob = b.d[off:off + size]; k = blob.find(s + b'\0')
                if k >= 0 and blob.find(s + b'\0', k + 1) < 0: return b.sec['.rodata'][0] + k, 'string'
        y = self.content(x, sec)
        if y is not None: return y, 'content'
        k = bisect.bisect_left(self.known, x)
        shifts = {decisive(self.votes[y]) - y for y in self.known[max(k - 1, 0):k + 1] if abs(y - x) <= 0x1000 and a.section(y) == sec}
        if len(shifts) == 1: return x + shifts.pop(), 'near'
        return None, 'unreferenced'
    def content(self, x, sec):
        """Where the five words around x, a word of initialised data, are in the new build's same section, the
        addresses among them carried and any that cannot be left open: the one place, or None."""
        a, b = self.a, self.b
        if x & 3 or sec not in b.sec or a.sec[sec][3] == 8: return None
        pattern, known = b'', 0
        for at in range(x - 8, x + 12, 4):
            w = a.u32(at) if a.section(at) == sec else None
            y = w
            if w is not None and a.section(w) is not None:
                y = self.code(w) if a.text <= w < a.end else decisive(self.votes[w]) if w in self.votes else None
            pattern += b'.{4}' if y is None else re.escape(struct.pack('<I', y))
            known += y not in (None, 0)
        if known < 3: return None
        addr, off, size, _ = b.sec[sec]
        found = [m.start() for m in re.finditer(b'(?=' + pattern + b')', b.d[off:off + size], re.S) if not (addr + m.start()) & 3]
        return addr + found[0] + 8 if len(found) == 1 else None
    def lookup(self, x):
        if self.a.text <= x < self.a.end:
            y = self.code(x)
            if y is None: y = self.starts.get(x)  # a function whose first word changed: where its symbol or its callers went
            return (y, 'code') if y is not None else (None, 'code changed')
        return self.data(x)
    def old(self, y):
        """The old address of new address y, or None."""
        if self.b.text <= y < self.b.end:
            i = self.back.get((y - self.b.text) // 4)
            return self.starts_back.get(y) if i is None else self.a.text + 4 * i + (y & 3)
        return self.a.gp if y == self.b.gp else self.rdata.get(y)
    def word(self, x):
        """(the new build's word for the old one at x, how): same; moved, differing only as addresses moved;
        changed, by Shanling; gone, with no counterpart."""
        y = self.lookup(x)[0]
        wa, wb = self.a.u32(x), self.b.u32(y) if y is not None else None
        if wa is None or wb is None: return None, 'gone'
        if wa == wb: return wb, 'same'
        if not self.a.text <= x < self.a.end:  # a data word: a pointer that moved with what it points at
            return (wb, 'moved') if self.lookup(wa)[0] == wb else (wb, 'changed')
        return wb, 'changed' if x in self.unexplained else 'moved'

Lit = collections.namedtuple('Lit', 'start end value digits line kind')
HEX = re.compile(r'(?<![0-9A-Za-z_])0[xX]([0-9a-fA-F]{5,8})(?=(?:[uU][lL]{0,2}|[lL]{1,2}[uU]?)?(?![0-9A-Za-z_]))')  # C's integer suffix is not the literal's
LE = re.compile(r'(?<=["\'])(?:[0-9a-f]{8}){1,3}(?=["\'])')  # instruction words as their bytes, in a quoted string
NAME = re.compile(r'[A-Za-z_]\w{2,}')
LOW, HIGH = 0x400000, 0x1000000  # where the stock executables load

def literals(text):
    """Every hex literal of 5 to 8 digits in text, and every quoted run of 4, 8 or 12 hex bytes (kind 'le',
    its value the words), in order."""
    lines = [m.start() for m in re.finditer('\n', text)]
    out = [Lit(m.start(), m.end(), int(m[1], 16), len(m[1]), bisect.bisect_left(lines, m.start()), 'hex') for m in HEX.finditer(text)]
    out += [Lit(m.start(), m.end(), struct.unpack(f'<{len(m[0]) // 8}I', bytes.fromhex(m[0])), len(m[0]), bisect.bisect_left(lines, m.start()), 'le')
            for m in LE.finditer(text)]
    return sorted(out)

def address(lit):
    """Whether lit is written as an address could be: in the executables' range, and not with a word's 8 digits."""
    return lit.kind == 'hex' and LOW <= lit.value < HIGH and lit.digits < 8

# What names a colour, and the run of numbers it is given: those are no addresses, whatever a binary has there.
COLOUR = re.compile(r'\b(?:color_t|rgba?|RGBA?|px|luma|ink|mix|blend|gradient|gr[ae]y|hue|[a-z_]*colou?rs?|[A-Z_]*(?:RGB|COLOU?R|TINT|GR[AE]Y)[A-Z_]*)'
                    r'(?:\[\w*\])?\s*(?:[(=\[{:,]|(?=0x))(?:\s|[(){}\[\],]|0[xX][0-9a-fA-F]+|\d+)*')
OFFSET = re.compile(r'[\w)\]]\s*[-+]\s*$')  # what a literal added to or taken from something has before it
MENTION = re.compile(rf"\b({'|'.join(BINARIES)})'s\b", re.I)  # a binary named as the owner of what follows
LEVELS = ('word', 'name', 'start', 'data', 'table', 'same', 'near', 'told', 'line', 'note')
STRONG, SPECIFIC, SOLID = LEVELS[:2], LEVELS[:4], LEVELS[:5]
# What a literal in a comment has before it on its line, in code; in prose every line is one.
COMMENT = re.compile(r'#(?!define|include|if|el|endif)|/\*|//|^\s*\*'), re.compile('')

def judge(text, lits, views, anchors=(), home=None, table=False, prose=False):
    """Which literals are stock addresses, and of which binary: {index into lits: (binary, how)}, how being the
    evidence. views is {binary: View}; anchors are the (binary, address, how) other files found; home is the
    binary the file is about, table whether the file is a table of its patch sites. Solid evidence, strongest first:
      word   a literal on its line or the next or the one before is the word the binary holds there
      name   a name on its line is the binary's symbol at that address, or its function holding it
      start  a function starts there
      data   the binary's code forms that address, or a symbol starts there
      table  the file is a table of the binary's sites, and it is word-aligned in its code, or in its data
    Then, for what is left: same, the same value has solid evidence elsewhere. And last, weak evidence, for
    a literal word-aligned in the binary's code:
      near   it is within 0x40 bytes of an address with solid evidence, or inside a function that holds one
      told   a file about the binary judged the value an address
      line   every literal on its line, two or more, is word-aligned in the binary's code
      note   it is in a comment
    Past 0x40 bytes the last three count only for the file's binaries: home, and any with three solid
    literals in the file. Of two binaries with weak evidence home has it, then the only one named as an owner
    ("demo's") in the 60 characters before the literal, then one a file about it judged the value for, then the file's. A
    literal two binaries still claim alike goes to home, else to the binary of the nearest literal with solid
    evidence within 15 lines, else to the one with more of the file's solid evidence; a tie is ('?', how),
    listed and never rewritten. Only word and name count for a number a colour's name is given, for one
    added to or taken from something (an offset), for a multiple of 0x10000 (a page, a size) or, outside
    prose, on a line of four or more literals most of which nothing claims: a colour can sit where a
    function starts."""
    rows = text.split('\n')
    starts = [0]
    for row in rows: starts.append(starts[-1] + len(row) + 1)
    by_line = collections.defaultdict(list)
    for n, lit in enumerate(lits):
        if address(lit): by_line[lit.line].append(n)
    beside = collections.defaultdict(set)  # line: the words on it
    for lit in lits:
        beside[lit.line] |= {lit.value} if lit.kind == 'hex' else set(lit.value[:1])
    found = {}  # index: {binary: level}
    for line, ns in by_line.items():
        names = set(NAME.findall(rows[line]))
        for n in ns:
            x = lits[n].value
            words = (beside[line - 1] | beside[line] | beside[line + 1]) - {x, 0, 0xffffffff}
            for binary, view in views.items():
                elf = view.elf
                if elf.section(x) is None: continue
                code = elf.text <= x < elf.end
                if elf.u32(x) in words: level = 0
                elif any(s[0] == x or (s[2] == 'FUNC' and s[0] < x < s[0] + s[1]) for s in map(elf.syms.get, names) if s): level = 1
                elif x in view.starts: level = 2
                elif not code and view.data(x): level = 3
                elif table and binary == home and (not x & 3 if code else elf.section(x) in DATA): level = 4
                else: continue
                found.setdefault(n, {})[binary] = level
    found = {n: {b: level for b, level in claims.items() if level == min(claims.values())} for n, claims in found.items()}
    weight = collections.Counter(b for claims in found.values() for b in claims)
    ours = {b for b in views if b == home or weight[b] >= 3}
    out = {}
    def settle(n, claims):
        claims = sorted(claims, key=lambda b: (-weight[b], b))
        if len(claims) == 1: return claims[0]
        if home in claims: return home
        by = min((((abs(lits[k].line - lits[n].line), abs(lits[k].start - lits[n].start)), v[0]) for k, v in out.items()
                  if v[1] in SOLID and v[0] in claims and abs(lits[k].line - lits[n].line) <= 15), default=None)
        return by[1] if by else claims[0] if weight[claims[0]] > weight[claims[1]] else '?'
    for n, claims in sorted(found.items(), key=lambda item: len(item[1])):
        out[n] = (settle(n, claims), LEVELS[min(claims.values())])
    solid, weak = collections.defaultdict(set), collections.defaultdict(set)
    for binary, x, how in anchors: (solid if how in SOLID else weak)[binary].add(x)
    for n, (binary, how) in out.items(): solid[binary].add(lits[n].value)
    spots = {b: sorted(solid[b]) for b in views}
    functions = {b: {views[b].function(x) for x in solid[b]} - {None} for b in views}
    def held(b, x): return not x & 3 and views[b].function(x) is not None
    def close(b, x):
        k = bisect.bisect_left(spots[b], x)
        return any(abs(y - x) <= 0x40 for y in spots[b][max(k - 1, 0):k + 1])
    later = {}
    for line, ns in by_line.items():
        values = {lits[n].value for n in ns}
        for n in ns:
            if n in out: continue
            x, scores = lits[n].value, {}
            before = rows[line][:lits[n].start - starts[line]]
            said = {name.lower() for name in MENTION.findall(before[-60:])}
            for b in views:
                if x in solid[b]: scores[b] = (0, 'same'); continue
                if not held(b, x): continue
                how = 'near' if close(b, x) else 'told' if x in weak[b] else None
                if how is None and b in ours:
                    how = ('near' if views[b].function(x) in functions[b] else 'line' if len(values) >= 2 and all(held(b, v) for v in values)
                           else 'note' if COMMENT[prose].search(before) else None)
                if how: scores[b] = (1 if b == home else 2 if said == {b} else 3 if x in weak[b] else 4 if b in ours else 5, how)
            if scores:
                best = min(score for score, _ in scores.values())
                binary = settle(n, [b for b, (score, _) in scores.items() if score == best])
                later[n] = (binary, scores[binary][1] if binary != '?' else 'told')
    out.update(later)
    for line, ns in by_line.items():
        spans = [(starts[line] + m.start(), starts[line] + m.end()) for m in COLOUR.finditer(rows[line])]
        many = not prose and len(ns) >= 4 and 2 * sum(n in out for n in ns) < len(ns)
        for n in ns:
            if n in out and out[n][1] not in STRONG and (many or not lits[n].value & 0xffff or any(a <= lits[n].start < b for a, b in spans)
                                                         or OFFSET.search(rows[line][:lits[n].start - starts[line]])): del out[n]
    return out

DATA = ('.rodata', '.data', '.data.rel.ro', '.bss', '.sbss', '.got')
# The binary a file is about, first match; in a table of patch sites every literal in the binary's range is its address.
HOMES = ((r'tools/(peq|flac)\.py', 'hciplayer', True), (r'tools/(build|ipod)\.py|patch/ipod\.json', 'demo', True),
         (r'patch/(peq_player|mp3_gapless|lavf_gapless|flac_\w+)\.c', 'hciplayer', False), (r'tools/bt_\w+\.S', 'bluealsa', False),
         (r'patch/', 'demo', False))

def about(rel):
    """judge's home, table and prose for the file rel."""
    home, table = next(((b, t) for pattern, b, t in HOMES if re.match(pattern, rel)), (None, False))
    return dict(home=home, table=table, prose=rel.endswith('.md'))

def judge_tree(texts, views):
    """{file: (its literals, their verdicts)} for texts, {file: text}: each file judged alone, then again
    with what is evident in every file."""
    lits = {rel: literals(text) for rel, text in texts.items()}
    alone = {rel: judge(text, lits[rel], views, **about(rel)) for rel, text in texts.items()}
    anchors = {(b, lits[rel][n].value, how) for rel, verdict in alone.items() for n, (b, how) in verdict.items()
               if how in SOLID and b != '?' or b == about(rel)['home']}  # weak evidence only from a file about that binary
    out = {}
    for rel, text in texts.items():
        verdict = judge(text, lits[rel], views, anchors, **about(rel))
        out[rel] = lits[rel], {n: alone[rel][n] if alone[rel].get(n, ('',))[0] == v[0] else v for n, v in verdict.items()}  # the file's own evidence, where it had some
    return out

def rewrite(text, lits, verdict, maps):
    """text with its stock addresses (lits as judge's verdict has them) and the stock words beside them
    carried from the old build to the new: (new text, notes), notes being (line, literal, what happened)
    for everything not simply carried."""
    notes = []
    # A (file offset, size) pair that is a section of an old build is that section of the new, whatever
    # address the offset looks like: PDR in tools/build.py.
    pairs = {}
    for n, (one, two) in enumerate(zip(lits, lits[1:])):
        for mp in maps.values():
            for name, (_, off, size, _) in mp.a.sec.items():
                if off and size and one.kind == two.kind == 'hex' and (one.value, two.value) == (off, size) and one.line == two.line and name in mp.b.sec:
                    pairs[n], pairs[n + 1] = mp.b.sec[name][1:3]
    edits = [(lits[n].start, lits[n].end, f'0x{v:0{lits[n].digits}x}') for n, v in pairs.items()]
    evident = []  # (binary, address, line)
    for n, (binary, how) in sorted(verdict.items()):
        if n in pairs: continue
        lit = lits[n]
        if binary == '?':
            notes.append((lit.line + 1, f'{lit.value:#x}', f'ambiguous: {how} in more than one binary')); continue
        evident.append((binary, lit.value, lit.line))
        y, why = maps[binary].lookup(lit.value)
        if y is None: notes.append((lit.line + 1, f'{lit.value:#x}', f'{binary}: not carried, {why} ({maps[binary].a.where(lit.value)})'))
        elif y != lit.value:
            edits.append((lit.start, lit.end, f'0x{y:0{lit.digits}x}'))
            if why == 'near': notes.append((lit.line + 1, f'{lit.value:#x}', f'{binary}: carried to {y:#x} by its neighbours\' shift, a guess'))
    # A word literal is the stock word at, or up to 5 words either side of, the nearest evident address that holds it.
    for n, lit in enumerate(lits):
        if n in verdict or n in pairs or (lit.kind == 'hex' and lit.digits != 8): continue
        words: tuple[int, ...] = (lit.value,) if lit.kind == 'hex' else lit.value
        best = None
        if len(words) == 3 and words[0] >> 16 == 0x3c1c and words[1] >> 16 == 0x279c:  # a PIC prologue says where it is: how far the GOT is
            for binary, mp in maps.items():
                x = mp.a.gp - ((words[0] & 0xffff) << 16) - s16(words[1] & 0xffff)
                if all(mp.a.u32(x + 4 * k) == w for k, w in enumerate(words)): best = ((-1, 0), binary, x)
        for binary, x, line in evident:
            elf = maps[binary].a
            for d in range(-20, 24, 4):
                if all(elf.u32(x + d + 4 * k) == w for k, w in enumerate(words)):
                    rank = (abs(line - lit.line), abs(d))
                    if best is None or rank < best[0]: best = (rank, binary, x + d)
        if best is None: continue
        _, binary, x = best
        new = [maps[binary].word(x + 4 * k) for k in range(len(words))]
        if all(how == 'same' for _, how in new): continue
        if all(how in ('same', 'moved') for _, how in new):
            edits.append((lit.start, lit.end, struct.pack(f'<{len(new)}I', *(w for w, _ in new)).hex() if lit.kind == 'le' else f'0x{new[0][0]:08x}'))
        else:
            bad = next(k for k, (_, how) in enumerate(new) if how not in ('same', 'moved'))
            was = maps[binary].a.where(x + 4 * bad)
            notes.append((lit.line + 1, f'{words[bad]:#010x}', f'{binary}: the stock word at {x + 4 * bad:#x} ({was}) {new[bad][1]}'
                          + (f', now {new[bad][0]:#010x}' if new[bad][0] is not None else '')))
    for start, end, new in sorted(edits, reverse=True): text = text[:start] + new + text[end:]
    return text, notes

def stale(texts, maps, views_old, views_new):
    """The literals of texts, {file: text}, still on the old build's layout: [(file, line, literal, binary, how)].
    One is stale if the old build makes it an address by evidence that depends on the layout, or it is where
    an address the tree now holds on such evidence came from, and the new build gives it no such evidence.
    A new address can sit where an old function started, so for all but a word's or a name's evidence it
    must also be one the map does not carry, or have nothing at all for it in the new build. A literal with
    only a table's, a line's or a comment's word for it reads the same on both layouts and is not seen."""
    lits = {rel: literals(text) for rel, text in texts.items()}
    kw = lambda rel: {**about(rel), 'table': False}
    def judged(views, anchors):
        return {rel: {n: v for n, v in judge(text, lits[rel], views, anchors, **kw(rel)).items() if v[1] in SPECIFIC + ('same',) and v[0] != '?'}
                for rel, text in texts.items()}
    spread = lambda verdicts: {(b, lits[rel][n].value, how) for rel, verdict in verdicts.items() for n, (b, how) in verdict.items()}
    new = judged(views_new, spread(judged(views_new, ())))
    came = {(b, maps[b].old(y), 'start') for b, y, _ in spread(new)}
    old = judged(views_old, {c for c in came if c[1] is not None} | spread(judged(views_old, ())))
    loose = judge_tree(texts, views_new)
    return [(rel, lits[rel][n].line + 1, f'{lits[rel][n].value:#x}', b, how) for rel in texts for n, (b, how) in sorted(old[rel].items())
            if n not in new[rel] and maps[b].a.d != maps[b].b.d
            and (how in STRONG or maps[b].lookup(lits[rel][n].value)[0] is None or n not in loose[rel][1])]

def read_stock_map(text):
    """The stock map: the stock version its addresses are of, which its first line names, then
    {binary: [(address, size, name)]} and the rows whole, in file order, from under the line of column names."""
    lines = text.splitlines()
    version = re.findall(r'V\d+(?:\.\d+)+', lines[0])
    check(version, 'stock map: the first line names no stock version')
    rows = [line.split('\t') for line in lines[2:] if line.strip()]
    for row in rows: check(len(row) == 5, f'stock map: {row!r} is not binary, address, size, name, what')
    names = collections.defaultdict(list)
    for binary, address, size, name, _ in rows: names[binary].append((int(address, 16), int(size, 16), name))
    return version[0], names, rows

def carry_stock_map(text, maps):
    """The stock map with each row at its new address and size, and notes on the rows not carried or changed."""
    _, _, rows = read_stock_map(text)
    out, notes = text.splitlines()[:2], []
    for binary, address, size, name, what in rows:
        mp = maps[binary]
        x, z = int(address, 16), int(size, 16)
        y, why = mp.lookup(x)
        if y is None:
            notes.append((name, f'{binary}: not carried from {x:#x}, {why}'))
            out.append('\t'.join((binary, address, size, name, what))); continue
        if mp.a.text <= x < mp.a.end and z:
            end = mp.code(x + z - 4)
            gone = sum(mp.code(x + o) is None for o in range(0, z, 4))
            changed = sum(x + o in mp.unexplained for o in range(0, z, 4))
            if gone or changed or end is None: notes.append((name, f'{binary}: changed, {gone} words gone and {changed} changed of {z // 4}'))
            if end is not None and end >= y: z = end + 4 - y
        out.append('\t'.join((binary, f'{y:#x}', f'{z:#x}', name, what)))
    return '\n'.join(out) + '\n', notes

def function_diff(a, b):
    """The exported functions of two builds by name: (identical once addresses are masked, [(name, old size,
    new size)] changed, names only in the old, names only in the new)."""
    fa = {n: s for n, s in a.syms.items() if s[2] == 'FUNC' and s[1] and a.text <= s[0] < a.end}
    fb = {n: s for n, s in b.syms.items() if s[2] == 'FUNC' and s[1] and b.text <= s[0] < b.end}
    body = lambda e, s: [heavy(w) for w in e.words[(s[0] - e.text) // 4:(s[0] - e.text + s[1]) // 4]]
    changed = [(n, fa[n][1], fb[n][1]) for n in sorted(fa.keys() & fb.keys()) if body(a, fa[n]) != body(b, fb[n])]
    return len(fa.keys() & fb.keys()) - len(changed), changed, sorted(fa.keys() - fb.keys()), sorted(fb.keys() - fa.keys())

def gaps(m, base, least=2):
    """The runs of at least `least` unaligned words in m: [(address, words)]."""
    out, start = [], None
    for i, j in enumerate([*m, 0]):
        if j < 0 and start is None: start = i
        elif j >= 0 and start is not None:
            if i - start >= least: out.append((base + 4 * start, i - start))
            start = None
    return out

def places(mp, addresses):
    """Our code addresses that sit in a function, or the statics after one, that changed:
    {(name, 'body' or 'statics after'): [(old address, new address or None)]}."""
    a, m = mp.a, mp.m
    starts = sorted({s for s, _ in a.starts if a.text <= s < a.end})
    rows = collections.defaultdict(list)
    for x in sorted(set(addresses)):
        if not a.text <= x < a.end: continue
        k = bisect.bisect_right(starts, x) - 1
        if k < 0: continue
        s = starts[k]; e = starts[k + 1] if k + 1 < len(starts) else a.end
        name = min(n for st, n in a.starts if st == s)
        body = x < s + a.syms[name][1]
        lo, hi = (s, s + a.syms[name][1]) if body else (s + a.syms[name][1], e)
        if mp.code(x) is None or any(m[i] < 0 or a.text + 4 * i in mp.unexplained for i in range((lo - a.text) // 4, (hi - a.text) // 4)):
            rows[(name, 'body' if body else 'statics after')].append((x, mp.code(x)))
    return rows

class Stock:
    """A stock ZIP, opened: its hash, the version it names, and its files by rootfs path."""
    def __init__(self, zip_path, work):  # pragma: no mutate block
        raw = pathlib.Path(zip_path).read_bytes()
        self.sha = hashlib.sha256(raw).hexdigest()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            members = [n for n in z.namelist() if n.endswith('/update.tar')]
            check(len(members) == 1, f'{zip_path}: no single update.tar')
            self.member = members[0]
            tar = z.read(self.member)
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            files = ((m.name, t.extractfile(m)) for m in t.getmembers())
            self.blobs = {name: f.read() for name, f in files if f}
        self.version = self.blobs['firmware_v20.info'].decode().splitlines()[1]
        # ponytail: a real stock ZIP named otherwise (a 'V1.36b' version, a '(final)' folder) is refused until these patterns are widened
        check(re.fullmatch(r'V[0-9]+(?:\.[0-9]+)+', self.version), f'{zip_path}: version {self.version!r} is no stock version (as V1.36 is)')
        check(re.fullmatch(r'[A-Za-z0-9 ._-]+/update\.tar', self.member), f'{zip_path}: folder {self.member!r} is no update.tar in a plainly named folder')
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix=self.sha[:12] + '-', dir=work))
        self.squashfs = self.dir/'rootfs.squashfs'
        self.squashfs.write_bytes(self.blobs['recovery-update/rootfs.squashfs'])
        self.root = None
    def cat(self, path):  # pragma: no mutate block
        return subprocess.check_output(['unsquashfs', '-cat', str(self.squashfs), path])
    def tree(self):  # pragma: no mutate block
        """The whole rootfs, extracted once: its directory."""
        if self.root is None:
            self.root = self.dir/'root'
            subprocess.run(['unsquashfs', '-no-xattrs', '-n', '-d', str(self.root), str(self.squashfs)], check=True, capture_output=True)
        return self.root
    def hashes(self):  # pragma: no mutate block
        """{sha256: what} of the ZIP, the kernel, the rootfs image and every file in it."""
        out = {self.sha: 'the ZIP', **{hashlib.sha256(b).hexdigest(): n for n, b in self.blobs.items()}}
        root = self.tree()
        for p in sorted(root.rglob('*')):
            if p.is_file() and not p.is_symlink(): out.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), str(p.relative_to(root)))
        return out

def open_maps(old, new):  # pragma: no mutate block
    """{binary: Map} for two opened ZIPs, each side with the stock map's names: the side the map's first line
    names has them as written, the other where the Map puts them."""
    version, names, _ = read_stock_map(STOCK_MAP.read_text()) if STOCK_MAP.exists() else (None, {}, [])
    maps = {}
    for binary, path in BINARIES.items():
        mp = maps[binary] = Map(Elf(old.cat(path)), Elf(new.cat(path)))
        rows = names.get(binary, [])
        if version == new.version != old.version:
            mp.b.named(rows)
            mp.a.named([(mp.old(y), size, name) for y, size, name in rows if mp.old(y) is not None])
        else:
            mp.a.named(rows)
            mp.b.named([(mp.lookup(x)[0], size, name) for x, size, name in rows if mp.lookup(x)[0] is not None])
    return maps

def tracked():  # pragma: no mutate block
    """The files this tool reads and rewrites: git's, under SCANNED, that are text."""
    out = []
    for rel in subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z', '--', *SCANNED], text=True).split('\0'):
        if not rel or rel in SKIPPED or (ROOT/rel).is_symlink(): continue
        try: (ROOT/rel).read_text()
        except (UnicodeDecodeError, FileNotFoundError): continue
        out.append(rel)
    return out

def pins(text, old, new, hashes):
    """text with the old ZIP's pins as the new one's: each SHA-256 of a stock file that has a new one, the
    version, and the ZIP's member path. Returns (new text, notes)."""
    notes = []
    def swap(m):
        what = hashes[0].get(m[0])
        if what is None: return m[0]
        now = [h for h, w in hashes[1].items() if w == what]
        if not now: notes.append((text.count('\n', 0, m.start()) + 1, m[0][:12], f'{what} is not in the new firmware')); return m[0]
        return now[0]
    text = re.sub(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])', swap, text)
    if old.version != new.version:
        text = text.replace(old.member, new.member)
        text = re.sub(rf'(?<![0-9A-Za-z.]){re.escape(old.version)}(?![0-9])', new.version, text)
    return text, notes

def read_tree():  # pragma: no mutate block
    return {rel: (ROOT/rel).read_text() for rel in tracked()}

def apply(old, new, write=True, out=sys.stdout):  # pragma: no mutate block
    """Rewrite every tracked file; returns how many notes it left."""
    maps = open_maps(old, new)
    hashes = (old.hashes(), new.hashes())
    texts = read_tree()
    judged = judge_tree({rel: text for rel, text in texts.items() if rel not in TABLES}, {b: view(mp.a) for b, mp in maps.items()})
    failed = changed = 0
    for rel, text in texts.items():
        if rel in TABLES:
            now, notes = carry_stock_map(text, maps)
            now, notes = pins(now, old, new, hashes)[0], [(0, name, what) for name, what in notes]
        else:
            lits, verdict = judged[rel]
            now, notes = rewrite(text, lits, verdict, maps)
            now, more = pins(now, old, new, hashes)
            notes += more
        for line, literal, what in sorted(notes): print(f'{rel}:{line}: {literal} {what}', file=out)
        failed += len(notes)
        if now != text:
            changed += 1
            if write: (ROOT/rel).write_text(now)
    print(f'{changed} files {"rewritten" if write else "would change"}, {failed} notes', file=out)
    return failed

def check_tree(old, new, out=sys.stdout):  # pragma: no mutate block
    """Print each literal still on the old layout; returns how many."""
    maps = open_maps(old, new)
    texts = read_tree()
    tables = {rel: read_stock_map(texts.pop(rel)) for rel in TABLES if rel in texts}
    count = 0
    for rel, (version, _, _) in tables.items():
        if version != new.version: print(f'{rel}: its addresses are {version}\'s', file=out); count += 1
    for rel, line, literal, binary, how in stale(texts, maps, {b: view(mp.a) for b, mp in maps.items()}, {b: view(mp.b) for b, mp in maps.items()}):
        print(f'{rel}:{line}: {literal} is {binary}\'s in {old.version} ({how}: {maps[binary].a.where(int(literal, 16))})', file=out)
        count += 1
    for rel, (_, names, _) in tables.items():
        for binary, rows in names.items():
            elf = maps[binary].b
            for x, size, name in rows:
                if elf.section(x) is None or (elf.text <= x < elf.end and x & 3):
                    print(f'{rel}: {name} at {x:#x} is not in {new.version}\'s {binary}', file=out); count += 1
    print(f'{count} stock addresses left on {old.version}\'s layout' if count else f'No stock address left on {old.version}\'s layout', file=out)
    return count

def kernel(blob):  # pragma: no mutate block
    """The kernel in an xImage, unpacked: gzip at 0x4260."""
    return gzip.GzipFile(fileobj=io.BytesIO(blob[0x4260:])).read()

def strings(blob, least=8):
    """The printable ASCII runs of at least `least` characters in blob."""
    return set(m[0].decode() for m in re.finditer(rb'[\x20-\x7e]{%d,}' % least, blob))

def config_keys(text):
    """config.ini's keys, as SECTION KEY."""
    out, section = [], ''
    for line in text.splitlines():
        if line.startswith('['): section = line.strip('[] \r')
        elif '=' in line: out.append(f'{section} {line.split("=")[0].strip()}')
    return out

def report(old, new, out=sys.stdout):  # pragma: no mutate block
    p = lambda *a: print(*a, file=out)
    p(f'# {old.version} ({old.sha[:12]}) -> {new.version} ({new.sha[:12]})\n')
    ra, rb = old.tree(), new.tree()
    fa = {str(f.relative_to(ra)) for f in ra.rglob('*') if f.is_file() or f.is_symlink()}
    fb = {str(f.relative_to(rb)) for f in rb.rglob('*') if f.is_file() or f.is_symlink()}
    same = lambda f: (ra/f).is_symlink() == (rb/f).is_symlink() and ((ra/f).readlink() == (rb/f).readlink() if (ra/f).is_symlink() else (ra/f).read_bytes() == (rb/f).read_bytes())
    differ = sorted(f for f in fa & fb if not same(f))
    p(f'## Files: {len(fb - fa)} new, {len(fa - fb)} gone, {len(differ)} changed')
    for f in sorted(fa - fb): p(f'- {f}')
    for f in sorted(fb - fa): p(f'+ {f}')
    for f in differ: p(f'~ {f}')
    ka, kb = kernel(old.blobs['recovery-update/xImage']), kernel(new.blobs['recovery-update/xImage'])
    version = lambda k: next((s for s in strings(k, 20) if s.startswith('Linux version')), '?')
    p(f'\n## Kernel: {"unchanged" if ka == kb else "changed"}\n- {version(ka)}\n+ {version(kb)}')
    if ka != kb:
        sa, sb = strings(ka), strings(kb)
        p(f'strings: {len(sa - sb)} gone, {len(sb - sa)} new')
        for s in sorted(sb - sa)[:400]: p(f'+ {s}')
        for s in sorted(sa - sb)[:400]: p(f'- {s}')
    config = 'release/assets/default/raw/config.ini'
    ca, cb = (ra/config).read_text(errors='replace'), (rb/config).read_text(errors='replace')
    p('\n## config.ini keys')
    for k in sorted(set(config_keys(cb)) - set(config_keys(ca))): p(f'+ {k}')
    for k in sorted(set(config_keys(ca)) - set(config_keys(cb))): p(f'- {k}')
    for line in difflib.unified_diff(ca.splitlines(), cb.splitlines(), old.version, new.version, lineterm='', n=0): p(line)
    hashes = old.hashes()
    pinned = collections.defaultdict(list)
    for rel in tracked():
        for h in re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])', (ROOT/rel).read_text()):
            if h in hashes: pinned[hashes[h]].append(rel)
    p('\n## Stock files we pin by SHA-256 that changed')
    for what, rels in sorted(pinned.items()):
        if what in differ or what not in fa: p(f'~ {what}: {", ".join(sorted(set(rels)))}')
    maps = open_maps(old, new)
    views = {b: view(mp.a) for b, mp in maps.items()}
    texts = {rel: text for rel, text in read_tree().items() if rel not in TABLES}
    ours = collections.defaultdict(lambda: collections.defaultdict(set))  # binary: address: files
    tally, bare, weak = collections.Counter(), [], []
    for rel, (lits, verdict) in judge_tree(texts, views).items():
        rows = texts[rel].split('\n')
        for n, (binary, how) in verdict.items():
            tally[(rel.split('/')[0], binary, how)] += 1
            if binary != '?': ours[binary][lits[n].value].add(rel)
            if how not in SOLID and not about(rel)['prose']:
                weak.append(f'{rel}:{lits[n].line + 1}: {lits[n].value:#x} {binary} {how} | {rows[lits[n].line].strip()[:120]}')
        bare += [f'{rel}:{lit.line + 1}: {lit.value:#x} | {rows[lit.line].strip()[:120]}' for n, lit in enumerate(lits)
                 if n not in verdict and address(lit) and not lit.value & 3 and any(v.function(lit.value) is not None for v in views.values())]
    for binary, mp in maps.items():
        a, b = mp.a, mp.b
        p(f'\n## {binary}: {"unchanged" if a.d == b.d else "changed"}')
        if a.d == b.d: continue
        hit = sum(j >= 0 for j in mp.m)
        identical, changed, gone, added = function_diff(a, b)
        p(f'.text: {len(mp.m)} words, {hit} aligned ({hit / len(mp.m):.2%}); new build {len(b.words)} words, {len(b.words) - hit} new or changed')
        p(f'exported functions: {identical + len(changed)} in both, {identical} identical once addresses are masked, {len(changed)} changed '
          f'({sum(x != y for _, x, y in changed)} in size), {len(gone)} gone, {len(added)} new')
        p(f'anchors out of order: {mp.dropped}; functions aligned again from their callers: {mp.moved}')
        disagree = [(x, decisive(c)) for x, c in sorted(mp.calls.items()) if decisive(c) is not None and mp.lookup(x)[0] != decisive(c)]
        p(f'call targets: {len(mp.calls)} pairs, {len(disagree)} disagree with the alignment')
        for x, y in disagree: p(f'  {x:#x} ({a.where(x)}) is called as {y:#x} ({b.where(y)}), aligned to {mp.lookup(x)[0] and hex(mp.lookup(x)[0])}')
        p(f'addresses formed by operands: {len(mp.votes)} ({sum(decisive(c) is None for c in mp.votes.values())} with rival targets); '
          f'words changed for another reason: {len(mp.unexplained)}')
        p('\n### Changed exported functions')
        for n, x, y in changed: p(f'  {x:6d} -> {y:6d} ({y - x:+5d})  {n}')
        p('\n### New exported functions\n  ' + ' '.join(added) + '\n\n### Gone\n  ' + ' '.join(gone))
        p('\n### Old code with no counterpart')
        for x, n in gaps(mp.m, a.text): p(f'  {x:#x} {4 * n:6d} bytes  {a.where(x)}')
        used = set(mp.m)
        p('\n### New code with no counterpart')
        for y, n in gaps([0 if j in used else -1 for j in range(len(b.words))], b.text): p(f'  {y:#x} {4 * n:6d} bytes  {b.where(y)}')
        code = {x: f for x, f in ours[binary].items() if a.text <= x < a.end}
        carried = sum(mp.code(x) is not None for x in code)
        p(f'\n### Our addresses: {len(ours[binary])}, {len(code)} in code, {carried} carried, {len(code) - carried} in code Shanling rewrote')
        rows = places(mp, code)
        p(f'in functions or the statics after them that changed: {sum(map(len, rows.values()))} in {len(rows)} places')
        for (name, kind), v in sorted(rows.items()):
            p(f'{name} [{kind}]')
            for x, y in v: p(f'    {x:#x} -> {f"{y:#x}" if y else "SITE REWRITTEN"}  {", ".join(sorted(f.split("/")[-1] for f in code[x]))}')
        p('\n### Our data addresses not carried')
        for x, files in sorted(ours[binary].items()):
            if not a.text <= x < a.end and mp.lookup(x)[0] is None: p(f'  {x:#x} {mp.lookup(x)[1]} ({a.where(x)})  {", ".join(sorted(f.split("/")[-1] for f in files))}')
    p('\n## Literals by evidence (folder, binary, how)')
    for key, n in sorted(tally.items()): p(f'{n:6d}  {" ".join(key)}')
    p(f'\n## Judged on weak evidence outside the docs: {len(weak)}. Read these: a colour can sit in a binary\'s code')
    for line in weak: p('  ' + line)
    p(f'\n## Bare literals: {len(bare)} word-aligned values inside a .text with nothing to say they are addresses; never rewritten')
    for line in bare: p('  ' + line)
    if STOCK_MAP.exists():
        _, notes = carry_stock_map(STOCK_MAP.read_text(), maps)
        p(f'\n## Stock map: {len(notes)} named functions changed or not carried')
        for name, what in notes: p(f'  {name}: {what}')
    if STOCK_SITES.exists():
        lost = [row for row in read_stock_map(STOCK_SITES.read_text())[2] if maps[row[0]].lookup(int(row[1], 16))[0] is None]
        p(f'\n## Patch sites to find by hand: {len(lost)} (binary, address, the function it was in, where in it and what uses it)')
        for row in lost: p('  ' + '  '.join((row[0], row[1], *row[3:])))

def refs(elf, what, out=sys.stdout):
    """Print where elf's code forms the address what, loads it from the GOT, calls it, or loads a string holding what (text)."""
    votes, calls, sites, callsites, _ = track(elf, elf, list(range(len(elf.words))))
    if isinstance(what, str):
        rodata, off, size, _ = elf.sec['.rodata']
        blob = elf.d[off:off + size]
        targets = {}
        for m in re.finditer(re.escape(what.encode()), blob):
            start = blob.rfind(b'\0', 0, m.start()) + 1
            targets[rodata + start] = blob[start:blob.index(b'\0', start)].decode('utf-8', 'replace')
    else: targets = {what: elf.imports.get(what) or elf.where(what)}
    got, _, size, _ = elf.sec['.got']
    # lw off($gp): of any slot for an address, as demo calls its own functions through local ones; of a symbol's slot
    # for a text, whose page sits in a local slot that is no use of the text
    held = elf.got if isinstance(what, str) else range(got, got + size)
    loads = {elf.text + 4 * i: (elf.u32(elf.gp + s16(w & 0xffff)), 0) for i, w in enumerate(elf.words)
             if w >> 21 == 0x47c and elf.gp + s16(w & 0xffff) in held}
    for pc, (x, _) in sorted({**loads, **sites, **callsites}.items()):
        if x in targets: print(f'{pc:#x}  {elf.where(pc)}  ->  {x:#x} {targets[x]!r}', file=out)
    for x in sorted(targets):
        if x not in votes and x not in calls and all(x != y for y, _ in loads.values()): print(f'{x:#x} {targets[x]!r}: no code names it', file=out)

def main(argv=None):  # pragma: no mutate block
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('report', 'apply', 'check', 'refs'))
    ap.add_argument('args', nargs='+', metavar='ZIP', help='OLD.zip NEW.zip, or for refs: ZIP BINARY and addresses or strings')
    ap.add_argument('--dry-run', action='store_true', help='apply: print what would change and write nothing')
    ap.add_argument('--work', type=pathlib.Path, default=pathlib.Path(tempfile.gettempdir()), help='where to extract (default: the temporary directory)')
    a = ap.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix='q2-rebase-', dir=a.work) as work:
            check(ROOT not in pathlib.Path(work).resolve().parents, 'Extract outside the repo')
            if a.command == 'refs':
                check(len(a.args) >= 3 and a.args[1] in BINARIES, 'refs ZIP BINARY WHAT...')
                elf = Elf(Stock(a.args[0], work).cat(BINARIES[a.args[1]]))
                if STOCK_MAP.exists(): elf.named(read_stock_map(STOCK_MAP.read_text())[1].get(a.args[1], ()))
                for what in a.args[2:]: refs(elf, int(what, 16) if re.fullmatch('0x[0-9a-fA-F]+', what) else what)
                return 0
            check(len(a.args) == 2, f'{a.command} OLD.zip NEW.zip')
            old, new = Stock(a.args[0], work), Stock(a.args[1], work)
            if a.command == 'report': report(old, new); return 0
            if a.command == 'apply': apply(old, new, not a.dry_run); return 0
            return 1 if check_tree(old, new) else 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        ap.error(str(exc))

if __name__ == '__main__':
    sys.exit(main())
