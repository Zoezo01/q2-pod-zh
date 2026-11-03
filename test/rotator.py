#!/usr/bin/env python3
"""Execute relocated stock and lazy-clock MIPS modules; no target hardware needed.

Only imported kernel functions and completion delivery are modeled. Instructions,
relocations, guards, descriptor setup, MMIO and IRQ paths come from the modules.
Instructions are stepped, with a fresh integer CPU context after completed
branches and modeled kernel calls to avoid a Unicorn MIPS chaining bug. These
checks do not simulate silicon DMA, power, or unsafe forced module removal.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

from unicorn import (Uc, UcError, UC_ERR_EXCEPTION, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN,
                     UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE)
from unicorn.mips_const import UC_MIPS_REG_PC, UC_MIPS_REG_0, UC_MIPS_REG_HI, UC_MIPS_REG_LO, UC_CPU_MIPS32_24KF

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from rotator import patch_module

STOCK = 'a341847285bc2e204f81be19d6cd942b0265275fba9c13ff8257a9f2574f2dc0'


def reg(n):
    return UC_MIPS_REG_0 + n


def signed(v):
    return v - 0x100000000 if v & 0x80000000 else v


def cstring(data, start):
    return data[start:].split(b'\0', 1)[0].decode()


class Module:
    """Independent ELF reader/relocator, deliberately separate from patcher code."""
    def __init__(self, data):
        h = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
        assert h[0][:7] == b'\x7fELF\x01\x01\x01' and h[1:3] == (1, 8)
        self.sections = [struct.unpack_from('<10I', data, h[6] + n * h[11])
                         for n in range(h[12])]
        self.bytes = [bytearray(s[5]) if s[1] == 8 else bytearray(data[s[4]:s[4]+s[5]])
                      for s in self.sections]
        names = self.bytes[h[13]]
        self.names = [cstring(names, s[0]) for s in self.sections]
        sym = self.names.index('.symtab')
        strings = self.bytes[self.sections[sym][6]]
        self.symbols = []
        for off in range(0, len(self.bytes[sym]), 16):
            n, value, size, info, other, section = struct.unpack_from('<IIIBBH', self.bytes[sym], off)
            self.symbols.append((cstring(strings, n), value, size, info, section))
        self.named = {s[0]: i for i, s in enumerate(self.symbols) if s[0]}

    def load(self, machine, base, *, init_base=None):
        cursors = dict(core=base)
        if init_base is not None:cursors["init"]=init_base
        addresses = {}
        for i, s in enumerate(self.sections):
            if s[2] & 2:
                group="init" if init_base is not None and self.names[i].startswith(".init") else "core"
                align = max(s[8], 4)
                cursor = (cursors[group] + align - 1) & -align
                addresses[i] = cursor
                cursors[group] = cursor + max(s[5], 4) + 0x40
        for group,cursor in cursors.items():
            start=init_base if group=="init" else base
            if cursor>start:
                machine.uc.mem_map(start & ~0xfff, ((cursor+0xfff)&~0xfff)-(start&~0xfff))
        values = []
        for name, value, size, info, section in self.symbols:
            if section == 0:
                values.append(machine.external(name) if name else 0)
            elif section == 0xfff1:
                values.append(value)
            elif section in addresses:
                values.append(addresses[section] + value)
            else:
                values.append(0)
        for i, s in enumerate(self.sections):
            if s[1] != 9 or s[7] not in addresses:
                continue
            target = self.bytes[s[7]]
            pending = []
            for off, info in struct.iter_unpack('<II', self.bytes[i]):
                symbol, kind = info >> 8, info & 255
                value = values[symbol]
                word = struct.unpack_from('<I', target, off)[0]
                if kind == 2:  # R_MIPS_32
                    patched = (word + value) & 0xffffffff
                elif kind == 4:  # R_MIPS_26
                    address = ((word & 0x3ffffff) << 2) + value
                    site=addresses[s[7]]+off
                    assert 0<=address<=0xffffffff and address%4==0, ('Invalid R_MIPS_26 target',hex(address))
                    assert (address & 0xf0000000) == ((site+4) & 0xf0000000), (
                        'R_MIPS_26 region overflow',self.symbols[symbol][0],hex(site),hex(address))
                    patched = (word & 0xfc000000) | ((address >> 2) & 0x3ffffff)
                elif kind == 5:  # R_MIPS_HI16: final low addend comes later.
                    pending.append((off, word, value))
                    continue
                elif kind == 6:  # R_MIPS_LO16
                    low = struct.unpack('<h', struct.pack('<H', word & 65535))[0]
                    remaining = []
                    for highoff, highword, highvalue in pending:
                        if highvalue != value:
                            remaining.append((highoff, highword, highvalue))
                            continue
                        full = ((highword & 65535) << 16) + low + value
                        struct.pack_into('<I', target, highoff,
                                         (highword & 0xffff0000) | (((full + 0x8000) >> 16) & 65535))
                    pending = remaining
                    patched = (word & 0xffff0000) | ((value + low) & 65535)
                else:
                    raise AssertionError(('unsupported relocation', kind))
                struct.pack_into('<I', target, off, patched)
            assert not pending, pending
        for i, address in addresses.items():
            machine.uc.mem_write(address, bytes(self.bytes[i]))
        return {name: values[i] for name, i in self.named.items()}, addresses[self.names.index('.bss')]


class Machine:
    def __init__(self, data, base=0x01001000, *, patched=True):
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
        self.uc.mem_map(0x02000000, 0x10000)  # descriptor and request, also KSEG aliases
        self.uc.mem_map(0x04000000, 0x10000)  # stack
        self.uc.mem_map(0x05000000, 0x10000)  # kernel mock functions/data
        self.uc.mem_map(0x13070000, 0x1000)  # rotator's KSEG1 physical mapping
        self.halt = 0x0500f000
        self.externals = {}
        self.handlers = {}
        self.events = []
        self.mmio = []
        self.locked = self.prepared = self.enabled = self.irq = False
        self.get_error = self.prepare_error = self.enable_error = 0
        self.wait = 'success'
        self.patched = patched
        self.symbols, self.bss = Module(data).load(self, base)
        self.uc.hook_add(UC_HOOK_CODE, self.code)
        self.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.memory)
        self.write(0x0500e000, 0x1234abcd)
        self.write(0x0500e004, self.externals['_cache_callback'])
        self.request = 0x02001000
        self.pdev = 0x02002000
        self.clock = 0x02003000
        self.config()

    def external(self, name):
        if name in self.externals:
            return self.externals[name]
        if name == '__stack_chk_guard':
            address = 0x0500e000
        elif name == '_dma_cache_wback':
            self.external('_cache_callback')
            address = 0x0500e004
        else:
            address = 0x05000000 + len(self.handlers) * 0x100
            self.handlers[address] = name
            self.uc.mem_write(address, struct.pack('<II', 0x03e00008, 0))
        self.externals[name] = address
        return address

    def write(self, address, value):
        self.uc.mem_write(address, struct.pack('<I', value & 0xffffffff))

    def read(self, address):
        return struct.unpack('<I', self.uc.mem_read(address, 4))[0]

    def config(self, width=32, height=16, src=2, dst=2):
        self.uc.mem_write(self.request, struct.pack('<12I', 0x02004000, 0x02005000,
                                                   height, width, width, width, src, 0, dst, 0, 0, 0))

    def code(self, u, address, size, unused):
        self.last_code = address
        assert address not in self.handlers, 'stepped past external-call boundary'

    def kernel_call(self, address):
        u = self.uc
        name = self.handlers[address]
        args = [u.reg_read(reg(n)) for n in range(4, 8)]
        self.events.append(name)
        result = 0
        if name in ('__stack_chk_fail', '__assert'):
            raise AssertionError(name)
        if name == 'clk_get':
            assert bytes(u.mem_read(args[1], 9)) == b'gate_rot\0'
            result = self.get_error or self.clock
        elif name == 'mutex_lock':
            assert args[0] == self.bss + 4 and not self.locked
            self.locked = True
        elif name == 'mutex_unlock':
            assert args[0] == self.bss + 4 and self.locked
            self.locked = False
        elif name == 'clk_prepare':
            assert args[0] == self.clock and not self.prepared
            assert self.locked or not self.patched
            result, self.prepare_error = self.prepare_error, 0
            if result == 0:
                self.prepared = True
        elif name == 'clk_enable':
            assert args[0] == self.clock and self.prepared and not self.enabled
            result, self.enable_error = self.enable_error, 0
            if result == 0:
                self.enabled = True
        elif name == 'clk_disable':
            assert args[0] == self.clock and self.enabled and not self.irq
            self.enabled = False
        elif name == 'clk_unprepare':
            assert args[0] == self.clock and self.prepared and not self.enabled
            self.prepared = False
        elif name == 'clk_put':
            assert args[0] == self.clock and not self.prepared and not self.enabled
        elif name == 'dma_alloc_attrs':
            assert args[1] == 32
            result = 0xa2000000
        elif name == 'memset':
            memory_address = args[0] & 0x1fffffff if 0x80000000 <= args[0] < 0xc0000000 else args[0]
            u.mem_write(memory_address, bytes([args[1] & 255]) * args[2])
            result = args[0]
        elif name == 'request_threaded_irq':
            assert self.enabled and args[0] == 37 and args[2:] == [0, 0x80]
            assert args[1] == self.symbols['rotator_irq_handler']
            assert self.read(u.reg_read(reg(29)) + 20) == self.bss
            self.irq = True
        elif name == 'free_irq':
            assert self.irq and self.enabled and args[:2] == [37, self.bss]
            self.irq = False
        elif name == 'prepare_to_wait_event':
            if self.wait == 'success':
                self.write(self.bss + 0x38, 1)
            elif self.wait == 'interrupted':
                result = -512
        elif name == 'schedule_timeout':
            assert self.wait == 'timeout'
            result = 0
        elif name not in ('__mutex_init', '__init_waitqueue_head', 'misc_register',
                          'dma_free_attrs', 'misc_deregister', '_cache_callback',
                          'init_wait_entry', 'finish_wait', 'printk', '__wake_up'):
            raise AssertionError(('unmodeled kernel call', name))
        # Imported C functions may destroy every caller-saved register.
        for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 24, 25):
            u.reg_write(reg(n), 0x5a000000 + n)
        u.reg_write(reg(2), result & 0xffffffff)
        return u.reg_read(reg(31))

    def fresh_cpu(self):
        # Unicorn retains stale pending branch state at some chained MIPS
        # branches. Single-step each instruction (a branch step includes its
        # actual delay instruction), then reset only after completed transfers
        # and modeled C returns. Copy RAM and every integer architectural
        # register; never model, modify or skip a module instruction.
        previous = self.uc
        current = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        current.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
        for begin, end, permissions in previous.mem_regions():
            current.mem_map(begin, end - begin + 1, permissions)
            current.mem_write(begin, bytes(previous.mem_read(begin, end - begin + 1)))
        for register in [*(reg(n) for n in range(1, 32)), UC_MIPS_REG_HI, UC_MIPS_REG_LO]:
            current.reg_write(register, previous.reg_read(register))
        current.hook_add(UC_HOOK_CODE, self.code)
        current.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.memory)
        self.uc = current

    def memory(self, u, access, address, size, value, unused):
        physical = address & 0x1fffffff
        if 0x13070000 <= physical < 0x13071000:
            assert self.enabled, ('MMIO with clock disabled', hex(u.reg_read(UC_MIPS_REG_PC)))
            self.mmio.append((physical - 0x13070000, access == UC_MEM_WRITE, value))

    def run(self, name, argument=None):
        self.uc.reg_write(reg(29), 0x04008000)
        self.uc.reg_write(reg(31), self.halt)
        preserved = {n: 0x60000000 + n for n in (*range(16, 24), 28, 30)}
        for n, value in preserved.items():
            self.uc.reg_write(reg(n), value)
        if argument is not None:
            self.uc.reg_write(reg(4), argument)
        start = self.symbols[name]
        for _ in range(10000):
            if start == self.halt:
                break
            if start in self.handlers:
                start = self.kernel_call(start)
                self.fresh_cpu()
                continue
            word = self.read(start)
            opcode = word >> 26
            transfer = opcode in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23)
            transfer |= opcode == 0 and word & 63 in (8, 9)  # jr/jalr
            # Unicorn executes the actual delay instruction within this step,
            # even when count=1 prevents its separate code-hook notification.
            self.uc.emu_start(start, self.halt, count=1)
            start = self.uc.reg_read(UC_MIPS_REG_PC)
            if transfer:
                self.fresh_cpu()
        else:
            raise AssertionError('instruction budget exhausted')
        assert self.uc.reg_read(reg(29)) == 0x04008000
        assert all(self.uc.reg_read(reg(n)) == v for n, v in preserved.items()), name
        return signed(self.uc.reg_read(reg(2)))

    def probe(self):
        assert self.run('ingenic_rotator_probe', self.pdev) == 0

    def convert(self):
        result = self.run('rotator_complete_conversion', self.request)
        assert not self.locked
        return result

    def remove(self):
        assert self.run('ingenic_rotator_remove', self.pdev) == 0


def check(stock, patched, base):
    cases = 0
    # The unchanged stock is a positive control for the loader and kernel mocks.
    old = Machine(stock, base, patched=False)
    old.probe()
    assert old.enabled and old.prepared and not old.mmio
    assert old.convert() == 0
    old.remove()
    cases += 1

    # Harness controls: real module traps and disabled-clock MMIO must be fatal.
    bad = Machine(stock, base, patched=False)
    bad.probe()
    bad.write(bad.symbols['rotator_complete_conversion'], 0x0000000d)  # break
    try:
        bad.convert()
    except UcError as error:
        assert error.errno == UC_ERR_EXCEPTION
    else:
        raise AssertionError('module instruction trap was swallowed')
    cases += 1
    bad = Machine(stock, base, patched=False)
    bad.probe()
    bad.enabled = False
    try:
        bad.convert()
    except AssertionError as error:
        assert 'MMIO with clock disabled' in str(error)
    else:
        raise AssertionError('disabled-clock MMIO was not detected')
    cases += 1

    for module, is_patched in [(stock, False), (patched, True)]:
        m = Machine(module, base, patched=is_patched)
        m.get_error = -2
        assert m.run('ingenic_rotator_probe', m.pdev) == -1
        assert not m.enabled and not m.prepared and not m.mmio
        assert m.events.count('clk_get') == 1
        assert not any(e in m.events for e in ('clk_prepare', 'clk_enable', 'clk_put', 'misc_register'))
        cases += 1

    m = Machine(patched, base)
    m.probe()
    assert not m.prepared and not m.enabled and not m.mmio
    assert m.read(m.bss + 0x50) == 0
    assert not any(e.startswith('clk_') and e != 'clk_get' for e in m.events)
    m.remove()
    assert m.events.count('clk_put') == 1
    cases += 1

    for args in [(3, 16, 2, 2), (2048, 16, 2, 2), (32, 3, 2, 2),
                 (32, 2048, 2, 2), (32, 16, 7, 2), (32, 16, 2, 1), (32, 16, 2, 7)]:
        m = Machine(patched, base)
        m.probe()
        m.config(*args)
        start = len(m.events)
        assert m.convert() == -22
        assert not m.enabled and not m.mmio and 'mutex_lock' not in m.events[start:]
        m.remove()
        cases += 1

    m = Machine(patched, base)
    m.probe()
    assert m.convert() == 0
    initial = list(m.mmio)
    assert m.uc.mem_read(0x02000000, 32) == old.uc.mem_read(0x02000000, 32)
    assert m.events.index('mutex_lock') < m.events.index('clk_prepare') < m.events.index('clk_enable') < m.events.index('memset')
    assert m.prepared and m.enabled and m.irq
    assert m.read(m.bss + 0x50) == 1
    for _ in range(3):
        assert m.convert() == 0
    assert Counter(m.events)['clk_prepare'] == Counter(m.events)['clk_enable'] == 1
    assert Counter(m.events)['request_threaded_irq'] == 1
    assert initial == old.mmio, 'stock/patched descriptor/register programming differs'
    m.remove()
    assert Counter(m.events)['clk_disable'] == Counter(m.events)['clk_unprepare'] == 1
    assert m.read(m.bss + 0x50) == 0
    cases += 1

    for call, error in [('prepare', -12), ('prepare', -517), ('enable', -5), ('enable', -22)]:
        m = Machine(patched, base)
        m.probe()
        setattr(m, call + '_error', error)
        assert m.convert() == error
        assert not m.enabled and not m.prepared and not m.irq and not m.mmio
        counts = Counter(m.events)
        assert counts['clk_unprepare'] == (call == 'enable')
        assert m.read(m.bss + 0x50) == 0
        assert m.convert() == 0
        assert m.enabled
        m.remove()
        cases += 1

        # Removal immediately after acquisition failure has no clock to release.
        m = Machine(patched, base)
        m.probe()
        setattr(m, call + '_error', error)
        assert m.convert() == error
        before = Counter(m.events)
        m.remove()
        after = Counter(m.events)
        assert after['clk_disable'] == before['clk_disable'] == 0
        assert after['clk_unprepare'] == before['clk_unprepare']
        assert after['clk_put'] == 1
        assert m.read(m.bss + 0x50) == 0
        cases += 1

    for outcome in ['timeout', 'interrupted']:
        m = Machine(patched, base)
        m.probe()
        m.wait = outcome
        assert m.convert() == -145
        assert m.enabled and m.prepared and m.irq
        assert 'clk_disable' not in m.events and 'clk_unprepare' not in m.events
        # Run the REAL IRQ after the failed wait, including its accesses after wake.
        m.write(0x13070010, 6)
        before = len(m.mmio)
        assert m.run('rotator_irq_handler') == 1
        assert m.read(m.bss + 0x38) == 1
        assert len(m.mmio) > before and '__wake_up' in m.events
        assert m.enabled
        m.remove()
        cases += 1

    for status in [0, 2, 4, 6]:
        m = Machine(patched, base)
        m.probe()
        assert m.convert() == 0
        m.write(0x13070010, status)
        m.write(m.bss + 0x38, 0)
        assert m.run('rotator_irq_handler') == 1
        assert m.read(m.bss + 0x38) == bool(status & 4)
        assert m.enabled
        m.remove()
        cases += 1
    return cases



def check_metadata(stock, patched):
    """Pin the transformation independently of the patcher's parser and encoder."""
    before, after = Module(stock), Module(patched)
    old_header = list(struct.unpack_from('<16sHHIIIIIHHHHHH', stock))
    new_header = list(struct.unpack_from('<16sHHIIIIIHHHHHH', patched))
    old_header[6] = new_header[6]  # File offsets move when payload sections grow.
    assert old_header == new_header, 'ELF identity, ABI flags or section indices changed'
    assert before.names == after.names
    changed = {'.text', '.rel.text', '.bss', '.symtab', '.strtab', '.modinfo',
               '.note.gnu.build-id'}
    for i, name in enumerate(before.names):
        old, new = list(before.sections[i]), list(after.sections[i])
        old[4] = new[4]  # section file offset
        if name in changed:
            old[5] = new[5]  # size
        assert old == new, ('section metadata changed', name)
        if name not in changed:
            assert before.bytes[i] == after.bytes[i], ('section payload changed', name)
    def payload(module, name):
        return bytes(module.bytes[module.names.index(name)])
    old_text, new_text = payload(before, '.text'), payload(after, '.text')
    pins = {0xcc: 0x8e510040, 0xd0: 0x3c020000, 0xd8: 0x24420000,
            0x578: 0x3c020000, 0x57c: 0x24420000,
            0x734: 0x3c020000, 0x738: 0x24420000,
            0x73c: 0x0040f809, 0x740: 0x02202025, 0x744: 0x10400024}
    assert len(old_text) == 0x860 and len(new_text) > len(old_text)
    for off in range(0, len(old_text), 4):
        old_word, new_word = (struct.unpack_from('<I', data, off)[0]
                              for data in (old_text, new_text))
        if off in pins:
            assert old_word == pins[off] and new_word != old_word, hex(off)
        else:
            assert old_word == new_word, ('unexpected instruction edit', hex(off))
    # IRQ and all DMA/wait/validation code are byte-identical, except displaced
    # entry and release instructions explicitly pinned above.
    assert old_text[0x5d8:0x698] == new_text[0x5d8:0x698]
    old_rels = list(struct.iter_unpack('<II', payload(before, '.rel.text')))
    new_rels = list(struct.iter_unpack('<II', payload(after, '.rel.text')))
    removed = {0xd0: ('memset', 5), 0xd8: ('memset', 6),
               0x578: ('clk_disable', 5), 0x57c: ('clk_disable', 6),
               0x734: ('clk_prepare', 5), 0x738: ('clk_prepare', 6)}
    for off, (symbol, kind) in removed.items():
        assert (off, before.named[symbol] << 8 | kind) in old_rels
    retained = [(off, info) for off, info in old_rels if off not in removed]
    assert new_rels[:len(retained)] == retained
    added = new_rels[len(retained):]
    assert [off for off, info in added if off < len(old_text)] == [0xcc, 0x578]
    for off, info in added:
        symbol, kind = info >> 8, info & 255
        assert symbol < len(before.symbols), 'new external dependency'
        if kind == 4:
            assert before.symbols[symbol][3] & 15 == 3
            assert before.names[before.symbols[symbol][4]] == '.text'
        else:
            assert kind in (5, 6) and len(old_text) <= off < len(new_text)
    assert after.symbols[:len(before.symbols)] == before.symbols
    assert [s[0] for s in after.symbols[len(before.symbols):]] == [
        'q2_rotator_acquire', 'q2_rotator_release']
    for symbol in after.symbols[len(before.symbols):]:
        assert symbol[3] == 0x12 and before.names[symbol[4]] == '.text'
        assert len(old_text) <= symbol[1] < len(new_text)
    assert payload(after, '.symtab').startswith(payload(before, '.symtab'))
    assert payload(after, '.strtab').startswith(payload(before, '.strtab'))
    assert payload(after, '.modinfo') == payload(before, '.modinfo') + b'q2_clock_policy=lazy-first-use-v1\0'
    info = payload(after, '.modinfo').split(b'\0')
    assert b'vermagic=5.10.21+ SMP preempt mod_unload MIPS32_R5 32BIT ' in info
    assert b'depends=utils' in info
    assert before.sections[before.names.index('.bss')][5] == 0x50
    assert after.sections[after.names.index('.bss')][5] == 0x54
    old_note, new_note = (payload(module, '.note.gnu.build-id') for module in (before, after))
    assert old_note[:16] == new_note[:16] and old_note[16:] != new_note[16:]
    exports = [name for name in before.names if 'ksymtab' in name or 'kcrctab' in name]
    assert exports and all(payload(before, name) == payload(after, name) for name in exports)
    return {'unchanged_export_sections': exports, 'original_instruction_edits': [hex(x) for x in pins],
            'existing_symbols_preserved': len(before.symbols), 'new_external_dependencies': 0}


def check_delay_slots():
    """Prove count=1 executes the real MIPS delay instruction before transfer."""
    for words, target in [((0x10000002, 0xac880000), 0x1000c),
                          ((0x0040f809, 0x26440004), 0x10040)]:
        uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        uc.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
        uc.mem_map(0x10000, 0x1000)
        uc.mem_write(0x10000, struct.pack('<II', *words))
        uc.reg_write(reg(2), target)
        uc.reg_write(reg(4), 0x10100)
        uc.reg_write(reg(8), 123)
        uc.reg_write(reg(18), 0x10200)
        writes = []
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda u, access, address, size, value, _: writes.append((address, value)))
        uc.emu_start(0x10000, 0x10100, count=1)
        assert uc.reg_read(UC_MIPS_REG_PC) == target
        if words[0] == 0x10000002:
            assert bytes(uc.mem_read(0x10100, 4)) == struct.pack('<I', 123)
            assert writes == [(0x10100, 123)]
        else:
            assert uc.reg_read(reg(4)) == 0x10204 and uc.reg_read(reg(31)) == 0x10008
    return 2

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stock', type=Path)
    ap.add_argument('--json', type=Path)
    args = ap.parse_args()
    original = args.stock.read_bytes()
    assert hashlib.sha256(original).hexdigest() == STOCK
    changed = patch_module(original)
    assert changed == patch_module(original), 'nondeterministic patch'
    assert changed != original
    original_elf = Module(original)
    altered_irq = bytearray(original)
    altered_irq[original_elf.sections[original_elf.names.index('.text')][4] + 0x5dc] ^= 1
    for invalid in [changed, original[:-1], b'', bytes(altered_irq),
                    bytes([original[0] ^ 1]) + original[1:]]:
        try:
            patch_module(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError('unsupported input accepted')
    metadata = check_metadata(original, changed)
    # These place section symbols and imported functions on different HI16/LO16 carries.
    bases = [0x01001000, 0x01007800, 0x0100f800]
    delay_slots = check_delay_slots()
    counts = [check(original, changed, base) for base in bases]
    result = {'status': 'PASS', 'cases': sum(counts), 'delay_slot_controls': delay_slots, 'load_bases': [hex(b) for b in bases],
              'metadata': metadata, 'stock_sha256': STOCK, 'patched_sha256': hashlib.sha256(changed).hexdigest(),
              'limitations': ['Kernel calls and completion delivery modeled',
                              'Instruction stepping; fresh CPU after each completed branch and external call',
                              'No silicon DMA, power or forced-unload validation']}
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
