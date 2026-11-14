#!/usr/bin/env python3
"""Refresh test/i18n_zh.py's generated half from an upstream English tree.

Upstream's tests assert English UI strings; this fork ships a fixed Simplified
Chinese UI. Rather than editing hundreds of assertions (and resolving them again
on every upstream merge), test/zh_expect.py rewrites the literals at import and
the pairs live in one table. This script rebuilds that table's generated half:

    python3 test/gen_zh_expect.py                 # newest numeric upstream tag
    python3 test/gen_zh_expect.py --upstream 1.8.2
    python3 test/gen_zh_expect.py --check        # is the table up to date?

It lines up each file's upstream and local versions and pairs a string literal
that changed from English to Chinese in the same position. The hand-written
ZH_OVERRIDES / ZH_SKIP blocks in i18n_zh.py are never touched: put the pairs the
alignment cannot see (codepoint arrays, clock/date formats) and its few mistakes
there.
"""
import argparse
import difflib
import io
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TABLE = ROOT / 'test' / 'i18n_zh.py'
BEGIN = '# --- GENERATED START ---'
END = '# --- GENERATED END ---'

STR = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'')
ATTR = re.compile(r'([\w.]+)\s*=\s*"([^"]*)"')
CJK = re.compile(r'[\u4e00-\u9fff]')
FMT = re.compile(r'%(?:[-+ #0-9.]+)?[diouxXeEfgGcs%]|\{[:!]?[^{}]*\}')
# Literals that are fields, identifiers, paths or config keys, never UI words.
NOT_UI = re.compile(r'''^[a-z_][a-z0-9_./-]*$''')
PATHY = re.compile(r'://|\.bin\b|\.png\b|\.c\b|\.py\b|\.xml\b|/tmp/|^/')

SKELETON = '''"""Upstream's tests assert the English UI; this fork ships a fixed Simplified Chinese one.

Every expectation lives here, not in the test files: merging upstream then only
needs this table refreshed (test/gen_zh_expect.py), never a conflict in hundreds
of assertions. test/zh_expect.py rewrites test modules' literals at import.

The generated block is machine-written -- edit ZH_OVERRIDES and ZH_SKIP instead.
"""
''' + BEGIN + '''
ZH_GENERATED = {}
''' + END + '''

# Hand-written: pairs the source alignment cannot see (the Classic dialog's
# codepoint array, clock and date formats, theme names) plus its corrections.
ZH_OVERRIDES = {
    'Update Library': '更新曲库',
    'Minimal': '简约',
    'Classic': '经典',
    'Modern': '现代',
    'Classic Light': '经典',
}

# Literals that are inputs to the code under test, not expectations. Rewriting
# one of these would change the test's setup rather than what it expects.
ZH_SKIP = {
}

ZH = {**ZH_GENERATED, **ZH_OVERRIDES}
for _k in ZH_SKIP:
    ZH.pop(_k, None)
# The firmware tests compare ctypes bytes, the tools tests compare str.
ZH_BYTES = {k.encode(): v.encode() for k, v in ZH.items()}
'''


def git(*args):
    r = subprocess.run(['git', *args], cwd=ROOT, capture_output=True)
    return r.stdout.decode('utf-8', 'replace') if r.returncode == 0 else None


def upstream_ref(want):
    if want:
        return want
    tags = (git('tag', '-l', '[0-9]*') or '').split()
    numeric = [t for t in tags if re.fullmatch(r'\d+\.\d+(\.\d+)?', t)]
    if not numeric:
        sys.exit('No numeric upstream tag here: pass --upstream (e.g. 1.8.2)')
    return sorted(numeric, key=lambda t: tuple(map(int, t.split('.'))))[-1]


def unescape(s):
    """A C literal's escapes to the characters Python's tests actually compare."""
    out = bytearray()
    i = 0
    simple = {'n': 10, 't': 9, 'r': 13, '0': 0, '\\': 92, '"': 34, "'": 39}
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            c = s[i + 1]
            if c == 'x' and re.fullmatch(r'[0-9a-fA-F]{2}', s[i + 2:i + 4] or ''):
                out.append(int(s[i + 2:i + 4], 16))
                i += 4
                continue
            if c in simple:
                out.append(simple[c])
                i += 2
                continue
            out.extend(s[i:i + 2].encode())
            i += 2
            continue
        out.extend(s[i].encode('utf-8'))
        i += 1
    return out.decode('utf-8', 'replace')


def literals(line):
    """The string literals on a source line, with comments stripped."""
    t = line.strip()
    if t.startswith(('/*', '*', '//', '<!--', '#')):
        return []
    return [unescape(m.group(1) if m.group(1) is not None else m.group(2))
            for m in STR.finditer(line)]


def attributes(line):
    """name="..." pairs on a theme.xml line, in order (labels repeat on a line)."""
    if line.strip().startswith(('<!--', '-->')):
        return []
    return ATTR.findall(line)


def looks_like_ui(s):
    return (2 <= len(s) <= 80 and re.search(r'[A-Za-z]{2}', s) and not CJK.search(s)
            and not NOT_UI.match(s) and not PATHY.search(s) and '\n' not in s
            and not s.startswith(' ') and not s.endswith(' '))


def pairs(a_src, b_src, extract, attr_mode=False):
    """Line up two versions and pair English -> Chinese literals in place."""
    a, b = a_src.splitlines(), b_src.splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    found = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != 'replace' or i2 - i1 != j2 - j1:
            continue
        for x, y in zip(a[i1:i2], b[j1:j2]):
            lx, ly = extract(x), extract(y)
            if not lx or len(lx) != len(ly):
                continue
            # A theme.xml line can carry several label= attributes; pair them
            # by position and name rather than collapsing them into a dict.
            items = ([(e, c) for (ke, e), (kc, c) in zip(lx, ly) if ke == kc]
                     if attr_mode else list(zip(lx, ly)))
            for e, c in items:
                if not looks_like_ui(e) or not CJK.search(c) or FMT.findall(e) != FMT.findall(c):
                    continue
                if e in found and found[e] != c:
                    found[e] = None  # ambiguous: let ZH_OVERRIDES decide
                else:
                    found.setdefault(e, c)
    return {k: v for k, v in found.items() if v is not None}


def build(ref):
    files = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / 'patch').glob('*')
                   if p.suffix in ('.c', '.h', '.inc'))
    files += ['tools/ipod.py', 'tools/build.py', 'tools/theme.py']
    files += sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / 'assets/themes').glob('*/theme.xml'))
    found, ambiguous, sources = {}, set(), 0
    for path in files:
        up = git('show', ref + ':' + path)
        local = ROOT / path
        if up is None or not local.exists():
            continue
        sources += 1
        extract = attributes if path.endswith('.xml') else literals
        for k, v in pairs(up, local.read_text(encoding='utf-8', errors='replace'),
                          extract, attr_mode=path.endswith('.xml')).items():
            if k in found and found[k] != v:
                ambiguous.add(k)
            else:
                found.setdefault(k, v)
    for k in ambiguous:
        found.pop(k, None)
    print(f'{ref}: {sources} files, {len(found)} pairs, {len(ambiguous)} ambiguous skipped')
    return found, ambiguous


def render(pairs_):
    lines = [BEGIN, 'ZH_GENERATED = {']
    for k in sorted(pairs_):
        lines.append(f'    {k!r}: {pairs_[k]!r},')
    lines += ['}', END]
    # No trailing newline: the file's own tail (the hand-written blocks) is
    # re-used verbatim, so a second run must produce byte-identical output.
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--upstream', help='an upstream tag present in this repo (default: newest numeric tag)')
    ap.add_argument('--check', action='store_true', help='exit 1 if the generated block is stale')
    args = ap.parse_args()
    ref = upstream_ref(args.upstream)
    pairs_, ambiguous = build(ref)
    block = render(pairs_)
    if not TABLE.exists():
        TABLE.write_text(SKELETON, encoding='utf-8')
    text = TABLE.read_text(encoding='utf-8')
    if BEGIN not in text or END not in text:
        sys.exit('i18n_zh.py lost its GENERATED markers')
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    fresh = head + block + tail
    if args.check:
        print('stale' if fresh != text else 'up to date')
        return 1 if fresh != text else 0
    if fresh != text:
        TABLE.write_text(fresh, encoding='utf-8', newline='\n')
        print('wrote', TABLE.relative_to(ROOT))
    if ambiguous:
        print('ambiguous (add to ZH_OVERRIDES if a test needs them):', ', '.join(sorted(ambiguous)[:20]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
