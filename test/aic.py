#!/usr/bin/env python3
"""Execute actual stock/patched AIC instructions with modeled kernel wait queues.

No device, DMA or kernel scheduler runs here. Tests interleave actual notifier,
trigger and hw_free functions around imported wait/schedule/wake calls. Every
module instruction and relocation is retained. Branch-safe instruction stepping
resets only integer CPU context after completed transfers; an independent valid
MIPS control documents the Unicorn chaining limitation requiring this approach.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

from unicorn import Uc, UcError, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_TLB_VIRTUAL, UC_HOOK_MEM_WRITE
from unicorn.mips_const import UC_CPU_MIPS32_24KF, UC_MIPS_REG_0, UC_MIPS_REG_PC, UC_MIPS_REG_HI, UC_MIPS_REG_LO

from module_mips import Module, relocation_control
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import aic as patcher

REGS = [UC_MIPS_REG_0 + n for n in range(1, 32)] + [UC_MIPS_REG_HI, UC_MIPS_REG_LO]
reg = lambda n: UC_MIPS_REG_0 + n


def cpu():
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    u.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
    u.ctl_set_tlb_mode(UC_TLB_VIRTUAL)
    return u


def clear_branch(u, clean):
    values = [u.reg_read(r) for r in REGS]
    u.context_restore(clean)
    for r, v in zip(REGS, values):
        u.reg_write(r, v)


def branch_control():
    """Store-delay branch followed by JAL is valid; no helper bytes are rewritten."""
    words = {0x1001000: 0x14200004, 0x1001004: 0xac627440,
             0x1001014: 0x0d400940, 0x1001018: 0}
    def new():
        u = cpu()
        u.mem_map(0x1000000, 0x10000)
        u.mem_map(0x5000000, 0x10000)
        clean = u.context_save()
        for a, w in words.items():
            u.mem_write(a, struct.pack('<I', w))
        for n, v in [(1, 1), (2, 0x1234), (3, 0x1000000)]:
            u.reg_write(reg(n), v)
        return u, clean
    u, _ = new()
    try:
        u.emu_start(0x1001000, 0x5002500, count=10)
        continuous = 'PASS'
    except UcError as e:
        continuous = str(e)
    u, clean = new()
    u.emu_start(0x1001000, 0x5002500, count=1)
    pc = u.reg_read(UC_MIPS_REG_PC)
    assert pc == 0x1001014
    clear_branch(u, clean)
    u.emu_start(pc, 0x5002500, count=1)
    assert u.reg_read(UC_MIPS_REG_PC) == 0x5002500
    assert u.reg_read(reg(31)) == 0x100101c
    assert bytes(u.mem_read(0x1007440, 4)) == struct.pack('<I', 0x1234)
    assert all(bytes(u.mem_read(a, 4)) == struct.pack('<I', w) for a, w in words.items())
    return dict(continuous=continuous, branch_safe='PASS', instructions_unchanged=True)


class Task:
    def __init__(self, name, pc, args, sp, halt):
        self.name, self.pc, self.sp = name, pc, sp
        self.regs = {r: 0 for r in REGS}
        self.preserved = {reg(n): 0x60000000 + n for n in (*range(16, 24), 28, 30)}
        self.regs.update(self.preserved)
        self.regs.update({reg(29): sp, reg(31): halt})
        self.regs.update({reg(4+i): v for i, v in enumerate(args)})
        self.blocked = None
        self.state = 0
        self.done = False


class Machine:
    PRIVATE, STREAM, RUNTIME, COMPONENT = 0x02001000, 0x02002000, 0x02003000, 0x02004000
    def __init__(self, data, base=0xc0001000, kernel=0x80000000):
        self.uc = cpu()
        self.clean = self.uc.context_save()
        for a in (0x02000000, kernel):
            self.uc.mem_map(a, 0x10000)
        self.uc.mem_map(0x04000000, 0x100000)
        self.kernel = kernel
        self.halt = kernel + 0xff00
        self.handlers, self.externals, self.events = {}, {}, []
        self.tasks, self.waiters = [], {}
        self.symbols, self.bss = Module(data).load(self, base, init_base=0xd0001000)
        self.original_code = bytes(self.uc.mem_read(self.symbols['aic_trigger'], 0x2330))
        self.write(self.externals['__stack_chk_guard'], 0x79ac1234)
        self.write(self.STREAM + 0x70, self.RUNTIME)
        self.write(self.STREAM + 8, 0x02005000)
        self.write(0x02005000 + 0x55c, 0x02006000)
        self.write(0x02006000, 0x02007000)
        self.write(0x02007004, 1)  # stock hw_free no optional DMA-buffer free
        self.write(self.RUNTIME + 0xf8, self.PRIVATE)
        self.write(self.PRIVATE, 0x15)
        self.write(self.PRIVATE + 4, 0x27)
        self.write(self.PRIVATE + 8, 0x82008000)
        self.write(self.PRIVATE + 0xc, self.STREAM)
        self.write(self.PRIVATE + 0x20, 5804)
        self.queue = self.PRIVATE + 0x34
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.memory_write)

    def memory_write(self, u, access, address, size, value, unused):
        if address == self.PRIVATE + 0x2c and value == 1:
            # Execute the original notifier ACK instruction only after its
            # stack waiter has been removed; hw_free may free resources next.
            assert not any(t.name == 'aic_dma_pcm_notice_thread'
                           for _, t in self.waiters.values()), 'ACK before finish_wait'
            self.events.append(('ack', value))

    def external(self, name):
        if name not in self.externals:
            if name == '__stack_chk_guard':
                a = self.kernel + 0xf000
            else:
                a = self.kernel + 0x1000 + len(self.handlers) * 0x20
                self.handlers[a] = name
            self.externals[name] = a
        return self.externals[name]

    def read(self, a):
        return struct.unpack('<I', self.uc.mem_read(a, 4))[0]

    def write(self, a, v):
        self.uc.mem_write(a, struct.pack('<I', v & 0xffffffff))

    def spawn(self, name, *args):
        t = Task(name, self.symbols[name], args, 0x040f8000 - 0x1000 * len(self.tasks), self.halt)
        self.tasks.append(t)
        return t

    def save(self, t):
        t.regs = {r: self.uc.reg_read(r) for r in REGS}

    def wake(self, queue, mode):
        for entry, (q, t) in list(self.waiters.items()):
            if q == queue and t.state & mode:
                t.state = 0
                t.blocked = None
                self.events.append(('woken', t.name))

    def expire(self, t):
        assert t.blocked in ('period', 'timeout')
        t.blocked, t.state = None, 0

    def service(self, t, name):
        u = self.uc
        a = [u.reg_read(reg(n)) for n in range(4, 8)]
        self.events.append((name, *a))
        result = 0
        if name == 'init_wait_entry':
            assert a[1] == 0
            u.mem_write(a[0], bytes(20))
        elif name == 'prepare_to_wait_event':
            assert a[0] == self.queue and a[2] in (2, 0x402)
            self.waiters[a[1]] = (a[0], t)
            t.state = a[2]
        elif name == 'finish_wait':
            assert self.waiters[a[1]] == (a[0], t)
            del self.waiters[a[1]]
            t.state = 0
        elif name == 'schedule_timeout':
            assert a[0] in (100, 0x7fffffff), hex(a[0])
            result = a[0]
            if t.state:
                t.blocked = 'idle' if a[0] == 0x7fffffff else 'timeout'
        elif name == '__wake_up':
            assert a == [self.queue, 3, 0, 0]
            self.wake(a[0], a[1])
        elif name == 'usleep_range':
            assert a[0] == a[1]
            t.blocked = 'period'
        elif name == 'snd_pcm_period_elapsed':
            assert a[0] == self.STREAM
        elif name == 'audio_dma_start':
            assert a[:2] == [0x27, 0x82008000]
        elif name in ('audio_dma_stop', 'audio_release_dma_dev'):
            assert a[0] == 0x27
        elif name == 'audio_disconnect_dev':
            assert a[:2] in ([0x27, 0x15], [0x15, 0x27])
        elif name == 'snd_pcm_lib_free_pages':
            assert a[0] == self.STREAM
        else:
            raise AssertionError(('Unmodeled service', name, a))
        ra = u.reg_read(reg(31))
        # Deliberately poison all caller-saved registers, including assembler
        # temporary and t9, to validate both helper and hook liveness.
        for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 24, 25):
            u.reg_write(reg(n), 0x5a000000 + n)
        u.reg_write(reg(2), result & 0xffffffff)
        t.pc = ra

    def run(self, t, pause=None):
        assert not t.done and not t.blocked, (t.name, t.blocked)
        u = self.uc
        u.context_restore(self.clean)
        for r, v in t.regs.items():
            u.reg_write(r, v)
        for _ in range(20000):
            if t.pc == self.halt:
                t.done = True
                self.save(t)
                assert t.regs[reg(29)] == t.sp
                assert all(t.regs[r] == v for r, v in t.preserved.items())
                return 'done'
            if t.pc in self.handlers:
                name = self.handlers[t.pc]
                if pause == (name, 'before'):
                    self.save(t)
                    return 'pause'
                self.service(t, name)
                clear_branch(u, self.clean)
                if t.blocked:
                    self.save(t)
                    return t.blocked
                if pause == (name, 'after'):
                    self.save(t)
                    return 'pause'
                continue
            word = self.read(t.pc)
            op = word >> 26
            transfer = op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23) or op == 0 and word & 63 in (8, 9)
            u.emu_start(t.pc, self.halt, count=1)
            t.pc = u.reg_read(UC_MIPS_REG_PC)
            if transfer:
                clear_branch(u, self.clean)
        raise AssertionError('Instruction budget exhausted')

    def trigger(self, command):
        t = self.spawn('aic_dma_trigger', self.COMPONENT, self.STREAM, command)
        assert self.run(t) == 'done'
        return t.regs[reg(2)]

    def worker(self):
        return self.spawn('aic_dma_pcm_notice_thread', self.PRIVATE)

    def counts(self):
        return Counter(e[0] for e in self.events)

    def free(self, direction=0):
        self.write(self.STREAM + 0x30, direction)
        return self.spawn('aic_dma_pcm_hw_free', self.COMPONENT, self.STREAM)


def semantic_tests(stock, patched, base, kernel):
    cases = 0
    def new(data=patched):
        return Machine(data, base, kernel)

    # Stock positive control: stopped notifier repeatedly sleeps 10 ms.
    m = new(stock)
    t = m.worker()
    for _ in range(3):
        assert m.run(t) == 'period'
        assert m.events[-1][1:3] == (10000, 10000)
        m.expire(t)
    m.write(m.PRIVATE + 0x28, 1)
    assert m.run(t) == 'done' and m.read(m.PRIVATE + 0x2c) == 1
    assert not m.waiters
    cases += 1

    # Patched stopped worker queues once and has no periodic sleep.
    m = new()
    t = m.worker()
    assert m.run(t) == 'idle'
    assert m.counts()['usleep_range'] == 0 and len(m.waiters) == 1
    assert t.state == 0x402 and m.events[-1][1] == 0x7fffffff
    cases += 1

    # Already set conditions still pair prepare/finish without scheduling.
    for offset in (0x28, 0x30):
        m = new()
        m.write(m.PRIVATE + offset, 1)
        t = m.spawn('q2_aic_wait_stopped', m.PRIVATE)
        assert m.run(t) == 'done'
        assert m.counts()['schedule_timeout'] == 0 and not m.waiters
        assert m.counts()['prepare_to_wait_event'] == m.counts()['finish_wait'] == 1
        cases += 1

    # Both producers at every lost-wakeup boundary, including after schedule.
    for event in ('resume', 'exit'):
        for point in [('prepare_to_wait_event', 'before'),
                      ('prepare_to_wait_event', 'after'),
                      ('schedule_timeout', 'before'), None]:
            m = new()
            t = m.worker()
            assert m.run(t, point) == ('pause' if point else 'idle')
            if event == 'resume':
                assert m.trigger(1) == 0
                assert m.read(m.PRIVATE + 0x30) == 1
            else:
                freeing = m.free()
                assert m.run(freeing) == 'timeout'
                assert m.read(m.PRIVATE + 0x28) == 1
            assert not t.blocked
            result = m.run(t)
            if event == 'resume':
                assert result == 'period'
                assert m.events[-1][1:3] == (5804, 5804)
                assert not m.waiters
            else:
                assert result == 'done' and m.read(m.PRIVATE + 0x2c) == 1
                assert not freeing.blocked
                assert m.run(freeing) == 'done'
                assert not m.waiters
                names = [e[0] for e in m.events]
                assert names.index('finish_wait') < names.index('ack') < names.index('audio_release_dma_dev')
            cases += 1

    # Negative controls execute the unchanged stock producer in a second
    # module instance against the patched stopped worker. Its condition store
    # alone cannot wake an already queued sleeper: both new wake hooks matter.
    for producer in ('aic_dma_trigger', 'aic_dma_pcm_hw_free'):
        m = new()
        stock_symbols, _ = Module(stock).load(m, 0xc5001000, init_base=0xd5001000)
        t = m.worker()
        assert m.run(t) == 'idle'
        args = (m.COMPONENT, m.STREAM, 1) if producer == 'aic_dma_trigger' else (m.COMPONENT, m.STREAM)
        p = m.spawn(producer, *args)
        p.pc = stock_symbols[producer]
        assert m.run(p) == ('done' if producer == 'aic_dma_trigger' else 'timeout')
        assert t.blocked == 'idle' and m.counts()['__wake_up'] == 0
        assert m.read(m.PRIVATE + (0x30 if producer == 'aic_dma_trigger' else 0x28)) == 1
        cases += 1

    # Spurious wake returns to the same event wait without timed polling.
    m = new()
    t = m.worker()
    assert m.run(t) == 'idle'
    for _ in range(4):
        m.wake(m.queue, 3)
        assert m.run(t) == 'idle'
    assert m.counts()['schedule_timeout'] == 5 and m.counts()['usleep_range'] == 0
    assert len(m.waiters) == 1
    cases += 1

    # Active cadence is stock-identical, for several realistic timer periods.
    for period in (1000, 5804, 23219):
        observations = []
        for data in (stock, patched):
            m = new(data)
            m.write(m.PRIVATE + 0x20, period)
            assert m.trigger(1) == 0
            t = m.worker()
            for _ in range(3):
                assert m.run(t) == 'period'
                m.expire(t)
            m.write(m.PRIVATE + 0x28, 1)
            assert m.run(t) == 'done'
            observations.append([e for e in m.events if e[0] in ('usleep_range', 'snd_pcm_period_elapsed')])
        assert observations[0] == observations[1]
        cases += 1

    # Actual trigger table: start/resume/pause-release; stop/suspend/pause-push.
    for command in (1, 4, 6, 0, 3, 5, 2, 7, 0xffffffff):
        m = new()
        assert m.trigger(command) == (0xffffffea if command in (2, 7, 0xffffffff) else 0)
        start = command in (1, 4, 6)
        assert m.read(m.PRIVATE + 0x30) == int(start)
        assert m.counts()['__wake_up'] == int(start)
        assert m.counts()['audio_dma_start'] == int(start)
        assert m.counts()['audio_dma_stop'] == int(command in (0, 3, 5))
        cases += 1

    # Repeat pause/resume on the SAME worker/queue with no stale waiter.
    m = new()
    t = m.worker()
    assert m.run(t) == 'idle'
    for _ in range(12):
        assert m.trigger(1) == 0 and not t.blocked
        assert m.run(t) == 'period' and not m.waiters
        assert m.trigger(0) == 0
        m.expire(t)
        assert m.run(t) == 'idle' and len(m.waiters) == 1
    cases += 1

    # Teardown during active timed sleep must not wake/shorten that sleep.
    for direction in (0, 1):
        m = new()
        assert m.trigger(1) == 0
        t = m.worker()
        assert m.run(t) == 'period'
        freeing = m.free(direction)
        assert m.run(freeing) == 'timeout'
        assert t.blocked == 'period'
        m.expire(t)
        assert m.run(t) == 'done'
        assert m.run(freeing) == 'done' and not m.waiters
        assert m.counts()['snd_pcm_period_elapsed'] == 1
        cases += 1

    # Existing immediate-ack and 100-jiffy timeout paths remain stock behavior.
    for ack in (0, 1):
        m = new()
        m.write(m.PRIVATE + 0x2c, ack)
        freeing = m.free()
        if not ack:
            assert m.run(freeing) == 'timeout'
            # Model the existing finite timeout expiring, not a new policy.
            m.expire(freeing)
            freeing.regs[reg(2)] = 0
        assert m.run(freeing) == 'done' and not m.waiters
        assert m.counts()['__wake_up'] == 1
        cases += 1
    return cases


def structural_tests(stock, patched):
    before, after = Module(stock), Module(patched)
    assert before.sections[before.names.index('.MIPS.abiflags')][5] == 24
    assert before.bytes[before.names.index('.MIPS.abiflags')] == after.bytes[after.names.index('.MIPS.abiflags')]
    assert struct.unpack_from('<I', stock, 36)[0] == struct.unpack_from('<I', patched, 36)[0]
    for name in ('.gnu.linkonce.this_module',):
        assert before.bytes[before.names.index(name)] == after.bytes[after.names.index(name)]
    # Original entry, all active instructions and every stock instruction except
    # the three intentional hooks remain byte-identical before relocation.
    a = before.bytes[before.names.index('.text')]
    b = after.bytes[after.names.index('.text')]
    changed = set(range(0x183c, 0x1848)) | set(range(0x1ff0, 0x1ff8)) | set(range(0x21dc, 0x21e4))
    assert all(a[i] == b[i] for i in range(len(a)) if i not in changed)
    assert before.bytes[before.names.index('.note.gnu.build-id')] != after.bytes[after.names.index('.note.gnu.build-id')]
    assert {s[0] for s in before.symbols if s[4] == 0} == {s[0] for s in after.symbols if s[4] == 0}
    for i, section in enumerate(after.sections):
        if section[1] == 9 and after.sections[section[7]][2] & 2:
            for _, info in struct.iter_unpack('<II', after.bytes[i]):
                if info & 255 == 4:
                    assert after.symbols[info >> 8][4] == section[7], 'R26 crosses sections'
    for bad in (stock[:-1], bytes([stock[0] ^ 1]) + stock[1:], patched):
        try:
            patcher.patch_module(bad)
            raise AssertionError('accepted unknown/already patched input')
        except ValueError:
            pass
    # Exercise local opcode/relocation guards independently of the SHA guard.
    e = patcher.Elf(stock)
    struct.pack_into('<I', e.by_name['.text']['data'], 0x1ff0, 0)
    try:
        patcher.validate_stock(e)
        raise AssertionError('accepted changed hook')
    except ValueError:
        pass
    e = patcher.Elf(stock)
    relocations = e.by_name['.rel.text']['data']
    for offset in range(0, len(relocations), 8):
        site, info = struct.unpack_from('<II', relocations, offset)
        if site == 0x2114:
            struct.pack_into('<II', relocations, offset, site, info ^ 3)
            break
    try:
        patcher.validate_stock(e)
        raise AssertionError('accepted changed wait relocation')
    except ValueError:
        pass
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stockfs', type=Path)
    ap.add_argument('--module', type=Path, help='also attest an already packaged module')
    args = ap.parse_args()
    stock = (args.stockfs / patcher.MODULE_PATH).read_bytes()
    patched = patcher.patch_module(stock)
    assert patched == patcher.patch_module(stock), 'non-deterministic output'
    if args.module:
        assert patched == args.module.read_bytes(), 'packaged module differs'
    controls = branch_control()
    regions = relocation_control()
    count = structural_tests(stock, patched)
    for base, kernel in [(0xc0001000, 0x80000000), (0xc1007800, 0x81000000),
                         (0xc200f800, 0x82000000)]:
        count += semantic_tests(stock, patched, base, kernel)
    print(json.dumps(dict(cases=count, result='PASS', branch_control=controls, relocation_control=regions,
                         sha256=hashlib.sha256(patched).hexdigest()), indent=2))


if __name__ == '__main__':
    main()
