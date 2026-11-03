#!/usr/bin/env python3
"""Exercise the shipped LDAC encoder's intermediate rates and bluealsa's Auto dispatch."""
import pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from build import machine, read, word
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import *

directory = pathlib.Path(sys.argv[1])
def image(name):
    return subprocess.check_output(['unsquashfs', '-cat', str(directory/'rootfs.squashfs'), name])

def call(u, address, *args):
    for register, value in zip((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3), args):
        u.reg_write(register, value & 0xffffffff)
    u.reg_write(UC_MIPS_REG_T9, address)
    u.reg_write(UC_MIPS_REG_SP, 0x200f000)
    u.reg_write(UC_MIPS_REG_RA, 0x200ff00)
    u.emu_start(address, 0x200ff00, count=10000)
    assert u.reg_read(UC_MIPS_REG_PC) == 0x200ff00
    return u.reg_read(UC_MIPS_REG_V0)

encoder = machine(image('usr/lib/libldacBT.so.2.0.19'), 0x2000000)
handle = 0x2001000
# An initialized stereo encoder. alter_eqmid changes the next frame's size without allocation.
for offset, value in ((4, 1), (0x14, 2), (0x28, 5), (0x58, 0)):
    word(encoder, handle+offset, value)
for expected in (1, 3, 4, 2):
    assert call(encoder, 0x217c, handle, -1) == 0
    assert read(encoder, handle+0x58) == expected
    # Each channel's bytes per 128 samples, including the three-byte frame header.
    assert (read(encoder, handle+0x60)+3)*48000*8//128//1000 == {1:660, 3:492, 4:396, 2:330}[expected]
assert call(encoder, 0x217c, handle, -1) == 0xffffffff
for expected in (4, 3, 1, 0):
    assert call(encoder, 0x217c, handle, 1) == 0
    assert read(encoder, handle+0x58) == expected

daemon = machine(image('usr/bin/bluealsa'), 0x2000000)
# SetLDACQuality converts the wire values 0x15/16/17/18 to 0/1/2/3.
for quality, eqmid, adaptive in ((0,0,0), (1,1,0), (2,2,0), (3,1,1)):
    word(daemon, 0x441a50, quality)
    call(daemon, 0x416388)
    assert (read(daemon, 0x4417a0), read(daemon, 0x441bf0)) == (eqmid, adaptive)

# Run the actual ABR library with its encoder callbacks delegated to the actual encoder above.
abr = machine(image('usr/lib/libldacBT_ABR.so.2.0.0'), 0x2000000)
state, history = 0x2002000, 0x2003000
calloc = read(abr, 0x18ea0-0x7fc8)
word(abr, state+8, 0x2000100)
word(abr, state+12, 0x2000104)
def hook(u, address, size, _):
    if address == 0x2000100:
        result = call(encoder, 0x217c, handle, u.reg_read(UC_MIPS_REG_A1))
    elif address == 0x2000104:
        result = read(encoder, handle+0x58)
    elif address == calloc:
        assert u.reg_read(UC_MIPS_REG_A0) == 13 and u.reg_read(UC_MIPS_REG_A1) == 1
        u.mem_write(history, bytes(13))
        result = history
    else:
        return
    u.reg_write(UC_MIPS_REG_V0, result)
    u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
abr.hook_add(UC_HOOK_CODE, hook)
# Initialize as the Q2 daemon does, with a 40 ms interval and thresholds 12/8/4.
assert call(abr, 0x8a0, state, 40) == 0
assert call(abr, 0x980, state, 12, 8, 4) == 0
assert call(abr, 0x9c4, handle, state, 5, 0) == 0  # fixed quality stays fixed
assert call(abr, 0x9c4, handle, state, 5, 1) == 1
assert call(abr, 0x9c4, handle, state, 5, 1) == 3  # 492 kbps, between Standard and Connection
seen = {3}
for _ in range(2000):
    seen.add(call(abr, 0x9c4, handle, state, 0, 1))
assert 0 in seen, seen  # a recovered link returns to HQ
print('Native LDAC Auto: 990/660/492/396/330 kbps, congestion reduction and HQ recovery passed.')
