#!/usr/bin/env python3
"""Build a `strings/*.bin` table from a translation file, and check one against the English it covers.

    python3 langpack.py build en_US.bin zh_CN.tsv zh_CN.bin
    python3 langpack.py check en_US.bin zh_CN.tsv

A table is the one `relabel` in build.py reads: a u16 0 and a u16 pair count, a u32 offset to each key and each
value in file order, two zero words, then the strings as nul-terminated UTF-8, a pair's key and then its value.
A translation file is TSV, `key<TAB>source<TAB>text`, one line per pair of the table it translates. A line
matches a pair on its key and its source text, so a key that appears twice with two texts takes two lines, and
every pair needs exactly one line: a table and its translation always hold the same keys, in the same order.
A `%` conversion in a text must match its source's, and a text that is empty, a tab or a newline is refused.

Nothing here decides what the Q2 reads or when: a pack lands on the card until the language-pack issue (#375)
gives it a home on the device, so this tool is the tables those packs will hold and the check each one must pass."""
import pathlib
import re
import struct
import sys
from collections import Counter

USAGE = 'usage: langpack.py build en_US.bin zh_CN.tsv zh_CN.bin\n       langpack.py check en_US.bin zh_CN.tsv'
HEADER = 4  # the u16 0 and the u16 pair count
GAP = 8  # the two zero words before the strings
# A conversion, which a translation keeps: %d, %02x, %s, and the literal %% a text may also hold.
CONVERSION = re.compile(r'%(?!%)[-+ #0]*[0-9]*(?:\.[0-9]+)?[diouxXeEfFgGaAcspn]')


def read_table(data):
    """The (key, value) pairs a table holds, in file order."""
    if len(data) < HEADER:
        raise ValueError(f'the table is {len(data)} bytes, shorter than its header')
    zero, count = struct.unpack_from('<HH', data, 0)
    if zero != 0:
        raise ValueError(f'the table starts with {zero}, not 0')
    start = HEADER + 8 * count + GAP
    if len(data) < start:
        raise ValueError(f'the table has {len(data)} bytes for its {count} pairs, fewer than its header')
    offsets = struct.unpack_from(f'<{2 * count}I', data, HEADER)
    return [(_string(data, start, key), _string(data, start, value)) for key, value in zip(offsets[0::2], offsets[1::2])]


def _string(data, start, at):
    """The nul-terminated string at a table's offset, which has to lie past the header."""
    if not start <= at < len(data):
        raise ValueError(f'the table points at {at:#x}, outside the {len(data)} bytes it has')
    end = data.find(b'\0', at)
    if end < 0:
        raise ValueError(f'the string at {at:#x} has no end')
    try:
        return data[at:end].decode('utf-8')
    except UnicodeDecodeError as e:
        raise ValueError(f'the string at {at:#x} is not UTF-8: {e}') from e


def write_table(pairs):
    """The bytes of a table holding these (key, value) pairs."""
    offsets, strings, at = [], [], HEADER + 8 * len(pairs) + GAP
    for key, value in pairs:
        key, value = key.encode('utf-8'), value.encode('utf-8')
        offsets += [at, at + len(key) + 1]
        strings += [key + b'\0' + value + b'\0']
        at += len(key) + len(value) + 2
    return struct.pack(f'<HH{2 * len(pairs)}I', 0, len(pairs), *offsets) + b'\0' * GAP + b''.join(strings)


def read_translations(text):
    """The (key, source, text) rows a TSV holds, empty lines skipped."""
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split('\t')
        if len(fields) != 3:
            raise ValueError(f'line {number} has {len(fields)} fields, not 3')
        rows.append((fields[0], fields[1], fields[2]))
    return rows


def check(pairs, rows):
    """What a translation file leaves wrong for a table, as readable sentences; [] when it covers it."""
    want = Counter(pairs)
    have = Counter((key, source) for key, source, text in rows)
    problems = [f'{key}: no line for {source!r}' for key, source in sorted(want - have)]
    problems += [f'{key}: no pair in the table for {source!r}' for key, source in sorted(have - want)]
    for key, source, text in rows:
        if not text:
            problems.append(f'{key}: no text')
        elif _conversions(source) != _conversions(text):
            problems.append(f'{key}: {text!r} does not keep the conversions of {source!r}')
    return problems


def _conversions(text):
    """The conversions a text holds, %% left out: its own escape, not one to keep."""
    return CONVERSION.findall(text.replace('%%', ''))


def build(pairs, rows):
    """The bytes of the table these pairs' translation holds, one row per pair in file order."""
    problems = check(pairs, rows)
    if problems:
        raise ValueError('\n'.join(problems))
    lines = {}
    for key, source, text in rows:
        lines.setdefault((key, source), []).append(text)
    used, translated = Counter(), []
    for key, source in pairs:
        translated.append((key, lines[(key, source)][used[(key, source)]]))
        used[(key, source)] += 1
    return write_table(translated)


def main(argv):
    """The exit code: 0 when it worked, 1 when check found problems, 2 when the command line or a file is wrong."""
    if not argv or argv[0] not in ('build', 'check'):
        print(USAGE, file=sys.stderr)
        return 2
    command, args = argv[0], argv[1:]
    if len(args) != (3 if command == 'build' else 2):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        pairs = read_table(pathlib.Path(args[0]).read_bytes())
        rows = read_translations(pathlib.Path(args[1]).read_text(encoding='utf-8'))
        if command == 'check':
            problems = check(pairs, rows)
            print('\n'.join(problems) if problems else f'{len(pairs)} pairs, {len(rows)} lines: ok')
            return 1 if problems else 0
        pathlib.Path(args[2]).write_bytes(build(pairs, rows))
    except (OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    print(f'{args[2]}: {len(pairs)} pairs')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
