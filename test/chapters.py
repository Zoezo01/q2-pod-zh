#!/usr/bin/env python3
"""Host parser/process checks, using real FFmpeg for QuickTime and Nero chapters."""
import argparse
import ctypes as C
import mmap
import pathlib
import struct
import subprocess as S
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--stock-root', type=pathlib.Path, help='extracted stock rootfs, for bundled FFmpeg checks')
parser.add_argument('--qemu', type=pathlib.Path, help='qemu-mipsel executable')
args = parser.parse_args()
if bool(args.stock_root) != bool(args.qemu): parser.error('--stock-root and --qemu are used together')

class Chapter(C.Structure):
    _fields_ = [('sec', C.c_int), ('title', C.c_char * 256)]

class Chapters(C.Structure):
    _fields_ = [(k, C.c_int) for k in ('pid', 'fd', 'used', 'done', 'failed', 'count')] + [
        ('at', C.c_ulonglong), ('rows', Chapter * 512), ('raw', C.c_char * (256*1024+1))]

with tempfile.TemporaryDirectory(prefix='q2-chapters-') as tmp:
    d = pathlib.Path(tmp)
    S.run(['cc', '-shared', '-fPIC', '-DPEQ_HOST', '-Wall', '-Wextra', '-Werror',
           str(ROOT/'patch/chapters.c'), '-o', str(d/'chapters.so')], check=True)
    lib = C.CDLL(str(d/'chapters.so'))
    lib.chapters_parse.argtypes = [C.c_char_p, C.POINTER(Chapter)]
    lib.chapters_start.argtypes = [C.c_char_p, C.c_ulonglong]
    lib.chapters_start.restype = C.POINTER(Chapters)
    lib.chapters_poll.argtypes = [C.POINTER(Chapters), C.c_ulonglong]
    lib.chapters_cancel.argtypes = [C.POINTER(Chapters)]
    libc = C.CDLL(None)
    libc.free.argtypes = [C.c_void_p]
    def parse(raw):
        rows = (Chapter * 512)()
        n = lib.chapters_parse(C.create_string_buffer(raw.encode()), rows)
        return n, [(r.sec, r.title.decode()) for r in rows[:max(0, n)]]
    def metadata(start='12345', base='1/1000', title='title=Café\\=一\\;\\#\\\\line\\\nnext\n'):
        return f';FFMETADATA1\n[CHAPTER]\nTIMEBASE={base}\nSTART={start}\nEND=99999\n{title}'
    assert parse(metadata()) == (1, [(12, 'Café=一;#\\line\nnext')])
    assert parse(metadata(title='')) == (1, [(12, '')])
    assert parse(metadata(title='title=last')) == (1, [(12, 'last')])
    assert parse(';FFMETADATA1\n') == (0, [])
    assert parse(metadata('9223372036854775807', '1/2147483647'))[0] == -1
    for start, base in [('-1', '1/1'), ('1x', '1/1'), ('1', '0/1'), ('1', '1/0'),
                        ('1', '1/1x'), ('9223372036854775808', '1/1'),
                        ('2147483648', '1/1'), ('9', '999999999999/1')]:
        assert parse(metadata(start, base))[0] == -1, (start, base)
    assert parse(';bad\n')[0] == -1
    assert parse(';FFMETADATA1\n[CHAPTER]')[0] == -1
    assert parse(metadata('7', '2/3', '')) == (1, [(4, '')])
    assert parse(metadata().replace('TIMEBASE=1/1000\n', ''))[0] == -1
    assert parse(metadata() + metadata('0', title='title=first').split('\n', 1)[1]) == (
        2, [(0, 'first'), (12, 'Café=一;#\\line\nnext')])
    assert parse(';FFMETADATA1\n' + '[CHAPTER]\nTIMEBASE=1/1\nSTART=0\n' * 513)[0] == -1
    def now(): return int(time.monotonic()*1000)
    def finish(c, at=None):
        deadline = time.monotonic()+12
        while not lib.chapters_poll(c, at if at is not None else now()):
            assert time.monotonic() < deadline, 'process was not reaped'
            time.sleep(.005)
        result = c.contents
        out = result.failed, [(r.sec, r.title.decode()) for r in result.rows[:max(0, result.count)]]
        assert result.fd == -1 and result.pid <= 0
        libc.free(c)
        return out
    def extract(path):
        out = finish(lib.chapters_start(str(path).encode(), now()))
        if args.stock_root:
            result = S.run([str(args.qemu), '-L', str(args.stock_root),
                            str(args.stock_root/'usr/bin/ffmpeg'), '-nostdin', '-v', 'error',
                            '-i', str(path), '-f', 'ffmetadata', 'pipe:1'], capture_output=True, timeout=10)
            assert bool(result.returncode) == bool(out[0]), result.stderr
            if not result.returncode:
                n, rows = parse(result.stdout.decode())
                assert n >= 0 and rows == out[1], (rows, out)
        return out
    def ff(*args):
        S.run(['/usr/bin/ffmpeg', '-nostdin', '-v', 'error', *map(str, args)], check=True)
    bare = d/'empty.m4b'
    ff('-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '4', '-c:a', 'aac', bare)
    assert extract(bare) == (0, [])
    assert extract(d/'missing.m4b')[0] == 1
    meta = d/'meta.txt'
    meta.write_text(';FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1250\ntitle=Café\\=一\\;\\#\\\\end\n'
                    '[CHAPTER]\nTIMEBASE=1/1000\nSTART=1250\nEND=4000\ntitle=Second\n')
    qt = d/'book ; $(touch SHOULD_NOT_EXIST) [一].m4b'
    ff('-i', bare, '-i', meta, '-map_metadata', '1', '-map_chapters', '1', '-c', 'copy', '-movflags', 'disable_chpl', qt)
    got = extract(qt)
    assert got == (0, [(0, 'Café=一;#\\end'), (1, 'Second')]), got
    assert not (d/'SHOULD_NOT_EXIST').exists()
    # ID3 CHAP frames, as an MP3 audiobook or podcast carries its chapters.
    mp3 = d/'book ; $(touch SHOULD_NOT_EXIST) [一].mp3'
    ff('-f', 'lavfi', '-i', 'anullsrc=r=22050:cl=mono', '-i', meta, '-t', '4', '-map', '0', '-map_metadata', '1',
       '-map_chapters', '1', '-c:a', 'libmp3lame', '-id3v2_version', '3', mp3)
    assert extract(mp3) == (0, [(0, 'Café=一;#\\end'), (1, 'Second')])
    # FLAC's and Ogg's CHAPTERxxx comments (FFmpeg reads them, but its muxers write none of their own).
    for name, codec in (('book.flac', 'flac'), ('book.ogg', 'libvorbis')):
        ff('-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '4', '-c:a', codec,
           '-metadata', 'CHAPTER001=00:00:00.000', '-metadata', 'CHAPTER001NAME=Café=一;#\\end',
           '-metadata', 'CHAPTER002=00:00:01.250', '-metadata', 'CHAPTER002NAME=Second', d/name)
        assert extract(d/name) == (0, [(0, 'Café=一;#\\end'), (1, 'Second')]), name
    assert not (d/'SHOULD_NOT_EXIST').exists()
    # Nero chpl only: insert the atom into moov, without a QuickTime text track.
    raw = bare.read_bytes()
    pos = 0
    while raw[pos+4:pos+8] != b'moov': pos += struct.unpack_from('>I', raw, pos)[0]
    size = struct.unpack_from('>I', raw, pos)[0]
    titles = ['日本語', 'Opening', '']
    payload = b'\x01\0\0\0' + b'\0'*4 + bytes([3])
    for sec, title in zip((0, 1.25, 2.75), titles):
        b = title.encode()
        payload += struct.pack('>QB', int(sec*10000000), len(b)) + b
    atom = struct.pack('>I4s', len(payload)+8, b'chpl') + payload
    nero = d/'nero.m4b'
    nero.write_bytes(raw[:pos] + struct.pack('>I', size+len(atom)) + raw[pos+4:pos+size] + atom + raw[pos+size:])
    assert extract(nero) == (0, [(0, titles[0]), (1, titles[1]), (2, titles[2])])
    # A FIFO makes FFmpeg wait for input, so cancellation and timeout are deterministic.
    import os
    fifo = d/'blocked.m4b'
    os.mkfifo(fifo)
    c = lib.chapters_start(str(fifo).encode(), now())
    lib.chapters_cancel(c)
    assert finish(c)[0] == 1
    c = lib.chapters_start(str(fifo).encode(), 0)
    assert finish(c, 10001)[0] == 1
    # Exercise bounded output without depending on FFmpeg's metadata limits.
    c = lib.chapters_start(str(fifo).encode(), now())
    c.contents.used = 256*1024
    assert finish(c)[0] == 1
    # MP4 index estimate: stsz/stz2 counts under moov's sample tables, any atom order.
    lib.mp4_index_bytes.argtypes = [C.c_char_p]
    lib.mp4_index_bytes.restype = C.c_ulonglong
    lib.mp4_fits.argtypes = [C.c_char_p]
    def atom(kind, body, large=False):
        return struct.pack('>I4sQ', 1, kind, len(body)+16) + body if large else struct.pack('>I4s', len(body)+8, kind) + body
    def trak(table, count):
        return atom(b'trak', atom(b'mdia', atom(b'minf', atom(b'stbl', atom(table, struct.pack('>4xII', 0, count))))))
    def index(name, raw):
        (d/name).write_bytes(raw)
        return lib.mp4_index_bytes(str(d/name).encode())
    ftyp = atom(b'ftyp', b'M4B \0\0\0\0')
    moov = atom(b'moov', trak(b'stsz', 5000000) + trak(b'stz2', 7))
    assert index('big.m4b', ftyp + atom(b'mdat', b'\0'*64, large=True) + moov) == 5000007*28
    assert index('first.M4A', ftyp + moov + atom(b'mdat', b'')) == 5000007*28
    assert index('open.mp4', ftyp + moov + struct.pack('>I4s', 0, b'mdat') + b'\0'*8) == 5000007*28
    assert index('cut.m4b', ftyp + moov[:-6]) == 5000000*28  # nothing past the cut
    assert index('bad.m4b', struct.pack('>I4s', 4, b'ftyp') + moov) == 0
    assert index('song.mp3', ftyp + moov) == 0
    assert lib.mp4_index_bytes(str(d/'missing.m4b').encode()) == 0
    assert lib.mp4_fits(str(d/'big.m4b').encode()) == (5000007*28//1024 + 16*1024 <= int(
        [l for l in open('/proc/meminfo') if l.startswith('MemAvailable:')][0].split()[1]))
    assert lib.mp4_fits(str(bare).encode()) and 0 < lib.mp4_index_bytes(str(bare).encode()) < 100*28
    # strict() parses a buffer that ends at an unmapped page, so reading past the terminator crashes.
    ARENA = 1 << 16
    arena = mmap.mmap(-1, ARENA + 4096)
    base = C.addressof(C.c_char.from_buffer(arena))
    libc.mprotect.argtypes = [C.c_void_p, C.c_size_t, C.c_int]
    assert libc.mprotect(base + ARENA, 4096, 0) == 0
    def strict(raw, dirty=False):
        raw = raw.encode() if isinstance(raw, str) else raw
        at = base + ARENA - len(raw) - 1
        C.memmove(at, raw + b'\0', len(raw) + 1)
        rows = (Chapter * 512)()
        if dirty: C.memset(rows, 255, C.sizeof(rows))
        n = lib.chapters_parse(C.cast(at, C.c_char_p), rows)
        return n, [(r.sec, r.title) for r in rows[:max(0, n)]]
    H = ';FFMETADATA1\n'
    def ch(start='1', base='1/1', title='title=x\n'):
        return f'[CHAPTER]\nTIMEBASE={base}\nSTART={start}\nEND=9\n{title}'
    assert strict(H + ch()) == (1, [(1, b'x')])
    for bad in (';bad\n', ';FFMETADATA', ';FFMETADATA1'):
        assert strict(bad)[0] == -1, bad
    for bad in (';FFMETADATA2\n', ';GFMETADATA1\n', ';FFMETADATA1x\n'):
        assert strict(bad + ch())[0] == -1, bad
    assert strict((H + ch()).replace('\n', '\r\n')) == (1, [(1, b'x')])
    assert strict(H + ch('9'))[1] == [(9, b'x')]
    assert strict(H + ch('1A'))[0] == -1
    assert strict(H + '[CHAPTER]\nTIMEBASE=1/1\nSTART=5\nSTART=7\n')[1] == [(7, b'')]
    for start in ('18446744073709551617', '9300000000000000000'):
        assert strict(H + ch(start, '1/2147483647'))[0] == -1, start
    assert strict(H + ch(title='title=abc\\'))[1] == [(1, b'abc\\')]
    assert strict(H + ch(title='title=last'))[1] == [(1, b'last')]
    assert strict(H + '[CHAPTER]\nTIMEBASE=1/1\n\nSTART=2\n')[1] == [(2, b'')]
    assert strict(H + '[CHAPTER]\nTIMEBASE=1/1\n')[0] == -1
    assert strict(H + '[CHAPTER]\nSTART=2\nTIMEBASE=1/1\n')[1] == [(2, b'')]
    assert strict(H + ch('1', '2147483647/1'))[1] == [(2147483647, b'x')]
    assert strict(H + ch('2147483647', '1/2147483647'))[1] == [(1, b'x')]
    assert strict(H + ch('2147483647', '1/1'))[1] == [(2147483647, b'x')]
    assert strict(H + ch('0', '2147483648/1'))[0] == -1
    assert strict(H + ch('1', '1/2147483648'))[0] == -1
    assert strict(H + ch('4611686011984936957', '2147483647/1'))[0] == -1  # wraps to 3 seconds
    assert strict(H + ch(str(999*2147483 + 998), '1000/999'))[0] == -1  # over only by the remainder
    assert strict(H + ch('8', '2/3'))[1] == [(5, b'x')]
    assert strict(H + ch('2') + ch('2'))[0] == 2
    assert strict(H + ch(), dirty=True)[1] == [(1, b'x')]
    assert strict(H + ch('0') + '[CHAPTER]\nTIMEBASE=1/1\n')[0] == -1
    assert strict(H + ch('1', title='title=one\n') + ch('2', title=''))[1] == [(1, b'one'), (2, b'')]
    assert strict(H + ch('6') + 'TIMEBASEX=5/1\nSTARTX=7\ntitleX=zz\n')[1] == [(6, b'x')]
    assert strict(H + 'TIMEBASE=bad\nSTART=bad\n' + ch())[0] == 1
    def titled(t):
        return strict(H.encode() + b'[CHAPTER]\nTIMEBASE=1/1\nSTART=0\ntitle=' + t)[1][0][1]
    for t, want in ((b'a'*255, b'a'*255), (b'a'*256, b'a'*255), (b'a'*2000, b'a'*255),
                    (b'a'*254 + 'é'.encode(), b'a'*254), (b'a'*253 + '一'.encode(), b'a'*253), (b'\x80'*300, b'')):
        assert titled(t) == want, t[-8:]

    # The process and its pipe, scripted: nothing forks, and a poll's order of reads and waits is exact.
    fakes = d/'fake.c'
    fakes.write_text(f'#define PEQ_HOST\n#include "{ROOT}/patch/peq.h"\n#include "{ROOT}/patch/chapters.h"\n' + r'''
#include <signal.h>
#include <sys/wait.h>
#include <errno.h>
#include <setjmp.h>
#include <stdarg.h>
struct {
    int no_calloc, no_pipe, no_fcntl, no_dup2, fopen_errno, pipe_fd[2], fork_result, wait_status;
    int forks, kills, kill_pid, kill_sig, execs, nclosed, closed[64], ndups, dups[8][2], fcntl_args[3];
    int reads[64], read_sizes[64], read_calls, read_at, waits[16], wait_calls, wait_pid, opened, fclosed;
    long seek_from, seek_to;
    char meminfo[512], feed[1 << 18];
} s;
static jmp_buf out;
static void *f_calloc(size_t n, size_t size) { return s.no_calloc ? 0 : calloc(n, size); }
static int f_pipe2(int *p, int flags) {
    (void)flags;
    if (s.no_pipe) return -1;
    p[0] = s.pipe_fd[0], p[1] = s.pipe_fd[1];
    return 0;
}
static int f_fork(void) { ++s.forks; return s.fork_result; }
static int f_close(int fd) { if (fd < 0) return -1; s.closed[s.nclosed++] = fd; return 0; }
static int f_fcntl(int fd, int cmd, ...) {
    va_list args;
    va_start(args, cmd);
    s.fcntl_args[0] = fd, s.fcntl_args[1] = cmd, s.fcntl_args[2] = va_arg(args, int);
    va_end(args);
    return s.no_fcntl ? -1 : 0;
}
static int f_dup2(int a, int b) {
    s.dups[s.ndups][0] = a, s.dups[s.ndups++][1] = b;
    return s.no_dup2 ? -1 : b;
}
static int f_open(const char *path, int flags) { (void)flags; return strcmp(path, "/dev/null") ? -1 : 9; }
static int f_execv(const char *path, char *const *args) { (void)path, (void)args; ++s.execs; return -1; }
static void f_exit(int code) { (void)code; longjmp(out, 1); }
static long f_read(int fd, void *buf, unsigned long n) {
    (void)fd;
    s.read_sizes[s.read_calls] = n;
    int r = s.reads[s.read_calls++];
    if (r < 0) { errno = -r; return -1; }
    memcpy(buf, s.feed + s.read_at, r);
    s.read_at += r;
    return r;
}
static int f_waitpid(int pid, int *status, int flags) {
    (void)flags;
    s.wait_pid = pid;
    int r = s.waits[s.wait_calls++];
    if (r < 0) { errno = -r; return -1; }
    if (r) *status = s.wait_status;
    return r;
}
static int f_kill(int pid, int sig) { ++s.kills; s.kill_pid = pid, s.kill_sig = sig; return 0; }
static FILE *f_fopen(const char *path, const char *mode) {
    int meminfo = !strcmp(path, "/proc/meminfo");
    if (s.fopen_errno && !meminfo) { errno = s.fopen_errno; return 0; }
    FILE *f = fopen(meminfo ? s.meminfo : path, mode);
    if (f) ++s.opened;
    return f;
}
static int f_fclose(FILE *f) { ++s.fclosed; return fclose(f); }
static int f_fseek(FILE *f, long at, int whence) {
    return fseek(f, s.seek_from && at >= s.seek_from ? at - s.seek_from + s.seek_to : at, whence);
}
#define calloc f_calloc
#define pipe2 f_pipe2
#define fork f_fork
#define close f_close
#define fcntl f_fcntl
#define dup2 f_dup2
#define open f_open
#define execv f_execv
#define read f_read
#define waitpid f_waitpid
#define kill f_kill
#define fopen f_fopen
#define fclose f_fclose
#define fseek f_fseek
#undef child_exit
#define child_exit f_exit
''' + f'#include "{ROOT}/patch/chapters.c"\n' + r'''
int run_child(const char *path) { if (setjmp(out)) return 1; chapters_start(path, 0); return 0; }
''')
    S.run(['cc', '-shared', '-fPIC', '-w', '-fsanitize=undefined', '-fno-sanitize-recover=undefined',
           str(fakes), '-o', str(d/'fake.so')], check=True)
    fake = C.CDLL(str(d/'fake.so'))
    fake.chapters_start.argtypes = lib.chapters_start.argtypes
    fake.chapters_start.restype = lib.chapters_start.restype
    fake.chapters_poll.argtypes = lib.chapters_poll.argtypes
    fake.chapters_cancel.argtypes = lib.chapters_cancel.argtypes
    fake.mp4_index_bytes.argtypes = [C.c_char_p]
    fake.mp4_index_bytes.restype = C.c_ulonglong
    fake.mp4_fits.argtypes = [C.c_char_p]
    fake.run_child.argtypes = [C.c_char_p]
    class Script(C.Structure):
        _fields_ = [(k, C.c_int) for k in ('no_calloc', 'no_pipe', 'no_fcntl', 'no_dup2', 'fopen_errno')] + [
            ('pipe_fd', C.c_int * 2)] + [(k, C.c_int) for k in ('fork_result', 'wait_status', 'forks', 'kills',
            'kill_pid', 'kill_sig', 'execs', 'nclosed')] + [('closed', C.c_int * 64), ('ndups', C.c_int),
            ('dups', C.c_int * 16), ('fcntl_args', C.c_int * 3), ('reads', C.c_int * 64),
            ('read_sizes', C.c_int * 64)] + [(k, C.c_int) for k in ('read_calls', 'read_at')] + [
            ('waits', C.c_int * 16)] + [(k, C.c_int) for k in ('wait_calls', 'wait_pid', 'opened', 'fclosed')] + [
            ('seek_from', C.c_long), ('seek_to', C.c_long), ('meminfo', C.c_char * 512), ('feed', C.c_char * (1 << 18))]
    s = Script.in_dll(fake, 's')
    TEXT = b';FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1\nSTART=3\nEND=9\ntitle=end\n'
    def setup(**flags):
        C.memset(C.byref(s), 0, C.sizeof(s))
        for key, value in flags.items(): setattr(s, key, value)
    def script(fork=4242, fds=(40, 41), reads=(), waits=(), status=0, feed=TEXT, child=False, **flags):
        setup(fork_result=fork, wait_status=status, **flags)
        s.pipe_fd[0], s.pipe_fd[1] = fds
        for i, n in enumerate(reads): s.reads[i] = n
        for i, n in enumerate(waits): s.waits[i] = n
        C.memmove(C.addressof(s) + Script.feed.offset, feed, len(feed))
        return fake.run_child(b'/x.m4b') if child else fake.chapters_start(b'/x.m4b', 1000)
    poll = lambda c, at=1001: fake.chapters_poll(c, at)
    closed = lambda: s.closed[:s.nclosed]
    dups = lambda: [tuple(s.dups[2*i:2*i+2]) for i in range(s.ndups)]
    assert not script(no_calloc=1)
    c = script(no_pipe=1)
    r = c.contents
    assert r.failed and r.done and r.fd == -1 and not s.forks and poll(c) == 1
    c = script(fork=-1)
    r = c.contents
    assert r.failed and r.done and r.fd == -1 and not s.kills and sorted(closed()) == [40, 41]
    c = script(no_fcntl=1, waits=[4242])
    r = c.contents
    assert r.failed and not r.done and r.fd == -1 and s.kill_pid == 4242 and s.kill_sig == 9 and not s.execs
    assert poll(c) == 1 and r.pid == 0
    c = script(reads=[-11], waits=[0, 0])
    assert poll(c, 10999) == 0 and not c.contents.failed  # 9999 ms after the start
    assert poll(c, 11000) == 0 and c.contents.failed
    # A pipe that stays full can't hold up a UI tick: the poll returns with what it has read so far.
    c = script(reads=[4096]*20, waits=[0])
    assert poll(c) == 0 and s.read_calls < 20 and c.contents.used == 4096*s.read_calls and not c.contents.failed
    # No read asks for more than the room left, and the last of the room is read whole.
    for room in (4097, 100):
        c = script(reads=[-11], waits=[0])
        c.contents.used = 256*1024 - room
        poll(c)
        assert 0 < s.read_sizes[0] <= room, room
    assert s.read_sizes[0] == 100
    # A full buffer kills FFmpeg at once: without it the 10-second timeout still fails the read, only late.
    c = script(reads=[0], waits=[0])
    c.contents.used = 256*1024
    assert poll(c) == 0 and c.contents.failed and s.kill_pid == 4242 and s.kill_sig == 9
    c = script(reads=[1, 9, len(TEXT) - 10], waits=[4242])
    r = c.contents
    assert poll(c) == 1 and r.used == len(TEXT) and r.count == 1 and r.rows[0].sec == 3 and r.rows[0].title == b'end'
    assert 40 in closed() and 41 in closed()
    calls = s.read_calls
    assert poll(c) == 1 and r.count == 1 and not r.failed and s.read_calls == calls
    c = script(reads=[len(TEXT) - 1], waits=[4242], feed=TEXT[:-1])
    assert poll(c) == 1 and c.contents.rows[0].title == b'end'
    for e, failed in ((4, 0), (11, 0), (5, 1)):
        c = script(reads=[-e, 1], waits=[0])
        assert poll(c) == 0 and c.contents.failed == failed and s.read_calls == 1, e
    for fds in ((40, 41), (0, 7)):
        c = script(fds=fds, reads=[20, -11, len(TEXT) - 20], waits=[4242])
        r = c.contents
        assert poll(c) == 0 and r.pid == 0 and r.fd == fds[0] and not r.done and not r.failed
        assert poll(c) == 1 and r.count == 1 and s.wait_calls == 1
        c = script(fds=fds, reads=[20, -11], waits=[4242], status=256)
        r = c.contents
        assert poll(c) == 1 and r.failed and r.fd == -1 and closed().count(fds[0]) == 1
    c = script(reads=[len(TEXT)], waits=[4242], status=256)
    assert poll(c) == 1 and c.contents.failed and c.contents.count == -1
    c = script(reads=[7], waits=[4242], feed=b'garbage')
    assert poll(c) == 1 and c.contents.failed
    c = script(reads=[len(TEXT), -11, 0], waits=[-4, 4242])
    r = c.contents
    assert poll(c) == 0 and r.pid == 4242 and not r.failed and not r.done
    assert poll(c) == 1 and r.pid == 0 and r.count == 1
    c = script(reads=[len(TEXT)], waits=[-10])
    assert poll(c) == 1 and c.contents.failed
    c = script(reads=[-4], waits=[4242])
    assert poll(c) == 0 and c.contents.pid == 0
    c = script(reads=[-11], waits=[0])
    assert poll(c) == 0 and c.contents.pid == 4242 and s.wait_calls == 1 and s.wait_pid == 4242
    c = script()
    fake.chapters_cancel(c)
    r = c.contents
    assert r.failed == 1 and r.fd == -1 and 40 in closed() and s.kill_pid == 4242 and s.kill_sig == 9
    c = script(fds=(0, 7))
    fake.chapters_cancel(c)
    assert 0 in closed()
    c = script(reads=[-11], waits=[4242])
    poll(c)
    fake.chapters_cancel(c)
    assert not s.kills
    assert script(fork=0, child=True) == 1 and s.execs == 1 and dups() == [(41, 1), (9, 2)]
    assert script(fork=0, fds=(0, 1), child=True) == 1 and s.execs == 1 and dups() == [(9, 2)]
    assert list(s.fcntl_args) == [1, 2, 0]
    assert script(fork=0, no_dup2=1, child=True) == 1 and not s.execs
    # The real ffmpeg, waiting on the FIFO: a poll returns at once and pid is the child's.
    c = lib.chapters_start(str(fifo).encode(), now())
    assert lib.chapters_poll(c, now()) == 0 and c.contents.pid > 0
    lib.chapters_cancel(c)
    assert finish(c)[0] == 1

    # MP4 atoms: every walk boundary, and the size of what counts.
    stsz = atom(b'stsz', struct.pack('>4xII', 0, 9))
    def box(*kinds, inner=stsz):
        for kind in reversed(kinds): inner = atom(kind, inner)
        return inner
    assert index('pad.m4b', atom(b'free', b'') + moov) == 5000007*28
    assert index('large.m4b', atom(b'moov', stsz, large=True) + atom(b'stsz', struct.pack('>4xII', 0, 2), large=True)) == 11*28
    assert index('tail.m4b', ftyp + struct.pack('>I4s', 0, b'moov') + stsz) == 9*28
    assert index('short.m4b', struct.pack('>I', 4) + moov) == 0
    assert index('over.m4b', struct.pack('>I4s', 16, b'moov') + struct.pack('>I4s', 28, b'trak') + stsz) == 9*28
    assert index('five.m4b', box(*[b'moov']*5)) == 9*28
    assert index('six.m4b', box(*[b'moov']*6)) == 0
    for kind in (b'free', b'moox', b'trax', b'mdix', b'minx', b'stbx'):
        assert index('near.m4b', box(kind)) == 0, kind
    assert index('near.m4b', atom(b'stzx', stsz[8:]) + atom(b'stsx', stsz[8:])) == 0
    assert index('thin.m4b', atom(b'stsz', b'\0'*10 + b'\x09') + atom(b'free', b'')) == 0
    assert index('same.m4b', box(b'stbl', inner=stsz + stsz)) == 18*28
    assert index('after.m4b', moov + stsz) == (5000007 + 9)*28
    assert lib.mp4_index_bytes(b'noextension') == 0 and lib.mp4_index_bytes(None) == 0
    big = str(d/'big.m4b').encode()
    setup(fopen_errno=79)
    assert fake.mp4_index_bytes(big) == 2**64 - 1
    setup(fopen_errno=2)
    assert fake.mp4_index_bytes(big) == 0
    far = d/'far.m4b'
    for jump, want in ((0x7fffffff, 9*28), (0x80000000, 0)):
        far.write_bytes(struct.pack('>I4sQ', 1, b'free', jump) + stsz)
        setup(seek_from=jump, seek_to=16)
        assert fake.mp4_index_bytes(str(far).encode()) == want, jump
    # What fits, against a meminfo of the test's own.
    need = 5000007*28//1024 + 16*1024
    lines = 'MemTotal:       99999999 kB\nMemFree:               1 kB\nMemAvailable:   {} kB\n'
    def fits(text, path=big, **flags):
        setup(**flags)
        info = d/'meminfo'
        info.unlink(missing_ok=True)
        if text is not None: info.write_text(text)
        s.meminfo = str(info).encode()
        got = fake.mp4_fits(path)
        assert s.opened == s.fclosed
        return got
    (d/'song.mp3').write_bytes(b'')
    assert fits(lines.format(need)) == 1
    assert fits(lines.format(need - 1)) == 0
    assert fits(lines.format(0), str(d/'song.mp3').encode()) == 1
    assert fits('MemTotal:       99999999 kB\n') == 1
    assert fits(None) == 1
    assert fits(lines.format(need) + ' '*600) == 1
    assert fits(lines.format(need), fopen_errno=79) == 0
print('Chapters: parser, QuickTime, Nero, ID3, FLAC and Ogg, literal filenames, empty/error, timeout, cancellation, output cap and MP4 index estimate passed.')
