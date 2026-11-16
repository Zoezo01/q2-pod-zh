#!/usr/bin/env python3
"""Per-caller call counts of two builds' payloads, and where they differ: a split that moves code between files
should leave every caller making the same calls, so a new call here is a helper that stopped being inlined
(or one clang now calls three times through RGBA()).

  python3 test/callcmp.py BASE_BUILD BUILD

Each argument is a build directory (its patch.elf, every payload object linked) or an ELF. Counted are the
payload's own jal and tail-call j, by function name. Limits: static functions of one name in several files
(closed, tick, row, scrub_row, ...) are summed under it, callers and callees alike, so a call one copy lost and
another gained cancels out; and a call through jalr on a constant isn't counted: every stock call, and the few
payload calls made that way (page_row_detail_into). Exits 1 if any count differs."""
import argparse
import collections
import pathlib
import re
import subprocess
import sys

FUNCTION = re.compile(r'^[0-9a-f]+ <(.+)>:$')
CALL = re.compile(r'^\s*[0-9a-f]+:\s+(jal|j)\s+0x[0-9a-f]+ <([^+>]+)>$')  # <name+0x..> is a jump inside one


def calls(lines):
    """{caller: Counter(callee)} from llvm-objdump -d lines."""
    out = collections.defaultdict(collections.Counter)
    caller = None
    for line in lines:
        if m := FUNCTION.match(line): caller = m[1]
        elif (m := CALL.match(line)) and caller: out[caller][m[2]] += 1
    return out


def load(path):
    path = pathlib.Path(path)
    if path.is_dir(): path /= 'patch.elf'
    return calls(subprocess.run(['llvm-objdump', '-d', '--no-show-raw-insn', path], check=True, capture_output=True, text=True).stdout.splitlines())


def differ(base, new):
    """(caller, callee, base count, new count) for each count that moved."""
    return [(f, g, base[f][g], new[f][g]) for f in sorted(base.keys() | new.keys())
            for g in sorted(base[f].keys() | new[f].keys()) if base[f][g] != new[f][g]]


FIXTURE = '''00b00000 <a>:
  b00000:      	jal	0xb00010 <b>
  b00004:      	j	0xb00008 <a+0x8>
  b00008:      	j	0xb00010 <b>
00b00010 <b>:
  b00010:      	jal	0xb00000 <a>'''
assert calls(FIXTURE.splitlines()) == {'a': {'b': 2}, 'b': {'a': 1}}
assert differ({'a': collections.Counter(b=2)}, {'a': collections.Counter(b=1, c=1)}) == [('a', 'b', 2, 1), ('a', 'c', 0, 1)]

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('base'); p.add_argument('new')
    a = p.parse_args()
    rows = differ(load(a.base), load(a.new))
    for f, g, x, y in rows: print(f'{f}\t-> {g}\t{x} -> {y}')
    print('identical' if not rows else f'{len(rows)} call counts differ')
    sys.exit(1 if rows else 0)
