#!/usr/bin/env python
"""Peak bytes the firmware's own code holds while Songs, Albums and Cover Flow open on a 50,000-song library,
on this machine: test/score.py's P2. Not a test: `python3 test/fw/peak.py [songs]`.

Prints `Songs\t<peak bytes>\t<songs on the card>`, then Albums and CoverFlow. Counted, as one running total:
  * the firmware's calloc/realloc/strdup/free (peak_shim.c, linked in with ld --wrap: only fw.so's calls), as the
    bytes asked for;
  * each stSongInfo row a libcstl deque holds (stock's deques are Python lists here): REC_SIZE and its strings, as
    the device's copy (0x5c65ac) duplicates them, from its push to its clear, destroy or pop.
It is a peak: an allocation freed before the most is held, or made after, does not move it. Not counted, and the
Q2's: malloc's own overhead and libcstl's blocks, 32-bit sizes (a struct of pointers is wider here), stock's
widgets and rows built after the hook returns, sqlite, Cover Flow's worker and its covers (no thread runs here)."""
import ctypes as C
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import typing

HERE = pathlib.Path(__file__).resolve().parent
sys.dont_write_bytecode = True  # nothing written into the checkout
import conftest  # noqa: E402
import host  # noqa: E402
import test_e2e_library as e2e  # noqa: E402

O, libc = conftest.O, conftest.libc
B0, NB = 0x2000, 0x3000  # the string blocks' high halves, and each string's offset in its block: the same numbers
add: typing.Any = None  # peak_shim.c's q2m_add of the firmware loaded


class Arena:
    """e2e.Memory without its 256 records. A record's strings lie 4 bytes apart and each read takes 8, so string k
    sits at (word k+1) << 32 | word k: every word but the first is a block's number and an offset in the block
    before. Placed from the last string back, each at its block's next free byte, which names the block the string
    before goes in; a full block starts the record again elsewhere."""
    def __init__(self):
        self.rec = libc.mmap(0, 64 << 20, 3, 0x62, -1, 0)  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT
        assert 0 < self.rec < 1 << 31
        for b in range(B0, B0 + NB):
            assert libc.mmap(b << 32 | B0, NB, 3, e2e.FIXED, -1, 0) == b << 32 | B0, hex(b)
        self.n = self.turn = 0
        self.bump = [B0 + i * 7919 % (NB // 2) for i in range(NB)]

    def record(self, kind=8, id_=0, track=0, disc=0, cue=(0, 0), **strings):
        values = [strings.get(k, '').encode() + b'\0' for k in e2e.STRINGS]
        while True:
            hi = B0 + self.turn % NB
            self.turn += 1
            words = [0] * 9 + [hi]
            for k in range(8, -1, -1):
                lo = self.bump[hi - B0]
                if lo + len(values[k]) > B0 + NB:
                    break
                C.memmove(hi << 32 | lo, values[k], len(values[k]))
                self.bump[hi - B0] = lo + len(values[k])
                words[k] = hi = lo
            else:
                break
        r = self.rec + self.n * 0x60
        self.n += 1
        assert self.n * 0x60 <= 64 << 20
        body = (C.c_char * 0x60).from_address(r)
        struct.pack_into('<10I', body, O['REC_NAME'], *words)
        for field, value in (('ID', id_), ('TYPE', kind), ('TRACK', track), ('DISC', disc), ('CUE_START', cue[0]),
                             ('CUE_END', cue[1])):
            struct.pack_into('<i', body, O['REC_' + field], value)
        return r


def row_bytes(r):
    """A row as a deque holds it on the device: the record and a copy of each string."""
    n = O['REC_SIZE']
    # the strings the firmware names; genre, composer and cover are left out (empty on this card), as the 8-byte
    # pointer the firmware stores at REC_ALBUM_ARTIST here lands on their words too
    for field in ('NAME', 'PATH', 'TITLE', 'ALBUM', 'ARTIST', 'ALBUM_ARTIST'):
        p = C.c_void_p.from_address(r + O['REC_' + field]).value
        if p:
            n += len(C.string_at(p)) + 1
    return n


class Held(list):
    """A deque's rows, each counted while it is in."""
    def __init__(self):
        super().__init__()
        self.sizes = []

    def append(self, r):
        self.sizes.append(row_bytes(r))
        add(self.sizes[-1])
        super().append(r)

    def clear(self):
        add(-sum(self.sizes))
        self.sizes.clear()
        super().clear()

    def pop(self, index=-1):
        add(-self.sizes.pop(index))
        return super().pop(index)

    def __setitem__(self, k, rows):  # an assign: the rows go, the other deque's are copied in
        assert k == slice(None)
        rows = list(rows)
        self.clear()
        for r in rows:
            self.append(r)


class Counted(conftest.Deques):
    def __init__(self, fw):
        super().__init__(fw)
        fw.mock('fw_deque_destroy', lambda q, *_: self.lists.pop(q & 0xffffffff).clear())

    def new(self, rows=()):
        handle = super().new()
        self.lists[handle] = Held()
        return handle


class Disk(e2e.Disk):
    """e2e.Disk whose folder test is a set's, not a pass over 50,000 files."""
    def folder(self, path):
        if 'folders' not in vars(self):
            self.folders = {f[:i] for f in self.files for i in range(len(f)) if f[i] == '/'}
        return path in self.folders

    def fwrite(self, data, size, count, f, *_):  # "a": Cover Flow's load trace (e2e.Disk keeps only "w")
        self.handles[f][2] = self.handles[f][2] or b''
        return super().fwrite(data, size, count, f)


def card(n):
    """n songs: albums of 10, artists of 10 albums, every tag set but genre and composer."""
    tags = {}
    for i in range(n):
        album, artist = f'Album {i // 10:05}', f'Artist {i // 100:04}'
        tags[f'/mnt/mmc/Music/{artist}/{album}/{i % 10 + 1:02} Song {i:06}.flac'] = dict(
            title=f'Song {i:06}', album=album, artist=artist, album_artist=artist, track=i % 10 + 1)
    return {path: b'' for path in tags}, tags


def build(out):
    subprocess.run(['cc', '-O1', '-fPIC', '-c', str(HERE/'peak_shim.c'), '-o', str(out/'shim.o')], check=True)
    wrap = ','.join(f'--wrap={f}' for f in ('calloc', 'realloc', 'free', 'strdup'))
    return host.build(out/'fw', [f'-Wl,{wrap},{out}/shim.o'])  # -Wl: nothing to a compile, the shim to the link


def songs(q, n):
    q.fw.var('fw_ctx_g_class_type', C.c_int).value = e2e.SONGS
    assert q.fw.fn('ringnav_localclass', C.c_int, C.c_int)(e2e.SONGS) == n


def albums(q, n):
    q.fw.var('fw_ctx_g_class_type', C.c_int).value = e2e.ALBUMS
    assert q.fw.fn('ringnav_localclass', C.c_int, C.c_int)(e2e.ALBUMS) == (n + 9) // 10


def coverflow(q, n):
    """A first open, no art cached: the albums, then a job per album for the worker (which the host never starts)."""
    q.fw.fn('coverflow__coverflow_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert 'window_create' in [kind for kind, _ in q.made]


def measure(name, action, library, scratch, arena, files, db, n):
    """One screen in a child: a fresh firmware, and nothing it leaves at stock's addresses reaches the next."""
    global add
    sys.stdout.flush()
    child = os.fork()
    if child:
        assert os.waitpid(child, 0)[1] == 0, name
        return
    with tempfile.TemporaryDirectory(dir=library.parent) as private:
        fw = host.Fw(library, pathlib.Path(private))
    start, size = O['MCL_POOL'], O['MCL_FD'] + 4 - O['MCL_POOL']
    conftest.claim(start)
    C.memset(start, 0, size)
    add = fw.fn('q2m_add', None, C.c_long)
    q = e2e.Q2(fw, conftest.Mcl(), scratch, arena, files, library=db)
    q.deques.lists[q.pool] = []  # ints, not rows
    live, peak, calls = (C.c_long.in_dll(fw.lib, f'q2m_{k}') for k in ('live', 'peak', 'calls'))
    peak.value, calls.value = live.value, 0
    action(q, n)
    assert calls.value, name
    print(f'{name}\t{peak.value}\t{n}', flush=True)
    os._exit(0)


def main():
    n = int(sys.argv[1]) if sys.argv[1:] else 50000
    out = pathlib.Path(tempfile.mkdtemp())
    library = build(out)
    scratch = host.offsets(library.parent/'navigation.o', 'scratch_t')
    setattr(e2e, 'Deques', Counted)
    setattr(e2e, 'Disk', Disk)
    files, tags = card(n)
    db = e2e.Library(tags)
    db.scan(files)
    arena = Arena()
    for name, action in (('Songs', songs), ('Albums', albums), ('CoverFlow', coverflow)):
        measure(name, action, library, scratch, arena, files, db, n)
    shutil.rmtree(out)


if __name__ == '__main__':
    main()
