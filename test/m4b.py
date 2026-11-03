#!/usr/bin/env python3
"""Exercise the installed TagLib's native MP4 dispatch, without decoding audio or flashing."""
import pathlib, struct, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import machine, fileoff, segments, TAG_C, patch_m4b_cover, read
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import *
raw = subprocess.check_output(['unsquashfs','-cat',str(pathlib.Path(sys.argv[1])/'stock.squashfs'),'usr/lib/libtag.so.2.0.1'])
# Pin the native resolver: M4A and M4B compare in the same chain and branch to MP4's constructor.
assert raw[fileoff(raw,0x115130):fileoff(raw,0x115130)+4] == b'M4B\0'
for ext in ('M4A','M4B','m4b','M4b','m4B','MP4'):
    u=machine(raw,0x2000000)
    gp=0x158590
    eq,alloc,mp4=[read(u,gp+n) for n in (-0x7c10,-0x69ac,-0x6e90)]
    assert eq==0xa44e8 and mp4==0xdcc9c
    seen=[]
    def hook(u,addr,size,_):
        if addr==eq:
            p=u.reg_read(UC_MIPS_REG_A1); b=bytes(u.mem_read(p,4)).split(b'\0')[0].decode()
            # FileRef normalizes the extension to uppercase before this dispatch chain.
            u.reg_write(UC_MIPS_REG_V0,int(ext.upper()==b))
        elif addr==alloc: u.reg_write(UC_MIPS_REG_V0,0x2000200)
        elif addr==mp4: seen.append(addr); u.emu_stop(); return
        else: return
        u.reg_write(UC_MIPS_REG_PC,u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE,hook)
    u.reg_write(UC_MIPS_REG_GP,gp); u.reg_write(UC_MIPS_REG_SP,0x200f000)
    u.reg_write(UC_MIPS_REG_S1,0x2000100); u.reg_write(UC_MIPS_REG_S2,0x2000300)
    u.mem_write(0x200f018,struct.pack('<I',gp))
    u.emu_start(0x10a7ac,0x10a3f4,count=200)
    assert seen==[mp4],ext
print('Installed TagLib: M4B and M4A reach the same MP4 metadata constructor.')

# Cover extraction has a separate vendor dispatcher; metadata support alone is insufficient.
raw = subprocess.check_output(['unsquashfs', '-cat', str(pathlib.Path(sys.argv[1])/'stock.squashfs'), TAG_C])
patched = patch_m4b_cover(raw)
try:
    patch_m4b_cover(raw[:-1])
    raise AssertionError('Accepted an unrecognized TagLib library')
except ValueError:
    pass
_, segment = segments(patched)[-1]
assert segment[0] == 1 and segment[2] == 0x30000 and segment[4:7] == (48, 48, 5)
# The dispatch table's GOT slots are pinned by the library hash in patch_m4b_cover.
gp = 0x327e0
slots = dict(mp3=-0x7d1c, m4a=-0x7cb8, wma=-0x7e24, wav=-0x7f38,
             ape=-0x7c9c, aiff=-0x7e7c, flac=-0x7d84, ogg=-0x7c88, dsf=-0x7f14)
helpers = {struct.unpack_from('<I', raw, fileoff(raw, gp+slot))[0]: name for name, slot in slots.items()}

def cover_dispatch(data, ext, load, readable=True):
    u = machine(data, 0x2000000, load)
    # Model the loader's rebasing of the GOT entries used by the dispatcher.
    for slot in (-0x7c64, -0x7da8, -0x7e90, -0x7fd8, *slots.values()):
        val = struct.unpack_from('<I', raw, fileoff(raw, gp+slot))[0]
        u.mem_write(load+gp+slot, struct.pack('<I', load+val))
    path, dst, stop = 0x2000100, 0x2000300, 0x2000800
    u.mem_write(path, ('/books/book.'+ext+'\0').encode())
    def string(p):
        b = bytearray()
        while u.mem_read(p+len(b), 1)[0]: b += u.mem_read(p+len(b), 1)
        return bytes(b)
    imports = {struct.unpack_from('<I', raw, fileoff(raw, gp+s))[0]: name
               for s, name in ((-0x7c64, 'access'), (-0x7da8, 'strrchr'), (-0x7e90, 'strcasecmp'))}
    seen = []
    def hook(u, addr, size, _):
        addr -= load
        if addr in helpers:
            seen.append(helpers[addr])
            assert [u.reg_read(r) for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1,
                    UC_MIPS_REG_A2, UC_MIPS_REG_A3)] == [path, dst, 0x100000, 0x600000]
            u.emu_stop(); return
        name = imports.get(addr)
        if not name: return
        a, b = u.reg_read(UC_MIPS_REG_A0), u.reg_read(UC_MIPS_REG_A1)
        if name == 'access': result = 0 if readable else -1
        elif name == 'strrchr':
            at = string(a).rfind(bytes([b])); result = a+at if at >= 0 else 0
        else: result = int(string(a).lower() != string(b).lower())
        u.reg_write(UC_MIPS_REG_V0, result & 0xffffffff)
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, hook)
    for reg, val in ((UC_MIPS_REG_A0, path), (UC_MIPS_REG_A1, dst),
                     (UC_MIPS_REG_A2, 0x100000), (UC_MIPS_REG_A3, 0x600000),
                     (UC_MIPS_REG_T9, load+0x12508), (UC_MIPS_REG_RA, stop),
                     (UC_MIPS_REG_SP, 0x200f000)):
        u.reg_write(reg, val)
    u.emu_start(load+0x12508, stop, count=500)
    return seen

for load in (0, 0x40000000):
    for ext in ('m4b', 'M4B', 'M4b', 'm4B'):
        assert cover_dispatch(raw, ext, load) == [], ext
        assert cover_dispatch(patched, ext, load) == ['m4a'], ext
        assert cover_dispatch(patched, ext, load, readable=False) == [], ext
    for ext in ('mp3', 'm4a', 'mp4', 'wma', 'wav', 'ape', 'aiff', 'aif', 'flac', 'ogg', 'dsf', 'm4', 'm4b.extra', 'unknown'):
        assert cover_dispatch(patched, ext, load) == cover_dispatch(raw, ext, load), ext
print('Cover dispatch: M4B uses the existing MP4 extractor; all original formats, read failures and relocated loading preserved.')
