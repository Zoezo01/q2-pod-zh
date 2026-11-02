"""Build the firmware's C for this machine, with no stock firmware: its real device code, not stand-ins.

Each patch/*.c compiles as it does for the Q2, without PEQ_HOST, against a generated peq_platform.h
and stock.h. A stock call or a libc call with effects outside the process (system, unlink, fork...)
becomes a stub that records the call and returns 0, or what a test's mock returns; only PURE libc is
real. So a test can't touch the card, the network or this machine.

The code runs 64-bit, so `long` and pointers are wider than on the Q2. Code that depends on those, or
on a stock widget's layout, stays with test/patch.py, which runs the MIPS build under emulation."""
import concurrent.futures
import ctypes
import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
import build as tools_build  # noqa: E402
import peq as tools_peq  # noqa: E402

PURE = set('memset memcpy memcmp memmove strcmp strncmp strlen strcasecmp strncasecmp strrchr strstr strcasestr strdup '
           'calloc realloc free snprintf __isoc99_sscanf qsort pow cos sin log sqrt atoi strtol'.split())
# Their own programs (boot, start, remote) or the player's payload, which test/peq.py builds -m32: its
# audio ABI checks need 32-bit pointers. flac_lpc.c is MIPS assembly.
SKIP = {'boot', 'start', 'remote', 'peq_player', 'mp3_gapless', 'flac_lpc', 'flac_sync', 'flac_output'}
SOURCES = [p for p in sorted((ROOT/'patch').glob('*.c')) if p.stem not in SKIP]
FLAGS = ['-O0', '-g', '-fno-builtin', '-ffreestanding', '-fPIC', '-fkeep-static-functions', '-fno-omit-frame-pointer',
         '-w', '-D__asm__(...)=']
STUB_ARGS = 6


def platform_header():
    lines = ['struct dirent { unsigned ino, off; unsigned short reclen; unsigned char d_type; char d_name[256]; };']
    for name, (ret, args) in tools_peq.LIBC.items():
        if name not in PURE:
            lines.append(f'#define {name} fw_{name}')
        lines.append(f'extern {ret} {name}({args});')
    lines += [f'extern {ret} {name}({args});' for name, (ret, args) in tools_build.FUNCTIONS.items() if name not in tools_peq.LIBC]
    lines += [f'extern volatile unsigned char {name};' for name in tools_build.GLOBALS]
    return '\n'.join(lines) + '\n'


def stock_header():
    lines = [f'#define RING_STEP {tools_build.RING_STEP}', '#define BOLD_RANGES {0x20,0x7e}',
             '#define TIMEZONE_NAMES {' + ','.join(json.dumps(zone) for _, zone in tools_build.TIMEZONES) + '}',
             '#define TIMEZONE_ORDER {' + ','.join(map(str, tools_build.TIMEZONE_ORDER)) + '}',
             '#define TIMEZONE_CITIES {' + ','.join(json.dumps(label.split(' ', 1)[1]) for label, _ in tools_build.TIMEZONES) + '}',
             '#define SETTINGS_ICON_NAMES "bt_adjvol\\0display_backlight\\0"', '#define ROCKBOX_FLAG "/tmp/q2pod-rockbox"',
             '#define STOCK_VERSION "V1.32"', f'#define Q2POD_VERSION "{tools_build.VERSION} Pod"', '#define Q2POD_TAG "1.0.0"',
             '#define Q2POD_REPO "owner/repo"', '#define Q2POD_UA "Q2Pod/test"']
    for name, size in tools_build.CONTEXT_DATA.items():
        lines += [f'extern unsigned char fw_ctx_{name}[{size}];', f'#define {name} ((const unsigned char *)fw_ctx_{name})']
    return '\n'.join(lines) + '\n'


def nm(path):
    return [line.split() for line in subprocess.run(['nm', str(path)], capture_output=True, text=True, check=True).stdout.splitlines()]


def stubs_source(names, data):
    """Each undefined function records its call in fw_log and asks its mock, if a test set one."""
    args = ', '.join(f'long a{i}' for i in range(STUB_ARGS))
    call = ', '.join(f'a{i}' for i in range(STUB_ARGS))
    out = ['struct fw_call { const char *name; long args[%d]; };' % STUB_ARGS, 'struct fw_call fw_log[8192]; int fw_log_n;',
           'static void note(const char *name, long *args) {', '    if (fw_log_n == 8192) return;',
           '    fw_log[fw_log_n].name = name;', '    for (int i = 0; i < %d; i++) fw_log[fw_log_n].args[i] = args[i];' % STUB_ARGS,
           '    fw_log_n++;', '}']
    for name in names:
        out += [f'long (*fw_mock_{name})({args});', f'long {name}({args}) {{',
                f'    long saved[] = {{ {call} }}; note("{name}", saved);',
                f'    return fw_mock_{name} ? fw_mock_{name}({call}) : 0;', '}']
    out += [f'unsigned char {name}[{size}];' for name, size in data.items()]
    return '\n'.join(out) + '\n'


def compile_one(source, out, extra):
    obj = out/(source.stem + '.o')
    subprocess.run(['cc', *FLAGS, *extra, '-I', str(out), '-I', str(ROOT/'patch'), '-c', str(source), '-o', str(obj)], check=True)
    return obj


def build(out, extra=()):
    """Compile everything into out/fw.so, extra going to cc (test/fw/conftest.py's coverage and sanitizers).
    Every static gains a global <file>__<name> a test can reach."""
    out.mkdir(parents=True, exist_ok=True)
    (out/'peq_platform.h').write_text(platform_header())
    (out/'stock.h').write_text(stock_header())
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        objects = list(pool.map(lambda s: compile_one(s, out, extra), SOURCES))
    defined, undefined = set(), set()
    for obj in objects:
        rows = nm(obj)
        defined |= {r[-1] for r in rows if len(r) == 3 and r[1] in 'TDBRVWC'}
        undefined |= {r[-1] for r in rows if len(r) == 2 and r[0] == 'U'}
        local = [r[-1] for r in rows if len(r) == 3 and r[1] in 'tdbr' and re.fullmatch(r'[A-Za-z_]\w*', r[-1])]
        renames = [f for name in local for f in ('--redefine-sym', f'{name}={obj.stem}__{name}', '--globalize-symbol', f'{obj.stem}__{name}')]
        subprocess.run(['objcopy', *renames, str(obj)], check=True)
    system = {'__stack_chk_fail', '_GLOBAL_OFFSET_TABLE_', *PURE}
    system |= {n for n in undefined if n.startswith(('__gcov', '__asan', '__ubsan', '__lsan'))}
    data = {f'fw_ctx_{name}': size for name, size in tools_build.CONTEXT_DATA.items()} | {name: 1 for name in tools_build.GLOBALS}
    names = sorted(undefined - defined - system - set(data))
    (out/'fw_stubs.c').write_text(stubs_source(names, data))
    stubs = out/'fw_stubs.o'
    subprocess.run(['cc', '-O1', '-fPIC', '-w', '-c', str(out/'fw_stubs.c'), '-o', str(stubs)], check=True)
    library = out/'fw.so'
    subprocess.run(['cc', '-shared', *extra, *map(str, objects), str(stubs), '-lm', '-o', str(library)], check=True)
    (out/'stubs.json').write_text(json.dumps(names))
    return library


class Fw:
    """A fresh load of the library: its globals start as the Q2's do, and nothing outlives the test."""
    def __init__(self, library, private):
        copy = private/'fw.so'
        shutil.copy(library, copy)
        self.lib = ctypes.CDLL(str(copy))
        self.keep = []

    def fn(self, name, restype, *argtypes):
        """A firmware function: `name`, or `file__name` for a static."""
        f = getattr(self.lib, name)
        f.restype, f.argtypes = restype, list(argtypes)
        return f

    def var(self, name, ctype):
        return ctype.in_dll(self.lib, name)

    def mock(self, name, fn):
        """Replace a stub: fn takes its six integer registers as Python ints and returns an int."""
        callback = ctypes.CFUNCTYPE(ctypes.c_long, *[ctypes.c_long] * STUB_ARGS)(lambda *a: int(fn(*a) or 0))
        self.keep.append(callback)
        pointer = ctypes.c_void_p.in_dll(self.lib, f'fw_mock_{name}')
        pointer.value = ctypes.cast(callback, ctypes.c_void_p).value

    def calls(self, name=None):
        """[(stub name, its six arguments)] in order, or just `name`'s."""
        count = ctypes.c_int.in_dll(self.lib, 'fw_log_n').value
        record = type('Call', (ctypes.Structure,), {'_fields_': [('name', ctypes.c_char_p), ('args', ctypes.c_long * STUB_ARGS)]})
        log = (record * 8192).in_dll(self.lib, 'fw_log')
        rows = [(log[i].name.decode(), tuple(log[i].args)) for i in range(count)]
        return [r for r in rows if name is None or r[0] == name]
