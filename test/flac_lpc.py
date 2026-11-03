#!/usr/bin/env python3
"""Compare emitted LPC MIPS against pinned stock hciplayer; never needs a device.

Requires unicorn==2.1.4 and the normal clang/ld.lld build tools. Example:
  python test/flac_lpc.py /path/to/stock-hciplayer --out /mnt/e/q2-work/flac-lab/lpc
Use --elf /path/to/peq-player.elf to test the integrated payload instead.
Counts executed MIPS instructions, not cycles, wall-clock speed, or power.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import struct
import sys
import tempfile

from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_BLOCK
from unicorn.mips_const import (
    UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3,
    UC_MIPS_REG_S0, UC_MIPS_REG_S1, UC_MIPS_REG_S2, UC_MIPS_REG_S3,
    UC_MIPS_REG_S4, UC_MIPS_REG_S5, UC_MIPS_REG_S6, UC_MIPS_REG_S7,
    UC_MIPS_REG_FP, UC_MIPS_REG_GP, UC_MIPS_REG_SP, UC_MIPS_REG_RA, UC_MIPS_REG_PC,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build import FLAGS, ROOT, run, segments, symbols
from peq import PLAYER_SHA

STOCK = {32: 0x5ba9bc, 64: 0x5bab3c}
DATA, CO, SP, END = 0x2001000, 0x2043000, 0x210f000, 0x3000000
SAVED = (
    UC_MIPS_REG_S0, UC_MIPS_REG_S1, UC_MIPS_REG_S2, UC_MIPS_REG_S3,
    UC_MIPS_REG_S4, UC_MIPS_REG_S5, UC_MIPS_REG_S6, UC_MIPS_REG_S7,
    UC_MIPS_REG_FP, UC_MIPS_REG_GP,
)


def compile_payload(out):
    obj, elf = out / 'flac-lpc.o', out / 'flac-lpc.elf'
    run('clang', *FLAGS, '-c', ROOT / 'patch/flac_lpc.c', '-o', obj)
    run('ld.lld', '-m', 'elf32ltsmip', '-Ttext=0xe10000',
        '--entry=flac_lpc32', obj, '-o', elf)
    return elf


def load_images(u, *images):
    """Map shared LOAD pages once, then copy each segment's exact file bytes.

    The integrated linker script places text, rodata and data in separate LOAD
    segments sharing their boundary page. Mapping every segment independently
    rejects that valid ELF; skipping its mem_write would silently lose content.
    """
    pages, loads = set(), []
    for image in images:
        for _, (kind, off, base, _, filesz, memsz, _, _) in segments(image):
            if kind != 1 or not memsz:
                continue
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
    for base, memsz, data in loads:
        u.mem_write(base, data)  # Fresh mappings already zero each segment's BSS.
        assert bytes(u.mem_read(base, len(data))) == data


class Machine:
    def __init__(self, stock, payload):
        self.u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        load_images(self.u, stock, payload)
        self.u.mem_map(0x2000000, 0x200000)
        self.u.mem_map(END, 0x10000)
        self.count = 0
        self.u.hook_add(UC_HOOK_BLOCK, self._block)

    def _block(self, uc, address, size, user):
        self.count += size // 4  # MIPS32 blocks include their branch delay slot.

    def call(self, address, values, coefficients, order, shift, n):
        self.count = 0
        u = self.u
        data = (b'Q' * 32 + struct.pack('<%dI' % len(values),
                                      *(v & 0xffffffff for v in values)) + b'Z' * 32)
        coeff = struct.pack('<%di' % len(coefficients), *coefficients)
        u.mem_write(DATA - 32, data)
        u.mem_write(CO, coeff)
        u.mem_write(SP + 16, struct.pack('<I', n))
        for k, reg in enumerate(SAVED):
            u.reg_write(reg, 0x10000000 + k)
        for reg, value in (
            (UC_MIPS_REG_A0, DATA), (UC_MIPS_REG_A1, CO),
            (UC_MIPS_REG_A2, order), (UC_MIPS_REG_A3, shift),
            (UC_MIPS_REG_SP, SP), (UC_MIPS_REG_RA, END),
        ):
            u.reg_write(reg, value)
        u.emu_start(address, END, count=10000000)
        assert u.reg_read(UC_MIPS_REG_PC) == END, 'did not return'
        assert u.reg_read(UC_MIPS_REG_SP) == SP, 'stack not restored'
        for k, reg in enumerate(SAVED):
            assert u.reg_read(reg) == 0x10000000 + k, ('saved register', hex(address), reg)
        raw = bytes(u.mem_read(DATA - 32, len(data)))
        assert raw[:32] == data[:32] and raw[-32:] == data[-32:], 'output canary'
        assert raw[32:32 + 4 * order] == data[32:32 + 4 * order], 'warm-ups changed'
        assert bytes(u.mem_read(CO, len(coeff))) == coeff, 'coefficients changed'
        return raw, self.count


def reference(values, coefficients, order, shift, bits):
    """Independent integer oracle, restricted to the specialized shift range."""
    out = [v & 0xffffffff for v in values]
    mask = (1 << bits) - 1
    for i in range(order, len(out)):
        total = 0
        for k in range(order):
            sample = out[i - order + k]
            sample -= (sample >> 31) << 32
            total = (total + sample * coefficients[k]) & mask
        total -= (total >> (bits - 1)) << bits
        out[i] = (out[i] + (total >> shift)) & 0xffffffff
    return struct.pack('<%dI' % len(out), *out)


def verify(stock, payload, entries):
    machine = Machine(stock, payload)
    rng = random.Random(0x514c5043)
    cases = 0

    def compare(bits, order, shift, values, coefficients, oracle=True):
        a, stock_count = machine.call(STOCK[bits], values, coefficients, order, shift, len(values))
        b, candidate_count = machine.call(entries[bits], values, coefficients, order, shift, len(values))
        assert a == b, ('binary mismatch', bits, order, shift, len(values))
        if oracle and shift <= 31:
            assert b[32:-32] == reference(values, coefficients, order, shift, bits), (
                'oracle mismatch', bits, order, shift, len(values))
        return stock_count, candidate_count

    # Both accumulator widths, every legal order, short/odd tails, and 24-bit inputs.
    # The shift 32/63 cases exercise unchanged-stock fallback outside FLAC's usual range.
    for bits in STOCK:
        for order in range(1, 33):
            for n in sorted({order, order + 1, order + 2, order + 3, order + 15, 65, 128}):
                if n < order:
                    continue
                for shift in (0, 1, 7, 15, 31, 32, 63):
                    for width in (16, 24, 32):
                        values = [rng.randrange(-(1 << (width - 1)), 1 << (width - 1)) for _ in range(n)]
                        coefficients = [rng.randrange(-32768, 32768) for _ in range(order)]
                        compare(bits, order, shift, values, coefficients)
                        cases += 1
    print(f'{cases} randomized binary cases passed', flush=True)

    # Large signed coefficients force 32/64-bit wraparound, beyond valid FLAC precision.
    for bits in STOCK:
        for order in range(1, 33):
            for shift in (0, 1, 15, 31):
                for value in (-2147483648, 2147483647, -8388608, 8388607, -1, 0, 1):
                    coefficients = [-2147483648 if k % 2 else 2147483647 for k in range(order)]
                    compare(bits, order, shift, [value] * (order + 33), coefficients)
                    cases += 1
    print(f'{cases} total binary cases passed; o32 registers, canaries, warm-ups and oracle checked', flush=True)

    rows = []
    for bits in STOCK:
        for order in range(1, 13):
            values = [rng.randrange(-8388608, 8388608) for _ in range(4096)]
            coefficients = [rng.randrange(-128, 128) for _ in range(order)]
            before, after = compare(bits, order, 7, values, coefficients)
            assert after < before, ('specialization regressed', bits, order, before, after)
            rows.append(dict(accumulator_bits=bits, order=order, blocksize=len(values),
                             stock_instructions=before, candidate_instructions=after,
                             reduction_percent=round(100 * (1 - after / before), 2)))
    return dict(cases=cases, benchmark=rows,
                limit='LPC reconstruction instruction counts only; not cycles, full decoder time, or mW.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('--elf', type=Path, help='already linked player payload containing both LPC symbols')
    parser.add_argument('--out', type=Path, help='scratch/result directory; default is a temporary directory')
    args = parser.parse_args()
    stock = args.stock.read_bytes()
    assert hashlib.sha256(stock).hexdigest() == PLAYER_SHA, 'unexpected stock hciplayer'
    temporary = None
    if args.out:
        out = args.out
        out.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix='q2-flac-lpc-')
        out = Path(temporary.name)
    elf = args.elf or compile_payload(out)
    syms = symbols(elf)
    report = verify(stock, elf.read_bytes(), {bits: syms[f'flac_lpc{bits}'] for bits in STOCK})
    report.update(stock_sha256=PLAYER_SHA,
                  source_sha256=hashlib.sha256((ROOT / 'patch/flac_lpc.c').read_bytes()).hexdigest(),
                  payload_sha256=hashlib.sha256(elf.read_bytes()).hexdigest())
    (out / 'lpc-results.json').write_text(json.dumps(report, indent=2) + '\n')
    for row in report['benchmark']:
        print(f"LPC{row['accumulator_bits']} order {row['order']:2}: "
              f"{row['stock_instructions']:6} -> {row['candidate_instructions']:6} instructions "
              f"({row['reduction_percent']:5.2f}% fewer)")
    if args.out:
        print(f"Report: {out / 'lpc-results.json'}")
    if temporary:
        temporary.cleanup()


if __name__ == '__main__':
    main()
