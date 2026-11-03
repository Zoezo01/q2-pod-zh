#!/usr/bin/env python3
"""Execute the stock and replacement FLAC output routines in Unicorn.
Counts measure instructions/data accesses, not target cycles or watts.
Usage: python test/flac_output.py STOCK --work SCRATCH [--payload LINKED_ELF]
"""
import argparse
import hashlib
import json
import pathlib
import random
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from build import FLAGS, ROOT
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import UC_MIPS_REG_0, UC_MIPS_REG_PC

SHA = "9c3f8c6d01f1ba62392622f6098b06a36b3e4f022a5468eaca6a5803e74f8e11"
MODES = {"s16i": (0x5b7e80, 2, False), "s32i": (0x5b8d18, 4, False),
         "s16p": (0x5b8ba0, 2, True), "s32p": (0x5ba83c, 4, True)}
PTRS, INPUT, OUTPUT, STRIDE = 0x2000000, 0x2100000, 0x2500000, 0x50000
STACK, SP, RETURN = 0x4000000, 0x4008000, 0x5000000
GUARD = bytes([0xa5]) * 32

def word(value):
    return struct.pack("<I", value & 0xffffffff)

def elf(path):
    data = path.read_bytes()
    h = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    assert h[0][:6] == b"\x7fELF\x01\x01"
    segments, symbols = [], {}
    for i in range(h[10]):
        p = struct.unpack_from("<IIIIIIII", data, h[5] + i * h[9])
        if p[0] == 1:
            segments.append((p[2], data[p[1]:p[1] + p[4]]))
    sections = [struct.unpack_from("<IIIIIIIIII", data, h[6] + i * h[11])
                for i in range(h[12])]
    for s in sections:
        if s[1] != 2:
            continue
        t = sections[s[6]]
        strings = data[t[4]:t[4] + t[5]]
        for off in range(s[4], s[4] + s[5], s[9]):
            name, value = struct.unpack_from("<II", data, off)
            symbols[strings[name:].split(b"\0", 1)[0].decode()] = value
    return segments, symbols

def compile_payload(work):
    obj, payload, script = [work / n for n in ("flac_output.o", "flac_output.elf", "output.ld")]
    subprocess.run(["clang", *FLAGS, "-mnan=2008", "-ffunction-sections", "-c",
                    str(ROOT / "patch/flac_output.c"), "-o", str(obj)], check=True)
    script.write_text("SECTIONS { . = 0x1000000; .text : { *(.text*) } "
                      ".rodata : { *(.rodata*) } .data : { *(.data*) } "
                      ".bss : { *(.bss*) } /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) } }\n")
    subprocess.run(["ld.lld", "-m", "elf32ltsmip", "-T", str(script),
                    "-e", "flac_output_s16i", str(obj), "-o", str(payload)], check=True)
    return payload

class Machine:
    def __init__(self, stock, payload):
        self.uc = u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        u.mem_map(0x400000, 0x1c00000)
        u.mem_map(PTRS, 0x1000000)
        u.mem_map(STACK, 0x10000)
        u.mem_map(RETURN, 0x1000)
        for path in (stock, payload):
            for address, data in elf(path)[0]:
                u.mem_write(address, data)
        u.hook_add(UC_HOOK_CODE, self.code)
        self.instructions = {}

    def code(self, u, address, size, data):
        self.counts["instructions"] += 1
        # Unicorn 2.1.4's MIPS read hook mis-handles loads in branch delay
        # slots. Decode executed load/store instructions instead; this also
        # checks their effective addresses without changing emulated state.
        if address not in self.instructions:
            ins = int.from_bytes(u.mem_read(address, 4), "little")
            self.instructions[address] = (ins >> 26, (ins >> 21) & 31,
                                           (ins & 0xffff) - (0x10000 if ins & 0x8000 else 0))
        op, base, offset = self.instructions[address]
        widths = {0x20: 1, 0x21: 2, 0x23: 4, 0x24: 1, 0x25: 2,
                  0x28: 1, 0x29: 2, 0x2b: 4}
        if op not in widths:
            assert op not in (0x22, 0x26, 0x2a, 0x2e), "unexpected unaligned memory op"
            return
        address = (u.reg_read(UC_MIPS_REG_0 + base) + offset) & 0xffffffff
        size, read = widths[op], op < 0x28
        if STACK <= address and address + size <= STACK + 0x10000:
            self.counts["stack_accesses"] += 1
            return
        ranges = self.read_ranges if read else self.write_ranges
        assert any(lo <= address and address + size <= hi for lo, hi in ranges), (
            "bounds", read, hex(address), size)
        key = ("pointer_reads" if address < PTRS + 0x100 else "sample_reads"
               ) if read else "output_writes"
        self.counts[key] += 1

    def run(self, address, mode, channels, length, shift, samples, offset):
        u = self.uc
        _, width, planar = MODES[mode]
        frames, active = max(length, 0), max(channels, 0)
        sources = [INPUT + i * STRIDE for i in range(8)]
        dests = [OUTPUT + i * STRIDE + offset for i in range(8)]
        u.mem_write(PTRS, b"".join(map(word, dests)))
        u.mem_write(PTRS + 0x80, b"".join(map(word, sources)))
        self.read_ranges = [(PTRS, PTRS + 32), (PTRS + 0x80, PTRS + 0xa0)]
        source_bytes = [b"".join(map(word, row)) for row in samples]
        for i, raw in enumerate(source_bytes):
            u.mem_write(sources[i], raw)
            self.read_ranges.append((sources[i], sources[i] + len(raw)))
        self.write_ranges = ([(dests[i], dests[i] + frames * width) for i in range(active)]
                             if planar else [(dests[0], dests[0] + frames * active * width)])
        for lo, hi in self.write_ranges:
            u.mem_write(lo - 32, GUARD + b"\xcc" * (hi - lo) + GUARD)
        preserved = {r: 0x70000000 + r * 16 for r in (*range(16, 24), 28, 30)}
        for r in range(1, 32):
            u.reg_write(UC_MIPS_REG_0 + r, 0x60000000 + r)
        for r, v in preserved.items():
            u.reg_write(UC_MIPS_REG_0 + r, v)
        for r, v in {4: PTRS, 5: PTRS + 0x80, 6: channels, 7: length,
                     29: SP, 31: RETURN, 25: address}.items():
            u.reg_write(UC_MIPS_REG_0 + r, v & 0xffffffff)
        u.mem_write(SP + 16, word(shift))
        self.counts = dict.fromkeys(("instructions", "pointer_reads", "sample_reads",
                                    "output_writes", "stack_accesses"), 0)
        try:
            u.emu_start(address, RETURN, count=30_000_000)
        except Exception as error:
            raise AssertionError((mode, channels, length, shift, hex(address),
                                  hex(u.reg_read(UC_MIPS_REG_PC)), self.counts)) from error
        assert u.reg_read(UC_MIPS_REG_PC) == RETURN, "instruction limit"
        assert u.reg_read(UC_MIPS_REG_0 + 29) == SP
        for r, v in preserved.items():
            assert u.reg_read(UC_MIPS_REG_0 + r) == v, ("callee-saved", r)
        result = []
        for lo, hi in self.write_ranges:
            assert bytes(u.mem_read(lo - 32, 32)) == GUARD
            assert bytes(u.mem_read(hi, 32)) == GUARD
            result.append(bytes(u.mem_read(lo, hi - lo)) if hi > lo else b"")
        for i, raw in enumerate(source_bytes):
            assert bytes(u.mem_read(sources[i], len(raw))) == raw, "input modified"
        return result, self.counts.copy()

def reference(mode, channels, length, shift, samples):
    _, width, planar = MODES[mode]
    def pack(value):
        return ((value << (shift & 31)) & ((1 << (width * 8)) - 1)).to_bytes(width, "little")
    if planar:
        return [b"".join(map(pack, row)) for row in samples]
    return [b"".join(pack(samples[c][i]) for i in range(max(length, 0))
                     for c in range(max(channels, 0)))]

def check(stock, payload, work):
    assert hashlib.sha256(stock.read_bytes()).hexdigest() == SHA, "wrong stock player"
    symbols = elf(payload)[1]
    machine, rng, cases, rows = Machine(stock, payload), random.Random(0x51464c41), [], []
    for mode in MODES:
        for channels in (1, 2):
            for length in (-1, 0, 1, 2, 3, 15, 16, 17, 31, 257):
                for shift in (0, 1, 8, 15, 16, 24, 31, 32, -1):
                    cases.append((mode, channels, length, shift, 0))
            # Non-word-aligned S16 output and maximum FLAC block size.
            for length in (4096, 65535):
                cases.append((mode, channels, length, 0 if "16" in mode else 8,
                              2 if "16" in mode else 0))
        for channels in (-1, 0, 3, 8):
            for length in (0, 1, 17):
                cases.append((mode, channels, length, 8, 0))
    extremes = [0, 1, -1, -32768, 32767, -8388608, 8388607,
                -2147483648, 2147483647, 0x55555555, 0xaaaaaaaa]
    for mode, channels, length, shift, offset in cases:
        # Long blocks include ordinary signed 16/24-bit random PCM; shorter
        # cases stress every bit of the vendor int32 intermediate buffers.
        bits = (16 if "16" in mode else 24) if length >= 4096 else 32
        samples = [[extremes[(i + c) % len(extremes)] if i < len(extremes)
                    else rng.randrange(-(1 << (bits - 1)), 1 << (bits - 1))
                    for i in range(max(length, 0))]
                   for c in range(max(channels, 0))]
        old, before = machine.run(MODES[mode][0], mode, channels, length, shift, samples, offset)
        new, after = machine.run(symbols["flac_output_" + mode], mode, channels,
                                 length, shift, samples, offset)
        assert old == new == reference(mode, channels, length, shift, samples), (
            mode, channels, length, shift, offset)
        if length >= 4096:
            assert after["instructions"] < before["instructions"]
            assert after["pointer_reads"] < before["pointer_reads"]
            assert after["sample_reads"] == before["sample_reads"]
            assert after["output_writes"] == before["output_writes"]
            rows.append(dict(mode=mode, channels=channels, frames=length,
                             stock=before, optimized=after))
    report = dict(cases=len(cases), seed="0x51464c41", stock_sha256=SHA, metrics=rows,
                  note="Instruction/data-access counts, not hardware cycles or watts.")
    (work / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"FLAC output: {len(cases)} stock/new/reference cases passed; ABI and bounds checked")
    for row in rows:
        if row["frames"] == 4096:
            print(row)
    return report

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stock", type=pathlib.Path)
    p.add_argument("--work", type=pathlib.Path)
    p.add_argument("--payload", type=pathlib.Path, help="production linked ELF, with symbols")
    a = p.parse_args()
    if a.work:
        a.work.mkdir(parents=True, exist_ok=True)
        check(a.stock, a.payload or compile_payload(a.work), a.work)
    else:
        with tempfile.TemporaryDirectory(prefix="q2-flac-output-") as tmp:
            work = pathlib.Path(tmp)
            check(a.stock, a.payload or compile_payload(work), work)

if __name__ == "__main__":
    main()
