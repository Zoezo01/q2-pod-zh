#!/usr/bin/env python3
"""Run actual linked MCU host helpers with explicit modeled sleep/wake delivery."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('q2_mcu_host', ROOT / 'test/pwm_mcu.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
sys.path.insert(0, str(ROOT / 'tools'))
import mcu_park
import pwm

REQ, ACK, WAKE, SFTINT = base.REQ, base.ACK, 0xb3429f10, 0xb00000bc


class Machine(base.Machine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.uc.mem_map(0xb0000000, 0x1000)
        self.wake_mode = 'normal'
        self.wake_sleeps = 0
        self.kicks = 0
        self.early_ack_seen = False

    def memory(self, u, access, addr, size, value, user):
        super().memory(u, access, addr, size, value, user)
        if access != base.UC_MEM_WRITE:
            return
        if addr in (WAKE, SFTINT):
            self.events.append(('protocol', addr, value))
        if addr == WAKE and value == 1:
            assert all(v[2] for v in self.clocks.values()), 'wake token before clocks'
            self.wake_sleeps = 0
        if addr == REQ and value == 0 and self.value('q2_mcu_known'):
            assert self.read(WAKE) == 1, 'REQ released before wake token'
        if addr == SFTINT and value == 1:
            assert self.read(REQ) == 0 and self.read(WAKE) == 1
            self.kicks += 1

    def call(self, addr):
        name = self.handlers[addr]
        if name == 'usleep_range' and self.pending_ack == 0 and self.read(WAKE):
            self.wake_sleeps += 1
            if self.wake_mode == 'early_ack' and self.wake_sleeps == 1:
                self.write(ACK, 0)
                self.early_ack_seen = True
                self.write(SFTINT, 0)
            elif self.wake_mode == 'lost_first' and self.wake_sleeps == 1:
                self.write(SFTINT, 0)
            elif self.wake_mode != 'no_irq' and self.read(SFTINT):
                # Model the exact wake-only IRQ's required acknowledgement order.
                self.write(SFTINT, 0)
                self.write(WAKE, 0)
                self.events.append(('wake_irq_completed',))
            old_mode = self.ack_mode
            if self.read(WAKE):
                self.ack_mode = 'timeout'
            result = super().call(addr)
            self.ack_mode = old_mode
            return result
        if name == 'stock_mcu_write_data' and self.value('q2_mcu_known'):
            assert not self.read(WAKE), 'command published before wake IRQ acknowledgement'
            assert self.read(ACK) == 0 and self.read(REQ) == 0
            assert not self.value('q2_mcu_fault')
        return super().call(addr)


def checks(module, image, address):
    cases = 0
    def fresh():
        m = Machine(module, image, address)
        m.init()
        m.boot()
        m.off()
        return m

    for mode in ('normal', 'early_ack', 'lost_first'):
        m = fresh()
        m.wake_mode = mode
        for _ in range(4):
            assert m.run('q2_mcu_write_data', 0x02001000, 7, 100, 0) == 7
            m.off()
            assert m.read(REQ) == m.read(ACK) == 1 and m.read(WAKE) == 0
            cases += 1
        if mode == 'early_ack':
            assert m.early_ack_seen and m.wake_sleeps >= 2
        if mode == 'lost_first':
            assert m.kicks >= 8

    # An already-set interrupt latch must not be cleared or mistaken for an ACK.
    m = fresh()
    m.write(SFTINT, 1)
    assert m.run('q2_mcu_write_data', 0, 7, 100, 0) == 7
    assert m.kicks == 0
    m.off()
    cases += 1

    # Missing IRQ delivery cannot be replaced by ACK alone, nor by elapsed time.
    for early in (False, True):
        m = fresh()
        m.wake_mode = 'no_irq'
        if early:
            m.write(ACK, 0)
        before = len([e for e in m.events if e[0] == 'stock_mcu_write_data'])
        assert m.run('q2_mcu_write_data', 0, 7, 100, 0) == -110
        m.on()
        assert m.value('q2_mcu_fault') == 1
        for command, args in [('q2_mcu_write_data', (0, 7, 100, 0)),
                              ('q2_mcu_reset', ()), ('q2_mcu_shutdown', ())]:
            assert m.run(command, *args) == -110
            m.on()
        after = len([e for e in m.events if e[0] == 'stock_mcu_write_data'])
        assert before == after
        cases += 1

    # Clock failures must leave the old sleeping state untouched and retryable.
    for action in ('clk_prepare', 'clk_enable'):
        for index in range(3):
            m = fresh()
            m.fail = (action, index)
            assert m.run('q2_mcu_write_data', 0, 7, 100, 0) == -5
            m.off()
            assert m.read(REQ) == m.read(ACK) == 1 and m.read(WAKE) == 0
            assert m.kicks == 0
            assert m.run('q2_mcu_write_data', 0, 7, 100, 0) == 7
            m.off()
            cases += 1

    # All lifecycle and upload wrappers use the same wake handshake.
    for command in (0x20004d70, 0x20004d71, 0x20004d72, 0x80044d73):
        m = fresh()
        assert m.run('q2_mcu_ioctl', 0, command, 0, 0) == 0
        assert ('wake_irq_completed',) in m.events
        cases += 1

    m = fresh()
    assert m.run('q2_mcu_write_data', 0, 0, 100, 0) == 0
    m.off()
    assert m.read(REQ) == m.read(ACK) == 1
    cases += 1

    # Unsupported image retains the original transport and all PWM clock leases.
    m = Machine(module, image, address)
    m.init()
    m.uc.mem_write(0xb3422100, b'\xff')
    m.boot()
    m.on()
    assert not m.value('q2_mcu_known')
    assert m.run('q2_mcu_write_data', 0, 7, 100, 0) == 7
    assert m.kicks == 0 and m.read(WAKE) == 0
    cases += 1
    return cases


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stockfs', type=Path)
    ap.add_argument('--json', type=Path)
    args = ap.parse_args()
    helper, symbols = mcu_park.compile_helper(sleep=True)
    image = mcu_park.patch_image((args.stockfs/'usr/data/libmcu-bare.bin').read_bytes(),
                                 helper, sleep=True, symbols=symbols)
    module = pwm.patch_mcu((args.stockfs/'module_driver/soc_mcu.ko').read_bytes(),
                           image, len(helper), sleep=True)
    import module_regions
    placement = module_regions.validate(base.reader, module)
    cases = sum(checks(module, image, addr) for addr in (0x01001000, 0x01007800, 0x0100f800))
    split = base.check_init_entry(module, image)
    result = dict(status='PASS', cases=cases, split_init_entries=split,
                  sha256=hashlib.sha256(module).hexdigest(), relocation_regions=placement,
                  limitations=['Kernel services and MCU wake delivery modeled',
                               'Actual linked instructions; inherited Unicorn transfer-context workaround',
                               'No physical wake or power measurement'])
    if args.json:
        args.json.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
