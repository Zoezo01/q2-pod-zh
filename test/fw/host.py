"""Build the firmware's C for this machine, with no stock firmware: its real device code, not stand-ins.

navigation.c and tools/peq.py's UI_SOURCES compile as they do for the Q2, without PEQ_HOST, against the
stock.h and themes.inc tools/build.py writes and a peq_platform.h laid out as tools/peq.py's: each stock
function a macro calling its address, here a stub's. A stock call or a libc call with effects outside the
process (system, unlink, fork...) reaches a stub that records the call and returns 0, or what a test's
mock returns; only PURE libc is real. So a test can't touch the card, the network or this machine.

The code runs 64-bit, so `long` and pointers are wider than on the Q2. Code that depends on those, or
on a stock widget's layout, stays with test/patch.py, which runs the MIPS build under emulation."""
import _ctypes
import concurrent.futures
import ctypes
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
import build as tools_build  # noqa: E402
import peq as tools_peq  # noqa: E402
import theme as tools_theme  # noqa: E402

PURE = set('memset memcpy memcmp memmove strcmp strncmp strlen strcasecmp strncasecmp strrchr strstr strcasestr strdup '
           'calloc realloc free snprintf __isoc99_sscanf qsort pow cos sin log sqrt atoi strtol'.split())
SOURCES = [ROOT/'patch'/name for name in ['navigation.c', *tools_peq.UI_SOURCES]]
# GCC 14 makes these two errors anyway; the rest is the 64-bit build's noise (pointer <-> int casts of
# stock's 32-bit addresses) or style the Q2's clang build already checks.
FLAGS = ['-O0', '-g', '-fno-builtin', '-ffreestanding', '-fPIC', '-fkeep-static-functions', '-fno-omit-frame-pointer',
         '-Werror=implicit-function-declaration', '-Werror=int-conversion', '-Wno-int-to-pointer-cast',
         '-Wno-pointer-to-int-cast', '-Wno-builtin-declaration-mismatch', '-Wno-incompatible-pointer-types',
         '-Wno-format', '-Wno-attributes', '-D__asm__(...)=']
STUB_ARGS = 6
# Stock's context data as host arrays; a 4-byte cell holds a pointer (P(x, 0) reads one), 8 bytes here.
CONTEXT = {name: max(size, 8) for name, size in tools_build.CONTEXT_DATA.items()}
FLOATS = ('float', 'double')


def returns():
    """Each stock and stubbed libc call's C return type, by the name its stub has."""
    types = {name if name in PURE else f'fw_{name}': ret for name, (ret, _) in tools_peq.LIBC.items()}
    return types | {name: ret for name, (ret, _) in tools_build.FUNCTIONS.items()}


def platform_header():
    lines = ['struct dirent { unsigned ino, off; unsigned short reclen; unsigned char d_type; char d_name[256]; };']
    for name, (ret, args) in tools_peq.LIBC.items():
        if name not in PURE:
            lines.append(f'#define {name} fw_{name}')
        lines.append(f'extern {ret} {name}({args});')
    # Stock's functions as tools/peq.py lays them out, so peq.h's Light Mode wrappers compile here too:
    # an address cast to the function's type, here its stub's (a char array to GCC, so no call is
    # "through a non-compatible type").
    lines += [f'extern char {name}[];' for name in tools_build.FUNCTIONS]
    lines += tools_peq.function_macros({name: name for name in tools_build.FUNCTIONS})
    lines += [f'extern volatile unsigned char {name};' for name in tools_build.GLOBALS]
    return '\n'.join(lines) + '\n'


def stock_header(themes):
    lines = [f'extern unsigned char fw_ctx_{name}[{size}];' for name, size in CONTEXT.items()]
    ranges = [tools_build.bold_font()[1], tools_build.classic_light_font()[1]]
    context = {name: f'fw_ctx_{name}' for name in tools_build.CONTEXT_DATA}
    return '\n'.join(lines) + '\n' + tools_build.stock_header(context, ranges, themes, repo='owner/repo')


def nm(path, *flags):
    return [line.split() for line in subprocess.run(['nm', *flags, str(path)], capture_output=True, text=True, check=True).stdout.splitlines()]


def stubs_source(names, data):
    """Each undefined function records its call in fw_log and asks its mock, if a test set one; a
    float or double comes back in the float register, as the caller reads it."""
    types = returns()
    args = ', '.join(f'long a{i}' for i in range(STUB_ARGS))
    call = ', '.join(f'a{i}' for i in range(STUB_ARGS))
    out = ['struct fw_call { const char *name; long args[%d]; int mocked; };' % STUB_ARGS,
           'struct fw_call fw_log[8192]; int fw_log_n;',
           'static void note(const char *name, long *args, int mocked) {', '    if (fw_log_n == 8192) return;',
           '    fw_log[fw_log_n].name = name;', '    for (int i = 0; i < %d; i++) fw_log[fw_log_n].args[i] = args[i];' % STUB_ARGS,
           '    fw_log[fw_log_n++].mocked = mocked;', '}']
    # Two stock calls whose answer every caller reads get their libc meaning unless mocked.
    out += ['#include <stdarg.h>', '#include <stdio.h>', '#include <string.h>',
            f'long (*fw_mock_tk_snprintf)({args});', 'int tk_snprintf(char *s, unsigned n, const char *f, ...) {',
            '    va_list ap, rest; va_start(ap, f); va_copy(rest, ap);',
            '    long saved[] = { (long)s, n, (long)f, va_arg(rest, long), va_arg(rest, long), va_arg(rest, long) }; va_end(rest);',
            '    note("tk_snprintf", saved, fw_mock_tk_snprintf != 0);',
            '    int r = fw_mock_tk_snprintf ? fw_mock_tk_snprintf(saved[0], saved[1], saved[2], saved[3], saved[4], saved[5]) : vsnprintf(s, n, f, ap);',
            '    va_end(ap); return r;', '}',
            f'long (*fw_mock_tk_strcmp)({args});', f'long tk_strcmp({args}) {{',
            f'    long saved[] = {{ {call} }}; note("tk_strcmp", saved, fw_mock_tk_strcmp != 0);',
            f'    if (fw_mock_tk_strcmp) return fw_mock_tk_strcmp({call});',
            '    return a0 && a1 ? strcmp((const char *)a0, (const char *)a1) : a0 != a1;', '}']
    for name in names:
        if name in ('tk_snprintf', 'tk_strcmp'):
            continue
        ret = types.get(name, 'long')
        kind = ret if ret in FLOATS else 'long'
        out += [f'{kind} (*fw_mock_{name})({args});', f'{"void" if ret == "void" else kind} {name}({args}) {{',
                f'    long saved[] = {{ {call} }}; note("{name}", saved, fw_mock_{name} != 0);',
                f'    {"" if ret == "void" else "return "}fw_mock_{name} ? fw_mock_{name}({call}) : 0;', '}']
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
    themes = tools_theme.builtins()
    (out/'peq_platform.h').write_text(platform_header())
    (out/'stock.h').write_text(stock_header(themes))
    tools_theme.emit(themes, out)
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
    data = {f'fw_ctx_{name}': size for name, size in CONTEXT.items()} | {name: 1 for name in tools_build.GLOBALS}
    names = sorted(undefined - defined - system - set(data))
    (out/'fw_stubs.c').write_text(stubs_source(names, data))
    stubs = out/'fw_stubs.o'
    subprocess.run(['cc', '-O1', '-fPIC', '-w', '-c', str(out/'fw_stubs.c'), '-o', str(stubs)], check=True)
    library = out/'fw.so'
    subprocess.run(['cc', '-shared', *extra, *map(str, objects), str(stubs), '-lm', '-o', str(library)], check=True)
    types = returns()
    (out/'stubs.json').write_text(json.dumps({name: types.get(name) for name in names}))
    sizes = {r[-1]: int(r[1], 16) for r in nm(library, '-S', '--defined-only') if len(r) == 4}
    (out/'sizes.json').write_text(json.dumps(sizes))
    return library


def offsets(obj, type_name):
    """A struct typedef's field offsets as this build lays it out (its pointers move them), from obj's
    debug info: {field: offset}."""
    dump = subprocess.run(['readelf', '--debug-dump=info', str(obj)], capture_output=True, text=True, check=True).stdout
    kind = tools_build.need(re.search(rf': {type_name}\n(?:.*\n)*?\s+<\w+>\s+DW_AT_type\s*: <0x(\w+)>', dump), f'No {type_name} in {obj}')[1]
    body = dump[dump.index(f' <1><{kind}>:'):]
    body = body[:body.index('\n <1>')]
    found = {}
    for chunk in body.split('\n <2>')[1:]:
        name = re.search(r'DW_AT_name\s*:\s*(?:\(indirect string[^)]*\): )?(\w+)', chunk)
        where = re.search(r'DW_AT_data_member_location\s*:\s*(\d+)', chunk)
        if name and where:
            found[name[1]] = int(where[1])
    return found


class Fw:
    """A fresh load of the library: its globals start as the Q2's do, and nothing outlives the test.
    close() unloads it; its private copy leaves the disk as soon as it's loaded."""
    def __init__(self, library, private):
        copy = private/'fw.so'
        shutil.copy(library, copy)
        self.lib = ctypes.CDLL(str(copy))
        os.unlink(copy)
        self.keep = []
        self.stubs = json.loads((library.parent/'stubs.json').read_text())
        self.sizes = json.loads((library.parent/'sizes.json').read_text())

    def close(self):
        if 'lib' in vars(self):  # once; fw.lib goes with the library
            _ctypes.dlclose(self.lib._handle)
            del self.lib

    def fn(self, name, restype, *argtypes):
        """A firmware function: `name`, or `file__name` for a static."""
        f = getattr(self.lib, name)
        f.restype, f.argtypes = restype, list(argtypes)
        return f

    def var(self, name, ctype):
        """A firmware global as ctype: a struct mirror (or an array of them) must be the symbol's whole
        size, so a C struct that grew a field fails here, not as wrong offsets; a scalar or an array of
        them may view its start."""
        size = self.sizes.get(name)
        if size is not None:
            item = ctype._type_ if issubclass(ctype, ctypes.Array) else ctype
            whole = issubclass(item, (ctypes.Structure, ctypes.Union))
            assert ctypes.sizeof(ctype) == size if whole else ctypes.sizeof(ctype) <= size, \
                f'{name} is {size} bytes, {ctype.__name__} {ctypes.sizeof(ctype)}: the C changed, update the mirror'
        return ctype.in_dll(self.lib, name)

    def mock(self, name, fn):
        """Replace a stub: fn takes its six integer registers as Python ints and returns an int (a
        float for a stub returning one)."""
        kind = {'double': ctypes.c_double, 'float': ctypes.c_float}.get(self.stubs.get(name), ctypes.c_long)
        cast = int if kind is ctypes.c_long else float
        callback = ctypes.CFUNCTYPE(kind, *[ctypes.c_long] * STUB_ARGS)(lambda *a: cast(fn(*a) or 0))
        self.keep.append(callback)
        pointer = ctypes.c_void_p.in_dll(self.lib, f'fw_mock_{name}')
        pointer.value = ctypes.cast(callback, ctypes.c_void_p).value

    def log(self):
        count = ctypes.c_int.in_dll(self.lib, 'fw_log_n').value
        class Call(ctypes.Structure):
            _fields_ = [('name', ctypes.c_char_p), ('args', ctypes.c_long * STUB_ARGS), ('mocked', ctypes.c_int)]
        return (Call * 8192).in_dll(self.lib, 'fw_log')[:count]

    def calls(self, name=None):
        """[(stub name, its six arguments)] in order, or just `name`'s."""
        rows = [(r.name.decode(), tuple(r.args)) for r in self.log()]
        return [r for r in rows if name is None or r[0] == name]

    def unknown(self):
        """The stubs this test reached unmocked that tools/build.py and tools/peq.py don't declare: their
        return type is unknown, so their 0 is a guess (conftest.py's strict mode)."""
        return sorted({r.name.decode() for r in self.log() if not r.mocked and self.stubs.get(r.name.decode()) is None})
