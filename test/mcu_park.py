#!/usr/bin/env python3
"""Host-only execution checks for the MCU park prototype.

These tests execute the actual patched image in Unicorn's flat virtual-address
translation mode. MMIO stores/reads are modeled as ordinary memory; this checks
instruction ordering, state preservation and interleavings, not real interrupt
delivery, bus completion, physical clock gating or current consumption.
"""
import argparse
from pathlib import Path
import hashlib
import json
import struct
import sys
import unittest

from unicorn import Uc, UcError, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_TLB_VIRTUAL
from unicorn import mips_const as mips

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import mcu_park as patch

STOCK = HELPER = PATCHED = None
SYMBOLS = {}
BASE = patch.MCU_BASE
REQ, ACK = BASE + patch.PROTOCOL_OFFSET, BASE + patch.PROTOCOL_OFFSET + 4
FLAG, WRITER, READER = BASE + 0x3F8C, BASE + 0x3CE8, BASE + 0x3CEC
MASK = 0x10001038
SP = BASE + 0x6B00


def reg(number):
    return getattr(mips, "UC_MIPS_REG_" + str(number))


class Machine:
    def __init__(self, data=None, mask=0, status=0x20400005):
        if data is None:
            data = PATCHED
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        self.uc.ctl_set_cpu_model(mips.UC_CPU_MIPS32_24KF)
        self.uc.ctl_set_tlb_mode(UC_TLB_VIRTUAL)
        self.uc.mem_map(BASE, 0x10000)
        self.uc.mem_map(0x10000000, 0x2000)
        self.uc.mem_write(BASE, data)
        for n in range(1, 32):
            self.uc.reg_write(reg(n), (0xABC00000 + n * 0x1001) & 0xFFFFFFFF)
        self.uc.reg_write(reg(18), BASE)  # original main's s2
        self.uc.reg_write(mips.UC_MIPS_REG_SP, SP)
        self.uc.reg_write(mips.UC_MIPS_REG_HI, 0x12345678)
        self.uc.reg_write(mips.UC_MIPS_REG_LO, 0xFEDCBA98)
        self.uc.reg_write(mips.UC_MIPS_REG_CP0_STATUS, status)
        self.write(MASK, mask)
        self.pc = BASE + patch.HOOK_OFFSET
        self.trace = []
        self.accesses = []
        self.on_code = None
        self.on_memory = None
        self.uc.hook_add(UC_HOOK_CODE, self.code)
        self.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.memory)

    def write(self, address, value):
        self.uc.mem_write(address, struct.pack("<I", value & 0xFFFFFFFF))

    def read(self, address):
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def state(self):
        return [self.uc.reg_read(reg(n)) for n in range(1, 32)] + [
            self.uc.reg_read(mips.UC_MIPS_REG_HI),
            self.uc.reg_read(mips.UC_MIPS_REG_LO),
            self.uc.reg_read(mips.UC_MIPS_REG_CP0_STATUS)]

    def code(self, uc, address, size, user):
        self.trace.append(address)
        if self.on_code:
            self.on_code(address)

    def memory(self, uc, access, address, size, value, user):
        self.accesses.append((access, address, size, value))
        if not (BASE <= address < BASE + 0x10000 or MASK <= address < MASK + 4):
            raise AssertionError(f"Unexpected MCU data access {address:#x}")
        if self.on_memory:
            self.on_memory(access, address, size, value)

    def run(self, count=1000, until=None, skip=0):
        old = self.on_code
        seen = 0
        stopped = False

        def check(address):
            nonlocal seen, stopped
            if old:
                old(address)
            if until is not None and address == until:
                if seen >= skip:
                    stopped = True
                    self.uc.emu_stop()
                seen += 1

        self.on_code = check
        try:
            self.uc.emu_start(self.pc, 0, count=count)
            self.pc = self.uc.reg_read(mips.UC_MIPS_REG_PC)
        finally:
            self.on_code = old
        if until is not None and not stopped:
            raise AssertionError(f"Did not reach {until:#x}; pc={self.pc:#x}")
        return self

    def park(self):
        self.write(REQ, 1)
        self.run(until=SYMBOLS["q2_pwm_parked"])
        return self

    def resume_command(self):
        self.write(FLAG, 1)
        self.write(REQ, 0)
        self.run(until=BASE + 0xFA4)
        return self


class ImageChecks(unittest.TestCase):
    def test_deterministic_and_stock_preservation(self):
        helper2, symbols2 = patch.compile_helper()
        self.assertEqual(HELPER, helper2)
        self.assertEqual(SYMBOLS, symbols2)
        self.assertEqual(PATCHED, patch.patch_image(STOCK, helper2))
        self.assertEqual(PATCHED[:patch.HOOK_OFFSET], STOCK[:patch.HOOK_OFFSET])
        self.assertEqual(PATCHED[patch.HOOK_OFFSET+8:len(STOCK)], STOCK[patch.HOOK_OFFSET+8:])
        self.assertEqual(len(PATCHED), patch.IMAGE_SIZE)
        self.assertLessEqual(len(PATCHED), 0x8000)
        self.assertEqual(PATCHED[len(STOCK):patch.HELPER_OFFSET],
                         bytes(patch.HELPER_OFFSET-len(STOCK)))
        self.assertEqual(PATCHED[patch.HELPER_OFFSET+len(HELPER):patch.PROTOCOL_OFFSET],
                         bytes(patch.PROTOCOL_OFFSET-patch.HELPER_OFFSET-len(HELPER)))

    def test_reject_modified_truncated_extended_double_patch(self):
        changed = bytearray(STOCK)
        changed[0] ^= 1
        for bad in [bytes(changed), STOCK[:-1], STOCK+b"\0", PATCHED]:
            with self.subTest(length=len(bad)):
                with self.assertRaises(ValueError):
                    patch.patch_image(bad, HELPER)

    def test_manifest_and_abi(self):
        data = patch.make_manifest(STOCK, PATCHED, HELPER, SYMBOLS)
        for area in data["immutable_ranges"]:
            self.assertEqual(area["sha256"], hashlib.sha256(
                PATCHED[area["start"]:area["end_exclusive"]]).hexdigest())
        self.assertEqual(struct.unpack_from("<IIII", PATCHED, patch.PROTOCOL_OFFSET),
                         (0, 0, patch.MAGIC, patch.VERSION))
        self.assertEqual(data["protocol"]["linux_req"], 0xB3429F00)


class InstructionChecks(unittest.TestCase):
    def test_stock_positive_control_and_register_parity(self):
        for status in (0, 1, 0x20400004, 0x20401005):
            with self.subTest(status=status):
                stock = Machine(STOCK, status=status)
                stock.write(FLAG, 1)
                stock.run(until=BASE+0xFA4)
                new = Machine(status=status)
                new.write(FLAG, 1)
                new.run(until=BASE+0xFA4)
                self.assertEqual(new.state(), stock.state())
                self.assertEqual(new.read(ACK), 0)
                self.assertFalse(any(a == MASK for _, a, _, _ in new.accesses))

    def test_idle_without_park_preserves_no_clock_protocol_side_effect(self):
        m = Machine()
        m.run(300)
        self.assertEqual(m.read(ACK), 0)
        self.assertEqual(m.read(MASK), 0)
        self.assertFalse(any(a == MASK for _, a, _, _ in m.accesses))
        self.assertNotIn(BASE+0xFA0, m.trace)

    def test_park_resume_prior_mask_and_interrupt_flags(self):
        for status in (0, 1, 0x20400004, 0x20401005):
            for mask in (0, 0x40, 0x10204080, 0x102040C0):
                with self.subTest(status=status, mask=mask):
                    m = Machine(mask=mask, status=status)
                    reference = Machine(STOCK, mask=mask, status=status)
                    reference.write(FLAG, 1)
                    reference.run(until=BASE+0xFA4)
                    m.park()
                    self.assertEqual(m.read(ACK), 1)
                    self.assertEqual(m.read(MASK), mask | 0x40)
                    self.assertEqual(m.uc.reg_read(mips.UC_MIPS_REG_CP0_STATUS), status)
                    m.resume_command()
                    self.assertEqual(m.read(ACK), 0)
                    self.assertEqual(m.read(MASK), mask)
                    self.assertEqual(m.state(), reference.state())

    def test_pending_command_precedes_park(self):
        m = Machine(mask=0x8000)
        m.write(REQ, 1)
        m.write(FLAG, 1)
        m.run(until=BASE+0xFA4)
        self.assertEqual(m.read(ACK), 0)
        self.assertEqual(m.read(MASK), 0x8000)
        self.assertFalse(any(a == MASK for _, a, _, _ in m.accesses))

    def test_committed_nonempty_ring_without_mailbox_is_serviced(self):
        m = Machine(mask=0x8000)
        m.write(REQ, 1)
        m.write(WRITER, 4)
        m.write(READER, 0)
        m.run(until=BASE+0xFA4)
        self.assertEqual(m.read(ACK), 0)
        self.assertEqual(m.read(MASK), 0x8000)
        self.assertFalse(any(a == MASK for _, a, _, _ in m.accesses))
        self.assertNotIn(SYMBOLS["q2_pwm_ack_park"], m.trace)

    def test_uncommitted_partial_ring_without_mailbox_is_not_serviced(self):
        m = Machine()
        m.write(REQ, 0)
        m.write(WRITER, 4)
        m.run(300)
        self.assertEqual(m.read(ACK), 0)
        self.assertNotIn(BASE+0xFA0, m.trace)
        self.assertFalse(any(a == MASK for _, a, _, _ in m.accesses))

    def test_races_after_mask_are_rechecked(self):
        for race in ("flag", "ring", "request_cancel"):
            with self.subTest(race=race):
                m = Machine(mask=0x8000)
                m.write(REQ, 1)
                fired = False
                def inject(access, address, size, value):
                    nonlocal fired
                    from unicorn import UC_MEM_WRITE
                    if not fired and access == UC_MEM_WRITE and address == MASK:
                        fired = True
                        if race == "flag":
                            m.write(FLAG, 1)
                        elif race == "ring":
                            m.write(WRITER, 4)
                        else:
                            m.write(REQ, 0)
                m.on_memory = inject
                m.run(until=SYMBOLS["q2_pwm_ack_run"])
                self.assertTrue(fired)
                self.assertEqual(m.read(ACK), 0)
                self.assertEqual(m.read(MASK), 0x8000)
                self.assertNotIn(SYMBOLS["q2_pwm_ack_park"], m.trace)

    def test_park_is_sticky_despite_mailbox_interrupt(self):
        m = Machine().park()
        m.write(FLAG, 1)
        m.write(WRITER, 4)
        m.run(300)
        self.assertEqual(m.read(ACK), 1)
        self.assertEqual(m.read(MASK) & 0x40, 0x40)
        self.assertNotIn(BASE+0xFA0, m.trace)
        m.resume_command()
        self.assertEqual(m.read(ACK), 0)
        self.assertEqual(m.read(MASK), 0)

    def test_resume_preserves_other_mask_changes(self):
        m = Machine(mask=0x1000).park()
        m.write(MASK, m.read(MASK) | 0x8000)
        m.resume_command()
        self.assertEqual(m.read(MASK), 0x9000)

    def test_ack_store_order(self):
        from unicorn import UC_MEM_WRITE
        m = Machine()
        events = []
        def observe(access, address, size, value):
            if access == UC_MEM_WRITE and address == ACK:
                events.append((value, m.read(MASK),
                               m.uc.reg_read(mips.UC_MIPS_REG_CP0_STATUS)))
        m.on_memory = observe
        m.park()
        m.resume_command()
        self.assertEqual(events[0][0:2], (1, 0x40))
        self.assertEqual(events[1], (0, 0, 0x20400005))


def main():
    global STOCK, HELPER, PATCHED, SYMBOLS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", type=Path, help="Exact captured libmcu-bare.bin")
    parser.add_argument("--json", type=Path, help="Optional machine-readable result")
    args = parser.parse_args()
    STOCK = args.stock.read_bytes()
    HELPER, SYMBOLS = patch.compile_helper()
    PATCHED = patch.patch_image(STOCK, HELPER)
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "status": "PASS" if result.wasSuccessful() else "FAIL",
        "test_methods": result.testsRun,
        "stock_sha256": patch.sha256(STOCK),
        "patched_sha256": patch.sha256(PATCHED),
        "helper_bytes": len(HELPER),
        "execution": "Actual patched instructions; continuous Unicorn MIPS32 execution with UC_TLB_VIRTUAL",
        "limitations": [
            "Flat virtual address translation models the MCU memory map",
            "MMIO is modeled memory; actual interrupt delivery and bus posting are not simulated",
            "No physical clock gating or current measurement",
            "Paired Linux module integration is tested separately",
        ],
    }
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
