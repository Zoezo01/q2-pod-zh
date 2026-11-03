#!/usr/bin/env python3
"""hciplayer's futex waits (patch/peq_player.c): the built player under Unicorn.

The command handler's wrapper wakes the stopped loop when a command set the play flag (set-songurl) and the
paused loop when one changed its state (mcl-play's resume), and wakes nothing for a status query; the
stopped loop waits on the flag while 0, the paused loop on cmd_gen only while no command moved it.
Usage: test/hci_wait.py BUILD_DIR"""
import pathlib, struct, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import jal, machine, read, symbols, word
from peq import CMD_DONE, IDLE_WAIT, PAUSE_WAIT
from unicorn import UC_HOOK_CODE, UC_HOOK_INTR
from unicorn.mips_const import *

out = pathlib.Path(sys.argv[1])/'peq'
raw = (out/'hciplayer').read_bytes()
ps = symbols(out/'peq.elf')
for (address, _), want in ((IDLE_WAIT, jal(ps['hci_idle_wait'])), (PAUSE_WAIT, jal(ps['hci_pause_wait'])),
                           (CMD_DONE, jal(ps['hci_command']))):
    assert struct.unpack_from('<I', raw, address-0x400000)[0] == want, hex(address)
FLAG, STACK, DONE, HANDLER, MPCTX, GEN = 0xb10e34, 0x2008000, 0x2000000, 0x42be20, 0x2001000, ps['cmd_gen']

def machine_with(handler=lambda u: None):
    """The built player with a stand-in command handler, its context, and syscalls recorded."""
    u = machine(raw, DONE)
    word(u, 0xaf9e30, MPCTX), word(u, MPCTX + 4, 2)  # paused
    seen = []
    def syscall(u, intno, unused):
        seen.append(tuple(u.reg_read(r) for r in (UC_MIPS_REG_V0, UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2)))
        u.reg_write(UC_MIPS_REG_V0, 0), u.reg_write(UC_MIPS_REG_7, 0)
    def stand_in(u, address, size, unused):
        handler(u)
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_INTR, syscall)
    u.hook_add(UC_HOOK_CODE, stand_in, begin=HANDLER, end=HANDLER)
    return u, seen
def call(u, fn, a0=0):
    u.reg_write(UC_MIPS_REG_29, STACK), u.reg_write(UC_MIPS_REG_31, DONE), u.reg_write(UC_MIPS_REG_4, a0)
    u.emu_start(ps[fn], DONE, count=10000)

# A status query changes nothing: no wake.
u, seen = machine_with()
call(u, 'hci_command', 0x2002000); assert seen == [] and read(u, GEN) == 0, seen
# set-songurl sets the play flag: both loops are woken.
u, seen = machine_with(lambda u: word(u, FLAG, 1))
call(u, 'hci_command'); assert seen == [(4238, GEN, 129, 1), (4238, FLAG, 129, 1)] and read(u, GEN) == 1, seen
# mcl-play only sets the context's state to playing: the paused loop is woken.
u, seen = machine_with(lambda u: word(u, MPCTX + 4, 1))
call(u, 'hci_command'); assert seen == [(4238, GEN, 129, 1)], seen
# Next and Stop while paused set only their flags: the paused loop is woken.
for address in (0xb10e6c, 0xb10e70):
    u, seen = machine_with(lambda u: word(u, address, 1))
    call(u, 'hci_command'); assert seen == [(4238, GEN, 129, 1)], (hex(address), seen)
# The command buffer reaches the handler.
got = []
u, seen = machine_with(lambda u: got.append(u.reg_read(UC_MIPS_REG_4)))
call(u, 'hci_command', 0x2002000); assert got == [0x2002000], got
# The stopped loop's wait: FUTEX_WAIT_PRIVATE on the flag while 0, with a timeout.
u, seen = machine_with()
call(u, 'hci_idle_wait', 10000); assert len(seen) == 1 and seen[0][:4] == (4238, FLAG, 128, 0), seen
# The pause loop: a wait while cmd_gen is as last seen; none once a command moved it.
u, seen = machine_with()
word(u, GEN, 5); call(u, 'hci_pause_wait')  # moved from the 0 last seen: no wait, 5 is seen
call(u, 'hci_pause_wait')                   # unchanged: waits while it is 5
word(u, GEN, 6); call(u, 'hci_pause_wait')  # a command while the loop checked: no wait
assert seen == [(4238, GEN, 128, 5)], seen
print('hciplayer futex waits: Play wakes the stopped loop, resume the paused one, status queries nothing.')
