"""Mini Linux user mode for the Q2's hciplayer on Unicorn: real ld.so, libc and libm, the real player binary, its real
demuxers and decoders. libasound is a model (hooked exports), threads are green (switched at blocking syscalls and a
real-time quantum), the clock is virtual, and a test drives the player through its command socket as demo does.
libmad's long straight-line synth overflows Unicorn's translator, so its blocks are split where it's mapped."""
import errno, os, pathlib, re, struct, subprocess, sys
from unicorn import *
from unicorn.mips_const import *

A = (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)
PAGE = 4096
TP_SLOT = 0x7000
STUB_RET = 0x7ffe0000


def elf_segments(b):
    phoff, = struct.unpack_from('<I', b, 28)
    es, n = struct.unpack_from('<HH', b, 42)
    return [struct.unpack_from('<8I', b, phoff + i * es) for i in range(n)]


def dynsyms(path):
    """name -> value, size for defined FUNC dynsyms of a library file (st_value relative to its link base)."""
    b = pathlib.Path(path).read_bytes()
    dyn = None
    segs = elf_segments(b)
    for s in segs:
        if s[0] == 2: dyn = s
    def off(v):
        for s in segs:
            if s[0] == 1 and s[2] <= v < s[2] + s[4]: return v - s[2] + s[1]
        raise ValueError
    tags = {}
    for i in range(0, dyn[4], 8):
        t, v = struct.unpack_from('<II', b, dyn[1] + i)
        tags.setdefault(t, v)
    symtab, strtab = off(tags[6]), off(tags[5])
    n = tags[0x70000011]  # MIPS_SYMTABNO
    out = {}
    for i in range(n):
        name, value, size, info, other, shndx = struct.unpack_from('<IIIBBH', b, symtab + 16 * i)
        if value and shndx and (info & 15) == 2:
            nm = b[strtab + name:b.index(b'\0', strtab + name)].decode()
            if nm.startswith('snd_') or value not in out: out[value] = nm
    return out


class Thread:
    def __init__(self, tid, ctx, pc, tp):
        self.tid, self.ctx, self.pc, self.tp = tid, ctx, pc, tp
        self.wait = None       # ('futex', addr) | ('sleep',) | ('accept', fd) | ('recv', fd) | ('poll',) ...
        self.wake_at = None    # virtual ns
        self.ret = None        # (v0, a3) to set on resume
        self.clear_tid = 0
        self.dead = False
        self.resume = None     # callable run at wake (returns ret)


class PCM:
    """The ALSA model: a ring drained by the virtual clock; frames captured by the script through on_write."""
    def __init__(self, os_):
        self.os = os_
        self.rate, self.ch, self.fmt = 44100, 2, 2
        self.buffer_us = 500000
        self.periods = 16
        self.state = 2  # PREPARED
        self.fill = 0   # frames in the ring at clock self.t
        self.t = 0
        self.xruns = 0
        self.nonblock = 0
        self.total = 0
        self.start_threshold = 1

    @property
    def buffer_frames(self): return self.rate * self.buffer_us // 1000000

    def sync(self):
        now = self.os.clock
        if self.state == 3:  # RUNNING
            done = (now - self.t) * self.rate // 10**9
            if done >= self.fill and self.fill >= 0:
                if done > self.fill:
                    # underrun: the gap is what's heard
                    gap = (done - self.fill) * 10**9 // self.rate
                    self.os.event('xrun', gap_ms=gap / 1e6, total=self.total)
                    self.xruns += 1
                    self.state = 4  # XRUN
                self.fill = 0
            else:
                self.fill -= done
            self.t += done * 10**9 // self.rate
        else:
            self.t = now
        return self.fill


MAD_TRAMPOLINES = 0x6ff00000


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


class Emu:
    def __init__(self, exe, root, work, env=(), fsmap=None, log=None, verbose=False, argv=('hciplayer', '-ao', 'alsa')):
        self.exe, self.root = pathlib.Path(exe), pathlib.Path(root)
        self.fsmap = fsmap or {}
        self.verbose = verbose
        self.clock = 10**12
        self.u = u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        u.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
        self.pages = set()
        self.next_map = 0x60000000
        self.unmapped = {}  # by size, for the next mmap; else a player's many plays reach ld.so
        self.fds = {}
        self.next_fd = 3
        self.threads = []
        self.cur = None
        self.next_tid = 100
        self.timers = []
        self.pending = []   # (virtual ns, callable) script events
        self.events = []
        self.listen_fd = None
        self.accept_q = []
        self.out = open(log or os.devnull, 'w', errors='replace', buffering=1)
        self.pcm = None
        self.on_write = None
        self.stop_flag = False
        self.alsa_active = False
        self.tmp = pathlib.Path(work) / 'tmp'
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.ctl = {'data': b'', 'reply': bytearray(), 'cb': None}
        self.cmdq = []
        self.replies = []
        self.exited = None
        self.ioctls = []
        self.hooks = {}
        self.load(argv, env)

    # ---- memory
    def ensure(self, addr, size):
        s = addr & ~(PAGE - 1)
        e = (addr + size + PAGE - 1) & ~(PAGE - 1)
        run = None
        for p in range(s, e, PAGE):
            if p not in self.pages:
                self.pages.add(p)
                if run is None: run = p
            elif run is not None:
                self.u.mem_map(run, p - run); run = None
        if run is not None:
            self.u.mem_map(run, e - run)

    def w32(self, a, *v): self.u.mem_write(a, struct.pack('<' + 'I' * len(v), *[x & 0xffffffff for x in v]))
    def r32(self, a): return struct.unpack('<I', self.u.mem_read(a, 4))[0]
    def rf(self, a): return struct.unpack('<f', self.u.mem_read(a, 4))[0]
    def rd(self, a): return struct.unpack('<d', self.u.mem_read(a, 8))[0]
    def cstr(self, a, n=4096):
        out = bytearray()
        while len(out) < n:
            chunk = bytes(self.u.mem_read(a + len(out), PAGE - ((a + len(out)) & (PAGE - 1))))
            i = chunk.find(b'\0')
            if i >= 0: out += chunk[:i]; break
            out += chunk
        return bytes(out)
    def a(self, i): return self.u.reg_read(A[i])
    def arg(self, i):
        if i < 4: return self.a(i)
        return self.r32(self.u.reg_read(UC_MIPS_REG_SP) + 4 * i)

    def patch_rdhwr(self, data):
        d = bytearray(data)
        # rdhwr rt,$29 (user local): lw rt,TP_SLOT($zero)
        i = 0
        while True:
            i = d.find(b'\x3b\xe8', i)
            if i < 0: break
            if i % 4 == 0 and d[i + 3] == 0x7c and (d[i + 2] & 0xe0) == 0:
                rt = d[i + 2] & 31
                d[i:i + 4] = struct.pack('<I', 0x8c000000 | rt << 16 | TP_SLOT)
            i += 2
        return bytes(d)

    def load_elf(self, b, base=0):
        top = 0
        for s in elf_segments(b):
            if s[0] != 1: continue
            t, o, v, _, f, m, fl, al = s
            self.ensure(base + v, m)
            data = b[o:o + f]
            if fl & 1: data = self.patch_rdhwr(data)
            self.u.mem_write(base + v, data)
            top = max(top, base + v + m)
        return top

    def load(self, argv, env):
        u = self.u
        self.ensure(TP_SLOT, 4)
        self.ensure(STUB_RET, PAGE)
        b = self.exe.read_bytes()
        top = self.load_elf(b)
        self.brk = (top + PAGE - 1) & ~(PAGE - 1)
        self.brk0 = self.brk
        ld = (self.root / 'lib/ld-2.28.so').read_bytes()
        self.ld_base = 0x77e00000
        self.load_elf(ld, self.ld_base)
        sp = 0x7fff0000
        self.ensure(sp - 0x1000000, 0x1000000 + 0x8000)
        strs = sp + 0x100
        def put(s):
            nonlocal strs
            p = strs
            u.mem_write(p, s.encode() + b'\0')
            strs += len(s) + 1
            return p
        aps = [put(x) for x in argv]
        eps = [put(x) for x in list(env) + ['PATH=/usr/bin:/bin', 'LD_BIND_NOW=1']]
        rnd = strs = (strs + 16) & ~15
        strs += 16
        eh = struct.unpack_from('<I', b, 28)[0]
        phes, phn = struct.unpack_from('<HH', b, 42)
        aux = [(3, 0x400000 + eh), (4, phes), (5, phn), (6, PAGE), (7, self.ld_base), (9, struct.unpack_from('<I', b, 24)[0]),
               (11, 0), (12, 0), (13, 0), (14, 0), (16, 0), (23, 0), (25, rnd), (0, 0)]
        p = sp
        self.w32(p, len(aps)); p += 4
        for x in aps: self.w32(p, x); p += 4
        self.w32(p, 0); p += 4
        for x in eps: self.w32(p, x); p += 4
        self.w32(p, 0); p += 4
        for k, v in aux: self.w32(p, k, v); p += 8
        u.reg_write(UC_MIPS_REG_SP, sp)
        u.reg_write(UC_MIPS_REG_CP0_STATUS, u.reg_read(UC_MIPS_REG_CP0_STATUS) | 1 << 29 | 1 << 26)
        self.entry = self.ld_base + struct.unpack_from('<I', ld, 24)[0]
        u.hook_add(UC_HOOK_INTR, self._intr)
        t = Thread(1, None, self.entry, 0)
        self.threads.append(t)
        self.cur = t

    # ---- function hooks by absolute address
    def hook_fn(self, addr, fn):
        """Run fn(emu) instead of the function at addr; its return value goes to v0 unless fn returns None and it set it."""
        def go(u, a, s, _):
            r = fn(self)
            if r is not False:
                u.reg_write(UC_MIPS_REG_V0, (r or 0) & 0xffffffff)
                u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
        self.u.hook_add(UC_HOOK_CODE, go, begin=addr, end=addr)

    def watch_fn(self, addr, fn):
        """Observe a location; execution continues."""
        def go(u, a, s, _): fn(self)
        self.u.hook_add(UC_HOOK_CODE, go, begin=addr, end=addr)

    # ---- events
    def event(self, name, **kw):
        self.events.append((self.clock, name, kw))
        if self.verbose: print(f'{(self.clock - 10**12) / 1e9:9.3f} {name} {kw}', file=sys.stderr)

    def at(self, seconds_from_now, fn):
        self.pending.append((self.clock + int(seconds_from_now * 1e9), fn))

    def send_cmd(self, text):
        """What demo writes to the player's socket."""
        self.cmdq.append(text.encode())
        self.event('cmd', text=text)
        self._kick()

    def _kick(self):
        if self.cmdq:
            for t in self.threads:
                if t.wait and t.wait[0] == 'select':
                    t.wait = None; t.wake_at = None; t.ret = (1, 0)
                    return

    # ---- ALSA model installed once the libraries are loaded
    def split_mad(self, d, base):
        d = bytearray(d)
        self.ensure(MAD_TRAMPOLINES, 0x10000)
        for k, o in enumerate(mad_splits(self.root/'usr/lib/libmad.so.0.2.1')):
            t = MAD_TRAMPOLINES + k * 12
            self.u.mem_write(t, bytes(d[o:o + 4]) + struct.pack('<I', 0x08000000 | (base + o + 8) >> 2 & 0x3ffffff) + bytes(d[o + 4:o + 8]))
            d[o:o + 8] = struct.pack('<II', 0x08000000 | t >> 2 & 0x3ffffff, 0)
        return bytes(d)

    def install_alsa(self, base):
        syms = dynsyms(self.root / 'usr/lib/libasound.so.2.0.0')
        self.alsa = {base + v: n for v, n in syms.items() if n.startswith('snd_')}
        lo, hi = min(self.alsa), max(self.alsa)
        def go(u, addr, size, _):
            if not self.alsa_active: return
            n = self.alsa.get(addr)
            if n is None: return
            r = self._alsa(n)
            if r is not False:
                u.reg_write(UC_MIPS_REG_V0, (r or 0) & 0xffffffff)
                u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
        self.u.hook_add(UC_HOOK_CODE, go, begin=lo, end=hi)

    def _alsa(self, n):
        a = self.a
        if n == 'snd_pcm_open':
            self.pcm = PCM(self)
            self.w32(a(0), 0x7ffd0000)
            self.event('pcm_open', mode=a(3))
            return 0
        p = self.pcm
        if n in ('snd_pcm_hw_params_sizeof', 'snd_pcm_sw_params_sizeof', 'snd_pcm_status_sizeof'): return 1024
        if n == 'snd_pcm_close':
            self.event('pcm_close', left=p.sync() if p else 0, total=p.total if p else 0)
            self.pcm = None
            return 0
        if not p: return -77 if n != 'snd_lib_error_set_handler' else 0
        if n == 'snd_pcm_hw_params_set_rate_near':
            p.rate = self.r32(a(2)); return 0
        if n == 'snd_pcm_hw_params_set_rate': p.rate = a(2); return 0
        if n == 'snd_pcm_hw_params_set_channels_near': p.ch = self.r32(a(2)); return 0
        if n == 'snd_pcm_hw_params_set_channels': p.ch = a(2); return 0
        if n == 'snd_pcm_hw_params_set_format': p.fmt = a(2); return 0
        if n == 'snd_pcm_hw_params_test_format': return 0
        if n == 'snd_pcm_hw_params_set_buffer_time_near': p.buffer_us = self.r32(a(2)); return 0
        if n == 'snd_pcm_hw_params_set_periods_near': p.periods = self.r32(a(2)); return 0
        if n == 'snd_pcm_hw_params_get_buffer_size': self.w32(a(1), p.buffer_frames); return 0
        if n == 'snd_pcm_hw_params_get_period_size':
            self.w32(a(1), p.buffer_frames // p.periods)
            if a(2): self.w32(a(2), 0)
            return 0
        if n == 'snd_pcm_hw_params_get_rate_max': self.w32(a(1), 768000); return 0
        if n == 'snd_pcm_hw_params_can_pause': return 1
        if n == 'snd_pcm_sw_params_get_boundary': self.w32(a(1), 0x40000000); return 0
        if n == 'snd_pcm_sw_params_set_start_threshold': p.start_threshold = a(2); return 0
        if n == 'snd_pcm_hw_params':
            self.event('pcm_params', rate=p.rate, ch=p.ch, fmt=p.fmt, buf=p.buffer_frames)
            return 0
        if n == 'snd_pcm_status':
            p.sync()
            self.w32(a(1), p.state, 0)
            self.status_ptr = a(1)
            return 0
        if n == 'snd_pcm_status_get_avail': p.sync(); return p.buffer_frames - p.fill
        if n == 'snd_pcm_status_get_delay': p.sync(); return p.fill
        if n == 'snd_pcm_status_get_state': p.sync(); return p.state
        if n == 'snd_pcm_avail_update' or n == 'snd_pcm_avail': p.sync(); return p.buffer_frames - p.fill
        if n == 'snd_pcm_delay': p.sync(); self.w32(a(1), p.fill); return 0
        if n == 'snd_pcm_state': p.sync(); return p.state
        if n == 'snd_pcm_nonblock': p.nonblock = a(1); return 0
        if n == 'snd_pcm_prepare':
            p.sync(); p.state = 2; p.fill = 0; p.t = self.clock; self.event('pcm_prepare'); return 0
        if n == 'snd_pcm_drop':
            left = p.sync(); self.event('pcm_drop', left=left, total=p.total); p.state = 1; p.fill = 0; return 0
        if n == 'snd_pcm_drain':
            left = p.sync()
            self.event('pcm_drain', left=left, total=p.total)
            p.state = 1
            self.u.reg_write(UC_MIPS_REG_V0, 0)
            self.u.reg_write(UC_MIPS_REG_PC, self.u.reg_read(UC_MIPS_REG_RA))
            self.block_current(self.clock + left * 10**9 // p.rate, None, 'drain')
            p.fill = 0
            return False
        if n == 'snd_pcm_pause':
            p.sync()
            self.event('pcm_pause', on=a(1))
            p.state = 6 if a(1) else 3
            p.t = self.clock
            return 0
        if n == 'snd_pcm_resume': return 0
        if n == 'snd_pcm_writei': return self._writei(a(1), a(2))
        if n in ('snd_strerror', 'snd_pcm_format_description'): return False
        return 0

    def _writei(self, buf, frames):
        p = self.pcm
        p.sync()
        if p.state == 4:
            return -32 & 0xffffffff  # EPIPE
        room = p.buffer_frames - p.fill
        n = min(frames, room)
        if n <= 0:
            if p.nonblock: return -11 & 0xffffffff
            # blocking: wait for one period's worth
            self.block_current(self.clock + 5 * 10**6, None, 'write', retry=True)
            return False
        bps = {2: 2, 3: 2, 10: 4, 14: 4, 6: 3}.get(p.fmt, 4) * p.ch
        data = bytes(self.u.mem_read(buf, n * bps))
        if self.on_write: self.on_write(self, data, n, p)
        p.fill += n
        p.total += n
        if p.state in (2, 1):
            p.state = 3
            p.t = self.clock
            self.event('pcm_start', total=p.total)
        return n

    def block_current(self, wake_at, ret, why, retry=False, resume=None):
        t = self.cur
        t.wait = (why,)
        t.wake_at = wake_at
        t.ret = ret
        t.retry = retry
        t.resume = resume
        self.u.emu_stop()
        self.switching = True

    # ---- syscalls
    def _intr(self, u, intno, _):
        if intno != 17:
            pc = u.reg_read(UC_MIPS_REG_PC)
            raise RuntimeError(f'exception {intno} at {pc:#x} tid {self.cur.tid}: {bytes(u.mem_read(pc, 4)).hex()}')
        self.fault_n = 0  # it got as far as a syscall: not stuck on a fault
        nr = u.reg_read(UC_MIPS_REG_V0)
        args = [self.a(0), self.a(1), self.a(2), self.a(3)]
        h = getattr(self, f'sys_{nr}', None)
        if h is None:
            if nr in NAMES: h = getattr(self, 'sys_' + NAMES[nr], None)
        if h is None:
            self.event('unknown_syscall', nr=nr, args=[hex(x) for x in args])
            self.ret(-errno.ENOSYS)
            return
        r = h(*args)
        if r is not None: self.ret(r)

    def ret(self, r):
        u = self.u
        if r < 0 and r >= -4095:
            u.reg_write(UC_MIPS_REG_V0, -r); u.reg_write(UC_MIPS_REG_A3, 1)
        else:
            u.reg_write(UC_MIPS_REG_V0, r & 0xffffffff); u.reg_write(UC_MIPS_REG_A3, 0)

    def path(self, p):
        p = p.decode(errors='replace')
        for k, v in self.fsmap.items():
            if p == k or p.startswith(k.rstrip('/') + '/'): return v + p[len(k.rstrip('/')):]
        if p.startswith('/tmp/') or p.startswith('/mnt/data'): return str(self.tmp / p.lstrip('/').replace('/', '_'))
        if p.startswith('/dev/'): return None
        return str(self.root) + p

    def sys_open(self, path, flags, mode, _):
        raw = self.cstr(path)
        if raw.startswith(b'/dev/'):
            fd = self.next_fd; self.next_fd += 1
            self.fds[fd] = {'dev': raw.decode()}
            self.event('open_dev', path=raw.decode())
            return fd
        p = self.path(raw)
        h = 0
        if flags & 3 == 1: h |= os.O_WRONLY
        if flags & 3 == 2: h |= os.O_RDWR
        if flags & 0x100: h |= os.O_CREAT
        if flags & 0x200: h |= os.O_TRUNC
        if flags & 0x8: h |= os.O_APPEND
        if flags & 0x10000: h |= os.O_DIRECTORY
        if (h & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)) and not p.startswith(str(self.tmp)):
            return -errno.EACCES
        fail = self.hooks.get('open')
        if fail:
            r = fail(raw)
            if r: return -r
        try:
            hfd = os.open(p, h, 0o644)
        except OSError as e:
            return -e.errno
        fd = self.next_fd; self.next_fd += 1
        self.fds[fd] = {'h': hfd, 'path': raw}
        self.event('open', path=raw.decode(errors='replace'), fd=fd)
        delay = self.hooks.get('delay')
        dl = delay(raw) if delay else 0
        if dl:
            self.block_current(self.clock + int(dl * 1e9), (fd, 0), 'slow_open')
            return None
        return fd
    def sys_openat(self, dirfd, path, flags, mode):
        return self.sys_open(path, flags, mode, 0)
    def sys_close(self, fd, *_):
        f = self.fds.pop(fd, None)
        if f and 'h' in f: os.close(f['h'])
        if f and 'sock' in f and f['sock'].get('cb'):
            f['sock']['cb'](bytes(f['sock']['reply']))
        return 0
    def sys_read(self, fd, buf, n, _):
        f = self.fds.get(fd)
        if f is None: return -errno.EBADF
        if 'h' in f:
            hook = self.hooks.get('read')
            if hook:
                r = hook(self, f, n)
                if r: return r
            d = os.read(f['h'], n)
            self.u.mem_write(buf, d)
            return len(d)
        if 'sock' in f: return self.sys_recv(fd, buf, n, 0)
        return 0
    def sys_write(self, fd, buf, n, _):
        d = bytes(self.u.mem_read(buf, n))
        if fd in (1, 2):
            self.out.write(d.decode(errors='replace')); return n
        f = self.fds.get(fd)
        if f is None: return -errno.EBADF
        if 'h' in f: return os.write(f['h'], d)
        if 'sock' in f:
            self.replies.append(d)
            return n
        return n
    def sys_writev(self, fd, iov, cnt, _):
        tot = 0
        for i in range(cnt):
            base, ln = self.r32(iov + 8 * i), self.r32(iov + 8 * i + 4)
            tot += self.sys_write(fd, base, ln, 0)
        return tot
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
        raw = self.cstr(path)
        p = self.path(raw)
        if p is None: return -errno.ENOENT
        try: self.stat_out(os.stat(p), buf)
        except OSError as e: return -e.errno
        return 0
    sys_lstat64 = sys_stat64
    def sys_fstat64(self, fd, buf, *_):
        f = self.fds.get(fd)
        if fd <= 2 or f is None or 'h' not in f:
            st = os.stat('/dev/null')
            self.stat_out(st, buf)
            return 0
        self.stat_out(os.fstat(f['h']), buf)
        return 0
    def sys_access(self, path, mode, *_):
        p = self.path(self.cstr(path))
        return 0 if p and os.path.exists(p) else -errno.ENOENT
    def sys_llseek(self, fd, hi, lo, res):
        whence = self.arg(4)
        f = self.fds.get(fd)
        if not f or 'h' not in f or b'unseekable' in f['path']: return -errno.ESPIPE
        r = os.lseek(f['h'], (hi << 32) | lo, whence)
        self.u.mem_write(res, struct.pack('<q', r))
        return 0
    def sys_lseek(self, fd, off, whence, _):
        f = self.fds.get(fd)
        if not f or 'h' not in f or b'unseekable' in f['path']: return -errno.ESPIPE  # a path's way to be a pipe
        return os.lseek(f['h'], off if off < 1 << 31 else off - (1 << 32), whence)
    def sys_brk(self, a, *_):
        if a > self.brk:
            self.ensure(self.brk, a - self.brk)
            self.brk = a
        return self.brk
    def sys_mmap2(self, addr, length, prot, flags):
        fd = self.arg(4); pgoff = self.arg(5)
        return self._mmap(addr, length, prot, flags, fd, pgoff * PAGE)
    def sys_mmap(self, addr, length, prot, flags):
        return self._mmap(addr, length, prot, flags, self.arg(4), self.arg(5))
    def _mmap(self, addr, length, prot, flags, fd, off):
        size = (length + PAGE - 1) & ~(PAGE - 1)
        fixed = flags & 0x10
        if not fixed:
            free = self.unmapped.get(size)
            if free:
                addr = free.pop()
            else:
                addr = self.next_map
                self.next_map += size + PAGE
        self.ensure(addr, size)
        if flags & 0x800:  # MAP_ANONYMOUS on MIPS
            self.u.mem_write(addr, bytes(size))
        else:
            f = self.fds.get(fd)
            if not f or 'h' not in f: return -errno.EBADF
            d = os.pread(f['h'], length, off)
            pth = f.get('path', b'')
            if prot & 4:
                d = self.patch_rdhwr(d)
                if b'libmad' in pth: d = self.split_mad(d, addr - off)
            self.u.mem_write(addr, d + bytes(size - len(d)))
            if b'libasound' in pth and prot & 4 and not hasattr(self, 'alsa'):
                self.install_alsa(addr - off)
        return addr
    def sys_munmap(self, addr, length, *_):
        self.unmapped.setdefault((length + PAGE - 1) & ~(PAGE - 1), []).append(addr)
        return 0
    def sys_mprotect(self, *_): return 0
    def sys_madvise(self, *_): return 0
    def sys_mremap(self, old, olds, news, flags):
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
    def sys_set_robust_list(self, *_): return 0
    def sys_rt_sigaction(self, *_): return 0
    def sys_rt_sigprocmask(self, *_): return 0
    def sys_sigaltstack(self, *_): return 0
    def sys_getpid(self, *_): return 1  # the main thread's tid
    def sys_gettid(self, *_): return self.cur.tid
    def sys_getuid(self, *_): return 0
    sys_geteuid = sys_getgid = sys_getegid = sys_getuid
    def sys_getppid(self, *_): return 1
    def sys_kill(self, pid, sig, *_):
        self.event('kill', pid=pid, sig=sig)
        return 0
    def sys_tgkill(self, tgid, tid, sig, _):
        self.event('tgkill', tid=tid, sig=sig)
        if sig == 6: self.exited = 'abort'; self.u.emu_stop(); self.switching = True
        return 0
    def sys_uname(self, buf, *_):
        for i, s in enumerate([b'Linux', b'q2', b'5.10.21', b'#1', b'mips', b'']):
            self.u.mem_write(buf + 65 * i, s + b'\0')
        return 0
    def sys_readlink(self, *_): return -errno.ENOENT
    def sys_getcwd(self, buf, n, *_):
        self.u.mem_write(buf, b'/\0'); return 2
    def sys_prlimit64(self, pid, res, new, old):
        if old: self.u.mem_write(old, struct.pack('<QQ', 8 << 20, 1 << 40))
        return 0
    def sys_ugetrlimit(self, res, old, *_):
        self.u.mem_write(old, struct.pack('<II', 8 << 20, 0x7fffffff))
        return 0
    def sys_getrlimit(self, res, old, *_): return self.sys_ugetrlimit(res, old)
    def sys_sysinfo(self, buf, *_):
        self.u.mem_write(buf, bytes(64)); return 0
    def sys_prctl(self, *_): return 0
    def sys_sched_yield(self, *_):
        self.block_current(self.clock, (0, 0), 'yield')
        return 0
    def sys_sched_getaffinity(self, pid, n, buf, _):
        self.u.mem_write(buf, struct.pack('<I', 1)); return 4
    def sys_getpriority(self, *_): return 20
    def sys_setpriority(self, *_): return 0
    def sys_clock_gettime(self, cid, ts, *_):
        self.w32(ts, self.clock // 10**9, self.clock % 10**9); return 0
    def sys_clock_getres(self, cid, ts, *_):
        if ts: self.w32(ts, 0, 1000)
        return 0
    def sys_gettimeofday(self, tv, tz, *_):
        if tv: self.w32(tv, self.clock // 10**9, self.clock % 10**9 // 1000)
        return 0
    def sys_time(self, p, *_):
        if p: self.w32(p, self.clock // 10**9)
        return self.clock // 10**9
    def sys_nanosleep(self, req, rem, *_):
        s, n = self.r32(req), self.r32(req + 4)
        self.block_current(self.clock + s * 10**9 + n, (0, 0), 'sleep')
        return 0
    def sys_clock_nanosleep(self, cid, flags, req, rem):
        s, n = self.r32(req), self.r32(req + 4)
        at = s * 10**9 + n
        self.block_current(at if flags & 1 else self.clock + at, (0, 0), 'sleep')
        return 0
    def sys_futex(self, addr, op, val, timeout):
        cmd = op & 0x7f
        if cmd in (0, 9):
            if self.r32(addr) != val: return -errno.EAGAIN
            wake = None
            if timeout:
                s, n = self.r32(timeout), self.r32(timeout + 4)
                wake = (s * 10**9 + n) if cmd == 9 and not (0) else self.clock + s * 10**9 + n
            t = self.cur
            t.wait = ('futex', addr)
            t.wake_at = wake
            t.ret = (errno.ETIMEDOUT, 1)
            t.retry = False
            t.resume = None
            self.u.emu_stop()
            self.switching = True
            return None
        if cmd in (1, 10):
            return self.fwake(addr, val)
        if cmd == 5:  # WAKE_OP: not modelled beyond waking both
            uaddr2 = self.arg(4)
            return self.fwake(addr, val) + self.fwake(uaddr2, timeout)
        if cmd in (3, 4):  # REQUEUE/CMP_REQUEUE
            uaddr2 = self.arg(4)
            if cmd == 4 and self.r32(addr) != self.arg(5): return -errno.EAGAIN
            n = self.fwake(addr, val)
            for t in self.threads:
                if t.wait and t.wait[0] == 'futex' and t.wait[1] == addr: t.wait = ('futex', uaddr2)
            return n
        self.event('futex_op', op=op)
        return -errno.ENOSYS
    def fwake(self, addr, n):
        c = 0
        for t in self.threads:
            if c >= n: break
            if t.wait and t.wait[0] == 'futex' and t.wait[1] == addr:
                t.wait = None; t.ret = (0, 0); t.wake_at = None; c += 1
        return c
    def sys_clone(self, flags, stack, ptid, tls):
        ctid = self.arg(4)
        u = self.u
        if not flags & 0x100:  # fork-like: refuse
            self.event('fork_refused')
            return -errno.EAGAIN
        tid = self.next_tid; self.next_tid += 1
        ctx = u.context_save()
        pc = u.reg_read(UC_MIPS_REG_PC)
        t = Thread(tid, ctx, pc + 4 if self.pc_at_syscall else pc, tls if flags & 0x80000 else self.cur.tp)
        t.child_sp = stack
        t.ret = (0, 0)
        t.clear_tid = ctid if flags & 0x200000 else 0
        if flags & 0x100000: self.w32(ptid, tid)
        if flags & 0x1000000: self.w32(ctid, tid)
        self.threads.append(t)
        self.event('thread', tid=tid)
        return tid
    def sys_exit(self, code, *_):
        t = self.cur
        t.dead = True
        if t.clear_tid:
            self.w32(t.clear_tid, 0)
            self.fwake(t.clear_tid, 1)
        self.event('thread_exit', tid=t.tid, code=code)
        self.u.emu_stop()
        self.switching = True
        return None
    def sys_exit_group(self, code, *_):
        self.exited = code
        self.event('exit_group', code=code)
        self.u.emu_stop()
        self.switching = True
        return None
    # sockets
    def sys_socket(self, dom, ty, proto, _):
        fd = self.next_fd; self.next_fd += 1
        self.fds[fd] = {'sock': self.ctl}
        self.event('socket', fd=fd, dom=dom, ty=ty)
        return fd
    def sys_bind(self, fd, addr, n, _):
        self.event('bind', fd=fd, name=bytes(self.u.mem_read(addr + 2, 40)).split(b'\0')[0])
        return 0
    def sys_listen(self, *_): return 0
    def sys_setsockopt(self, *_): return 0
    def sys_getsockopt(self, *_): return 0
    def sys_shutdown(self, *_): return 0
    def sys_connect(self, *_): return 0
    def sys_accept(self, fd, addr, alen, _):
        if self.accept_q:
            c = self.accept_q.pop(0)
            n = self.next_fd; self.next_fd += 1
            self.fds[n] = {'sock': c}
            return n
        t = self.cur
        t.wait = ('accept', fd); t.wake_at = None; t.ret = None; t.retry = False; t.resume = None
        self.u.emu_stop(); self.switching = True
        return None
    def sys_recv(self, fd, buf, n, flags):
        f = self.fds.get(fd)
        if not f or 'sock' not in f: return -errno.EBADF
        if not self.cmdq: return -errno.EAGAIN
        d = self.cmdq.pop(0)[:n]
        self.u.mem_write(buf, d)
        return len(d)
    def sys_recvfrom(self, fd, buf, n, flags): return self.sys_recv(fd, buf, n, flags)
    def sys_send(self, fd, buf, n, flags): return self.sys_write(fd, buf, n, 0)
    def sys_sendto(self, fd, buf, n, flags): return self.sys_write(fd, buf, n, 0)
    def sys_ioctl(self, fd, req, arg, _):
        f = self.fds.get(fd, {})
        self.ioctls.append((self.clock, f.get('dev', fd), req, arg))
        if 'dev' in f:
            self.event('ioctl', dev=f['dev'], req=hex(req), arg=arg)
            return 0
        return -errno.ENOTTY
    def sys_fcntl64(self, fd, cmd, arg, _): return 0
    def sys_fcntl(self, fd, cmd, arg, _): return 0
    def sys_getdents64(self, *_): return 0
    def sys_rmdir(self, *_): return 0
    def sys_mkdir(self, *_): return 0
    def sys_unlink(self, *_): return 0
    def sys_rename(self, *_): return 0
    def sys_fsync(self, *_): return 0
    def sys_select(self, *_): return 0
    def sys__newselect(self, nfds, r, w, e):
        tv = self.arg(4)
        if self.cmdq:
            if r: self.u.mem_write(r, struct.pack('<I', 0xffffffff) * 8)
            return 1
        at = self.clock + self.r32(tv) * 10**9 + self.r32(tv + 4) * 1000 if tv else None
        if r:
            self.u.mem_write(r, bytes(128))  # timed out: nothing set; a wake finds the set restored below
            self.sel_r = r
        self.sel_r = r
        self.block_current(at, (0, 0), 'select')
        return None
    def sys_poll(self, fds, n, timeout, _):
        if timeout and timeout < 1 << 31:
            self.block_current(self.clock + timeout * 10**6, (0, 0), 'poll')
            return None
        self.block_current(None, (0, 0), 'poll')
        return None

    # ---- scheduler
    def run(self, until=None, count=None):
        """Run until virtual time 'until' (seconds from boot), everything idle, or exit."""
        u = self.u
        limit = None if until is None else 10**12 + int(until * 1e9)
        self.pc_at_syscall = False
        idx = 0
        while self.exited is None:
            self.pending.sort(key=lambda x: x[0])
            while self.pending and self.pending[0][0] <= self.clock:
                _, fn = self.pending.pop(0)
                fn(self)
                self._kick()
            ready = [t for t in self.threads if not t.dead and (not t.wait or (t.wake_at is not None and t.wake_at <= self.clock))]
            if not ready:
                ws = [t.wake_at for t in self.threads if not t.dead and t.wake_at is not None]
                ws += [p[0] for p in self.pending]
                if not ws:
                    self.event('all_blocked'); return 'blocked'
                nxt = min(ws)
                if limit is not None and nxt > limit: self.clock = limit; return 'time'
                self.clock = max(self.clock, nxt)
                continue
            if limit is not None and self.clock >= limit: return 'time'
            # round robin
            idx = (idx + 1) % len(ready)
            t = ready[idx]
            self.cur = t
            if t.ctx is not None:
                u.context_restore(t.ctx)
            self.w32(TP_SLOT, t.tp)
            if t.wait: t.wait = None
            t.wake_at = None
            if hasattr(t, 'child_sp') and t.child_sp:
                u.reg_write(UC_MIPS_REG_SP, t.child_sp); t.child_sp = 0
            retry = getattr(t, 'retry', False)
            t.retry = False
            if t.ret is not None and t.ret[0] == 1 and getattr(self,'sel_r',0):
                self.u.mem_write(self.sel_r, struct.pack('<I', 0xffffffff) * 8)
            if t.ret is not None and not retry:
                v0, a3 = t.ret
                u.reg_write(UC_MIPS_REG_V0, v0); u.reg_write(UC_MIPS_REG_A3, a3)
            t.ret = None
            self.switching = False
            if t.ctx is None:
                pc = self.entry
            else:
                pc = t.pc
            try:
                u.emu_start(pc, 0xfffffff0, timeout=300000)
            except UcError as e:
              # A stale error status comes back on any stop (hook or quantum): a real fault repeats at one PC.
              cpc = u.reg_read(UC_MIPS_REG_PC)
              if not self.switching:
                if cpc == getattr(self, 'fault_pc', None):
                    self.fault_n += 1
                    if self.fault_n > 3:
                        raise RuntimeError(f'{e} at pc={cpc:#x} ra={u.reg_read(UC_MIPS_REG_RA):#x} tid={t.tid} clock={(self.clock-10**12)/1e9:.3f}')
                else:
                    self.fault_pc, self.fault_n = cpc, 0
            t.ctx = u.context_save()
            t.pc = u.reg_read(UC_MIPS_REG_PC)
        return self.exited


NAMES = {4001: 'exit', 4003: 'read', 4004: 'write', 4005: 'open', 4006: 'close', 4013: 'time', 4019: 'lseek', 4020: 'getpid',
         4024: 'getuid', 4033: 'access', 4037: 'kill', 4039: 'mkdir', 4040: 'rmdir', 4045: 'brk', 4047: 'getgid', 4049: 'geteuid', 4050: 'getegid',
         4054: 'ioctl', 4055: 'fcntl', 4064: 'getppid', 4076: 'getrlimit', 4078: 'gettimeofday', 4085: 'readlink', 4090: 'mmap', 4091: 'munmap',
         4096: 'getpriority', 4097: 'setpriority', 4116: 'sysinfo', 4118: 'fsync', 4120: 'clone', 4122: 'uname', 4125: 'mprotect', 4140: 'llseek', 4142: '_newselect',
         4145: 'readv', 4146: 'writev', 4162: 'sched_yield', 4166: 'nanosleep', 4167: 'mremap', 4168: 'accept', 4169: 'bind', 4170: 'connect',
         4174: 'listen', 4175: 'recv', 4176: 'recvfrom', 4178: 'send', 4180: 'sendto', 4181: 'setsockopt', 4182: 'shutdown', 4183: 'socket',
         4188: 'poll', 4192: 'prctl', 4194: 'rt_sigaction', 4195: 'rt_sigprocmask', 4203: 'getcwd', 4206: 'sigaltstack', 4210: 'mmap2', 4213: 'stat64',
         4214: 'lstat64', 4215: 'fstat64', 4218: 'madvise', 4219: 'getdents64', 4220: 'fcntl64', 4222: 'gettid', 4238: 'futex', 4239: 'sched_getaffinity',
         4246: 'exit_group', 4252: 'set_tid_address', 4263: 'clock_gettime', 4264: 'clock_getres', 4265: 'clock_nanosleep', 4266: 'tgkill', 4283: 'set_thread_area', 4288: 'openat', 4309: 'set_robust_list', 4338: 'prlimit64'}
