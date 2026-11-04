#!/usr/bin/env python3
"""The seam between two tracks, in samples: the built hciplayer, with its real demuxers and decoders, in real MIPS.

Generated pairs play through a real preload (set-songurl, set-gapless, as demo sends them) and every PCM frame is
kept. A stretch of each source, found by correlation, gives where that track's first sample came out, so priming and
padding are counted, not guessed. ld.so, libc and the player are stock's; ALSA is a model whose ring drains on a virtual
clock, threads are green. Files come from ffmpeg and lame, HE-AAC from test/fixtures (libfdk-aac 2.0.4: 1 s of pink noise
at AOT 5, 48 kbps, and AOT 29, 32 kbps); patch/lavf_gapless.c's numbers are what's under test. Stock's player, from the
same squashfs, is what a file with numbers of no use must come out as.
Usage: test/player_seam.py BUILD_DIR [HCIPLAYER]   (a stock HCIPLAYER shows what each case measured before the fix)"""
import array, math, os, pathlib, re, shutil, struct, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import segments
from unicorn import *
from unicorn.mips_const import *

A = UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3
PAGE, TP_SLOT, AUTH = 4096, 0x7000, 0x43084c  # AUTH: the player's licence check
MAD_TRAMPOLINES = 0x6ff00000
E = dict(ENOENT=2, EBADF=9, EAGAIN=11, EACCES=13, ENOTTY=25, ESPIPE=29, ENOSYS=89, ETIMEDOUT=145)


def alsa_symbols(path):
    """Offset of each snd_* export of the library."""
    b = pathlib.Path(path).read_bytes()
    segs = [s for _, s in segments(b)]
    dyn = next(s for s in segs if s[0] == 2)
    def off(v):
        return next(v - s[2] + s[1] for s in segs if s[0] == 1 and s[2] <= v < s[2] + s[4])
    tags = {}
    for i in range(0, dyn[4], 8):
        t, v = struct.unpack_from('<II', b, dyn[1] + i)
        tags.setdefault(t, v)
    symtab, strtab, out = off(tags[6]), off(tags[5]), {}
    for i in range(tags[0x70000011]):  # DT_MIPS_SYMTABNO
        name, value, _, info, _, shndx = struct.unpack_from('<IIIBBH', b, symtab + 16 * i)
        text = b[strtab + name:b.index(b'\0', strtab + name)].decode()
        if value and shndx and info & 15 == 2 and text.startswith('snd_'): out[value] = text
    return out


def mad_splits(path):
    """Unicorn's translator overflows on libmad's kilo-instruction straight-line synthesis: places to end a block, in
    long runs where this instruction and the next two are plain and nothing branches to the two after it."""
    branch = re.compile(r'^(b|beq|beqz|bne|bnez|bgez|bgtz|blez|bltz|bal|bgezal|bltzal|jal|jalr|jr|j|bc1t|bc1f|beql|bnel|jr\.hb|jalr\.hb|bgezl|bltzl|blezl|bgtzl|bc1tl|bc1fl)$')
    ins, targets = [], set()
    for line in subprocess.check_output(['llvm-objdump', '-d', '--no-show-raw-insn', '--triple=mipsel', str(path)],
                                         text=True, stderr=subprocess.DEVNULL).splitlines():
        m = re.match(r'\s+([0-9a-f]+):\s+(\S+)(.*)', line)
        if not m: continue
        ins.append((int(m[1], 16), m[2]))
        if branch.match(m[2]):
            t = re.findall(r'0x([0-9a-f]+)', m[3])
            if t: targets.add(int(t[0], 16))
    out, run, i = [], 0, 0
    while i + 2 < len(ins):
        a, op = ins[i]
        is_branch = bool(branch.match(op))
        run = 0 if is_branch or a in targets else run + 1
        if run >= 40 and not is_branch and not branch.match(ins[i - 1][1]) and not branch.match(ins[i + 1][1]) \
                and not branch.match(ins[i + 2][1]) and ins[i + 1][0] == a + 4 and a + 4 not in targets and a + 8 not in targets:
            out.append(a)
            run = 0
            i += 2
            continue
        i += 1
    return out


class Thread:
    def __init__(self, tid, ctx, pc, tp):
        self.tid, self.ctx, self.pc, self.tp = tid, ctx, pc, tp
        self.wait = self.wake_at = self.ret = None
        self.clear_tid = self.child_sp = 0
        self.dead = self.retry = False


class PCM:
    """A ring drained by the virtual clock."""
    def __init__(self, emu):
        self.emu, self.rate, self.ch, self.fmt, self.buffer_us, self.periods = emu, 44100, 2, 2, 500000, 16
        self.state, self.fill, self.t, self.nonblock = 2, 0, 0, 0  # 2 PREPARED

    @property
    def buffer_frames(self): return self.rate * self.buffer_us // 1000000

    def sync(self):
        now = self.emu.clock
        if self.state == 3:  # RUNNING
            done = (now - self.t) * self.rate // 10**9
            if done >= self.fill:
                if done > self.fill: self.state = 4  # XRUN
                self.fill = 0
            else:
                self.fill -= done
            self.t += done * 10**9 // self.rate
        else:
            self.t = now
        return self.fill


class Emu:
    """Just enough Linux for hciplayer."""
    def __init__(self, exe, root, aud, tmp):
        self.root, self.aud, self.tmp = pathlib.Path(root), str(aud), pathlib.Path(tmp)
        self.clock, self.pages, self.next_map, self.fds, self.next_fd = 10**12, set(), 0x60000000, {}, 3
        self.threads, self.pending, self.events, self.chunks = [], [], [], []
        self.next_tid, self.cmdq, self.replies, self.pcm, self.exited, self.alsa = 100, [], [], None, None, None
        self.ctl, self.unmapped = {}, {}  # unmapped: by size, for the next mmap; else one player's plays reach ld.so
        self.u = u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        u.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
        self.ensure(TP_SLOT, 4)
        b = pathlib.Path(exe).read_bytes()
        top = self.load_elf(b)
        self.brk = (top + PAGE - 1) & ~(PAGE - 1)
        ld = (self.root/'lib/ld-2.28.so').read_bytes()
        ld_base = 0x77e00000
        self.load_elf(ld, ld_base)
        sp, strs = 0x7fff0000, 0x7fff0100
        self.ensure(sp - 0x1000000, 0x1000000 + 0x8000)
        def put(s):
            nonlocal strs
            p = strs
            u.mem_write(p, s.encode() + b'\0')
            strs += len(s) + 1
            return p
        argv = [put(x) for x in ('hciplayer', '-ao', 'alsa')]
        envp = [put(x) for x in ('PATH=/usr/bin:/bin', 'LD_BIND_NOW=1')]
        rnd = strs = (strs + 16) & ~15
        strs += 16
        phoff, = struct.unpack_from('<I', b, 28)
        size, n = struct.unpack_from('<HH', b, 42)
        aux = [(3, 0x400000 + phoff), (4, size), (5, n), (6, PAGE), (7, ld_base), (9, struct.unpack_from('<I', b, 24)[0]),
               (11, 0), (12, 0), (13, 0), (14, 0), (16, 0), (23, 0), (25, rnd), (0, 0)]
        p = sp
        for v in [len(argv), *argv, 0, *envp, 0, *(x for pair in aux for x in pair)]:
            self.w32(p, v)
            p += 4
        u.reg_write(UC_MIPS_REG_SP, sp)
        u.reg_write(UC_MIPS_REG_CP0_STATUS, u.reg_read(UC_MIPS_REG_CP0_STATUS) | 1 << 29 | 1 << 26)
        self.entry = ld_base + struct.unpack_from('<I', ld, 24)[0]
        u.hook_add(UC_HOOK_INTR, self.syscall)
        def licensed(u, a, s, _):
            u.reg_write(UC_MIPS_REG_V0, 0)
            u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
        u.hook_add(UC_HOOK_CODE, licensed, begin=AUTH, end=AUTH)
        self.cur = Thread(1, None, self.entry, 0)
        self.threads.append(self.cur)

    def ensure(self, addr, size):
        run = None
        end = (addr + size + PAGE - 1) & ~(PAGE - 1)
        for p in range(addr & ~(PAGE - 1), end, PAGE):
            if p not in self.pages:
                self.pages.add(p)
                if run is None: run = p
            elif run is not None:
                self.u.mem_map(run, p - run)
                run = None
        if run is not None: self.u.mem_map(run, end - run)

    def w32(self, a, *v): self.u.mem_write(a, struct.pack('<' + 'I' * len(v), *[x & 0xffffffff for x in v]))
    def r32(self, a): return struct.unpack('<I', self.u.mem_read(a, 4))[0]
    def arg(self, i): return self.u.reg_read(A[i]) if i < 4 else self.r32(self.u.reg_read(UC_MIPS_REG_SP) + 4 * i)
    def cstr(self, a):
        out = bytearray()
        while True:
            chunk = bytes(self.u.mem_read(a + len(out), PAGE - ((a + len(out)) & (PAGE - 1))))
            i = chunk.find(b'\0')
            if i >= 0: return bytes(out + chunk[:i])
            out += chunk

    def no_rdhwr(self, data):
        """rdhwr rt,$29 faults here: lw rt, TP_SLOT($zero), which the scheduler keeps current."""
        d, i = bytearray(data), 0
        while (i := d.find(b'\x3b\xe8', i)) >= 0:
            if i % 4 == 0 and d[i + 3] == 0x7c and not d[i + 2] & 0xe0:
                d[i:i + 4] = struct.pack('<I', 0x8c000000 | (d[i + 2] & 31) << 16 | TP_SLOT)
            i += 2
        return bytes(d)

    def load_elf(self, b, base=0):
        top = 0
        for _, s in segments(b):
            if s[0] != 1: continue
            _, o, v, _, f, m, flags, _ = s
            self.ensure(base + v, m)
            self.u.mem_write(base + v, self.no_rdhwr(b[o:o + f]) if flags & 1 else b[o:o + f])
            top = max(top, base + v + m)
        return top

    def split_mad(self, d, base):
        d = bytearray(d)
        self.ensure(MAD_TRAMPOLINES, 0x10000)
        for k, o in enumerate(mad_splits(self.root/'usr/lib/libmad.so.0.2.1')):
            t = MAD_TRAMPOLINES + k * 12
            self.u.mem_write(t, bytes(d[o:o + 4]) + struct.pack('<I', 0x08000000 | (base + o + 8) >> 2 & 0x3ffffff) + bytes(d[o + 4:o + 8]))
            d[o:o + 8] = struct.pack('<II', 0x08000000 | t >> 2 & 0x3ffffff, 0)
        return bytes(d)

    def install_alsa(self, base):
        names = {base + v: n for v, n in alsa_symbols(self.root/'usr/lib/libasound.so.2.0.0').items()}
        def go(u, addr, size, _):
            n = names.get(addr)
            if n is None: return
            r = self.snd(n)
            if r is not False:
                u.reg_write(UC_MIPS_REG_V0, (r or 0) & 0xffffffff)
                u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
        self.u.hook_add(UC_HOOK_CODE, go, begin=min(names), end=max(names))
        self.alsa = names

    def snd(self, n):
        a = self.arg
        if n == 'snd_pcm_open':
            self.pcm = PCM(self)
            self.w32(a(0), 0x7ffd0000)
            return 0
        if n in ('snd_pcm_hw_params_sizeof', 'snd_pcm_sw_params_sizeof', 'snd_pcm_status_sizeof'): return 1024
        p = self.pcm
        if n == 'snd_pcm_close':
            self.pcm = None
            return 0
        if not p: return -77 if n != 'snd_lib_error_set_handler' else 0
        if n == 'snd_pcm_hw_params_set_rate_near': p.rate = self.r32(a(2))
        elif n == 'snd_pcm_hw_params_set_rate': p.rate = a(2)
        elif n == 'snd_pcm_hw_params_set_channels_near': p.ch = self.r32(a(2))
        elif n == 'snd_pcm_hw_params_set_channels': p.ch = a(2)
        elif n == 'snd_pcm_hw_params_set_format': p.fmt = a(2)
        elif n == 'snd_pcm_hw_params_set_buffer_time_near': p.buffer_us = self.r32(a(2))
        elif n == 'snd_pcm_hw_params_set_periods_near': p.periods = self.r32(a(2))
        elif n == 'snd_pcm_hw_params_get_buffer_size': self.w32(a(1), p.buffer_frames)
        elif n == 'snd_pcm_hw_params_get_period_size':
            self.w32(a(1), p.buffer_frames // p.periods)
            if a(2): self.w32(a(2), 0)
        elif n == 'snd_pcm_hw_params_get_rate_max': self.w32(a(1), 768000)
        elif n == 'snd_pcm_hw_params_can_pause': return 1
        elif n == 'snd_pcm_sw_params_get_boundary': self.w32(a(1), 0x40000000)
        elif n == 'snd_pcm_status':
            p.sync()
            self.w32(a(1), p.state, 0)
        elif n in ('snd_pcm_status_get_avail', 'snd_pcm_avail_update', 'snd_pcm_avail'): return p.buffer_frames - p.sync()
        elif n == 'snd_pcm_status_get_delay': return p.sync()
        elif n == 'snd_pcm_delay': self.w32(a(1), p.sync())
        elif n in ('snd_pcm_status_get_state', 'snd_pcm_state'):
            p.sync()
            return p.state
        elif n == 'snd_pcm_nonblock': p.nonblock = a(1)
        elif n == 'snd_pcm_prepare':
            p.state, p.fill, p.t = 2, 0, self.clock
        elif n == 'snd_pcm_drop':
            self.events.append(('drop', len(self.chunks)))
            p.state, p.fill = 1, 0
        elif n == 'snd_pcm_drain':
            left = p.sync()
            p.state = 1
            self.u.reg_write(UC_MIPS_REG_V0, 0)
            self.u.reg_write(UC_MIPS_REG_PC, self.u.reg_read(UC_MIPS_REG_RA))
            self.block(self.clock + left * 10**9 // p.rate, None, 'drain')
            p.fill = 0
            return False
        elif n == 'snd_pcm_pause':
            p.sync()
            p.state, p.t = 6 if a(1) else 3, self.clock
        elif n == 'snd_pcm_writei': return self.writei(a(1), a(2))
        elif n in ('snd_strerror', 'snd_pcm_format_description'): return False
        return 0

    def writei(self, buf, frames):
        p = self.pcm
        p.sync()
        if p.state == 4: return -32  # EPIPE
        n = min(frames, p.buffer_frames - p.fill)
        if n <= 0:
            if p.nonblock: return -11
            self.block(self.clock + 5 * 10**6, None, 'write', retry=True)
            return False
        size = {2: 2, 3: 2, 10: 4, 14: 4, 6: 3}.get(p.fmt, 4) * p.ch
        self.chunks.append((p.rate, p.ch, p.fmt, bytes(self.u.mem_read(buf, n * size))))
        p.fill += n
        if p.state in (2, 1): p.state, p.t = 3, self.clock
        return n

    def block(self, wake_at, ret, why, retry=False):
        t = self.cur
        t.wait, t.wake_at, t.ret, t.retry = (why,), wake_at, ret, retry
        self.u.emu_stop()
        self.switching = True

    def syscall(self, u, intno, _):
        if intno != 17:
            pc = u.reg_read(UC_MIPS_REG_PC)
            raise RuntimeError(f'exception {intno} at {pc:#x}')
        nr = u.reg_read(UC_MIPS_REG_V0)
        name = NAMES.get(nr)
        r = getattr(self, 'sys_' + name)(*(self.arg(i) for i in range(4))) if name else -E['ENOSYS']
        if r is not None:
            if -4095 <= r < 0:
                u.reg_write(UC_MIPS_REG_V0, -r), u.reg_write(UC_MIPS_REG_A3, 1)
            else:
                u.reg_write(UC_MIPS_REG_V0, r & 0xffffffff), u.reg_write(UC_MIPS_REG_A3, 0)

    def path(self, raw):
        p = raw.decode(errors='replace')
        if p.startswith('/mnt/mmc/'): return self.aud + p[8:]
        if p.startswith('/tmp/') or p.startswith('/mnt/data'): return str(self.tmp/p.lstrip('/').replace('/', '_'))
        return str(self.root) + p

    def sys_open(self, path, flags, *_):
        raw = self.cstr(path)
        if raw.startswith(b'/dev/'):
            fd = self.next_fd
            self.next_fd += 1
            self.fds[fd] = {}
            return fd
        p, h = self.path(raw), 0
        if flags & 3 == 1: h |= os.O_WRONLY
        if flags & 3 == 2: h |= os.O_RDWR
        if flags & 0x100: h |= os.O_CREAT
        if flags & 0x200: h |= os.O_TRUNC
        if flags & 0x8: h |= os.O_APPEND
        if flags & 0x10000: h |= os.O_DIRECTORY
        if h & (os.O_WRONLY | os.O_RDWR | os.O_CREAT) and not p.startswith(str(self.tmp)): return -E['EACCES']
        try: hfd = os.open(p, h, 0o644)
        except OSError as e: return -e.errno
        fd = self.next_fd
        self.next_fd += 1
        self.fds[fd] = {'h': hfd, 'path': raw}
        return fd
    def sys_openat(self, _, path, flags, __): return self.sys_open(path, flags)
    def sys_close(self, fd, *_):
        f = self.fds.pop(fd, None)
        if f and 'h' in f: os.close(f['h'])
        return 0
    def sys_read(self, fd, buf, n, _):
        f = self.fds.get(fd)
        if f is None: return -E['EBADF']
        if 'h' in f:
            d = os.read(f['h'], n)
            self.u.mem_write(buf, d)
            return len(d)
        return self.sys_recv(fd, buf, n, 0) if 'sock' in f else 0
    def sys_write(self, fd, buf, n, _):
        f = self.fds.get(fd)
        if fd in (1, 2): return n
        if f is None: return -E['EBADF']
        d = bytes(self.u.mem_read(buf, n))
        if 'h' in f: return os.write(f['h'], d)
        if 'sock' in f: self.replies.append(d)
        return n
    def sys_writev(self, fd, iov, cnt, _):
        return sum(self.sys_write(fd, self.r32(iov + 8 * i), self.r32(iov + 8 * i + 4), 0) for i in range(cnt))
    def sys_readv(self, fd, iov, cnt, _):
        tot = 0
        for i in range(cnt):
            base, ln = self.r32(iov + 8 * i), self.r32(iov + 8 * i + 4)
            got = self.sys_read(fd, base, ln, 0)
            if got <= 0: return got if not tot else tot
            tot += got
            if got < ln: break
        return tot
    def stat_out(self, st, buf):
        d = bytearray(144)
        struct.pack_into('<I', d, 0, st.st_dev & 0xffffffff)
        struct.pack_into('<Q', d, 16, st.st_ino)
        struct.pack_into('<IIII', d, 24, st.st_mode, st.st_nlink, 0, 0)
        struct.pack_into('<q', d, 56, st.st_size)
        struct.pack_into('<IIIIII', d, 64, int(st.st_atime), 0, int(st.st_mtime), 0, int(st.st_ctime), 0)
        struct.pack_into('<I', d, 88, 4096)
        struct.pack_into('<q', d, 96, st.st_blocks)
        self.u.mem_write(buf, bytes(d))
    def sys_stat64(self, path, buf, *_):
        try: self.stat_out(os.stat(self.path(self.cstr(path))), buf)
        except OSError as e: return -e.errno
        return 0
    sys_lstat64 = sys_stat64
    def sys_fstat64(self, fd, buf, *_):
        f = self.fds.get(fd)
        self.stat_out(os.fstat(f['h']) if f and 'h' in f else os.stat('/dev/null'), buf)
        return 0
    def sys_access(self, path, *_): return 0 if os.path.exists(self.path(self.cstr(path))) else -E['ENOENT']
    def sys_llseek(self, fd, hi, lo, res):
        f = self.fds.get(fd)
        if not f or 'h' not in f or b'unseekable' in f['path']: return -E['ESPIPE']
        self.u.mem_write(res, struct.pack('<q', os.lseek(f['h'], (hi << 32) | lo, self.arg(4))))
        return 0
    def sys_lseek(self, fd, off, whence, _):
        f = self.fds.get(fd)
        if not f or 'h' not in f or b'unseekable' in f['path']: return -E['ESPIPE']
        return os.lseek(f['h'], off if off < 1 << 31 else off - (1 << 32), whence)
    def sys_brk(self, a, *_):
        if a > self.brk:
            self.ensure(self.brk, a - self.brk)
            self.brk = a
        return self.brk
    def sys_mmap2(self, addr, length, prot, flags): return self.mmap(addr, length, prot, flags, self.arg(4), self.arg(5) * PAGE)
    def mmap(self, addr, length, prot, flags, fd, off):
        size = (length + PAGE - 1) & ~(PAGE - 1)
        if not flags & 0x10:  # MAP_FIXED
            if self.unmapped.get(size):
                addr = self.unmapped[size].pop()
            else:
                addr = self.next_map
                self.next_map += size + PAGE
        self.ensure(addr, size)
        if flags & 0x800:  # MIPS MAP_ANONYMOUS
            self.u.mem_write(addr, bytes(size))
            return addr
        f = self.fds.get(fd)
        if not f or 'h' not in f: return -E['EBADF']
        d = os.pread(f['h'], length, off)
        if prot & 4:
            d = self.no_rdhwr(d)
            if b'libmad' in f['path']: d = self.split_mad(d, addr - off)
        self.u.mem_write(addr, d + bytes(size - len(d)))
        if b'libasound' in f['path'] and prot & 4 and not self.alsa: self.install_alsa(addr - off)
        return addr
    def sys_munmap(self, addr, length, *_):
        self.unmapped.setdefault((length + PAGE - 1) & ~(PAGE - 1), []).append(addr)
        return 0
    def sys_mremap(self, old, olds, news, _):
        if news <= olds: return old
        self.sys_munmap(old, olds)
        na = self.next_map
        self.next_map += ((news + PAGE - 1) & ~(PAGE - 1)) + PAGE
        self.ensure(na, news)
        self.u.mem_write(na, bytes(self.u.mem_read(old, olds)))
        return na
    def sys_set_thread_area(self, tp, *_):
        self.cur.tp = tp
        self.w32(TP_SLOT, tp)
        return 0
    def sys_set_tid_address(self, p, *_):
        self.cur.clear_tid = p
        return self.cur.tid
    def sys_getpid(self, *_): return 42
    def sys_gettid(self, *_): return self.cur.tid
    def sys_getppid(self, *_): return 1
    def sys_uname(self, buf, *_):
        for i, s in enumerate([b'Linux', b'q2', b'5.10.21', b'#1', b'mips', b'']):
            self.u.mem_write(buf + 65 * i, s + b'\0')
        return 0
    def sys_readlink(self, *_): return -E['ENOENT']
    def sys_getcwd(self, buf, *_):
        self.u.mem_write(buf, b'/\0')
        return 2
    def sys_prlimit64(self, _, __, ___, old):
        if old: self.u.mem_write(old, struct.pack('<QQ', 8 << 20, 1 << 40))
        return 0
    def sys_ugetrlimit(self, _, old, *__):
        self.u.mem_write(old, struct.pack('<II', 8 << 20, 0x7fffffff))
        return 0
    def sys_getrlimit(self, res, old, *_): return self.sys_ugetrlimit(res, old)
    def sys_sysinfo(self, buf, *_):
        self.u.mem_write(buf, bytes(64))
        return 0
    def sys_sched_yield(self, *_): self.block(self.clock, (0, 0), 'yield')
    def sys_sched_getaffinity(self, _, __, buf, ___):
        self.u.mem_write(buf, struct.pack('<I', 1))
        return 4
    def sys_getpriority(self, *_): return 20
    def sys_clock_gettime(self, _, ts, *__):
        self.w32(ts, self.clock // 10**9, self.clock % 10**9)
        return 0
    def sys_clock_getres(self, _, ts, *__):
        if ts: self.w32(ts, 0, 1000)
        return 0
    def sys_gettimeofday(self, tv, *_):
        if tv: self.w32(tv, self.clock // 10**9, self.clock % 10**9 // 1000)
        return 0
    def sys_time(self, p, *_):
        if p: self.w32(p, self.clock // 10**9)
        return self.clock // 10**9
    def sys_nanosleep(self, req, *_):
        self.block(self.clock + self.r32(req) * 10**9 + self.r32(req + 4), (0, 0), 'sleep')
    def sys_clock_nanosleep(self, _, flags, req, __):
        at = self.r32(req) * 10**9 + self.r32(req + 4)
        self.block(at if flags & 1 else self.clock + at, (0, 0), 'sleep')
    def sys_futex(self, addr, op, val, timeout):
        cmd = op & 0x7f
        if cmd in (0, 9):
            if self.r32(addr) != val: return -E['EAGAIN']
            at = None
            if timeout:
                at = self.r32(timeout) * 10**9 + self.r32(timeout + 4) + (0 if cmd == 9 else self.clock)
            t = self.cur
            t.wait, t.wake_at, t.ret, t.retry = ('futex', addr), at, (E['ETIMEDOUT'], 1), False
            self.u.emu_stop()
            self.switching = True
            return None
        if cmd in (1, 10): return self.wake(addr, val)
        if cmd == 5: return self.wake(addr, val) + self.wake(self.arg(4), timeout)
        if cmd in (3, 4):
            if cmd == 4 and self.r32(addr) != self.arg(5): return -E['EAGAIN']
            n = self.wake(addr, val)
            for t in self.threads:
                if t.wait and t.wait == ('futex', addr): t.wait = ('futex', self.arg(4))
            return n
        return -E['ENOSYS']
    def wake(self, addr, n):
        c = 0
        for t in self.threads:
            if c >= n: break
            if t.wait == ('futex', addr):
                t.wait, t.ret, t.wake_at = None, (0, 0), None
                c += 1
        return c
    def sys_clone(self, flags, stack, ptid, tls):
        if not flags & 0x100: return -E['EAGAIN']  # no fork: the player's helpers aren't under test
        tid = self.next_tid
        self.next_tid += 1
        t = Thread(tid, self.u.context_save(), self.u.reg_read(UC_MIPS_REG_PC), tls if flags & 0x80000 else self.cur.tp)
        t.child_sp, t.ret = stack, (0, 0)
        ctid = self.arg(4)
        t.clear_tid = ctid if flags & 0x200000 else 0
        if flags & 0x100000: self.w32(ptid, tid)
        if flags & 0x1000000: self.w32(ctid, tid)
        self.threads.append(t)
        return tid
    def sys_exit(self, *_):
        t = self.cur
        t.dead = True
        if t.clear_tid:
            self.w32(t.clear_tid, 0)
            self.wake(t.clear_tid, 1)
        self.u.emu_stop()
        self.switching = True
    def sys_exit_group(self, code, *_):
        self.exited = code
        self.u.emu_stop()
        self.switching = True
    def sys_socket(self, *_):
        fd = self.next_fd
        self.next_fd += 1
        self.fds[fd] = {'sock': 1}
        return fd
    def sys_accept(self, *_):
        t = self.cur
        t.wait, t.wake_at, t.ret, t.retry = ('accept',), None, None, False
        self.u.emu_stop()
        self.switching = True
    def sys_recv(self, fd, buf, n, _):
        if not self.cmdq: return -E['EAGAIN']
        d = self.cmdq.pop(0)[:n]
        self.u.mem_write(buf, d)
        return len(d)
    sys_recvfrom = sys_recv
    def sys_send(self, fd, buf, n, _): return self.sys_write(fd, buf, n, 0)
    sys_sendto = sys_send
    def sys_ioctl(self, fd, *_): return 0 if fd in self.fds and not self.fds[fd].get('h') else -E['ENOTTY']
    def sys__newselect(self, nfds, r, w, e):
        tv = self.arg(4)
        if self.cmdq:
            if r: self.u.mem_write(r, struct.pack('<I', 0xffffffff) * 8)
            return 1
        at = self.clock + self.r32(tv) * 10**9 + self.r32(tv + 4) * 1000 if tv else None
        if r: self.u.mem_write(r, bytes(128))
        self.select_set = r
        self.block(at, (0, 0), 'select')
    def sys_poll(self, _, __, timeout, ___):
        self.block(self.clock + timeout * 10**6 if timeout and timeout < 1 << 31 else None, (0, 0), 'poll')
    for name in ('set_robust_list', 'rt_sigaction', 'rt_sigprocmask', 'sigaltstack', 'getuid', 'geteuid', 'getgid', 'getegid', 'prctl',
                 'setpriority', 'kill', 'tgkill', 'mprotect', 'madvise', 'bind', 'listen', 'setsockopt', 'getsockopt',
                 'shutdown', 'connect', 'fcntl64', 'fcntl', 'getdents64', 'rmdir', 'mkdir', 'unlink', 'rename', 'fsync'):
        locals()['sys_' + name] = lambda self, *_: 0
    del name

    def send(self, text):
        self.cmdq.append(text.encode())
        for t in self.threads:
            if t.wait and t.wait[0] == 'select':
                t.wait, t.wake_at, t.ret = None, None, (1, 0)
                return

    def run(self, seconds):
        """Until that much virtual time has passed, or all is blocked."""
        u, limit, idx = self.u, self.clock + int(seconds * 1e9), 0
        while self.exited is None:
            ready = [t for t in self.threads if not t.dead and (not t.wait or (t.wake_at is not None and t.wake_at <= self.clock))]
            if not ready:
                wakes = [t.wake_at for t in self.threads if not t.dead and t.wake_at is not None]
                if not wakes or min(wakes) > limit:
                    self.clock = limit
                    return
                self.clock = max(self.clock, min(wakes))
                continue
            if self.clock >= limit: return
            idx = (idx + 1) % len(ready)
            t = self.cur = ready[idx]
            if t.ctx is not None: u.context_restore(t.ctx)
            self.w32(TP_SLOT, t.tp)
            t.wait = t.wake_at = None
            if t.child_sp:
                u.reg_write(UC_MIPS_REG_SP, t.child_sp)
                t.child_sp = 0
            if t.ret is not None and t.ret[0] == 1 and getattr(self, 'select_set', 0):
                u.mem_write(self.select_set, struct.pack('<I', 0xffffffff) * 8)
            if t.ret is not None and not t.retry:
                u.reg_write(UC_MIPS_REG_V0, t.ret[0]), u.reg_write(UC_MIPS_REG_A3, t.ret[1])
            t.ret, t.retry = None, False
            self.switching = False
            try:
                u.emu_start(t.pc, 0xfffffff0, timeout=300000)
            except UcError as e:
                # A stale error status comes back on any stop: a real fault repeats at one PC.
                pc = u.reg_read(UC_MIPS_REG_PC)
                if not self.switching:
                    self.faults = getattr(self, 'faults', 0) + 1 if pc == getattr(self, 'fault_pc', None) else 0
                    self.fault_pc = pc
                    if self.faults > 3: raise RuntimeError(f'{e} at pc={pc:#x}')
            t.ctx = u.context_save()
            t.pc = u.reg_read(UC_MIPS_REG_PC)


NAMES = {4001: 'exit', 4003: 'read', 4004: 'write', 4005: 'open', 4006: 'close', 4013: 'time', 4019: 'lseek', 4020: 'getpid',
         4024: 'getuid', 4033: 'access', 4037: 'kill', 4039: 'mkdir', 4040: 'rmdir', 4045: 'brk', 4047: 'getgid', 4049: 'geteuid',
         4050: 'getegid', 4054: 'ioctl', 4055: 'fcntl', 4064: 'getppid', 4076: 'getrlimit', 4078: 'gettimeofday', 4085: 'readlink',
         4091: 'munmap', 4097: 'setpriority', 4096: 'getpriority', 4116: 'sysinfo', 4118: 'fsync', 4120: 'clone', 4122: 'uname',
         4125: 'mprotect', 4140: 'llseek', 4142: '_newselect', 4145: 'readv', 4146: 'writev', 4162: 'sched_yield', 4166: 'nanosleep',
         4167: 'mremap', 4168: 'accept', 4169: 'bind', 4170: 'connect', 4174: 'listen', 4175: 'recv', 4176: 'recvfrom', 4178: 'send',
         4180: 'sendto', 4181: 'setsockopt', 4182: 'shutdown', 4183: 'socket', 4188: 'poll', 4192: 'prctl', 4194: 'rt_sigaction',
         4195: 'rt_sigprocmask', 4203: 'getcwd', 4206: 'sigaltstack', 4210: 'mmap2', 4213: 'stat64', 4214: 'lstat64', 4215: 'fstat64',
         4218: 'madvise', 4219: 'getdents64', 4220: 'fcntl64', 4222: 'gettid', 4238: 'futex', 4239: 'sched_getaffinity',
         4246: 'exit_group', 4252: 'set_tid_address', 4263: 'clock_gettime', 4264: 'clock_getres', 4265: 'clock_nanosleep', 4266: 'tgkill',
         4283: 'set_thread_area', 4288: 'openat', 4309: 'set_robust_list', 4338: 'prlimit64'}

SECONDS = 2
TEMPLATE_AT, TEMPLATE = 30000, 4096
SAMPLES = 44100 * SECONDS
PADDING = -(SAMPLES + 1024) % 1024  # what FFmpeg's AAC encoder pads these to
LONG = 4 * 44100
EDGE = SAMPLES + 29
CHAIN_LINKS = {96000: 192000, 48480: 144480, 113760: 209760}  # a first link's frames, and the pair's true total with B
SHORT = 50, 500, 8000, 22000


def ffmpeg(*args): subprocess.check_call(['ffmpeg', '-v', 'error', '-y', *map(str, args)])


def source(path, seed, rate, samples=None):
    """Exactly SECONDS of full-level noise, or samples, loud to its last sample so a cut shows."""
    samples = samples or rate * SECONDS
    ffmpeg('-f', 'lavfi', '-i', f'anoisesrc=d={samples // rate + 1}:c=pink:r={rate}:seed={seed}:a=0.4', '-ac', 2,
           '-af', f'atrim=end_sample={samples}', '-c:a', 'pcm_s16le', path)


def left(wav):
    b = pathlib.Path(wav).read_bytes()
    a = array.array('h', b[b.index(b'data') + 8:])
    return differences([x / 32768 for x in a[::2]])


def differences(x):
    """Whitened: pink noise's neighbours are nearly the same, and the correlation's peak should be one sample wide."""
    return [b - a for a, b in zip(x, x[1:])]


def find(out, signal, lo, hi):
    """Where signal[TEMPLATE_AT:] sits in out, as the offset of signal's first sample, and the match's quality."""
    t = signal[TEMPLATE_AT:TEMPLATE_AT + TEMPLATE]
    lo, hi = max(lo + TEMPLATE_AT, 0), min(hi + TEMPLATE_AT, len(out) - TEMPLATE)
    best, at = -1, lo
    for i in range(lo, hi):
        c = math.sumprod(out[i:i + TEMPLATE], t)
        if c > best: best, at = c, i
    energy = math.sqrt(math.sumprod(t, t) * math.sumprod(*[out[at:at + TEMPLATE]] * 2)) or 1
    return at - TEMPLATE_AT, best / energy


def insert_box(path, chain, atom):
    """Atom at the end of the box chain leads to, growing its parents; ffmpeg puts moov after mdat, so no chunk offset
    moves. chain: (name, header size) of each."""
    b = bytearray(pathlib.Path(path).read_bytes())
    def child(start, end, name):
        while start < end:
            size, = struct.unpack_from('>I', b, start)
            if b[start + 4:start + 8] == name: return start, start + size
            start += size
        raise ValueError(name)
    parents, lo, hi = [], 0, len(b)
    for name, skip in chain:
        lo, hi = child(lo, hi, name)
        parents.append(lo)
        lo += skip
    b[hi:hi] = atom
    for at in parents:
        struct.pack_into('>I', b, at, struct.unpack_from('>I', b, at)[0] + len(atom))
    pathlib.Path(path).write_bytes(b)


def add_tag(path, value):
    """An iTunSMPB freeform tag in moov/udta/meta/ilst, as iTunes and qaac write it."""
    def box(name, body): return struct.pack('>I', 8 + len(body)) + name + body
    insert_box(path, ((b'moov', 8), (b'udta', 8), (b'meta', 12), (b'ilst', 8)),
               box(b'----', box(b'mean', bytes(4) + b'com.apple.iTunes') + box(b'name', bytes(4) + b'iTunSMPB')
                   + box(b'data', struct.pack('>II', 1, 0) + value.encode())))


def poke(path, name, skip, fmt, *values):
    """Overwrites a field skip bytes after the first box of that name in moov."""
    b = bytearray(pathlib.Path(path).read_bytes())
    struct.pack_into(fmt, b, b.index(name, b.index(b'moov')) + 4 + skip, *values)
    pathlib.Path(path).write_bytes(b)


def set_stts(path, entries):
    at, old = stts(path)
    assert len(old) == len(entries)
    b = bytearray(pathlib.Path(path).read_bytes())
    for i, e in enumerate(entries): struct.pack_into('>II', b, at + 8 * i, *e)
    pathlib.Path(path).write_bytes(b)


def timescale(path, ticks, edit):
    """A movie timescale of ticks a second, and the edit's duration in them, as other muxers than FFmpeg's write."""
    poke(path, b'mvhd', 12, '>I', ticks)
    poke(path, b'elst', 8, '>I', edit)


def stts(path):
    """stts's entries as (their offset, [(count, delta), ...]); moov's only."""
    b = pathlib.Path(path).read_bytes()
    at = b.index(b'stts', b.index(b'moov')) + 8
    n, = struct.unpack_from('>I', b, at)
    return at + 4, [struct.unpack_from('>II', b, at + 4 + 8 * i) for i in range(n)]


def whole_frames(path):
    """The last stts entry as a whole frame, as encoders that leave the end to a tag or the edit list do. Returns the
    frames the file now declares."""
    at, entries = stts(path)
    b = bytearray(pathlib.Path(path).read_bytes())
    if entries[-1][1] != 1024:
        struct.pack_into('>II', b, at + 8 * (len(entries) - 1), 1, 1024)
    pathlib.Path(path).write_bytes(b)
    return sum(c for c, _ in entries) * 1024


def generate(d):
    for s, seed in ('a', 1), ('b', 2):
        source(d/f'{s}.wav', seed, 44100)
        source(d/f'{s}48.wav', seed, 48000)
        wav = d/f'{s}.wav'
        ffmpeg('-i', wav, '-c:a', 'flac', d/f'{s}.flac')
        ffmpeg('-i', wav, '-c:a', 'alac', d/f'{s}.alac.m4a')
        subprocess.check_call(['lame', '--quiet', '-q', '2', '-V', '2', wav, d/f'{s}.mp3'])
        aac = '-c:a', 'aac', '-b:a', '192k'
        ffmpeg('-i', wav, *aac, d/f'{s}.m4a')  # FFmpeg's: edit list, last stts entry short
        ffmpeg('-i', wav, *aac, '-f', 'adts', d/f'{s}.aac')
        ffmpeg('-i', wav, '-c:a', 'libvorbis', '-q:a', 5, d/f'{s}.ogg')
        ffmpeg('-i', d/f'{s}48.wav', '-c:a', 'libopus', '-b:a', '128k', d/f'{s}.opus')
        # An edit list and whole frames: the end is the edit's duration.
        shutil.copy(d/f'{s}.m4a', d/f'{s}_elst.m4a')
        whole_frames(d/f'{s}_elst.m4a')
        # No edit list, whole frames: iTunes' shape, or nothing to go on but a tag.
        ffmpeg('-i', wav, *aac, '-use_editlist', 0, '-metadata', 'title=x', d/f'{s}_plain.m4a')
        total = whole_frames(d/f'{s}_plain.m4a')
        padding = total - 1024 - SAMPLES
        def tagged(name, priming, remainder, length):
            shutil.copy(d/f'{s}_plain.m4a', d/f'{s}_{name}.m4a')
            add_tag(d/f'{s}_{name}.m4a', f' 00000000 {priming:08X} {remainder:08X} {length:016X} 00000000 00000000')
        tagged('tag', 1024, padding, SAMPLES)
        tagged('tag_remainder', 1024, padding, 0)  # the total left out: the remainder says it
        # The tag's priming over more than a frame, past real audio: 1088 are lost, and the end still counts from the tag.
        tagged('tag_2112', 2112, padding, SAMPLES - 1088)
        tagged('junk_total', 1024, padding, 0x7fffffff)  # longer than the file
        tagged('junk_edit', 1024, padding, SAMPLES // 2)  # a tag left behind by an edit: half the file would go
        tagged('junk_priming', 0xffff, padding, SAMPLES)  # lavf refuses it
    # Wrong ends: a track long enough that an end 2 s early is mid-track.
    source(d/'c.wav', 3, 44100, LONG)
    source(d/'d.wav', 4, 44100, LONG)
    aac = '-c:a', 'aac', '-b:a', '192k'
    vorbis = '-c:a', 'libvorbis', '-q:a', 5
    for s in 'c', 'd':
        ffmpeg('-i', d/f'{s}.wav', *aac, d/f'{s}.m4a')
    ffmpeg('-i', d/'c.wav', *aac, '-use_editlist', 0, d/'plain.m4a')
    whole_frames(d/'plain.m4a')
    (count, _), _ = stts(d/'plain.m4a')[1]
    for name, entries in ('stts_136', [(count - 100, 1024), (1, 136)]), ('stts_0', [(count - 100, 1024), (1, 0)]), \
            ('last_delta_0', [(count, 1024), (1, 0)]):
        shutil.copy(d/'plain.m4a', d/f'{name}.m4a')
        set_stts(d/f'{name}.m4a', entries)
    def wrong(name, priming, remainder, length):
        shutil.copy(d/'plain.m4a', d/f'{name}.m4a')
        add_tag(d/f'{name}.m4a', f' 00000000 {priming:08X} {remainder:08X} {length:016X} 00000000 00000000')
    padding = (count + 1) * 1024 - 1024 - LONG
    wrong('stale', 1024, padding, LONG - 5000)
    wrong('stale_near', 1024, padding, LONG - 400)
    wrong('stale_300', 1024, padding, LONG - 300)
    wrong('stale_frame', 1024, padding, LONG - (1024 - padding))  # a whole frame of padding: none is
    wrong('stale_100', 1024, padding, LONG - 100)  # inside the last packet
    wrong('priming0', 0, 0, LONG)
    wrong('priming0_small', 0, 8, 0)  # a little padding, as a tag might say on a file with real priming
    ffmpeg('-i', d/'c.wav', *aac, '-movflags', 'frag_keyframe+empty_moov', '-frag_duration', 500000, d/'fragmented_unseekable.m4a')
    shutil.copy(d/'fragmented_unseekable.m4a', d/'frag.m4a')
    # A last sample holding two AUs: the decoder puts the second back, half a packet read with none queued. Dropping
    # samples moves where in a block the first of them falls.
    for drop in range(4):
        f = d/f'two_aus_{drop}.m4a'
        shutil.copy(d/'plain.m4a', f)
        set_stts(f, [(count - 1 - drop, 1024), (1, 232)])
        b = bytearray(f.read_bytes())
        at = b.index(b'stsz', b.index(b'moov')) + 4
        n, = struct.unpack_from('>I', b, at + 8)
        sizes = struct.unpack_from(f'>{n}I', b, at + 12)
        struct.pack_into('>I', b, at + 8, n - 1 - drop)
        struct.pack_into('>I', b, at + 12 + 4 * (n - 2 - drop), sizes[-2 - drop] + sizes[-1 - drop])
        f.write_bytes(b)
    # Real HE-AAC and HE+PS, at the core's timescale and the output's, and the output's with stts in the core's and a tag.
    for he in 'he', 'ps':
        ffmpeg('-i', pathlib.Path(__file__).parent/'fixtures'/f'{he}.aac', '-c', 'copy', d/f'{he}.m4a')
        for shape, ticks, delta, tag in ('core', 22050, 1024, False), ('output', 44100, 2048, False), ('lying', 44100, 1024, True):
            f = d/f'{he}_{shape}.m4a'
            shutil.copy(d/f'{he}.m4a', f)
            poke(f, b'mdhd', 12, '>I', ticks)
            set_stts(f, [(c, delta) for c, _ in stts(f)[1]])
            if tag: add_tag(f, ' 00000000 00000400 00000020 0000000000000000 00000000 00000000')
    # Timestamps in anything but the output rate's units, on LC: an mdhd changed.
    shutil.copy(d/'plain.m4a', d/'timebase.m4a')
    poke(d/'timebase.m4a', b'mdhd', 12, '>I', 22050)
    shutil.copy(d/'a.m4a', d/'timebase_edit.m4a')
    poke(d/'timebase_edit.m4a', b'mdhd', 12, '>I', 22050)
    shutil.copy(d/'a.m4a', d/'timebase_high.m4a')  # priming below 0 by less than its length, in the wrong units
    poke(d/'timebase_high.m4a', b'mdhd', 12, '>I', 88200)
    # An edit list that's not ours to read: two entries (a short one first), or a media time past lavf's priming bound.
    shutil.copy(d/'a_elst.m4a', d/'two_edits.m4a')
    b = pathlib.Path(d/'two_edits.m4a').read_bytes()
    at = b.index(b'elst', b.index(b'moov')) + 8
    duration, media, rate = struct.unpack_from('>IiI', b, at + 4)
    poke(d/'two_edits.m4a', b'elst', 4, '>I', 2)
    poke(d/'two_edits.m4a', b'elst', 8, '>I', duration - 100)
    insert_box(d/'two_edits.m4a', ((b'moov', 8), (b'trak', 8), (b'edts', 8), (b'elst', 16)), struct.pack('>IiI', 100, media, rate))
    # An edit too far from the file to be its end: FFmpeg's stts still says it.
    for name, off in ('edit_short', -5000), ('edit_long', 5000):
        shutil.copy(d/'a.m4a', d/f'{name}.m4a')
        poke(d/f'{name}.m4a', b'elst', 8, '>I', duration + off)
    shutil.copy(d/'a_elst.m4a', d/'media_time_big.m4a')
    poke(d/'media_time_big.m4a', b'elst', 12, '>i', 20000)
    # A long priming from an edit: whole blocks before 0.
    shutil.copy(d/'a.m4a', d/'media_time_8192.m4a')
    poke(d/'media_time_8192.m4a', b'elst', 12, '>i', 8192)
    shutil.copy(d/'a.m4a', d/'media_time_16000.m4a')
    poke(d/'media_time_16000.m4a', b'elst', 12, '>i', 16000)
    # A tag and an edit list: the decoder skips the priming and the timestamps start below 0 as well.
    shutil.copy(d/'a.m4a', d/'tag_and_edit.m4a')
    add_tag(d/'tag_and_edit.m4a', f' 00000000 00000400 00000000 {SAMPLES:016X} 00000000 00000000')
    # Priming over a frame, from both: the decoder's skip leaves a part of a third packet, whose frames are still below 0.
    shutil.copy(d/'a.m4a', d/'tag_and_edit_2112.m4a')
    poke(d/'tag_and_edit_2112.m4a', b'elst', 12, '>i', 2112)
    add_tag(d/'tag_and_edit_2112.m4a', f' 00000000 00000840 00000000 {SAMPLES - 1088:016X} 00000000 00000000')
    # A tag's total far short on FFmpeg's file: the edit and stts still say the end.
    shutil.copy(d/'a.m4a', d/'tag_far_short.m4a')
    add_tag(d/'tag_far_short.m4a', f' 00000000 00000400 00000000 {SAMPLES - 5000:016X} 00000000 00000000')
    # Whole frames and a timescale that can't say where in a frame the music ends. EDGE isn't a whole number of ticks.
    source(d/'e.wav', 5, 44100, EDGE)
    ffmpeg('-i', d/'e.wav', *aac, d/'edge.m4a')
    whole_frames(d/'edge.m4a')
    for ticks, name in (1000, 'edge_1000'), (600, 'edge_600'):
        shutil.copy(d/'edge.m4a', d/f'{name}.m4a')
        timescale(d/f'{name}.m4a', ticks, EDGE * ticks // 44100)
    # Links: the last page's granule, the whole file's end to ogg_get_length, is the last link's, far too early.
    def link(name, wav, *args, encoder=vorbis):
        ffmpeg('-i', d/wav, *encoder, *args, d/f'{name}.tmp.ogg')
        return (d/f'{name}.tmp.ogg').read_bytes()
    (d/'chain.ogg').write_bytes(link('l1', 'c.wav', '-serial_offset', 7) + link('l2', 'b.wav', '-serial_offset', 7))
    (d/'chain_serials.ogg').write_bytes(link('l1', 'c.wav', '-serial_offset', 7) + link('l2', 'b.wav', '-serial_offset', 9))
    (d/'chain_rates.ogg').write_bytes(link('l1', 'c.wav', '-serial_offset', 7) + link('l2', 'b48.wav', '-serial_offset', 9))
    opus = '-c:a', 'libopus', '-b:a', '128k'
    (d/'chain.opus').write_bytes(link('l1', 'a48.wav', '-serial_offset', 7, encoder=opus) + link('l2', 'b48.wav', '-serial_offset', 9, encoder=opus))
    # Where one link's end falls in a block changes what a block holds of the next's start.
    for frames in CHAIN_LINKS:
        source(d/f'o{frames}.wav', 6, 48000, frames)
        first = link('l1', f'o{frames}.wav', '-serial_offset', 7, encoder=opus)
        (d/f'chain_{frames}.opus').write_bytes(first + link('l2', 'b48.wav', '-serial_offset', 7, encoder=opus))
    (d/'chain_three.opus').write_bytes(link('l1', 'o48480.wav', '-serial_offset', 7, encoder=opus)
                                       + link('l2', 'a48.wav', '-serial_offset', 7, encoder=opus) + link('l3', 'b48.wav', '-serial_offset', 7, encoder=opus))
    # Tracks under a second, all their audio on one page, and one cut without re-encoding.
    for frames in SHORT:
        source(d/f's{frames}.wav', 7, 44100, frames)
        ffmpeg('-i', d/f's{frames}.wav', *vorbis, d/f's{frames}.ogg')
    ffmpeg('-i', d/'s8000.wav', *aac, d/'s8000.m4a')
    source(d/'s48.wav', 7, 48000, 8000)
    ffmpeg('-i', d/'s48.wav', *opus, d/'s8000.opus')
    ffmpeg('-i', d/'a.ogg', '-t', 1, '-c', 'copy', d/'cut.ogg')
    # Many pages, and a stream that can't seek, so lavf never reads the last page.
    ffmpeg('-i', d/'a.wav', *vorbis, '-page_duration', 100000, d/'pages.ogg')
    shutil.copy(d/'a.ogg', d/'a_unseekable.ogg')
    # The last granule too low, by more than the last packet's padding.
    ffmpeg('-i', d/'c.wav', *vorbis, d/'granule_src.ogg')
    plain = (d/'granule_src.ogg').read_bytes()
    for name, low in ('granule', 100000), ('granule_near', 20000), ('granule_7000', 7000), ('granule_100', 100):
        b = bytearray(plain)
        at = b.rindex(b'OggS') + 6
        struct.pack_into('<q', b, at, struct.unpack_from('<q', b, at)[0] - low)
        (d/f'{name}.ogg').write_bytes(b)
    # A pre-skip past the bound.
    b = bytearray((d/'a.opus').read_bytes())
    struct.pack_into('<H', b, b.index(b'OpusHead') + 10, 30000)
    (d/'pre_skip.opus').write_bytes(b)
    ffmpeg('-i', d/'a.wav', '-c:a', 'pcm_s16be', d/'a.aiff')
    ffmpeg('-i', d/'b.wav', '-c:a', 'pcm_s16be', d/'b.aiff')


class Player:
    def __init__(self, exe, root, aud, tmp):
        self.e = Emu(exe, root, aud, tmp)
        self.e.run(0.3)
        self.seen = 0

    def play(self, name, next_name=None, seeks=(), during=None):
        """Plays name (and a preloaded next) to the end, returning the left channel's differences, and each seek's index
        in them. seeks: (at seconds of played time, to seconds). during: a command for 0.3 s in."""
        e = self.e
        first = len(e.chunks)
        e.send('{set-songurl\\/mnt/mmc/%s|ss=0|0|0|1|0|90}' % name)
        e.run(0.05)
        if next_name:
            e.send('{set-gapless\\/mnt/mmc/%s|ss=0|0|0|1|90}' % next_name)
        if during:
            e.run(0.3)
            e.send(during)
        for at, to in seeks:
            e.run(at)
            e.send('{mcl-seek\\%d}' % to)
            e.run(0.02)
        idle = 0
        for _ in range(400):
            n = len(e.chunks)
            e.run(0.25)
            idle = idle + 1 if len(e.chunks) == n else 0
            if idle >= 8 and len(e.chunks) > first: break
        marks = [c for k, c in e.events if k == 'drop' and c >= first]
        e.events.clear()
        out, at = [], []
        for i, (rate, ch, fmt, data) in enumerate(e.chunks[first:]):
            if first + i in marks: at.append(len(out))
            if fmt in (2, 3):
                a, k = array.array('h', data[:len(data) // 2 * 2]), 1 / 32768
                if fmt == 3: a.byteswap()
            elif fmt == 14: a, k = array.array('f', data[:len(data) // 4 * 4]), 1
            else: a, k = array.array('i', data[:len(data) // 4 * 4]), 1 / 2**31
            out += [x * k for x in a[::ch]]
        del e.chunks[first:]
        return differences(out), at


failed = []
def check(name, got, want):
    ok = got == want
    print(f'  {"ok  " if ok else "FAIL"} {name}: {got}' + ('' if ok else f', wanted {want}'), flush=True)
    if not ok: failed.append(name)


def pair(player, sources, name, a, b, kept, seam, after=0):
    """Plays a into b. kept: A's first sample's place in the output (priming left in). seam: what lies between A's
    last true sample and B's first. after: what B leaves past its last."""
    out, _ = player.play(a, b)
    A, B = sources
    oa, qa = find(out, A, kept - 4000, kept + 8000)
    ob, qb = find(out, B, oa + len(A) - 4000, oa + len(A) + 8000)
    print(f'  {name}: A at {oa} (q {qa:.2f}), B at {ob} (q {qb:.2f}), seam {ob - oa - len(A) - 1}, {len(out) + 1} frames', flush=True)
    check(f'{name} found', qa > 0.5 and qb > 0.5, True)
    # The differences lose each track's first sample: A's and B's offsets are one less than the sample's, both the same.
    check(f'{name} kept', oa, kept)
    check(f'{name} seam', ob - oa - len(A) - 1, seam)
    check(f'{name} after', len(out) - ob - len(B), after)


def seek(player, sources, name, file, next_name, to, want):
    """Seeks from 0.6 s in; want: the source frame the first frames after the seek must be."""
    out, at = player.play(file, next_name, [(0.6, to)])
    A, B = sources
    assert at, f'{name}: no seek happened'
    start, frames = at[-1], len(A) + 1
    # A template from further on in the source, so a decoder's first frame after the flush doesn't matter.
    span = 22050
    source_at = to * frames // SECONDS + span
    t = A[source_at:source_at + TEMPLATE]
    best, where = -1, 0
    for i in range(start, min(start + span + 6000, len(out) - TEMPLATE)):
        c = math.sumprod(out[i:i + TEMPLATE], t)
        if c > best: best, where = c, i
    landed = source_at - (where - start)
    end = start + frames - landed
    ob, qb = find(out, B, end - 4000, end + 4000)
    q = best / (math.sqrt(math.sumprod(t, t) * math.sumprod(*[out[where:where + TEMPLATE]] * 2)) or 1)
    print(f'  {name}: first frame after the seek is source frame {landed} (q {q:.2f}), B at {ob} (q {qb:.2f})', flush=True)
    check(f'{name} found', q > 0.5 and qb > 0.5, True)
    check(f'{name} landed', landed, want)
    check(f'{name} seam', ob - end, 0)


def whole(player, file, want):
    """Frames the file comes to on its own."""
    out, _ = player.play(file)
    check(file, len(out) + 1, want)


def like_stock(player, stock, file, less=0):
    """Comes to what stock's does, less a part the file's numbers do say."""
    out, _ = stock.play(file)
    was = len(out) + 1
    out, _ = player.play(file)
    check(f'{file} (stock {was}, less {less})', len(out) + 1, was - less)


def crossfade(exe, root, aud, tmp, sources):
    """Fading 1 s, the next track starts exactly 1 s before the true end: not 1 s before the padding's."""
    player = Player(exe, root, aud, tmp)
    (tmp/'tmp_q2xfade').write_bytes(struct.pack('<i', 1000))
    out, _ = player.play('c.m4a', 'd.m4a', during='{mcl-seteq\\0|0|0|0|0|0|0|0|0|0|1}')
    (tmp/'tmp_q2xfade').unlink()
    oa, qa = find(out, sources[0], -4000, 8000)
    ob, qb = find(out, sources[1], LONG - 44100 - 4000, LONG - 44100 + 4000)
    print(f'  Crossfade 1 s: C at {oa} (q {qa:.2f}), D at {ob} (q {qb:.2f})', flush=True)
    check('Crossfade found', qa > 0.5 and qb > 0.5, True)
    check('Crossfade: next starts 1 s before the true end', ob - oa, LONG - 44100)


def bluetooth(exe, root, aud, tmp):
    """Through the 48 kHz resampler: M4A's seam against FLAC's, the same music with no seam at all."""
    player = Player(exe, root, aud, tmp)
    for command in '{mcl-setoutputway\\1}', '{mcl-setvol\\100}', '{mcl-softvolflag\\1}':  # without a volume, silence
        player.e.send(command)
        player.e.run(0.05)
    frames = SAMPLES * 48000 // 44100
    ref, _ = player.play('a.flac', 'b.flac')
    out, _ = player.play('a.m4a', 'b.m4a')
    oa, qa = find(out, ref, -4000, 4000)
    ob, qb = find(out, ref[frames:], frames - 4000, frames + 4000)
    print(f'  Bluetooth: A at {oa} (q {qa:.2f}), B at {ob} (q {qb:.2f}), {len(out) + 1} frames, FLAC {len(ref) + 1}', flush=True)
    check('Bluetooth found', qa > 0.5 and qb > 0.5, True)
    check('Bluetooth seam', ob - oa - frames, 0)


def main():
    build = pathlib.Path(sys.argv[1])
    exe = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else build/'peq/hciplayer'
    work = pathlib.Path(tempfile.mkdtemp(prefix='seam-', dir=build))
    try:
        subprocess.check_call(['unsquashfs', '-q', '-n', '-d', work/'root', build/'stock.squashfs', 'lib', 'usr/lib', 'usr/bin/hciplayer'], stdout=subprocess.DEVNULL)
        aud, tmp = work/'aud', work/'tmp'
        aud.mkdir(), tmp.mkdir()
        generate(aud)
        sources = left(aud/'a.wav'), left(aud/'b.wav')
        sources48 = left(aud/'a48.wav'), left(aud/'b48.wav')
        player = Player(exe, work/'root', aud, tmp)
        stock = Player(work/'root/usr/bin/hciplayer', work/'root', aud, tmp)

        print('exact already')
        pair(player, sources, 'AIFF', 'a.aiff', 'b.aiff', 0, 0)
        pair(player, sources, 'FLAC', 'a.flac', 'b.flac', 0, 0)
        pair(player, sources, 'ALAC', 'a.alac.m4a', 'b.alac.m4a', 0, 0)
        pair(player, sources, 'MP3', 'a.mp3', 'b.mp3', 0, 0)
        print('M4A')
        pair(player, sources, 'edit list', 'a.m4a', 'b.m4a', 0, 0)
        pair(player, sources, 'edit list, whole frames', 'a_elst.m4a', 'b_elst.m4a', 0, 0)
        pair(player, sources, 'iTunSMPB', 'a_tag.m4a', 'b_tag.m4a', 0, 0)
        pair(player, sources, 'iTunSMPB, remainder only', 'a_tag_remainder.m4a', 'b_tag_remainder.m4a', 0, 0)
        pair(player, sources, 'iTunSMPB, priming over a frame', 'a_tag_2112.m4a', 'b_tag.m4a', -1088, 0)
        print('Ogg')
        pair(player, sources48, 'Opus', 'a.opus', 'b.opus', 0, 0)
        pair(player, sources, 'Vorbis', 'a.ogg', 'b.ogg', 0, 0)
        print('Preloads stay separate')
        pair(player, sources, 'M4A into FLAC', 'a.m4a', 'b.flac', 0, 0)
        pair(player, sources, 'FLAC into M4A', 'a.flac', 'b.m4a', 0, 0)
        pair(player, sources, 'trimmed into junk', 'a_tag.m4a', 'b_junk_total.m4a', 0, 0, PADDING)
        pair(player, sources, 'junk into trimmed', 'a_junk_total.m4a', 'b_tag.m4a', 0, PADDING)
        print('Numbers that are nonsense')
        pair(player, sources, 'total past the end', 'a_junk_total.m4a', 'b_junk_total.m4a', 0, PADDING, PADDING)
        pair(player, sources, 'tag from before an edit', 'a_junk_edit.m4a', 'b_junk_edit.m4a', 0, PADDING, PADDING)
        pair(player, sources, 'priming lavf refuses', 'a_junk_priming.m4a', 'b_junk_priming.m4a', 1024, 1024 + PADDING, PADDING)
        print('An end that is wrong by more than the last packet')
        for file in 'stts_136.m4a', 'stts_0.m4a', 'last_delta_0.m4a', 'priming0.m4a', 'priming0_small.m4a', 'fragmented_unseekable.m4a', \
                'timebase.m4a', 'timebase_edit.m4a', 'timebase_high.m4a', 'media_time_big.m4a', 'granule.ogg', 'granule_near.ogg', 'granule_7000.ogg', 'granule_100.ogg', \
                'chain_rates.ogg', 'pre_skip.opus', 'stale.m4a', 'stale_near.m4a', 'stale_300.m4a', 'stale_frame.m4a':
            like_stock(player, stock, file)
        # ponytail: wrong by less than what's left of the last packet, it can't be told from padding, and that much goes.
        like_stock(player, stock, 'stale_100.m4a', (SAMPLES * 2 + 1024 + 1023) // 1024 * 1024 - 1024 - LONG + 100)
        like_stock(player, stock, 'two_edits.m4a', 1024)  # the priming, as the first edit's media time says
        for drop in range(4):
            like_stock(player, stock, f'two_aus_{drop}.m4a')  # half read, then past it: nothing goes
        like_stock(player, stock, 'chain.ogg', 120)  # the last link's padding
        like_stock(player, stock, 'chain_serials.ogg', 120)
        whole(player, 'frag.m4a', LONG + 1024)  # its priming kept, its padding cut, no music lost
        print('Chained Opus: each link comes to its true length')
        for frames, total in CHAIN_LINKS.items():
            whole(player, f'chain_{frames}.opus', total)
        whole(player, 'chain_three.opus', 240480)
        whole(player, 'chain.opus', 192000)
        print('Short tracks, cut tracks, many pages, no last page')
        for frames in SHORT:
            like_stock(player, stock, f's{frames}.ogg')
        like_stock(player, stock, 'cut.ogg')
        like_stock(player, stock, 'a_unseekable.ogg')
        whole(player, 's8000.m4a', 8000)
        whole(player, 's8000.opus', 8000)
        pair(player, sources, 'Vorbis of many pages', 'pages.ogg', 'b.ogg', 0, 0)
        print('HE-AAC keeps what stock plays')
        for he in 'he', 'ps':
            for shape in 'core', 'output', 'lying':
                like_stock(player, stock, f'{he}_{shape}.m4a')
        print('An edit in another timescale: up to a tick kept, none lost')
        pair(player, sources, 'priming past a block', 'media_time_8192.m4a', 'b.m4a', -7168, 0)
        pair(player, sources, 'priming past two blocks', 'media_time_16000.m4a', 'b.m4a', -14976, 0)
        pair(player, sources, 'tag and edit list', 'tag_and_edit.m4a', 'b.m4a', 0, 0)
        pair(player, sources, 'tag and edit list over a frame', 'tag_and_edit_2112.m4a', 'b.m4a', -1088, 0)
        pair(player, sources, 'tag far short', 'tag_far_short.m4a', 'b.m4a', 0, 0)
        pair(player, sources, 'edit far short', 'edit_short.m4a', 'b.m4a', 0, 0)
        pair(player, sources, 'edit past the end', 'edit_long.m4a', 'b.m4a', 0, 0)
        whole(player, 'edge_1000.m4a', (2000 + 1) * 441 // 10 + 1)
        whole(player, 'edge_600.m4a', (1200 + 1) * 735 // 10 + 1)
        print('Nothing to go on')
        pair(player, sources, 'ADTS', 'a.aac', 'b.aac', 1024, 1024 + PADDING, PADDING)
        print('Seeks')
        seek(player, sources, 'edit list, to 0', 'a.m4a', 'b.m4a', 0, 0)
        seek(player, sources, 'iTunSMPB, to 0', 'a_tag.m4a', 'b_tag.m4a', 0, 0)
        seek(player, sources48, 'Opus, to 0', 'a.opus', 'b.opus', 0, 0)
        seek(player, sources, 'Vorbis, to 0', 'a.ogg', 'b.ogg', 0, 0)
        # Stock's landing, where a packet starts: a frame later would be priming cut again.
        seek(player, sources, 'edit list, to 1', 'a.m4a', 'b.m4a', 1, 44032)
        seek(player, sources, 'iTunSMPB, to 1', 'a_tag.m4a', 'b_tag.m4a', 1, 44032)
        seek(player, sources48, 'Opus, to 1', 'a.opus', 'b.opus', 1, 47688)
        seek(player, sources, 'Vorbis, to 1', 'a.ogg', 'b.ogg', 1, 45760)
        del player, stock
        print('Crossfade and Bluetooth')
        crossfade(exe, work/'root', aud, tmp, (left(aud/'c.wav'), left(aud/'d.wav')))
        bluetooth(exe, work/'root', aud, tmp)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if failed:
        print(f'player_seam: {len(failed)} FAILED: {failed}')
        sys.exit(1)
    print('player_seam: all as expected')


if __name__ == '__main__':
    main()
