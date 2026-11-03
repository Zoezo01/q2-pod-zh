#!/usr/bin/env python3
"""Execute stock MIPS FLAC synchronization/residual code against flac_sync_check.

Usage: python test/flac_sync.py --stock STOCK_HCIPLAYER --out SCRATCH_DIRECTORY
Or pass --payload BUILT_PEQ_ELF to test the integrated payload without compiling.
The two libc mutex entries are mocked as serialized operations. Instruction counts
exclude their real implementation; they are not cycle, CPU, or power measurements.
No device is accessed. Outputs stay in the explicitly supplied scratch directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3,
    UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_V0, UC_MIPS_REG_PC,
    UC_MIPS_REG_S0, UC_MIPS_REG_S1, UC_MIPS_REG_S2, UC_MIPS_REG_S3,
    UC_MIPS_REG_S4, UC_MIPS_REG_S5, UC_MIPS_REG_S6, UC_MIPS_REG_S7, UC_MIPS_REG_FP)

STOCK_SHA = '9c3f8c6d01f1ba62392622f6098b06a36b3e4f022a5468eaca6a5803e74f8e11'
CHECK, CONSUME, RESIDUAL = 0x42b6b0, 0x42b66c, 0x5b6334
LOCK, UNLOCK, MUTEX = 0xadb580, 0xadb3f0, 0xb10db4
HEARTBEAT, STOP, FATAL = 0xb10e68, 0xb10e6c, 0xb10e70
CALLS = (0x5b64a4, 0x5b6558, 0x5b6740, 0x5b67a0, 0x5b6810)
STACK, DATA, RETURN = 0x10000000, 0x11000000, 0x12000000
SAVED = (UC_MIPS_REG_S0, UC_MIPS_REG_S1, UC_MIPS_REG_S2, UC_MIPS_REG_S3,
         UC_MIPS_REG_S4, UC_MIPS_REG_S5, UC_MIPS_REG_S6, UC_MIPS_REG_S7, UC_MIPS_REG_FP)
ROOT = Path(__file__).resolve().parents[1]


def elf(raw):
    assert raw[:7] == b'\x7fELF\x01\x01\x01', 'little-endian ELF32 required'
    phoff, shoff = struct.unpack_from('<II', raw, 28)
    phsize, phnum, shsize, shnum = struct.unpack_from('<HHHH', raw, 42)
    segments = [struct.unpack_from('<8I', raw, phoff + i * phsize) for i in range(phnum)]
    sections = [struct.unpack_from('<10I', raw, shoff + i * shsize) for i in range(shnum)]
    names = {}
    for section in sections:
        _, kind, _, _, off, size, link, _, _, entsize = section
        if kind != 2:
            continue
        strings = sections[link]
        table = raw[strings[4]:strings[4]+strings[5]]
        for pos in range(off, off + size, entsize):
            name, value, _, _, _, _ = struct.unpack_from('<IIIBBH', raw, pos)
            label = table[name:].split(b'\0', 1)[0].decode()
            if label:
                names[label] = value
    return segments, names


def at(raw, address, size):
    for kind, off, base, _, filesz, _, _, _ in elf(raw)[0]:
        if kind == 1 and base <= address and address + size <= base + filesz:
            return raw[off + address - base:off + address - base + size]
    raise AssertionError(hex(address))


def compile_payload(out):
    cc = os.environ.get('CC', 'clang')
    flags = ['--target=mipsel-linux-gnu', '-march=mips32r2', '-mabi=32', '-mfp64',
             '-mnan=2008', '-mno-abicalls', '-fno-pic', '-G0', '-ffreestanding',
             '-fno-builtin', '-fno-stack-protector', '-fno-unwind-tables',
             '-fno-asynchronous-unwind-tables', '-Oz', '-Wall', '-Wextra', '-Werror']
    obj, linked = out / 'flac_sync.o', out / 'flac_sync.elf'
    linker = out / 'flac_sync.ld'
    linker.write_text('SECTIONS { . = 0xe10000; .text : { *(.text*) } '
                      '.rodata : { *(.rodata*) } .data : { *(.data*) *(.sdata*) } '
                      '.bss : { *(.bss*) *(.sbss*) } /DISCARD/ : { *(.comment) '
                      '*(.note*) *(.pdr) *(.mdebug*) *(.MIPS.abiflags) *(.reginfo) } }\n')
    subprocess.run([cc, *flags, '-c', str(ROOT / 'patch/flac_sync.c'), '-o', str(obj)], check=True)
    subprocess.run(['ld.lld', '-m', 'elf32ltsmip', '-T', str(linker), '-e', 'flac_sync_check',
                    str(obj), '-o', str(linked)], check=True)
    return linked.read_bytes()


class Machine:
    def __init__(self, stock, payload, patched=False):
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        self.target = elf(payload)[1]['flac_sync_check'] if patched else CHECK
        pages = set()
        for raw in (stock, payload):
            for kind, _, base, _, _, memsz, _, _ in elf(raw)[0]:
                if kind == 1 and memsz:
                    pages.update(range(base & ~4095, (base + memsz + 4095) & ~4095, 4096))
        for base, size in ((STACK, 0x10000), (DATA, 0x20000), (RETURN, 0x1000)):
            pages.update(range(base, base + size, 4096))
        ordered = sorted(pages)
        start = end = ordered[0]
        for page in ordered[1:] + [ordered[-1] + 8192]:
            if page != end + 4096:
                self.uc.mem_map(start, end + 4096 - start)
                start = page
            end = page
        for raw in (stock, payload):
            for kind, off, base, _, filesz, _, _, _ in elf(raw)[0]:
                if kind == 1 and filesz:
                    self.uc.mem_write(base, raw[off:off + filesz])
        if patched:
            word = 0x0c000000 | ((self.target >> 2) & 0x03ffffff)
            for site in CALLS:
                assert at(stock, site, 4) == struct.pack('<I', 0x0c10adac)
                self.uc.mem_write(site, struct.pack('<I', word))
        self.uc.hook_add(UC_HOOK_CODE, self.code)
        self.uc.hook_add(UC_HOOK_MEM_READ, self.read)
        self.reset()

    def byte(self, address, value=None):
        if value is not None:
            self.uc.mem_write(address, bytes([value & 255]))
        return self.uc.mem_read(address, 1)[0]

    def word(self, address, value=None):
        if value is not None:
            self.uc.mem_write(address, struct.pack('<I', value & 0xffffffff))
        return struct.unpack('<I', self.uc.mem_read(address, 4))[0]

    def reset(self, heartbeat=0, fatal=0, stop=0):
        self.byte(HEARTBEAT, heartbeat)
        self.word(FATAL, fatal)
        self.word(STOP, stop)
        self.instructions = self.locks = self.unlocks = self.callbacks = self.stock_calls = 0
        self.held = False
        self.on_step = self.on_callback = self.on_lock = self.on_unlock = None
        self.trace, self.reads, self.sites = [], [], set()

    def read(self, uc, access, address, size, value, data):
        if address in (HEARTBEAT, STOP, FATAL):
            self.reads.append(address)

    def code(self, uc, address, size, data):
        self.instructions += 1
        self.trace.append(address)
        if self.on_step:
            self.on_step(self, address)
        if address == self.target:
            self.callbacks += 1
            site = uc.reg_read(UC_MIPS_REG_RA) - 8
            if site in CALLS:
                self.sites.add(site)
            if self.on_callback:
                self.on_callback(self)
        if address == CHECK:
            self.stock_calls += 1
        if address == LOCK:
            if self.on_lock:
                self.on_lock(self)
            assert not self.held, 'recursive/unscheduled contended mutex'
            assert uc.reg_read(UC_MIPS_REG_A0) == MUTEX
            self.held = True
            self.locks += 1
            uc.reg_write(UC_MIPS_REG_V0, 0)
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
        elif address == UNLOCK:
            assert self.held
            self.held = False
            self.unlocks += 1
            if self.on_unlock:
                self.on_unlock(self)
            uc.reg_write(UC_MIPS_REG_V0, 0)
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def call(self, address, args=()):
        self.uc.reg_write(UC_MIPS_REG_SP, STACK + 0xff00)
        self.uc.reg_write(UC_MIPS_REG_RA, RETURN)
        for i, reg in enumerate(SAVED):
            self.uc.reg_write(reg, 0x12340000 + i)
        for reg, value in zip((UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3), (*args, 0, 0, 0, 0)):
            self.uc.reg_write(reg, value)
        self.uc.emu_start(address, RETURN, count=2000000)
        assert self.uc.reg_read(UC_MIPS_REG_PC) == RETURN, 'instruction budget exhausted or unexpected stop'
        assert not self.held
        assert all(self.uc.reg_read(reg) == 0x12340000 + i for i, reg in enumerate(SAVED))
        return self.uc.reg_read(UC_MIPS_REG_V0)

    def metric(self):
        return dict(instructions=self.instructions, mutex_pairs=self.locks,
                    residual_checks=self.callbacks, stock_checks=self.stock_calls)


def consume(reader, other):
    reader.reset(other.byte(HEARTBEAT), other.word(FATAL), other.word(STOP))
    result = reader.call(CONSUME)
    other.byte(HEARTBEAT, reader.byte(HEARTBEAT))
    return result


def binary(value, width):
    return format(value & ((1 << width) - 1), f'0{width}b') if width else ''


def residual_bits(values, parameter=0, method=0, escape=None, warmup=0, partitions=0):
    count = len(values) + warmup
    assert count % (1 << partitions) == 0
    bits = binary(method, 2) + binary(partitions, 4)
    offset = 0
    for part in range(1 << partitions):
        n = count // (1 << partitions) - (warmup if part == 0 else 0)
        selected = values[offset:offset+n]
        offset += n
        width = 4 + method
        bits += binary((1 << width) - 1 if escape is not None else parameter, width)
        if escape is not None:
            bits += binary(escape, 5) + ''.join(binary(value, escape) for value in selected)
        else:
            for value in selected:
                unsigned = (value << 1) ^ (value >> 31)
                bits += '0' * (unsigned >> parameter) + '1' + binary(unsigned, parameter)
    return bits


def run_residual(machine, bits, count, warmup=0, setup=None):
    machine.reset()
    raw = int(bits + '0' * ((-len(bits)) % 8), 2).to_bytes((len(bits) + 7)//8, 'big')
    context, stream, output = DATA, DATA + 0x1000, DATA + 0x10000
    machine.uc.mem_write(context, bytes(256))
    machine.uc.mem_write(stream, raw + bytes(16))
    machine.uc.mem_write(output, struct.pack('<'+'i'*count, *([99]*warmup + [0x12345678]*(count-warmup))))
    for off, value in ((0x2c, stream), (0x34, 0), (0x38, len(bits)), (0x40, count)):
        machine.word(context + off, value)
    if setup:
        setup(machine)
    result = machine.call(RESIDUAL, (context, output, warmup))
    decoded = bytes(machine.uc.mem_read(output, count * 4))
    return result, decoded, machine.word(context + 0x34)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--payload', type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    stock = args.stock.read_bytes()
    assert hashlib.sha256(stock).hexdigest() == STOCK_SHA, 'unexpected stock hciplayer'
    payload = args.payload.read_bytes() if args.payload else compile_payload(args.out)
    original, patched, reader = (Machine(stock, payload, flag) for flag in (False, True, False))
    checks = 0

    # Exact stock truth table, including non-boolean states, heartbeat normalization,
    # ABI preservation, and FATAL == 1 short-circuiting the STOP read.
    for heartbeat in (0, 1, 2, 255):
        for fatal in (0, 1, 2, 0xffffffff):
            for stop in (0, 1, 2, 0xffffffff):
                outcomes = []
                for machine in (original, patched):
                    machine.reset(heartbeat, fatal, stop)
                    result = machine.call(machine.target)
                    outcomes.append((result, machine.byte(HEARTBEAT)))
                    assert [a for a in machine.reads if a != HEARTBEAT] == ([FATAL] if fatal == 1 else [FATAL, STOP])
                assert outcomes[0] == outcomes[1] == (int(fatal == 1 or stop == 1), 1)
                assert patched.locks == (heartbeat != 1)
                checks += 1

    # Interrupt each compiled callback instruction with the actual stock heartbeat
    # consumer. Requests arriving while the mutex is held wait until its unlock.
    overlaps = 0
    for heartbeat in (0, 1):
        for machine in (original, patched):
            machine.reset(heartbeat)
            machine.call(machine.target)
            length = machine.instructions
            for slot in range(1, length + 2):
                machine.reset(heartbeat)
                event = {'fired': False, 'pending': False, 'result': None}
                def complete(m):
                    event['result'] = consume(reader, m)
                    event['pending'] = False
                def step(m, address):
                    if m.instructions == slot and not event['fired']:
                        event['fired'] = True
                        if m.held:
                            event['pending'] = True
                        else:
                            complete(m)
                def unlocked(m):
                    if event['pending']:
                        complete(m)
                machine.on_step, machine.on_unlock = step, unlocked
                assert machine.call(machine.target) == 0
                if not event['fired']:
                    complete(machine)
                assert not event['pending']
                # The concurrent operations must match one legal serialization:
                # publish-before-consume => observed 1, final 0; reverse => initial, 1.
                assert (event['result'], machine.byte(HEARTBEAT)) in {(1, 0), (heartbeat, 1)}
                overlaps += 1

    # Specifically pause a consumer between its byte read and clear. A fast check
    # may finish while that consumer owns the mutex: it publishes no new data.
    for paused_at in (0x42b690, 0x42b694):
        reader.reset(1)
        def pause(m, address):
            if address == paused_at:
                m.uc.emu_stop()
        reader.on_step = pause
        reader.uc.reg_write(UC_MIPS_REG_SP, STACK + 0xff00)
        reader.uc.reg_write(UC_MIPS_REG_RA, RETURN)
        reader.uc.emu_start(CONSUME, RETURN, count=1000)
        assert reader.held and reader.uc.reg_read(UC_MIPS_REG_PC) == paused_at
        patched.reset(reader.byte(HEARTBEAT))
        def finish_reader(m=None):
            reader.on_step = None
            reader.uc.emu_start(paused_at, RETURN, count=1000)
            assert not reader.held and reader.uc.reg_read(UC_MIPS_REG_V0) == 1
            if m is not None:
                m.byte(HEARTBEAT, reader.byte(HEARTBEAT))
        if paused_at == 0x42b694:  # cleared, but not unlocked: slow path waits.
            patched.on_lock = finish_reader
        assert patched.call(patched.target) == 0
        if paused_at == 0x42b690:
            assert patched.locks == 0
            finish_reader()
            assert reader.byte(HEARTBEAT) == 0
        else:
            assert patched.locks == 1 and patched.byte(HEARTBEAT) == 1
        checks += 1

    # A request set before any instruction is observed in this check or, if its
    # word was already read, in the immediately following residual check.
    schedules = 0
    for heartbeat in (0, 1):
        patched.reset(heartbeat)
        patched.call(patched.target)
        length = patched.instructions
        for flag in (FATAL, STOP):
            for slot in range(1, length + 2):
                patched.reset(heartbeat)
                def request(m, address):
                    if m.instructions == slot:
                        m.word(flag, 1)
                patched.on_step = request
                first = patched.call(patched.target)
                patched.on_step = None
                patched.word(flag, 1)
                assert first in (0, 1) and patched.call(patched.target) == 1
                schedules += 1

    fixtures = [
        ('rice0', [-17, -2, -1, 0, 1, 2, 17], dict(parameter=0)),
        ('rice4', [-1000, -17, -1, 0, 1, 17, 1000], dict(parameter=4)),
        ('rice14', [-10000, -1, 0, 1, 10000], dict(parameter=14)),
        ('rice2-30', [-(1<<29), -1, 0, 1, 1<<29], dict(parameter=30, method=1)),
        ('escape0', [0]*8, dict(escape=0)),
        ('escape4', [-8, -1, 0, 1, 7], dict(escape=4)),
        ('escape26', [-(1<<25), -1, 0, 1, (1<<25)-1], dict(escape=26)),
        ('partitioned', [-2, -1, 0, 1, 2, 3, 4, 3, 2, 1, 0, -1, -2, -3], dict(parameter=2, warmup=2, partitions=1)),
    ]
    sites = set()
    for name, values, options in fixtures:
        bits = residual_bits(values, **options)
        warmup = options.get('warmup', 0)
        a = run_residual(original, bits, len(values)+warmup, warmup)
        b = run_residual(patched, bits, len(values)+warmup, warmup)
        assert a == b and a[0] == 0, name
        assert a[1] == struct.pack('<'+'i'*(len(values)+warmup), *([99]*warmup + values)), name
        sites.update(patched.sites)
        checks += 1
    # An unterminated unary code reaches the stock sentinel/cancellation path.
    malformed = '0000000000' + '0'*16
    assert run_residual(original, malformed, 8) == run_residual(patched, malformed, 8)
    sites.update(patched.sites)
    assert sites == set(CALLS), [hex(site) for site in sites]
    checks += 1

    # Cancel before every residual callback, via either independently written word.
    # The residual stored in the call's delay slot and the consumed bit index match.
    values = list(range(-16, 16))
    bits = residual_bits(values, parameter=3)
    for flag in (FATAL, STOP):
        for boundary in range(1, len(values)+1):
            def setup(machine):
                def cancel(m):
                    if m.callbacks == boundary:
                        m.word(flag, 1)
                machine.on_callback = cancel
            a = run_residual(original, bits, len(values), setup=setup)
            b = run_residual(patched, bits, len(values), setup=setup)
            assert a == b and a[0] == 0xffffffff
            assert original.callbacks == patched.callbacks == boundary
            checks += 1

    metrics = {}
    values = [0]*8192
    bits = residual_bits(values, parameter=0)
    for period in (0, 2048):
        pair = []
        for label, machine in (('stock', original), ('coalesced', patched)):
            def setup(m):
                if period:
                    def clear(x):
                        if x.callbacks % period == 0:
                            consume(reader, x)
                    m.on_callback = clear
            pair.append(run_residual(machine, bits, len(values), setup=setup))
            metrics[f'{label}_clear_period_{period}'] = machine.metric()
        assert pair[0] == pair[1]
        assert patched.locks == (1 if not period else 1 + len(values)//period)
        checks += 1
    report = dict(checks=checks, heartbeat_interleavings=overlaps, cancellation_schedules=schedules,
                  residual_call_sites=[hex(site) for site in sorted(sites)], metrics=metrics,
                  qualification='Unicorn instruction counts with mocked mutex libc calls; not CPU, cycles, or mW.')
    (args.out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
