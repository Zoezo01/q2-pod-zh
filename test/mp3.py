#!/usr/bin/env python3
"""Native check of the actual Xing parser, including seek bounds and CRC frames, the ID3v2 TXXX decoder and
id3_gain's walk of a tag's frames."""
from pathlib import Path
import subprocess
import tempfile

source = (Path(__file__).resolve().parents[1] / 'patch/peq_player.c').read_text()
def cut(*names): return ''.join(source[(s := source.index(n)):source.index('\n}\n', s) + 2] for n in names)
parser = cut('int mp3_toc(', 'unsigned id3_txxx(')
# id3_gain's world: the stream's fd reads tag[], and the stock calls (the vendor's walker, av_dict_set,
# av_dict_free, ff_replaygain_export) only note what they are given.
world = r'''
#include <assert.h>
#define AT(p, o, type) (*(type *)((char *)(p) + (o)))
static unsigned char tag[4096];
static long long tag_len, fd_at;
static unsigned tag_n, seeks, kept, exports;
static char pairs[4][2][32];
static long long fd_seek(int fd, long long off, int whence) {
    assert(fd == 3 && ++seeks < 64); /* a walk that has stopped moving ends here, not in a hang */
    return fd_at = whence ? fd_at + off : off;
}
static int fd_read(int fd, void *to, unsigned n) {
    long long left = fd_at < tag_len ? tag_len - fd_at : 0;
    if (n > left) n = left;
    memcpy(to, tag + fd_at, n);
    fd_at += n;
    return n;
}
static void stock_walker(void *s, int size) {}
static int dict_set(void **d, const char *key, const char *value, int flags) {
    assert(kept < 4);
    strcpy(pairs[kept][0], key);
    strcpy(pairs[kept++][1], value);
    *d = pairs;
    return 0;
}
static void dict_free(void **d) { *d = 0; }
static int export_gains(void *st, void *d) { exports++; return 0; }
#define lseek64 fd_seek
#define read fd_read
#define STOCK_ID3 stock_walker
#define AV_DICT_SET dict_set
#define AV_DICT_FREE dict_free
#define RG_EXPORT export_gains
'''
walker = cut('unsigned id3_int(', 'void id3_gain(')
check = r'''
#include <assert.h>
#include <math.h>
#include <string.h>
static void begin(int v) { memset(tag, 0, sizeof tag); memcpy(tag, "ID3", 3); tag[3] = v; tag_n = 10; }
static void add(const char *id, unsigned size, const void *body, unsigned n) { /* a frame, its size as its version writes it */
    unsigned char *o = tag + tag_n;
    memcpy(o, id, 4);
    for (int i = 0; i < 4; i++) o[4 + i] = tag[3] == 4 ? size >> (21 - 7 * i) & 127 : size >> (24 - 8 * i);
    memcpy(o + 10, body, n);
    tag_n += 10 + n;
}
static void walk(unsigned pad) { /* id3_gain on the tag, pad zero bytes after its frames; the fd is put back */
    long long stream[16] = {0};
    unsigned size = tag_n - 10 + pad;
    for (int i = 0; i < 4; i++) tag[6 + i] = size >> (21 - 7 * i) & 127;
    AT(stream, 0x14, int) = 3;
    AT(stream, 0x30, long long) = 10; /* MPlayer has read the tag's header */
    tag_len = tag_n + pad, fd_at = 1234, seeks = kept = exports = 0;
    id3_gain(stream, size);
    assert(fd_at == 1234 && exports == 1);
}
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

    /* id3_gain's walk: every TXXX of a tag reaches the dictionary, in 2.3 and 2.4, past an extended header */
    static const char peak[] = "\0REPLAYGAIN_TRACK_PEAK\0" "0.988123", long_comment[200] = "\3eng";
    begin(3);
    add("TIT2", 5, "\0Song", 5), add("TXXX", sizeof latin - 1, latin, sizeof latin - 1), add("TXXX", sizeof peak - 1, peak, sizeof peak - 1);
    walk(64);
    assert(kept == 2 && !strcmp(pairs[0][0], "REPLAYGAIN_TRACK_GAIN") && !strcmp(pairs[0][1], "-6.50 dB") &&
           !strcmp(pairs[1][0], "REPLAYGAIN_TRACK_PEAK") && !strcmp(pairs[1][1], "0.988123"));
    begin(4); /* 2.4 writes 200 as 00 00 01 48: read plainly, the walk would step past the TXXX */
    add("COMM", sizeof long_comment, long_comment, sizeof long_comment), add("TXXX", sizeof latin - 1, latin, sizeof latin - 1);
    walk(64);
    assert(kept == 1 && !strcmp(pairs[0][1], "-6.50 dB"));
    begin(3);
    tag[5] = 0x40; /* 2.3's extended header: its size, 6, then that many bytes */
    tag[13] = 6, tag_n = 20;
    add("TXXX", sizeof latin - 1, latin, sizeof latin - 1);
    walk(64);
    assert(kept == 1 && !strcmp(pairs[0][1], "-6.50 dB"));

    /* A 2.3 frame's size is 32 bits, and 0xfffffff6 with its 10-byte header is 0 in 32: the walk read that
     * frame for ever. It ends there now, with what came before it; fd_seek's count stops a walk that doesn't. */
    begin(3);
    add("TXXX", sizeof latin - 1, latin, sizeof latin - 1), add("PRIV", 0xfffffff6u, "", 0), add("TXXX", sizeof peak - 1, peak, sizeof peak - 1);
    walk(64);
    assert(kept == 1 && !strcmp(pairs[0][1], "-6.50 dB"));
    begin(3);
    add("PRIV", 0xfffffff6u, "", 0);
    walk(0); /* the whole tag is that frame's header: 20 bytes */
    assert(!kept);
    begin(3);
    memset(tag + 10, 0xff, 1500), tag_n = 1510; /* any 10 of these read as a frame of size 0xffffffff: 9 bytes a step, in 32 bits */
    walk(0);
    assert(!kept && seeks == 4); /* the fd's place, the tag, that one frame, the fd's place again */
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    src, exe = Path(tmp) / 'mp3.c', Path(tmp) / 'mp3'
    src.write_text('#include <string.h>\n' + parser + world + walker + check)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-fsanitize=undefined', '-fno-sanitize-recover=undefined',
                    str(src), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('Xing seek bounds, nonfinite input, CRC and truncated headers; TXXX encodings; the ID3v2 frame walk: OK')
