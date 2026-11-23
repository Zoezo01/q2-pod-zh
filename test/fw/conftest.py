"""The firmware's C as one host library, freshly loaded for each test (host.py).

Q2_FW_SANITIZE=1 builds it with AddressSanitizer and UBSan: run pytest with LD_PRELOAD set to
`cc -print-file-name=libasan.so` and ASAN_OPTIONS=detect_leaks=0. Q2_FW_COVERAGE=1 builds it for gcov.
Q2_FW_STRICT=1 fails a test that reached a stub tools/ doesn't declare, unmocked (host.Fw.unknown):
its 0 is a guess at what stock would answer. Q2_FW_PSEUDO=1 gives every text tr() knows (langpack.py's
own_strings) a translation three times its bytes, and Q2_FW_PSEUDO=lang/xx.tsv a pack's table, so a run with
Q2_FW_SANITIZE finds what a long one, or a multibyte one, overflows; tests that read English fail, as they should, and only a sanitizer's report is news."""
import ctypes as C
import fcntl
import functools
import hashlib
import os
import pathlib
import re
import struct
import sys
import traceback

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import host  # noqa: E402

ROOT = host.ROOT
O = {k: int(v, 0) for k, v in re.findall(r'^#define (\w+) +(0x[0-9a-f]+|\d+)\b', (ROOT/'patch/offsets.inc').read_text(), re.M)}
# navigation.c's source with peq.h (pod_rows' POD_ enum), navigation.h and the units split out of it (each
# includes navigation.h), for tests that read a #define or enum the UI's code shares
NAVIGATION_UNITS = sorted(p.stem for p in (ROOT/'patch').glob('*.c') if '#include "navigation.h"' in p.read_text())
NAVIGATION_SOURCE = ''.join((ROOT/'patch'/n).read_text() for n in ['peq.h', 'navigation.h', *(f'{u}.c' for u in NAVIGATION_UNITS)])


def ui_name(lib, name):
    """The symbol of navigation.c's `name`, wherever the split put it: `name` itself once navigation.h shares it,
    else `<unit>__<name>`, a static of navigation.c or of a unit split out of it."""
    return next(s for s in (name, *(f'{u}__{name}' for u in NAVIGATION_UNITS)) if hasattr(lib, s))


def c_enum(first):
    """peq.h's or navigation.h's enum that starts with `first` (NAVIGATION_SOURCE holds it), by name: each its place, as
    none sets a value."""
    source = re.sub(r'/\*.*?\*/', '', NAVIGATION_SOURCE, flags=re.S)
    body = host.tools_build.need(re.search(rf'enum \{{\s*({first}\b[^}}]*)\}}', source), f'No enum {first}')[1]
    assert '=' not in body
    return {name.strip(): k for k, name in enumerate(body.split(',')) if name.strip()}


SPOT_OK, SPOT_NOSOCK, SPOT_FAIL = c_enum('SPOT_OK').values()  # spot_exchange's results
POD = c_enum('POD_CHARGE')  # pod_rows' rows, by name


def pod_value(fw, name):
    return fw.fn('pod_value', C.c_int, C.c_int)(POD[name])


FNV_SEED, FNV_PRIME = 2166136261, 16777619

# The C uses stock's fixed addresses as they are: zeroed pages here. A non-PIE python (Debian's and
# Ubuntu's own) has its code and data there, which the C would overwrite.
libc = C.CDLL(None)
libc.mmap.restype, libc.mmap.argtypes = C.c_void_p, [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
libc.mprotect.argtypes = [C.c_void_p, C.c_size_t, C.c_int]
if libc.mmap(0x400000, 0x800000, 3, 0x100022, -1, 0) != 0x400000:  # read/write, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
    pytest.exit('test/fw needs a position-independent python: 0x400000-0xc00000, stock\'s addresses, is taken '
                            '(a non-PIE python lives there); use one built with -pie, e.g. actions/setup-python\'s or a venv of a PIE build', returncode=4)


# Strings at chosen addresses: stock's records hold 4-byte pointers a word apart and P() reads 8 here, so a test puts
# a field's string where its word is the low half and the next field's the high one, in a page mapped for it. Each
# file takes its high halves from high_halves, which hands none out twice: no two files' pages meet, in any order.
# From 0x1001: past AddressSanitizer's shadow (to 0x10007fff7fff), short of its heap (0x500000000000).
_high = 0x1001


def high_halves(n):
    """n high halves no other caller gets this session, as a range: an index past its end fails."""
    global _high
    start, _high = _high, _high + n
    assert _high <= 0x5000, 'out of high halves'
    return range(start, _high)


@pytest.fixture(scope='session')
def library(tmp_path_factory):
    if os.environ.get('Q2_FW_LIBRARY'):  # one already built: test/cmut/run.py links each mutant's
        return pathlib.Path(os.environ['Q2_FW_LIBRARY'])
    extra = []
    if os.environ.get('Q2_FW_SANITIZE'):
        extra += ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined', '-fno-sanitize=alignment']
    if os.environ.get('Q2_FW_COVERAGE'):
        extra += ['--coverage']
    if not os.environ.get('PYTEST_XDIST_WORKER'):
        return host.build(tmp_path_factory.mktemp('fw'), extra)
    # pytest-xdist's workers (-n) share one build, in the folder above their own: the first to ask makes it and the
    # rest wait. A build each, all at once, took a worker's first test half of its 60 seconds under ASan.
    out = tmp_path_factory.getbasetemp().parent/'fw'
    with open(f'{out}.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (out/'sizes.json').exists():  # host.build's last file
            host.build(out, extra)
    return out/'fw.so'


@pytest.fixture
def fw(library, tmp_path):
    loaded = host.Fw(library, tmp_path)
    # page_list sizes its rows by its title label's fields (I(t, W_H)): a zeroed widget to read
    label = C.create_string_buffer(0x400)
    loaded.mock('hscroll_label_create', lambda *_: C.addressof(label))
    # stock's tk_sha256_* (the Q2's own code, not here), as hashlib: one hash per context address
    sums = {}
    loaded.mock('tk_sha256_init', lambda ctx, *_: sums.__setitem__(ctx, hashlib.sha256()))
    loaded.mock('tk_sha256_hash', lambda ctx, data, n, *_: sums[ctx].update(C.string_at(data, n)))
    loaded.mock('tk_sha256_done', lambda ctx, out, *_: C.memmove(out, sums.pop(ctx).digest(), 32) and 0)
    if os.environ.get('Q2_FW_PSEUDO'):
        pseudo(loaded)
    yield loaded
    unknown = loaded.unknown()
    loaded.close()
    if unknown and os.environ.get('Q2_FW_STRICT'):
        pytest.fail(f'unmocked stubs with no known return type: {", ".join(unknown)}')


@functools.cache
def pseudo_lines(table):
    """(key, English, text) as language.c keeps them, in its order (by_key: strcmp of the key, then the English):
    a .tsv's lines, unescaped, or each of Q2 Pod's own texts with a pseudo translation."""
    sys.path.insert(0, str(ROOT/'tools'))
    import langpack
    if table.endswith('.tsv'):
        def undo(s):
            return re.sub(r'\\([nt\\])', lambda m: {'n': '\n', 't': '\t', '\\': '\\'}[m[1]], s).encode()
        rows = {tuple(map(undo, r)) for r in langpack.read_lines(pathlib.Path(table).read_text(encoding='utf-8'))}
    else:
        rows = {(t, t, b'[' + t + b' ' + b'~' * (2 * len(t)) + b']') for t in (s.encode() for s, _ in langpack.own_strings())}
    return sorted(rows)


def pseudo(loaded):
    """language.c's table as a copy's load leaves it (lg: loaded, n, raw, line)."""
    lines = pseudo_lines(os.environ['Q2_FW_PSEUDO'])
    keep = [C.create_string_buffer(b) for row in lines for b in row]
    line = (C.c_char_p * len(keep))(*[C.cast(k, C.c_char_p) for k in keep])
    loaded.keep += [keep, line]
    lg = C.addressof(loaded.var('language__lg', C.c_char * 32))
    C.memmove(lg, struct.pack('<iI', 1, len(lines)), 8)
    C.c_void_p.from_address(lg + 8).value = C.addressof(line)
    C.c_void_p.from_address(lg + 16).value = C.addressof(line)


def text(value):
    """A C string the firmware may write into: mutable, NUL-terminated, kept alive by the caller."""
    return C.create_string_buffer(value if isinstance(value, bytes) else value.encode())


# Fakes more than one test file uses.
def block(fw, data=b'', size=None):
    """A heap block of data and a NUL, or of size bytes exactly, so ASan sees a byte past it."""
    p = fw.fn('calloc', C.c_void_p, C.c_size_t, C.c_size_t)(1, len(data) + 1 if size is None else size or 1)
    C.memmove(p, data, len(data))
    return p

def claim(address):
    """A page at one of stock's fixed addresses, mapped if free, made writable and executable (a test's
    stand-in code goes there) if conftest's own map holds it."""
    page = address & ~0xfff
    if libc.mmap(page, 0x2000, 7, 0x100022, -1, 0) != page:  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
        assert not libc.mprotect(page, 0x2000, 7)

def ask_cover(fw):
    """A card app's song asks for its cover, as get_onlineid3info does (build.py COVERINFO_CALL, tidal.c
    app_coverinfo): stock's player_set_coverinfo sets g_playcover_type to 0 for its cover thread, here before the
    call, as its stand-in at tidal.c's own define only returns. The stand-in outlives the test: call it in_fork."""
    at = int(re.findall(r'#define player_set_coverinfo \(\(.*\)(0x[0-9a-f]+)u\)', (ROOT/'patch/tidal.c').read_text())[0], 16)
    claim(at)
    C.memmove(at, b'\xc3', 1)  # ret
    fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
    rec, id_ = C.create_string_buffer(0x60), C.create_string_buffer(b'app:1:0')  # a card app's record
    C.c_void_p.from_address(C.addressof(rec) + O['REC_TIDAL_ID']).value = C.addressof(id_)
    fw.var('g_playcover_type', C.c_ubyte).value = 0
    fw.fn('app_coverinfo', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_void_p)(b'https://srv/cover.jpg', b'Song', 4,
                                                                                  C.addressof(rec))

class Externpic:
    """/tmp/externpic.jpg as stock's cover thread writes it, seen through __xstat (o32: st_size word 12, st_mtime
    word 16, its nanoseconds 17): write() is the next download, 125 ms on, of size bytes, so most share their second
    with the last; grow() is the same write read again with more of it there, in the same tick. Nothing else is
    there."""
    def __init__(self, fw):
        self.n, self.size = 0, 0
        fw.mock('fw___xstat', self.stat)

    def write(self, size=20000):
        self.n, self.size = self.n + 1, size

    def grow(self, size):
        self.size = size

    def stat(self, version, path, info, *_):
        if not C.string_at(path).endswith(b'/tmp/externpic.jpg'):
            return -1
        words = (C.c_uint * 36).from_address(info)
        words[12], words[16], words[17] = self.size, 1800000000 + self.n // 8, self.n % 8 * 125000000
        return 0

def in_fork(test):
    """The test in a child: what it writes at stock's code addresses would outlive the test here. The child
    closes its firmware (dlclose) before it leaves: gcov writes a library's counts then, and _exit alone would
    lose the child's, so a forked test counted nothing towards the C's coverage."""
    @functools.wraps(test)
    def run(*args, **kwargs):
        read, write = os.pipe()
        child = os.fork()
        if not child:
            failure = b''
            try:
                test(*args, **kwargs)
            except BaseException:
                failure = traceback.format_exc().encode()
            for fixture in (*args, *kwargs.values()):
                if isinstance(fixture, host.Fw):
                    fixture.close()
            os.write(write, failure)
            os._exit(1 if failure else 0)
        os.close(write)
        failure = b''
        while chunk := os.read(read, 65536):
            failure += chunk
        os.close(read)
        status = os.waitpid(child, 0)[1]
        if status:
            pytest.fail(f'child status {status:#x}\n' + failure.decode())
    return run

def fnv1a(data, h=FNV_SEED):
    for byte in data:
        h = (h ^ byte) * FNV_PRIME & 0xffffffff
    return h

class Mcl:
    """Stock's player globals, at their fixed addresses: an int each, by name."""
    def __getattr__(self, name):
        return C.c_int.from_address(O['MCL_' + name.upper()]).value

    def __setattr__(self, name, value):
        C.c_int.from_address(O['MCL_' + name.upper()]).value = value

@pytest.fixture
def stock_gapless():
    """Stock's Gapless byte (mclGetGaplessMode), on as stock's default, and put back after: conftest's map outlives
    a test."""
    byte = C.c_ubyte.from_address(O['MCL_GAPLESS'])
    saved, byte.value = byte.value, 1
    yield byte
    byte.value = saved


@pytest.fixture
def mcl():
    """Stock's player globals, zeroed for the test and put back after: conftest's map outlives a load."""
    start, size = O['MCL_POOL'], O['MCL_FD'] + 4 - O['MCL_POOL']
    claim(start)
    saved = C.string_at(start, size)
    C.memset(start, 0, size)
    yield Mcl()
    C.memmove(start, saved, size)


class Deques:
    """Stock's libcstl deques as lists; a handle is a number, as nothing here dereferences one. A context slot
    is 4 bytes on the host and P() reads 8, so its neighbour lands in a handle's high half: masked off."""
    def __init__(self, fw):
        self.lists = {}
        fw.mock('fw__create_deque', lambda *_: self.new())
        fw.mock('fw_deque_size', lambda q, *_: len(self[q]))
        fw.mock('fw_deque_at', lambda q, i, *_: self[q][i])
        fw.mock('fw__deque_push_back', lambda q, v, *_: self[q].append(v))
        fw.mock('fw_deque_clear', lambda q, *_: self[q].clear())
        fw.mock('fw_deque_assign', lambda q, other, *_: self.assign(q, other))
        fw.mock('fw_deque_init_copy', lambda q, other, *_: self.assign(q, other))
        fw.mock('fw_deque_destroy', lambda q, *_: self.lists.pop(q & 0xffffffff) and 0)

    def new(self, rows=()):
        handle = len(self.lists) + 0x1000 + sum(self.lists) % 7
        while handle in self.lists:
            handle += 1
        self.lists[handle] = list(rows)
        return handle

    def assign(self, q, other):
        self[q][:] = self[other]

    def __getitem__(self, q):
        return self.lists[q & 0xffffffff]


class Rows(Deques):
    """Deques whose stSongInfo rows are held as libcstl holds them: a push or an assign copies the row
    into the deque (stock's copy, 0x5c65ac; here its strings are shared), deque_at points at that copy,
    and a clear or a destroy frees it. live() counts the rows held, peak the most at once. A deque from
    new() holds values, as in Deques: the shuffle pool's, or rows the test owns."""
    def __init__(self, fw):
        super().__init__(fw)
        self.held, self.copies, self.peak = set(), {}, 0
        fw.mock('fw__create_deque', lambda name, *_: self.rows() if C.string_at(name) == b'stSongInfo' else self.new())
        fw.mock('fw__deque_push_back', lambda q, v, *_: self.push(q, v))
        fw.mock('fw_deque_clear', lambda q, *_: self.free(q))
        fw.mock('fw_deque_destroy', lambda q, *_: self.free(q) or self.lists.pop(q & 0xffffffff) and 0)

    def rows(self, records=()):
        handle = self.new()
        self.held.add(handle)
        for r in records:
            self.push(handle, r)
        return handle

    def push(self, q, v):
        if (q & 0xffffffff) in self.held:
            copy = C.create_string_buffer(C.string_at(v, O['REC_SIZE']), O['REC_SIZE'])
            v = C.addressof(copy)
            self.copies[v] = copy
            self.peak = max(self.peak, self.live())
        self[q].append(v)

    def free(self, q):
        if (q & 0xffffffff) in self.held:
            for v in self[q]:
                del self.copies[v]
        self[q].clear()

    def assign(self, q, other):
        rows = list(self[other])
        self.free(q)
        for v in rows:
            self.push(q, v)

    def live(self):
        return len(self.copies)


class Files:
    """The card's and flash's files as a dict: fopen and all, so blob_io and the snapshot's reader run whole; "a" appends."""
    def __init__(self, fw, files=None):
        self.files = files if files is not None else {}
        self.open = {}
        self.fail_rename = False
        fw.mock('fw_fopen', self.fopen)
        fw.mock('fw_fread', self.fread)
        fw.mock('fw_fseek', self.fseek)
        fw.mock('fw_ftell', lambda f, *_: self.open[f][1])
        fw.mock('fw_fwrite', self.fwrite)
        fw.mock('fw_fclose', self.fclose)
        fw.mock('fw_rename', self.rename)
        fw.mock('fw_unlink', lambda path, *_: self.files.pop(C.string_at(path).decode(), None) and 0)

    def fopen(self, path, mode, *_):
        path, mode = C.string_at(path).decode(), C.string_at(mode).decode()
        if 'r' in mode and path not in self.files:
            return 0
        handle = 0x2000 + len(self.open)
        while handle in self.open:  # one closed since leaves this count on a file still open
            handle += 1
        self.open[handle] = [path, 0, b'' if 'w' in mode else self.files.get(path, b'') if 'a' in mode else None]
        return handle

    def fread(self, out, size, count, f, *_):
        state = self.open[f]
        data = self.files[state[0]]
        have = max(0, min(count, (len(data) - state[1]) // size))
        C.memmove(out, data[state[1]:state[1] + have * size], have * size)
        state[1] += have * size
        return have

    def fseek(self, f, offset, whence, *_):
        state = self.open[f]
        offset = C.c_int(offset).value
        target = offset + {0: 0, 1: state[1], 2: len(self.files[state[0]])}[whence]
        if target < 0:
            return -1
        state[1] = target
        return 0

    def fwrite(self, data, size, count, f, *_):
        self.open[f][2] += C.string_at(data, size * count)
        return count

    def fclose(self, f, *_):
        path, _, written = self.open.pop(f)
        if written is not None:
            self.files[path] = written
        return 0

    def rename(self, old, new, *_):
        if self.fail_rename:
            return -1
        self.files[C.string_at(new).decode()] = self.files.pop(C.string_at(old).decode())
        return 0


class Card:
    """A card's files as a dict, written through: fopen creates the entry, fwrite appends to it, fclose is a flag.
    fail_write, fail_rename, fail_sync, fail_close and fail_ferror make those calls fail; unlinked lists the paths removed.
    access=False leaves fw_access to the test (games' listing asks the real one)."""
    def __init__(self, fw, files=None, access=True):
        self.files, self.open, self.unlinked = dict(files or {}), {}, []
        self.fail_write = self.fail_rename = self.fail_sync = self.fail_close = self.fail_ferror = False
        for name in 'fopen fread fwrite fseek ftell rename unlink'.split():
            fw.mock('fw_' + name, getattr(self, name))
        fw.mock('fw_fclose', lambda *_: self.fail_close)
        fw.mock('fw_fflush', lambda *_: 0)
        fw.mock('fw_fsync', lambda *_: self.fail_sync)
        fw.mock('fw_ferror', lambda *_: self.fail_ferror)
        if access:
            fw.mock('fw_access', lambda path, *_: self.name(path) not in self.files)

    def name(self, path):
        return C.string_at(path).decode()

    def fopen(self, path, mode, *_):
        name, write = self.name(path), C.string_at(mode)[:1] == b'w'
        if not write and name not in self.files:
            return 0
        handle = 0x1000 + len(self.open)
        self.open[handle] = [name, 0]
        if write:
            self.files[name] = b''
        return handle

    def fread(self, buf, size, n, f, *_):
        name, at = self.open[f]
        data = self.files[name][at:at + size * n]
        C.memmove(buf, data, len(data))
        self.open[f][1] += len(data)
        return len(data) // size

    def fwrite(self, buf, size, n, f, *_):
        if self.fail_write:
            return 0
        self.files[self.open[f][0]] += C.string_at(buf, size * n)
        return n

    def fseek(self, f, off, whence, *_):
        base = {0: 0, 1: self.open[f][1], 2: len(self.files[self.open[f][0]])}[whence]
        if base + off < 0:
            return -1
        self.open[f][1] = base + off
        return 0

    def ftell(self, f, *_):
        return self.open[f][1]

    def rename(self, a, b, *_):
        if self.fail_rename:
            return -1
        self.files[self.name(b)] = self.files.pop(self.name(a))
        return 0

    def unlink(self, path, *_):
        self.unlinked.append(self.name(path))
        self.files.pop(self.unlinked[-1], None)
        return 0

def favdir_card(fw, files, app='jf', folder='Jellyfin'):
    """A card app .<app> whose q2app names favdir=<folder> (addon.c addon_fav_path): its q2app in files (a Files
    dict, by str path) and the card's root listing it, as opendir and readdir (host.py's struct dirent: d_name at
    11) give it; tk_str_start_with as libc's."""
    files[f'/mnt/mmc/.{app}/q2app'] = f'name=Jellyfin\nfavdir={folder}\n'.encode()
    names, keep = [], []

    def opendir(path, *_):
        names[:] = [f'.{app}'.encode(), folder.encode()] if C.string_at(path) == b'/mnt/mmc' else []
        return 0x77 if names else 0

    def readdir(d, *_):
        if not names:
            return 0
        name = names.pop(0)
        ent = C.create_string_buffer(268)
        C.memmove(C.addressof(ent) + 11, name, len(name))
        keep.append(ent)
        return C.addressof(ent)
    fw.mock('fw_opendir', opendir)
    fw.mock('fw_readdir', readdir)
    fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))


class Game(C.Structure):
    """addon.c's gm: the running card game."""
    _fields_ = [*((k, C.c_int) for k in 'pid sock eat audio'.split()), *((k, C.c_uint) for k in 'seen held term'.split()),
                ('path', C.c_char * 48), ('swap', C.c_int), ('back', C.c_int), ('seek', C.c_int), ('dim', C.c_int), ('polled', C.c_uint), ('id', C.c_char * 16),
                *((k, C.c_int) for k in 'view down native stock screen'.split()), ('vreq', C.c_char * 8), ('vsent', C.c_char * 16)]


# Fixtures and fakes the navigation, playback and player tests share.
@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets as this build lays it out: its pointers move them."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    """navigation.c's state by field: st('rt_page', C.c_void_p).value."""
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


@pytest.fixture(scope='module')
def qm_fields(library):
    """queue_menu.c's own state, qm: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'queue_menu.o', 'qm')


@pytest.fixture
def qm(fw, qm_fields):
    """queue_menu.c's state by field: qm('qm_kind').value."""
    base = C.addressof(fw.var('queue_menu__qm', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + qm_fields[name])


@pytest.fixture(scope='module')
def power_fields(library):
    """system.c's own state, pw: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'system.o', 'pw')


@pytest.fixture
def power(fw, power_fields):
    base = C.addressof(fw.var('system__pw', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + power_fields[name])


@pytest.fixture(scope='module')
def lists_fields(library):
    """library_lists.c's own state, ll: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'library_lists.o', 'll')


@pytest.fixture
def lists(fw, lists_fields):
    base = C.addressof(fw.var('library_lists__ll', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + lists_fields[name])


def opus(tags):
    """An Ogg Opus file: OpusHead, then tags as packet 1 on pages of its own (no CRC: xiph.c reads none)."""
    pages, seq = [], 0
    for packet in (b'OpusHead' + bytes([1, 2]) + struct.pack('<HIhB', 312, 48000, 0, 0), tags):
        lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
        for at in range(0, len(lacing), 255):
            part = lacing[at:at + 255]
            pages.append(b'OggS\0' + bytes([at > 0]) + bytes(8) + struct.pack('<III', 1, seq, 0) + bytes([len(part)]) +
                         bytes(part) + packet[at * 255:at * 255 + sum(part)])
            seq += 1
    return b''.join(pages)


def stock_stub(address, result, log):
    """x86-64 at a fixed stock address: its first three arguments logged, result returned. Only ever in a fork."""
    claim(address)
    code = (b'\x48\xb8' + struct.pack('<Q', C.addressof(log)) + bytes.fromhex('488938 48897008 48895010') +
            b'\xb8' + struct.pack('<I', result) + b'\xc3')
    C.memmove(address, code, len(code))


def rgba(c, a=0xff):
    """0xRRGGBB at alpha a as a color_t, whose bytes are r, g, b, a."""
    return a << 24 | (c & 255) << 16 | c & 0xff00 | c >> 16 & 255


def channels_low_first(c):
    """A colour word's (c & 255, c >> 8 & 255, c >> 16 & 255): the byte order test_nav_paint's channels is not."""
    return c & 255, c >> 8 & 255, c >> 16 & 255


class Config:
    """config.ini, its value or the default."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v if isinstance(v, bytes) else str(v).encode() for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)
