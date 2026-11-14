"""Seeded malformed inputs for a parser's fuzz test. test/score.py's R2 counts a parser once its
test_fuzz_<name> has taken every case here and passed, under ASan and UBSan, in ten seconds:

    def test_fuzz_lrc(fw):
        for data in fuzz.cases('lrc', [b'[00:01.00]a line\\n']):
            ...  # the parser comes back, and what it returns holds its own limits

Give the parser its input and its room as conftest.block's, heap blocks of the exact size, so ASan sees a byte
past either: a ctypes buffer is Python's own memory, where it sees nothing.
"""
import os
import random

HUGE = (0xffffffff, 0x7fffffff, 0x80000000, 0xffff, 0x10000, 0)
FILL = (b'\0', b'\n', b'\r\n', b'\xff\xfe', b'=', b'[', b'"', b'\t')
COUNT = 3000


def cases(name, seeds):
    """COUNT inputs made from the valid ones in seeds, the same every run: each with one to three of a cut, flipped
    bits, a field made huge or a byte or four off what is left, a stretch repeated, a run put in. The scoreboard's
    count of them is written once all are taken."""
    rng = random.Random(f'q2-fuzz-{name}')
    for i in range(COUNT):
        data = bytearray(seeds[i % len(seeds)])
        for _ in range(rng.randint(1, 3)):
            kind, at = rng.randrange(5), rng.randrange(len(data) + 1)
            if kind == 0: del data[at:]
            elif kind == 1:
                for _ in range(rng.randint(1, 8)):
                    if data: data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
            elif kind == 2:  # a length: an extreme, or just past or short of the bytes after it
                width = rng.choice((1, 2, 4, 8))
                value = rng.choice((*HUGE, *(max(len(data) - at - width + d, 0) for d in (-4, -1, 1, 4))))
                data[at:at + width] = (value & (1 << 8*width) - 1).to_bytes(width, rng.choice(('little', 'big')))
            elif kind == 3:
                end = min(len(data), at + rng.randrange(65))
                data[at:end] = data[at:end]*rng.choice((2, 50, 3000))
            else: data[at:at] = rng.choice(FILL)*rng.choice((1, 1, 300, 70000))
        yield bytes(data)
    if os.environ.get('Q2_FUZZ_LOG'):
        with open(os.environ['Q2_FUZZ_LOG'], 'a') as log: log.write(f'{name}\t{COUNT}\n')
