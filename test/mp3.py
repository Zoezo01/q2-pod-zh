#!/usr/bin/env python3
"""Native check of the actual Xing parser, including seek bounds and CRC frames, and the ID3v2 TXXX decoder."""
from pathlib import Path
import subprocess
import tempfile

source = (Path(__file__).resolve().parents[1] / 'patch/peq_player.c').read_text()
parser = ''.join(source[(s := source.index(n)):source.index('\n}\n', s) + 2] for n in ('int mp3_toc(', 'unsigned id3_txxx('))
check = r'''
#include <assert.h>
#include <math.h>
#include <string.h>
int main(void) {
    unsigned char h[192] = {0xff, 0xfb, 0x90, 0};
    memcpy(h + 36, "Xing\0\0\0\x07", 8);
    h[46] = 0x10; /* 4096 frames */
    for (int i = 0; i < 100; i++) h[52+i] = i * 2;
    double frac, length;
    assert(mp3_toc(h, sizeof h, 0, &frac, &length) && frac == 0);
    assert(mp3_toc(h, sizeof h, -1, &frac, &length) && frac == 0);
    assert(mp3_toc(h, sizeof h, length * 2, &frac, &length) && frac == 1);
    assert(!mp3_toc(h, sizeof h, NAN, &frac, &length));
    assert(!mp3_toc(h, sizeof h, INFINITY, &frac, &length));
    assert(mp3_toc(h, sizeof h, length / 2, &frac, &length));
    double half = frac;
    memmove(h + 38, h + 36, 116);
    h[1] &= ~1; /* CRC after the MPEG header */
    assert(mp3_toc(h, sizeof h, length / 2, &frac, &length) && frac == half);
    for (unsigned n = 0; n < 154; n++)
        assert(!mp3_toc(h, n, 0, &frac, &length));

    char t[64];
    static const unsigned char latin[] = "\0REPLAYGAIN_TRACK_GAIN\0-6.50 dB";
    assert(id3_txxx(latin, sizeof latin - 1, t, sizeof t) == 30 && !strcmp(t, "REPLAYGAIN_TRACK_GAIN") &&
           !strcmp(t + 22, "-6.50 dB"));
    static const unsigned char le[] = {1, 0xff, 0xfe, 'K', 0, 0, 0, 0xff, 0xfe, '-', 0, '1', 0, 0xe9, 0};
    assert(id3_txxx(le, sizeof le, t, sizeof t) == 5 && !memcmp(t, "K\0-1?", 6));
    static const unsigned char be[] = {1, 0xfe, 0xff, 0, 'K', 0, 0, 0, '2'}, be2[] = {2, 0, 'K', 0, 0, 0, '2'};
    assert(id3_txxx(be, sizeof be, t, sizeof t) == 3 && !memcmp(t, "K\0002", 4));
    assert(id3_txxx(be2, sizeof be2, t, sizeof t) == 3 && !memcmp(t, "K\0002", 4));
    assert(id3_txxx(latin, sizeof latin - 1, t, 4) == 3 && !strcmp(t, "REP"));
    assert(!id3_txxx(latin, 0, t, sizeof t) && !id3_txxx((const unsigned char *)"\4x", 2, t, sizeof t));
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    src, exe = Path(tmp) / 'mp3.c', Path(tmp) / 'mp3'
    src.write_text('#include <string.h>\n' + parser + check)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-fsanitize=undefined', '-fno-sanitize-recover=undefined',
                    str(src), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('Xing seek bounds, nonfinite input, CRC and truncated headers; TXXX encodings: OK')
