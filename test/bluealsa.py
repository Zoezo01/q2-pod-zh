#!/usr/bin/env python3
"""Run the actual MIPS DROP paths with the encoder signal deliberately delayed.

Usage: python test/bluealsa.py BUILD_DIRECTORY (requires the existing Unicorn).
"""
import pathlib, struct, subprocess, sys
from unicorn import UC_HOOK_CODE
from unicorn.mips_const import *
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from build import (BLUEALSA, BT_DROP, BT_DROP_HANDLER, BT_PCM_CALLERS, BT_SILENCE_SAMPLES, bt_silence,
                   machine, read, word)


def check_drop(raw, queued, caller=None):
    u = machine(raw, 0x1000000)
    pcm, transport, state, stop = 0x1000000, 0x1000200, 0x1000400, 0x1000600
    word(u, pcm, transport)
    word(u, pcm + 4, transport + 0x50)
    word(u, state + 0x1c, 48000 * 300)  # the previous track's pacing clock
    word(u, state + 0x30, 100)
    fifo = [queued]
    locked = [False]
    signals, flushes = [], []
    def hook(u, address, size, _):
        if address == 0x414640:  # stock DROP repoll
            u.emu_stop()
            return
        if address == 0x4140b8:  # stock nonblocking io_pcm_flush
            assert u.reg_read(UC_MIPS_REG_A0) == pcm
            batch = min(fifo[0], 32768)
            fifo[0] -= batch
            flushes.append((batch, locked[0]))
            u.reg_write(UC_MIPS_REG_V0, batch // 2)
        elif address == 0x40b738:  # stock send_signal
            assert u.reg_read(UC_MIPS_REG_A0) == transport + 0x50
            assert not locked[0]
            signals.append(u.reg_read(UC_MIPS_REG_A1))
            u.reg_write(UC_MIPS_REG_V0, 0)
        elif address in (0x430690, 0x4303a0):  # PCM fd mutex
            assert u.reg_read(UC_MIPS_REG_A0) == pcm + 0xc
            assert locked[0] == (address == 0x4303a0)
            locked[0] = address == 0x430690
            u.reg_write(UC_MIPS_REG_V0, 0)
        else:
            return
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, hook)
    saved = (UC_MIPS_REG_S0, UC_MIPS_REG_S1, UC_MIPS_REG_S2, UC_MIPS_REG_S7)
    for i, register in enumerate(saved): u.reg_write(register, 0xabcd0000 + i)
    stack = 0x100ff00
    u.reg_write(UC_MIPS_REG_SP, stack)
    u.reg_write(UC_MIPS_REG_A0, pcm)
    u.reg_write(UC_MIPS_REG_RA, stop)
    u.emu_start(BT_DROP, stop, count=1000)
    assert u.reg_read(UC_MIPS_REG_PC) == stop
    assert u.reg_read(UC_MIPS_REG_V0) == 0 and u.reg_read(UC_MIPS_REG_SP) == stack
    assert [u.reg_read(r) for r in saved] == [0xabcd0000 + i for i in range(len(saved))]
    assert signals == [6]
    empty_at_ack = fifo[0] == 0 and all(held for _, held in flushes)
    # New track data is written after the acknowledged DROP, before the encoder
    # finally handles it. The stock handler wrongly discards these samples.
    fifo[0] = 4096
    flush_count = len(flushes)
    u.reg_write(UC_MIPS_REG_S1, pcm)
    u.reg_write(UC_MIPS_REG_S2, state)
    if caller:
        address, offset = caller
        word(u, stack + 0x54, address)
        buffer = 0x1001000
        if offset is not None:
            ffb = state + offset
            for off, value in ((0, buffer), (4, buffer+128), (8, 1024), (12, 2)):
                word(u, ffb+off, value)
            u.reg_write(UC_MIPS_REG_S6, buffer+128)
            u.reg_write(UC_MIPS_REG_S5, 960)
        else:
            word(u, stack+0x40, 0)  # caller's base offset, saved s4
            word(u, stack+0x4c, 128)  # caller's partial byte offset, saved s7
            word(u, stack+0x48, 224)  # caller's remaining samples, saved s6
            u.reg_write(UC_MIPS_REG_S6, buffer+128)
            u.reg_write(UC_MIPS_REG_S5, 224)
    u.emu_start(BT_DROP_HANDLER, 0x41463c, count=1000)
    rewound = True
    if caller:
        rewound = u.reg_read(UC_MIPS_REG_S6) == buffer
        if offset is not None:
            rewound &= read(u, ffb+4)==buffer and u.reg_read(UC_MIPS_REG_S5)==1024
            rewound &= read(u, ffb)==buffer and read(u, ffb+8)==1024 and read(u, ffb+12)==2
        else:
            rewound &= read(u, stack+0x4c)==0 and read(u, stack+0x48)==256 and u.reg_read(UC_MIPS_REG_S5)==256
    # Stock repolls at 0x414640; stop before the imported cancellation function.
    return empty_at_ack and fifo[0] == 4096 and len(flushes) == flush_count and read(u, state + 0x1c) == 0 and read(u, state + 0x30) == 0xffffffff and rewound


def silence_machine(raw):
    """io_poll_and_read_pcm under Unicorn, poll and io_pcm_read scripted: call(poll result, ...)."""
    u = machine(raw, 0x1000000)
    io, pcm, transport, buffer, stop = 0x1000000, 0x1000100, 0x1000300, 0x1001000, 0x100f000
    word(u, pcm + 4, transport)
    log = {}
    def signed(v): return v - (1 << 32 if v >> 31 else 0)
    def hook(u, address, size, _):
        if address == 0x4307a0:  # poll(fds, 2, timeout): a signal first, if scripted
            log['timeout'] = signed(u.reg_read(UC_MIPS_REG_A2))
            log.setdefault('timeouts', []).append(log['timeout'])
            signal = log.pop('signal', None)
            u.mem_write(u.reg_read(UC_MIPS_REG_A0) + 6, struct.pack('<H', signal is not None))
            u.reg_write(UC_MIPS_REG_V0, 1 if signal is not None else log['poll'])
            if signal is not None: log['next'] = signal
        elif address == 0x40b7c4:  # the transport's signal read
            word(u, u.reg_read(UC_MIPS_REG_A1), log.pop('next'))
        elif address == 0x41414c:  # io_pcm_read
            u.reg_write(UC_MIPS_REG_V0, log['got'])
        elif address == 0x42ff70:  # pthread_cond_signal: Drain's waiter woken
            log['drained'] = True
        elif address == 0x42ff00:  # clock_gettime: asrsync_init
            log['asrs'] = True
        elif address not in (0x430ac0, 0x40c7a0):  # setcancelstate, pcm active
            return
        u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
    u.hook_add(UC_HOOK_CODE, hook)
    def call(poll, timeout=-1, frames=1, samples=256, got=64, width=2, signal=None):
        log.clear()
        log.update(poll=poll, got=got, **({'signal': signal} if signal is not None else {}))
        word(u, io + 0x30, timeout)
        word(u, io + 0x1c, frames)
        u.mem_write(pcm + 0x2a, struct.pack('<H', width << 8 | width * 8))
        u.mem_write(buffer, b'\xff' * 2048)
        for register, value in ((UC_MIPS_REG_SP, 0x100e000), (UC_MIPS_REG_A0, io), (UC_MIPS_REG_A1, pcm),
                                (UC_MIPS_REG_A2, buffer), (UC_MIPS_REG_A3, samples), (UC_MIPS_REG_RA, stop)):
            u.reg_write(register, value)
        u.emu_start(0x4145d0, stop, count=20000)
        log['timeout_after'] = signed(read(u, io + 0x30))
        log['zeroed'] = bytes(u.mem_read(buffer, 2048)).index(b'\xff')
        return signed(u.reg_read(UC_MIPS_REG_V0))
    return u, call, log


def check_silence(stock, raw):
    """Drain's timeout, or PAUSE's 100 ms, after real PCM fills silence (stock sends nothing), later
    polls don't wait, real PCM ends it, and with the budget spent or none armed it behaves as stock."""
    _, call, log = silence_machine(stock)
    assert call(1) == 64 and call(0, timeout=100) == 0, 'stock: Drain timeout returns nothing'
    # (the scripted second poll then returns 0, which a real unbounded one wouldn't)
    assert call(0, signal=3) == 0 and log['timeouts'] == [-1, -1], 'stock: PAUSE polls again without a timeout'
    u, call, log = silence_machine(raw)
    quiet = bt_silence()[1]['quiet']
    assert call(1) == 64 and log['timeout'] == -1 and read(u, quiet) == BT_SILENCE_SAMPLES, 'no idle wait'
    assert call(0, timeout=100) == 256 and log['timeout'] == 100 and log['drained'], "Drain's timeout fills"
    assert log['timeout_after'] == -1 and log['zeroed'] == 512, '256 S16 samples, waiter woken'
    assert call(0, frames=0) == 256 and log['timeout'] == 0 and log.get('asrs'), 'filling: no wait, pacing starts'
    assert call(0, width=4) == 256 and log['zeroed'] == 1024, 'S32 (LDAC): 4 bytes a sample'
    assert read(u, quiet) == BT_SILENCE_SAMPLES - 768
    assert call(1, got=0) == 0 and read(u, quiet) == BT_SILENCE_SAMPLES - 768, 'PCM closed: no re-arm'
    word(u, quiet, 100)
    assert call(0) == 256 and read(u, quiet) == 0, 'the budget runs out'
    assert call(1) == 64 and log['timeout'] == -1 and read(u, quiet) == BT_SILENCE_SAMPLES
    assert call(1) == 64 and log['timeout'] == -1, 'real PCM ends the fill'
    assert call(0, signal=3) == 256 and log['timeouts'] == [-1, 100], 'PAUSE (a skip): 100 ms, then the fill'
    assert call(1) == 64
    word(u, quiet, 0)
    assert call(0, timeout=100) == 0 and log['drained'] and log['timeout_after'] == -1, 'no budget: drain as stock'


def check(directory):
    directory = pathlib.Path(directory)
    def extract(name):
        return subprocess.check_output(['unsquashfs', '-cat', str(directory / name), BLUEALSA])
    stock, patched = extract('stock.squashfs'), extract('rootfs.squashfs')
    # Execute stock argument construction independently of the reset patch's
    # metadata: it must identify the actual caller's PCM tail, not nearby memory.
    layouts = ((0x406474, 0x220, 0x294), (0x4150a8, 0x118, 0x14c),
               (0x417438, 0x280, 0x318), (0x4180e0, 0x220, 0x254),
               (0x418c50, 0x220, 0x280), (0x41a6d8, 0x2a0, None))
    for (caller, offset), (start, io, ffb) in zip(BT_PCM_CALLERS, layouts):
        u = machine(stock, 0x1000000)
        stack, buffer = 0x1008000, 0x1001000
        u.reg_write(UC_MIPS_REG_SP, stack)
        if ffb is not None:
            assert ffb-io == offset
            u.mem_write(stack+ffb, struct.pack('<4I', buffer, buffer+128, 1024, 2))
            # The first caller caches data/tail/element-size before this block.
            for register, value in ((UC_MIPS_REG_FP,buffer), (UC_MIPS_REG_A2,buffer+128), (UC_MIPS_REG_T0,2)):
                u.reg_write(register,value)
            want = 960
        else:
            for register, value in ((UC_MIPS_REG_S1,buffer), (UC_MIPS_REG_S7,128), (UC_MIPS_REG_S6,224)):
                u.reg_write(register,value)
            want = 224
        u.emu_start(start, 0x4145d0, count=100)
        assert u.reg_read(UC_MIPS_REG_PC)==0x4145d0, hex(caller)
        assert u.reg_read(UC_MIPS_REG_A0)==stack+io and u.reg_read(UC_MIPS_REG_A2)==buffer+128
        assert u.reg_read(UC_MIPS_REG_A3)==want
    for queued in (0, 4096, 65536):
        assert not check_drop(stock, queued), 'Regression must reproduce against stock'
        assert check_drop(patched, queued), f'DROP lost new-track audio with {queued} old bytes'
    for caller in BT_PCM_CALLERS:
        assert not check_drop(stock, 4096, caller)
        assert check_drop(patched, 4096, caller), f'DROP retained partial PCM for {caller[0]:x}'
    check_silence(stock, patched)
    print("Bluetooth silence: Drain's or PAUSE's 100 ms timeout fills silence until real PCM or the budget ends; stock sends nothing.")
    print('Bluetooth DROP: delayed encoder signal preserves new PCM, flushes all old PCM and resets pacing; stock reproduces the fault.')


if __name__ == '__main__':
    check(sys.argv[1])
