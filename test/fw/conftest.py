"""The firmware's C as one host library, freshly loaded for each test (host.py).

Q2_FW_SANITIZE=1 builds it with AddressSanitizer and UBSan: run pytest with LD_PRELOAD set to
`cc -print-file-name=libasan.so` and ASAN_OPTIONS=detect_leaks=0. Q2_FW_COVERAGE=1 builds it for gcov."""
import ctypes
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import host  # noqa: E402

# The C uses stock's fixed addresses as they are. A position-independent python leaves them free, so
# they're zeroed pages; a non-PIE one (Ubuntu's, at 0x400000) holds them itself, and test_playback.py's
# claim() and in_fork() deal with that: nothing here may write there unguarded.
libc = ctypes.CDLL(None)
libc.mmap.restype, libc.mmap.argtypes = ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.mmap(0x400000, 0x800000, 3, 0x100022, -1, 0)  # read/write, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE


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
    return host.Fw(library, tmp_path)


def text(value):
    """A C string the firmware may write into: mutable, NUL-terminated, kept alive by the caller."""
    return ctypes.create_string_buffer(value if isinstance(value, bytes) else value.encode())
