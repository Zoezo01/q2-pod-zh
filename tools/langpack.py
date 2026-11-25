#!/usr/bin/env python3
"""Language packs' tables (docs/internals.md#languages): the list of texts a pack translates, and the check a
translation has to pass.

    python3 langpack.py template BUILD > en.tsv
    python3 langpack.py check en.tsv de.tsv [default.ttf]

en.tsv is every text the firmware asks for: each pair of BUILD's strings/en_US.bin (the table `relabel` in
build.py writes: a u16 0 and a u16 pair count, a u32 offset to each key and each value in file order, then the
strings), then Q2 Pod's own English, each its own key: the literals patch/*.c hands to tr() or marks with N_(),
and the English the build gives assets as tr_text. A line is `key<TAB>English<TAB>where` (`stock`, `assets`, or
the first file that says it), with \\n, \\t and \\\\ escaped
as the Q2 reads them (patch/language.c).

A translation is `key<TAB>English<TAB>text` lines under a first line `# ` and the language's own name. check
names each line whose key and English en.tsv lacks (the English changed since: the Q2 shows English), whose
text is empty or loses a %-conversion its English has (the Q2 skips it), and with a font each character it
lacks; then how many of en.tsv's texts it leaves English. Exit 1 on a problem, 2 on a wrong command line."""
import pathlib
import re
import struct
import sys

USAGE = 'usage: langpack.py template BUILD > en.tsv\n       langpack.py check en.tsv de.tsv [default.ttf]'
ROOT = pathlib.Path(__file__).resolve().parent.parent
# One printf spec as glibc reads it, over bytes, left to right: "%", a positional n$, flags, width, precision (a
# lone "." too), length, then any one byte as its letter. Known letters are conversions (%m takes no argument, but
# must match); %% (and %5%) and an unknown letter are none, unless a * or n$ in the spec takes an argument. A spec the
# string's end cuts short makes glibc 2.28 fail the whole call: it counts as one conversion of its own kind (b'').
POSITION = rb'0*[1-9][0-9]*\$'
SPEC = re.compile(rb"%%(?:%s)?[-+ #0'I]*(?:\*(?:%s)?|[0-9]*)(?:\.(?:\*(?:%s)?|[0-9]*))?(?:hh|h|ll|l|L|q|j|z|Z|t)?(.|\Z)"
                  % (POSITION, POSITION, POSITION), re.S)


def conversions(text):
    """The conversions text holds, each as written (bytes): %d, %02x, %s, %1$s; b'' for a spec the string's end cuts short."""
    found = []
    for m in SPEC.finditer(text.encode()):
        if not m[1]:
            found.append(b'')
        elif set(b'*$') & set(m[0][:len(m[0]) - 1]) or m[1] in b'diouxXeEfFgGaAcspnCSm':
            found.append(m[0])
    return found


# tr("...") or N_("..."), the literal maybe in pieces C joins
MARKED = re.compile(r'\b(?:tr|N_)\(\s*((?:"(?:[^"\\\n]|\\.)*"\s*)+)\)')
PIECE = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
C_ESCAPE = re.compile(rb'\\(x[0-9a-fA-F]{1,2}|[0-7]{1,3}|.)')
C_SIMPLE = {b'n': b'\n', b't': b'\t', b'r': b'\r', b'0': b'\0', b'\\': b'\\', b'"': b'"', b"'": b"'", b'?': b'?'}


def read_table(data):
    """The (key, value) pairs a strings/*.bin table holds, in file order."""
    if len(data) < 4 or struct.unpack_from('<H', data)[0]:
        raise ValueError('not a strings table')
    count = struct.unpack_from('<H', data, 2)[0]
    if len(data) < 4 + 8 * count:
        raise ValueError('not a strings table')
    offsets = struct.unpack_from(f'<{2 * count}I', data, 4)

    def string(at):
        return data[at:data.index(b'\0', at)].decode()
    return [(string(k), string(v)) for k, v in zip(offsets[0::2], offsets[1::2])]


def escape(s):
    return s.replace('\\', '\\\\').replace('\n', '\\n').replace('\t', '\\t')


def c_literal(pieces):
    """A C string literal's text: its pieces joined, its escapes undone, as UTF-8."""
    def undo(m):
        e = m[1]
        if e[:1] == b'x':
            return bytes([int(e[1:], 16)])
        if e[:1].isdigit() and e != b'0' or len(e) > 1:
            return bytes([int(e, 8)])
        return C_SIMPLE.get(e, b'\\' + e)
    return C_ESCAPE.sub(undo, ''.join(pieces).encode()).decode()


def own_strings(root=ROOT):
    """Q2 Pod's own English, each once: (text, the first patch/ file that says it)."""
    seen = {}
    for path in sorted((root/'patch').glob('*.c')):
        source = re.sub(r'/\*.*?\*/', '', path.read_text(encoding='utf-8'), flags=re.S)
        for m in MARKED.finditer(source):
            seen.setdefault(c_literal(PIECE.findall(m[1])), path.name)
    return list(seen.items())


def asset_strings():
    """The English the build gives assets as their tr_text: build.py's english_ui (but the keyboard's keys) and
    ipod.py's Home labels and confirm buttons; and what the build's headers hand tr(): the time zones' cities
    (clock.c) and the themes' own options and schemes (navigation.c)."""
    import build
    import ipod
    import theme
    themes = theme.builtins()
    texts = [*build.ENGLISH.values(), *(v for rel, m in build.ENGLISH_IN.items() if rel != 'kb_default_t9.bin'
                                        for v in m.values()),
             *(row['text'] for row in ipod.HOME_RENAMED.values()), *ipod.HOME_LITERAL.values(), *ipod.CONFIRM_TEXT,
             *(label.split(' ', 1)[1] for label, _ in build.TIMEZONES),
             *(t for _, _, o in theme.theme_keys(themes).values() if o for t in (o['label'], *o['names'])),
             *(s['label'] for t in themes for s in t['schemes'] if s.get('label'))]
    return list(dict.fromkeys(texts))


def template(build, root=ROOT):
    """en.tsv's lines: the build's table, then Q2 Pod's own English, patch/'s and then the assets'."""
    rows = [(key, value, 'stock') for key, value in read_table((pathlib.Path(build)/'strings/en_US.bin').read_bytes())]
    own = own_strings(root)
    rows += [(text, text, where) for text, where in own]
    said = {text for text, _ in own}
    rows += [(text, text, 'assets') for text in asset_strings() if text not in said]
    return ''.join('\t'.join(map(escape, row)) + '\n' for row in rows)


def read_lines(text):
    """A TSV's rows, its # lines and empty lines left out."""
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip() and not line.startswith('#'):
            fields = line.split('\t')
            if len(fields) != 3:
                raise ValueError(f'line {number} has {len(fields)} fields, not 3')
            rows.append(tuple(fields))
    return rows


def check(english, translation, font=None):
    """What translation (rows) leaves wrong against en.tsv's rows, as sentences, and how many it leaves English.
    font: the characters a font has, or None."""
    want = {(key, en) for key, en, _ in english}
    problems = []
    for key, en, text in translation:
        if (key, en) not in want:
            problems.append(f'{key}: en.tsv has no {en!r} (a changed English, or a typo)')
        elif not text:
            problems.append(f'{key}: no text')
        elif conversions(en) != conversions(text):
            problems.append(f'{key}: {text!r} does not keep the conversions of {en!r}')
        lacking = sorted({c for c in text if font is not None and ord(c) not in font and c != '\\'})
        if lacking:
            problems.append(f'{key}: the font has no {"".join(lacking)!r}')
    return problems, len(want - {(key, en) for key, en, _ in translation})


def main(argv):
    """The exit code: 0 when it worked, 1 when check found problems, 2 when the command line or a file is wrong."""
    try:
        if len(argv) == 2 and argv[0] == 'template':
            sys.stdout.write(template(argv[1]))
            return 0
        if argv[:1] == ['check'] and len(argv) in (3, 4):
            font = None
            if len(argv) == 4:
                from fontTools.ttLib import TTFont
                font = set(TTFont(argv[3], lazy=True).getBestCmap() or {})
            english, translation = (read_lines(pathlib.Path(p).read_text(encoding='utf-8')) for p in argv[1:3])
            problems, left = check(english, translation, font)
            print('\n'.join(problems + [f'{left} texts left English']))
            return 1 if problems else 0
    except (OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
