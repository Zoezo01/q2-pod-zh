"""The firmware's C as one host library, freshly loaded for each test (host.py).

Q2_FW_SANITIZE=1 builds it with AddressSanitizer and UBSan: run pytest with LD_PRELOAD set to
`cc -print-file-name=libasan.so` and ASAN_OPTIONS=detect_leaks=0. Q2_FW_COVERAGE=1 builds it for gcov.
Q2_FW_STRICT=1 fails a test that reached a stub tools/ doesn't declare, unmocked (host.Fw.unknown):
its 0 is a guess at what stock would answer."""
import ctypes
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import host  # noqa: E402

# The C uses stock's fixed addresses as they are: zeroed pages here. A non-PIE python (Debian's and
# Ubuntu's own) has its code and data there, which the C would overwrite.
libc = ctypes.CDLL(None)
libc.mmap.restype, libc.mmap.argtypes = ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
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
    yield loaded
    unknown = loaded.unknown()
    loaded.close()
    if unknown and os.environ.get('Q2_FW_STRICT'):
        pytest.fail(f'unmocked stubs with no known return type: {", ".join(unknown)}')


def text(value):
    """A C string the firmware may write into: mutable, NUL-terminated, kept alive by the caller."""
    return ctypes.create_string_buffer(value if isinstance(value, bytes) else value.encode())
