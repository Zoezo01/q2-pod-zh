#!/usr/bin/env python3
"""Write tools/stock_sites.tsv: a line for every stock code address patch/ and tools/ name that is no function's
start, saying how to find it again once Shanling recompile: the function it is in, what the instruction is (a call
by its callee and which of the function's calls to it; anything else by its text and which of its kind), the last
string loaded before it, the next call after it, and what of ours uses it (where the reason for the patch is).

    python3 -I docs/rebasing/sitenotes.py STOCK.zip [--check]

STOCK.zip is the stock firmware the tree is on. --check writes nothing and fails if the table is not current.
A note is only of use written while the site is still known: run this when a rebase is done, not when the next
one starts. tools/rebase.py apply then carries the table, and a row it cannot carry reads how to find the site.
"""
import bisect, pathlib, re, subprocess, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
import rebase

SITES = rebase.STOCK_SITES
HEAD = ("# How to find each stock code address our files name again, at stock {}'s addresses. docs/rebasing.md\n"
        'binary\taddress\tsize\tfunction\twhere in it; what uses it\n')
# Numbers in a binary's code range that are no address, by what they belong to.
NOT_SITES = {'tools/build.py PDR'}  # a file offset
# A line that names what a literal belongs to, by the file's kind: a constant, a function, a JSON table.
OWNER = {'.py': r'^([A-Za-z_]\w*(?:, \w+)*)\s*=|^def (\w+)', '.json': r'^  "(\w+)":',
         '': r'#define\s+(\w+)|^(?:PROVIDE\()?\s*(\w+)\s*=\s*0x|^(\w+):|^[A-Za-z_][\w\s\*]*?\b(\w+)\([^;]*$'}
COMMENT = re.compile(r'\s*(#(?!define|include|if|el|endif)|//|/\*|\*)')

def nth(i, n): return f'the only' if n == 1 else f'{i}{"th" if 10 < i % 100 < 14 else {1: "st", 2: "nd", 3: "rd"}.get(i % 10, "th")} of {n}'

class Binary:
    def __init__(self, name, blob, rows, work):
        self.name, self.e = name, rebase.Elf(blob)
        self.e.named(rows)
        self.v = rebase.view(self.e)
        _, _, self.sites, self.callsites, _ = rebase.track(self.e, self.e, list(range(len(self.e.words))))
        self.path = pathlib.Path(work)/name
        self.path.write_bytes(blob)
        self.text = {}
    def label(self, a):
        e = self.e
        if a in e.imports: return e.imports[a]
        w = e.where(a)
        return w if '+' not in w and not w.startswith('0x') else f'sub_{a:x}'
    def function(self, y):
        """(name, start, end) of the function holding y."""
        w = self.e.where(y)
        if '(past)' not in w and not w.startswith('0x'):
            n = w.split('+')[0]
            return n, self.e.syms[n][0], sum(self.e.syms[n][:2])
        s = self.v.function(y)
        i = bisect.bisect_right(self.v.sorted, y)
        return f'sub_{s:x}', s, self.v.sorted[i] if i < len(self.v.sorted) else self.e.end
    def callee(self, pc, lo):
        """What the call at pc calls: a direct call's target, or what its jalr's register was loaded with."""
        e = self.e
        if pc in self.callsites: return self.label(self.callsites[pc][0])
        w = e.u32(pc)
        if w >> 26 or w & 0x3f != 9: return None
        rs, p = (w >> 21) & 31, pc - 4
        while p >= lo and pc - p <= 96:
            v = e.u32(p); op = v >> 26
            if op in (0x23, 9) and (v >> 16) & 31 == rs:
                if p in self.sites: return self.label(self.sites[p][0])
                slot = e.gp + rebase.s16(v & 0xffff)
                return (e.got.get(slot) or self.label(e.u32(slot))) if op == 0x23 and (v >> 21) & 31 == 28 else 'a pointer'
            if not op and (v >> 11) & 31 == rs: return 'a pointer'
            p -= 4
        return 'a pointer'
    def insn(self, y, lo, hi):
        if lo not in self.text:
            out = subprocess.check_output(['llvm-objdump', '-d', '--no-show-raw-insn', f'--start-address={lo:#x}', f'--stop-address={hi:#x}', str(self.path)], text=True)
            self.text[lo] = {int(m[1], 16): ' '.join(m[2].split()) for m in re.finditer(r'^\s*([0-9a-f]+):\s+(.*?)(?:\s+<[^>]*>)?$', out, re.M)}
        e, w = self.e, self.e.u32(y)
        t = self.text[lo].get(y, f'{w:08x}')
        m = re.search(r'0x([0-9a-f]+)$', t)
        if m and w >> 26 in (1, 4, 5, 6, 7, 20, 21, 22, 23): t = t[:m.start()] + f'{(int(m[1], 16) - y) // 4:+d} words'  # a branch: how far, not where
        if w >> 21 == 0x47c:  # lw off($gp): the slot by what it holds, since its offset moves
            slot = e.gp + rebase.s16(w & 0xffff)
            held = e.got.get(slot) or (self.label(e.u32(slot)) if e.text <= e.u32(slot) < e.end else 'a page')
            t = re.sub(r'-?0x[0-9a-f]+\(\$gp\)', f'GOT[{held}]', t)
        if y in self.sites:  # a low half: what it forms
            a = self.sites[y][0]
            s = e.cstr(a) if e.section(a) == '.rodata' else None
            t += f', forming "{s.decode()}"' if s and len(s) > 2 and s.isascii() and s.decode().isprintable() else f', forming {e.where(a)}' if '(past)' not in e.where(a) and not e.where(a).startswith('0x') else ''
        return t
    def feeds(self, y, hi):
        """The jalr $25 that the load of $25 at y is for, or None."""
        for pc in range(y + 4, min(hi, y + 128), 4):
            w = self.e.u32(pc)
            if w == 0x0320f809: return pc
            if w >> 26 in (0x23, 9) and (w >> 16) & 31 == 25: return None
    def note(self, y):
        e = self.e
        name, lo, hi = self.function(y)
        calls = [(pc, c) for pc in range(lo, hi, 4) for c in [self.callee(pc, lo)] if c]
        strings = [(pc, s.decode('utf-8', 'replace')) for pc in range(lo, hi, 4) if pc in self.sites and e.section(self.sites[pc][0]) == '.rodata'
                   for s in [e.cstr(self.sites[pc][0])] if s and len(s) > 2 and s.isascii() and s.decode().isprintable()]
        def which(pc):
            same = [p for p, c in calls if c == dict(calls)[pc]]
            return f'{nth(same.index(pc) + 1, len(same))} calls to {dict(calls)[pc]}'.replace('the only calls', 'the only call')
        w, called = e.u32(y), dict(calls)
        after = next((pc for pc, _ in calls if y < pc <= y + 32), None)
        if y in called: what = which(y)
        elif y - 4 in called: what = f'`{self.insn(y, lo, hi)}`, the delay slot of {which(y - 4)}'
        elif w >> 16 == 0x8f99 and self.feeds(y, hi) in called: what = f'the load of $25 for {which(self.feeds(y, hi))}'
        else:
            same = [pc for pc in range(lo, hi, 4) if rebase.heavy(e.u32(pc)) == rebase.heavy(w)]
            what = f'`{self.insn(y, lo, hi)}`' + (f' ({nth(same.index(y) + 1, len(same))} such)' if len(same) > 1 else '')
            if after: what += f', before {which(after)}'
        before = [s for pc, s in strings if pc < y]
        if before: what += f', after "{before[-1]}"'
        return name, what

def owner(rel, lines, n):
    """What of ours the literal on line n belongs to: the nearest name above it, and a JSON table's purpose."""
    pattern = re.compile(OWNER.get(pathlib.Path(rel).suffix, OWNER['']))
    below = n
    while below < len(lines) - 1 and (COMMENT.match(lines[below]) or not lines[below].strip()): below += 1  # a comment is about what follows it
    for k in (n, below, *range(n - 1, -1, -1)):
        m = pattern.search(lines[k])
        if m and not COMMENT.match(lines[k]) and (k in (n, below) or not lines[k][:1].isspace() or rel.endswith('.json')):
            name = next(g for g in m.groups() if g)
            if not rel.endswith('.json'): return f'{rel} {name}'
            extra = [x[1] for j in range(k, n + 1) for x in [re.search(r'"(?:value|purpose)": "(.*)"', lines[j])] if x][-2:]
            return f'{rel} {name}' + (f' {extra[0]} ({extra[1]})' if len(extra) == 2 else f' ({extra[0]})' if extra else '')
    return rel

def table(stock, work):
    _, names, _ = rebase.read_stock_map(rebase.STOCK_MAP.read_text())
    bins = {b: Binary(b, stock.cat(path), names.get(b, ()), work) for b, path in rebase.BINARIES.items()}
    texts = {rel: (ROOT/rel).read_text() for rel in rebase.tracked()
             if rel.startswith(('patch/', 'tools/')) and rel not in (*rebase.TABLES, 'tools/rebase.py')}
    judged = rebase.judge_tree(texts, {b: x.v for b, x in bins.items()})
    users = {}
    for rel, (lits, verdict) in judged.items():
        lines = texts[rel].split('\n')
        for n, (b, _) in verdict.items():
            y = lits[n].value
            if b in bins and bins[b].e.text <= y < bins[b].e.end and not y & 3 and y not in bins[b].v.starts and owner(rel, lines, lits[n].line) not in NOT_SITES:
                users.setdefault((list(bins).index(b), y), []).append(owner(rel, lines, lits[n].line))
    rows, loose = [], []
    for (i, y), used in sorted(users.items()):
        b = list(bins)[i]
        name, what = bins[b].note(y)
        if name.startswith('sub_') and b != 'bluealsa': loose.append(f'{b} {y:#x} in {name}: {used[0]}')
        rows.append('\t'.join((b, f'{y:#x}', '0x4', name, f'{what}; {", ".join(dict.fromkeys(used))}')))
    return HEAD.format(stock.version) + '\n'.join(rows) + '\n', loose

def selftest():
    assert [nth(1, 1), nth(2, 3), nth(11, 12), nth(23, 30)] == ['the only', '2nd of 3', '11th of 12', '23rd of 30']
    py = ['# about B', '# more', 'B = (1,', '     2)', 'def f():', '    x = 3']
    assert [owner('a.py', py, n) for n in (0, 2, 3, 5)] == ['a.py B', 'a.py B', 'a.py B', 'a.py f']
    c = ['/* about g', ' * more */', 'static int g(void) {', '    return 4;', '}', '#define K 5']
    assert [owner('a.c', c, n) for n in (1, 3, 5)] == ['a.c g', 'a.c g', 'a.c K']
    js = ['{', '  "t": [', '    {', '      "value": "v",', '      "purpose": "why",', '      "sites": [', '        ["1"]', '  "u": {', '    "2": "3"']
    assert [owner('a.json', js, n) for n in (6, 8)] == ['a.json t v (why)', 'a.json u']

def main():
    selftest()
    args = [a for a in sys.argv[1:] if a != '--check']
    if len(args) != 1: sys.exit(__doc__)
    with tempfile.TemporaryDirectory(prefix='q2-sites-') as work:
        text, loose = table(rebase.Stock(args[0], work), work)
    for line in loose: print(f'in no named function (name it in tools/stock_map.tsv, or the literal is another binary\'s): {line}', file=sys.stderr)
    if '--check' in sys.argv:
        if not SITES.exists() or SITES.read_text() != text: sys.exit(f'{SITES.relative_to(ROOT)} is not current: run docs/rebasing/sitenotes.py')
    else: SITES.write_text(text)
    print(f'{text.count(chr(10)) - 2} sites, {len(loose)} in no named function')

if __name__ == '__main__':
    main()
