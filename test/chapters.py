#!/usr/bin/env python3
"""Host parser/process checks, using real FFmpeg for QuickTime and Nero chapters."""
import argparse
import ctypes as C
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
    assert parse(metadata() + metadata('0').split('\n', 1)[1])[0] == -1
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
print('Chapters: parser, QuickTime, Nero, literal filenames, empty/error, timeout, cancellation, output cap and MP4 index estimate passed.')
