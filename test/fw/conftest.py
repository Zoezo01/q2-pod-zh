"""The firmware's C as one host library, freshly loaded for each test (host.py).

Q2_FW_SANITIZE=1 builds it with AddressSanitizer and UBSan: run pytest with LD_PRELOAD set to
`cc -print-file-name=libasan.so` and ASAN_OPTIONS=detect_leaks=0. Q2_FW_COVERAGE=1 builds it for gcov.
Q2_FW_STRICT=1 fails a test that reached a stub tools/ doesn't declare, unmocked (host.Fw.unknown):
its 0 is a guess at what stock would answer."""
import ctypes as C
import functools
import os
import pathlib
import re
import sys
import traceback

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import host  # noqa: E402

ROOT = host.ROOT
O = {k: int(v, 0) for k, v in re.findall(r'^#define (\w+) +(0x[0-9a-f]+|\d+)\b', (ROOT/'patch/offsets.inc').read_text(), re.M)}
FNV_SEED, FNV_PRIME = 2166136261, 16777619

# The C uses stock's fixed addresses as they are: zeroed pages here. A non-PIE python (Debian's and
# Ubuntu's own) has its code and data there, which the C would overwrite.
libc = C.CDLL(None)
libc.mmap.restype, libc.mmap.argtypes = C.c_void_p, [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
libc.mprotect.argtypes = [C.c_void_p, C.c_size_t, C.c_int]
if libc.mmap(0x400000, 0x800000, 3, 0x100022, -1, 0) != 0x400000:  # read/write, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
    pytest.exit('test/fw needs a position-independent python: 0x400000-0xc00000, stock\'s addresses, is taken '
                            '(a non-PIE python lives there); use one built with -pie, e.g. actions/setup-python\'s or a venv of a PIE build', returncode=4)


@pytest.fixture(scope='session')
def library(tmp_path_factory):
    extra = []
    if os.environ.get('Q2_FW_SANITIZE'):
        extra += ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined', '-fno-sanitize=alignment']
    if os.environ.get('Q2_FW_COVERAGE'):
        extra += ['--coverage']
    return host.build(tmp_path_factory.mktemp('fw'), extra)


@pytest.fixture
def fw(library, tmp_path):
    loaded = host.Fw(library, tmp_path)
    # page_list sizes its rows by its title label's fields (I(t, W_H)): a zeroed widget to read
    label = C.create_string_buffer(0x400)
    loaded.mock('hscroll_label_create', lambda *_: C.addressof(label))
    yield loaded
    unknown = loaded.unknown()
    loaded.close()
    if unknown and os.environ.get('Q2_FW_STRICT'):
        pytest.fail(f'unmocked stubs with no known return type: {", ".join(unknown)}')


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

def in_fork(test):
    """The test in a child: what it writes at stock's code addresses would outlive the test here."""
    @functools.wraps(test)
    def run(*args, **kwargs):
        read, write = os.pipe()
        child = os.fork()
        if not child:
            try:
                test(*args, **kwargs)
                os.write(write, b'')
            except BaseException:
                os.write(write, traceback.format_exc().encode())
                os._exit(1)
            os._exit(0)
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

class Files:
    """The card's and flash's files as a dict: fopen and all, so blob_io and the snapshot's reader run whole."""
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
        self.open[handle] = [path, 0, b'' if 'w' in mode else None]
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
