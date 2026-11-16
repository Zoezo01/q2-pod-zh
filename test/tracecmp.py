#!/usr/bin/env python3
"""A masked diff of two Q2_TRACE files (Q2_TRACE=FILE python3 test/patch.py BUILD): what a refactor that should
change nothing on screen must leave identical.

  python3 test/tracecmp.py BASE.trace NEW.trace                     # exit 1 on any difference
  python3 test/tracecmp.py --split page_closed=qm_closed A.trace B.trace  # ... but for the pairs named

The trace isn't address-free, so before comparing each line is masked (MASKS). Cases are matched by their
`== N` line, and each difference is printed under the case it ran in. --split OLD=NEW, repeatable, allows what
moving code between files changes and nothing else: a ret line of the same length whose hook name or $v0 is OLD
in BASE and NEW in NEW, every other field equal, as a destroy handler's name (`ret page_closed` becomes the
file's own closer) and a void function's leftover $v0 naming a different struct (st=pw). Allowed lines are still
printed, and each pair used is counted."""
import argparse
import collections
import difflib
import re
import sys


def ringnav_box(m):  # key = h << 24 | face, face a string literal's address: keep h and whether face is set
    v = int(m[2]) & 0xffffffff
    return f'{m[1]}h={v >> 24},face={"*" if v & 0xffffff else 0}'


MASKS = [
    (re.compile(r'^(widget_set_prop_int\tn\d+\t"_ringnav_box"\t)(-?\d+)$'), ringnav_box),
    (re.compile(r'^(ret\tt9_box\t)\S+$'), r'\1*'),  # a void function: $v0 is whatever its last call left
    (re.compile(r'"V[0-9A-Z]{3}i"'), '"V***i"'),  # About's --dev tag and number: each build takes the next
    (re.compile(r'( dev )\d+"'), r'\1*"'),
]


def mask(line):
    for pattern, replace in MASKS: line = pattern.sub(replace, line)
    return line


def moved(a, b, pairs):
    """True if a and b are ret lines of one length that differ only in name or $v0, each by a pair named."""
    a, b = a.split('\t'), b.split('\t')
    return (a[0] == b[0] == 'ret' and len(a) == len(b) and a[3:] == b[3:]
            and all(x == y or (x, y) in pairs for x, y in zip(a[1:3], b[1:3])))


def cases(lines):
    """{N: masked lines up to and including `== N`}; lines after the last case go under the next number."""
    out, run = {}, []
    for line in lines:
        run.append(mask(line))
        if line.startswith('== '):
            n = int(line[3:])
            if n in out: raise SystemExit(f'case {n} twice: not one run of test/patch.py')
            out[n] = run; run = []
    if run: out[max(out, default=0) + 1] = run
    return out


def compare(base, new, pairs=()):
    """[(kind, case, base line, new line)] for each difference, kind 'allowed' (by pairs) or 'differs'."""
    base, new, out = cases(base), cases(new), []
    for n in sorted(base.keys() | new.keys()):
        a, b = base.get(n, []), new.get(n, [])
        if a == b: continue
        for op, k1, k2, l1, l2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == 'replace' and k2 - k1 == l2 - l1:  # line for line: each pair is allowed or differs
                out += [('allowed' if moved(u, v, pairs) else 'differs', n, u, v) for u, v in zip(a[k1:k2], b[l1:l2])]
            elif op != 'equal':
                out += [('differs', n, u, None) for u in a[k1:k2]] + [('differs', n, None, v) for v in b[l1:l2]]
    return out


def used(rows):
    """{(OLD, NEW): times} over the allowed rows."""
    return collections.Counter((x, y) for kind, _, u, v in rows if kind == 'allowed'
                               for x, y in zip(u.split('\t')[1:3], v.split('\t')[1:3]) if x != y)


assert mask('widget_set_prop_int\tn3\t"_ringnav_box"\t314258364') == 'widget_set_prop_int\tn3\t"_ringnav_box"\th=18,face=*'
assert mask('widget_set_prop_int\tn3\t"_ringnav_box"\t285212672') == 'widget_set_prop_int\tn3\t"_ringnav_box"\th=17,face=0'
assert mask('widget_set_text_utf8\tn19\t"V0JUi"') == 'widget_set_text_utf8\tn19\t"V***i"'
assert mask('widget_set_text_utf8\tn29\t"1.8.6 dev 714"') == 'widget_set_text_utf8\tn29\t"1.8.6 dev *"'
assert mask('ret\tt9_box\tst') == mask('ret\tt9_box\t00000001') == 'ret\tt9_box\t*'
_base = ['== 1', 'ret\tpage_closed\t00000000', 'ret\tf\tst', 'canvas_fill_rect\t0,0,1,1', '== 2']
_new = ['== 1', 'ret\tqm_closed\t00000000', 'ret\tf\tpw', 'canvas_fill_rect\t0,0,1,2', '== 2']
_pairs = {('page_closed', 'qm_closed'), ('st', 'pw')}
assert compare(_base, _base, _pairs) == []
assert compare(_base, _new) == [('differs', 2, x, y) for x, y in zip(_base[1:4], _new[1:4])]
assert compare(_base, _new, _pairs) == [('allowed', 2, _base[1], _new[1]), ('allowed', 2, _base[2], _new[2]),
                                        ('differs', 2, _base[3], _new[3])]
assert used(compare(_base, _new, _pairs)) == {('page_closed', 'qm_closed'): 1, ('st', 'pw'): 1}
assert compare(_base, _base[:3] + _base[4:]) == [('differs', 2, _base[3], None)]
assert compare(_base, _base[:4]) == [('differs', 2, '== 2', None)]
assert compare(_base[:2], _base) == [('differs', 2, None, x) for x in _base[2:]]  # a truncated run: its tail
assert compare(['ret\tf\tpw'], ['ret\tf\tst'], _pairs)[0][0] == 'differs'  # a pair names one direction
# a pair allows only a ret line's name and $v0, between lines of one length, and lines only one for one
assert [k for k, *_ in compare(['ret\tf\tffffffff', 'ret\tf\tP', 'canvas_draw_icon\tst\t0\t0', 'ret\tf\tst\tab12cd34ef56', 'ret\tpage_closed'],
                               ['ret\tf\tfffffffe', 'ret\tf\tst', 'canvas_draw_icon\tpw\t0\t0', 'ret\tf\tst', 'ret\tqm_closed\tst'], _pairs)] == ['differs'] * 5
assert [k for k, *_ in compare(['ret\tf\tst', 'ret\tg\tst'], ['ret\th\tst'], {('f', 'h')})] == ['differs'] * 3
try: cases(['== 1', 'x', '== 1']); raise AssertionError('a repeated case was taken')
except SystemExit: pass
_image = 'ret\tringnav_image_add\t00000000\t'  # an image's pixel hash is never a pair's, even one named
assert compare([_image + 'ab12cd34ef56'], [_image + 'fe98dc76ba54'], {('ab12cd34ef56', 'fe98dc76ba54')})[0][0] == 'differs'

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--split', action='append', default=[], metavar='OLD=NEW',
                   help="allow a ret line's name or $v0 OLD in base to be NEW; repeatable")
    p.add_argument('base'); p.add_argument('new')
    a = p.parse_args()
    pairs = {tuple(s.split('=', 1)) for s in a.split}
    if any(len(x) != 2 for x in pairs): p.error('--split takes OLD=NEW')
    with open(a.base) as f, open(a.new) as g: base, new = f.read().splitlines(), g.read().splitlines()
    rows = compare(base, new, pairs)
    for kind, case, x, y in rows:
        print(f'{kind}\tin case {case}\t' + '  ->  '.join(f'{c} {v}' for c, v in (('-', x), ('+', y)) if v is not None))
    for (x, y), n in sorted(used(rows).items()): print(f'pair\t{x}={y}\t{n}')
    bad = sum(kind == 'differs' for kind, *_ in rows)
    n = len(cases(base))
    print(f'identical, {n} cases' if not rows else f'{bad} lines differ, {len(rows) - bad} allowed, {n} cases')
    sys.exit(1 if bad else 0)
