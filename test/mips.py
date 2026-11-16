"""The MIPS harness test/*.py's Unicorn runners share: an ELF's LOAD pages, a machine, words, a call, PLT stubs."""
import pathlib
import re
import struct
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import segments  # noqa: E402
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN  # noqa: E402
from unicorn.mips_const import (UC_CPU_MIPS32_24KF, UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2,  # noqa: E402
                                UC_MIPS_REG_A3, UC_MIPS_REG_CP0_STATUS, UC_MIPS_REG_RA, UC_MIPS_REG_SP, UC_MIPS_REG_V0)

# The player runners' (xfade_player.py, bt_resample.py) return address, import stubs, heap and stack top.
DONE, STUBS, HEAP, STACK = 0x2000000, 0x2800000, 0x3000000, 0x40f0000


def load_elf(u, *images, load=0):
    """Map the images' LOAD pages once, shifted by load, then copy each segment's exact file bytes.

    A linker script may place text, rodata and data in separate LOAD segments sharing their boundary
    page. Mapping every segment independently rejects that valid ELF; skipping its mem_write would
    silently lose content.
    """
    pages, loads = set(), []
    for image in images:
        for _, (kind, off, base, _, filesz, memsz, _, _) in segments(image):
            if kind != 1 or not memsz:
                continue
            base += load
            assert filesz <= memsz and off + filesz <= len(image)
            assert all(base + memsz <= a or a + n <= base for a, n, _ in loads), 'overlapping LOAD data'
            pages.update(range(base & ~4095, (base + memsz + 4095) & ~4095, 4096))
            loads.append((base, memsz, image[off:off + filesz]))
    ordered = sorted(pages)
    start = end = ordered[0]
    for page in ordered[1:] + [ordered[-1] + 8192]:
        if page != end + 4096:
            u.mem_map(start, end + 4096 - start)
            start = page
        end = page
    for base, _, data in loads:
        u.mem_write(base, data)  # Fresh mappings already zero each segment's BSS.


def machine(raw, scratch, load=0):
    """A little-endian MIPS Unicorn with raw's LOAD segments (at +load) and 64 KB of scratch at scratch."""
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    load_elf(u, raw, load=load)
    u.mem_map(scratch, 0x10000)
    return u


def word(u, address, value): u.mem_write(address, struct.pack('<I', value & 0xffffffff))
def read(u, address): return struct.unpack('<I', u.mem_read(address, 4))[0]


def player(raw):
    """The built hciplayer on a 24KF with its FPU on (CU1, FR), and DONE, STUBS, HEAP and the stack mapped."""
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    u.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
    load_elf(u, raw)
    for base, size in ((DONE, 0x10000), (STUBS, 0x1000), (HEAP, 0x1000000), (STACK - 0xf0000, 0x100000)):
        u.mem_map(base, size)
    u.reg_write(UC_MIPS_REG_CP0_STATUS, u.reg_read(UC_MIPS_REG_CP0_STATUS) | 1 << 29 | 1 << 26)  # CU1, FR
    return u


def call(u, fn, *args):
    """fn(*args) on player()'s stack, returning to DONE: its v0."""
    for i, value in enumerate(args): u.reg_write((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)[i], value)
    u.reg_write(UC_MIPS_REG_SP, STACK), u.reg_write(UC_MIPS_REG_RA, DONE)
    u.emu_start(fn, DONE, count=100_000_000)
    return u.reg_read(UC_MIPS_REG_V0)


def peq_lib(imports_s):
    """Each peq_lib_* stub's GOT word in the build's peq_imports.S: {name: address}."""
    return {name: int(hi) << 16 | int(lo) for name, hi, lo in
            re.findall(r'peq_lib_(\w+):\nlui \$gp.*\nori \$gp.*\nlui \$t8, (\d+)\nori \$t8, \$t8, (\d+)', imports_s)}


def stubs(u, binary, base):
    """Each R_MIPS_JUMP_SLOT of binary (a path) bound to base + 4i, the way the loader binds it to libc: {base + 4i: name}."""
    names = {}
    for line in subprocess.check_output(['readelf', '-rW', binary], text=True).splitlines():
        s = line.split()
        if len(s) >= 5 and s[2] == 'R_MIPS_JUMP_SLOT':
            names[base + 4*len(names)] = s[4].split('@')[0]
            u.mem_write(int(s[0], 16), struct.pack('<I', base + 4*(len(names)-1)))
    return names
